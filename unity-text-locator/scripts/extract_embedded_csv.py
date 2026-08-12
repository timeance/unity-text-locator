#!/usr/bin/env python3
"""Extract occurrence-preserving CSV tables embedded in Unity binaries."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any


SIGNATURES = (b"ID,\xe5\x90\x8d\xe5\x89\x8d", b"ID,Name", b"ID,\xe5\x90\x8d\xe7\xa7\xb0", b"Name,Description")
TRANSLATABLE_COLUMNS = {"名前", "名称", "説明", "タイトル", "物語", "報酬", "Name", "Title", "Description", "Story", "Reward", "text", "label", "message"}
IGNORED_COLUMNS = {"ID", "id", "type", "Type", "price", "cost", "flag", "count", "amount", "売値", "買値", "効果"}
ROW_RE = re.compile(r"(?m)^(?:\"?\d+\"?|\"?[A-Za-z][A-Za-z0-9_-]{0,32}\"?),")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode(data: bytes) -> str | None:
    for encoding in ("utf-8", "cp932"):
        try:
            return data.replace(b"\x00", b"").decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def extract_table(data: bytes, offset: int) -> str | None:
    start = offset
    while start > 0 and data[start - 1:start] not in (b"\n", b"\r", b"\x00"):
        start -= 1
    end = min(len(data), offset + 65536)
    null = data.find(b"\x00\x00\x00\x00", offset, end)
    if null != -1:
        end = null
    text = decode(data[start:end])
    if not text or len(text.splitlines()) < 3 or not ROW_RE.search(text):
        return None
    return text.strip("\r\n")


def parse_table(text: str, source_file: str, offset: int, source_sha256: str) -> dict[str, Any] | None:
    try:
        records = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error:
        return None
    if len(records) < 2:
        return None
    header = records[0]
    if len(header) < 2:
        return None
    columns = []
    for index, name in enumerate(header):
        clean = name.strip()
        if clean in TRANSLATABLE_COLUMNS or (clean not in IGNORED_COLUMNS and re.search(r"[ぁ-んァ-ン一-龯]", clean)):
            columns.append(index)
    if not columns:
        return None
    occurrences: list[dict[str, Any]] = []
    row_count = 0
    for record_number, row in enumerate(records[1:], 2):
        if len(row) != len(header):
            continue
        row_count += 1
        row_id = row[0].strip() if row else str(record_number)
        for column_index in columns:
            value = row[column_index].strip()
            if len(value) < 2:
                continue
            occurrences.append({"original_flat": value, "source_file": source_file, "embedded_offset": offset, "record_number": record_number, "row_id": row_id, "column": header[column_index].strip()})
    if not row_count or not occurrences:
        return None
    return {"source_file": source_file, "source_sha256": source_sha256, "embedded_offset": offset, "columns": header, "row_count": row_count, "occurrences": occurrences}


def scan_file(path: Path, game_root: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    try:
        source_file = path.relative_to(game_root).as_posix()
    except ValueError:
        source_file = path.as_posix()
    source_hash = file_sha256(path)
    results: list[dict[str, Any]] = []
    for signature in SIGNATURES:
        cursor = 0
        while True:
            offset = data.find(signature, cursor)
            if offset < 0:
                break
            table = extract_table(data, offset)
            if table:
                parsed = parse_table(table, source_file, offset, source_hash)
                if parsed:
                    parsed["signature"] = signature.decode("utf-8", errors="replace")
                    results.append(parsed)
            cursor = offset + len(signature)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract embedded CSV occurrences from Unity assets")
    parser.add_argument("game_root", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    game_root = args.game_root.resolve()
    candidates = sorted(set(game_root.rglob("sharedassets*.assets")) | set(game_root.rglob("*.bundle")))
    tables: list[dict[str, Any]] = []
    for path in candidates:
        try:
            tables.extend(scan_file(path, game_root))
        except OSError:
            continue
    occurrences = [row for table in tables for row in table["occurrences"]]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "embedded_csv_occurrences.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["original_flat", "source_file", "embedded_offset", "record_number", "row_id", "column"])
        writer.writeheader()
        writer.writerows(occurrences)
    manifest = {"format": "unity-embedded-csv-occurrences-v1", "game_root": str(game_root), "tables": tables, "occurrence_count": len(occurrences), "duplicate_texts_preserved": True, "writeback": "not_supported_by_this_discovery_adapter"}
    (args.out_dir / "embedded_csv_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files_scanned": len(candidates), "tables": len(tables), "occurrences": len(occurrences), "csv": str(csv_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
