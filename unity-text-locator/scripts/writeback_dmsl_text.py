#!/usr/bin/env python3
"""Validate and stage fixed-width DMSL translations into Unity candidates.

Dry-run is the default. The command performs a whole-batch preflight and writes
nothing when any row is too long, stale, duplicated, structurally invalid, or
no longer located at the manifest field path. Source game files are never
modified. ``--base-root`` composes onto an earlier external candidate tree.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import struct
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import UnityPy

from extract_dmsl_text import (
    FORMAT,
    normalize_byte_array,
    object_name,
    serialized_file_name,
    sha256_bytes,
    sha256_file,
)


SOURCE_HEADERS = ("original_flat", "source", "original")
TRANSLATION_HEADERS = (
    "zh_cn", "translated_flat", "translation", "translated_text", "translated", "text", "original_flat"
)
TOKEN_RE = re.compile(
    r"<[^<>]+>|\{(?:\d+|[A-Za-z_][\w.]*)\}|"
    r"%(?:\d+\$)?[-+#0 ]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]"
)


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def safe_join(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or not relative or any(part == ".." for part in rel.parts):
        raise ValueError(f"unsafe manifest source_path: {relative!r}")
    path = (root / rel).resolve()
    if not is_within(path, root):
        raise ValueError(f"manifest source_path escapes root: {relative!r}")
    return path


def unflatten_text(value: str) -> str:
    """Decode the extractor's backslash-controls-v1 representation."""
    output: list[str] = []
    index = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value) and value[index + 1] in escapes:
            output.append(escapes[value[index + 1]])
            index += 2
        else:
            output.append(char)
            index += 1
    return "".join(output)


