#!/usr/bin/env python3
"""Trace visible TMP text components to FontAsset and material bundles.

The tool scans explicit component assets or every bundle under candidate roots,
then resolves PPtr FileIDs through serialized-file externals and a CAB-to-bundle
index. It is read-only and emits a JSON report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


MATERIAL_FIELDS = (
    "m_Material",
    "m_sharedMaterial",
    "m_fontMaterial",
    "m_fontSharedMaterials",
    "m_fontMaterials",
)


def print_json(value: Any, *, pretty: bool = False) -> None:
    # Keep stdout machine-readable on legacy Windows code pages. `--out`
    # retains the original Unicode in a UTF-8 report.
    print(json.dumps(value, ensure_ascii=True, indent=2 if pretty else None))


def normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        resolved = path.resolve()
        unique[normalized(resolved)] = resolved
    return list(unique.values())


def collect_inputs(args: argparse.Namespace) -> tuple[list[Path], list[Path]]:
    explicit = [Path(value).resolve() for value in args.asset]
    for path in explicit:
        if not path.is_file():
            raise FileNotFoundError(f"asset not found: {path}")

    rooted: list[Path] = []
    for value in args.candidate_root:
        root = Path(value).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"candidate root not found: {root}")
        rooted.extend(sorted(path.resolve() for path in root.rglob("*.bundle") if path.is_file()))

    index_files = dedupe_paths([*explicit, *rooted])
    if not index_files:
        raise ValueError("provide at least one --asset or --candidate-root")
    source_files = dedupe_paths(explicit) if explicit else index_files
    return source_files, index_files


def pptr(value: Any) -> dict[str, int] | None:
    if isinstance(value, dict):
        file_id = value.get("m_FileID")
        path_id = value.get("m_PathID")
    else:
        file_id = getattr(value, "m_FileID", None)
        path_id = getattr(value, "m_PathID", None)
    if file_id is None or path_id is None:
        return None
    return {"file_id": int(file_id), "path_id": int(path_id)}


def pptrs_from_value(value: Any, field_path: str) -> list[dict[str, Any]]:
    direct = pptr(value)
    if direct is not None:
        return [{"field_path": field_path, **direct}]
    if isinstance(value, list):
        found: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            found.extend(pptrs_from_value(item, f"{field_path}[{index}]"))
        return found
    return []


def serialized_name(obj: Any) -> str:
    return str(getattr(obj.assets_file, "name", "") or "")


def external_values(external: Any) -> tuple[str, str]:
    if isinstance(external, dict):
        name = str(external.get("name", "") or "")
        path = str(external.get("path", "") or "")
    else:
        name = str(getattr(external, "name", "") or "")
        path = str(getattr(external, "path", "") or "")
    if not name and path:
        name = path.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name, path


def externals_for(obj: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, external in enumerate(getattr(obj.assets_file, "externals", []) or [], start=1):
        name, path = external_values(external)
        result.append({"file_id": index, "name": name, "path": path})
    return result


def object_name(tree: dict[str, Any], obj: Any, local_objects: dict[tuple[int, int], Any]) -> str:
    own_name = tree.get("m_Name")
    if isinstance(own_name, str) and own_name:
        return own_name
    game_object = pptr(tree.get("m_GameObject"))
    if game_object and game_object["file_id"] == 0 and game_object["path_id"]:
        target = local_objects.get((id(obj.assets_file), game_object["path_id"]))
        if target is not None:
            try:
                name = getattr(target.read(check_read=False), "m_Name", "")
                if isinstance(name, str) and name:
                    return name
            except Exception:
                pass
    return f"{obj.type.name}_{obj.path_id}"


def matches_filters(
    component: dict[str, Any],
    path_ids: set[int],
    text_needles: list[str],
    name_needles: list[str],
) -> bool:
    if path_ids and component["path_id"] not in path_ids:
        return False
    if text_needles and not any(needle in component["text"] for needle in text_needles):
        return False
    folded_name = component["object_name"].casefold()
    if name_needles and not any(needle.casefold() in folded_name for needle in name_needles):
        return False
    return True


def resolve_pointer(
    pointer: dict[str, Any],
    source_serialized_file: str,
    source_bundle: str,
    externals: list[dict[str, Any]],
    cab_index: dict[str, set[str]],
    object_index: dict[tuple[str, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    file_id = int(pointer["file_id"])
    path_id = int(pointer["path_id"])
    external_name: str | None = None
    external_path: str | None = None
    error: str | None = None
    if file_id == 0:
        target_serialized_file = source_serialized_file
    elif 1 <= file_id <= len(externals):
        external = externals[file_id - 1]
        external_name = external["name"]
        external_path = external["path"]
        target_serialized_file = external_name
    else:
        target_serialized_file = ""
        error = f"FileID {file_id} is outside the {len(externals)}-entry external table"

    key = target_serialized_file.casefold()
    locators = object_index.get((key, path_id), []) if key and path_id else []
    bundles = sorted({item["bundle"] for item in locators})
    if not bundles and key:
        bundles = sorted(cab_index.get(key, set()))
    if file_id == 0 and source_bundle not in bundles:
        bundles.insert(0, source_bundle)
    bundles = sorted(set(bundles))

    return {
        **pointer,
        "external_index": file_id - 1 if file_id > 0 else None,
        "external_name": external_name,
        "external_path": external_path,
        "target_serialized_file": target_serialized_file or None,
        "actual_bundle": bundles[0] if len(bundles) == 1 else None,
        "bundle_candidates": bundles,
        "target_object_found": bool(locators),
        "target_object_types": sorted({item["type"] for item in locators}),
        "resolution_error": error or ("ambiguous CAB-to-bundle mapping" if len(bundles) > 1 else None),
    }


def scan_bundle_payload(asset_path: Path, scan_components: bool) -> dict[str, Any]:
    try:
        import UnityPy  # type: ignore
    except ImportError as exc:
        raise RuntimeError("UnityPy is required to trace TMP references") from exc

    asset_string = str(asset_path)
    env = UnityPy.load(asset_string)
    objects = list(env.objects)
    if not objects:
        raise ValueError(f"asset contains no Unity objects: {asset_path}")
    local_objects = {(id(obj.assets_file), int(obj.path_id)): obj for obj in objects}
    serialized_files = sorted({serialized_name(obj) for obj in objects if serialized_name(obj)})
    object_entries = [
        {
            "bundle": asset_string,
            "serialized_file": serialized_name(obj),
            "path_id": int(obj.path_id),
            "type": obj.type.name,
        }
        for obj in objects
    ]
    raw_components: list[dict[str, Any]] = []
    typetree_errors: Counter[str] = Counter()
    if scan_components:
        for obj in objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                tree = obj.read_typetree()
            except Exception as exc:
                typetree_errors[type(exc).__name__] += 1
                continue
            if not isinstance(tree.get("m_text"), str) or "m_fontAsset" not in tree:
                continue
            materials: list[dict[str, Any]] = []
            for field in MATERIAL_FIELDS:
                if field in tree:
                    materials.extend(pptrs_from_value(tree[field], field))
            raw_components.append(
                {
                    "source_bundle": asset_string,
                    "serialized_file": serialized_name(obj),
                    "path_id": int(obj.path_id),
                    "component_type": obj.type.name,
                    "object_name": object_name(tree, obj, local_objects),
                    "text": tree["m_text"],
                    "font_asset": pptr(tree.get("m_fontAsset")),
                    "materials": [item for item in materials if item["path_id"] != 0],
                    "externals": externals_for(obj),
                }
            )
    payload = {
        "file": {
            "asset": asset_string,
            "source_component_scan": scan_components,
            "object_count": len(objects),
            "serialized_files": serialized_files,
        },
        "object_entries": object_entries,
        "raw_components": raw_components,
        "typetree_errors": dict(typetree_errors),
    }
    # Keep UnityPy/native decompression objects out of the returned payload.
    obj = None
    tree = None
    local_objects.clear()
    objects.clear()
    del local_objects, objects, env
    return payload


def isolated_component_scan(asset_path: Path) -> dict[str, Any]:
    command = [sys.executable, str(Path(__file__).resolve()), "--_worker-scan", str(asset_path)]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    stdout = completed.stdout.decode("utf-8", errors="replace").splitlines()
    if completed.returncode != 0 or not stdout:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"isolated scan failed for {asset_path} (exit {completed.returncode}): {stderr}"
        )
    try:
        payload = json.loads(stdout[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"isolated scan returned invalid JSON for {asset_path}") from exc
    if not isinstance(payload, dict) or "raw_components" not in payload:
        raise RuntimeError(f"isolated scan returned an invalid payload for {asset_path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        help="component asset/bundle to scan (repeatable); roots then index dependencies only",
    )
    parser.add_argument(
        "--candidate-root",
        action="append",
        default=[],
        help="recursively index *.bundle files; also scan them when --asset is omitted",
    )
    parser.add_argument("--path-id", action="append", type=int, default=[], help="component PathID")
    parser.add_argument(
        "--text-contains", action="append", default=[], help="keep components containing any value"
    )
    parser.add_argument(
        "--object-name-contains",
        action="append",
        default=[],
        help="keep components whose GameObject/component name contains any value",
    )
    parser.add_argument("--out", type=Path, help="optional UTF-8 JSON output path")
    parser.add_argument("--_worker-scan", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        if args._worker_scan:
            payload = scan_bundle_payload(Path(args._worker_scan).resolve(), True)
            print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
            return 0

        source_files, index_files = collect_inputs(args)
        source_keys = {normalized(path) for path in source_files}
        cab_index: dict[str, set[str]] = defaultdict(set)
        object_index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        raw_components: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        scan_errors: list[dict[str, Any]] = []
        typetree_error_counts: Counter[str] = Counter()
        tmp_component_count = 0

        isolate_sources = len(source_files) > 1
        for asset_path in index_files:
            asset_string = str(asset_path)
            is_source = normalized(asset_path) in source_keys
            try:
                payload = (
                    isolated_component_scan(asset_path)
                    if is_source and isolate_sources
                    else scan_bundle_payload(asset_path, is_source)
                )
            except Exception as exc:
                error = {"asset": asset_string, "stage": "load", "error": str(exc)}
                if is_source:
                    raise RuntimeError(f"failed to load source asset {asset_path}: {exc}") from exc
                scan_errors.append(error)
                continue
            file_entry = payload["file"]
            file_entry["source_component_scan"] = is_source
            for name in file_entry["serialized_files"]:
                cab_index[name.casefold()].add(asset_string)
            for entry in payload["object_entries"]:
                object_index[(entry["serialized_file"].casefold(), int(entry["path_id"]))].append(
                    {
                        "bundle": entry["bundle"],
                        "serialized_file": entry["serialized_file"],
                        "type": entry["type"],
                    }
                )
            files.append(file_entry)
            raw_components.extend(payload["raw_components"])
            tmp_component_count += len(payload["raw_components"])
            typetree_error_counts.update(payload["typetree_errors"])

        selected = [
            item
            for item in raw_components
            if matches_filters(
                item,
                set(args.path_id),
                list(args.text_contains),
                list(args.object_name_contains),
            )
        ]
        components: list[dict[str, Any]] = []
        for item in selected:
            externals = item.pop("externals")
            font = item.pop("font_asset")
            materials = item.pop("materials")
            components.append(
                {
                    **item,
                    "external_count": len(externals),
                    "font_asset": resolve_pointer(
                        font,
                        item["serialized_file"],
                        item["source_bundle"],
                        externals,
                        cab_index,
                        object_index,
                    )
                    if font
                    else None,
                    "materials": [
                        {
                            "field_path": pointer["field_path"],
                            **resolve_pointer(
                                pointer,
                                item["serialized_file"],
                                item["source_bundle"],
                                externals,
                                cab_index,
                                object_index,
                            ),
                        }
                        for pointer in materials
                    ],
                }
            )

        usage: dict[tuple[str, int, tuple[str, ...]], dict[str, Any]] = {}
        for component in components:
            font = component["font_asset"]
            if not font:
                key = ("<null>", 0, ())
            else:
                key = (
                    str(font["target_serialized_file"] or "<unresolved>"),
                    int(font["path_id"]),
                    tuple(font["bundle_candidates"]),
                )
            if key not in usage:
                usage[key] = {
                    "target_serialized_file": key[0],
                    "path_id": key[1],
                    "actual_bundle": key[2][0] if len(key[2]) == 1 else None,
                    "bundle_candidates": list(key[2]),
                    "component_count": 0,
                }
            usage[key]["component_count"] += 1

        report = {
            "format": "tmp-font-usage-trace-v1",
            "mode": "read-only",
            "source_assets": [str(path) for path in source_files],
            "index_assets": [str(path) for path in index_files],
            "filters": {
                "path_ids": args.path_id,
                "text_contains_any": args.text_contains,
                "object_name_contains_any": args.object_name_contains,
                "cross_category_semantics": "AND",
            },
            "source_asset_count": len(source_files),
            "index_asset_count": len(index_files),
            "indexed_serialized_file_count": len(cab_index),
            "tmp_component_count_before_filters": tmp_component_count,
            "matched_component_count": len(components),
            "font_usage": sorted(
                usage.values(),
                key=lambda item: (-item["component_count"], item["target_serialized_file"], item["path_id"]),
            ),
            "components": components,
            "files": files,
            "scan_errors": scan_errors,
            "typetree_errors": dict(sorted(typetree_error_counts.items())),
        }
        if args.out:
            out = args.out.resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print_json(
                {
                    "ok": bool(components),
                    "report": str(out),
                    "matched_component_count": len(components),
                    "font_asset_count": len(usage),
                }
            )
        else:
            print_json(report, pretty=True)
        return 0 if components else 3
    except Exception as exc:
        print_json({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    exit_code = main()
    if "--_worker-scan" in sys.argv:
        # A worker is a single-shot UnityPy process. Flush its JSON payload and
        # bypass native finalizers that can crash after large TypeTree scans.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)
    raise SystemExit(exit_code)
