#!/usr/bin/env python3
"""Write one-column translations to Utage scenario books by manifest row mapping."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

import UnityPy  # type: ignore


PLACEHOLDER_RE = re.compile(r"(<[^>]+>|\\[nrt]|\{[^{}]+\}|%[sdif]|\$[A-Za-z0-9_]+|\[[A-Za-z0-9_:-]+\])")
TAG_RE = re.compile(r"<[^>]*>")


def flat(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def unflat(text: str) -> str:
    return text.replace("\\n", "\n")


def read_one_column(path: Path, header: str) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row if row else [""] for row in csv.reader(handle)]
    if rows and [cell.lstrip("\ufeff") for cell in rows[0]] == [header]:
        rows = rows[1:]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one {header} column")
    return [row[0] for row in rows]


def read_translation(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row if row else [""] for row in csv.reader(handle)]
    if rows and [cell.lstrip("\ufeff") for cell in rows[0]] in (["zh_cn"], ["original_flat"]):
        rows = rows[1:]
    elif rows and [cell.lstrip("\ufeff") for cell in rows[0]] == ["original_flat", "zh_cn"]:
        rows = [[row[1]] for row in rows[1:]]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one zh_cn column")
    return [row[0] for row in rows]


def get_row(tree: dict, occurrence: dict) -> dict | None:
    grids = tree.get("importGridList", [])
    grid_index = int(occurrence["grid_index"])
    row_position = int(occurrence["row_position"])
    if grid_index >= len(grids):
        return None
    rows = grids[grid_index].get("rows", [])
    if row_position >= len(rows):
        return None
    return rows[row_position]


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
    if manifest.get("format") != "utage-text-v1":
        raise SystemExit("This writer requires an utage-text-v1 manifest from extract_utage_scenarios.py")
    source_rows = read_one_column(args.source_csv, "original_flat")
    translations = read_translation(args.translation_csv)
    if len(source_rows) != len(translations):
        raise SystemExit(f"row count mismatch: source={len(source_rows)} translation={len(translations)}")

    asset = Path(manifest["asset"])
    if hashlib.sha256(asset.read_bytes()).hexdigest() != manifest.get("source_sha256"):
        raise SystemExit("Utage asset SHA256 changed since extraction")

    selected: list[dict] = []
    for occurrence in manifest["occurrences"]:
        text_row = int(occurrence["text_row"])
        original = source_rows[text_row - 1]
        if original != occurrence["original_flat"]:
            raise SystemExit(f"manifest/source mismatch at text row {text_row}")
        translation = translations[text_row - 1]
        if not translation.strip() or translation == original:
            continue
        if sorted(PLACEHOLDER_RE.findall(original)) != sorted(PLACEHOLDER_RE.findall(translation)):
            raise SystemExit(f"placeholder mismatch at text row {text_row}")
        if sorted(TAG_RE.findall(original)) != sorted(TAG_RE.findall(translation)):
            raise SystemExit(f"tag mismatch at text row {text_row}")
        if not args.allow_newline_changes and original.count("\\n") != translation.count("\\n"):
            raise SystemExit(f"newline count mismatch at text row {text_row}")
        mapped = dict(occurrence)
        mapped["zh_cn"] = translation
        selected.append(mapped)

    env = UnityPy.load(str(asset))
    objects = {obj.path_id: obj for obj in env.objects if obj.type.name == "MonoBehaviour"}
    trees: dict[int, dict] = {}
    changed_objects: set[int] = set()
    patched: list[dict] = []
    skipped: list[dict] = []
    counts: Counter[str] = Counter()
    for occurrence in selected:
        path_id = int(occurrence["path_id"])
        obj = objects.get(path_id)
        if obj is None:
            skipped.append({"text_row": occurrence["text_row"], "reason": "object missing"})
            continue
        if path_id not in trees:
            try:
                trees[path_id] = obj.read_typetree()
            except Exception as exc:
                skipped.append({"text_row": occurrence["text_row"], "reason": f"typetree read failed: {exc}"})
                continue
        row = get_row(trees[path_id], occurrence)
        if row is None or row.get("rowIndex") != occurrence.get("row_index"):
            skipped.append({"text_row": occurrence["text_row"], "reason": "row location changed"})
            continue
        column = int(occurrence["text_column"])
        strings = row.get("strings", [])
        if len(strings) <= column or flat(str(strings[column])) != occurrence["original_flat"]:
            skipped.append({"text_row": occurrence["text_row"], "reason": "current text mismatch"})
            continue
        strings[column] = unflat(str(occurrence["zh_cn"]))
        changed_objects.add(path_id)
        patched.append({"text_row": occurrence["text_row"], "book": occurrence["book"], "row_index": occurrence["row_index"]})
        counts[str(occurrence["book"])] += 1

    if changed_objects and not args.dry_run:
        for path_id in changed_objects:
            objects[path_id].save_typetree(trees[path_id])
        backup_root = args.out_dir / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_root / Path(manifest["source_file"])
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, backup_path)
        asset_file = next(iter(env.files.values()))
        asset.write_bytes(asset_file.save())
    else:
        backup_path = None

    verification_failures: list[dict] = []
    if patched and not args.dry_run:
        saved_env = UnityPy.load(str(asset))
        saved_objects = {obj.path_id: obj for obj in saved_env.objects if obj.type.name == "MonoBehaviour"}
        for occurrence in selected:
            obj = saved_objects.get(int(occurrence["path_id"]))
            if obj is None:
                verification_failures.append({"text_row": occurrence["text_row"], "reason": "saved object missing"})
                continue
            row = get_row(obj.read_typetree(), occurrence)
            column = int(occurrence["text_column"])
            if row is None or len(row.get("strings", [])) <= column or row["strings"][column] != unflat(str(occurrence["zh_cn"])):
                verification_failures.append({"text_row": occurrence["text_row"], "reason": "saved translation not found"})

    result = {
        "dry_run": args.dry_run,
        "asset": str(asset),
        "translation_rows_used": len(selected),
        "patched_rows": len(patched),
        "touched_books": dict(counts),
        "skipped": skipped,
        "verification_failures": verification_failures,
        "backup_path": str(backup_path) if backup_path else None,
    }
    report = args.out_dir / ("utage_writeback_dryrun.json" if args.dry_run else "utage_writeback_report.json")
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if skipped or verification_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
