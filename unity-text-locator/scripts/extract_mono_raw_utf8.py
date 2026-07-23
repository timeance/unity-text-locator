#!/usr/bin/env python3
"""Audit aligned uint32-length UTF-8 strings inside MonoBehaviour raw data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
from pathlib import Path

import UnityPy  # type: ignore


JP_SIGNAL_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
INTERNAL_KEY_RE = re.compile(
    r"^(?:BGM|SE|SFX|AM|VOICE|VO|BG|CG|MOVIE|ANIM|SPRITE|IMAGE)[_:/.-]",
    re.IGNORECASE,
)
RESOURCE_SUFFIX_RE = re.compile(
    r"(?:画像|背景|立ち絵|音声|ボイス|\.png|\.jpe?g|\.ogg|\.wav|\.mp3|\.mp4|\.asset)$",
    re.IGNORECASE,
)
ALLOWED_ACTIONS = {"translate", "preserve"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def escape_text(text: str) -> str:
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


def classify(text: str, extra_internal: list[re.Pattern[str]]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if INTERNAL_KEY_RE.search(text):
        reasons.append("resource-key-prefix")
    if text.startswith("*"):
        reasons.append("asterisk-label")
    if RESOURCE_SUFFIX_RE.search(text):
        reasons.append("resource-name-or-extension")
    if any(pattern.search(text) for pattern in extra_internal):
        reasons.append("project-internal-regex")
    if reasons:
        return "preserve_internal", reasons
    return "suggest_visible", ["japanese-signal"]


def scan_object(raw: bytes, max_bytes: int, extra_internal: list[re.Pattern[str]]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for offset in range(0, max(0, len(raw) - 4), 4):
        byte_length = struct.unpack_from("<I", raw, offset)[0]
        if byte_length < 1 or byte_length > max_bytes:
            continue
        start = offset + 4
        end = start + byte_length
        if end > len(raw):
            continue
        payload = raw[start:end]
        if b"\0" in payload:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not JP_SIGNAL_RE.search(text):
            continue
        if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            continue
        pad_end = (end + 3) & ~3
        if pad_end > len(raw) or any(raw[end:pad_end]):
            continue
        decision, reasons = classify(text, extra_internal)
        hits.append(
            {
                "prefix_offset": offset,
                "byte_length": byte_length,
                "padded_span_length": pad_end - offset,
                "original": text,
                "original_flat": escape_text(text),
                "original_utf8_sha256": sha256_bytes(payload),
                "newline_events": newline_events(text),
                "classification": decision,
                "classification_reasons": reasons,
            }
        )
    return hits


def occurrence_id(asset_sha: str, path_id: int, offset: int, text_sha: str) -> str:
    value = f"{asset_sha}:{path_id}:{offset}:{text_sha}".encode("ascii")
    return hashlib.sha256(value).hexdigest()[:24]


def read_approvals(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    approvals: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row.get("occurrence_id", "").strip()
        if not key or key in approvals:
            raise SystemExit("approval CSV contains a blank or duplicate occurrence_id")
        approvals[key] = row
    return approvals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--path-id", type=int, action="append", default=None)
    parser.add_argument("--approval-csv", type=Path)
    parser.add_argument("--internal-regex", action="append", default=[])
    parser.add_argument("--max-bytes", type=int, default=12000)
    parser.add_argument("--description", default="mono_raw_residual_text")
    args = parser.parse_args()

    asset = args.asset.resolve()
    asset_bytes = asset.read_bytes()
    asset_sha = sha256_bytes(asset_bytes)
    requested = set(args.path_id or [])
    extra_internal = [re.compile(pattern) for pattern in args.internal_regex]
    env = UnityPy.load(str(asset))
    candidates: list[dict[str, object]] = []
    object_inventory: list[dict[str, object]] = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour" or (requested and obj.path_id not in requested):
            continue
        raw = obj.get_raw_data()
        typetree_readable = True
        try:
            obj.read_typetree()
        except Exception:
            typetree_readable = False
        if not requested and typetree_readable:
            continue
        raw_sha = sha256_bytes(raw)
        hits = scan_object(raw, args.max_bytes, extra_internal)
        object_inventory.append(
            {
                "path_id": obj.path_id,
                "raw_size": len(raw),
                "raw_sha256": raw_sha,
                "typetree_readable": typetree_readable,
                "candidate_count": len(hits),
            }
        )
        for hit in hits:
            hit.update(
                {
                    "path_id": obj.path_id,
                    "object_raw_size": len(raw),
                    "object_raw_sha256": raw_sha,
                    "occurrence_id": occurrence_id(
                        asset_sha,
                        obj.path_id,
                        int(hit["prefix_offset"]),
                        str(hit["original_utf8_sha256"]),
                    ),
                }
            )
            candidates.append(hit)

    if requested:
        found = {int(item["path_id"]) for item in object_inventory}
        missing = sorted(requested - found)
        if missing:
            raise SystemExit(f"requested MonoBehaviour PathID(s) not found: {missing}")

    approvals = read_approvals(args.approval_csv)
    known_ids = {str(item["occurrence_id"]) for item in candidates}
    unknown = sorted(set(approvals) - known_ids)
    if unknown:
        raise SystemExit(f"approval CSV has {len(unknown)} stale or unknown occurrence_id(s)")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    audit_csv = args.out_dir / "mono_raw_utf8_audit.csv"
    approval_csv = args.out_dir / "mono_raw_utf8_approval.csv"
    fieldnames = [
        "occurrence_id", "path_id", "prefix_offset", "byte_length", "classification",
        "classification_reasons", "original_flat", "action", "review_note",
    ]
    with audit_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for item in candidates:
            approval = approvals.get(str(item["occurrence_id"]), {})
            writer.writerow(
                {
                    "occurrence_id": item["occurrence_id"],
                    "path_id": item["path_id"],
                    "prefix_offset": item["prefix_offset"],
                    "byte_length": item["byte_length"],
                    "classification": item["classification"],
                    "classification_reasons": ";".join(item["classification_reasons"]),
                    "original_flat": item["original_flat"],
                    "action": approval.get("action", ""),
                    "review_note": approval.get("review_note", ""),
                }
            )
    if args.approval_csv is None:
        approval_csv.write_bytes(audit_csv.read_bytes())

    rows: list[dict[str, object]] = []
    unresolved: list[str] = []
    if args.approval_csv:
        for item in candidates:
            approval = approvals.get(str(item["occurrence_id"]))
            action = approval.get("action", "").strip().lower() if approval else ""
            if action not in ALLOWED_ACTIONS:
                unresolved.append(str(item["occurrence_id"]))
                continue
            item["action"] = action
            item["review_note"] = approval.get("review_note", "") if approval else ""
            if action == "translate":
                item["text_row"] = len(rows) + 1
                rows.append(item)

    stem = args.description
    source_csv = args.out_dir / f"{stem}.csv"
    manifest_path = args.out_dir / f"{stem}.manifest.json"
    if args.approval_csv and not unresolved:
        with source_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["original_flat"], lineterminator="\n")
            writer.writeheader()
            writer.writerows({"original_flat": row["original_flat"]} for row in rows)

    manifest = {
        "format": "unity-mono-raw-u32le-utf8-v1",
        "text_escape": "backslash-controls-exact-v1",
        "asset": str(asset),
        "asset_sha256": asset_sha,
        "asset_size": len(asset_bytes),
        "objects": object_inventory,
        "candidate_count": len(candidates),
        "coverage_complete": bool(args.approval_csv) and not unresolved,
        "unresolved_occurrence_ids": unresolved,
        "rows": rows,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "asset": str(asset),
        "candidates": len(candidates),
        "suggest_visible": sum(item["classification"] == "suggest_visible" for item in candidates),
        "preserve_internal": sum(item["classification"] == "preserve_internal" for item in candidates),
        "translate_rows": len(rows),
        "unresolved": len(unresolved),
        "coverage_complete": manifest["coverage_complete"],
        "audit_csv": str(audit_csv),
        "approval_csv": str(approval_csv),
        "manifest": str(manifest_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if args.approval_csv and unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
