#!/usr/bin/env python3
"""Create a conservative translator-facing CSV from a broad Unity text extract."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath


PATH_RE = re.compile(
    r"(?:^|[\\/])(?:Assets|StreamingAssets|Resources)[\\/]|"
    r"\.(?:asset|prefab|png|jpg|ogg|wav|mp3|bytes|bundle)(?:$|[\\/])",
    re.IGNORECASE,
)
CONTROL_TOKENS = (
    "ParamTbl[",
    "SystemTbl[",
    "JumpScenario",
    "ChangeScenario",
    "SetParam",
)


def read_rows(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "original_flat" not in (reader.fieldnames or []):
            raise SystemExit(f"{path} must contain original_flat")
        return [row.get("original_flat", "") for row in reader]


def sources_by_text(path: Path | None) -> dict[str, set[str]]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, set[str]] = defaultdict(set)
    for occ in data.get("occurrences", []):
        result[occ.get("original_flat", "")].add(occ.get("source_file", ""))
    return result


def is_global_game_manager_source(sources: set[str]) -> bool:
    return any(PurePosixPath(source.replace("\\", "/")).name.lower() == "globalgamemanagers" for source in sources)


def classify(text: str, sources: set[str]) -> tuple[str, str]:
    stripped = text.lstrip("\ufeff")
    if stripped.startswith("Key Japanese English\\n"):
        return "exclude", "structured localization table; translate by column only"
    if stripped.startswith("*"):
        return "exclude", "scenario label or control target"
    if any(token in stripped for token in CONTROL_TOKENS):
        return "exclude", "script command or parameter expression"
    if PATH_RE.search(stripped):
        return "exclude", "asset or resource path"
    if is_global_game_manager_source(sources):
        return "review", "globalgamemanagers identity/config string; changing product name changes save path"
    return "keep", ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path, help="extracted_text_filtered.csv")
    parser.add_argument("--manifest", type=Path, default=None, help="extraction_manifest.json")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--exclude-from-text",
        action="append",
        default=[],
        metavar="TEXT",
        help="Exclude TEXT and all later rows after manually verifying a trailing non-game/sample block.",
    )
    args = parser.parse_args()

    out_dir = args.out_dir or args.csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_rows(args.csv_path)
    sources = sources_by_text(args.manifest)

    boundary: int | None = None
    for marker in args.exclude_from_text:
        try:
            index = rows.index(marker)
        except ValueError as exc:
            raise SystemExit(f"--exclude-from-text marker was not found: {marker!r}") from exc
        boundary = index if boundary is None else min(boundary, index)

    safe: list[str] = []
    omitted: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    for index, text in enumerate(rows, start=1):
        if boundary is not None and index - 1 >= boundary:
            decision, reason = "exclude", "manually marked trailing non-game/sample block"
        else:
            decision, reason = classify(text, sources.get(text, set()))
        if decision == "keep":
            safe.append(text)
            continue
        omitted.append(
            {
                "original_row": str(index),
                "decision": decision,
                "reason": reason,
                "original_flat": text,
            }
        )
        counts[f"{decision}: {reason}"] += 1

    safe_path = out_dir / "extracted_text_visible_safe.csv"
    omitted_path = out_dir / "extracted_text_visible_safe_omitted.csv"
    report_path = out_dir / "visible_safe_audit_report_zh.md"
    with safe_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["original_flat"], lineterminator="\n")
        writer.writeheader()
        writer.writerows({"original_flat": text} for text in safe)
    with omitted_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["original_row", "decision", "reason", "original_flat"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(omitted)

    report = [
        "# Visible-Safe Text Audit",
        "",
        f"- Input CSV: `{args.csv_path}`",
        f"- Input rows: {len(rows)}",
        f"- Safe rows: {len(safe)}",
        f"- Omitted/review rows: {len(omitted)}",
        f"- Safe CSV: `{safe_path}`",
        f"- Omitted detail: `{omitted_path}`",
        "",
        "## Decisions",
        "",
        "- `exclude`: structural/control/sample/resource records not suitable for ordinary translation.",
        "- `review`: possible display text withheld because it may alter runtime identity or configuration.",
        "- Strings from `globalgamemanagers` are withheld by default because a translated product name changes the LocalLow save/log namespace.",
        "",
        "## Counts",
        "",
    ]
    report.extend(f"- {reason}: {count}" for reason, count in counts.most_common())
    if boundary is not None:
        report.extend(
            [
                "",
                "## Manual Boundary",
                "",
                f"- Rows from source row {boundary + 1} onward were excluded by `--exclude-from-text`.",
            ]
        )
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "safe_csv": str(safe_path),
                "omitted_csv": str(omitted_path),
                "report": str(report_path),
                "safe": len(safe),
                "omitted": len(omitted),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
