#!/usr/bin/env python3
"""Project a legacy normalized translation CSV onto a narrower approved source CSV.

This helper matches rows by original_flat text, so it is only safe for
legacy/audit-only subset work where duplicate originals are reviewed.
Row-mapped writeback should use per-source manifests instead.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def read_source(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "original_flat" not in (reader.fieldnames or []):
            raise SystemExit(f"{path} must contain original_flat")
        return [row.get("original_flat", "") for row in reader]


def read_translation(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = [field.lstrip("\ufeff") for field in (reader.fieldnames or [])]
        if fields != ["original_flat", "zh_cn"]:
            raise SystemExit(f"{path} must contain exactly original_flat,zh_cn after validation")
        return [
            {
                (key.lstrip("\ufeff") if key else key): value
                for key, value in row.items()
            }
            for row in reader
        ]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Keep only translations that belong to a final approved runtime-safe source list."
    )
    parser.add_argument("--translation-csv", required=True, type=Path)
    parser.add_argument("--target-source-csv", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    translations = read_translation(args.translation_csv)
    targets = read_source(args.target_source_csv)
    target_set = set(targets)
    original_counts = Counter(row["original_flat"] for row in translations)
    duplicates = sorted(text for text, count in original_counts.items() if count > 1)
    by_original = {row["original_flat"]: row["zh_cn"] for row in translations}
    missing_targets = [text for text in targets if text not in by_original]

    projected = [
        {"original_flat": original, "zh_cn": by_original.get(original, "")}
        for original in targets
    ]
    excluded = [
        row
        for row in translations
        if row["original_flat"] not in target_set and row["zh_cn"].strip()
    ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    projected_path = args.out_dir / "zh_cn_projected_to_subset.csv"
    excluded_path = args.out_dir / "translated_rows_excluded_from_subset.csv"
    report_path = args.out_dir / "translation_projection_report_zh.md"
    write_csv(projected_path, ["original_flat", "zh_cn"], projected)
    write_csv(excluded_path, ["original_flat", "zh_cn"], excluded)

    report = {
        "translation_csv": str(args.translation_csv),
        "target_source_csv": str(args.target_source_csv),
        "translation_rows": len(translations),
        "target_rows": len(targets),
        "projected_nonblank": sum(bool(row["zh_cn"].strip()) for row in projected),
        "excluded_nonblank": len(excluded),
        "missing_targets": missing_targets,
        "duplicate_translation_originals": duplicates,
        "projected_csv": str(projected_path),
        "excluded_csv": str(excluded_path),
    }
    (args.out_dir / "translation_projection_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 译表子集投影报告",
        "",
        "- 注意：此工具按 `original_flat` 文本值投影，只适用于旧译表或审计子集；存在重复原文时不得作为行级写回映射。",
        f"- 原译表行数: {report['translation_rows']}",
        f"- 最终批准基线行数: {report['target_rows']}",
        f"- 投影后非空译文: {report['projected_nonblank']}",
        f"- 被排除的非空译文: {report['excluded_nonblank']}",
        f"- 目标基线中缺少译文来源的行: {len(missing_targets)}",
        f"- 原译表重复原文键: {len(duplicates)}",
        f"- 投影译表: `{projected_path}`",
        f"- 被排除译文: `{excluded_path}`",
        "",
        "写回前必须再用目标基线运行 `validate_translation_csv.py`。",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if missing_targets or duplicates else 0


if __name__ == "__main__":
    raise SystemExit(main())
