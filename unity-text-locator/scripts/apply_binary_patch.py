#!/usr/bin/env python3
"""Apply a verified Unity translation binary patch package with rollback backups."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_relative(value: str) -> Path:
    if not value or "\x00" in value:
        raise SystemExit(f"Unsafe patch path in manifest: {value}")
    normalized = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    if (
        normalized.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in normalized.parts
        or not normalized.parts
    ):
        raise SystemExit(f"Unsafe patch path in manifest: {value}")
    return Path(*normalized.parts)


def contained_path(root: Path, relative: Path, *, must_exist: bool = True) -> Path:
    """Resolve a manifest path and require it to stay below root, including links."""
    root = root.resolve()
    candidate = (root / relative).resolve(strict=must_exist)
    try:
        candidate.relative_to(root)
    except ValueError:
        raise SystemExit(f"Patch path escapes its root: {relative.as_posix()}") from None
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--game-root", required=True, type=Path)
    args = parser.parse_args()

    package = args.package.resolve()
    root = args.game_root.resolve()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "unity-cn-middle-span-v1" or manifest.get("payload_compression") != "gzip":
        raise SystemExit("Unsupported patch manifest format")

    outputs: dict[Path, bytes] = {}
    skipped_current: list[str] = []
    for entry in manifest.get("files", []):
        relative = checked_relative(str(entry["path"]))
        target = contained_path(root, relative, must_exist=False)
        if not target.exists():
            raise SystemExit(f"Target file missing: {relative.as_posix()}")
        current = target.read_bytes()
        current_hash = sha256(current)
        if current_hash == entry["new_sha256"]:
            skipped_current.append(relative.as_posix())
            continue
        if current_hash != entry["old_sha256"]:
            raise SystemExit(f"Target does not match original or translated hash: {relative.as_posix()}")
        prefix = int(entry["prefix_bytes"])
        suffix = int(entry["suffix_bytes"])
        payload_relative = checked_relative(str(entry["payload"]))
        payload = contained_path(package, payload_relative, must_exist=False)
        if not payload.is_file():
            raise SystemExit(f"Patch payload missing: {payload_relative.as_posix()}")
        middle = gzip.decompress(payload.read_bytes())
        suffix_bytes = current[len(current) - suffix :] if suffix else b""
        translated = current[:prefix] + middle + suffix_bytes
        if sha256(translated) != entry["new_sha256"]:
            raise SystemExit(f"Payload hash verification failed: {relative.as_posix()}")
        outputs[target] = translated

    backup_root = root / ("_cn_patch_backup_" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    written: list[Path] = []
    if outputs:
        for target in outputs:
            backup = backup_root / target.relative_to(root)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        try:
            for target, data in outputs.items():
                temporary = target.with_name(target.name + ".cnpatch.tmp")
                temporary.write_bytes(data)
                os.replace(temporary, target)
                written.append(target)
        except Exception:
            for target in written:
                shutil.copy2(backup_root / target.relative_to(root), target)
            for target in outputs:
                temporary = target.with_name(target.name + ".cnpatch.tmp")
                if temporary.exists():
                    temporary.unlink()
            raise

    result = {
        "game_root": str(root),
        "written_files": [path.relative_to(root).as_posix() for path in written],
        "already_patched": skipped_current,
        "backup_root": str(backup_root) if written else None,
    }
    if written:
        (backup_root / "install_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
