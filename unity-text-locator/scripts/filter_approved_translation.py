#!/usr/bin/env python3
"""Blank row-addressable blocking translations and retain approved rows."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def read_column(path: Path, accepted_headers: set[str]) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if rows and len(rows[0]) == 1 and rows[0][0].lstrip("\ufeff") in accepted_headers:
        rows = rows[1:]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain exactly one text column")
    return [row[0] for row in rows]


def write_translation(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["zh_cn"])
        writer.writerows([[value] for value in values])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a writeback-ready translation by blanking rows with validation blocking issues."
    )
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--translation-csv", required=True, type=Path)
    parser.add_argument("--validation-report", required=True, type=Path)
    parser.add_argument("--out-csv", required=True, type=Path)
    parser.add_argument("--report-out", required=True, type=Path)
    args = parser.parse_args()

    source = read_column(args.source_csv, {"original_flat"})
    translations = read_column(args.translation_csv, {"zh_cn", "original_flat"})
    report = json.loads(args.validation_report.read_text(encoding="utf-8"))
    if len(source) != len(translations):
        raise SystemExit("Cannot filter a translation whose row count differs from its source")
    if report.get("source_rows") != len(source) or report.get("translation_rows") != len(translations):
        raise SystemExit("Validation report does not match the supplied CSV row counts")

    blocking = report.get("issues", [])
    unfilterable = [issue for issue in blocking if "row" not in issue]
    if unfilterable:
        raise SystemExit("Validation contains non-row-addressable blocking issues; repair input before filtering")
    blocked_indexes = sorted({int(issue["row"]) - 2 for issue in blocking})
    if any(index < 0 or index >= len(translations) for index in blocked_indexes):
        raise SystemExit("Validation issue row falls outside the supplied translation")

    output = list(translations)
    for index in blocked_indexes:
        output[index] = ""
    write_translation(args.out_csv, output)
    issue_types = Counter(str(issue.get("type", "unknown")) for issue in blocking)
    result = {
        "source_csv": str(args.source_csv),
        "translation_csv": str(args.translation_csv),
        "validation_report": str(args.validation_report),
        "output_csv": str(args.out_csv),
        "rows": len(output),
        "blocking_findings": len(blocking),
        "blanked_rows": len(blocked_indexes),
        "blocking_types": dict(sorted(issue_types.items())),
        "selected_translation_rows": sum(
            bool(translation.strip()) and translation != original
            for original, translation in zip(source, output)
        ),
        "policy": "Rows with blocking validation findings are blank and retain source text on writeback.",
    }
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
