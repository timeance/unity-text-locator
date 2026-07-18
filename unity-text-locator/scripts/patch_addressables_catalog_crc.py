#!/usr/bin/env python3
"""Prepare an external binary Addressables catalog candidate with selected CRCs patched.

Bundle records are located from each bundle's AssetBundle.m_Name. The source
catalog, catalog.hash, and bundleSize fields are never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any


RECORD = struct.Struct("<5I")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def print_json(value: Any) -> None:
    # ASCII-escaped stdout stays one valid JSON document even on legacy
    # Windows code pages. UTF-8 paths remain intact in the report file.
    print(json.dumps(value, ensure_ascii=True))


def all_offsets(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return offsets
        offsets.append(offset)
        start = offset + 1


def normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def is_within(path: Path, root: Path) -> bool:
    path_value = normalized(path)
    root_value = normalized(root)
    try:
        return os.path.commonpath([path_value, root_value]) == root_value
    except ValueError:
        return False


def infer_game_root(catalog: Path) -> Path | None:
    for parent in catalog.parents:
        if parent.name.casefold().endswith("_data"):
            return parent.parent.resolve()
    return None


def parse_u32(value: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("CRC must be an integer such as 0 or 0x1234abcd") from exc
    if not 0 <= parsed <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("CRC must fit an unsigned 32-bit integer")
    return parsed


def list_entry_path(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("target", "bundle", "path", "file"):
            value = item.get(key)
            if isinstance(value, str):
                return value
    raise ValueError(f"bundle-list entry has no path: {item!r}")


def load_bundle_list(path: Path) -> list[Path]:
    base = path.resolve().parent
    if path.suffix.casefold() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            for key in ("files", "bundles", "targets", "entries"):
                if key in raw:
                    raw = raw[key]
                    break
        if not isinstance(raw, list):
            raise ValueError("JSON bundle list must be an array or contain files/bundles/targets/entries")
        values = [list_entry_path(item) for item in raw]
    else:
        values = [
            line.strip()
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    return [(Path(value) if Path(value).is_absolute() else base / value).resolve() for value in values]


def collect_bundles(args: argparse.Namespace) -> list[Path]:
    paths = [Path(value).resolve() for value in args.bundle]
    for list_name in args.bundle_list:
        list_path = Path(list_name).resolve()
        if not list_path.is_file():
            raise FileNotFoundError(f"bundle list not found: {list_path}")
        paths.extend(load_bundle_list(list_path))
    for root_name in args.candidate_root:
        root = Path(root_name).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"candidate root not found: {root}")
        paths.extend(sorted(path.resolve() for path in root.rglob("*.bundle") if path.is_file()))

    unique: dict[str, Path] = {}
    for path in paths:
        unique[normalized(path)] = path
    if not unique:
        raise ValueError("provide at least one --bundle, --bundle-list, or --candidate-root")
    for path in unique.values():
        if not path.is_file():
            raise FileNotFoundError(f"bundle not found: {path}")
    return list(unique.values())


def read_internal_bundle_name(path: Path) -> str:
    try:
        import UnityPy  # type: ignore
    except ImportError as exc:
        raise RuntimeError("UnityPy is required to read AssetBundle.m_Name") from exc

    env = UnityPy.load(str(path))
    names: list[str] = []
    for obj in env.objects:
        if obj.type.name != "AssetBundle":
            continue
        value = getattr(obj.read(check_read=False), "m_Name", None)
        if isinstance(value, str) and value:
            names.append(value)
    if len(names) != 1:
        raise ValueError(f"{path}: expected one non-empty AssetBundle.m_Name, found {names!r}")
    return names[0]


def catalog_key(internal_name: str) -> bytes:
    value = internal_name[:-7] if internal_name.casefold().endswith(".bundle") else internal_name
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"internal bundle name is not valid UTF-8 text: {internal_name!r}") from exc


def locate_record(catalog: bytes, internal_name: str, external_size: int) -> dict[str, Any]:
    key = catalog_key(internal_name)
    string_offsets = all_offsets(catalog, key)
    if len(string_offsets) != 1:
        raise ValueError(
            f"catalog key {key!r} for {internal_name!r} occurs {len(string_offsets)} times; expected 1"
        )
    string_offset = string_offsets[0]
    pointer_offsets = all_offsets(catalog, struct.pack("<I", string_offset))
    candidates: list[tuple[int, tuple[int, int, int, int, int]]] = []
    for pointer_offset in pointer_offsets:
        record_offset = pointer_offset - 4
        if record_offset < 0 or record_offset + RECORD.size > len(catalog):
            continue
        values = RECORD.unpack_from(catalog, record_offset)
        hash_id, bundle_name_id, _crc, bundle_size, common_id = values
        if (
            bundle_name_id == string_offset
            and 0 <= hash_id <= len(catalog) - 16
            and 0 <= common_id <= len(catalog) - 4
            and bundle_size > 0
        ):
            candidates.append((record_offset, values))
    if len(candidates) != 1:
        raise ValueError(
            f"catalog key {internal_name!r} resolves to {len(candidates)} plausible records; expected 1"
        )

    record_offset, values = candidates[0]
    if record_offset != string_offset + len(key):
        raise ValueError(
            f"catalog record for {internal_name!r} is not adjacent to its key; refusing format guess"
        )
    hash_id, bundle_name_id, crc, bundle_size, common_id = values
    return {
        "catalog_key": key.decode("utf-8"),
        "catalog_string_offset": string_offset,
        "record_offset": record_offset,
        "crc_offset": record_offset + 8,
        "hash_id": hash_id,
        "bundle_name_id": bundle_name_id,
        "original_crc": crc,
        "catalog_bundle_size": bundle_size,
        "external_bundle_size": external_size,
        "size_matches_catalog": external_size == bundle_size,
        "common_id": common_id,
    }


def expected_byte_changes(source: bytes, entries: list[dict[str, Any]], new_crc: int) -> set[int]:
    expected: set[int] = set()
    replacement = struct.pack("<I", new_crc)
    for entry in entries:
        start = int(entry["crc_offset"])
        for index, value in enumerate(replacement):
            if source[start + index] != value:
                expected.add(start + index)
    return expected


def write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", help="untouched source binary catalog")
    parser.add_argument("--bundle", action="append", default=[], help="candidate bundle (repeatable)")
    parser.add_argument("--bundle-list", action="append", default=[], help="text or JSON bundle list")
    parser.add_argument(
        "--candidate-root", action="append", default=[], help="recursively include *.bundle files"
    )
    parser.add_argument("--game-root", help="game root; inferred from a *_Data ancestor when omitted")
    parser.add_argument("--out-dir", required=True, help="game-external output directory")
    parser.add_argument("--new-crc", type=parse_u32, default=0, help="replacement uint32 CRC (default: 0)")
    parser.add_argument("--write", action="store_true", help="write an external catalog candidate")
    parser.add_argument("--force", action="store_true", help="replace existing report/candidate files")
    args = parser.parse_args()

    try:
        catalog_path = Path(args.catalog).resolve()
        if not catalog_path.is_file():
            raise FileNotFoundError(f"catalog not found: {catalog_path}")
        game_root = Path(args.game_root).resolve() if args.game_root else infer_game_root(catalog_path)
        if game_root is None:
            raise ValueError("cannot infer game root from catalog path; pass --game-root")
        if not game_root.is_dir():
            raise FileNotFoundError(f"game root not found: {game_root}")
        if not is_within(catalog_path, game_root):
            raise ValueError(f"catalog is outside --game-root: {catalog_path}")

        out_dir = Path(args.out_dir).resolve()
        if is_within(out_dir, game_root):
            raise ValueError(f"--out-dir must be outside the game root: {game_root}")

        bundles = collect_bundles(args)
        source = catalog_path.read_bytes()
        entries: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        seen_crc_offsets: set[int] = set()
        for bundle in bundles:
            internal_name = read_internal_bundle_name(bundle)
            folded = internal_name.casefold()
            if folded in seen_names:
                raise ValueError(f"duplicate AssetBundle.m_Name in input: {internal_name!r}")
            seen_names.add(folded)
            located = locate_record(source, internal_name, bundle.stat().st_size)
            crc_offset = int(located["crc_offset"])
            if crc_offset in seen_crc_offsets:
                raise ValueError(f"multiple input bundles resolve to CRC offset {crc_offset}")
            seen_crc_offsets.add(crc_offset)
            entries.append(
                {
                    "external_file": str(bundle),
                    "external_sha256": sha256(bundle),
                    "internal_name": internal_name,
                    "new_crc": args.new_crc,
                    **located,
                }
            )

        report_name = "catalog_crc_write_report.json" if args.write else "catalog_crc_dry_run.json"
        report_path = out_dir / report_name
        candidate_path = out_dir / catalog_path.name
        outputs = [report_path] + ([candidate_path] if args.write else [])
        conflicts = [str(path) for path in outputs if path.exists()]
        if conflicts and not args.force:
            raise FileExistsError("output already exists; pass --force to replace: " + ", ".join(conflicts))

        hash_path = catalog_path.with_name("catalog.hash")
        report: dict[str, Any] = {
            "format": "addressables-binary-catalog-crc-patch-v1",
            "mode": "write-candidate" if args.write else "dry-run",
            "source_catalog": str(catalog_path),
            "source_catalog_sha256": sha256(catalog_path),
            "game_root": str(game_root),
            "new_crc": args.new_crc,
            "target_count": len(entries),
            "entries": entries,
            "catalog_hash": {
                "path": str(hash_path),
                "exists": hash_path.is_file(),
                "modified": False,
                "policy": "Never guessed or changed by this tool.",
            },
            "bundle_size_modified": False,
        }

        out_dir.mkdir(parents=True, exist_ok=True)
        if args.write:
            candidate = bytearray(source)
            for entry in entries:
                struct.pack_into("<I", candidate, int(entry["crc_offset"]), args.new_crc)
            candidate_bytes = bytes(candidate)
            expected = expected_byte_changes(source, entries, args.new_crc)
            actual = {index for index, pair in enumerate(zip(source, candidate_bytes)) if pair[0] != pair[1]}
            if len(candidate_bytes) != len(source) or actual != expected:
                raise RuntimeError(
                    f"byte-diff verification failed: expected {sorted(expected)}, got {sorted(actual)}"
                )
            write_atomic(candidate_path, candidate_bytes)
            reopened = candidate_path.read_bytes()
            reopened_diff = {index for index, pair in enumerate(zip(source, reopened)) if pair[0] != pair[1]}
            if len(reopened) != len(source) or reopened_diff != expected:
                raise RuntimeError("reopened candidate failed byte-diff verification")
            for entry in entries:
                value = struct.unpack_from("<I", reopened, int(entry["crc_offset"]))[0]
                if value != args.new_crc:
                    raise RuntimeError(f"CRC reopen verification failed at {entry['crc_offset']}")
            report.update(
                {
                    "candidate_catalog": str(candidate_path),
                    "candidate_catalog_sha256": sha256(candidate_path),
                    "candidate_size": len(reopened),
                    "changed_byte_offsets": sorted(expected),
                    "changed_byte_count": len(expected),
                    "changed_crc_record_count": sum(
                        int(entry["original_crc"]) != args.new_crc for entry in entries
                    ),
                    "byte_diff_verified": True,
                }
            )

        report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        write_atomic(report_path, report_text.encode("utf-8"))
        print_json(
            {
                "ok": True,
                "mode": report["mode"],
                "targets": len(entries),
                "report": str(report_path),
                "candidate": report.get("candidate_catalog"),
            }
        )
        return 0
    except Exception as exc:
        print_json({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
