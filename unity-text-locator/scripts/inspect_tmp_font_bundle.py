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


def normalized_family(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def population_mode_name(value: int) -> str:
    return {0: "static", 1: "dynamic", 2: "dynamic-os"}.get(value, f"unknown-{value}")


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
    source_fonts: list[dict[str, Any]] = []
    fonts: list[dict[str, Any]] = []

    for obj in env.objects:
        if obj.type.name not in {"MonoBehaviour", "Material", "Texture2D", "Font"}:
            continue
        try:
            data, name = read_name(obj)
        except Exception:
            continue
        if obj.type.name == "Font":
            source_fonts.append({"name": name, "path_id": int(obj.path_id)})
        elif obj.type.name == "Material":
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
            population_mode = int(getattr(data, "m_AtlasPopulationMode", -1))
            source_font = pptr(getattr(data, "m_SourceFontFile", None))
            face_info = getattr(data, "m_FaceInfo", None)
            face_family = str(getattr(face_info, "m_FamilyName", "")) if face_info else ""
            face_style = str(getattr(face_info, "m_StyleName", "")) if face_info else ""
            fonts.append(
                {
                    "name": name,
                    "path_id": int(obj.path_id),
                    "character_count": len(codepoints),
                    "glyph_count": len(glyphs),
                    "atlas_width": int(getattr(data, "m_AtlasWidth", 0)),
                    "atlas_height": int(getattr(data, "m_AtlasHeight", 0)),
                    "atlas_population_mode": population_mode,
                    "population_mode": population_mode_name(population_mode),
                    "source_font_file": source_font,
                    "face_family": face_family,
                    "face_style": face_style,
                    "material": pptr(getattr(data, "m_Material", None) or getattr(data, "material", None)),
                    "atlas_textures": [pptr(item) for item in (getattr(data, "m_AtlasTextures", None) or [])],
                    "codepoints": codepoints,
                }
            )

    sources_by_id = {item["path_id"]: item for item in source_fonts}
    for font in fonts:
        source_ref = font.get("source_font_file") or {}
        source_name = ""
        source_file_id = int(source_ref.get("file_id", 0))
        source_path_id = int(source_ref.get("path_id", 0))
        if source_file_id == 0:
            source = sources_by_id.get(int(source_ref.get("path_id", 0)))
            source_name = str((source or {}).get("name", ""))
        if source_file_id:
            source_status = "external-unresolved"
        elif source_path_id == 0:
            source_status = "none"
        elif source_name:
            source_status = "local-resolved"
        else:
            source_status = "local-unresolved"
        face_family = str(font.get("face_family", ""))
        identity_conflict: bool | None
        if source_status != "local-resolved" or not face_family:
            identity_conflict = None
        else:
            identity_conflict = bool(
                normalized_family(source_name) != normalized_family(face_family)
                and normalized_family(source_name) not in normalized_family(face_family)
                and normalized_family(face_family) not in normalized_family(source_name)
            )
        populated_dynamic = (
            str(font.get("population_mode", "")).startswith("dynamic")
            and int(font.get("character_count", 0)) > 0
            and int(font.get("glyph_count", 0)) > 0
        )
        font["source_font_name"] = source_name
        font["source_font_resolution"] = source_status
        font["font_family_identity_conflict"] = identity_conflict
        font["source_font_only_replacement_risk"] = populated_dynamic
        font["risk_notes"] = []
        if identity_conflict is True:
            font["risk_notes"].append(
                "The TMP face family and referenced source Font identity differ; verify the visible component chain."
            )
        elif source_status.endswith("unresolved"):
            font["risk_notes"].append(
                "The source Font identity is unresolved in this file; trace the visible component/CAB chain before comparing families."
            )
        if populated_dynamic:
            font["risk_notes"].append(
                "BLOCK: this dynamic TMP asset already has character/glyph tables. Replacing only its source TTF can map valid Unicode to wrong glyph indices."
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
        "source_fonts": source_fonts,
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
