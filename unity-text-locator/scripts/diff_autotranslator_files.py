#!/usr/bin/env python3
"""Diff two AutoTranslator files without merging or deploying either one."""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path


def parse(path: Path) -> tuple[OrderedDict[str, str], int, list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    entries: OrderedDict[str, str] = OrderedDict()
    duplicates: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in entries:
            duplicates.append(key)
        entries[key] = value.strip()
    return entries, len(text), duplicates


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff two _PreTranslated.txt files")
    parser.add_argument("--old", required=True, type=Path)
    parser.add_argument("--new", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()
    old, old_chars, old_duplicates = parse(args.old)
    new, new_chars, new_duplicates = parse(args.new)
    old_keys, new_keys = set(old), set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(key for key in old_keys & new_keys if old[key] != new[key])
    blocked = bool(old_duplicates or new_duplicates)
    report: dict[str, object] = {
        "format": "unity-autotranslator-diff-v1",
        "status": "blocked" if blocked else "ready",
        "old": str(args.old), "new": str(args.new),
        "old_entries": len(old), "new_entries": len(new),
        "old_duplicate_key_count": len(old_duplicates), "new_duplicate_key_count": len(new_duplicates),
        "old_duplicate_key_samples": old_duplicates[:20], "new_duplicate_key_samples": new_duplicates[:20],
        "added": len(added), "removed": len(removed), "changed": len(changed),
        "unchanged": len(old_keys & new_keys) - len(changed),
        "old_chars": old_chars, "new_chars": new_chars,
        "removed_warning": len(removed) > 10,
        "runtime_merge": "manual_review_required",
    }
    if not args.stats_only:
        report["added_samples"] = [{"key": key, "value": new[key]} for key in added[:20]]
        report["removed_samples"] = [{"key": key, "value": old[key]} for key in removed[:20]]
        report["changed_samples"] = [{"key": key, "old": old[key], "new": new[key]} for key in changed[:20]]
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
