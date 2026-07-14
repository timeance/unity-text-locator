#!/usr/bin/env python3
"""Convert an AiNiee cache.json back into a Unity one-column translation CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
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


def read_source_rows(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = [field.lstrip("\ufeff") for field in (reader.fieldnames or [])]
        if fields != ["original_flat"]:
            raise SystemExit(f"{path} must contain exactly one original_flat column")
        return [row.get("original_flat", "") for row in reader]


def source_rows_sha256(rows: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        encoded = row.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


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
        "--allow-partial",
        action="store_true",
        help="Allow missing, untranslated, or blank rows; unsafe for final writeback.",
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
    items = list(cache_io.iter_items(project))
    indexes = [int(item.text_index) for item in items]
    if any(index <= 0 for index in indexes) or len(indexes) != len(set(indexes)):
        raise SystemExit("cache contains invalid or duplicate text_index values")
    by_index = {int(item.text_index): item for item in items}

    if args.source_csv:
        source_rows = read_source_rows(args.source_csv)
        row_count = len(source_rows)
        expected_hash = (project.extra or {}).get("source_rows_sha256")
        actual_hash = source_rows_sha256(source_rows)
        if not expected_hash:
            raise SystemExit("cache has no source_rows_sha256; recreate it from the Unity source CSV")
        if expected_hash != actual_hash:
            raise SystemExit("source CSV content hash does not match the cache")
        source_mismatches = [
            index for index, source in enumerate(source_rows, start=1)
            if index in by_index and (by_index[index].source_text or "") != source
        ]
        if source_mismatches:
            raise SystemExit(f"cache source text differs at {len(source_mismatches)} row(s)")
    else:
        row_count = max(indexes, default=0)

    expected_indexes = set(range(1, row_count + 1))
    missing = sorted(expected_indexes - set(by_index))
    extra = sorted(set(by_index) - expected_indexes)
    incomplete = []
    values = []
    for index in range(1, row_count + 1):
        item = by_index.get(index)
        value = (item.translated_text or "") if item else ""
        if item is None or item.translation_status not in (1, 2) or not value.strip():
            incomplete.append(index)
        values.append(value)
    if (missing or extra or incomplete) and not args.allow_partial:
        raise SystemExit(
            "cache is incomplete: "
            f"{len(missing)} missing, {len(extra)} out-of-range, "
            f"{len(incomplete)} untranslated/blank row(s); use --allow-partial only for review"
        )

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
