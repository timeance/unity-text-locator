#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import struct
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import UnityPy  # type: ignore


PLACEHOLDER_RE = re.compile(r"(<[^>]+>|\\[nrt]|\{[^{}]+\}|%[sdif]|\$[A-Za-z0-9_]+|\[[A-Za-z0-9_:-]+\])")
TAG_RE = re.compile(r"<[^>]*>")


def unflat(text: str) -> str:
    return text.replace("\\n", "\n")


def read_source(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row if row else [""] for row in csv.reader(handle)]
    if rows and rows[0] == ["original_flat"]:
        rows = rows[1:]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one original_flat column")
    return [row[0] for row in rows]


def read_translations(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row if row else [""] for row in csv.reader(handle)]
    if rows and rows[0] in (["zh_cn"], ["original_flat"]):
        rows = rows[1:]
    elif rows and rows[0] == ["original_flat", "zh_cn"]:
        rows = [[row[1]] for row in rows[1:]]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one zh_cn column")
    return [row[0] for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--translation-csv", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--allow-newline-changes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    asset = Path(manifest["asset"])
    source_rows = read_source(args.source_csv)
    translations = read_translations(args.translation_csv)
    if len(source_rows) != len(translations):
        raise SystemExit(f"row count mismatch: source={len(source_rows)} translation={len(translations)}")
    expected_hash = manifest.get("source_sha256")
    if expected_hash and hashlib.sha256(asset.read_bytes()).hexdigest() != expected_hash:
        raise SystemExit("profile asset SHA256 changed since extraction")

    selected: list[dict] = []
    for occurrence in manifest["occurrences"]:
        text_row = int(occurrence["text_row"])
        original = source_rows[text_row - 1]
        if original != occurrence["original_flat"]:
            raise SystemExit(f"manifest/source mismatch at text row {text_row}")
        zh_cn = translations[text_row - 1]
        if not zh_cn.strip() or zh_cn == original:
            continue
        if sorted(PLACEHOLDER_RE.findall(original)) != sorted(PLACEHOLDER_RE.findall(zh_cn)):
            raise SystemExit(f"placeholder mismatch at text row {text_row}")
        if sorted(TAG_RE.findall(original)) != sorted(TAG_RE.findall(zh_cn)):
            raise SystemExit(f"tag mismatch at text row {text_row}")
        if not args.allow_newline_changes and original.count("\\n") != zh_cn.count("\\n"):
            raise SystemExit(f"newline count mismatch at text row {text_row}")
        mapped = dict(occurrence)
        mapped["zh_cn"] = zh_cn
        selected.append(mapped)

    env = UnityPy.load(str(asset))
    objects = {obj.path_id: obj for obj in env.objects}
    by_path_id: dict[int, list[dict]] = defaultdict(list)
    for occurrence in selected:
        by_path_id[int(occurrence["path_id"])].append(occurrence)

    patched: list[dict] = []
    skipped: list[dict] = []
    for path_id, occurrences in by_path_id.items():
        obj = objects.get(path_id)
        if obj is None:
            skipped.extend({"path_id": path_id, "reason": "object missing", **occurrence} for occurrence in occurrences)
            continue
        raw = bytearray(obj.get_raw_data())
        delta = 0
        changed = False
        for occurrence in sorted(occurrences, key=lambda item: int(item["offset"])):
            original = occurrence["original_flat"]
            offset = int(occurrence["offset"]) + delta
            old_len = int(occurrence["byte_length"])
            start = offset + 4
            end = start + old_len
            pad_end = (end + 3) & ~3
            if offset < 0 or pad_end > len(raw):
                skipped.append({"path_id": path_id, "text_row": occurrence["text_row"], "reason": "span outside object"})
                continue
            current = bytes(raw[start:end]).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
            if current != original:
                skipped.append({"path_id": path_id, "text_row": occurrence["text_row"], "reason": "current text mismatch"})
                continue
            new_bytes = unflat(occurrence["zh_cn"]).encode("utf-8")
            new_end = start + len(new_bytes)
            new_pad_end = (new_end + 3) & ~3
            segment = struct.pack("<I", len(new_bytes)) + new_bytes + b"\x00" * (new_pad_end - new_end)
            raw[offset:pad_end] = segment
            delta += len(segment) - (pad_end - offset)
            changed = True
            patched.append({"path_id": path_id, "text_row": occurrence["text_row"], "old_bytes": old_len, "new_bytes": len(new_bytes)})
        if changed and not args.dry_run:
            obj.set_raw_data(bytes(raw))

    backup_path = None
    if patched and not args.dry_run:
        backup_root = args.out_dir / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_root / asset.name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, backup_path)
        asset_file = next(iter(env.files.values()))
        asset.write_bytes(asset_file.save())

    result = {
        "dry_run": args.dry_run,
        "asset": str(asset),
        "translation_rows_used": len(selected),
        "patched_total": len(patched),
        "skipped": skipped,
        "backup_path": str(backup_path) if backup_path else None,
    }
    report = args.out_dir / ("character_profile_writeback_dryrun.json" if args.dry_run else "character_profile_writeback_report.json")
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
