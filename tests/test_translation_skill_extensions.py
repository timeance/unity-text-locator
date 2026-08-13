from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AINIEE_SCRIPTS = ROOT / "ainiee-translate" / "scripts"
sys.path.insert(0, str(AINIEE_SCRIPTS))

from ainiee_translate import apply_repairs, audit_translation, curate_terms, extract_terms, mtool  # noqa: E402


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EMBEDDED = load_script(
    "extract_embedded_csv",
    ROOT / "unity-text-locator" / "scripts" / "extract_embedded_csv.py",
)
RUNTIME_DIFF = load_script(
    "diff_autotranslator_files",
    ROOT / "unity-text-locator" / "scripts" / "diff_autotranslator_files.py",
)


class AiNieeExtensionTests(unittest.TestCase):
    def test_mtool_group_validation_preserves_control_markers(self) -> None:
        source = [{"text_index": 1, "source_text": r"\C[23]愛菜\C[0]\n次"}]
        translation = [{"text_index": 1, "translated_text": "爱菜"}]
        report = mtool.validate_records(source, translation)
        self.assertFalse(report["valid"])
        self.assertEqual(report["marker_issue_count"], 1)

    def test_term_candidates_merge_reading_forms_and_export_high_confidence(self) -> None:
        source_rows = [
            {"text_index": 1, "source_text": "愛菜(まな)"},
            {"text_index": 2, "source_text": "愛菜（まな）"},
        ]
        candidates, hints = extract_terms.extract(source_rows, 2)
        self.assertEqual(hints, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "愛菜")
        self.assertEqual(candidates[0]["confidence"], "high")

    def test_curated_term_conflict_blocks_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = root / "candidates.json"
            translations = root / "translations.json"
            output = root / "terms.json"
            candidates.write_text(
                json.dumps({"candidates": [{"source": "愛菜", "frequency": 2, "confidence": "high", "categories": ["reading_form"]}]}),
                encoding="utf-8",
            )
            translations.write_text(
                json.dumps([{"src": "愛菜", "dst": "爱菜"}, {"src": "愛菜", "dst": "艾菜"}]),
                encoding="utf-8",
            )
            argv = ["curate_terms", "--candidates", str(candidates), "--translations", str(translations), "--output", str(output)]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(curate_terms.main(), 1)
            self.assertFalse(output.exists())

    def test_source_audit_rejects_duplicates_extra_rows_and_structure_changes(self) -> None:
        source = [{"text_index": 1, "source_text": "「HPは%d」\n次"}]
        translation = [
            {"text_index": 1, "translated_text": "『HP是%s』"},
            {"text_index": 1, "translated_text": "重复"},
            {"text_index": 2, "translated_text": "多余"},
        ]
        report = audit_translation.audit(source, translation, None)
        kinds = {issue["kind"] for issue in report["issues"]}
        self.assertFalse(report["valid"])
        self.assertTrue({"duplicate_translation_text_index", "extra_translation_row", "placeholder_mismatch", "newline_mismatch", "dialogue_quote_mismatch"} <= kinds)

    def test_repair_manifest_rejects_missing_index_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            translation = root / "translation.json"
            manifest = root / "manifest.json"
            output = root / "output.json"
            translation.write_text(json.dumps([{"text_index": 1, "translated_text": "旧译"}]), encoding="utf-8")
            manifest.write_text(json.dumps({"items": [{"text_index": 2, "expected_before": "旧译", "replacement": "新译"}]}), encoding="utf-8")
            argv = ["apply_repairs", "--translation", str(translation), "--manifest", str(manifest), "--output", str(output)]
            with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                apply_repairs.main()
            self.assertFalse(output.exists())


class UnityExtensionTests(unittest.TestCase):
    def test_embedded_csv_manifest_preserves_duplicate_occurrences_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "Game_Data" / "sharedassets0.assets"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"prefix\x00" + "ID,名前,説明\n1,愛菜,主人公\n2,愛菜,案内役\n".encode("utf-8") + b"\x00\x00\x00\x00binary")
            out_dir = root / "report"
            argv = ["extract_embedded_csv", str(root), "--out-dir", str(out_dir)]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(EMBEDDED.main(), 0)
            manifest = json.loads((out_dir / "embedded_csv_manifest.json").read_text(encoding="utf-8"))
            duplicates = [row for table in manifest["tables"] for row in table["occurrences"] if row["original_flat"] == "愛菜"]
            self.assertEqual(len(duplicates), 2)
            self.assertEqual(manifest["tables"][0]["source_file"], "Game_Data/sharedassets0.assets")
            self.assertEqual(manifest["tables"][0]["source_sha256"], hashlib.sha256(asset.read_bytes()).hexdigest())

    def test_runtime_diff_blocks_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old.txt"
            new = root / "new.txt"
            output = root / "diff.json"
            old.write_text("同じ=相同\n", encoding="utf-8")
            new.write_text("同じ=相同\n同じ=另一译文\n", encoding="utf-8")
            argv = ["diff_autotranslator_files", "--old", str(old), "--new", str(new), "--output", str(output)]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(RUNTIME_DIFF.main(), 1)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["new_duplicate_key_count"], 1)
            self.assertEqual(report["runtime_merge"], "manual_review_required")


if __name__ == "__main__":
    unittest.main()
