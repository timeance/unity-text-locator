#!/usr/bin/env python3
"""Build a verified binary middle-span patch package from backups and translated files."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_relative(value: str) -> Path:
    normalized = PurePosixPath(value.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise SystemExit(f"Unsafe relative patch path: {value}")
    return Path(*normalized.parts)


def split_middle(original: bytes, translated: bytes) -> tuple[int, int, bytes]:
    prefix = 0
    prefix_limit = min(len(original), len(translated))
    while prefix < prefix_limit and original[prefix] == translated[prefix]:
        prefix += 1
    suffix = 0
    suffix_limit = min(len(original) - prefix, len(translated) - prefix)
    while suffix < suffix_limit and original[len(original) - suffix - 1] == translated[len(translated) - suffix - 1]:
        suffix += 1
    end = len(translated) - suffix if suffix else len(translated)
    return prefix, suffix, translated[prefix:end]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", required=True, type=Path, help="Current translated game root")
    parser.add_argument("--backup-root", required=True, type=Path, help="Backup directory mirroring original relative paths")
    parser.add_argument("--file", action="append", required=True, dest="files", help="Changed relative game path; repeat for each file")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--zip", action="store_true", dest="make_zip", help="Also create a zip archive next to out-dir")
    args = parser.parse_args()

    game_root = args.game_root.resolve()
    backup_root = args.backup_root.resolve()
    out_dir = args.out_dir.resolve()
    payload_dir = out_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for requested in args.files:
        relative = checked_relative(requested)
        original_path = backup_root / relative
        translated_path = game_root / relative
        if not original_path.exists() or not translated_path.exists():
            raise SystemExit(f"Patch source missing for {relative.as_posix()}")
        original = original_path.read_bytes()
        translated = translated_path.read_bytes()
        if original == translated:
            raise SystemExit(f"File has no changes: {relative.as_posix()}")
        prefix, suffix, middle = split_middle(original, translated)
        compressed = gzip.compress(middle, compresslevel=9)
        payload_name = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:20] + ".bin.gz"
        payload_relative = Path("payloads") / payload_name
        (out_dir / payload_relative).write_bytes(compressed)
        entries.append(
            {
                "path": relative.as_posix(),
                "old_sha256": sha256(original),
                "new_sha256": sha256(translated),
                "old_size": len(original),
                "new_size": len(translated),
                "prefix_bytes": prefix,
                "suffix_bytes": suffix,
                "payload": payload_relative.as_posix(),
                "payload_compressed_bytes": len(compressed),
                "payload_raw_bytes": len(middle),
            }
        )

    manifest = {"format": "unity-cn-middle-span-v1", "payload_compression": "gzip", "files": entries}
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_script = Path(__file__).with_name("apply_binary_patch.py")
    shutil.copy2(apply_script, out_dir / apply_script.name)
    powershell_script = Path(__file__).resolve().parents[1] / "assets" / "apply_cn_patch.ps1.txt"
    shutil.copy2(powershell_script, out_dir / powershell_script.name)
    installer = out_dir / "install_cn_patch.bat"
    installer.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "if \"%~1\"==\"\" (\r\n"
        "  echo Usage: install_cn_patch.bat \"path-to-game-folder\"\r\n"
        "  exit /b 2\r\n"
        ")\r\n"
        "set \"PATCH_DIR=%~dp0.\"\r\n"
        "set \"PATCH_GAME_ROOT=%~1\"\r\n"
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \"$code = Get-Content -Raw -LiteralPath (Join-Path $env:PATCH_DIR 'apply_cn_patch.ps1.txt'); & ([ScriptBlock]::Create($code)) -PatchDir $env:PATCH_DIR -GameRoot $env:PATCH_GAME_ROOT\"\r\n"
        "exit /b %errorlevel%\r\n",
        encoding="utf-8-sig",
    )
    zip_path = None
    if args.make_zip:
        zip_path = out_dir.parent / (out_dir.name + ".zip")
    result = {
        "manifest": str(manifest_path),
        "installer": str(installer),
        "file_count": len(entries),
        "zip": str(zip_path) if zip_path else None,
        "files": entries,
    }
    (out_dir / "build_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.make_zip:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(out_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(out_dir.parent))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