def read_one_column(
    path: Path,
    known_headers: Sequence[str],
    *,
    require_header: bool,
) -> tuple[str | None, list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        table = list(csv.reader(stream))
    if not table:
        raise ValueError(f"CSV is empty: {path}")
    # A physical blank row is the ordinary hand-edited form of one empty cell.
    table = [[""] if not row else row for row in table]
    widths = sorted({len(row) for row in table})
    if widths != [1]:
        raise ValueError(f"CSV must contain exactly one column; observed row widths {widths}")
    first = table[0][0]
    if first in known_headers:
        return first, [row[0] for row in table[1:]]
    if require_header:
        raise ValueError(f"CSV header must be one of {tuple(known_headers)!r}, got {first!r}")
    return None, [row[0] for row in table]


def get_tree_path(tree: Any, field_path: Sequence[Any]) -> list[Any]:
    value = tree
    for segment in field_path:
        if isinstance(segment, str) and isinstance(value, dict) and segment in value:
            value = value[segment]
        elif isinstance(segment, int) and isinstance(value, list) and 0 <= segment < len(value):
            value = value[segment]
        else:
            raise ValueError(f"bytecode field path drifted at segment {segment!r}")
    if not isinstance(value, list):
        raise ValueError("bytecode field path no longer resolves to a list")
    return value


def load_unique_object(env: Any, serialized_file: str, path_id: int, object_type: str) -> Any:
    matches = [
        obj
        for obj in env.objects
        if serialized_file_name(obj) == serialized_file
        and int(obj.path_id) == path_id
        and obj.type.name == object_type
    ]
    if len(matches) != 1:
        raise ValueError(
            f"serialized_file={serialized_file!r}, path_id={path_id}, object_type={object_type!r} "
            f"matched {len(matches)} objects"
        )
    return matches[0]


def verify_object_name(tree: Any, path_id: int, expected_name: str) -> None:
    if not isinstance(tree, dict):
        raise ValueError("object TypeTree is not an object")
    actual_name = object_name(tree, path_id)
    if actual_name != expected_name:
        raise ValueError(f"object name drift: expected={expected_name!r}, actual={actual_name!r}")


def inspect_operand(data: bytes, row: dict[str, Any]) -> tuple[bytes, str]:
    offset = int(row["bytecode_offset"])
    width = int(row["byte_length"])
    if offset < 0 or width <= 0 or offset + 5 + width > len(data):
        raise ValueError("operand is outside the bytecode field")
    if data[offset] != 0x06:
        raise ValueError(f"opcode drift: expected 0x06, got 0x{data[offset]:02x}")
    actual_width = struct.unpack_from("<I", data, offset + 1)[0]
    if actual_width != width:
        raise ValueError(f"width drift: field={actual_width}, manifest={width}")
    payload = data[offset + 5:offset + 5 + width]
    return payload, payload.decode("utf-8", errors="strict")


def structural_issues(original: str, translated: str) -> list[str]:
    issues: list[str] = []
    original_tokens = TOKEN_RE.findall(original)
    translated_tokens = TOKEN_RE.findall(translated)
    if original_tokens != translated_tokens:
        issues.append(f"tag/placeholder mismatch: {original_tokens!r} != {translated_tokens!r}")
    if original.count("\n") != translated.count("\n"):
        issues.append(
            f"newline count mismatch: {original.count(chr(10))} != {translated.count(chr(10))}"
        )
    return issues


def verify_environment(
    env: Any,
    expected: dict[tuple[str, int, str, str, int], tuple[list[Any], bytes, str]],
) -> None:
    for (serialized_file, path_id, object_type, path_key, offset), (
        field_path, wanted, expected_name,
    ) in expected.items():
        obj = load_unique_object(env, serialized_file, path_id, object_type)
        tree = obj.read_typetree()
        verify_object_name(tree, path_id, expected_name)
        raw = get_tree_path(tree, field_path)
        data = normalize_byte_array(raw)
        if data[offset] != 0x06:
            raise ValueError(f"reopen verification failed opcode at path_id {path_id}, field {path_key}")
        width = struct.unpack_from("<I", data, offset + 1)[0]
        actual = data[offset + 5:offset + 5 + width]
        if width != len(wanted) or actual != wanted:
            raise ValueError(f"reopen verification failed payload at path_id {path_id}, field {path_key}")


def add_issue(report: dict[str, Any], **issue: Any) -> None:
    report["issues"].append(issue)
    report["counts"]["blocking"] += 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_root", type=Path, help="original game root used by manifest source_path values")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-csv", type=Path, help="default: source_csv_name beside the manifest")
    parser.add_argument("--translation-csv", type=Path, required=True)
    parser.add_argument(
        "--base-root", type=Path,
        help="external candidate tree to compose onto; missing files fall back to original game files",
    )
    parser.add_argument("--out-dir", type=Path, help="external candidate root; required with --write")
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--write", action="store_true", help="write candidates after a clean whole-batch preflight")
    parser.add_argument("--overwrite", action="store_true", help="replace existing external candidate files")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    game_root = args.game_root.resolve()
    if not game_root.is_dir():
        raise SystemExit(f"game_root is not a directory: {game_root}")
    if args.write and args.out_dir is None:
        raise SystemExit("--write requires --out-dir")

    out_root = args.out_dir.resolve() if args.out_dir else None
    base_root = args.base_root.resolve() if args.base_root else None
    if out_root and is_within(out_root, game_root):
        raise SystemExit("--out-dir must be outside game_root")
    if base_root:
        if not base_root.is_dir():
            raise SystemExit(f"--base-root is not a directory: {base_root}")
        if is_within(base_root, game_root):
            raise SystemExit("--base-root must be outside game_root")
        if out_root == base_root:
            raise SystemExit("--out-dir must differ from --base-root")

    manifest_path = args.manifest.resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"cannot read manifest: {type(exc).__name__}: {exc}") from exc
    if manifest.get("format") != FORMAT:
        raise SystemExit(f"unsupported manifest format: {manifest.get('format')!r}")
    if manifest.get("text_escape") != "backslash-controls-v1" or not manifest.get("fixed_width"):
        raise SystemExit("manifest does not declare the supported fixed-width text encoding")

    source_csv = (
        args.source_csv.resolve()
        if args.source_csv
        else manifest_path.parent / str(manifest.get("source_csv_name", ""))
    )
    report: dict[str, Any] = {
        "mode": "write-candidates" if args.write else "dry-run",
        "whole_batch_policy": "any preflight issue rejects every candidate write",
        "fixed_width_policy": "UTF-8 bytes must fit; shorter payloads are right-padded with ASCII spaces",
        "source_game_files_modified": False,
        "write_performed": False,
        "counts": {
            "rows": 0, "planned": 0, "preserved_blank": 0, "unchanged": 0,
            "blocking": 0, "candidate_files": 0,
        },
        "bundles": [],
        "issues": [],
        "candidate_transaction": {
            "status": "not_started",
            "staged_files": 0,
            "published_files": 0,
            "rolled_back": False,
        },
    }

    try:
        expected_csv_hash = manifest.get("source_csv_sha256")
        actual_csv_hash = sha256_file(source_csv)
        if not expected_csv_hash or actual_csv_hash.lower() != str(expected_csv_hash).lower():
            raise ValueError(
                f"source CSV SHA-256 mismatch: expected={expected_csv_hash}, actual={actual_csv_hash}"
            )
        _, original_flat = read_one_column(source_csv, SOURCE_HEADERS, require_header=True)
        translation_header, translated_flat = read_one_column(
            args.translation_csv.resolve(), TRANSLATION_HEADERS, require_header=False
        )
    except Exception as exc:
        add_issue(report, scope="input", reason=str(exc))
        original_flat = []
        translated_flat = []
        translation_header = None

    rows = manifest.get("rows")
    if not isinstance(rows, list):
        add_issue(report, scope="manifest", reason="rows must be a list")
        rows = []
    report["counts"]["rows"] = len(rows)
    report["translation_column"] = translation_header or "headerless"
    if len(rows) != len(original_flat) or len(rows) != len(translated_flat):
        add_issue(
            report,
            scope="alignment",
            reason=(
                f"row count mismatch: manifest={len(rows)}, source_csv={len(original_flat)}, "
                f"translation_csv={len(translated_flat)}"
            ),
        )
    if [row.get("row_index") for row in rows if isinstance(row, dict)] != list(range(len(rows))):
        add_issue(report, scope="manifest", reason="row_index values must be contiguous and ordered")

    locations: set[tuple[Any, ...]] = set()
    occurrence_ids: set[str] = set()
    intervals: defaultdict[tuple[str, str, int, str, str], list[tuple[int, int, int]]] = defaultdict(list)
    grouped: defaultdict[str, list[tuple[dict[str, Any], str, str]]] = defaultdict(list)
    if len(rows) == len(original_flat) == len(translated_flat):
        for row, original_value, translated_value in zip(rows, original_flat, translated_flat):
            if not isinstance(row, dict):
                add_issue(report, scope="manifest", reason="each row must be an object")
                continue
            try:
                row_index = int(row["row_index"])
                source_path = str(row["source_path"])
                serialized_file = str(row["serialized_file"])
                path_id = int(row["path_id"])
                object_type = str(row["object_type"])
                expected_name = str(row["object_name"])
                field_path = row["bytecode_field_path"]
                offset = int(row["bytecode_offset"])
                width = int(row["byte_length"])
                occurrence_id = str(row["occurrence_id"])
                if not serialized_file:
                    raise ValueError("serialized_file must be a non-empty string")
                if not object_type:
                    raise ValueError("object_type must be a non-empty string")
                if not expected_name:
                    raise ValueError("object_name must be a non-empty string")
                if not isinstance(field_path, list) or not field_path:
                    raise ValueError("bytecode_field_path must be a non-empty list")
                if any(not isinstance(part, (str, int)) or isinstance(part, bool) for part in field_path):
                    raise ValueError("bytecode_field_path contains an unsupported segment")
                if offset < 0 or width <= 0:
                    raise ValueError("bytecode_offset and byte_length must be positive coordinates")
                path_key = json.dumps(field_path, ensure_ascii=True, separators=(",", ":"))
                location = (source_path, serialized_file, path_id, object_type, path_key, offset)
                if location in locations:
                    raise ValueError("duplicate manifest location")
                locations.add(location)
                if not occurrence_id or occurrence_id in occurrence_ids:
                    raise ValueError("missing or duplicate occurrence_id")
                occurrence_ids.add(occurrence_id)
                interval_key = (source_path, serialized_file, path_id, object_type, path_key)
                for existing_start, existing_end, existing_row in intervals[interval_key]:
                    if max(offset, existing_start) < min(offset + 5 + width, existing_end):
                        raise ValueError(f"operand overlaps row {existing_row}")
                intervals[interval_key].append((offset, offset + 5 + width, row_index))

                original = unflatten_text(original_value)
                original_bytes = original.encode("utf-8", errors="strict")
                if len(original_bytes) != width:
                    raise ValueError(
                        f"source CSV byte width {len(original_bytes)} != manifest byte_length {width}"
                    )
                expected_original_hash = str(row.get("original_utf8_sha256", ""))
                if sha256_bytes(original_bytes) != expected_original_hash:
                    raise ValueError("source CSV text hash differs from manifest")
                grouped[source_path].append((row, original, translated_value))
            except Exception as exc:
                add_issue(report, row_index=row.get("row_index"), scope="manifest-row", reason=str(exc))

    plans: list[dict[str, Any]] = []
    source_hashes = manifest.get("source_hashes_sha256", {})
    if not isinstance(source_hashes, dict):
        add_issue(report, scope="manifest", reason="source_hashes_sha256 must be an object")
        source_hashes = {}

    for source_path, work in sorted(grouped.items()):
        bundle_report: dict[str, Any] = {
            "source_path": source_path,
            "base": None,
            "base_sha256": None,
            "planned": 0,
            "candidate": None,
            "serialized_reopen": False,
            "disk_reopen": False,
        }
        report["bundles"].append(bundle_report)
        try:
            source = safe_join(game_root, source_path)
            if not source.is_file():
                raise FileNotFoundError(f"original source missing: {source}")
            expected_source_hash = source_hashes.get(source_path)
            actual_source_hash = sha256_file(source)
            if not expected_source_hash or actual_source_hash.lower() != str(expected_source_hash).lower():
                raise ValueError(
                    f"source SHA-256 mismatch: expected={expected_source_hash}, actual={actual_source_hash}"
                )

            base = source
            if base_root:
                candidate_base = safe_join(base_root, source_path)
                if candidate_base.exists():
                    if not candidate_base.is_file():
                        raise ValueError(f"base candidate is not a file: {candidate_base}")
                    base = candidate_base
            bundle_report["base"] = "candidate" if base != source else "original"
            bundle_report["base_sha256"] = sha256_file(base)
            env = UnityPy.load(str(base))
            expected_payloads: dict[
                tuple[str, int, str, str, int], tuple[list[Any], bytes, str]
            ] = {}
            object_work: defaultdict[
                tuple[str, int, str], list[tuple[dict[str, Any], str, str]]
            ] = defaultdict(list)
            for item in work:
                row = item[0]
                object_key = (
                    str(row["serialized_file"]), int(row["path_id"]), str(row["object_type"])
                )
                object_work[object_key].append(item)

            for (serialized_file, path_id, object_type), object_rows in object_work.items():
                try:
                    obj = load_unique_object(env, serialized_file, path_id, object_type)
                    tree = obj.read_typetree()
                    expected_names = {str(item[0]["object_name"]) for item in object_rows}
                    if len(expected_names) != 1:
                        raise ValueError(f"manifest object_name values disagree: {sorted(expected_names)!r}")
                    expected_name = next(iter(expected_names))
                    verify_object_name(tree, path_id, expected_name)
                    fields: dict[str, tuple[list[Any], bytearray]] = {}
                    changed = False
                    for row, original, translated_value in object_rows:
                        row_index = int(row["row_index"])
                        field_path = row["bytecode_field_path"]
                        path_key = json.dumps(field_path, ensure_ascii=True, separators=(",", ":"))
                        try:
                            if path_key not in fields:
                                raw = get_tree_path(tree, field_path)
                                fields[path_key] = (raw, bytearray(normalize_byte_array(raw)))
                            raw, data = fields[path_key]
                            payload, actual_text = inspect_operand(bytes(data), row)
                            if actual_text != original:
                                raise ValueError("field text differs from source CSV (field drift or stale base)")
                            if sha256_bytes(payload) != str(row["original_utf8_sha256"]):
                                raise ValueError("field payload hash differs from manifest")

                            if translated_value == "":
                                report["counts"]["preserved_blank"] += 1
                                continue
                            translated = unflatten_text(translated_value)
                            structural = structural_issues(original, translated)
                            if structural:
                                raise ValueError("; ".join(structural))
                            encoded = translated.encode("utf-8", errors="strict")
                            width = int(row["byte_length"])
                            if len(encoded) > width:
                                add_issue(
                                    report,
                                    row_index=row_index,
                                    occurrence_id=row["occurrence_id"],
                                    scope="byte-budget",
                                    reason="translation_too_long",
                                    bytes=len(encoded),
                                    limit=width,
                                    over_by=len(encoded) - width,
                                    translated_flat=translated_value,
                                )
                                continue
                            padded = encoded + b" " * (width - len(encoded))
                            if padded == payload:
                                report["counts"]["unchanged"] += 1
                                continue
                            start = int(row["bytecode_offset"]) + 5
                            data[start:start + width] = padded
                            expected_payloads[
                                (
                                    serialized_file, path_id, object_type, path_key,
                                    int(row["bytecode_offset"]),
                                )
                            ] = (
                                field_path, padded, expected_name
                            )
                            report["counts"]["planned"] += 1
                            bundle_report["planned"] += 1
                            changed = True
                        except Exception as exc:
                            add_issue(
                                report,
                                row_index=row_index,
                                occurrence_id=row.get("occurrence_id"),
                                source_path=source_path,
                                scope="row-preflight",
                                reason=str(exc),
                            )
                    if changed:
                        for raw, data in fields.values():
                            raw[:] = list(data)
                        obj.save_typetree(tree)
                except Exception as exc:
                    add_issue(
                        report, source_path=source_path, serialized_file=serialized_file,
                        path_id=path_id, object_type=object_type,
                        scope="object-preflight", reason=str(exc),
                    )

            candidate = safe_join(out_root, source_path) if out_root else None
            if candidate:
                if candidate.resolve() == source.resolve() or candidate.resolve() == base.resolve():
                    raise ValueError("candidate path resolves to an input file")
                if candidate.exists() and not candidate.is_file():
                    raise ValueError(f"candidate path exists but is not a file: {candidate}")
                if candidate.exists() and not args.overwrite:
                    raise FileExistsError(f"candidate already exists; pass --overwrite: {candidate}")
                bundle_report["candidate"] = str(candidate)
            plans.append(
                {
                    "source_path": source_path,
                    "env": env,
                    "expected": expected_payloads,
                    "candidate": candidate,
                    "report": bundle_report,
                }
            )
        except Exception as exc:
            add_issue(report, source_path=source_path, scope="bundle-preflight", reason=str(exc))

    serialized_plans: list[dict[str, Any]] = []
    if report["counts"]["blocking"] == 0:
        for plan in plans:
            if not plan["expected"]:
                continue
            try:
                serialized = plan["env"].file.save()
                reopened = UnityPy.load(serialized)
                verify_environment(reopened, plan["expected"])
                plan["report"]["serialized_reopen"] = True
                plan["serialized"] = serialized
                serialized_plans.append(plan)
            except Exception as exc:
                add_issue(
                    report, source_path=plan["source_path"],
                    scope="serialized-reopen", reason=f"{type(exc).__name__}: {exc}",
                )

    if args.write and report["counts"]["blocking"] == 0:
        transaction = report["candidate_transaction"]
        staged_plans: list[dict[str, Any]] = []
        transaction["status"] = "staging"

        # Stage and reopen every candidate before publishing any of them.
        try:
            for plan in serialized_plans:
                candidate: Path = plan["candidate"]
                candidate.parent.mkdir(parents=True, exist_ok=True)
                temporary = candidate.with_name(f".{candidate.name}.tmp-dmsl-{uuid.uuid4().hex}")
                plan["temporary"] = temporary
                plan["rollback"] = None
                plan["published"] = False
                temporary.write_bytes(plan["serialized"])
                reopened = UnityPy.load(temporary.read_bytes())
                verify_environment(reopened, plan["expected"])
                staged_plans.append(plan)
                transaction["staged_files"] += 1
        except Exception as exc:
            add_issue(
                report,
                source_path=plan.get("source_path") if "plan" in locals() else None,
                scope="candidate-stage",
                reason=f"{type(exc).__name__}: {exc}",
            )
            transaction["status"] = "stage_failed"

        if report["counts"]["blocking"] == 0:
            transaction["status"] = "publishing"
            try:
                for plan in staged_plans:
                    candidate = plan["candidate"]
                    if candidate.exists():
                        if not candidate.is_file():
                            raise ValueError(f"candidate path became a non-file: {candidate}")
                        if not args.overwrite:
                            raise FileExistsError(f"candidate appeared during write: {candidate}")
                        rollback = candidate.with_name(
                            f".{candidate.name}.rollback-dmsl-{uuid.uuid4().hex}"
                        )
                        os.replace(candidate, rollback)
                        plan["rollback"] = rollback
                    os.replace(plan["temporary"], candidate)
                    plan["published"] = True
                    transaction["published_files"] += 1

                # Read bytes before reopening so UnityPy cannot retain a Windows file handle.
                for plan in staged_plans:
                    final_env = UnityPy.load(plan["candidate"].read_bytes())
                    verify_environment(final_env, plan["expected"])

                for plan in staged_plans:
                    plan["report"]["disk_reopen"] = True
                report["counts"]["candidate_files"] = len(staged_plans)
                report["write_performed"] = bool(staged_plans)
                transaction["status"] = "committed"
            except Exception as exc:
                add_issue(
                    report,
                    source_path=plan.get("source_path") if "plan" in locals() else None,
                    scope="candidate-publish",
                    reason=f"{type(exc).__name__}: {exc}",
                )
                rollback_errors: list[dict[str, str]] = []
                for rollback_plan in reversed(staged_plans):
                    candidate = rollback_plan["candidate"]
                    rollback = rollback_plan.get("rollback")
                    try:
                        if rollback is not None and rollback.exists():
                            os.replace(rollback, candidate)
                        elif rollback_plan.get("published") and candidate.exists():
                            candidate.unlink()
                    except Exception as rollback_exc:
                        rollback_errors.append(
                            {
                                "source_path": rollback_plan["source_path"],
                                "reason": f"{type(rollback_exc).__name__}: {rollback_exc}",
                            }
                        )
                    rollback_plan["report"]["disk_reopen"] = False
                report["counts"]["candidate_files"] = 0
                report["write_performed"] = False
                transaction["rolled_back"] = not rollback_errors
                transaction["status"] = "rolled_back" if not rollback_errors else "rollback_failed"
                for rollback_error in rollback_errors:
                    add_issue(report, scope="candidate-rollback", **rollback_error)

        # A stage failure or a successful/failed publication can all leave unused temp files.
        cleanup_errors: list[dict[str, str]] = []
        for cleanup_plan in serialized_plans:
            temporary = cleanup_plan.get("temporary")
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        {
                            "source_path": cleanup_plan["source_path"],
                            "reason": f"temp cleanup failed: {type(cleanup_exc).__name__}: {cleanup_exc}",
                        }
                    )
            rollback = cleanup_plan.get("rollback")
            if rollback is not None and rollback.exists() and transaction["status"] != "rollback_failed":
                try:
                    rollback.unlink()
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        {
                            "source_path": cleanup_plan["source_path"],
                            "reason": f"rollback cleanup failed: {type(cleanup_exc).__name__}: {cleanup_exc}",
                        }
                    )
        for cleanup_error in cleanup_errors:
            add_issue(report, scope="candidate-cleanup", **cleanup_error)

    if report["counts"]["blocking"]:
        report["result"] = "rejected"
        report["write_performed"] = False if report["counts"]["candidate_files"] == 0 else report["write_performed"]
    elif args.write:
        report["result"] = "candidates_written"
    else:
        report["result"] = "dry_run_clean"

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if report["counts"]["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
