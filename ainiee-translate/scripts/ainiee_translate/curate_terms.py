"""Export reviewed candidates as an AiNiee {src, dst, info} term table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def translation_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [{"src": source, "dst": target} for source, target in value.items()]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    raise ValueError("translations must be a source-to-target object or a row list")


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate reviewed term candidates into AiNiee format")
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--translations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-frequency", type=int, default=2)
    parser.add_argument("--include-medium", action="store_true")
    args = parser.parse_args()
    candidate_data = read_json(args.candidates)
    candidates = candidate_data.get("candidates", []) if isinstance(candidate_data, dict) else []
    known = {str(row.get("source")): row for row in candidates if isinstance(row, dict)}
    mappings: dict[str, set[str]] = {}
    owners: dict[str, set[str]] = {}
    for row in translation_rows(read_json(args.translations)):
        source = str(row.get("src", row.get("source", ""))).strip()
        target = str(row.get("dst", row.get("target", ""))).strip()
        if not source or not target:
            continue
        mappings.setdefault(source, set()).add(target)
        for alias in [source, *list(row.get("aliases") or [])]:
            owners.setdefault(str(alias), set()).add(target)
    conflicts = {source: sorted(targets) for source, targets in mappings.items() if len(targets) > 1}
    alias_conflicts = {alias: sorted(targets) for alias, targets in owners.items() if len(targets) > 1}
    if conflicts or alias_conflicts:
        print(json.dumps({"status": "blocked", "conflicts": conflicts, "alias_conflicts": alias_conflicts}, ensure_ascii=False, indent=2))
        return 1
    output = []
    skipped_unknown = []
    for source, targets in mappings.items():
        candidate = known.get(source)
        if not candidate:
            skipped_unknown.append(source)
            continue
        if int(candidate.get("frequency", 0)) < args.min_frequency:
            continue
        if candidate.get("confidence") != "high" and not args.include_medium:
            continue
        categories = ", ".join(candidate.get("categories", []))
        output.append({"src": source, "dst": next(iter(targets)), "info": f"{categories}; source rows: {candidate.get('frequency', 0)}"})
    output.sort(key=lambda row: (-len(row["src"]), row["src"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "entries": len(output), "skipped_unknown": skipped_unknown[:20], "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
