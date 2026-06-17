#!/usr/bin/env python3
"""Install a preflighted runtime Chinese font fallback into a Mono TMP game."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


FONT_CHOICES = {
    "Microsoft YaHei": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf"],
    "Microsoft YaHei UI": [r"C:\Windows\Fonts\msyh.ttc"],
    "Noto Sans SC": [r"C:\Windows\Fonts\NotoSansSC-VF.ttf"],
    "SimHei": [r"C:\Windows\Fonts\simhei.ttf"],
    "SimSun": [r"C:\Windows\Fonts\simsun.ttc"],
    "DengXian": [r"C:\Windows\Fonts\Deng.ttf"],
}
DEFAULT_PATTERNS = ["misaki", "k8x12", "nijimi", "maboroshinonijimimincho", "pixel", "noto", "mplus", "liberation", "arial", "font"]


def find_data_dir(root: Path) -> Path:
    if root.name.lower().endswith("_data"):
        return root
    for child in root.iterdir():
        if child.is_dir() and child.name.lower().endswith("_data"):
            return child
    raise SystemExit(f"No *_Data folder found under {root}")


def find_csc() -> Path:
    candidates = [
        Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
        Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
    ]
    csc = next((path for path in candidates if path.exists()), None)
    if not csc:
        raise SystemExit("Could not find csc.exe from .NET Framework 4.x")
    return csc


def resolve_font(font_name: str, font_file: Path | None) -> tuple[str, Path]:
    if font_file:
        if not font_file.exists():
            raise SystemExit(f"Font file not found: {font_file}")
        return font_file.stem, font_file
    for candidate in FONT_CHOICES.get(font_name, []):
        path = Path(candidate)
        if path.exists():
            return font_name, path
    raise SystemExit(f"Could not find font '{font_name}'. Use --font-file PATH.")


def installed_runtime_font_files() -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for candidates in FONT_CHOICES.values():
        for candidate in candidates:
            normalized = str(Path(candidate))
            if normalized not in seen and Path(candidate).exists():
                found.append(normalized)
                seen.add(normalized)
    return found


def compile_dll(skill_dir: Path, managed: Path, csc: Path, output_dll: Path) -> None:
    source = skill_dir / "assets" / "ChineseFontFixer.cs"
    refs = [
        "netstandard.dll",
        "UnityEngine.dll",
        "UnityEngine.CoreModule.dll",
        "UnityEngine.TextRenderingModule.dll",
        "UnityEngine.TextCoreFontEngineModule.dll",
        "UnityEngine.UI.dll",
        "Unity.TextMeshPro.dll",
    ]
    arguments = [str(csc), "/target:library", "/optimize+", "/nologo", "/out:" + str(output_dll)]
    arguments.extend("/reference:" + str(managed / ref) for ref in refs if (managed / ref).exists())
    arguments.append(str(source))
    subprocess.run(arguments, check=True, capture_output=True, text=True, encoding="mbcs", errors="replace")


def build_json_outputs(data_dir: Path) -> tuple[dict[Path, bytes], int]:
    scripting = data_dir / "ScriptingAssemblies.json"
    runtime = data_dir / "RuntimeInitializeOnLoads.json"
    if not scripting.exists() or not runtime.exists():
        raise SystemExit(
            "Automatic font injection requires ScriptingAssemblies.json and RuntimeInitializeOnLoads.json. "
            "This build needs a project-specific globalgamemanagers or assembly hook."
        )
    scripting_data = json.loads(scripting.read_text(encoding="utf-8-sig"))
    names = scripting_data.get("names")
    types = scripting_data.get("types")
    if not isinstance(names, list) or not isinstance(types, list) or len(names) != len(types):
        raise SystemExit("Unsupported ScriptingAssemblies.json format")
    if "Assembly-CSharp.dll" in names:
        assembly_type = types[names.index("Assembly-CSharp.dll")]
    else:
        assembly_type = 16
    if "ChineseFontFixer.dll" not in names:
        names.append("ChineseFontFixer.dll")
        types.append(assembly_type)

    runtime_data = json.loads(runtime.read_text(encoding="utf-8-sig"))
    entries = runtime_data.get("root")
    if not isinstance(entries, list):
        raise SystemExit("Unsupported RuntimeInitializeOnLoads.json format")
    entry = {
        "assemblyName": "ChineseFontFixer",
        "nameSpace": "",
        "className": "ChineseFontBootstrap",
        "methodName": "Initialize",
        "loadTypes": 0,
        "isUnityClass": False,
    }
    if not any(
        existing.get("assemblyName") == "ChineseFontFixer"
        and existing.get("className") == "ChineseFontBootstrap"
        for existing in entries
        if isinstance(existing, dict)
    ):
        entries.insert(0, entry)
    return {
        scripting: json.dumps(scripting_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        runtime: json.dumps(runtime_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    }, assembly_type


def build_font_asset_outputs(
    data_dir: Path, root: Path, font_name: str, font_path: Path, patterns: list[str]
) -> tuple[dict[Path, bytes], list[dict]]:
    try:
        import UnityPy  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("UnityPy is required only when --patch-embedded-fonts is used") from exc
    outputs: dict[Path, bytes] = {}
    patched: list[dict] = []
    font_bytes = font_path.read_bytes()
    lowered_patterns = [pattern.lower() for pattern in patterns]
    safe_name = "ChineseFontFixer_" + "".join(character if character.isalnum() else "_" for character in font_name)
    for path in [data_dir / "resources.assets", data_dir / "sharedassets0.assets"]:
        if not path.exists():
            continue
        env = UnityPy.load(str(path))
        asset_file = next(iter(env.files.values()))
        changed = False
        for obj in env.objects:
            if getattr(obj.type, "name", str(obj.type)) != "Font":
                continue
            try:
                font = obj.read()
            except Exception:
                continue
            old_name = getattr(font, "m_Name", "") or ""
            old_names = getattr(font, "m_FontNames", []) or []
            joined = (old_name + " " + " ".join(old_names)).lower()
            if not any(pattern in joined for pattern in lowered_patterns):
                continue
            old_len = len(getattr(font, "m_FontData", b"") or b"")
            font.m_Name = safe_name
            font.m_FontNames = [font_name]
            font.m_FontData = font_bytes
            font.save()
            changed = True
            patched.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "path_id": obj.path_id,
                    "old_name": old_name,
                    "old_font_names": old_names,
                    "old_len": old_len,
                    "new_len": len(font_bytes),
                }
            )
        if changed:
            outputs[path] = asset_file.save()
    return outputs, patched


def backup_path(path: Path, root: Path, backup_root: Path) -> Path:
    destination = backup_root / path.relative_to(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def commit_outputs(outputs: dict[Path, bytes], root: Path, backup_root: Path) -> tuple[list[str], list[str]]:
    existing = {path: path.exists() for path in outputs}
    written: list[Path] = []
    leftover_temporaries: list[Path] = []
    for path in outputs:
        if path.exists():
            shutil.copy2(path, backup_path(path, root, backup_root))
    try:
        for path, payload in outputs.items():
            temporary = path.with_name(path.name + ".ChineseFontFixer.tmp")
            temporary.write_bytes(payload)
            try:
                os.replace(temporary, path)
            except PermissionError:
                # Some game installs allow content writes but reject replacement/rename.
                shutil.copyfile(temporary, path)
                try:
                    temporary.unlink()
                except PermissionError:
                    leftover_temporaries.append(temporary)
            written.append(path)
    except Exception:
        for path in written:
            saved = backup_root / path.relative_to(root)
            if existing[path] and saved.exists():
                shutil.copy2(saved, path)
            elif not existing[path] and path.exists():
                path.unlink()
        raise
    return (
        [path.relative_to(root).as_posix() for path in written],
        [path.relative_to(root).as_posix() for path in leftover_temporaries],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_root", type=Path)
    parser.add_argument("--font", default="Microsoft YaHei", choices=sorted(FONT_CHOICES))
    parser.add_argument("--font-file", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--font-name-pattern", action="append", default=None, help="Embedded Font asset name substring to replace")
    parser.add_argument("--patch-embedded-fonts", action="store_true", help="Also replace matching embedded legacy Font assets")
    parser.add_argument("--dry-run", action="store_true", help="Run compatibility checks and compile staging output without modifying game files")
    args = parser.parse_args()

    skill_dir = Path(__file__).resolve().parents[1]
    supplied_root = args.game_root.resolve()
    data_dir = find_data_dir(supplied_root)
    root = data_dir.parent if supplied_root.name.lower().endswith("_data") else supplied_root
    managed = data_dir / "Managed"
    tmp_assembly = managed / "Unity.TextMeshPro.dll"
    if not tmp_assembly.exists():
        raise SystemExit("Unity.TextMeshPro.dll not found. This automatic fixer supports TMP/Mono games only.")
    csc = find_csc()
    json_outputs, assembly_type = build_json_outputs(data_dir)
    if not args.patch_embedded_fonts and (args.font_file or args.font != "Microsoft YaHei"):
        raise SystemExit("--font and --font-file select embedded replacement data only; use them with --patch-embedded-fonts")

    outputs: dict[Path, bytes] = dict(json_outputs)
    patched_fonts: list[dict] = []
    selected_font = args.font
    selected_font_file: str | None = None
    if args.patch_embedded_fonts:
        selected_font, font_path = resolve_font(args.font, args.font_file)
        selected_font_file = str(font_path)
        font_outputs, patched_fonts = build_font_asset_outputs(
            data_dir, root, selected_font, font_path, args.font_name_pattern or DEFAULT_PATTERNS
        )
        outputs.update(font_outputs)

    output_dll = managed / "ChineseFontFixer.dll"
    with tempfile.TemporaryDirectory(prefix="ChineseFontFixer-") as temporary_dir:
        staged_dll = Path(temporary_dir) / output_dll.name
        try:
            compile_dll(skill_dir, managed, csc, staged_dll)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"ChineseFontFixer compilation failed during preflight: {exc.stderr or exc.stdout}") from exc
        outputs[output_dll] = staged_dll.read_bytes()

    out_dir = args.out_dir or (root / "_translation" / "unity-text-report" / "font-fix")
    out_dir.mkdir(parents=True, exist_ok=True)
    backup_root = out_dir / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    written_files: list[str] = []
    leftover_temp_files: list[str] = []
    if not args.dry_run:
        backup_root.mkdir(parents=True, exist_ok=True)
        written_files, leftover_temp_files = commit_outputs(outputs, root, backup_root)

    result = {
        "dry_run": args.dry_run,
        "game_root": str(root),
        "data_dir": str(data_dir),
        "injection_mode": "ScriptingAssemblies.json + RuntimeInitializeOnLoads.json",
        "assembly_type": assembly_type,
        "runtime_fallback_fonts": list(FONT_CHOICES),
        "runtime_fallback_font_files_found": installed_runtime_font_files(),
        "patch_embedded_fonts": args.patch_embedded_fonts,
        "embedded_font_name": selected_font if args.patch_embedded_fonts else None,
        "embedded_font_file": selected_font_file,
        "managed_dll": str(output_dll),
        "planned_files": sorted(path.relative_to(root).as_posix() for path in outputs),
        "written_files": written_files,
        "leftover_temp_files": leftover_temp_files,
        "backup_root": str(backup_root) if not args.dry_run else None,
        "patched_fonts": patched_fonts,
    }
    (out_dir / ("font_fix_dryrun_report.json" if args.dry_run else "font_fix_report.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
