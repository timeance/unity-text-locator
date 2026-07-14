import importlib.util
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "unity-text-locator" / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


APPLY = load_script("apply_binary_patch.py")
BUILD = load_script("build_binary_patch.py")


class CheckedRelativeTests(unittest.TestCase):
    def test_accepts_nested_relative_path(self):
        for module in (APPLY, BUILD):
            self.assertEqual(module.checked_relative("Game_Data/resources.assets"), Path("Game_Data/resources.assets"))

    def test_rejects_windows_and_parent_escapes(self):
        unsafe = (
            r"C:\outside\file",
            r"C:outside\file",
            r"\\server\share\file",
            r"\rooted\file",
            "/absolute/file",
            "../outside/file",
            "inside/../../outside",
            "",
        )
        for module in (APPLY, BUILD):
            for value in unsafe:
                with self.subTest(module=module.__name__, value=value):
                    with self.assertRaises(SystemExit):
                        module.checked_relative(value)


class ContainmentTests(unittest.TestCase):
    def test_normal_child_is_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "nested" / "file.bin"
            child.parent.mkdir()
            child.write_bytes(b"data")
            for module in (APPLY, BUILD):
                self.assertEqual(module.contained_path(root, Path("nested/file.bin")), child.resolve())

    def test_absolute_relative_object_cannot_override_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside.bin"
            root.mkdir()
            outside.write_bytes(b"data")
            for module in (APPLY, BUILD):
                with self.assertRaises(SystemExit):
                    module.contained_path(root, outside)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "payload.bin").write_bytes(b"data")
            try:
                (root / "linked").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"cannot create symlink: {error}")
            for module in (APPLY, BUILD):
                with self.assertRaises(SystemExit):
                    module.contained_path(root, Path("linked/payload.bin"))


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell unavailable")
class PowerShellInstallerTests(unittest.TestCase):
    def run_installer(self, package: Path, game: Path) -> subprocess.CompletedProcess[str]:
        template = ROOT / "unity-text-locator" / "assets" / "apply_cn_patch.ps1.txt"
        def quote(value: Path) -> str:
            return "'" + str(value).replace("'", "''") + "'"
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f"$code = Get-Content -Raw -LiteralPath {quote(template)}; "
            f"& ([ScriptBlock]::Create($code)) -PatchDir {quote(package)} -GameRoot {quote(game)}"
        )
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_manifest(self, package: Path, *, target: str, payload: str, current: bytes = b"old") -> None:
        manifest = {
            "format": "unity-cn-middle-span-v1",
            "payload_compression": "gzip",
            "files": [{
                "path": target,
                "payload": payload,
                "old_sha256": hashlib.sha256(current).hexdigest(),
                "new_sha256": hashlib.sha256(b"new").hexdigest(),
                "prefix_bytes": 0,
                "suffix_bytes": 0,
            }],
        }
        (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_rejects_drive_qualified_target(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package, game = base / "package", base / "game"
            package.mkdir()
            game.mkdir()
            self.write_manifest(package, target=r"C:outside.bin", payload="payload.bin.gz")
            result = self.run_installer(package, game)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Unsafe patch path", result.stdout + result.stderr)

    def test_rejects_payload_parent_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package, game = base / "package", base / "game"
            package.mkdir()
            game.mkdir()
            (game / "target.bin").write_bytes(b"old")
            (base / "payload.bin.gz").write_bytes(gzip.compress(b"new"))
            self.write_manifest(package, target="target.bin", payload="../payload.bin.gz")
            result = self.run_installer(package, game)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Unsafe patch path", result.stdout + result.stderr)

    def test_accepts_contained_target_and_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package, game = base / "package", base / "game"
            (package / "payloads").mkdir(parents=True)
            (game / "data").mkdir(parents=True)
            (game / "data" / "target.bin").write_bytes(b"old")
            (package / "payloads" / "middle.bin.gz").write_bytes(gzip.compress(b"new"))
            self.write_manifest(package, target="data/target.bin", payload="payloads/middle.bin.gz")
            result = self.run_installer(package, game)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((game / "data" / "target.bin").read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()
