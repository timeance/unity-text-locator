#!/usr/bin/env python3
"""Write approved raw MonoBehaviour UTF-8 rows to an external Unity candidate."""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import re
import struct
import tempfile
from collections import defaultdict
from pathlib import Path

import UnityPy  # type: ignore


PLACEHOLDER_RE = re.compile(r"(<[^>]+>|\\\\|\\[Nnrt]|\{[^{}]+\}|%[sdif]|\$[A-Za-z0-9_]+|\[[A-Za-z0-9_:-]+\])")
TAG_RE = re.compile(r"<[^>]*>")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_column(path: Path, header: str) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if rows and [cell.lstrip("\ufeff") for cell in rows[0]] == [header]:
        rows = rows[1:]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain exactly one {header} column")
    return [row[0] for row in rows]


def read_translation(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if rows and [cell.lstrip("\ufeff") for cell in rows[0]] == ["zh_cn"]:
        rows = rows[1:]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain exactly one zh_cn column")
    return [row[0] for row in rows]


def decode_text(value: str) -> str:
    result: list[str] = []
    index = 0
    escapes = {"\\": "\\", "N": "\r\n", "n": "\n", "r": "\r", "t": "\t"}
    while index < len(value):
        if value[index] != "\\":
            result.append(value[index])
            index += 1
            continue
        if index + 1 >= len(value) or value[index + 1] not in escapes:
            raise ValueError(f"unsupported escape at character {index}")
        result.append(escapes[value[index + 1]])
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-csv", type=Path, required=True)
    parser.add_argument("--translation-csv", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out-asset", type=Path)
    parser.add_argument("--allow-object-resize", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("format") != "unity-mono-raw-u32le-utf8-v1":
        raise SystemExit("unsupported manifest format")
    if not manifest.get("coverage_complete"):
        raise SystemExit("manifest coverage is incomplete; review every audit occurrence first")
    source = Path(manifest["asset"])
    source_bytes = source.read_bytes()
    if sha256_bytes(source_bytes) != manifest.get("asset_sha256"):
        raise SystemExit("source asset SHA256 changed since extraction")
    source_rows = read_column(args.source_csv, "original_flat")
    translations = read_translation(args.translation_csv)
    rows = manifest.get("rows", [])
    if len(source_rows) != len(rows) or len(translations) != len(rows):
        raise SystemExit("source, translation, and manifest row counts differ")

    env = UnityPy.load(str(source))
    objects = {obj.path_id: obj for obj in env.objects}
    all_source_raw_sha = {path_id: sha256_bytes(obj.get_raw_data()) for path_id, obj in objects.items()}
    grouped: dict[int, list[tuple[dict[str, object], str]]] = defaultdict(list)
    for row, original_flat, translated_flat in zip(rows, source_rows, translations):
        text_row = int(row["text_row"])
        if original_flat != row["original_flat"]:
            raise SystemExit(f"manifest/source mismatch at text row {text_row}")
        if not translated_flat.strip():
            raise SystemExit(f"blank translation at approved text row {text_row}")
        if sorted(PLACEHOLDER_RE.findall(original_flat)) != sorted(PLACEHOLDER_RE.findall(translated_flat)):
            raise SystemExit(f"placeholder or escape mismatch at text row {text_row}")
        if sorted(TAG_RE.findall(original_flat)) != sorted(TAG_RE.findall(translated_flat)):
            raise SystemExit(f"tag mismatch at text row {text_row}")
        try:
            translated = decode_text(translated_flat)
        except ValueError as exc:
            raise SystemExit(f"text row {text_row}: {exc}") from exc
        if newline_events(translated) != row.get("newline_events", []):
            raise SystemExit(f"ordered newline events changed at text row {text_row}")
        grouped[int(row["path_id"])].append((row, translated))

    applied: list[dict[str, object]] = []
    resized_rows: list[int] = []
    translated_payloads: dict[int, list[bytes]] = defaultdict(list)
    for path_id, selected in grouped.items():
        obj = objects.get(path_id)
        if obj is None:
            raise SystemExit(f"MonoBehaviour PathID {path_id} is missing")
        raw = bytearray(obj.get_raw_data())
        expected_sha = str(selected[0][0]["object_raw_sha256"])
        if sha256_bytes(raw) != expected_sha:
            raise SystemExit(f"object raw SHA256 changed for PathID {path_id}")
        delta = 0
        for row, translated in sorted(selected, key=lambda item: int(item[0]["prefix_offset"])):
            text_row = int(row["text_row"])
            offset = int(row["prefix_offset"]) + delta
            old_length = struct.unpack_from("<I", raw, offset)[0]
            if old_length != int(row["byte_length"]):
                raise SystemExit(f"byte length changed at text row {text_row}")
            start = offset + 4
            end = start + old_length
            pad_end = (end + 3) & ~3
            old_payload = bytes(raw[start:end])
            if sha256_bytes(old_payload) != row["original_utf8_sha256"]:
                raise SystemExit(f"source payload changed at text row {text_row}")
            payload = translated.encode("utf-8")
            new_end = start + len(payload)
            new_pad_end = (new_end + 3) & ~3
            old_span = pad_end - offset
            new_span = new_pad_end - offset
            if old_span != new_span:
                resized_rows.append(text_row)
                if not args.allow_object_resize:
                    raise SystemExit(
                        f"text row {text_row} changes aligned span {old_span}->{new_span}; "
                        "rerun with --allow-object-resize for a canary-only candidate"
                    )
            segment = struct.pack("<I", len(payload)) + payload + b"\0" * (new_pad_end - new_end)
            raw[offset:pad_end] = segment
            delta += len(segment) - old_span
            translated_payloads[path_id].append(payload)
            applied.append(
                {
                    "text_row": text_row,
                    "path_id": path_id,
                    "old_bytes": old_length,
                    "new_bytes": len(payload),
                    "aligned_span_delta": new_span - old_span,
                }
            )
        obj.set_raw_data(bytes(raw))

    if args.write and args.out_asset is None:
        raise SystemExit("--out-asset is required with --write")
    if args.out_asset and args.out_asset.resolve() == source.resolve():
        raise SystemExit("in-place raw MonoBehaviour writeback is forbidden")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.write:
        candidate = args.out_asset.resolve()
    else:
        temporary = tempfile.TemporaryDirectory(prefix="unity-mono-raw-", ignore_cleanup_errors=True)
        candidate = Path(temporary.name) / source.name
    candidate.parent.mkdir(parents=True, exist_ok=True)
    asset_file = next(iter(env.files.values()))
    candidate.write_bytes(asset_file.save())

    reopened = UnityPy.load(str(candidate))
    reopened_objects = {obj.path_id: obj for obj in reopened.objects}
    verification_failures: list[dict[str, object]] = []
    for path_id, expected_sha in all_source_raw_sha.items():
        reopened_obj = reopened_objects.get(path_id)
        if reopened_obj is None:
            verification_failures.append({"path_id": path_id, "reason": "object missing"})
            continue
        reopened_raw = reopened_obj.get_raw_data()
        if path_id not in grouped and sha256_bytes(reopened_raw) != expected_sha:
            verification_failures.append({"path_id": path_id, "reason": "untargeted object changed"})
        for payload in translated_payloads.get(path_id, []):
            if payload not in reopened_raw:
                verification_failures.append({"path_id": path_id, "reason": "translation payload missing"})

    candidate_bytes = candidate.read_bytes()
    ratio = len(candidate_bytes) / max(len(source_bytes), 1)
    result = {
        "dry_run": not args.write,
        "source_asset": str(source),
        "source_sha256": sha256_bytes(source_bytes),
        "candidate_asset": str(candidate) if args.write else None,
        "candidate_sha256": sha256_bytes(candidate_bytes),
        "source_size": len(source_bytes),
        "candidate_size": len(candidate_bytes),
        "size_ratio": ratio,
        "size_amplification_warning": ratio > 1.5 or len(candidate_bytes) - len(source_bytes) > 1024 * 1024,
        "applied_rows": len(applied),
        "resized_rows": resized_rows,
        "allow_object_resize": args.allow_object_resize,
        "verification_failures": verification_failures,
        "reopen_verified": not verification_failures,
        "runtime_canary_required": bool(resized_rows) or ratio > 1.5,
        "applied": applied,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if temporary is not None:
        del reopened_objects
        del reopened
        gc.collect()
        temporary.cleanup()
    return 1 if verification_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
