from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "unity-text-locator" / "scripts" / "extract_unity_text.py"
SPEC = importlib.util.spec_from_file_location("extract_unity_text", SCRIPT)
assert SPEC and SPEC.loader
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)


class RawCandidateGateTests(unittest.TestCase):
    def result(self, root: Path) -> dict[str, object]:
        return {
            "root": str(root),
            "files_scanned": [
                {"path": "Game_Data/resources.assets", "sha256": "abc", "category": "resources.assets"}
            ],
            "occurrences": [
                {
                    "source_file": "Game_Data/resources.assets",
                    "offset_hex": "0x10",
                    "byte_length": 9,
                    "method": "raw-utf8-fallback",
                    "confidence": "medium",
                    "original_flat": "候補テキスト",
                    "notes": "raw candidate",
                },
                {
                    "source_file": "Game_Data/resources.assets",
                    "offset_hex": "0x20",
                    "byte_length": 12,
                    "method": "unity-len-utf8",
                    "confidence": "high",
                    "original_flat": "表示テキスト",
                    "notes": "length-prefixed",
                },
            ],
        }

    def test_raw_candidates_are_audit_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Game"
            out = Path(temporary) / "text"
            root.mkdir()
            extractor.write_source_text_outputs(self.result(root), out)

            audit = out / "raw_utf8_candidates_audit.csv"
            self.assertTrue(audit.is_file())
            with audit.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual([row["original_flat"] for row in csv.DictReader(handle)], ["候補テキスト"])

            index = json.loads((out / "source_text_index.json").read_text(encoding="utf-8"))
            source_csv = out / index[0]["source_csv"]
            with source_csv.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["original_flat"] for row in rows], ["表示テキスト"])

    def test_explicit_opt_in_exposes_raw_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Game"
            out = Path(temporary) / "text"
            root.mkdir()
            extractor.write_source_text_outputs(self.result(root), out, include_raw_candidates=True)
            index = json.loads((out / "source_text_index.json").read_text(encoding="utf-8"))
            self.assertEqual(index[0]["rows"], 2)
            self.assertFalse((out / "raw_utf8_candidates_audit.csv").exists())


if __name__ == "__main__":
    unittest.main()
