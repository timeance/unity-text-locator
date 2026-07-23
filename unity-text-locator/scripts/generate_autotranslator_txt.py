#!/usr/bin/env python3
"""Generate an auditable XUnity.AutoTranslator text candidate from row-mapped CSVs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Sequence


FORMAT = "unity-text-locator-autotranslator-v1"
OUTPUT_NAME = "_PreTranslated.txt"
REPORT_NAME = "autotranslator_export_report.json"


class InputError(ValueError):
    """Raised when an input file does not satisfy the one-column contract."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rows_sha256(rows: list[str]) -> str:
    digest = hashlib.sha256()
    for value in rows:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def read_column(path: Path, header: str) -> list[str]:
    if not path.is_file():
        raise InputError(f"input file does not exist: {path}")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(f"{path} must be UTF-8 or UTF-8 with BOM") from exc

    rows = [row if row else [""] for row in csv.reader(io.StringIO(text, newline=""))]
    if not rows:
        raise InputError(f"{path} is empty")
    actual_header = [cell.lstrip("\ufeff") for cell in rows[0]]
    if actual_header != [header]:
        raise InputError(f"{path} must contain exactly one {header} column")
    body = rows[1:]
    if any(len(row) != 1 for row in body):
        raise InputError(f"{path} must contain exactly one {header} column")
    return [row[0] for row in body]


