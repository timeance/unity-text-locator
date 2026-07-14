#!/usr/bin/env python3
"""Inspect a TMP font bundle and measure its coverage without modifying files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import UnityPy


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_files(file: Any) -> Iterable[Any]:
    yield file
    children = getattr(file, "files", None)
    if isinstance(children, dict):
        for child in children.values():
            yield from walk_files(child)


def unity_versions(env: Any) -> list[str]:
    versions: set[str] = set()
    for root in env.files.values():
        for file in walk_files(root):
            value = getattr(file, "unity_version", None)
            if value:
                versions.add(str(value))
    return sorted(versions)


def version_family(version: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def compare_versions(source: list[str], target: list[str]) -> dict[str, Any]:
    source_families = {item for value in source if (item := version_family(value))}
    target_families = {item for value in target if (item := version_family(value))}
    shared = sorted(source_families & target_families)
    if not source or not target:
        verdict = "unknown"
    elif set(source) & set(target):
        verdict = "exact-version-candidate"
    elif shared:
        verdict = "same-major-minor-candidate"
    else:
        verdict = "cross-version-high-risk"
    return {
        "verdict": verdict,
        "shared_major_minor": [f"{major}.{minor}" for major, minor in shared],
        "note": "Version compatibility is necessary but not sufficient; require a runtime canary.",
    }


def read_name(obj: Any) -> tuple[Any, str]:
    data = obj.read(check_read=False)
    return data, str(getattr(data, "m_Name", ""))


def pptr(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    return {
        "file_id": int(getattr(value, "m_FileID", 0)),
        "path_id": int(getattr(value, "m_PathID", 0)),
    }


def collect_translation_chars(path: Path) -> Counter[str]:
    files = [path] if path.is_file() else sorted(path.rglob("*.csv"))
    chars: Counter[str] = Counter()
    for file in files:
        try:
            with file.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or "zh_cn" not in reader.fieldnames:
                    continue
                for row in reader:
                    chars.update(char for char in (row.get("zh_cn") or "") if not char.isspace())
        except (OSError, UnicodeError, csv.Error):
            continue
    return chars


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("font_bundle", type=Path)
    parser.add_argument("--target-asset", type=Path)
    parser.add_argument("--translation-root", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    bundle = args.font_bundle.resolve()
    if not bundle.is_file():
        raise SystemExit(f"font bundle not found: {bundle}")

    env = UnityPy.load(str(bundle))
    materials: list[dict[str, Any]] = []
    textures: list[dict[str, Any]] = []
    fonts: list[dict[str, Any]] = []

    for obj in env.objects:
        if obj.type.name not in {"MonoBehaviour", "Material", "Texture2D"}:
            continue
        try:
            data, name = read_name(obj)
        except Exception:
            continue
        if obj.type.name == "Material":
            materials.append({"name": name, "path_id": int(obj.path_id)})
        elif obj.type.name == "Texture2D":
            stream = getattr(data, "m_StreamData", None)
            textures.append(
                {
                    "name": name,
                    "path_id": int(obj.path_id),
                    "width": int(getattr(data, "m_Width", 0)),
                    "height": int(getattr(data, "m_Height", 0)),
                    "texture_format": int(getattr(data, "m_TextureFormat", -1)),
                    "inline_bytes": len(getattr(data, "image_data", b"")),
                    "stream_path": str(getattr(stream, "path", "")) if stream else "",
                    "stream_size": int(getattr(stream, "size", 0)) if stream else 0,
                    "platform_blob_bytes": len(getattr(data, "m_PlatformBlob", b"")),
                    "color_space": int(getattr(data, "m_ColorSpace", -1)),
                }
            )
        else:
            glyphs = getattr(data, "m_GlyphTable", None)
            characters = getattr(data, "m_CharacterTable", None)
            if glyphs is None or characters is None:
                continue
            codepoints = {int(getattr(char, "m_Unicode", -1)) for char in characters}
            fonts.append(
                {
                    "name": name,
                    "path_id": int(obj.path_id),
                    "character_count": len(codepoints),
                    "glyph_count": len(glyphs),
                    "atlas_width": int(getattr(data, "m_AtlasWidth", 0)),
                    "atlas_height": int(getattr(data, "m_AtlasHeight", 0)),
                    "atlas_population_mode": int(getattr(data, "m_AtlasPopulationMode", -1)),
                    "material": pptr(getattr(data, "m_Material", None) or getattr(data, "material", None)),
                    "atlas_textures": [pptr(item) for item in (getattr(data, "m_AtlasTextures", None) or [])],
                    "codepoints": codepoints,
                }
            )

    used_chars = collect_translation_chars(args.translation_root.resolve()) if args.translation_root else Counter()
    for font in fonts:
        codepoints = font.pop("codepoints")
        if used_chars:
            missing = {char: count for char, count in used_chars.items() if ord(char) not in codepoints}
            font["coverage"] = {
                "unique_used": len(used_chars),
                "covered_unique": len(used_chars) - len(missing),
                "missing_unique": len(missing),
                "missing_total_occurrences": sum(missing.values()),
                "missing": sorted(missing.items(), key=lambda item: (-item[1], item[0])),
            }

    source_versions = unity_versions(env)
    target_versions: list[str] = []
    if args.target_asset:
        target_versions = unity_versions(UnityPy.load(str(args.target_asset.resolve())))

    report: dict[str, Any] = {
        "font_bundle": str(bundle),
        "size": bundle.stat().st_size,
        "sha256": sha256(bundle),
        "unity_versions": source_versions,
        "fonts": fonts,
        "materials": materials,
        "textures": textures,
        "runtime_canary_required": True,
    }
    if args.target_asset:
        report["target_asset"] = str(args.target_asset.resolve())
        report["target_unity_versions"] = target_versions
        report["version_compatibility"] = compare_versions(source_versions, target_versions)
    if not fonts:
        report["warning"] = "No TMP font asset with character and glyph tables was found."

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = args.out.resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    try:
        print(rendered)
    except UnicodeEncodeError:
        # Windows consoles may still use a legacy code page. Keep stdout
        # machine-readable without making UTF-8 console configuration a
        # hidden prerequisite; --out always preserves the original Unicode.
        print(rendered.encode("ascii", errors="backslashreplace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
