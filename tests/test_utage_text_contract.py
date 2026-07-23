from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "unity-text-locator"
    / "scripts"
    / "writeback_utage_scenarios.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location("writeback_utage_scenarios", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UtageTextContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.writer = load_script()

    def test_exact_escape_round_trip_preserves_controls(self) -> None:
        original = "path\\name\rfirst\r\nsecond\nthird\tend"
        encoded = self.writer.encode_exact(original)

        self.assertEqual(encoded, r"path\\name\rfirst\Nsecond\nthird\tend")
        self.assertEqual(self.writer.decode_exact(encoded), original)
        self.assertEqual(self.writer.newline_events(original), ["CR", "CRLF", "LF"])

    def test_legacy_escape_still_normalizes_newlines(self) -> None:
        self.assertEqual(self.writer.flat_legacy("a\r\nb\rc\nd"), r"a\nb\nc\nd")


if __name__ == "__main__":
    unittest.main()
