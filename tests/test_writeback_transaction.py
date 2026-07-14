from __future__ import annotations

import importlib.util
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "unity-text-locator" / "scripts" / "writeback_unity_text.py"
SPEC = importlib.util.spec_from_file_location("writeback_unity_text", SCRIPT)
assert SPEC and SPEC.loader
writeback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(writeback)


class WritebackTransactionTests(unittest.TestCase):
    def test_resolve_under_root_rejects_parent_and_drive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for unsafe in ("../outside", r"C:\outside\file", r"\\server\share\file"):
                with self.subTest(unsafe=unsafe), self.assertRaises(SystemExit):
                    writeback.resolve_under_root(root, unsafe)

    def test_commit_staged_files_creates_backup_and_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            real_root = tmp_path / "game"
            stage_root = tmp_path / "stage"
            backup_root = tmp_path / "backups"
            relative = "Game_Data/resources.assets"
            target = real_root / relative
            staged = stage_root / relative
            target.parent.mkdir(parents=True)
            staged.parent.mkdir(parents=True)
            target.write_bytes(b"original")
            staged.write_bytes(b"translated")

            written = writeback.commit_staged_files(
                real_root,
                stage_root,
                [relative],
                {relative: writeback.file_sha256(target)},
                backup_root,
            )

            self.assertEqual(written, [relative])
            self.assertEqual(target.read_bytes(), b"translated")
            self.assertEqual((backup_root / relative).read_bytes(), b"original")

    def test_commit_refuses_source_changed_after_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            real_root = tmp_path / "game"
            stage_root = tmp_path / "stage"
            backup_root = tmp_path / "backups"
            relative = "sharedassets0.assets"
            target = real_root / relative
            staged = stage_root / relative
            target.parent.mkdir(parents=True)
            staged.parent.mkdir(parents=True)
            target.write_bytes(b"changed")
            staged.write_bytes(b"translated")

            with self.assertRaisesRegex(RuntimeError, "source changed"):
                writeback.commit_staged_files(
                    real_root,
                    stage_root,
                    [relative],
                    {relative: writeback.file_sha256(staged)},
                    backup_root,
                )

            self.assertEqual(target.read_bytes(), b"changed")
            self.assertFalse(backup_root.exists())

    def test_commit_rolls_back_earlier_files_when_later_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            real_root = tmp_path / "game"
            stage_root = tmp_path / "stage"
            backup_root = tmp_path / "backups"
            relatives = ["a.assets", "b.assets"]
            expected = {}
            for relative in relatives:
                target = real_root / relative
                staged = stage_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                staged.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(("old-" + relative).encode())
                staged.write_bytes(("new-" + relative).encode())
                expected[relative] = writeback.file_sha256(target)

            real_atomic_copy = writeback.atomic_copy
            second_staged = (stage_root / "b.assets").resolve()

            def fail_second_staged_copy(source: Path, target: Path) -> None:
                if source.resolve() == second_staged:
                    raise OSError("simulated commit failure")
                real_atomic_copy(source, target)

            with mock.patch.object(writeback, "atomic_copy", side_effect=fail_second_staged_copy):
                with self.assertRaisesRegex(OSError, "simulated"):
                    writeback.commit_staged_files(
                        real_root, stage_root, relatives, expected, backup_root
                    )

            self.assertEqual((real_root / "a.assets").read_bytes(), b"old-a.assets")
            self.assertEqual((real_root / "b.assets").read_bytes(), b"old-b.assets")


if __name__ == "__main__":
    unittest.main()
