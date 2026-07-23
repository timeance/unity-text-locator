#!/usr/bin/env python3
"""Extract fixed-width DMSL UTF-8 operands from Unity MonoBehaviours.

The supported operand is ``0x06 + uint32 little-endian byte length + UTF-8``.
Every duplicate is retained as a separate occurrence in a sidecar manifest.
This command is read-only with respect to Unity inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import UnityPy


FORMAT = "dmsl-0x06-u32le-utf8-v3"
KANA_RE = re.compile(r"[\u3040-\u30ff]")
JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
PATHISH_RE = re.compile(
    r"(?:^|[/\\])(?:assets?|resources?|scenario|script|voice|audio|bgm|se|cg|image|sprite)"
    r"(?:[/\\]|$)|\.(?:asset|prefab|png|jpe?g|webp|wav|ogg|mp3|mp4|anim|controller|mat|shader)$",
    re.IGNORECASE,
)
INVALID_STEM_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
IDENTIFIER_RE = re.compile(r"[\w./\\:@+\-]+", re.UNICODE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def flatten_text(text: str) -> str:
    """Represent control whitespace without putting physical newlines in CSV."""
    return (
        text.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def safe_stem(value: str) -> str:
    stem = INVALID_STEM_RE.sub("_", value).strip().rstrip(".")
    if not stem:
        raise ValueError("project name is empty after filename sanitization")
    return stem


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve_under(root: Path, value: Path) -> Path:
    path = (root / value).resolve() if not value.is_absolute() else value.resolve()
    if not is_within(path, root):
        raise ValueError(f"input must be within game_root: {path}")
    return path


def unwrap_byte_array(value: Any) -> tuple[list[Any] | None, tuple[str, ...]]:
    suffix: list[str] = []
    seen: set[int] = set()
    while isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            return None, ()
        seen.add(identity)
        key = next((candidate for candidate in ("data", "Array", "bytecode") if candidate in value), None)
        if key is None:
            return None, ()
        suffix.append(key)
        value = value[key]
    return (value, tuple(suffix)) if isinstance(value, list) else (None, ())


def iter_bytecode_arrays(value: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], list[Any]]]:
    """Yield exact paths to fields named bytecode, including list nesting."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if key.lower() == "bytecode":
                array, suffix = unwrap_byte_array(child)
                if array is not None:
                    yield child_path + suffix, array
                    continue
            if isinstance(child, (dict, list)):
                yield from iter_bytecode_arrays(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                yield from iter_bytecode_arrays(child, path + (index,))


def visible_japanese(text: str) -> bool:
    stripped = text.strip()
    if not KANA_RE.search(stripped) or len(JP_RE.findall(stripped)) < 2:
        return False
    if PATHISH_RE.search(stripped):
        return False
    if IDENTIFIER_RE.fullmatch(stripped) and not re.search(r"[、。！？!?…「」『』\s]", stripped):
        return False
    return valid_visible_text(text)


def valid_visible_text(text: str) -> bool:
    if not text or "\x00" in text:
        return False
    if any(ord(char) < 0x20 and char not in "\n\r\t" for char in text):
        return False
    return any(not char.isspace() for char in text)


def parse_operands(data: bytes, include_all_utf8: bool) -> Iterator[tuple[int, int, bytes, str]]:
    """Yield syntactically valid operands without deduplicating occurrences."""
    for offset in range(max(0, len(data) - 5)):
        if data[offset] != 0x06:
            continue
        byte_length = struct.unpack_from("<I", data, offset + 1)[0]
        start = offset + 5
        end = start + byte_length
        if byte_length == 0 or end > len(data):
            continue
        payload = data[start:end]
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        if valid_visible_text(text) and (include_all_utf8 or visible_japanese(text)):
            yield offset, byte_length, payload, text


def object_name(tree: dict[str, Any], path_id: int) -> str:
    for key in ("m_Name", "name", "Name"):
        value = tree.get(key)
        if isinstance(value, str) and value:
            return value
    return f"MonoBehaviour_{path_id}"


def serialized_file_name(obj: Any) -> str:
    assets_file = getattr(obj, "assets_file", None)
    name = getattr(assets_file, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError(f"path_id {obj.path_id} has no stable serialized file name")
    return name


def normalize_byte_array(raw: Sequence[Any]) -> bytes:
    try:
        return bytes(int(value) & 0xFF for value in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("bytecode field is not an integer byte array") from exc


def collect_candidates(
    game_root: Path,
    inputs: Sequence[Path],
    candidate_roots: Sequence[Path],
    patterns: Sequence[str],
) -> list[Path]:
    found: dict[str, Path] = {}

    def add(path: Path) -> None:
        resolved = resolve_under(game_root, path)
        if not resolved.is_file():
            raise ValueError(f"input file does not exist: {resolved}")
        key = str(resolved).casefold()
        found[key] = resolved

    for value in inputs:
        resolved = resolve_under(game_root, value)
        if resolved.is_file():
            add(resolved)
        elif resolved.is_dir():
            for pattern in patterns:
                for path in resolved.rglob(pattern):
                    if path.is_file():
                        add(path)
        else:
            raise ValueError(f"input does not exist: {resolved}")
    for value in candidate_roots:
        root = resolve_under(game_root, value)
        if not root.is_dir():
            raise ValueError(f"candidate root is not a directory: {root}")
        for pattern in patterns:
            for path in root.rglob(pattern):
                if path.is_file():
                    add(path)
    return sorted(found.values(), key=lambda item: item.as_posix().casefold())


def scan_source(path: Path, game_root: Path, include_all_utf8: bool) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    env = UnityPy.load(str(path))
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        serialized_file = serialized_file_name(obj)
        try:
            tree = obj.read_typetree()
        except Exception as exc:
            warnings.append(f"path_id {obj.path_id}: typetree: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(tree, dict):
            continue
        for field_path, raw in iter_bytecode_arrays(tree):
            try:
                data = normalize_byte_array(raw)
            except ValueError as exc:
                warnings.append(f"path_id {obj.path_id} field {list(field_path)!r}: {exc}")
                continue
            for offset, width, payload, text in parse_operands(data, include_all_utf8):
                rows.append(
                    {
                        "source_path": path.relative_to(game_root).as_posix(),
                        "serialized_file": serialized_file,
                        "path_id": int(obj.path_id),
                        "object_type": obj.type.name,
                        "object_name": object_name(tree, int(obj.path_id)),
                        "bytecode_field_path": list(field_path),
                        "bytecode_offset": offset,
                        "byte_length": width,
                        "original_utf8_sha256": sha256_bytes(payload),
                        "original_flat": flatten_text(text),
                    }
                )
    return rows, warnings


def write_source_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["original_flat"])
        writer.writerows([row["original_flat"]] for row in rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_root", type=Path, help="root used for portable manifest-relative source paths")
    parser.add_argument("--input", type=Path, action="append", default=[], help="explicit file or directory; repeatable")
    parser.add_argument(
        "--candidate-root", type=Path, action="append", default=[],
        help="directory to scan recursively below game_root; repeatable",
    )
    parser.add_argument("--glob", dest="patterns", action="append", help="candidate filename glob; repeatable")
    parser.add_argument("--project-name", help="output filename prefix (default: game_root folder name)")
    parser.add_argument("--out-dir", type=Path, help="report directory (default: game_root/_translation/.../text)")
    parser.add_argument(
        "--include-all-utf8", action="store_true",
        help="include every printable UTF-8 operand instead of conservative Japanese-visible rows",
    )
    parser.add_argument(
        "--allow-load-errors", action="store_true",
        help="write an incomplete extraction and return success when some Unity inputs fail to load",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    game_root = args.game_root.resolve()
    if not game_root.is_dir():
        raise SystemExit(f"game_root is not a directory: {game_root}")
    if not args.input and not args.candidate_root:
        raise SystemExit("provide at least one --input or --candidate-root")
    patterns = args.patterns or ["*.bundle"]
    try:
        candidates = collect_candidates(game_root, args.input, args.candidate_root, patterns)
        project_name = safe_stem(args.project_name or game_root.name)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not candidates:
        raise SystemExit("no candidate files matched")

    out_dir = (args.out_dir or game_root / "_translation" / "unity-text-report" / "text").resolve()
    csv_path = out_dir / f"{project_name}_dmsl_text.csv"
    manifest_path = out_dir / f"{project_name}_dmsl_text.manifest.json"
    all_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}

    for index, source in enumerate(candidates, 1):
        relative = source.relative_to(game_root).as_posix()
        try:
            rows, source_warnings = scan_source(source, game_root, args.include_all_utf8)
            source_hashes[relative] = sha256_file(source)
            all_rows.extend(rows)
            if source_warnings:
                warnings.append({"source_path": relative, "messages": source_warnings})
            print(f"[{index}/{len(candidates)}] {relative}: {len(rows)}", file=sys.stderr)
        except Exception as exc:
            errors.append({"source_path": relative, "error": f"{type(exc).__name__}: {exc}"})

    locations: set[tuple[Any, ...]] = set()
    occurrence_counts: defaultdict[str, int] = defaultdict(int)
    manifest_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(all_rows):
        location = (
            row["source_path"], row["serialized_file"], row["path_id"], row["object_type"],
            json.dumps(row["bytecode_field_path"], ensure_ascii=True), row["bytecode_offset"],
        )
        if location in locations:
            raise SystemExit(f"duplicate extracted location: {location!r}")
        locations.add(location)
        original_key = row["original_utf8_sha256"]
        occurrence_index = occurrence_counts[original_key]
        occurrence_counts[original_key] += 1
        occurrence_seed = json.dumps(location, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        manifest_rows.append(
            {
                "row_index": row_index,
                "occurrence_index": occurrence_index,
                "occurrence_id": sha256_bytes(occurrence_seed)[:24],
                **{key: value for key, value in row.items() if key != "original_flat"},
            }
        )

    write_source_csv(csv_path, all_rows)
    manifest = {
        "format": FORMAT,
        "text_escape": "backslash-controls-v1",
        "fixed_width": True,
        "source_csv_name": csv_path.name,
        "source_csv_sha256": sha256_file(csv_path),
        "source_hashes_sha256": source_hashes,
        "rows": manifest_rows,
        "scan": {
            "candidate_count": len(candidates),
            "matched_occurrences": len(manifest_rows),
            "load_errors": errors,
            "warnings": warnings,
            "filter": "all-printable-utf8" if args.include_all_utf8 else "conservative-visible-japanese",
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "rows": len(manifest_rows),
        "sources": len(source_hashes),
        "load_errors": len(errors),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if errors and not args.allow_load_errors:
        return 1
    return 0


if __name__ == "__main__":
    exit_code = main()
    # Some UnityPy native dependencies crash in CPython finalization after a
    # large multi-bundle scan. This is a single-shot read-only CLI, so flush
    # completed outputs and bypass that native teardown path.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
