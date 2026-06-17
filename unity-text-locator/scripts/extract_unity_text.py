#!/usr/bin/env python3
"""Extract translation-focused Japanese text from Unity game files.

The extractor is read-only. It keeps a broad audit CSV and writes per-source,
occurrence-preserving one-column text CSVs with row-mapped manifests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import struct
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


JP_SIGNAL_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
JP_ANY_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f\u3400-\u4dbf\u4e00-\u9fff]")

MAX_STRING_BYTES = 12000
DEFAULT_MAX_FILE_MB = 220
DEFAULT_IGNORED_DIRS = {".git", "__pycache__", ".svn", ".hg", "_translation", "monobleedingedge", "d3d12", "images"}

GLYPH_TABLE_PATTERNS = (
    "\u3041\u3042\u3043\u3044\u3045\u3046\u3047\u3048\u3049\u304a",
    "\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7",
    "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
    "\uff41\uff42\uff43\uff44\uff45\uff46\uff47\uff48\uff49\uff4a",
    "\uff71\uff72\uff73\uff74\uff75\uff76\uff77\uff78\uff79\uff7a",
)

EXTRA_ALLOWED_CHARS = set("...---\"\"''?!")
RAW_ALLOWED_ASCII = set("\t\n\r " + "".join(chr(i) for i in range(33, 127)))


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def find_data_dirs(root: Path) -> list[Path]:
    candidates: list[Path] = []
    if root.name.lower().endswith("_data"):
        candidates.append(root)
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name.lower().endswith("_data"):
                candidates.append(child)
    except OSError:
        pass
    return sorted(set(candidates))


def category_for_extraction(path: Path) -> str:
    parts = [p.lower() for p in path.parts]
    name = path.name.lower()
    suffix = path.suffix.lower()
    if "managed" in parts and name == "assembly-csharp.dll":
        return "Managed Assembly-CSharp.dll"
    if "managed" in parts and suffix == ".dll":
        return "Managed DLL"
    if "streamingassets" in parts and suffix == ".bundle":
        return "Addressables bundle"
    if "streamingassets" in parts:
        return "StreamingAssets config/catalog"
    if name.startswith("level") and suffix == "":
        return "Scene level file"
    if name == "resources.assets":
        return "resources.assets"
    if name.startswith("sharedassets") and suffix == ".assets":
        return "sharedassets*.assets"
    if name.startswith("globalgamemanagers"):
        return "globalgamemanagers"
    return "Other Unity/binary file"


def is_extract_candidate(path: Path, include_managed: bool) -> bool:
    parts = [p.lower() for p in path.parts]
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix == ".ress":
        return False
    if "managed" in parts:
        return include_managed and name == "assembly-csharp.dll"
    if "streamingassets" in parts:
        return suffix in {".bundle", ".bin", ".json", ".xml", ".txt", ".csv", ".tsv", ".ini", ".cfg", ".bytes"}
    if name.startswith("level") and suffix == "":
        return True
    if name == "resources.assets":
        return True
    if name.startswith("sharedassets") and suffix == ".assets":
        return True
    if name.startswith("globalgamemanagers"):
        return True
    return False


def iter_candidate_files(root: Path, ignored_dirs: set[str], include_managed: bool) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in ignored_dirs]
        base = Path(dirpath)
        for filename in filenames:
            path = base / filename
            if is_extract_candidate(path, include_managed):
                yield path


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \t\n")


def flat_text(text: str) -> str:
    return clean_text(text).replace("\n", "\\n")


def safe_filename_component(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" ._") or "game"


def project_name(root: Path) -> str:
    game_root = root.parent if root.name.lower().endswith("_data") else root
    return safe_filename_component(game_root.name)


def source_description(source_file: str) -> str:
    path = Path(source_file.replace("\\", "/"))
    name = path.name
    lower = name.lower()
    if lower == "resources.assets":
        return "resources_text"
    if lower.startswith("sharedassets") and lower.endswith(".assets"):
        return safe_filename_component(path.stem) + "_text"
    if lower.startswith("level"):
        return safe_filename_component(name) + "_text"
    if lower == "globalgamemanagers":
        return "globalgamemanagers_text"
    if "managed" in (part.lower() for part in path.parts):
        return "managed_text"
    descriptor = safe_filename_component(path.stem or name)
    return descriptor if descriptor.lower().endswith("_text") else descriptor + "_text"


def source_stem(root: Path, source_file: str) -> str:
    return project_name(root) + "_" + source_description(source_file)


def char_stats(text: str) -> dict[str, float]:
    length = max(len(text), 1)
    kana = len(JP_SIGNAL_RE.findall(text))
    cjk = len(CJK_RE.findall(text))
    jp = kana + cjk
    punctuation = sum(1 for ch in text if ch in " \n\t.,;:!?()[]{}<>\"'/-_#&+=%")
    punctuation += sum(1 for ch in text if 0x3000 <= ord(ch) <= 0x303F)
    unique_ratio = len(set(text)) / length
    return {
        "length": float(length),
        "kana": float(kana),
        "cjk": float(cjk),
        "jp": float(jp),
        "jp_ratio": jp / length,
        "kana_ratio": kana / length,
        "punct_ratio": punctuation / length,
        "unique_ratio": unique_ratio,
    }


def looks_like_glyph_table(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if any(pattern in compact for pattern in GLYPH_TABLE_PATTERNS):
        return True
    stats = char_stats(compact)
    length = int(stats["length"])
    if length < 80:
        return False
    if stats["unique_ratio"] > 0.62 and stats["jp_ratio"] > 0.72 and stats["punct_ratio"] < 0.08:
        return True
    if length > 120 and stats["jp_ratio"] > 0.88 and stats["punct_ratio"] < 0.12:
        return True
    if length > 80 and stats["kana_ratio"] > 0.70 and stats["unique_ratio"] > 0.45:
        return True
    return False


def confidence(text: str) -> str:
    kana = len(JP_SIGNAL_RE.findall(text))
    cjk = len(CJK_RE.findall(text))
    if kana >= 2:
        return "strong-jp"
    if kana == 1 and len(text) >= 5:
        return "strong-jp"
    if cjk >= 2:
        return "weak-cjk"
    return "reject"


def weak_cjk_is_useful(text: str) -> bool:
    cjk = len(CJK_RE.findall(text))
    if cjk < 2:
        return False
    if len(text) <= 16:
        return True
    if "<sprite" in text and len(text) <= 120:
        return True
    return False


def printable_enough(text: str) -> bool:
    return all(ch in "\t\n\r" or ord(ch) >= 32 for ch in text)


def accept_text(text: str, allow_weak_cjk: bool, source_method: str) -> tuple[bool, str, str]:
    text = clean_text(text)
    if len(text) < 2 or len(text) > 5000:
        return False, "reject", "length"
    if not printable_enough(text) or not JP_ANY_RE.search(text):
        return False, "reject", "not-printable-or-no-japanese"
    if looks_like_glyph_table(text):
        return False, "reject", "font-or-glyph-table"
    conf = confidence(text)
    if conf == "reject":
        return False, conf, "no-japanese-signal"
    if conf == "weak-cjk" and not (allow_weak_cjk and weak_cjk_is_useful(text)):
        return False, conf, "weak-cjk"
    if source_method == "raw-utf8-fallback" and len(text) > 280:
        return False, conf, "long-raw-fallback"
    return True, conf, ""


def scan_len_prefixed_utf8(data: bytes) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    size = len(data)
    offset = 0
    while offset <= size - 8:
        length = struct.unpack_from("<I", data, offset)[0]
        if 2 <= length <= MAX_STRING_BYTES and offset + 4 + length <= size:
            start = offset + 4
            end = start + length
            raw = data[start:end]
            if b"\x00" not in raw:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                ok, conf, note = accept_text(text, allow_weak_cjk=True, source_method="unity-len-utf8")
                pad_end = (end + 3) & ~3
                padding_ok = pad_end <= size and all(byte == 0 for byte in data[end:pad_end])
                if ok and padding_ok:
                    hits.append(
                        {
                            "offset": offset,
                            "text_offset": start,
                            "byte_length": length,
                            "text": clean_text(text),
                            "method": "unity-len-utf8",
                            "confidence": conf,
                            "note": note,
                            "span": (offset, pad_end),
                        }
                    )
                    offset = max(offset + 4, pad_end)
                    continue
        offset += 1
    return hits


def decode_utf8_char(data: bytes, offset: int) -> tuple[str | None, int]:
    byte = data[offset]
    if byte < 0x80:
        return chr(byte), 1
    for length in (2, 3, 4):
        if offset + length <= len(data):
            chunk = data[offset : offset + length]
            try:
                char = chunk.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(char) == 1:
                return char, length
    return None, 1


def raw_allowed(char: str) -> bool:
    if char in RAW_ALLOWED_ASCII or char in EXTRA_ALLOWED_CHARS:
        return True
    codepoint = ord(char)
    return (
        0x3000 <= codepoint <= 0x303F
        or 0x3040 <= codepoint <= 0x30FF
        or 0x31F0 <= codepoint <= 0x31FF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xFF00 <= codepoint <= 0xFFEF
    )


def overlaps(start: int, end: int, intervals: list[tuple[int, int]]) -> bool:
    if not intervals:
        return False
    starts = [item[0] for item in intervals]
    pos = bisect_right(starts, start) - 1
    for index in (pos, pos + 1):
        if 0 <= index < len(intervals):
            left, right = intervals[index]
            if start < right and end > left:
                return True
    return False


def scan_raw_utf8(data: bytes, covered: list[tuple[int, int]]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    size = len(data)
    offset = 0
    covered = sorted(covered)
    while offset < size:
        char, char_size = decode_utf8_char(data, offset)
        if char is None or not raw_allowed(char):
            offset += max(char_size, 1)
            continue
        start = offset
        chars: list[str] = []
        while offset < size:
            char, char_size = decode_utf8_char(data, offset)
            if char is None or not raw_allowed(char):
                break
            chars.append(char)
            offset += char_size
        end = offset
        if end - start < 4 or overlaps(start, end, covered):
            continue
        text = "".join(chars)
        ok, conf, note = accept_text(text, allow_weak_cjk=False, source_method="raw-utf8-fallback")
        if ok:
            hits.append(
                {
                    "offset": start,
                    "text_offset": start,
                    "byte_length": end - start,
                    "text": clean_text(text),
                    "method": "raw-utf8-fallback",
                    "confidence": conf,
                    "note": note or "not confirmed as Unity length-prefixed string",
                    "span": (start, end),
                }
            )
    return hits


def extract_records(
    root: Path,
    max_file_mb: int = DEFAULT_MAX_FILE_MB,
    include_managed: bool = False,
    ignored_dirs: set[str] | None = None,
) -> dict[str, object]:
    root = root.resolve()
    ignored_dirs = ignored_dirs or set(DEFAULT_IGNORED_DIRS)
    max_file_bytes = max_file_mb * 1024 * 1024
    occurrences: list[dict[str, object]] = []
    files_scanned: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    for path in iter_candidate_files(root, ignored_dirs, include_managed):
        try:
            file_size = path.stat().st_size
        except OSError:
            continue
        if file_size > max_file_bytes:
            skipped.append({"path": rel(path, root), "size": file_size, "reason": "too-large"})
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            skipped.append({"path": rel(path, root), "size": file_size, "reason": str(exc)})
            continue

        source_file = rel(path, root)
        category = category_for_extraction(path)
        files_scanned.append(
            {
                "path": source_file,
                "size": file_size,
                "sha256": hashlib.sha256(data).hexdigest(),
                "category": category,
            }
        )
        len_hits = scan_len_prefixed_utf8(data)
        intervals = [hit["span"] for hit in len_hits]
        raw_hits = scan_raw_utf8(data, intervals)
        all_hits = sorted([*len_hits, *raw_hits], key=lambda item: (int(item["offset"]), str(item["method"])))
        for hit in all_hits:
            occurrences.append(
                {
                    "source_file": source_file,
                    "category": category,
                    "offset": int(hit["offset"]),
                    "offset_hex": f"0x{int(hit['offset']):X}",
                    "text_offset": int(hit["text_offset"]),
                    "text_offset_hex": f"0x{int(hit['text_offset']):X}",
                    "byte_length": int(hit["byte_length"]),
                    "method": str(hit["method"]),
                    "confidence": str(hit["confidence"]),
                    "original": str(hit["text"]),
                    "original_flat": flat_text(str(hit["text"])),
                    "notes": str(hit["note"]),
                }
            )

    unique: dict[str, dict[str, object]] = {}
    for occurrence in occurrences:
        original_flat = str(occurrence["original_flat"])
        if original_flat not in unique:
            unique[original_flat] = {
                "original_flat": original_flat,
                "occurrence_count": 0,
                "first_source_file": occurrence["source_file"],
                "first_offset_hex": occurrence["offset_hex"],
                "categories": set(),
                "methods": set(),
                "confidence": occurrence["confidence"],
            }
        row = unique[original_flat]
        row["occurrence_count"] = int(row["occurrence_count"]) + 1
        row["categories"].add(occurrence["category"])  # type: ignore[union-attr]
        row["methods"].add(occurrence["method"])  # type: ignore[union-attr]
        if occurrence["confidence"] == "strong-jp":
            row["confidence"] = "strong-jp"

    unique_rows: list[dict[str, object]] = []
    for row in unique.values():
        copy = dict(row)
        copy["categories"] = "; ".join(sorted(copy["categories"]))  # type: ignore[arg-type]
        copy["methods"] = "; ".join(sorted(copy["methods"]))  # type: ignore[arg-type]
        unique_rows.append(copy)

    # Translation CSV should follow source order so a later zh_cn.csv can match rows by position.
    source_order = {row["original_flat"]: index for index, row in enumerate(unique.values())}
    unique_rows.sort(key=lambda item: source_order[str(item["original_flat"])])

    return {
        "root": str(root),
        "files_scanned": files_scanned,
        "skipped": skipped,
        "occurrence_count": len(occurrences),
        "unique_text_count": len(unique_rows),
        "occurrences": occurrences,
        "unique_texts": unique_rows,
    }


def write_outputs(result: dict[str, object], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    unique_texts = result["unique_texts"]
    occurrences = result["occurrences"]
    assert isinstance(unique_texts, list)
    assert isinstance(occurrences, list)

    csv_path = out_dir / "extracted_text.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["original_flat"])
        writer.writeheader()
        for row in unique_texts:
            writer.writerow({"original_flat": row["original_flat"]})

    manifest_path = out_dir / "extraction_manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_source_text_outputs(result, out_dir / "text")

    category_counts = Counter(str(row["category"]) for row in occurrences if isinstance(row, dict))
    method_counts = Counter(str(row["method"]) for row in occurrences if isinstance(row, dict))
    file_counts = Counter(str(row["source_file"]) for row in occurrences if isinstance(row, dict))
    lines = [
        "# Unity Text Extraction Summary",
        "",
        f"- Files scanned: {len(result['files_scanned'])}",
        f"- Occurrences extracted: {result['occurrence_count']}",
        f"- Unique original_flat rows: {result['unique_text_count']}",
        f"- CSV: `{csv_path.name}`",
        "",
        "## By Category",
    ]
    for name, count in category_counts.most_common():
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append("## By Method")
    for name, count in method_counts.most_common():
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append("## Top Source Files")
    for name, count in file_counts.most_common(40):
        lines.append(f"- `{name}`: {count}")
    lines.append("")
    lines.append("## Notes")
    lines.append("- The CSV intentionally contains only one column: `original_flat`.")
    lines.append("- `extraction_manifest.json` keeps offsets and methods for audit and later reinjection research.")
    lines.append("- `text/<ProjectFolderName>_<description>.csv` preserves per-file occurrence order for one-column translation and row-mapped writeback.")
    lines.append("- Long font/glyph tables are filtered by sequence and character-distribution heuristics.")
    (out_dir / "extraction-summary.md").write_text("\n".join(lines), encoding="utf-8")


def write_source_text_outputs(result: dict[str, object], text_dir: Path) -> None:
    occurrences = result["occurrences"]
    files_scanned = result["files_scanned"]
    assert isinstance(occurrences, list)
    assert isinstance(files_scanned, list)
    files_by_path = {
        str(row["path"]): row
        for row in files_scanned
        if isinstance(row, dict) and "path" in row
    }
    by_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for occurrence in occurrences:
        if isinstance(occurrence, dict):
            by_source[str(occurrence["source_file"])].append(occurrence)
    if not by_source:
        return

    text_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, object]] = []
    root = Path(str(result["root"]))
    used_stems: set[str] = set()
    for source_file, rows in sorted(by_source.items()):
        base_stem = source_stem(root, source_file)
        stem = base_stem
        suffix = 2
        while stem in used_stems:
            stem = f"{base_stem}_{suffix}"
            suffix += 1
        used_stems.add(stem)
        csv_path = text_dir / f"{stem}.csv"
        manifest_path = text_dir / f"{stem}.manifest.json"
        manifest_rows: list[dict[str, object]] = []
        with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["original_flat"], lineterminator="\n")
            writer.writeheader()
            for text_row, occurrence in enumerate(rows, start=1):
                writer.writerow({"original_flat": occurrence["original_flat"]})
                mapped = dict(occurrence)
                mapped["text_row"] = text_row
                manifest_rows.append(mapped)
        source_metadata = files_by_path.get(source_file, {})
        manifest = {
            "format": "source-text-v1",
            "root": result["root"],
            "source_file": source_file,
            "source_sha256": source_metadata.get("sha256"),
            "source_csv": csv_path.name,
            "translation_csv_suffix": "_translation.csv",
            "occurrences": manifest_rows,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        index.append(
            {
                "source_file": source_file,
                "rows": len(rows),
                "source_csv": csv_path.name,
                "manifest": manifest_path.name,
                "translation_csv": csv_path.stem + "_translation.csv",
            }
        )
    (text_dir / "source_text_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_to_outputs(
    root: Path,
    out_dir: Path,
    max_file_mb: int = DEFAULT_MAX_FILE_MB,
    include_managed: bool = False,
    ignored_dirs: set[str] | None = None,
) -> dict[str, object]:
    result = extract_records(root, max_file_mb=max_file_mb, include_managed=include_managed, ignored_dirs=ignored_dirs)
    write_outputs(result, out_dir)
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract translation-focused Japanese text from Unity game files.")
    parser.add_argument("root", help="Unity game root or *_Data folder")
    parser.add_argument("--out", default=None, help="Output report directory (default: <root>/_translation/unity-text-report)")
    parser.add_argument("--max-file-mb", type=int, default=DEFAULT_MAX_FILE_MB, help="Maximum file size to scan")
    parser.add_argument("--include-managed", action="store_true", help="Also extract strings from Managed/Assembly-CSharp.dll")
    parser.add_argument("--ignore-dir", action="append", default=[], help="Directory name to skip; can be repeated")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = Path(args.root)
    if not root.exists():
        print(f"Root does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"Root is not a directory: {root}", file=sys.stderr)
        return 2
    ignored_dirs = {*(name.lower() for name in DEFAULT_IGNORED_DIRS), *(name.lower() for name in args.ignore_dir)}
    out_dir = Path(args.out) if args.out else root / "_translation" / "unity-text-report"
    result = extract_to_outputs(
        root=root,
        out_dir=out_dir,
        max_file_mb=args.max_file_mb,
        include_managed=args.include_managed,
        ignored_dirs=ignored_dirs,
    )
    print(f"Wrote {out_dir / 'extracted_text.csv'}")
    print(f"Wrote {out_dir / 'extraction_manifest.json'}")
    print(f"Wrote per-source text CSVs under {out_dir / 'text'}")
    print(f"Unique original_flat rows: {result['unique_text_count']}")
    print(f"Occurrences extracted: {result['occurrence_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
