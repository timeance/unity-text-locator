from __future__ import annotations

import importlib.util
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load(name: str):
    script = ROOT / "unity-text-locator" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extractor = load("extract_mono_raw_utf8")
writer = load("writeback_mono_raw_utf8")


def record(text: str) -> bytes:
    payload = text.encode("utf-8")
    value = struct.pack("<I", len(payload)) + payload
    return value + b"\0" * ((-len(value)) % 4)


class MonoRawUtf8Tests(unittest.TestCase):
    def test_escape_round_trip_preserves_mixed_controls(self) -> None:
        original = "一行目\r二行目\r\n三行目\n\t\\末尾"
        escaped = extractor.escape_text(original)
        self.assertEqual(writer.decode_text(escaped), original)
        self.assertEqual(extractor.newline_events(original), ["CR", "CRLF", "LF"])

    def test_scanner_keeps_visible_and_classifies_internal_keys(self) -> None:
        raw = record("表示される文章です。") + record("BGM_Hシーン") + record("クレジット画像")
        hits = extractor.scan_object(raw, 1000, [])
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0]["classification"], "suggest_visible")
        self.assertEqual(hits[1]["classification"], "preserve_internal")
        self.assertEqual(hits[2]["classification"], "preserve_internal")

    def test_scanner_does_not_skip_nested_aligned_candidate(self) -> None:
        inner = record("内側の文章です。")
        outer_payload = "外側".encode("utf-8") + b"xx" + inner
        outer = struct.pack("<I", len(outer_payload)) + outer_payload
        outer += b"\0" * ((-len(outer)) % 4)
        hits = extractor.scan_object(outer, 1000, [])
        self.assertTrue(any(hit["original"] == "内側の文章です。" for hit in hits))

    def test_decode_rejects_unknown_escape(self) -> None:
        with self.assertRaises(ValueError):
            writer.decode_text(r"本文\x")


if __name__ == "__main__":
    unittest.main()
