#!/usr/bin/env python3
"""Read-only Unity game text locator and extractor.

Scans a Unity build folder for likely Japanese/CJK text locations and writes a
Markdown report plus JSON details. By default, it also writes a one-column CSV
of extracted text for translation. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


TEXT_EXTS = {
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".config",
    ".bytes",
    ".lua",
    ".yarn",
    ".ink",
    ".ks",
    ".scenario",
    ".script",
    ".md",
}

UNITY_ASSET_EXTS = {".assets", ".resS", ".resource"}
BUNDLE_EXTS = {".bundle", ".unity3d", ".ab", ".assetbundle"}
CODE_EXTS = {".dll", ".exe", ".pdb"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".gz", ".lz4", ".dat", ".bin"}

JP_SIGNAL_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
RUN_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uff00-\uffefA-Za-z0-9 \t\r\n.,;:!?%#&+=<>@_\-/\\'\"()\[\]{}"
    r"]{2,}"
)

ENCODINGS = (
    "utf-8-sig",
    "utf-16",
    "utf-16le",
    "utf-16be",
    "cp932",
    "shift_jis",
    "euc_jp",
)


@dataclass
class Sample:
    encoding: str
    strength: str
    text: str


@dataclass
class Finding:
    path: str
    size: int
    category: str
    priority: int
    samples: list[Sample]
    notes: list[str]


def looks_utf16(data: bytes) -> bool:
    head = data[:4096]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    if len(head) < 8:
        return False
    even_nuls = head[0::2].count(0)
    odd_nuls = head[1::2].count(0)
    half = max(len(head) // 2, 1)
    return even_nuls / half > 0.2 or odd_nuls / half > 0.2


def should_try_encoding(data: bytes, encoding: str) -> bool:
    if encoding.startswith("utf-16"):
        return looks_utf16(data)
    return True


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def safe_read(path: Path, max_bytes: int) -> bytes | None:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return None
        return path.read_bytes()
    except OSError:
        return None


def collapse_text(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def samples_from_decoded(
    decoded: str,
    encoding: str,
    include_cjk_only: bool,
    per_encoding_limit: int,
    seen: set[tuple[str, str]],
) -> list[Sample]:
    out: list[Sample] = []
    if not decoded or (not JP_SIGNAL_RE.search(decoded) and not CJK_RE.search(decoded)):
        return out

    found_for_encoding = 0
    for match in RUN_RE.finditer(decoded):
        snippet = collapse_text(match.group(0))
        if len(snippet) < 2:
            continue

        has_jp = bool(JP_SIGNAL_RE.search(snippet))
        has_cjk = bool(CJK_RE.search(snippet))
        if not has_jp and not (include_cjk_only and has_cjk):
            continue

        strength = "strong-jp" if has_jp else "weak-cjk"
        key = (strength, snippet)
        if key in seen:
            continue
        seen.add(key)
        out.append(Sample(encoding=encoding, strength=strength, text=snippet))
        found_for_encoding += 1
        if found_for_encoding >= per_encoding_limit:
            break

    return out


def extract_samples(data: bytes, include_cjk_only: bool, per_encoding_limit: int) -> list[Sample]:
    seen: set[tuple[str, str]] = set()

    try:
        decoded = data.decode("utf-8-sig")
        utf8_samples = samples_from_decoded(decoded, "utf-8-sig", include_cjk_only, per_encoding_limit, seen)
        if utf8_samples or b"\x00" not in data[:4096]:
            return utf8_samples
    except UnicodeDecodeError:
        pass

    out: list[Sample] = []
    for encoding in ENCODINGS:
        if encoding == "utf-8-sig" or not should_try_encoding(data, encoding):
            continue
        try:
            decoded = data.decode(encoding, errors="ignore")
        except LookupError:
            continue
        out.extend(samples_from_decoded(decoded, encoding, include_cjk_only, per_encoding_limit, seen))

    return out


def category_for(path: Path, root: Path) -> tuple[str, int, list[str]]:
    lower_parts = [p.lower() for p in path.parts]
    name = path.name.lower()
    suffix = path.suffix.lower()
    notes: list[str] = []

    if "streamingassets" in lower_parts:
        notes.append("inside StreamingAssets")
    if "managed" in lower_parts:
        notes.append("inside Managed")
    if "addressables" in lower_parts or "aa" in lower_parts:
        notes.append("possible Addressables path")

    if name.startswith("catalog") and suffix == ".json":
        return "addressables-catalog", 95, notes
    if suffix in TEXT_EXTS:
        if suffix in {".json", ".csv", ".tsv", ".xml", ".yaml", ".yml"}:
            return "structured-text", 100, notes
        return "loose-text", 98, notes
    if suffix in UNITY_ASSET_EXTS or re.fullmatch(r"(resources|sharedassets\d*|level\d*)\.assets", name):
        return "unity-asset-container", 75, notes
    if suffix in BUNDLE_EXTS or "bundle" in name:
        return "assetbundle", 70, notes
    if suffix in CODE_EXTS:
        if "managed" in lower_parts:
            return "managed-code", 55, notes
        return "binary-code", 35, notes
    if name == "global-metadata.dat":
        return "il2cpp-metadata", 58, notes
    if suffix in ARCHIVE_EXTS:
        return "archive-or-custom-binary", 45, notes
    if suffix == "":
        return "extensionless-possible-bundle", 50, notes
    return "other", 20, notes


def iter_files(root: Path, ignored_dirs: set[str]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in ignored_dirs]
        base = Path(dirpath)
        for filename in filenames:
            yield base / filename


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


def detect_backend(root: Path, data_dirs: list[Path]) -> dict[str, object]:
    managed_hits: list[str] = []
    il2cpp_hits: list[str] = []

    search_roots = [root, *data_dirs]
    for base in search_roots:
        managed = base / "Managed" / "Assembly-CSharp.dll"
        if managed.exists():
            managed_hits.append(rel(managed, root))
        metadata = base / "il2cpp_data" / "Metadata" / "global-metadata.dat"
        if metadata.exists():
            il2cpp_hits.append(rel(metadata, root))

    game_assembly = root / "GameAssembly.dll"
    if game_assembly.exists():
        il2cpp_hits.append(rel(game_assembly, root))

    backend = "unknown"
    if managed_hits and il2cpp_hits:
        backend = "mixed-or-ambiguous"
    elif managed_hits:
        backend = "mono"
    elif il2cpp_hits:
        backend = "il2cpp"

    return {
        "backend": backend,
        "managed_evidence": sorted(set(managed_hits)),
        "il2cpp_evidence": sorted(set(il2cpp_hits)),
    }


def detect_unity_markers(root: Path) -> dict[str, object]:
    data_dirs = find_data_dirs(root)
    markers: list[str] = []
    for data_dir in data_dirs or [root]:
        for marker in ("globalgamemanagers", "boot.config", "resources.assets"):
            candidate = data_dir / marker
            if candidate.exists():
                markers.append(rel(candidate, root))

    exe_candidates = []
    try:
        exe_candidates = [rel(p, root) for p in root.glob("*.exe")]
    except OSError:
        pass

    return {
        "root": str(root),
        "data_dirs": [rel(p, root) for p in data_dirs],
        "exe_candidates": exe_candidates,
        "unity_markers": sorted(set(markers)),
        **detect_backend(root, data_dirs),
    }


def scan(root: Path, max_file_mb: int, include_cjk_only: bool, sample_limit: int, ignored_dirs: set[str]) -> dict[str, object]:
    root = root.resolve()
    max_bytes = max_file_mb * 1024 * 1024
    metadata = detect_unity_markers(root)
    findings: list[Finding] = []
    skipped_large: list[str] = []
    scanned_files = 0

    for path in iter_files(root, ignored_dirs):
        scanned_files += 1
        try:
            size = path.stat().st_size
        except OSError:
            continue

        category, priority, notes = category_for(path, root)
        should_probe = category != "other" or size <= max_bytes
        if not should_probe:
            continue

        data = safe_read(path, max_bytes)
        if data is None:
            if category != "other":
                skipped_large.append(rel(path, root))
            continue

        samples = extract_samples(data, include_cjk_only, sample_limit)
        if samples:
            findings.append(
                Finding(
                    path=rel(path, root),
                    size=size,
                    category=category,
                    priority=priority,
                    samples=samples[:sample_limit],
                    notes=notes,
                )
            )
        elif category in {"addressables-catalog", "unity-asset-container", "assetbundle", "managed-code", "il2cpp-metadata"}:
            findings.append(
                Finding(
                    path=rel(path, root),
                    size=size,
                    category=category,
                    priority=max(priority - 25, 1),
                    samples=[],
                    notes=[*notes, "container/code candidate; no direct Japanese sample in raw bytes"],
                )
            )

    findings.sort(key=lambda item: (-item.priority, item.category, item.path.lower()))
    return {
        "metadata": metadata,
        "stats": {
            "scanned_files": scanned_files,
            "findings": len(findings),
            "skipped_large": skipped_large,
            "max_file_mb": max_file_mb,
            "include_cjk_only": include_cjk_only,
        },
        "findings": [asdict(f) for f in findings],
    }


def recommend_tools(result: dict[str, object]) -> list[str]:
    findings = result["findings"]
    assert isinstance(findings, list)
    categories = {str(f["category"]) for f in findings if isinstance(f, dict)}
    backend = str(result["metadata"].get("backend", "unknown"))  # type: ignore[union-attr]

    recs: list[str] = []
    if {"loose-text", "structured-text"} & categories:
        recs.append("Inspect/edit loose text files first; they are the lowest-friction translation targets.")
    if "addressables-catalog" in categories or "assetbundle" in categories:
        recs.append("Use the Addressables catalog to map keys to bundles, then extract bundles with UnityPy, AssetRipper, or UABEA.")
    if "unity-asset-container" in categories:
        recs.append("Export resources/sharedassets/level assets with AssetRipper, UABEA, or UnityPy and inspect TextAsset/MonoBehaviour/ScriptableObject data.")
    if backend == "mono" or "managed-code" in categories:
        recs.append("Inspect Managed DLLs with ILSpy/dnSpy for hardcoded UI strings and localization manager paths.")
    if backend == "il2cpp" or "il2cpp-metadata" in categories:
        recs.append("For IL2CPP, pair GameAssembly.dll with global-metadata.dat and use Il2CppDumper or Cpp2IL before code-string analysis.")
    if not recs:
        recs.append("No obvious text hits were found; check for encrypted/custom archives, compressed bundles, or an unsupported platform package.")
    return recs


def write_report(result: dict[str, object], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "hits.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    metadata = result["metadata"]
    stats = result["stats"]
    findings = result["findings"]
    assert isinstance(metadata, dict)
    assert isinstance(stats, dict)
    assert isinstance(findings, list)

    lines: list[str] = []
    lines.append("# Unity Text Locator and Extractor Report")
    lines.append("")
    lines.append(f"- Root: `{metadata.get('root')}`")
    lines.append(f"- Data dirs: {', '.join(f'`{p}`' for p in metadata.get('data_dirs', [])) or 'none detected'}")
    lines.append(f"- Backend: `{metadata.get('backend')}`")
    lines.append(f"- Scanned files: {stats.get('scanned_files')}")
    lines.append(f"- Findings: {stats.get('findings')}")
    lines.append("")

    if metadata.get("managed_evidence"):
        lines.append("## Mono Evidence")
        for item in metadata["managed_evidence"]:
            lines.append(f"- `{item}`")
        lines.append("")

    if metadata.get("il2cpp_evidence"):
        lines.append("## IL2CPP Evidence")
        for item in metadata["il2cpp_evidence"]:
            lines.append(f"- `{item}`")
        lines.append("")

    lines.append("## Recommendations")
    for rec in recommend_tools(result):
        lines.append(f"- {rec}")
    lines.append("")

    lines.append("## Top Candidates")
    if findings:
        for item in findings[:50]:
            assert isinstance(item, dict)
            sample_count = len(item.get("samples", []))
            notes = item.get("notes") or []
            note_text = f" ({'; '.join(notes)})" if notes else ""
            lines.append(
                f"- `{item['path']}` - {item['category']}, {item['size']} bytes, "
                f"{sample_count} sample(s){note_text}"
            )
    else:
        lines.append("- No direct candidates found.")
    lines.append("")

    lines.append("## Sample Hits")
    hit_rows = 0
    for item in findings:
        assert isinstance(item, dict)
        samples = item.get("samples") or []
        for sample in samples[:3]:
            hit_rows += 1
            lines.append(f"### `{item['path']}`")
            lines.append(f"- Encoding: `{sample['encoding']}`")
            lines.append(f"- Strength: `{sample['strength']}`")
            lines.append(f"- Sample: {sample['text']}")
            lines.append("")
            if hit_rows >= 80:
                break
        if hit_rows >= 80:
            break
    if hit_rows == 0:
        lines.append("No readable Japanese/CJK samples were extracted directly.")
        lines.append("")

    skipped = stats.get("skipped_large") or []
    if skipped:
        lines.append("## Skipped Large Files")
        lines.append(f"Files larger than `{stats.get('max_file_mb')}` MB were not byte-scanned.")
        for item in skipped[:100]:
            lines.append(f"- `{item}`")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Locate and extract likely translatable text in Unity game folders.")
    parser.add_argument("root", help="Unity game root or *_Data folder to scan")
    parser.add_argument("--out", default=None, help="Output report directory (default: <root>/_translation/unity-text-report)")
    parser.add_argument("--max-file-mb", type=int, default=120, help="Maximum file size to byte-scan")
    parser.add_argument("--sample-limit", type=int, default=8, help="Maximum samples per file")
    parser.add_argument("--no-cjk-only", action="store_true", help="Ignore CJK-only snippets that contain no kana")
    parser.add_argument("--no-extract", action="store_true", help="Only locate candidates; do not write extracted_text.csv")
    parser.add_argument("--extract-max-file-mb", type=int, default=220, help="Maximum file size for text extraction")
    parser.add_argument("--include-managed", action="store_true", help="Also extract Managed/Assembly-CSharp.dll strings")
    parser.add_argument(
        "--ignore-dir",
        action="append",
        default=[],
        help="Directory name to skip; can be passed multiple times",
    )
    return parser.parse_args(argv)


def run_extractor(root: Path, out_dir: Path, args: argparse.Namespace, ignored_dirs: set[str]) -> dict[str, object]:
    script_path = Path(__file__).with_name("extract_unity_text.py")
    spec = importlib.util.spec_from_file_location("unity_text_extractor", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load extractor script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract_to_outputs(  # type: ignore[attr-defined]
        root=root,
        out_dir=out_dir,
        max_file_mb=args.extract_max_file_mb,
        include_managed=args.include_managed,
        ignored_dirs=ignored_dirs,
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = Path(args.root)
    if not root.exists():
        print(f"Root does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"Root is not a directory: {root}", file=sys.stderr)
        return 2

    ignored_dirs = {".git", "__pycache__", ".svn", ".hg", "_translation", *[d.lower() for d in args.ignore_dir]}
    result = scan(
        root=root,
        max_file_mb=args.max_file_mb,
        include_cjk_only=not args.no_cjk_only,
        sample_limit=args.sample_limit,
        ignored_dirs=ignored_dirs,
    )
    out_dir = Path(args.out) if args.out else root / "_translation" / "unity-text-report"
    write_report(result, out_dir)
    print(f"Wrote {out_dir / 'report.md'}")
    print(f"Wrote {out_dir / 'hits.json'}")
    if not args.no_extract:
        extraction = run_extractor(root, out_dir, args, ignored_dirs)
        print(f"Wrote {out_dir / 'extracted_text.csv'}")
        print(f"Wrote {out_dir / 'extraction_manifest.json'}")
        print(f"Wrote per-source text CSVs under {out_dir / 'text'}")
        print(f"Unique original_flat rows: {extraction['unique_text_count']}")
        print(f"Occurrences extracted: {extraction['occurrence_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