def serialized_field(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def representability_issues(value: str, field: str, rows: list[int]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    if not value and field == "original_flat":
        findings.append({"type": "empty_key", "field": field, "rows": rows})
    if "=" in value:
        findings.append({"type": "ambiguous_delimiter", "field": field, "rows": rows})
    if field == "original_flat" and value.lstrip().startswith("//"):
        findings.append({"type": "comment_prefixed_key", "field": field, "rows": rows})
    for character, name in (
        ("\x00", "nul_character"),
        ("\ufeff", "embedded_bom"),
        ("\u2028", "unicode_line_separator"),
        ("\u2029", "unicode_paragraph_separator"),
    ):
        if character in value:
            findings.append({"type": name, "field": field, "rows": rows})
    return findings


def analyze_rows(source_rows: list[str], translation_rows: list[str]) -> tuple[list[tuple[str, str]], dict[str, object]]:
    report: dict[str, object] = {
        "source_rows": len(source_rows),
        "translation_rows": len(translation_rows),
        "selected_rows": sum(bool(value.strip()) for value in translation_rows),
        "blank_rows": sum(not value.strip() for value in translation_rows),
        "identical_rows_skipped": 0,
        "exported_entries": 0,
        "deduplicated_occurrences": 0,
        "duplicate_groups": [],
        "conflicts": [],
        "unrepresentable": [],
        "warnings": [],
    }
    if len(source_rows) != len(translation_rows):
        report["conflicts"] = [
            {
                "type": "row_count_mismatch",
                "source_rows": len(source_rows),
                "translation_rows": len(translation_rows),
            }
        ]
        return [], report

    grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for csv_row, (source, translation) in enumerate(zip(source_rows, translation_rows), start=2):
        grouped[source].append((csv_row, translation))

    pairs: list[tuple[str, str]] = []
    serialized_keys: dict[str, tuple[str, list[int]]] = {}
    conflicts = report["conflicts"]
    unrepresentable = report["unrepresentable"]
    duplicate_groups = report["duplicate_groups"]
    warnings = report["warnings"]
    assert isinstance(conflicts, list)
    assert isinstance(unrepresentable, list)
    assert isinstance(duplicate_groups, list)
    assert isinstance(warnings, list)

    for source, occurrences in grouped.items():
        rows = [row for row, _translation in occurrences]
        blank_rows = [row for row, translation in occurrences if not translation.strip()]
        values = {translation for _row, translation in occurrences if translation.strip()}

        if blank_rows and values:
            conflicts.append(
                {
                    "type": "mixed_selected_and_blank_occurrences",
                    "original_flat": source,
                    "rows": rows,
                    "blank_rows": blank_rows,
                }
            )
            continue
        if len(values) > 1:
            conflicts.append(
                {
                    "type": "conflicting_translations_for_key",
                    "original_flat": source,
                    "rows": rows,
                    "translations": sorted(values),
                }
            )
            continue
        if not values:
            continue

        translation = next(iter(values))
        if translation == source:
            report["identical_rows_skipped"] = int(report["identical_rows_skipped"]) + len(occurrences)
            continue

        findings = representability_issues(source, "original_flat", rows)
        findings.extend(representability_issues(translation, "zh_cn", rows))
        if findings:
            unrepresentable.extend(findings)
            continue

        serialized_key = serialized_field(source)
        previous = serialized_keys.get(serialized_key)
        if previous is not None and previous[0] != source:
            conflicts.append(
                {
                    "type": "serialized_key_collision",
                    "serialized_key": serialized_key,
                    "originals": [previous[0], source],
                    "rows": [*previous[1], *rows],
                }
            )
            continue
        serialized_keys[serialized_key] = (source, rows)

        if len(occurrences) > 1:
            duplicate_groups.append({"original_flat": source, "rows": rows, "translation": translation})
            report["deduplicated_occurrences"] = int(report["deduplicated_occurrences"]) + len(occurrences) - 1
        if source != source.strip():
            warnings.append({"type": "key_has_edge_whitespace", "rows": rows})
        pairs.append((serialized_key, serialized_field(translation)))

    report["exported_entries"] = len(pairs)
    return pairs, report


def render_candidate(pairs: list[tuple[str, str]], source_hash: str, translation_hash: str) -> bytes:
    lines = [
        "// Generated by unity-text-locator. Runtime verification required.",
        f"// Source CSV SHA256: {source_hash}",
        f"// Translation CSV SHA256: {translation_hash}",
        f"// Entries: {len(pairs)}",
        "",
    ]
    lines.extend(f"{source}={translation}" for source, translation in pairs)
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_report(path: Path, report: dict[str, object]) -> None:
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(path, payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a reviewed XUnity.AutoTranslator _PreTranslated.txt candidate."
    )
    parser.add_argument("--source-csv", required=True, type=Path, help="One-column original_flat CSV")
    parser.add_argument("--translation-csv", required=True, type=Path, help="Validated one-column zh_cn CSV")
    parser.add_argument("--out-dir", required=True, type=Path, help="Candidate/report directory; never a plugin directory")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write _PreTranslated.txt; without this flag only the JSON audit report is written",
    )
    args = parser.parse_args(argv)

    try:
        source_rows = read_column(args.source_csv, "original_flat")
        translation_rows = read_column(args.translation_csv, "zh_cn")
    except InputError as exc:
        parser.error(str(exc))

    source_hash = file_sha256(args.source_csv)
    translation_hash = file_sha256(args.translation_csv)
    pairs, analysis = analyze_rows(source_rows, translation_rows)
    conflicts = analysis["conflicts"]
    unrepresentable = analysis["unrepresentable"]
    assert isinstance(conflicts, list)
    assert isinstance(unrepresentable, list)
    blocked = bool(conflicts or unrepresentable)

    output_path = args.out_dir / OUTPUT_NAME
    report_path = args.out_dir / REPORT_NAME
    output_existed_before = output_path.is_file()
    existing_output_hash = file_sha256(output_path) if output_existed_before else None
    candidate = None if blocked else render_candidate(pairs, source_hash, translation_hash)

    report: dict[str, object] = {
        "format": FORMAT,
        "status": "blocked" if blocked else ("written" if args.write else "ready"),
        "source_csv": str(args.source_csv.resolve()),
        "translation_csv": str(args.translation_csv.resolve()),
        "source_csv_sha256": source_hash,
        "translation_csv_sha256": translation_hash,
        "source_rows_sha256": rows_sha256(source_rows),
        "translation_rows_sha256": rows_sha256(translation_rows),
        "output_txt": str(output_path.resolve()),
        "output_written": False,
        "output_existed_before": output_existed_before,
        "existing_output_sha256": existing_output_hash,
        "candidate_output_sha256": bytes_sha256(candidate) if candidate is not None else None,
        "encoding": "utf-8-sig",
        "runtime_verification_required": True,
        "key_semantics": "global text-value match; per-occurrence selection is not preserved",
        **analysis,
    }

    if args.write and not blocked:
        assert candidate is not None
        atomic_write(output_path, candidate)
        report["output_written"] = True
    write_report(report_path, report)

    summary = {
        "status": report["status"],
        "report": str(report_path),
        "output": str(output_path) if report["output_written"] else None,
        "source_rows": report["source_rows"],
        "selected_rows": report["selected_rows"],
        "exported_entries": report["exported_entries"],
        "blocking_issues": len(conflicts) + len(unrepresentable),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
