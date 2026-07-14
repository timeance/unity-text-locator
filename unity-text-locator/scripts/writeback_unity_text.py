#!/usr/bin/env python3
"""Write one-column translations back into Unity serialized files safely."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath


PLACEHOLDER_RE = re.compile(r"(<[^>]+>|\\[nrt]|\{[^{}]+\}|%[sdif]|\$[A-Za-z0-9_]+|\[[A-Za-z0-9_:-]+\])")
TAG_RE = re.compile(r"<[^>]*>")


def flat(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def unflat(text: str) -> str:
    return text.replace("\\n", "\n")


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \t\n")


def match_old_text(old_text: str, original_flat: str) -> tuple[bool, str, str]:
    if flat(old_text) == original_flat:
        return True, "", ""
    if flat(clean_text(old_text)) != original_flat:
        return False, "", ""
    start = 0
    end = len(old_text)
    while start < end and old_text[start] in " \t\n":
        start += 1
    while end > start and old_text[end - 1] in " \t\n":
        end -= 1
    return True, old_text[:start], old_text[end:]


def read_source(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row if row else [""] for row in csv.reader(handle)]
    if rows and rows[0] == ["original_flat"]:
        rows = rows[1:]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one original_flat column")
    return [row[0] for row in rows]


def read_translation(path: Path) -> tuple[list[str], list[str] | None]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row if row else [""] for row in csv.reader(handle)]
    if not rows:
        return [], None
    header = [cell.lstrip("\ufeff") for cell in rows[0]]
    if header in (["zh_cn"], ["original_flat"]):
        rows = rows[1:]
        if any(len(row) != 1 for row in rows):
            raise SystemExit(f"{path} must contain one translation column")
        return [row[0] for row in rows], None
    if header == ["original_flat", "zh_cn"]:
        rows = rows[1:]
        if any(len(row) != 2 for row in rows):
            raise SystemExit(f"{path} has malformed original_flat,zh_cn rows")
        return [row[1] for row in rows], [row[0] for row in rows]
    if any(len(row) != 1 for row in rows):
        raise SystemExit(f"{path} must contain one translation column")
    return [row[0] for row in rows], None


def validate_pair(source: str, translation: str, row: int, allow_newline_changes: bool) -> list[dict]:
    if not translation.strip() or translation == source:
        return []
    issues: list[dict] = []
    if sorted(PLACEHOLDER_RE.findall(source)) != sorted(PLACEHOLDER_RE.findall(translation)):
        issues.append({"type": "placeholder_mismatch", "row": row})
    if sorted(TAG_RE.findall(source)) != sorted(TAG_RE.findall(translation)):
        issues.append({"type": "tag_mismatch", "row": row})
    if not allow_newline_changes and source.count("\\n") != translation.count("\\n"):
        issues.append({"type": "newline_count_mismatch", "row": row})
    return issues


def prepare_plan(
    manifest: dict,
    source_csv: Path,
    translation_csv: Path,
    out_dir: Path,
    allow_newline_changes: bool,
) -> tuple[list[dict], Path, int]:
    source_rows = read_source(source_csv)
    translations, legacy_originals = read_translation(translation_csv)
    if len(source_rows) != len(translations):
        raise SystemExit(f"row count mismatch: source={len(source_rows)} translation={len(translations)}")
    if legacy_originals is not None:
        for row, (expected, actual) in enumerate(zip(source_rows, legacy_originals), start=2):
            if expected != actual:
                raise SystemExit(f"original_flat mismatch at CSV row {row}")
    structural_issues: list[dict] = []
    for row, (source, translation) in enumerate(zip(source_rows, translations), start=2):
        structural_issues.extend(validate_pair(source, translation, row, allow_newline_changes))
    if structural_issues:
        raise SystemExit(f"structural translation issues block writeback: {structural_issues[:10]}")

    normalized_path = out_dir / "zh_cn_writeback_utf8.csv"
    with normalized_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["zh_cn"])
        writer.writerows([[translation] for translation in translations])

    selected: list[dict] = []
    if manifest.get("format") == "source-text-v1":
        for occurrence in manifest.get("occurrences", []):
            text_row = int(occurrence.get("text_row", 0))
            if text_row < 1 or text_row > len(source_rows):
                raise SystemExit(f"manifest text_row outside source CSV: {text_row}")
            original = source_rows[text_row - 1]
            if occurrence.get("original_flat") != original:
                raise SystemExit(f"manifest/source mismatch at text row {text_row}")
            translation = translations[text_row - 1]
            if translation.strip() and translation != original:
                mapped = dict(occurrence)
                mapped["_translation"] = translation
                mapped["_row"] = text_row + 1
                selected.append(mapped)
    else:
        by_original: dict[str, str] = {}
        rows_by_original: dict[str, int] = {}
        for row, (original, translation) in enumerate(zip(source_rows, translations), start=2):
            if translation.strip() and translation != original:
                if original in by_original and by_original[original] != translation:
                    raise SystemExit(f"legacy manifest cannot disambiguate repeated original text at CSV row {row}")
                by_original[original] = translation
                rows_by_original[original] = row
        for occurrence in manifest.get("occurrences", []):
            original = occurrence.get("original_flat")
            if original in by_original:
                mapped = dict(occurrence)
                mapped["_translation"] = by_original[original]
                mapped["_row"] = rows_by_original[original]
                selected.append(mapped)
    return selected, normalized_path, sum(bool(translation.strip()) for translation in translations)


def backup(path: Path, root: Path, backup_root: Path, backed_up: set[str]) -> None:
    relative = path.relative_to(root).as_posix()
    if relative in backed_up:
        return
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    backed_up.add(relative)


def resolve_under_root(root: Path, relative: str) -> Path:
    """Resolve an existing manifest path without allowing it to escape root."""
    normalized = PurePosixPath(relative.replace("\\", "/"))
    windows = PureWindowsPath(relative)
    if (
        not relative
        or normalized.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or not normalized.parts
        or ".." in normalized.parts
    ):
        raise SystemExit(f"unsafe source path in manifest: {relative}")
    value = Path(*normalized.parts)
    resolved_root = root.resolve()
    resolved = (resolved_root / value).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise SystemExit(f"source path escapes game root: {relative}") from exc
    return resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_source_files(selected: list[dict]) -> list[str]:
    return sorted({str(item["source_file"]) for item in selected})


def stage_source_files(real_root: Path, stage_root: Path, relatives: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in relatives:
        source = resolve_under_root(real_root, relative)
        if not source.is_file():
            raise SystemExit(f"source file missing: {relative}")
        destination = resolve_under_root(stage_root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        hashes[relative] = file_sha256(source)
    return hashes


def atomic_copy(source: Path, target: Path) -> None:
    temporary = target.with_name(f"{target.name}.writeback-{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def commit_staged_files(
    real_root: Path,
    stage_root: Path,
    relatives: list[str],
    expected_hashes: dict[str, str],
    backup_root: Path,
) -> list[str]:
    """Commit validated staged files and roll back every earlier write on failure."""
    targets: dict[str, Path] = {}
    for relative in relatives:
        target = resolve_under_root(real_root, relative)
        if file_sha256(target) != expected_hashes[relative]:
            raise RuntimeError(f"source changed while writeback was staged: {relative}")
        staged = resolve_under_root(stage_root, relative)
        if not staged.is_file():
            raise RuntimeError(f"staged output missing: {relative}")
        targets[relative] = target

    for relative, target in targets.items():
        backup_path = backup_root / Path(relative)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)

    written: list[str] = []
    try:
        for relative, target in targets.items():
            atomic_copy(resolve_under_root(stage_root, relative), target)
            written.append(relative)
    except Exception:
        rollback_failures: list[str] = []
        for relative in reversed(written):
            try:
                atomic_copy(backup_root / Path(relative), targets[relative])
            except Exception:
                rollback_failures.append(relative)
        if rollback_failures:
            raise RuntimeError(f"writeback failed and rollback also failed: {rollback_failures}")
        raise
    return written


def verify_source_hash(manifest: dict, selected: list[dict]) -> None:
    expected = manifest.get("source_sha256")
    source_file = manifest.get("source_file")
    if not expected or not source_file or not selected:
        return
    path = resolve_under_root(Path(manifest["root"]), str(source_file))
    current = file_sha256(path)
    if current != expected:
        raise SystemExit(f"source file SHA256 changed since extraction: {source_file}")


def find_object(objects, offset: int):
    for obj in objects:
        if obj.byte_start <= offset < obj.byte_start + obj.byte_size:
            return obj
    return None


def patch_len_prefixed(
    manifest: dict,
    selected: list[dict],
    backup_root: Path,
    dry_run: bool,
    backed_up: set[str] | None = None,
) -> tuple[list[dict], list[dict], set[str]]:
    root = Path(manifest["root"])
    by_file: dict[str, list[dict]] = defaultdict(list)
    for occurrence in selected:
        if occurrence.get("method") == "unity-len-utf8":
            by_file[str(occurrence["source_file"])].append(occurrence)
    if not by_file:
        return [], [], set()
    try:
        import UnityPy  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("UnityPy is required for serialized Unity object writeback") from exc

    patched: list[dict] = []
    skipped: list[dict] = []
    if backed_up is None:
        backed_up = set()
    for relative, occurrences in sorted(by_file.items()):
        path = resolve_under_root(root, relative)
        if not path.exists():
            skipped.extend({"row": item["_row"], "source_file": relative, "reason": "file missing"} for item in occurrences)
            continue
        env = UnityPy.load(str(path))
        asset_file = next(iter(env.files.values()))
        groups: dict[int, tuple[object, list[dict]]] = {}
        for occurrence in sorted(occurrences, key=lambda item: int(item["offset"])):
            obj = find_object(env.objects, int(occurrence["offset"]))
            if obj is None:
                skipped.append({"row": occurrence["_row"], "source_file": relative, "reason": "no object contains offset"})
                continue
            groups.setdefault(obj.path_id, (obj, []))[1].append(occurrence)
        file_changed = False
        for _path_id, (obj, items) in groups.items():
            raw = bytearray(obj.get_raw_data())
            delta = 0
            object_changed = False
            for occurrence in sorted(items, key=lambda item: int(item["offset"])):
                original = str(occurrence["original_flat"])
                relative_offset = int(occurrence["offset"]) - obj.byte_start + delta
                if relative_offset < 0 or relative_offset + 4 > len(raw):
                    skipped.append({"row": occurrence["_row"], "source_file": relative, "reason": "string offset outside object"})
                    continue
                old_len = struct.unpack_from("<I", raw, relative_offset)[0]
                start = relative_offset + 4
                end = start + old_len
                pad_end = (end + 3) & ~3
                if pad_end > len(raw):
                    skipped.append({"row": occurrence["_row"], "source_file": relative, "reason": "string span outside object"})
                    continue
                old_text = bytes(raw[start:end]).decode("utf-8", errors="replace")
                ok, prefix, suffix = match_old_text(old_text, original)
                if not ok:
                    skipped.append({"row": occurrence["_row"], "source_file": relative, "reason": "current bytes do not match manifest original"})
                    continue
                new_text = prefix + unflat(str(occurrence["_translation"])) + suffix
                new_bytes = new_text.encode("utf-8")
                new_end = start + len(new_bytes)
                new_pad_end = (new_end + 3) & ~3
                segment = struct.pack("<I", len(new_bytes)) + new_bytes + b"\x00" * (new_pad_end - new_end)
                raw[relative_offset:pad_end] = segment
                delta += len(segment) - (pad_end - relative_offset)
                object_changed = True
                file_changed = True
                patched.append(
                    {
                        "row": occurrence["_row"],
                        "source_file": relative,
                        "method": "unity-len-utf8",
                        "written_text": new_text,
                        "path_id": int(obj.path_id),
                    }
                )
            if object_changed and not dry_run:
                obj.set_raw_data(bytes(raw))
        if file_changed and not dry_run:
            backup(path, root, backup_root, backed_up)
            path.write_bytes(asset_file.save())
    return patched, skipped, backed_up


def patch_raw(manifest: dict, selected: list[dict], backup_root: Path, backed_up: set[str], dry_run: bool) -> tuple[list[dict], list[dict]]:
    root = Path(manifest["root"])
    by_file: dict[str, list[dict]] = defaultdict(list)
    for occurrence in selected:
        if occurrence.get("method") == "raw-utf8-fallback":
            by_file[str(occurrence["source_file"])].append(occurrence)
    patched: list[dict] = []
    skipped: list[dict] = []
    for relative, occurrences in sorted(by_file.items()):
        path = resolve_under_root(root, relative)
        if not path.exists():
            skipped.extend({"row": item["_row"], "source_file": relative, "reason": "file missing"} for item in occurrences)
            continue
        data = bytearray(path.read_bytes())
        changed = False
        for occurrence in sorted(occurrences, key=lambda item: int(item["offset"])):
            original = str(occurrence["original_flat"])
            offset = int(occurrence["offset"])
            old_len = int(occurrence["byte_length"])
            old_text = bytes(data[offset : offset + old_len]).decode("utf-8", errors="replace")
            ok, prefix, suffix = match_old_text(old_text, original)
            method = "raw-utf8-fallback"
            if ok:
                new_bytes = (prefix + unflat(str(occurrence["_translation"])) + suffix).encode("utf-8")
            else:
                old_bytes = unflat(original).encode("utf-8")
                new_bytes = unflat(str(occurrence["_translation"])).encode("utf-8")
                matches = bytes(data).count(old_bytes)
                if matches != 1:
                    skipped.append({"row": occurrence["_row"], "source_file": relative, "reason": "current bytes mismatch and exact rebase is not unique"})
                    continue
                offset = bytes(data).find(old_bytes)
                old_len = len(old_bytes)
                method = "raw-utf8-fallback-rebased"
            if len(new_bytes) > old_len:
                skipped.append({"row": occurrence["_row"], "source_file": relative, "reason": "raw replacement longer than original"})
                continue
            data[offset : offset + old_len] = new_bytes + b" " * (old_len - len(new_bytes))
            changed = True
            patched.append({"row": occurrence["_row"], "source_file": relative, "method": method, "written_text": new_bytes.decode("utf-8")})
        if changed and not dry_run:
            backup(path, root, backup_root, backed_up)
            path.write_bytes(bytes(data))
    return patched, skipped


def validate_written(root: Path, patched: list[dict]) -> list[dict]:
    expected_raw: dict[str, Counter[str]] = defaultdict(Counter)
    expected_objects: dict[str, dict[int, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for item in patched:
        relative = str(item["source_file"])
        text = str(item["written_text"])
        if item.get("method") == "unity-len-utf8" and "path_id" in item:
            expected_objects[relative][int(item["path_id"])][text] += 1
        else:
            expected_raw[relative][text] += 1
    failures: list[dict] = []
    for relative, counter in expected_raw.items():
        data = resolve_under_root(root, relative).read_bytes()
        for text, count in counter.items():
            found = data.count(text.encode("utf-8"))
            if found < count:
                failures.append({"source_file": relative, "expected": flat(text), "expected_count": count, "found_count": found})
    if expected_objects:
        try:
            import UnityPy  # type: ignore
        except ImportError as exc:
            raise SystemExit("UnityPy is required for exact staged writeback validation") from exc
        for relative, by_path_id in expected_objects.items():
            env = UnityPy.load(str(resolve_under_root(root, relative)))
            objects = {int(obj.path_id): obj for obj in env.objects}
            for path_id, counter in by_path_id.items():
                obj = objects.get(path_id)
                if obj is None:
                    failures.append({"source_file": relative, "path_id": path_id, "reason": "saved object missing"})
                    continue
                raw = obj.get_raw_data()
                for text, count in counter.items():
                    found = raw.count(text.encode("utf-8"))
                    if found < count:
                        failures.append(
                            {
                                "source_file": relative,
                                "path_id": path_id,
                                "expected": flat(text),
                                "expected_count": count,
                                "found_count": found,
                            }
                        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--game-root",
        type=Path,
        default=None,
        help="Rebase a relocated manifest to this game root; source hashes are still required",
    )
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--translation-csv", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--allow-newline-changes", action="store_true")
    parser.add_argument(
        "--allow-raw-fallback",
        action="store_true",
        help="Allow risky fixed-span raw UTF-8 writes after container-specific review",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.game_root:
        manifest["root"] = str(args.game_root.resolve())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    selected, writeback_csv, translation_rows_used = prepare_plan(
        manifest, args.source_csv, args.translation_csv, args.out_dir, args.allow_newline_changes
    )
    verify_source_hash(manifest, selected)
    raw_selected = [item for item in selected if item.get("method") == "raw-utf8-fallback"]
    if raw_selected and not args.allow_raw_fallback:
        rows = sorted({int(item["_row"]) for item in raw_selected})
        raise SystemExit(
            "raw-utf8-fallback rows are audit-only by default; review the container and rerun "
            f"with --allow-raw-fallback to approve rows {rows[:20]}"
        )

    real_root = Path(manifest["root"]).resolve()
    relatives = selected_source_files(selected)
    backup_root = args.backup_dir or (
        args.out_dir / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    files_written: list[str] = []
    if args.dry_run or not relatives:
        backed_up: set[str] = set()
        raw_patched, raw_skipped = patch_raw(manifest, selected, backup_root, backed_up, True)
        len_patched, len_skipped, _ = patch_len_prefixed(manifest, selected, backup_root, True, backed_up)
        patched = raw_patched + len_patched
        skipped = raw_skipped + len_skipped
        validation_failures: list[dict] = []
    else:
        with tempfile.TemporaryDirectory(prefix=".writeback-stage-", dir=args.out_dir) as temporary:
            stage_root = Path(temporary) / "game-root"
            stage_root.mkdir(parents=True)
            expected_hashes = stage_source_files(real_root, stage_root, relatives)
            staged_manifest = dict(manifest)
            staged_manifest["root"] = str(stage_root)
            stage_backups = Path(temporary) / "internal-backups"
            backed_up = set()
            # Raw offsets are byte-stable; apply them before UnityPy serializes objects.
            raw_patched, raw_skipped = patch_raw(staged_manifest, selected, stage_backups, backed_up, False)
            len_patched, len_skipped, _ = patch_len_prefixed(
                staged_manifest, selected, stage_backups, False, backed_up
            )
            patched = raw_patched + len_patched
            skipped = raw_skipped + len_skipped
            validation_failures = validate_written(stage_root, patched)
            if not skipped and not validation_failures:
                files_written = commit_staged_files(
                    real_root, stage_root, relatives, expected_hashes, backup_root
                )
    result = {
        "dry_run": args.dry_run,
        "game_root": manifest.get("root"),
        "source_file": manifest.get("source_file"),
        "writeback_csv": str(writeback_csv),
        "backup_root": str(backup_root),
        "translation_rows_used": translation_rows_used,
        "selected_occurrences": len(selected),
        "patched_total": len(patched),
        "skipped": skipped,
        "validation_failures": validation_failures,
        "files_written": files_written,
    }
    (args.out_dir / "writeback_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if skipped or validation_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
