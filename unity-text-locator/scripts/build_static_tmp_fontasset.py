#!/usr/bin/env python3
"""Build a static TMP FontAsset candidate while preserving target identities."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import UnityPy


TAG_RE = re.compile(r"<[^>]*>")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def versions(env: Any) -> list[str]:
    return sorted(
        {
            str(getattr(obj.assets_file, "unity_version", ""))
            for obj in env.objects
            if getattr(obj.assets_file, "unity_version", "")
        }
    )


def object_at(env: Any, type_name: str, path_id: int) -> tuple[Any, dict[str, Any]]:
    matches = [obj for obj in env.objects if obj.type.name == type_name and int(obj.path_id) == path_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {type_name} PathID {path_id}, found {len(matches)}")
    return matches[0], matches[0].read_typetree()


def infer_donor_object(env: Any, type_name: str, path_id: int | None) -> tuple[Any, dict[str, Any]]:
    if path_id is not None:
        return object_at(env, type_name, path_id)
    matches = [obj for obj in env.objects if obj.type.name == type_name]
    if len(matches) != 1:
        raise RuntimeError(
            f"donor has {len(matches)} {type_name} objects; pass the matching --donor-{type_name.lower()}-path-id"
        )
    return matches[0], matches[0].read_typetree()


def walk_files(file: Any, name: str = "") -> Iterable[tuple[str, Any]]:
    yield name, file
    children = getattr(file, "files", None)
    if isinstance(children, dict):
        for child_name, child in children.items():
            yield from walk_files(child, str(child_name))


def top_file(env: Any) -> Any:
    roots = list(env.files.values())
    if len(roots) != 1:
        raise RuntimeError(f"expected one top-level Unity container, found {len(roots)}")
    return roots[0]


def stream_bytes(env: Any, texture: dict[str, Any]) -> bytes:
    inline = bytes(texture.get("image data", b""))
    stream = texture.get("m_StreamData") or {}
    stream_path = str(stream.get("path", ""))
    stream_size = int(stream.get("size", 0))
    stream_offset = int(stream.get("offset", 0))
    if stream_path and stream_size:
        wanted = Path(stream_path.replace("\\", "/")).name.lower()
        matches = []
        for child_name, file in walk_files(top_file(env)):
            object_name = str(getattr(file, "name", "") or getattr(file, "path", ""))
            candidate_name = child_name or object_name
            if Path(candidate_name.replace("\\", "/")).name.lower() == wanted:
                matches.append(file)
        if len(matches) != 1:
            raise RuntimeError(f"expected one streamed atlas {stream_path!r}, found {len(matches)}")
        source = matches[0]
        source.seek(stream_offset)
        payload = source.read_bytes(stream_size)
    else:
        payload = inline
    expected = int(texture.get("m_CompleteImageSize", len(payload)))
    if not payload or len(payload) != expected:
        raise RuntimeError(f"atlas payload length {len(payload)} does not match m_CompleteImageSize {expected}")
    return payload


def rewrite_pptr(value: Any, source_path_id: int, target_path_id: int) -> None:
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value and int(value["m_PathID"]) == source_path_id:
            value["m_FileID"] = 0
            value["m_PathID"] = target_path_id
        for child in value.values():
            rewrite_pptr(child, source_path_id, target_path_id)
    elif isinstance(value, (list, tuple)):
        for child in value:
            rewrite_pptr(child, source_path_id, target_path_id)


def translation_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(path for path in root.rglob("*.csv") if "translation" in path.stem.lower())


def required_characters(root: Path) -> set[str]:
    files = translation_files(root)
    if not files:
        raise RuntimeError(f"no translation CSV files found under {root}")
    result: set[str] = set()
    for path in files:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        if not rows:
            continue
        start = 1 if len(rows[0]) == 1 and rows[0][0].strip().lower() in {"zh_cn", "translation"} else 0
        for row in rows[start:]:
            if not row:
                continue
            value = TAG_RE.sub("", row[-1])
            result.update(
                char
                for char in value
                if not char.isspace() and not unicodedata.category(char).startswith("C")
            )
    if not result:
        raise RuntimeError("translation inputs contain no visible characters; coverage cannot be proven")
    return result


def pptr(tree: dict[str, Any], key: str) -> tuple[int, int]:
    value = tree.get(key) or {}
    return int(value.get("m_FileID", 0)), int(value.get("m_PathID", 0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True, help="Target bundle or asset to clone")
    parser.add_argument("--target-font-path-id", type=int, required=True)
    parser.add_argument("--target-material-path-id", type=int, required=True)
    parser.add_argument("--target-texture-path-id", type=int, required=True)
    parser.add_argument("--donor", type=Path, required=True, help="Static TMP donor bundle")
    parser.add_argument("--donor-font-path-id", type=int)
    parser.add_argument("--donor-material-path-id", type=int)
    parser.add_argument("--donor-texture-path-id", type=int)
    parser.add_argument("--translation-root", type=Path, required=True)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="replace an existing external candidate/report")
    parser.add_argument(
        "--allow-version-mismatch",
        action="store_true",
        help="Allow a mismatched serialized Unity version and mark the result as canary-only",
    )
    parser.add_argument(
        "--allow-donor-glyph-aliases",
        action="store_true",
        help="Allow required donor characters that share a glyph index; mark the result as canary-only",
    )
    args = parser.parse_args()

    target = args.target.resolve()
    donor = args.donor.resolve()
    translation_root = args.translation_root.resolve()
    game_root = args.game_root.resolve()
    out = args.out.resolve()
    report_path = args.report.resolve()
    for source in (target, donor):
        if not source.is_file():
            raise SystemExit(f"input file not found: {source}")
    if not game_root.is_dir():
        raise SystemExit(f"game root not found: {game_root}")
    if out == target or inside(out, game_root) or inside(report_path, game_root):
        raise SystemExit("candidate and report must be outside the game root and must not replace the target")
    for destination in (out, report_path):
        if destination.exists() and not destination.is_file():
            raise SystemExit(f"output path exists and is not a file: {destination}")
    existing = [str(path) for path in (out, report_path) if path.exists()]
    if existing and not args.force:
        raise SystemExit("external output already exists; pass --force to replace: " + ", ".join(existing))

    target_env = UnityPy.load(str(target))
    donor_env = UnityPy.load(str(donor))
    target_versions = versions(target_env)
    donor_versions = versions(donor_env)
    if target_versions != donor_versions and not args.allow_version_mismatch:
        raise SystemExit(
            f"Unity version mismatch: target={target_versions}, donor={donor_versions}; "
            "use --allow-version-mismatch only for an isolated runtime canary"
        )

    target_font_obj, target_font = object_at(target_env, "MonoBehaviour", args.target_font_path_id)
    target_material_obj, target_material = object_at(target_env, "Material", args.target_material_path_id)
    target_texture_obj, target_texture = object_at(target_env, "Texture2D", args.target_texture_path_id)
    donor_font_obj, donor_font = infer_donor_object(donor_env, "MonoBehaviour", args.donor_font_path_id)
    donor_material_obj, donor_material = infer_donor_object(donor_env, "Material", args.donor_material_path_id)
    donor_texture_obj, donor_texture = infer_donor_object(donor_env, "Texture2D", args.donor_texture_path_id)
    if not donor_font.get("m_CharacterTable") or not donor_font.get("m_GlyphTable"):
        raise SystemExit("donor is not a populated static TMP FontAsset")

    required = required_characters(translation_root)
    donor_codepoints = {int(entry["m_Unicode"]) for entry in donor_font["m_CharacterTable"]}
    missing = sorted((char, f"U+{ord(char):04X}") for char in required if ord(char) not in donor_codepoints)
    if missing:
        preview = ", ".join(f"{char}({codepoint})" for char, codepoint in missing[:20])
        raise SystemExit(f"donor misses {len(missing)} required characters; glyph aliases are forbidden: {preview}")
    codepoints_by_glyph: dict[int, set[int]] = {}
    for entry in donor_font["m_CharacterTable"]:
        codepoints_by_glyph.setdefault(int(entry.get("m_GlyphIndex", -1)), set()).add(int(entry["m_Unicode"]))
    donor_entries = {int(entry["m_Unicode"]): entry for entry in donor_font["m_CharacterTable"]}
    aliases = {
        f"U+{codepoint:04X}": [f"U+{other:04X}" for other in sorted(codepoints_by_glyph[glyph]) if other != codepoint]
        for codepoint in sorted(ord(char) for char in required)
        if len(
            codepoints_by_glyph[
                (glyph := int(donor_entries[codepoint].get("m_GlyphIndex", -1)))
            ]
        )
        > 1
    }
    if aliases and not args.allow_donor_glyph_aliases:
        preview = ", ".join(f"{key}->{value}" for key, value in list(aliases.items())[:10])
        raise SystemExit(
            f"donor uses shared glyph indices for {len(aliases)} required characters; "
            f"use --allow-donor-glyph-aliases only for a reviewed canary: {preview}"
        )

    atlas = stream_bytes(donor_env, donor_texture)
    new_texture = copy.deepcopy(donor_texture)
    new_texture["m_Name"] = target_texture.get("m_Name", "")
    new_texture["image data"] = atlas
    new_texture["m_StreamData"] = {"offset": 0, "size": 0, "path": ""}
    target_texture_obj.save_typetree(new_texture)

    new_material = copy.deepcopy(donor_material)
    new_material["m_Name"] = target_material.get("m_Name", "")
    new_material["m_Shader"] = copy.deepcopy(target_material.get("m_Shader"))
    rewrite_pptr(new_material, int(donor_texture_obj.path_id), int(target_texture_obj.path_id))
    target_material_obj.save_typetree(new_material)

    new_font = copy.deepcopy(donor_font)
    for key in ("m_GameObject", "m_Enabled", "m_Script", "m_Name", "m_EditorClassIdentifier"):
        if key in target_font:
            new_font[key] = copy.deepcopy(target_font[key])
    new_font["m_Material"] = {"m_FileID": 0, "m_PathID": int(target_material_obj.path_id)}
    new_font["m_AtlasTextures"] = [{"m_FileID": 0, "m_PathID": int(target_texture_obj.path_id)}]
    new_font["m_SourceFontFile"] = {"m_FileID": 0, "m_PathID": 0}
    new_font["m_AtlasPopulationMode"] = 0
    if "m_IsMultiAtlasTexturesEnabled" in new_font:
        new_font["m_IsMultiAtlasTexturesEnabled"] = 0
    target_font_obj.save_typetree(new_font)

    out.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=out.name + ".", suffix=".tmp", dir=str(out.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_bytes(top_file(target_env).save())
        # Loading from bytes prevents UnityPy from retaining a Windows file
        # handle that would block the atomic os.replace below.
        check_env = UnityPy.load(temp.read_bytes())
        check_font_obj, check_font = object_at(check_env, "MonoBehaviour", args.target_font_path_id)
        check_material_obj, check_material = object_at(check_env, "Material", args.target_material_path_id)
        check_texture_obj, check_texture = object_at(check_env, "Texture2D", args.target_texture_path_id)
        reopened_codepoints = {int(entry["m_Unicode"]) for entry in check_font.get("m_CharacterTable", [])}
        reopened_missing = sorted(f"U+{ord(char):04X}" for char in required if ord(char) not in reopened_codepoints)
        invariants = {
            "font_path_id": int(check_font_obj.path_id) == int(target_font_obj.path_id),
            "material_path_id": int(check_material_obj.path_id) == int(target_material_obj.path_id),
            "texture_path_id": int(check_texture_obj.path_id) == int(target_texture_obj.path_id),
            "font_name": check_font.get("m_Name") == target_font.get("m_Name"),
            "material_name": check_material.get("m_Name") == target_material.get("m_Name"),
            "texture_name": check_texture.get("m_Name") == target_texture.get("m_Name"),
            "script": check_font.get("m_Script") == target_font.get("m_Script"),
            "shader": check_material.get("m_Shader") == target_material.get("m_Shader"),
            "font_material_ref": pptr(check_font, "m_Material") == (0, args.target_material_path_id),
            "font_atlas_ref": bool(check_font.get("m_AtlasTextures"))
            and int(check_font["m_AtlasTextures"][0].get("m_PathID", 0)) == args.target_texture_path_id,
            "atlas_inline": len(check_texture.get("image data", b"")) == len(atlas),
            "source_font_cleared": pptr(check_font, "m_SourceFontFile") == (0, 0),
            "static_population": int(check_font.get("m_AtlasPopulationMode", -1)) == 0,
            "coverage_complete": not reopened_missing,
        }
        if not all(invariants.values()):
            failed = [name for name, passed in invariants.items() if not passed]
            raise RuntimeError(f"candidate reopen verification failed: {failed}")
        if out.exists() and not args.force:
            raise RuntimeError(f"candidate appeared during build; refusing to replace it: {out}")
        os.replace(temp, out)
    finally:
        if temp.exists():
            temp.unlink()

    report = {
        "candidate": str(out),
        "source_target": str(target),
        "donor": str(donor),
        "sha256": {"target": sha256(target), "donor": sha256(donor), "candidate": sha256(out)},
        "unity_versions": {"target": target_versions, "donor": donor_versions},
        "version_mismatch_allowed": bool(args.allow_version_mismatch and target_versions != donor_versions),
        "canary_only": bool(
            (args.allow_version_mismatch and target_versions != donor_versions)
            or (args.allow_donor_glyph_aliases and aliases)
        ),
        "target_ids": {
            "font": args.target_font_path_id,
            "material": args.target_material_path_id,
            "texture": args.target_texture_path_id,
        },
        "donor_ids": {
            "font": int(donor_font_obj.path_id),
            "material": int(donor_material_obj.path_id),
            "texture": int(donor_texture_obj.path_id),
        },
        "identity_and_reopen_checks": invariants,
        "font": {
            "character_count": len(check_font.get("m_CharacterTable", [])),
            "glyph_count": len(check_font.get("m_GlyphTable", [])),
            "atlas_population_mode": int(check_font.get("m_AtlasPopulationMode", -1)),
        },
        "texture": {
            "width": int(check_texture.get("m_Width", 0)),
            "height": int(check_texture.get("m_Height", 0)),
            "format": int(check_texture.get("m_TextureFormat", -1)),
            "inline_bytes": len(check_texture.get("image data", b"")),
        },
        "coverage": {
            "required_unique": len(required),
            "covered_unique": len(required),
            "missing": reopened_missing,
            "glyph_aliases": aliases,
            "donor_glyph_aliases_allowed": bool(args.allow_donor_glyph_aliases and aliases),
        },
        "not_applied_to_game": True,
        "runtime_canary_required": True,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
