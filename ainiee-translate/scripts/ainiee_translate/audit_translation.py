"""Audit source-aware translation invariants and emit a repair manifest."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(
    r"\\[A-Za-z]+\[[^\]\r\n]*\]|\\[.!|^]|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|\{\{[^{}\r\n]+\}\}|\{\d+\}|<[/A-Za-z][^<>\r\n]*>"
)
PLACEHOLDER_RE = re.compile(r"%(?:\d+\$)?[+#0\- ]*\d*(?:\.\d+)?[A-Za-z]|\$\{[^}\r\n]+\}")
JP_QUOTE_CHARS = "「」『』"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict) and isinstance(value.get("files"), dict):
        result = []
        for file_data in value["files"].values():
            if isinstance(file_data, dict) and isinstance(file_data.get("items"), list):
                result.extend(row for row in file_data["items"] if isinstance(row, dict))
        return result
    raise ValueError("input must be a row list or AiNiee cache object")


def signature(text: str) -> dict[str, Any]:
    return {
        "tokens": dict(Counter(TOKEN_RE.findall(text))),
        "placeholders": dict(Counter(PLACEHOLDER_RE.findall(text))),
        "newlines": re.findall(r"\r\n|\r|\n", text),
        "quotes": {char: text.count(char) for char in JP_QUOTE_CHARS if char in text},
    }


def glossary_issues(source: str, target: str, glossary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not glossary:
        return []
    issues = []
    for term in glossary.get("terms", []):
        if not isinstance(term, dict) or term.get("keep_source"):
            continue
        src, dst = str(term.get("src", "")), str(term.get("dst", ""))
        if src and dst and src in source and dst not in target:
            issues.append({"kind": "term_missing", "detail": f"{src} -> {dst} not found in translation"})
    return issues


def index_rows(values: list[dict[str, Any]], label: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    indexed: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for position, row in enumerate(values, 1):
        raw = row.get("text_index")
        if raw is None or isinstance(raw, bool):
            issues.append({"kind": f"invalid_{label}_text_index", "position": position, "value": raw})
            continue
        index = str(raw)
        if index in indexed:
            issues.append({"kind": f"duplicate_{label}_text_index", "text_index": raw})
            continue
        indexed[index] = row
    return indexed, issues


def audit(source_rows: list[dict[str, Any]], translation_rows: list[dict[str, Any]], glossary: dict[str, Any] | None) -> dict[str, Any]:
    source_by_index, source_index_issues = index_rows(source_rows, "source")
    translation_by_index, translation_index_issues = index_rows(translation_rows, "translation")
    issues: list[dict[str, Any]] = [*source_index_issues, *translation_index_issues]
    for index in sorted(set(translation_by_index) - set(source_by_index)):
        issues.append({"kind": "extra_translation_row", "text_index": translation_by_index[index].get("text_index")})
    for index, source_row in source_by_index.items():
        target_row = translation_by_index.get(index)
        if target_row is None:
            issues.append({"kind": "missing_row", "text_index": source_row.get("text_index")})
            continue
        source = str(source_row.get("source_text", ""))
        target = str(target_row.get("translated_text", target_row.get("translation", "")) or "")
        if not target.strip() and source.strip():
            issues.append({"kind": "empty_translation", "text_index": source_row.get("text_index"), "expected_before": target})
            continue
        expected = signature(source)
        actual = signature(target)
        if expected["tokens"] != actual["tokens"]:
            issues.append({"kind": "control_or_tag_mismatch", "text_index": source_row.get("text_index"), "expected": expected["tokens"], "actual": actual["tokens"], "expected_before": target})
        if expected["placeholders"] != actual["placeholders"]:
            issues.append({"kind": "placeholder_mismatch", "text_index": source_row.get("text_index"), "expected": expected["placeholders"], "actual": actual["placeholders"], "expected_before": target})
        if expected["newlines"] != actual["newlines"]:
            issues.append({"kind": "newline_mismatch", "text_index": source_row.get("text_index"), "expected": expected["newlines"], "actual": actual["newlines"], "expected_before": target})
        if expected["quotes"] and expected["quotes"] != actual["quotes"]:
            issues.append({"kind": "dialogue_quote_mismatch", "text_index": source_row.get("text_index"), "expected": expected["quotes"], "actual": actual["quotes"], "expected_before": target})
        for issue in glossary_issues(source, target, glossary):
            issue.update({"text_index": source_row.get("text_index"), "expected_before": target})
            issues.append(issue)
    return {"valid": not issues, "source_rows": len(source_rows), "translation_rows": len(translation_rows), "issue_count": len(issues), "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit source-aware translation invariants")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--translation", required=True, type=Path)
    parser.add_argument("--glossary", type=Path)
    parser.add_argument("--repair-manifest", type=Path)
    args = parser.parse_args()
    glossary = read_json(args.glossary) if args.glossary else None
    report = audit(rows(read_json(args.source)), rows(read_json(args.translation)), glossary)
    if args.repair_manifest:
        args.repair_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.repair_manifest.write_text(json.dumps({"format": "ainiee-source-repair-v1", "items": report["issues"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
