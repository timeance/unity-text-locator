#!/usr/bin/env python3
"""Validate a one-column Unity translation file before row-mapped writeback."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from pathlib import Path


ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "cp936", "shift_jis", "cp932"]
PLACEHOLDER_RE = re.compile(r"(<[^>]+>|\\[nrt]|\{[^{}]+\}|%[sdif]|\$[A-Za-z0-9_]+|\[[A-Za-z0-9_:-]+\])")
TAG_RE = re.compile(r"<[^>]*>")
JP_KANA_RE = re.compile(r"[\u3040-\u3096\u30a1-\u30fa\uff66-\uff9d]")


def decode_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def csv_rows(text: str) -> list[list[str]]:
    return [row if row else [""] for row in csv.reader(io.StringIO(text, newline=""))]


def read_source(path: Path) -> list[str]:
    text, _encoding = decode_text(path)
    rows = csv_rows(text)
    if rows and [cell.lstrip("\ufeff") for cell in rows[0]] == ["original_flat"]:
        rows = rows[1:]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one original_flat column")
    return [row[0] for row in rows]


def read_translation(path: Path) -> tuple[list[str], list[str] | None, str, list[dict]]:
    text, encoding = decode_text(path)
    rows = csv_rows(text)
    warnings: list[dict] = []
    if not rows:
        return [], None, encoding, warnings
    header = [cell.lstrip("\ufeff") for cell in rows[0]]
    if header != rows[0]:
        warnings.append({"type": "embedded_bom_stripped_from_header", "fields": rows[0]})
    if header == ["zh_cn"]:
        rows = rows[1:]
        mode = "one-column zh_cn"
    elif header == ["original_flat"]:
        rows = rows[1:]
        mode = "one-column translated text with legacy header"
        warnings.append({"type": "legacy_translation_header", "header": "original_flat"})
    elif header == ["original_flat", "zh_cn"]:
        body = rows[1:]
        if any(len(row) != 2 for row in body):
            raise SystemExit(f"{path} has malformed legacy original_flat,zh_cn rows")
        return [row[1] for row in body], [row[0] for row in body], encoding, warnings
    else:
        mode = "one-column headerless zh_cn"
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one zh_cn column")
    warnings.append({"type": "translation_input_mode", "mode": mode})
    return [row[0] for row in rows], None, encoding, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-csv", required=True, type=Path, help="One-column original_flat CSV")
    parser.add_argument("--translation-csv", required=True, type=Path, help="One-column zh_cn CSV, normally named *_translation.csv")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--allow-newline-changes", action="store_true", help="Downgrade literal newline-count changes from errors to warnings")
    args = parser.parse_args()

    out_dir = args.out_dir or args.translation_csv.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    source_rows = read_source(args.source_csv)
    translations, legacy_originals, encoding, initial_warnings = read_translation(args.translation_csv)
    issues: list[dict] = []
    warnings: list[dict] = list(initial_warnings)

    if len(source_rows) != len(translations):
        issues.append({"type": "row_count_mismatch", "source_rows": len(source_rows), "translation_rows": len(translations)})
    if encoding.lower() not in {"utf-8", "utf-8-sig"}:
        warnings.append({"type": "non_utf8_input", "encoding": encoding})

    for index, (source, zh) in enumerate(zip(source_rows, translations), start=1):
        csv_row = index + 1
        if legacy_originals is not None and legacy_originals[index - 1] != source:
            issues.append(
                {
                    "type": "original_flat_mismatch",
                    "row": csv_row,
                    "source_original": source,
                    "current_original": legacy_originals[index - 1],
                }
            )
        if not zh.strip():
            warnings.append({"type": "empty_translation", "row": csv_row, "original_flat": source})
            continue
        source_tokens = sorted(PLACEHOLDER_RE.findall(source))
        translation_tokens = sorted(PLACEHOLDER_RE.findall(zh))
        if source_tokens != translation_tokens:
            issues.append({"type": "placeholder_mismatch", "row": csv_row, "source_tokens": source_tokens, "translation_tokens": translation_tokens})
        source_tags = sorted(TAG_RE.findall(source))
        translation_tags = sorted(TAG_RE.findall(zh))
        if source_tags != translation_tags:
            issues.append({"type": "tag_mismatch", "row": csv_row, "source_tags": source_tags, "translation_tags": translation_tags})
        if source.count("\\n") != zh.count("\\n"):
            finding = {"type": "newline_count_mismatch", "row": csv_row, "source_newlines": source.count("\\n"), "translation_newlines": zh.count("\\n")}
            (warnings if args.allow_newline_changes else issues).append(finding)
        if zh == source:
            warnings.append({"type": "identical_translation", "row": csv_row, "text": zh})
        if JP_KANA_RE.search(TAG_RE.sub("", zh)):
            warnings.append({"type": "translation_contains_kana", "row": csv_row, "text": zh})

    normalized_path = out_dir / (args.translation_csv.stem + "_utf8.csv")
    with normalized_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["zh_cn"])
        writer.writerows([[translation] for translation in translations])

    report = {
        "source_csv": str(args.source_csv),
        "translation_csv": str(args.translation_csv),
        "normalized_csv": str(normalized_path),
        "encoding": encoding,
        "source_rows": len(source_rows),
        "translation_rows": len(translations),
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
    }
    (out_dir / "translation_validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Translation CSV Validation",
        "",
        f"- Source rows: {len(source_rows)}",
        f"- Translation rows: {len(translations)}",
        f"- Normalized translation CSV: `{normalized_path}`",
        f"- Blocking issues: {len(issues)}",
        f"- Warnings: {len(warnings)}",
        "",
        "## Blocking Issues",
    ]
    lines.extend(f"- {finding}" for finding in issues[:100]) if issues else lines.append("- None")
    lines.append("")
    lines.append("## Warnings")
    lines.extend(f"- {finding}" for finding in warnings[:100]) if warnings else lines.append("- None")
    (out_dir / "translation_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["normalized_csv", "encoding", "source_rows", "translation_rows", "issue_count", "warning_count"]}, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
