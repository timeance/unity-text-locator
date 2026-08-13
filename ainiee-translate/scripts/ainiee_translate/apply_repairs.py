"""Apply only explicitly reviewed source-aware repairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed expected_before repair entries")
    parser.add_argument("--translation", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    translation = read_json(args.translation)
    if not isinstance(translation, list):
        parser.error("translation must be a JSON row list")
    manifest = read_json(args.manifest)
    entries = manifest.get("items", []) if isinstance(manifest, dict) else []
    replacements: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict) or "replacement" not in item:
            continue
        index = str(item.get("text_index"))
        if index in replacements:
            parser.error(f"duplicate repair text_index {item.get('text_index')}")
        replacements[index] = item
    row_indexes = [str(row.get("text_index")) for row in translation if isinstance(row, dict)]
    if len(row_indexes) != len(set(row_indexes)):
        parser.error("translation contains duplicate text_index values")
    missing = sorted(set(replacements) - set(row_indexes))
    if missing:
        parser.error(f"repair text_index values not found: {', '.join(missing[:20])}")
    changed = 0
    for row in translation:
        if not isinstance(row, dict):
            continue
        item = replacements.get(str(row.get("text_index")))
        if not item:
            continue
        current = str(row.get("translated_text", "") or "")
        expected_before = str(item.get("expected_before", "") or "")
        if current != expected_before:
            parser.error(f"stale expected_before for text_index {row.get('text_index')}")
        row["translated_text"] = str(item["replacement"])
        changed += 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(translation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"changed": changed, "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
