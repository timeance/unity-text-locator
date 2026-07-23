#!/usr/bin/env python3
"""Validate or stage TypeTree UI translations as external Unity candidates.

Dry-run is the default.  ``--write`` uses a two-phase transaction: every source
bundle and field is preflighted first, then candidates are built in a temporary
root, reopened with UnityPy, and verified field by field before promotion.
Addressables catalogs are deliberately outside this tool's scope.
"""
from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import UnityPy
except ImportError as exc:  # pragma: no cover - depends on the host environment
    raise SystemExit("UnityPy is required: install it in the active Python environment") from exc


FORMAT = "unity-typetree-ui-text-v2"
TOKEN_RE = re.compile(
    r"<[^<>]+>|\\[nrt]|\{[^{}\r\n]+\}|"
    r"%(?:\d+\$)?[-+#0 ]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]"
)
ALLOWED_CATEGORIES = {"component_text", "ui_label_field", "localization_string_table"}
TEXT_LEAVES = {"text", "mtext"}
VALUE_LEAVES = {"mvalue", "string", "value"}
LABEL_HINTS = ("label", "caption", "title", "description", "option", "tooltip")


class ValidationError(ValueError):
    """A deterministic manifest, source, or translation validation failure."""


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


def source_markers(value: str) -> list[str]:
    """Return real newline styles and literal backslash-n markers in order."""
    markers: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("\r\n", index):
            markers.append("\r\n")
            index += 2
        elif value[index] == "\n":
            markers.append("\n")
            index += 1
        elif value[index] == "\r":
            markers.append("\r")
            index += 1
        elif value.startswith("\\n", index):
            markers.append("\\n")
            index += 2
        else:
            index += 1
    return markers


def split_translation_markers(value: str) -> tuple[list[str], int]:
    """Split translated CSV text at real newlines or literal backslash-n."""
    pieces: list[str] = []
    start = 0
    index = 0
    count = 0
    while index < len(value):
        width = 0
        if value.startswith("\r\n", index) or value.startswith("\\n", index):
            width = 2
        elif value[index] in "\r\n":
            width = 1
        if width:
            pieces.append(value[start:index])
            count += 1
            index += width
            start = index
        else:
            index += 1
    pieces.append(value[start:])
    return pieces, count


def decode_translation(value: str, source: str) -> str:
    """Restore the source field's exact newline/literal-marker conventions."""
    if value == flatten_text(source) or value == source:
        return source
    markers = source_markers(source)
    pieces, marker_count = split_translation_markers(value)
    if marker_count != len(markers):
        raise ValidationError(
            f"newline/\\n marker count mismatch: source={len(markers)}, translation={marker_count}"
        )
    rebuilt = pieces[0]
    for marker, piece in zip(markers, pieces[1:]):
        rebuilt += marker + piece
    return rebuilt


def read_one_column_csv(path: Path) -> tuple[str, list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames) != 1:
            raise ValidationError(f"CSV must have exactly one column: {path}")
        column = reader.fieldnames[0]
        values: list[str] = []
        for row_number, row in enumerate(reader, 2):
            if None in row:
                raise ValidationError(f"CSV row {row_number} has extra columns: {path}")
            values.append(row.get(column) or "")
    return column, values


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def safe_relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationError("bundle_path must be a non-empty string")
    path = Path(value)
    if path.is_absolute() or path.drive or any(part == ".." for part in path.parts):
        raise ValidationError(f"unsafe bundle_path: {value!r}")
    return path


def normalized_name(value: str | int) -> str:
    return "" if isinstance(value, int) else value.lower().replace("_", "")


