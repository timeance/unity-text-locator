from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "unity-text-locator" / "scripts" / "scan_unity_text.py"
SPEC = importlib.util.spec_from_file_location("scan_unity_text", SCRIPT)
assert SPEC and SPEC.loader
scanner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scanner
SPEC.loader.exec_module(scanner)


class UnityVersionDetectionTests(unittest.TestCase):
    def test_detects_legacy_and_unity_six_versions_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Game"
            data = root / "Game_Data"
            data.mkdir(parents=True)
            (data / "globalgamemanagers").write_bytes(b"header\x002019.4.40f1\x00tail")
            (data / "resources.assets").write_bytes(b"header\x006000.0.35f1\x00tail")

            result = scanner.detect_unity_versions(root, [data])

            self.assertEqual(result["unity_versions"], ["2019.4.40f1", "6000.0.35f1"])
            self.assertEqual(result["unity_version_status"], "detected")
            self.assertIn("Game_Data/globalgamemanagers", result["unity_version_evidence"]["2019.4.40f1"])

    def test_reports_unknown_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = scanner.detect_unity_versions(root, [])
            self.assertEqual(result["unity_versions"], [])
            self.assertEqual(result["unity_version_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
