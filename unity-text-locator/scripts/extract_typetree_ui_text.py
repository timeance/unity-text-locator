#!/usr/bin/env python3
"""Extract Japanese UI text from Unity TypeTrees without modifying the game.

The extractor records every occurrence separately.  A row is addressed by the
bundle path, serialized asset file, object PathID, and a typed field path; this
avoids conflating equal strings or equal PathIDs in different serialized files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    import UnityPy
except ImportError as exc:  # pragma: no cover - depends on the host environment
    raise SystemExit("UnityPy is required: install it in the active Python environment") from exc


FORMAT = "unity-typetree-ui-text-v2"
JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
KEYISH_RE = re.compile(r"^[A-Za-z0-9_./\\:@+\-]+$")
EXT_RE = re.compile(
    r"\.(?:png|jpe?g|webp|wav|ogg|mp3|mp4|asset|prefab|anim|controller|mat|shader)$",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(
    r"<[^<>]+>|\\[nrt]|\{[^{}\r\n]+\}|"
    r"%(?:\d+\$)?[-+#0 ]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]"
)
TEXT_LEAVES = {"text", "m_text"}
LABEL_HINTS = ("label", "caption", "title", "description", "option", "tooltip")
VALUE_LEAVES = {"m_value", "string", "value"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def flatten_text(value: str) -> str:
    return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def newline_events(value: str) -> list[str]:
    events: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("\r\n", index):
            events.append("CRLF")
            index += 2
        elif value[index] == "\n":
            events.append("LF")
            index += 1
        elif value[index] == "\r":
            events.append("CR")
            index += 1
        else:
            index += 1
    return events


def token_info(value: str) -> dict[str, Any]:
    ordered = TOKEN_RE.findall(value)
    return {"ordered": ordered, "count": len(ordered)}


def visible_japanese(value: str) -> bool:
    stripped = value.strip()
    if not stripped or "\x00" in stripped or not JP_RE.search(stripped):
        return False
    if any(ord(char) < 0x20 and char not in "\r\n\t" for char in stripped):
        return False
    if EXT_RE.search(stripped) or KEYISH_RE.fullmatch(stripped):
        return False
    # A lone CJK codepoint is commonly an enum/key artifact.  Kana or at least
    # two Japanese/CJK codepoints is a conservative default for visible UI.
    return bool(KANA_RE.search(stripped) or len(JP_RE.findall(stripped)) >= 2)


def safe_nonempty_text(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and "\x00" not in stripped and not any(
        ord(char) < 0x20 and char not in "\r\n\t" for char in stripped
    )


def iter_strings(value: Any, path: tuple[str | int, ...] = ()) -> Iterable[tuple[tuple[str | int, ...], str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_strings(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_strings(child, path + (index,))
    elif isinstance(value, str):
        yield path, value


def normalized_name(value: str | int) -> str:
    if isinstance(value, int):
        return ""
    return value.lower().replace("_", "")


def classify(path: tuple[str | int, ...]) -> str | None:
    names = [part for part in path if isinstance(part, str)]
    if not names:
        return None
    normalized = [normalized_name(part) for part in names]
    leaf = normalized[-1]
    if "mtabledata" in normalized and leaf == "mlocalized":
        return "localization_string_table"
    if leaf in {normalized_name(item) for item in TEXT_LEAVES}:
        return "component_text"
    if any(hint in leaf for hint in LABEL_HINTS):
        return "ui_label_field"
    if leaf in {normalized_name(item) for item in VALUE_LEAVES} and any(
        any(hint in parent for hint in LABEL_HINTS) for parent in normalized[:-1]
    ):
        return "ui_label_field"
    return None


def display_field_path(path: tuple[str | int, ...]) -> str:
    rendered = ""
    for part in path:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += ("." if rendered else "") + part
    return rendered


def serialized_file_name(obj: Any) -> str:
    assets_file = getattr(obj, "assets_file", None)
    name = getattr(assets_file, "name", None)
    return str(name or "<unknown-serialized-file>")


def object_name(tree: dict[str, Any], obj: Any) -> str:
    for key in ("m_Name", "name", "Name"):
        value = tree.get(key)
        if isinstance(value, str) and value:
            return value
    return f"{obj.type.name}_{obj.path_id}"


def container_paths(env: Any) -> dict[tuple[str, int], list[str]]:
    result: dict[tuple[str, int], list[str]] = defaultdict(list)
    try:
        entries = env.container.items()
    except Exception:
        return result
    for path, pointer in entries:
        try:
            obj = pointer.get_obj() if hasattr(pointer, "get_obj") else pointer
            key = (serialized_file_name(obj), int(obj.path_id))
            result[key].append(str(path))
        except Exception:
            continue
    return result


def scan_bundle(path: Path, root: Path, include_all_text: bool) -> tuple[list[dict[str, Any]], str | None]:
    try:
        env = UnityPy.load(path.read_bytes())
    except Exception as exc:
        return [], f"load: {type(exc).__name__}: {exc}"

    rows: list[dict[str, Any]] = []
    containers = container_paths(env)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict):
            continue
        asset_file = serialized_file_name(obj)
        name = object_name(tree, obj)
        for field_path, original in iter_strings(tree):
            category = classify(field_path)
            if category is None or not (
                safe_nonempty_text(original) if include_all_text else visible_japanese(original)
            ):
                continue
            relative = path.relative_to(root).as_posix()
            events = newline_events(original)
            rows.append(
                {
                    "original_flat": flatten_text(original),
                    "original": original,
                    "bundle_path": relative,
                    "serialized_file": asset_file,
                    "container_paths": sorted(containers.get((asset_file, int(obj.path_id)), [])),
                    "path_id": str(obj.path_id),
                    "object_type": obj.type.name,
                    "object_name": name,
                    "field_path": list(field_path),
                    "field_path_display": display_field_path(field_path),
                    "category": category,
                    "source_hash_sha256": text_sha256(original),
                    "newlines": {"count": len(events), "ordered": events},
                    "tokens": token_info(original),
                }
            )
    return rows, None


def resolve_scan_roots(game_root: Path, requested: list[Path]) -> list[Path]:
    if not requested:
        return [game_root]
    roots: list[Path] = []
    for value in requested:
        resolved = value.resolve() if value.is_absolute() else (game_root / value).resolve()
        if resolved != game_root and game_root not in resolved.parents:
            raise ValueError(f"scan root must be inside game_root: {resolved}")
        if not resolved.is_dir():
            raise FileNotFoundError(f"scan root is not a directory: {resolved}")
        roots.append(resolved)
    return roots


def discover_bundles(game_root: Path, scan_roots: list[Path], patterns: list[str]) -> list[Path]:
    found: set[Path] = set()
    for scan_root in scan_roots:
        for pattern in patterns:
            for candidate in scan_root.glob(pattern):
                resolved = candidate.resolve()
                if resolved.is_file() and (resolved == game_root or game_root in resolved.parents):
                    found.add(resolved)
    return sorted(found, key=lambda item: item.relative_to(game_root).as_posix().lower())


def safe_stem(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" ._")
    return cleaned or "unity"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_root", type=Path, help="Unity game root (read-only)")
    parser.add_argument(
        "--scan-root",
        type=Path,
        action="append",
        default=[],
        help="directory inside game_root to scan; repeatable (default: game_root)",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        help="recursive or direct glob relative to each scan root; repeatable "
        "(defaults: **/*.bundle and **/*.assets)",
    )
    parser.add_argument("--out-dir", type=Path, required=True, help="directory for CSV and manifest")
    parser.add_argument("--stem", help="output stem (default: <game-directory>_ui_text)")
    parser.add_argument(
        "--include-all-text",
        action="store_true",
        help="include classified non-Japanese fields as well as Japanese text",
    )
    parser.add_argument(
        "--strict-load-errors",
        action="store_true",
        help="return a failure when any matched file cannot be loaded by UnityPy",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.game_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"game_root is not a directory: {root}")
    try:
        scan_roots = resolve_scan_roots(root, args.scan_root)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    patterns = args.glob or ["**/*.bundle", "**/*.assets"]
    bundles = discover_bundles(root, scan_roots, patterns)
    if not bundles:
        raise SystemExit("no candidate bundles matched the requested scan roots and globs")

    all_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    bundle_hashes: dict[str, str] = {}
    for index, bundle in enumerate(bundles, 1):
        rows, error = scan_bundle(bundle, root, args.include_all_text)
        relative = bundle.relative_to(root).as_posix()
        if rows:
            all_rows.extend(rows)
            bundle_hashes[relative] = file_sha256(bundle)
            print(f"[{index}/{len(bundles)}] {relative}: {len(rows)} rows", file=sys.stderr)
        if error:
            errors.append({"bundle_path": relative, "error": error})

    occurrence_seen: Counter[str] = Counter()
    occurrence_totals = Counter(row["original"] for row in all_rows)
    rows_for_manifest: list[dict[str, Any]] = []
    for row_index, row in enumerate(all_rows):
        original = row["original"]
        occurrence_seen[original] += 1
        manifest_row = {key: value for key, value in row.items() if key != "original_flat"}
        manifest_row.update(
            {
                "row_index": row_index,
                "occurrence": occurrence_seen[original],
                "occurrence_count": occurrence_totals[original],
            }
        )
        rows_for_manifest.append(manifest_row)

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(args.stem or f"{root.name}_ui_text")
    csv_path = out_dir / f"{stem}.csv"
    manifest_path = out_dir / f"{stem}.manifest.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["original_flat"])
        writer.writerows([row["original_flat"]] for row in all_rows)

    manifest = {
        "format": FORMAT,
        "mode": "read-only-extraction",
        "source_csv_name": csv_path.name,
        "source_csv_sha256": file_sha256(csv_path),
        "source": {
            "game_root": str(root),
            "bundle_hashes_sha256": bundle_hashes,
        },
        "rows": rows_for_manifest,
        "scan": {
            "patterns": patterns,
            "candidate_count": len(bundles),
            "matched_occurrences": len(all_rows),
            "bundles_with_rows": len(bundle_hashes),
            "load_errors": errors,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "rows": len(all_rows),
        "bundles_with_rows": len(bundle_hashes),
        "load_errors": len(errors),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if args.strict_load_errors and errors else 0


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
