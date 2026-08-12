"""Extract conservative terminology candidates from an AiNiee cache.

This is a review aid only. It never assigns translations and never edits the
cache. Candidates are derived from source text shape and repetition so the
agent can confirm them against context before locking a glossary entry.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


KANJI = r"一-龯々ヶ"
KANA = r"ぁ-ゖァ-ヺー"
READING_RE = re.compile(rf"([{KANJI}]{{1,20}})[（(]([{KANA}]{{1,30}})[）)]")
KATAKANA_RE = re.compile(rf"[{r'ァ-ヺー'}]{{2,24}}")
JAPANESE_RE = re.compile(rf"[{KANJI}{KANA}]")
TRAILING_VARIANT_RE = re.compile(r"(?:[+＋?!？！~～]|[_-]?[0-9]{1,3}|[A-Z])$")
PUNCTUATION_RE = re.compile(r"[。！？!?、，,。．.「」『』【】（）()：:；;…]+")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def iter_items(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        yield from (item for item in value if isinstance(item, dict))
        return
    if not isinstance(value, dict):
        raise ValueError("input must be an AiNiee cache object or a list of rows")
    files = value.get("files")
    if not isinstance(files, dict):
        raise ValueError("cache object must contain a files mapping")
    for file_data in files.values():
        if not isinstance(file_data, dict):
            continue
        items = file_data.get("items", [])
        if isinstance(items, list):
            yield from (item for item in items if isinstance(item, dict))


def normalize_term(value: str) -> str:
    value = value.strip(" \t　")
    while value:
        previous = value
        value = re.sub(r"[（(][^（）()\r\n]{0,30}[）)]$", "", value)
        value = TRAILING_VARIANT_RE.sub("", value)
        value = value.strip(" \t　")
        if value == previous:
            break
    return value


def safe_label(value: str) -> bool:
    value = value.strip()
    return (
        2 <= len(value) <= 32
        and bool(JAPANESE_RE.search(value))
        and not PUNCTUATION_RE.search(value)
        and "\\" not in value
        and "<" not in value
        and ">" not in value
    )


def add(book: dict[str, dict[str, Any]], term: str, category: str, item: dict[str, Any]) -> None:
    term = normalize_term(term)
    if not safe_label(term):
        return
    entry = book.setdefault(
        term,
        {
            "source": term,
            "normalized": term,
            "categories": set(),
            "occurrences": [],
            "row_keys": set(),
        },
    )
    entry["categories"].add(category)
    row_key = str(item.get("text_index", len(entry["row_keys"])))
    if row_key not in entry["row_keys"]:
        entry["row_keys"].add(row_key)
        if len(entry["occurrences"]) < 5:
            entry["occurrences"].append(
                {"text_index": item.get("text_index"), "source_text": item.get("source_text", "")}
            )


def extract(items: Iterable[dict[str, Any]], min_frequency: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    book: dict[str, dict[str, Any]] = {}
    resource_hints: dict[str, dict[str, Any]] = defaultdict(lambda: {"resource": "", "frequency": 0})
    for item in items:
        source = str(item.get("source_text", ""))
        if not source.strip():
            continue
        for match in READING_RE.finditer(source):
            add(book, match.group(0), "reading_form", item)
        for match in KATAKANA_RE.finditer(source):
            add(book, match.group(0), "katakana", item)
        if safe_label(source):
            add(book, source, "short_label", item)
        if re.fullmatch(r"[A-Za-z0-9_./\\-]{3,160}", source.strip()) and ("/" in source or "\\" in source or "." in source):
            hint = resource_hints[source.strip()]
            hint["resource"] = source.strip()
            hint["frequency"] += 1

    candidates = []
    for entry in book.values():
        frequency = len(entry["row_keys"])
        if frequency < min_frequency:
            continue
        candidates.append(
            {
                "source": entry["source"],
                "normalized": entry["normalized"],
                "categories": sorted(entry["categories"]),
                "confidence": "high" if "reading_form" in entry["categories"] or "short_label" in entry["categories"] else "medium",
                "frequency": frequency,
                "requires_review": True,
                "occurrences": entry["occurrences"],
            }
        )
    candidates.sort(key=lambda item: (-({"high": 3, "medium": 2}[item["confidence"]]), -item["frequency"], item["source"]))
    hints = sorted(resource_hints.values(), key=lambda item: (-item["frequency"], item["resource"]))
    return candidates, hints


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract conservative glossary candidates from an AiNiee cache")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-frequency", type=int, default=2)
    args = parser.parse_args()
    if args.min_frequency < 1:
        parser.error("--min-frequency must be positive")
    candidates, hints = extract(iter_items(read_json(args.input)), args.min_frequency)
    payload = {"candidates": candidates, "resource_hints": hints, "requires_review": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "resource_hints": len(hints), "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
