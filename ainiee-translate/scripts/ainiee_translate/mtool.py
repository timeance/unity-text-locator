"""Deterministic helpers for flat MTool JSON translation projects.

The MTool contract is ``{source_key: translated_value}``: source keys are
immutable, while only values may change.  This module does not translate text
and never writes cache.json; it prepares auditable files for the primary agent.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

CONTROL_TOKEN_RE = re.compile(
    r"\\[A-Za-z]+\[[^\]\r\n]*\]"  # RPG-style control code, e.g. \V[1]
    r"|\{[^{}\r\n]+\}"              # brace placeholder
    r"|%(?:\d+\$)?[-+#0-9.]*[A-Za-z%]"  # printf-style placeholder
    r"|\$[A-Za-z_][A-Za-z0-9_]*"     # dollar variable
    r"|</?[A-Za-z][^<>\r\n]*?>"      # markup tag; <なし> is visible text, not a tag
)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json_atomic(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)


def require_flat_mtool(data: Any, label: str) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a top-level JSON object")
    bad = [k for k, v in data.items() if not isinstance(k, str) or not isinstance(v, str)]
    if bad:
        raise ValueError(f"{label} must contain only string keys and string values")
    return data


def load_selection(path: str | Path) -> set[int]:
    data = read_json(path)
    if isinstance(data, dict):
        for key in ("candidate_indices", "indices", "text_indices"):
            if key in data:
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("selection must be a JSON array or contain candidate_indices/indices")
    indices: list[int] = []
    for item in data:
        value = item.get("text_index") if isinstance(item, dict) else item
        indices.append(int(value))
    if len(indices) != len(set(indices)):
        raise ValueError("selection contains duplicate text_index values")
    return set(indices)


def load_cache_items(path: str | Path) -> list[dict[str, Any]]:
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("cache must be an AiNiee CacheProject JSON object with a files mapping")
    items: list[dict[str, Any]] = []
    for cache_file in data["files"].values():
        if not isinstance(cache_file, dict) or not isinstance(cache_file.get("items"), list):
            raise ValueError("every cache file must contain an items array")
        for item in cache_file["items"]:
            if not isinstance(item, dict):
                raise ValueError("cache items must be JSON objects")
            items.append(item)
    return items


def marker_signature(text: str) -> dict[str, Any]:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    tokens = Counter(CONTROL_TOKEN_RE.findall(text))
    return {
        "actual_crlf": crlf,
        "actual_lf": lf,
        "actual_cr": cr,
        "literal_crlf": text.count(r"\r\n"),
        "literal_lf": text.count(r"\n") - text.count(r"\r\n"),
        "literal_cr": text.count(r"\r") - text.count(r"\r\n"),
        "dollar": text.count("$"),
        "percent": text.count("%"),
        "tokens": sorted(tokens.items()),
    }


def validate_records(source: Any, translation: Any) -> dict[str, Any]:
    errors: list[str] = []
    marker_issues: list[dict[str, Any]] = []
    unchanged = 0

    if not isinstance(source, list):
        return {"valid": False, "errors": ["source is not a JSON array"]}
    if not isinstance(translation, list):
        return {"valid": False, "errors": ["translation is not a JSON array"]}

    try:
        source_ids = [int(row["text_index"]) for row in source]
        translation_ids = [int(row["text_index"]) for row in translation]
    except (KeyError, TypeError, ValueError):
        return {"valid": False, "errors": ["every row must contain an integer text_index"]}

    if len(source_ids) != len(set(source_ids)):
        errors.append("source contains duplicate text_index values")
    if len(translation_ids) != len(set(translation_ids)):
        errors.append("translation contains duplicate text_index values")
    if source_ids != translation_ids:
        errors.append(
            f"text_index/order mismatch: source={len(source_ids)} translation={len(translation_ids)}"
        )

    source_by_id = {
        int(row["text_index"]): row
        for row in source
        if isinstance(row, dict) and "text_index" in row
    }
    empty_ids: list[int] = []
    changed_source_ids: list[int] = []
    invalid_value_ids: list[int] = []

    for row in translation:
        if not isinstance(row, dict) or "text_index" not in row:
            continue
        text_index = int(row["text_index"])
        translated = row.get("translated_text")
        if not isinstance(translated, str):
            invalid_value_ids.append(text_index)
            continue
        if not translated.strip():
            empty_ids.append(text_index)
        src = source_by_id.get(text_index)
        if src is None:
            continue
        source_text = src.get("source_text")
        if not isinstance(source_text, str):
            errors.append(f"source_text is not a string at text_index {text_index}")
            continue
        if "source_text" in row and row["source_text"] != source_text:
            changed_source_ids.append(text_index)
        if translated == source_text:
            unchanged += 1
        source_markers = marker_signature(source_text)
        translated_markers = marker_signature(translated)
        if source_markers != translated_markers:
            marker_issues.append(
                {
                    "text_index": text_index,
                    "source": source_markers,
                    "translation": translated_markers,
                }
            )

    if invalid_value_ids:
        errors.append(f"translated_text is missing or non-string at {len(invalid_value_ids)} row(s)")
    if empty_ids:
        errors.append(f"empty translated_text at {len(empty_ids)} row(s)")
    if changed_source_ids:
        errors.append(f"source_text was changed at {len(changed_source_ids)} row(s)")
    if marker_issues:
        errors.append(f"control marker mismatch at {len(marker_issues)} row(s)")

    return {
        "valid": not errors,
        "source_rows": len(source),
        "translation_rows": len(translation),
        "unchanged_rows": unchanged,
        "errors": errors,
        "empty_text_indices": empty_ids[:20],
        "changed_source_indices": changed_source_ids[:20],
        "invalid_value_indices": invalid_value_ids[:20],
        "marker_issue_count": len(marker_issues),
        "marker_issues": marker_issues[:20],
    }


def group_paths(groups_dir: Path, prefix: str) -> list[Path]:
    return sorted(groups_dir.glob(f"{prefix}_*_src.json"))


def translation_path(source_path: Path) -> Path:
    if not source_path.name.endswith("_src.json"):
        raise ValueError(f"unexpected source filename: {source_path.name}")
    return source_path.with_name(source_path.name[:-9] + "_trans.json")


def cmd_inspect(args: argparse.Namespace) -> None:
    path = Path(args.input)
    data = require_flat_mtool(read_json(path), str(path))
    report = {
        "input": str(path),
        "entries": len(data),
        "key_equals_value": sum(k == v for k, v in data.items()),
        "key_differs_from_value": sum(k != v for k, v in data.items()),
        "blank_keys": sum(not k for k in data),
        "blank_values": sum(not v for v in data.values()),
        "source_characters": sum(len(k) for k in data),
        "value_characters": sum(len(v) for v in data.values()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_passthrough(args: argparse.Namespace) -> None:
    selection = load_selection(args.selection)
    items = load_cache_items(args.cache)
    all_ids = {int(item["text_index"]) for item in items}
    unknown = sorted(selection - all_ids)
    if unknown:
        raise ValueError(f"selection contains unknown text_index values: {unknown[:20]}")

    rows = []
    filled_from_key = 0
    for item in items:
        text_index = int(item["text_index"])
        if text_index in selection or int(item.get("translation_status", 0)) != 0:
            continue
        translated = item.get("translated_text", "")
        if translated == "":
            translated = item.get("source_text", "")
            filled_from_key += 1
        rows.append({"text_index": text_index, "translated_text": translated})

    write_json_atomic(args.output, rows)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "candidate_rows": len(selection),
                "passthrough_rows": len(rows),
                "blank_values_filled_from_key": filled_from_key,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_split(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = list(output_dir.glob(f"{args.prefix}_*_src.json"))
    existing += list(output_dir.glob(f"{args.prefix}_*_trans.json"))
    existing += list(output_dir.glob(f"{args.prefix}_*.json.tmp"))
    if existing:
        raise ValueError("output directory already contains MTool group artifacts; use a new directory")

    pending = [
        {"text_index": int(item["text_index"]), "source_text": item.get("source_text", "")}
        for item in load_cache_items(args.cache)
        if int(item.get("translation_status", 0)) == 0 and item.get("source_text", "").strip()
    ]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for row in pending:
        row_chars = len(row["source_text"])
        would_overflow_chars = args.max_chars > 0 and current and chars + row_chars > args.max_chars
        if current and (len(current) >= args.size or would_overflow_chars):
            groups.append(current)
            current = []
            chars = 0
        current.append(row)
        chars += row_chars
    if current:
        groups.append(current)

    manifest_groups = []
    for number, rows in enumerate(groups, 1):
        name = f"{args.prefix}_{number:03d}_src.json"
        write_json_atomic(output_dir / name, rows)
        manifest_groups.append(
            {
                "group": number,
                "source_file": name,
                "rows": len(rows),
                "characters": sum(len(row["source_text"]) for row in rows),
                "first_text_index": rows[0]["text_index"],
                "last_text_index": rows[-1]["text_index"],
            }
        )
    manifest = {
        "cache": str(args.cache),
        "prefix": args.prefix,
        "group_size_limit": args.size,
        "group_character_limit": args.max_chars,
        "candidate_rows": len(pending),
        "group_count": len(groups),
        "groups": manifest_groups,
    }
    write_json_atomic(output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def cmd_validate(args: argparse.Namespace) -> None:
    report = validate_records(read_json(args.source), read_json(args.translation))
    report["source"] = str(args.source)
    report["translation"] = str(args.translation)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("valid"):
        raise SystemExit(1)


def cmd_progress(args: argparse.Namespace) -> None:
    groups_dir = Path(args.groups_dir)
    sources = group_paths(groups_dir, args.prefix)
    total_rows = 0
    valid_rows = 0
    valid_groups = 0
    missing: list[str] = []
    invalid: list[dict[str, Any]] = []
    for source_path in sources:
        source = read_json(source_path)
        total_rows += len(source) if isinstance(source, list) else 0
        trans_path = translation_path(source_path)
        if not trans_path.exists():
            missing.append(trans_path.name)
            continue
        try:
            report = validate_records(source, read_json(trans_path))
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            invalid.append({"file": trans_path.name, "error": str(exc)})
            continue
        if report.get("valid"):
            valid_groups += 1
            valid_rows += report["translation_rows"]
        else:
            invalid.append({"file": trans_path.name, "errors": report.get("errors", [])})
    temp_files = sorted(path.name for path in groups_dir.glob(f"{args.prefix}_*.tmp"))
    report = {
        "groups_total": len(sources),
        "groups_valid": valid_groups,
        "rows_total": total_rows,
        "rows_valid": valid_rows,
        "progress_percent": round(valid_rows * 100 / total_rows, 3) if total_rows else 100.0,
        "missing_group_count": len(missing),
        "missing_groups": missing[:20],
        "invalid_group_count": len(invalid),
        "invalid_groups": invalid,
        "temporary_file_count": len(temp_files),
        "temporary_files": temp_files,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_merge(args: argparse.Namespace) -> None:
    groups_dir = Path(args.groups_dir)
    sources = group_paths(groups_dir, args.prefix)
    if not sources:
        raise ValueError("no source groups found")
    merged: list[dict[str, Any]] = []
    seen: set[int] = set()
    for source_path in sources:
        trans_path = translation_path(source_path)
        if not trans_path.exists():
            raise ValueError(f"missing translation: {trans_path.name}")
        translation = read_json(trans_path)
        report = validate_records(read_json(source_path), translation)
        if not report.get("valid"):
            raise ValueError(f"invalid translation {trans_path.name}: {report.get('errors', [])}")
        for row in translation:
            text_index = int(row["text_index"])
            if text_index in seen:
                raise ValueError(f"duplicate text_index across groups: {text_index}")
            seen.add(text_index)
            merged.append({"text_index": text_index, "translated_text": row["translated_text"]})
    write_json_atomic(args.output, merged)
    print(
        json.dumps(
            {"groups": len(sources), "rows": len(merged), "output": str(args.output)},
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_verify_output(args: argparse.Namespace) -> None:
    source = require_flat_mtool(read_json(args.input), str(args.input))
    output = require_flat_mtool(read_json(args.output), str(args.output))
    source_keys = list(source)
    output_keys = list(output)
    missing = [key for key in source_keys if key not in output]
    extra = [key for key in output_keys if key not in source]
    blank = [key for key, value in output.items() if not value]
    new_blank = [key for key in blank if source.get(key, "") != ""]
    marker_issues = []
    for key in source_keys:
        if key not in output:
            continue
        expected = marker_signature(key)
        actual = marker_signature(output[key])
        if expected != actual:
            marker_issues.append({"key": key, "source": expected, "translation": actual})
    valid = (
        not missing
        and not extra
        and source_keys == output_keys
        and (args.allow_empty_values or not new_blank)
        and not marker_issues
    )
    report = {
        "valid": valid,
        "input_entries": len(source),
        "output_entries": len(output),
        "same_key_order": source_keys == output_keys,
        "missing_key_count": len(missing),
        "extra_key_count": len(extra),
        "blank_value_count": len(blank),
        "new_blank_value_count": len(new_blank),
        "marker_issue_count": len(marker_issues),
        "missing_keys": missing[:20],
        "extra_keys": extra[:20],
        "blank_value_keys": blank[:20],
        "new_blank_value_keys": new_blank[:20],
        "marker_issues": marker_issues[:20],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not valid:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MTool translation workflow helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = sub.add_parser("inspect", help="Inspect a flat MTool JSON file")
    inspect_cmd.add_argument("input")
    inspect_cmd.set_defaults(func=cmd_inspect)

    passthrough_cmd = sub.add_parser(
        "passthrough", help="Build batch-write rows for every non-candidate untranslated item"
    )
    passthrough_cmd.add_argument("--cache", required=True)
    passthrough_cmd.add_argument("--selection", required=True)
    passthrough_cmd.add_argument("--output", required=True)
    passthrough_cmd.set_defaults(func=cmd_passthrough)

    split_cmd = sub.add_parser("split", help="Split remaining untranslated cache items")
    split_cmd.add_argument("--cache", required=True)
    split_cmd.add_argument("--output-dir", required=True)
    split_cmd.add_argument("--size", type=int, default=150)
    split_cmd.add_argument("--max-chars", type=int, default=5000)
    split_cmd.add_argument("--prefix", default="mtool")
    split_cmd.set_defaults(func=cmd_split)

    validate_cmd = sub.add_parser("validate", help="Validate one source/translation group")
    validate_cmd.add_argument("--source", required=True)
    validate_cmd.add_argument("--translation", required=True)
    validate_cmd.set_defaults(func=cmd_validate)

    progress_cmd = sub.add_parser("progress", help="Report progress from valid final group files")
    progress_cmd.add_argument("--groups-dir", required=True)
    progress_cmd.add_argument("--prefix", default="mtool")
    progress_cmd.set_defaults(func=cmd_progress)

    merge_cmd = sub.add_parser("merge", help="Validate and merge all completed groups")
    merge_cmd.add_argument("--groups-dir", required=True)
    merge_cmd.add_argument("--output", required=True)
    merge_cmd.add_argument("--prefix", default="mtool")
    merge_cmd.set_defaults(func=cmd_merge)

    verify_cmd = sub.add_parser("verify-output", help="Verify final MTool key/value integrity")
    verify_cmd.add_argument("--input", required=True)
    verify_cmd.add_argument("--output", required=True)
    verify_cmd.add_argument("--allow-empty-values", action="store_true")
    verify_cmd.set_defaults(func=cmd_verify_output)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "size", 1) <= 0:
        parser.error("--size must be greater than zero")
    try:
        args.func(args)
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
