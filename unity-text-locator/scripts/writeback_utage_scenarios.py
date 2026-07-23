#!/usr/bin/env python3
"""Write one-column translations to Utage scenario books by manifest row mapping."""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

import UnityPy  # type: ignore


PLACEHOLDER_RE = re.compile(r"(<[^>]+>|\\\\|\\[Nnrt]|\{[^{}]+\}|%[sdif]|\$[A-Za-z0-9_]+|\[[A-Za-z0-9_:-]+\])")
TAG_RE = re.compile(r"<[^>]*>")


def flat_legacy(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def unflat(text: str) -> str:
    return text.replace("\\n", "\n")


def encode_exact(text: str) -> str:
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


def decode_exact(text: str) -> str:
    result: list[str] = []
    index = 0
    escapes = {"\\": "\\", "N": "\r\n", "n": "\n", "r": "\r", "t": "\t"}
    while index < len(text):
        if text[index] != "\\":
            result.append(text[index])
            index += 1
            continue
        if index + 1 >= len(text) or text[index + 1] not in escapes:
            raise ValueError(f"unsupported escape at character {index}")
        result.append(escapes[text[index + 1]])
        index += 2
    return "".join(result)


def newline_events(text: str) -> list[str]:
    events: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("\r\n", index):
            events.append("CRLF")
            index += 2
        elif text[index] == "\r":
            events.append("CR")
            index += 1
        elif text[index] == "\n":
            events.append("LF")
            index += 1
        else:
            index += 1
    return events


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
    parser.add_argument("--out-asset", type=Path, help="External candidate asset; required unless --dry-run")
    parser.add_argument("--allow-newline-changes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and args.out_asset is None:
        raise SystemExit("--out-asset is required for Utage writeback")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_format = manifest.get("format")
    if manifest_format not in {"utage-text-v1", "utage-text-v2"}:
        raise SystemExit("This writer requires an Utage manifest from extract_utage_scenarios.py")
    if manifest_format == "utage-text-v2" and not manifest.get("coverage_complete"):
        raise SystemExit("Utage extraction coverage is incomplete; audit skipped raw MonoBehaviour objects first")
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
        if manifest_format == "utage-text-v2":
            try:
                decoded = decode_exact(translation)
            except ValueError as exc:
                raise SystemExit(f"text row {text_row}: {exc}") from exc
            if not args.allow_newline_changes and newline_events(decoded) != occurrence.get("newline_events", []):
                raise SystemExit(f"ordered newline events changed at text row {text_row}")
            mapped["decoded_zh_cn"] = decoded
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
        current_text = str(strings[column]) if len(strings) > column else ""
        current_flat = encode_exact(current_text) if manifest_format == "utage-text-v2" else flat_legacy(current_text)
        if len(strings) <= column or current_flat != occurrence["original_flat"]:
            skipped.append({"text_row": occurrence["text_row"], "reason": "current text mismatch"})
            continue
        strings[column] = (
            str(occurrence["decoded_zh_cn"])
            if manifest_format == "utage-text-v2"
            else unflat(str(occurrence["zh_cn"]))
        )
        changed_objects.add(path_id)
        patched.append({"text_row": occurrence["text_row"], "book": occurrence["book"], "row_index": occurrence["row_index"]})
        counts[str(occurrence["book"])] += 1

    if changed_objects:
        for path_id in changed_objects:
            objects[path_id].save_typetree(trees[path_id])
        temporary: tempfile.TemporaryDirectory[str] | None = None
        if args.dry_run:
            temporary = tempfile.TemporaryDirectory(prefix="utage-writeback-", ignore_cleanup_errors=True)
            candidate = Path(temporary.name) / asset.name
        else:
            candidate = args.out_asset.resolve()
            if candidate == asset.resolve():
                raise SystemExit("in-place Utage writeback is forbidden")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        asset_file = next(iter(env.files.values()))
        candidate.write_bytes(asset_file.save())
    else:
        temporary = None
        candidate = None

    verification_failures: list[dict] = []
    if patched and candidate is not None:
        saved_env = UnityPy.load(str(candidate))
        saved_objects = {obj.path_id: obj for obj in saved_env.objects if obj.type.name == "MonoBehaviour"}
        for occurrence in selected:
            obj = saved_objects.get(int(occurrence["path_id"]))
            if obj is None:
                verification_failures.append({"text_row": occurrence["text_row"], "reason": "saved object missing"})
                continue
            row = get_row(obj.read_typetree(), occurrence)
            column = int(occurrence["text_column"])
            expected = (
                str(occurrence["decoded_zh_cn"])
                if manifest_format == "utage-text-v2"
                else unflat(str(occurrence["zh_cn"]))
            )
            if row is None or len(row.get("strings", [])) <= column or row["strings"][column] != expected:
                verification_failures.append({"text_row": occurrence["text_row"], "reason": "saved translation not found"})

    result = {
        "dry_run": args.dry_run,
        "asset": str(asset),
        "candidate_asset": str(candidate) if candidate is not None and not args.dry_run else None,
        "translation_rows_used": len(selected),
        "patched_rows": len(patched),
        "touched_books": dict(counts),
        "skipped": skipped,
        "verification_failures": verification_failures,
        "reopen_verified": not verification_failures,
    }
    report = args.out_dir / ("utage_writeback_dryrun.json" if args.dry_run else "utage_writeback_report.json")
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if temporary is not None:
        if "saved_objects" in locals():
            del saved_objects
        if "saved_env" in locals():
            del saved_env
        gc.collect()
        temporary.cleanup()
    return 1 if skipped or verification_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
