import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AINIEE_SCRIPTS = ROOT / "ainiee-translate" / "scripts"
TO_CACHE = ROOT / "unity-text-locator" / "scripts" / "unity_csv_to_ainiee_cache.py"
FROM_CACHE = ROOT / "unity-text-locator" / "scripts" / "ainiee_cache_to_unity_translation.py"


HAS_MSGSPEC = importlib.util.find_spec("msgspec") is not None


def _load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SourceFingerprintTests(unittest.TestCase):
    def test_both_bridge_directions_use_the_same_fingerprint(self):
        forward = _load_script("unity_to_ainiee", TO_CACHE)
        reverse = _load_script("ainiee_to_unity", FROM_CACHE)
        rows = ["一", "", "line\nwith newline", "emoji: 😀"]
        self.assertEqual(forward.source_rows_sha256(rows), reverse.source_rows_sha256(rows))

    def test_fingerprint_preserves_row_boundaries(self):
        bridge = _load_script("ainiee_to_unity_boundaries", FROM_CACHE)
        self.assertNotEqual(
            bridge.source_rows_sha256(["ab", "c"]),
            bridge.source_rows_sha256(["a", "bc"]),
        )


@unittest.skipUnless(HAS_MSGSPEC, "ainiee-translate runtime dependency msgspec is not installed")
class AinieeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.source = self.work / "source.csv"
        self.cache = self.work / "cache.json"
        self.out = self.work / "translated.csv"
        self._write_source(["一", "二"])
        self._run(TO_CACHE, "--source-csv", self.source, "--out-cache", self.cache)

    def tearDown(self):
        self.temp.cleanup()

    def _write_source(self, rows):
        with self.source.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["original_flat"])
            writer.writerows([[row] for row in rows])

    def _run(self, *args, check=True):
        env = os.environ.copy()
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(AINIEE_SCRIPTS), existing] if existing else [str(AINIEE_SCRIPTS)]
        )
        return subprocess.run(
            [sys.executable, *map(str, args)], cwd=ROOT, env=env,
            capture_output=True, text=True, check=check,
        )

    def _batch_write(self, payload):
        translations = self.work / "translations.json"
        translations.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self._run(
            "-m", "ainiee_translate.batch", "write", self.cache, translations,
            check=False,
        )

    def test_duplicate_writeback_indexes_are_rejected_without_mutation(self):
        before = self.cache.read_bytes()
        result = self._batch_write([
            {"text_index": 1, "translated_text": "甲"},
            {"text_index": 1, "translated_text": "乙"},
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate text_index", result.stderr)
        self.assertEqual(before, self.cache.read_bytes())

    def test_unmatched_writeback_is_nonzero_and_does_not_save_partial_changes(self):
        before = self.cache.read_bytes()
        result = self._batch_write([
            {"text_index": 1, "translated_text": "甲"},
            {"text_index": 99, "translated_text": "不存在"},
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("unmatched", result.stdout)
        self.assertEqual(before, self.cache.read_bytes())

    def test_bridge_requires_complete_translated_cache(self):
        result = self._run(
            FROM_CACHE, "--cache", self.cache, "--source-csv", self.source,
            "--out-csv", self.out, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cache is incomplete", result.stderr)
        self.assertFalse(self.out.exists())

    def test_bridge_checks_source_content_hash(self):
        self.assertEqual(self._batch_write([
            {"text_index": 1, "translated_text": "甲"},
            {"text_index": 2, "translated_text": "乙"},
        ]).returncode, 0)
        self._write_source(["一", "已改变"])
        result = self._run(
            FROM_CACHE, "--cache", self.cache, "--source-csv", self.source,
            "--out-csv", self.out, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("content hash does not match", result.stderr)

    def test_bridge_exports_complete_cache(self):
        self.assertEqual(self._batch_write([
            {"text_index": 1, "translated_text": "甲"},
            {"text_index": 2, "translated_text": "乙"},
        ]).returncode, 0)
        self._run(
            FROM_CACHE, "--cache", self.cache, "--source-csv", self.source,
            "--out-csv", self.out,
        )
        with self.out.open("r", encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.reader(handle)), [["zh_cn"], ["甲"], ["乙"]])


if __name__ == "__main__":
    unittest.main()
