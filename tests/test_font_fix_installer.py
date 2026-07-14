from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "unity-text-locator" / "scripts" / "install_tmp_chinese_font_fix.py"
FIXER_SOURCE = REPO_ROOT / "unity-text-locator" / "assets" / "ChineseFontFixer.cs"
SPEC = importlib.util.spec_from_file_location("font_fix_installer", INSTALLER)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class RuntimeFontTests(unittest.TestCase):
    def test_runtime_font_destination_is_game_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "NotoSansCJKsc-Regular.otf"
            source.write_bytes(b"font")
            destination = installer.runtime_font_destination(root / "Game_Data", source)
            self.assertEqual(destination, root / "Game_Data" / "ChineseFontFixer" / "Fonts" / source.name)

    def test_runtime_font_rejects_non_font_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "payload.bin"
            source.write_bytes(b"not-font")
            with self.assertRaises(SystemExit):
                installer.runtime_font_destination(Path(temporary) / "Game_Data", source)

    def test_runtime_font_rejects_empty_font(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "empty.ttf"
            source.touch()
            with self.assertRaises(SystemExit):
                installer.runtime_font_destination(Path(temporary) / "Game_Data", source)

    def test_commit_creates_nested_font_directory_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Game"
            backup = Path(temporary) / "backup"
            existing = root / "Game_Data" / "config.json"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"old")
            font = root / "Game_Data" / "ChineseFontFixer" / "Fonts" / "Noto.otf"
            written, leftovers = installer.commit_outputs(
                {existing: b"new", font: b"font"}, root, backup
            )
            self.assertEqual(existing.read_bytes(), b"new")
            self.assertEqual(font.read_bytes(), b"font")
            self.assertEqual((backup / "Game_Data" / "config.json").read_bytes(), b"old")
            self.assertIn("Game_Data/ChineseFontFixer/Fonts/Noto.otf", written)
            self.assertEqual(leftovers, [])

    def test_commit_rolls_back_font_when_a_later_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Game"
            backup = Path(temporary) / "backup"
            font = root / "Game_Data" / "ChineseFontFixer" / "Fonts" / "Noto.otf"
            blocked_parent = root / "blocked"
            blocked_parent.parent.mkdir(parents=True)
            blocked_parent.write_bytes(b"not-a-directory")
            with self.assertRaises(OSError):
                installer.commit_outputs(
                    {font: b"font", blocked_parent / "later.json": b"later"}, root, backup
                )
            self.assertFalse(font.exists())

    def test_runtime_does_not_preflight_new_font_with_has_character(self) -> None:
        source = FIXER_SOURCE.read_text(encoding="utf-8")
        create_legacy = source.split("private Font CreateLegacyFont()", 1)[1].split(
            "private TMP_FontAsset CreateDynamicTMPFontAsset", 1
        )[0]
        self.assertNotIn("HasCharacter", create_legacy)
        self.assertLess(source.index("privateDirectory"), source.index("SpecialFolder.Fonts"))


if __name__ == "__main__":
    unittest.main()
