#!/usr/bin/env python3
"""Convert an AiNiee cache.json back into a Unity one-column translation CSV."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


def default_ainiee_scripts() -> Path:
    here = Path(__file__).resolve()
    repo_sibling = here.parents[2] / "ainiee-translate" / "scripts"
    if repo_sibling.exists():
        return repo_sibling
    skill_dir = os.environ.get("AINIEE_SKILL_DIR")
    if skill_dir:
        return Path(skill_dir) / "scripts"
    return repo_sibling


def add_ainiee_path(path: Path) -> None:
    expected = path / "ainiee_translate"
    if not expected.exists():
        raise SystemExit(
            f"cannot find ainiee_translate package under {path}; "
            "set --ainiee-scripts or AINIEE_SKILL_DIR to the ainiee-translate skill"
        )
    sys.path.insert(0, str(path))


def read_source_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = [field.lstrip("\ufeff") for field in (reader.fieldnames or [])]
        if fields != ["original_flat"]:
            raise SystemExit(f"{path} must contain exactly one original_flat column")
        return sum(1 for _ in reader)


def main() -> int:
    parser = argparse.ArgumentParser(description="AiNiee cache.json -> Unity zh_cn CSV")
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--out-csv", required=True, type=Path)
    parser.add_argument(
        "--source-csv",
        type=Path,
        default=None,
        help="Optional Unity source CSV used to enforce output row count and order.",
    )
    parser.add_argument(
        "--ainiee-scripts",
        type=Path,
        default=default_ainiee_scripts(),
        help="Path to ainiee-translate/scripts; defaults to the sibling repo skill or AINIEE_SKILL_DIR/scripts.",
    )
    args = parser.parse_args()

    add_ainiee_path(args.ainiee_scripts)

    from ainiee_translate import cache_io

    project = cache_io.load_cache(str(args.cache))
    by_index = {
        int(item.text_index): (item.translated_text or "")
        for item in cache_io.iter_items(project)
    }

    if args.source_csv:
        row_count = read_source_count(args.source_csv)
        values = [by_index.get(index, "") for index in range(1, row_count + 1)]
    else:
        values = [by_index[index] for index in sorted(by_index)]

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["zh_cn"])
        writer.writerows([[value] for value in values])

    nonblank = sum(bool(value.strip()) for value in values)
    print(f"wrote {len(values)} row(s), {nonblank} nonblank -> {args.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
