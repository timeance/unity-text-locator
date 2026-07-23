from __future__ import annotations

import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "unity-text-locator" / "scripts" / "generate_autotranslator_txt.py"
SPEC = importlib.util.spec_from_file_location("generate_autotranslator_txt", SCRIPT)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_column(path: Path, header: str, rows: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([header])
        writer.writerows([[row] for row in rows])


class AutoTranslatorExportTests(unittest.TestCase):
    def run_export(
        self,
        root: Path,
        source_rows: list[str],
        translation_rows: list[str],
        *,
        write: bool = True,
    ) -> tuple[int, Path, Path, Path]:
        source = root / "source.csv"
        translation = root / "translation.csv"
        out_dir = root / "candidate"
        write_column(source, "original_flat", source_rows)
        write_column(translation, "zh_cn", translation_rows)
        arguments = [
            "--source-csv",
            str(source),
            "--translation-csv",
            str(translation),
            "--out-dir",
            str(out_dir),
        ]
        if write:
            arguments.append("--write")
        with contextlib.redirect_stdout(io.StringIO()):
            result = exporter.main(arguments)
        return result, source, translation, out_dir

    def test_exact_export_has_bom_and_hash_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, source, translation, out_dir = self.run_export(
                Path(temporary), ["greeting", r"line\nchoice"], ["hello", r"translated\nchoice"]
            )

            output = out_dir / exporter.OUTPUT_NAME
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 0)
            self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
            decoded = output.read_text(encoding="utf-8-sig")
            self.assertIn("greeting=hello\n", decoded)
            self.assertIn(r"line\nchoice=translated\nchoice", decoded)
            self.assertEqual(report["status"], "written")
            self.assertEqual(report["source_csv_sha256"], sha256(source))
            self.assertEqual(report["translation_csv_sha256"], sha256(translation))
            self.assertEqual(report["candidate_output_sha256"], sha256(output))
            self.assertTrue(report["runtime_verification_required"])

    def test_blank_rows_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _source, _translation, out_dir = self.run_export(
                Path(temporary), ["first", "second"], ["translated", ""]
            )
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 0)
            self.assertEqual(report["blank_rows"], 1)
            self.assertEqual(report["exported_entries"], 1)
            self.assertNotIn("second=", (out_dir / exporter.OUTPUT_NAME).read_text(encoding="utf-8-sig"))

    def test_identical_duplicate_pairs_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _source, _translation, out_dir = self.run_export(
                Path(temporary), ["same", "same"], ["translated", "translated"]
            )
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            output = (out_dir / exporter.OUTPUT_NAME).read_text(encoding="utf-8-sig")
            self.assertEqual(result, 0)
            self.assertEqual(report["exported_entries"], 1)
            self.assertEqual(report["deduplicated_occurrences"], 1)
            self.assertEqual(output.count("same=translated"), 1)

    def test_conflicting_duplicate_translations_block_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _source, _translation, out_dir = self.run_export(
                Path(temporary), ["same", "same"], ["one", "two"]
            )
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 1)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["conflicts"][0]["type"], "conflicting_translations_for_key")
            self.assertFalse((out_dir / exporter.OUTPUT_NAME).exists())

    def test_blank_and_selected_duplicate_occurrences_block_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _source, _translation, out_dir = self.run_export(
                Path(temporary), ["same", "same"], ["translated", ""]
            )
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 1)
            self.assertEqual(report["conflicts"][0]["type"], "mixed_selected_and_blank_occurrences")
            self.assertFalse((out_dir / exporter.OUTPUT_NAME).exists())

    def test_row_count_mismatch_blocks_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _source, _translation, out_dir = self.run_export(
                Path(temporary), ["first", "second"], ["translated"]
            )
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 1)
            self.assertEqual(report["conflicts"][0]["type"], "row_count_mismatch")
            self.assertFalse((out_dir / exporter.OUTPUT_NAME).exists())

    def test_ambiguous_plain_text_key_blocks_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _source, _translation, out_dir = self.run_export(
                Path(temporary), ["key=value"], ["translated"]
            )
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 1)
            self.assertEqual(report["unrepresentable"][0]["type"], "ambiguous_delimiter")
            self.assertFalse((out_dir / exporter.OUTPUT_NAME).exists())

    def test_default_mode_writes_report_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _source, _translation, out_dir = self.run_export(
                Path(temporary), ["source"], ["translated"], write=False
            )
            report = json.loads((out_dir / exporter.REPORT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 0)
            self.assertEqual(report["status"], "ready")
            self.assertFalse(report["output_written"])
            self.assertFalse((out_dir / exporter.OUTPUT_NAME).exists())


if __name__ == "__main__":
    unittest.main()