def validate_and_tuple_path(row: dict[str, Any]) -> tuple[str | int, ...]:
    raw = row.get("field_path")
    if not isinstance(raw, list) or not raw:
        raise ValidationError("field_path must be a non-empty typed list")
    path: list[str | int] = []
    for part in raw:
        if isinstance(part, bool) or not isinstance(part, (str, int)):
            raise ValidationError(f"invalid field_path segment: {part!r}")
        if isinstance(part, int) and part < 0:
            raise ValidationError(f"negative field_path index: {part}")
        if isinstance(part, str) and not part:
            raise ValidationError("empty field_path key")
        path.append(part)

    category = row.get("category")
    if category not in ALLOWED_CATEGORIES:
        raise ValidationError(f"unsupported category: {category!r}")
    names = [normalized_name(part) for part in path if isinstance(part, str)]
    leaf = names[-1] if names else ""
    is_localization = "mtabledata" in names and leaf == "mlocalized"
    is_component = leaf in TEXT_LEAVES
    is_label = any(hint in leaf for hint in LABEL_HINTS) or (
        leaf in VALUE_LEAVES
        and any(any(hint in parent for hint in LABEL_HINTS) for parent in names[:-1])
    )
    if category == "localization_string_table" and not is_localization:
        raise ValidationError("Localization row does not end in m_TableData.*.m_Localized")
    if category == "component_text" and not is_component:
        raise ValidationError("component_text row does not target m_text/text")
    if category == "ui_label_field" and not is_label:
        raise ValidationError("ui_label_field row does not target an allowed UI label field")
    return tuple(path)


def get_field(tree: Any, path: tuple[str | int, ...]) -> Any:
    value = tree
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or part >= len(value):
                raise ValidationError(f"field path list index unavailable: {part}")
            value = value[part]
        else:
            if not isinstance(value, dict) or part not in value:
                raise ValidationError(f"field path key unavailable: {part!r}")
            value = value[part]
    return value


def set_field(tree: Any, path: tuple[str | int, ...], replacement: str) -> None:
    parent = tree
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = replacement


def serialized_file_name(obj: Any) -> str:
    assets_file = getattr(obj, "assets_file", None)
    return str(getattr(assets_file, "name", None) or "<unknown-serialized-file>")


def object_name(tree: dict[str, Any], obj: Any) -> str:
    for key in ("m_Name", "name", "Name"):
        value = tree.get(key)
        if isinstance(value, str) and value:
            return value
    return f"{obj.type.name}_{obj.path_id}"


def object_index(env: Any) -> dict[tuple[str, int], list[Any]]:
    result: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for obj in env.objects:
        result[(serialized_file_name(obj), int(obj.path_id))].append(obj)
    return result


