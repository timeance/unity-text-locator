#!/usr/bin/env python3
"""Convert a Unity one-column source CSV into an AiNiee cache.json.

The generated cache is meant for the sibling ainiee-translate skill. It keeps
the Unity CSV row number as text_index so translated rows can be projected back
to the exact Unity translation CSV order.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


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


def read_source_csv(path: Path) -> list[str]:
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
    parser = argparse.ArgumentParser(description="Unity source CSV -> AiNiee cache.json")
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--out-cache", required=True, type=Path)
    parser.add_argument(
        "--ainiee-scripts",
        type=Path,
        default=default_ainiee_scripts(),
        help="Path to ainiee-translate/scripts; defaults to the sibling repo skill or AINIEE_SKILL_DIR/scripts.",
    )
    parser.add_argument("--project-name", default=None)
    args = parser.parse_args()

    add_ainiee_path(args.ainiee_scripts)

    from ainiee_translate import cache_io
    from ainiee_translate._vendor.ModuleFolders.Service.Cache.CacheFile import CacheFile
    from ainiee_translate._vendor.ModuleFolders.Service.Cache.CacheItem import CacheItem
    from ainiee_translate._vendor.ModuleFolders.Service.Cache.CacheProject import (
        CacheProject,
        CacheProjectStatistics,
        ProjectType,
    )

    rows = read_source_csv(args.source_csv)
    source_hash = source_rows_sha256(rows)
    project_name = args.project_name or args.source_csv.stem
    cache_file = CacheFile(
        storage_path=args.source_csv.name,
        encoding="utf-8-sig",
        file_project_type=ProjectType.CSV,
        line_ending="\n",
        extra={"source_format": "unity-one-column-csv", "source_rows_sha256": source_hash},
    )
    for index, source_text in enumerate(rows, start=1):
        cache_file.add_item(
            CacheItem(
                text_index=index,
                source_text=source_text,
                translated_text="",
                extra={
                    "unity_source_csv": args.source_csv.name,
                    "unity_csv_data_row": index,
                },
            )
        )

    project = CacheProject(
        project_id=str(uuid4()),
        project_type=ProjectType.CSV,
        project_name=project_name,
        project_create_time=datetime.now(timezone.utc).isoformat(),
        input_path=str(args.source_csv),
        stats_data=CacheProjectStatistics(total_line=len(rows)),
        detected_encoding="utf-8-sig",
        detected_line_ending="\n",
        extra={"source_format": "unity-one-column-csv", "source_rows_sha256": source_hash},
    )
    project.add_file(cache_file)

    args.out_cache.parent.mkdir(parents=True, exist_ok=True)
    cache_io.save_cache(project, str(args.out_cache))
    print(f"converted {len(rows)} row(s) -> {args.out_cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
