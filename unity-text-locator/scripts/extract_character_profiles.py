#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
from pathlib import Path

import UnityPy  # type: ignore

JP_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")


def iter_strings(raw: bytes):
    for off in range(0, len(raw) - 4):
        n = struct.unpack_from("<I", raw, off)[0]
        if not (0 < n < 5000 and off + 4 + n <= len(raw)):
            continue
        b = raw[off + 4 : off + 4 + n]
        try:
            s = b.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if s and all(ch in "\n\t" or ord(ch) >= 32 for ch in s):
            yield off, n, s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True, type=Path)
    ap.add_argument("--out-csv", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    args = ap.parse_args()

    asset = args.asset.resolve()
    env = UnityPy.load(str(asset))
    rows: list[dict[str, str]] = []
    manifest: list[dict[str, str | int]] = []

    profile_objects: set[int] = set()
    raw_by_pid: dict[int, bytes] = {}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        raw = obj.get_raw_data()
        raw_by_pid[obj.path_id] = raw
        strings = list(iter_strings(raw))
        # Character profile records have a compact ID near the front and a long
        # Japanese description later in the same MonoBehaviour.
        if any(len(s) >= 40 and JP_RE.search(s) for _off, _n, s in strings):
            profile_objects.add(obj.path_id)

    for obj in env.objects:
        if obj.path_id not in profile_objects:
            continue
        raw = raw_by_pid[obj.path_id]
        for off, n, s in iter_strings(raw):
            if not JP_RE.search(s):
                continue
            original_flat = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
            row = {
                "path_id": obj.path_id,
                "byte_start": obj.byte_start,
                "offset": off,
                "byte_length": n,
                "original_flat": original_flat,
            }
            row["text_row"] = len(rows) + 1
            manifest.append(row)
            rows.append({"original_flat": original_flat})

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["original_flat"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    args.manifest.write_text(
        json.dumps(
            {
                "format": "profile-text-v1",
                "root": str(asset.parent),
                "asset": str(asset),
                "source_file": asset.name,
                "source_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                "source_csv": args.out_csv.name,
                "occurrences": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"text_rows": len(rows), "occurrences": len(manifest), "objects": len(profile_objects)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