def unique_object(index: dict[tuple[str, int], list[Any]], row: dict[str, Any]) -> Any:
    asset_file = row.get("serialized_file")
    if not isinstance(asset_file, str) or not asset_file:
        raise ValidationError("serialized_file missing from manifest row")
    try:
        path_id = int(row.get("path_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"invalid PathID: {row.get('path_id')!r}") from exc
    matches = index.get((asset_file, path_id), [])
    if not matches:
        raise ValidationError(f"object missing: serialized_file={asset_file!r}, PathID={path_id}")
    if len(matches) != 1:
        raise ValidationError(
            f"object is ambiguous: serialized_file={asset_file!r}, PathID={path_id}, matches={len(matches)}"
        )
    return matches[0]


def validate_manifest_rows(manifest: dict[str, Any], source_values: list[str], translations: list[str]) -> list[dict[str, Any]]:
    if manifest.get("format") != FORMAT:
        raise ValidationError(f"unsupported manifest format: {manifest.get('format')!r}")
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise ValidationError("manifest rows must be a list")
    if not (len(rows) == len(source_values) == len(translations)):
        raise ValidationError(
            f"row count mismatch: manifest={len(rows)}, source_csv={len(source_values)}, "
            f"translation_csv={len(translations)}"
        )
    if [row.get("row_index") for row in rows if isinstance(row, dict)] != list(range(len(rows))):
        raise ValidationError("manifest row_index values are not contiguous and ordered")

    seen_locations: set[tuple[str, str, int, str]] = set()
    occurrence_groups: dict[str, list[tuple[int, int]]] = defaultdict(list)
    prepared: list[dict[str, Any]] = []
    for index, (row, source_flat, translation_flat) in enumerate(zip(rows, source_values, translations)):
        if not isinstance(row, dict):
            raise ValidationError(f"manifest row {index} is not an object")
        original = row.get("original")
        if not isinstance(original, str):
            raise ValidationError(f"row {index}: original must be a string")
        if flatten_text(original) != source_flat:
            raise ValidationError(f"row {index}: source CSV differs from manifest original")
        if row.get("source_hash_sha256") != text_sha256(original):
            raise ValidationError(f"row {index}: source field SHA-256 mismatch")
        expected_tokens = {"ordered": TOKEN_RE.findall(original), "count": len(TOKEN_RE.findall(original))}
        if row.get("tokens") != expected_tokens:
            raise ValidationError(f"row {index}: manifest token metadata mismatch")
        events = newline_events(original)
        if row.get("newlines") != {"count": len(events), "ordered": events}:
            raise ValidationError(f"row {index}: manifest newline metadata mismatch")

        field_path = validate_and_tuple_path(row)
        relative = safe_relative_path(row.get("bundle_path"))
        try:
            path_id = int(row.get("path_id"))
            occurrence = int(row.get("occurrence"))
            occurrence_count = int(row.get("occurrence_count"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"row {index}: invalid PathID or occurrence metadata") from exc
        location = (
            relative.as_posix(),
            str(row.get("serialized_file")),
            path_id,
            json.dumps(field_path, ensure_ascii=False, separators=(",", ":")),
        )
        if location in seen_locations:
            raise ValidationError(f"row {index}: duplicate object field location in manifest")
        seen_locations.add(location)
        occurrence_groups[original].append((occurrence, occurrence_count))

        if translation_flat:
            translated = decode_translation(translation_flat, original)
            translated_tokens = TOKEN_RE.findall(translated)
            if translated_tokens != expected_tokens["ordered"]:
                raise ValidationError(
                    f"row {index}: token sequence mismatch: source={expected_tokens['ordered']!r}, "
                    f"translation={translated_tokens!r}"
                )
            if newline_events(translated) != events:
                raise ValidationError(f"row {index}: newline style/count mismatch after decoding")
        else:
            translated = original

        prepared.append(
            {
                "row": row,
                "relative": relative,
                "field_path": field_path,
                "translated": translated,
                "changed": bool(translation_flat) and translated != original,
                "blank": not translation_flat,
            }
        )

    for original, occurrences in occurrence_groups.items():
        totals = {total for _, total in occurrences}
        expected_total = len(occurrences)
        if totals != {expected_total} or sorted(number for number, _ in occurrences) != list(
            range(1, expected_total + 1)
        ):
            raise ValidationError(
                f"occurrence metadata is inconsistent for source SHA-256 {text_sha256(original)}"
            )
    return prepared


def manifest_bundle_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    source = manifest.get("source")
    hashes = source.get("bundle_hashes_sha256") if isinstance(source, dict) else None
    if not isinstance(hashes, dict):
        raise ValidationError("manifest source.bundle_hashes_sha256 is missing")
    return hashes


def validate_source_csv_identity(manifest: dict[str, Any], source_csv: Path) -> tuple[str, str]:
    name = manifest.get("source_csv_name")
    expected_hash = manifest.get("source_csv_sha256")
    if not isinstance(name, str) or not name:
        raise ValidationError("manifest source_csv_name is missing")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise ValidationError("manifest source_csv_sha256 is missing or invalid")
    actual_hash = file_sha256(source_csv)
    if actual_hash.lower() != expected_hash.lower():
        raise ValidationError(
            f"source CSV SHA-256 mismatch: expected={expected_hash}, actual={actual_hash}"
        )
    return name, actual_hash


def resolve_bundle(root: Path, relative: Path, label: str) -> Path:
    candidate = (root / relative).resolve()
    if not inside(candidate, root):
        raise ValidationError(f"{label} bundle escapes its root: {relative.as_posix()}")
    return candidate


def verify_bundle_fields(env: Any, items: list[dict[str, Any]], *, expected_translated: bool) -> None:
    index = object_index(env)
    tree_cache: dict[tuple[str, int], dict[str, Any]] = {}
    for item in items:
        row = item["row"]
        obj = unique_object(index, row)
        key = (serialized_file_name(obj), int(obj.path_id))
        if key not in tree_cache:
            tree = obj.read_typetree()
            if not isinstance(tree, dict):
                raise ValidationError(f"object TypeTree is not a dictionary: {key!r}")
            if obj.type.name != row.get("object_type"):
                raise ValidationError(f"object type drift at {key!r}")
            if object_name(tree, obj) != row.get("object_name"):
                raise ValidationError(f"object name drift at {key!r}")
            tree_cache[key] = tree
        expected = item["translated"] if expected_translated else row["original"]
        current = get_field(tree_cache[key], item["field_path"])
        if not isinstance(current, str) or current != expected:
            phase = "candidate" if expected_translated else "source"
            raise ValidationError(f"row {row['row_index']}: {phase} field differs from expected text")


def apply_bundle_fields(env: Any, items: list[dict[str, Any]]) -> None:
    index = object_index(env)
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    objects: dict[tuple[str, int], Any] = {}
    for item in items:
        obj = unique_object(index, item["row"])
        key = (serialized_file_name(obj), int(obj.path_id))
        objects[key] = obj
        grouped[key].append(item)
    for key, object_items in grouped.items():
        obj = objects[key]
        tree = obj.read_typetree()
        if not isinstance(tree, dict):
            raise ValidationError(f"object TypeTree is not a dictionary: {key!r}")
        changed = False
        for item in object_items:
            row = item["row"]
            current = get_field(tree, item["field_path"])
            if current != row["original"]:
                raise ValidationError(f"row {row['row_index']}: source changed between preflight and write")
            if item["changed"]:
                set_field(tree, item["field_path"], item["translated"])
                changed = True
        if changed:
            obj.save_typetree(tree)


def write_report(report: dict[str, Any], path: Path | None) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if path is not None:
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_root", type=Path, help="original Unity game root (never modified)")
    parser.add_argument("--manifest", type=Path, required=True, help="v2 TypeTree UI manifest")
    parser.add_argument("--source-csv", type=Path, required=True, help="original one-column extraction CSV")
    parser.add_argument("--translation-csv", type=Path, required=True, help="matching one-column translation CSV")
    parser.add_argument(
        "--base-root",
        type=Path,
        help="external prior-candidate root; matching bundles are used as the write base",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        help="external candidate root (default: sibling <game-name>_ui_candidate)",
    )
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--write", action="store_true", help="write verified external candidates")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    game_root = args.game_root.resolve()
    if not game_root.is_dir():
        raise SystemExit(f"game_root is not a directory: {game_root}")
    base_root = args.base_root.resolve() if args.base_root else None
    if base_root is not None:
        if inside(base_root, game_root):
            raise SystemExit("--base-root must be outside game_root")
        if not base_root.is_dir():
            raise SystemExit(f"--base-root is not a directory: {base_root}")
    out_root = (
        args.out_root.resolve()
        if args.out_root
        else game_root.parent / f"{game_root.name}_ui_candidate"
    )
    out_root = out_root.resolve()
    if inside(out_root, game_root):
        raise SystemExit("candidate --out-root must be outside game_root")

    report: dict[str, Any] = {
        "format": "unity-typetree-ui-writeback-report-v2",
        "mode": "write-external-candidates" if args.write else "dry-run",
        "source_game_modified": False,
        "candidate_root": str(out_root) if args.write else None,
        "base_root": str(base_root) if base_root else None,
        "catalog_crc_policy": "not modified; apply bundle and matching external catalog candidate atomically",
        "counts": {"rows": 0, "changed": 0, "blank_preserved": 0, "unchanged": 0, "bundles": 0, "failed": 0},
        "bundles": [],
        "issues": [],
    }

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        source_csv_name, source_csv_hash = validate_source_csv_identity(manifest, args.source_csv)
        source_column, source_values = read_one_column_csv(args.source_csv)
        translation_column, translations = read_one_column_csv(args.translation_csv)
        prepared = validate_manifest_rows(manifest, source_values, translations)
        bundle_hashes = manifest_bundle_hashes(manifest)
        report["source_column"] = source_column
        report["source_csv_name"] = source_csv_name
        report["source_csv_sha256"] = source_csv_hash
        report["translation_column"] = translation_column
        report["counts"]["rows"] = len(prepared)
        report["counts"]["changed"] = sum(item["changed"] for item in prepared)
        report["counts"]["blank_preserved"] = sum(item["blank"] for item in prepared)
        report["counts"]["unchanged"] = sum(
            not item["changed"] and not item["blank"] for item in prepared
        )
    except Exception as exc:
        report["counts"]["failed"] = 1
        report["issues"].append({"phase": "manifest-and-csv-preflight", "reason": str(exc)})
        write_report(report, args.report)
        return 1

    by_bundle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in prepared:
        by_bundle[item["relative"].as_posix()].append(item)
    report["counts"]["bundles"] = len(by_bundle)

    # Phase 1: read-only validation of every bundle and every tracked field.
    preflight: dict[str, dict[str, Any]] = {}
    for relative_text, items in sorted(by_bundle.items()):
        relative = items[0]["relative"]
        entry: dict[str, Any] = {
            "bundle_path": relative_text,
            "base": None,
            "base_sha256": None,
            "changed_fields": sum(item["changed"] for item in items),
            "candidate": None,
            "candidate_sha256": None,
            "staged_sha256": None,
            "staged_reopen": None,
            "final_destination_reopen": None,
            "all_fields_verified": None,
        }
        report["bundles"].append(entry)
        try:
            original_path = resolve_bundle(game_root, relative, "game")
            if not original_path.is_file():
                raise ValidationError(f"original bundle is missing: {original_path}")
            expected_hash = bundle_hashes.get(relative_text)
            if not isinstance(expected_hash, str) or not expected_hash:
                raise ValidationError("bundle SHA-256 missing from manifest")
            actual_original_hash = file_sha256(original_path)
            if actual_original_hash.lower() != expected_hash.lower():
                raise ValidationError(
                    f"original bundle SHA-256 mismatch: expected={expected_hash}, actual={actual_original_hash}"
                )

            selected = original_path
            if base_root is not None:
                layered = resolve_bundle(base_root, relative, "base")
                if layered.is_file():
                    selected = layered
            selected_bytes = selected.read_bytes()
            selected_hash = hashlib.sha256(selected_bytes).hexdigest()
            env = UnityPy.load(selected_bytes)
            verify_bundle_fields(env, items, expected_translated=False)
            entry["base"] = str(selected)
            entry["base_sha256"] = selected_hash
            preflight[relative_text] = {"path": selected, "sha256": selected_hash, "entry": entry}
        except Exception as exc:
            report["counts"]["failed"] += 1
            report["issues"].append(
                {"phase": "bundle-preflight", "bundle_path": relative_text, "reason": str(exc)}
            )

    if report["counts"]["failed"] or not args.write:
        write_report(report, args.report)
        return 1 if report["counts"]["failed"] else 0

    changed_bundles = [key for key, items in by_bundle.items() if any(item["changed"] for item in items)]
    if not changed_bundles:
        write_report(report, args.report)
        return 0

    # Phase 2: build and reopen all candidates in an external temporary root.
    out_root.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".unity-ui-candidate-", dir=str(out_root.parent))).resolve()
    staged_root = scratch / "staged"
    backups_root = scratch / "backups"
    staged: dict[str, Path] = {}
    try:
        for relative_text in sorted(changed_bundles):
            items = by_bundle[relative_text]
            state = preflight[relative_text]
            selected: Path = state["path"]
            selected_bytes = selected.read_bytes()
            if hashlib.sha256(selected_bytes).hexdigest() != state["sha256"]:
                raise ValidationError(f"base bundle changed after preflight: {relative_text}")
            env = UnityPy.load(selected_bytes)
            verify_bundle_fields(env, items, expected_translated=False)
            apply_bundle_fields(env, items)
            staged_path = resolve_bundle(staged_root, items[0]["relative"], "staging")
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_bytes = env.file.save()
            staged_path.write_bytes(candidate_bytes)

            reopened = UnityPy.load(candidate_bytes)
            verify_bundle_fields(reopened, items, expected_translated=True)
            entry = state["entry"]
            entry["staged_reopen"] = True
            entry["staged_sha256"] = file_sha256(staged_path)
            staged[relative_text] = staged_path
            del reopened
            del env

        # UnityPy can retain a Windows file handle until its environments are
        # collected.  Release them before atomically promoting staged bundles.
        gc.collect()

        # Promotion has rollback for pre-existing external candidates.  No path
        # in game_root is ever a source or destination of these moves.
        promoted: list[tuple[str, Path, Path | None]] = []
        try:
            for ordinal, relative_text in enumerate(sorted(staged)):
                relative = by_bundle[relative_text][0]["relative"]
                destination = resolve_bundle(out_root, relative, "candidate")
                if inside(destination, game_root):
                    raise ValidationError("candidate path resolved inside game_root")
                destination.parent.mkdir(parents=True, exist_ok=True)
                backup: Path | None = None
                if destination.exists():
                    if not destination.is_file():
                        raise ValidationError(f"candidate destination is not a file: {destination}")
                    backup = backups_root / f"{ordinal:06d}.bak"
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, backup)
                try:
                    os.replace(staged[relative_text], destination)
                except Exception:
                    if backup is not None and backup.exists():
                        os.replace(backup, destination)
                    raise
                promoted.append((relative_text, destination, backup))

            # The transaction is not complete until the exact bytes at every
            # final destination reopen and every tracked field verifies.  A
            # failure here rolls back all destinations promoted above.
            for relative_text, destination, _ in promoted:
                destination_bytes = destination.read_bytes()
                final_env = UnityPy.load(destination_bytes)
                verify_bundle_fields(final_env, by_bundle[relative_text], expected_translated=True)
                final_hash = hashlib.sha256(destination_bytes).hexdigest()
                entry = preflight[relative_text]["entry"]
                if final_hash != entry["staged_sha256"]:
                    raise ValidationError(f"final destination bytes differ from staged candidate: {relative_text}")
                entry["final_destination_reopen"] = True
                entry["all_fields_verified"] = True
                entry["candidate_sha256"] = final_hash
                del final_env

            for relative_text, destination, _ in promoted:
                preflight[relative_text]["entry"]["candidate"] = str(destination)
        except Exception:
            for relative_text, destination, backup in reversed(promoted):
                if destination.exists():
                    destination.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, destination)
                entry = preflight[relative_text]["entry"]
                entry["candidate"] = None
                entry["candidate_sha256"] = None
                entry["final_destination_reopen"] = False
                entry["all_fields_verified"] = False
            raise
    except Exception as exc:
        report["counts"]["failed"] += 1
        report["issues"].append({"phase": "candidate-build-or-promotion", "reason": str(exc)})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    write_report(report, args.report)
    return 1 if report["counts"]["failed"] else 0


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
