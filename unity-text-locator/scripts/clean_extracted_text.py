#!/usr/bin/env python3
"""Filter Unity extracted text into a translator-facing CSV.

Input: extracted_text.csv with one column, original_flat.
Output:
  - extracted_text_filtered.csv with one column, original_flat
  - removed_text_detail.csv with original_flat,reason
  - text_cleaning_report_zh.md
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


KANA_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
JP_ANY_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9f\u3400-\u4dbf\u4e00-\u9fff]")
TAG_RE = re.compile(r"<[^>]+>")

TECH_PATTERNS = [
    re.compile(r"UnityEvent", re.I),
    re.compile(r"Signal\s*Emitter|\u30b7\u30b0\u30ca\u30eb\u30a8\u30df\u30c3\u30bf\u30fc", re.I),
    re.compile(r"GameObject|Transform|RectTransform|Animator|Prefab", re.I),
    re.compile(r"XiaoMi|Bluetooth|Wireless|GameController|AndroidMode|Joystick", re.I),
]

CHINESE_SPECIFIC = set("蓝柄软击载输设这请轻选游戏标准简体繁体码驱动")
GLYPH_PATTERNS = (
    "\u3041\u3042\u3043\u3044\u3045\u3046\u3047\u3048\u3049\u304a",
    "\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7",
    "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)

REASON_ZH = {
    "duplicate": "重复文本",
    "empty": "空文本",
    "no Japanese/CJK signal": "没有日文或中日韩文字特征",
    "font/glyph table": "像字体字库或字形表",
    "engine/device/internal technical string": "像引擎、设备或内部技术字符串",
    "internal marker": "像内部标记或分隔符",
    "ASCII-only technical text": "仅包含 ASCII 技术字符",
    "placeholder/test text": "像占位或测试文本",
    "credits block removed by option": "按选项删除制作名单块",
    "likely Chinese/non-Japanese support string": "像中文或非日文支持文本",
    "long CJK-only weak text": "较长且缺少假名，疑似非游戏正文",
    "raw UTF-8 fallback fragment": "从二进制回退扫描得到的长碎片",
}


def read_rows(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "original_flat" not in (reader.fieldnames or []):
            raise SystemExit(f"{path} must contain original_flat")
        return [row.get("original_flat", "") for row in reader]


def manifest_maps(path: Path | None) -> tuple[dict[str, list[dict]], dict[str, Counter]]:
    if not path or not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    occurrences = data.get("occurrences", [])
    by_text: dict[str, list[dict]] = {}
    method_by_text: dict[str, Counter] = {}
    for occ in occurrences:
        flat = occ.get("original_flat", "")
        by_text.setdefault(flat, []).append(occ)
        method_by_text.setdefault(flat, Counter())[occ.get("method", "")] += 1
    return by_text, method_by_text


def looks_like_glyph_table(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if any(pattern in compact for pattern in GLYPH_PATTERNS):
        return True
    if len(compact) < 80:
        return False
    jp_count = len(JP_ANY_RE.findall(compact))
    unique_ratio = len(set(compact)) / max(len(compact), 1)
    punct_count = sum(1 for ch in compact if ch in ".,;:!?()[]{}<>/-_#&+=%")
    punct_ratio = punct_count / max(len(compact), 1)
    return jp_count / max(len(compact), 1) > 0.72 and unique_ratio > 0.55 and punct_ratio < 0.10


def classify(text: str, occurrences: list[dict], methods: Counter, remove_credits: bool) -> tuple[bool, str]:
    stripped = text.strip()
    without_tags = TAG_RE.sub("", stripped)
    if not stripped:
        return False, "empty"
    if not JP_ANY_RE.search(without_tags):
        return False, "no Japanese/CJK signal"
    if looks_like_glyph_table(without_tags):
        return False, "font/glyph table"
    if any(pattern.search(stripped) for pattern in TECH_PATTERNS):
        return False, "engine/device/internal technical string"
    if re.fullmatch(r"[-=*> ]{3,}.*", stripped):
        return False, "internal marker"
    if re.fullmatch(r"[A-Za-z0-9_ .:/\\+\-]+", without_tags):
        return False, "ASCII-only technical text"
    if re.search(r"Sample text|aaaaa|00444", stripped, re.I):
        return False, "placeholder/test text"
    if remove_credits and (
        "\u4f01\u753b" in stripped
        and "\u5236\u4f5c" in stripped
        and ("\u30c7\u30a3\u30ec\u30af\u30bf\u30fc" in stripped or "Presented by" in stripped)
    ):
        return False, "credits block removed by option"

    has_kana = bool(KANA_RE.search(without_tags))
    cjk_count = len(CJK_RE.findall(without_tags))
    if not has_kana and cjk_count:
        if any(ch in CHINESE_SPECIFIC for ch in without_tags):
            return False, "likely Chinese/non-Japanese support string"
        if len(without_tags) > 12 and not any(token in stripped for token in ("<sprite", "\u306e", "\u30fb")):
            return False, "long CJK-only weak text"

    if methods and methods.get("raw-utf8-fallback", 0) and not methods.get("unity-len-utf8", 0) and len(stripped) > 120:
        return False, "raw UTF-8 fallback fragment"

    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path, help="extracted_text.csv")
    parser.add_argument("--manifest", type=Path, default=None, help="extraction_manifest.json")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--remove-credits", action="store_true")
    args = parser.parse_args()

    out_dir = args.out_dir or args.csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_rows(args.csv_path)
    by_text, method_by_text = manifest_maps(args.manifest)

    kept: list[str] = []
    removed: list[dict[str, str]] = []
    reason_counts: Counter[str] = Counter()
    seen: set[str] = set()
    for text in rows:
        if text in seen:
            removed.append({"original_flat": text, "reason": "duplicate"})
            reason_counts["duplicate"] += 1
            continue
        seen.add(text)
        keep, reason = classify(text, by_text.get(text, []), method_by_text.get(text, Counter()), args.remove_credits)
        if keep:
            kept.append(text)
        else:
            removed.append({"original_flat": text, "reason": reason})
            reason_counts[reason] += 1

    filtered_path = out_dir / "extracted_text_filtered.csv"
    with filtered_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["original_flat"], lineterminator="\n")
        writer.writeheader()
        for text in kept:
            writer.writerow({"original_flat": text})

    removed_path = out_dir / "removed_text_detail.csv"
    with removed_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["original_flat", "reason"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(removed)

    report_path = out_dir / "text_cleaning_report_zh.md"
    lines = [
        "# Unity 文本清洗报告",
        "",
        f"- 输入行数: {len(rows)}",
        f"- 保留行数: {len(kept)}",
        f"- 删除行数: {len(removed)}",
        f"- 清洗后 CSV: `{filtered_path}`",
        f"- 删除明细: `{removed_path}`",
        "",
        "## 删除原因统计",
    ]
    for reason, count in reason_counts.most_common():
        lines.append(f"- {REASON_ZH.get(reason, reason)}: {count} ({reason})")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"filtered_csv": str(filtered_path), "removed_csv": str(removed_path), "report": str(report_path), "kept": len(kept), "removed": len(removed)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
