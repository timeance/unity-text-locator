#!/usr/bin/env python3
"""Extract visible Utage book text into an occurrence-preserving one-column CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import UnityPy  # type: ignore

from extract_mono_raw_utf8 import newline_events, scan_object


DEFAULT_BOOKS = ("ALL_Texts.book", "ALL_EventTexts.book")


def flat(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("\r\n", index):
            result.append(r"\N")
            index += 2
            continue
        char = text[index]
        result.append({"\\": r"\\", "\r": r"\r", "\n": r"\n", "\t": r"\t"}.get(char, char))
        index += 1
    return "".join(result)


def detect_game_root(asset: Path) -> Path:
    for parent in asset.parents:
        if parent.name.lower().endswith("_data"):
            return parent.parent
    return asset.parent


def safe_filename_component(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" ._") or "game"


def source_name(root: Path, description: str) -> str:
    return safe_filename_component(root.name) + "_" + safe_filename_component(description)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for *_utage_text.csv and manifest")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--book", action="append", default=None, help="Utage book name to extract; repeat for multiple books")
    parser.add_argument("--text-column", type=int, default=8, help="Index of visible Text cell in Utage rows")
    parser.add_argument("--description", default="utage_text", help="Translator filename description after the project folder name")
    args = parser.parse_args()

    asset = args.asset.resolve()
    root = (args.game_root.resolve() if args.game_root else detect_game_root(asset))
    try:
        source_file = asset.relative_to(root).as_posix()
    except ValueError:
        source_file = asset.name
    books = set(args.book or DEFAULT_BOOKS)

    env = UnityPy.load(str(asset))
    source_rows: list[dict[str, str]] = []
    occurrences: list[dict[str, object]] = []
    skipped_objects: list[dict[str, object]] = []
    book_counts: dict[str, int] = {}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception as exc:
            raw_candidates = scan_object(obj.get_raw_data(), 12000, [])
            skipped_objects.append(
                {
                    "path_id": obj.path_id,
                    "reason": str(exc),
                    "raw_japanese_candidates": len(raw_candidates),
                }
            )
            continue
        book = tree.get("m_Name")
        if book not in books:
            continue
        for grid_index, grid in enumerate(tree.get("importGridList", [])):
            for row_position, row in enumerate(grid.get("rows", [])):
                strings = row.get("strings", [])
                if len(strings) <= args.text_column:
                    continue
                original = str(strings[args.text_column])
                original_flat = flat(original)
                if not original_flat.strip():
                    continue
                text_row = len(source_rows) + 1
                source_rows.append({"original_flat": original_flat})
                occurrences.append(
                    {
                        "text_row": text_row,
                        "book": book,
                        "path_id": obj.path_id,
                        "grid_index": grid_index,
                        "row_position": row_position,
                        "row_index": row.get("rowIndex"),
                        "text_column": args.text_column,
                        "original_flat": original_flat,
                        "newline_events": newline_events(original),
                    }
                )
                book_counts[str(book)] = book_counts.get(str(book), 0) + 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = source_name(root, args.description)
    source_csv = args.out_dir / f"{stem}.csv"
    manifest_path = args.out_dir / f"{stem}.manifest.json"
    with source_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["original_flat"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(source_rows)
    missing_books = sorted(books - set(book_counts))
    unread_raw_candidates = sum(int(item.get("raw_japanese_candidates", 0)) for item in skipped_objects)
    coverage_complete = not missing_books and unread_raw_candidates == 0
    manifest = {
        "format": "utage-text-v2",
        "text_escape": "backslash-controls-exact-v1",
        "root": str(root),
        "asset": str(asset),
        "source_file": source_file,
        "source_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
        "source_csv": source_csv.name,
        "translation_csv": source_csv.stem + "_translation.csv",
        "books": sorted(books),
        "text_column": args.text_column,
        "occurrences": occurrences,
        "skipped_objects": skipped_objects,
        "missing_books": missing_books,
        "unread_raw_japanese_candidates": unread_raw_candidates,
        "coverage_complete": coverage_complete,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "asset": str(asset),
        "source_csv": str(source_csv),
        "manifest": str(manifest_path),
        "rows": len(source_rows),
        "book_counts": book_counts,
        "skipped_objects": len(skipped_objects),
        "missing_books": missing_books,
        "unread_raw_japanese_candidates": unread_raw_candidates,
        "coverage_complete": coverage_complete,
        "fallback": "Run extract_mono_raw_utf8.py for skipped MonoBehaviour objects before writeback.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if coverage_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
