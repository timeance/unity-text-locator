"""Read/write our own cache.json (a CacheProject serialized with msgspec), and
iterate / mutate items by translation status. The CacheProject / CacheItem types
are vendored from AiNiee under _vendor/ — no external AiNiee repo required."""
import json
import os
import tempfile
import time
import types
from contextlib import contextmanager
from pathlib import Path
import msgspec
from . import helpers
from ._vendor.ModuleFolders.Service.Cache.CacheProject import CacheProject, CacheProjectStatistics
from ._vendor.ModuleFolders.Service.Cache.CacheItem import CacheItem, TranslationStatus


def _m():
    """Back-compat shim: callers used to get these types from ainiee_lib.load()."""
    return types.SimpleNamespace(
        CacheProject=CacheProject, CacheItem=CacheItem,
        CacheProjectStatistics=CacheProjectStatistics, TranslationStatus=TranslationStatus)


def save_cache(project, path: str) -> None:
    # Ensure stats_data is never null (its field type is non-Optional in msgspec eyes)
    if project.stats_data is None:
        project.stats_data = CacheProjectStatistics()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = msgspec.json.encode(project)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as w:
            w.write(payload)
            w.flush()
            os.fsync(w.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def _cache_lock(cache_path: str, timeout: float = 10.0):
    """Portable cooperative lock using atomic O_EXCL creation."""
    lock_path = f"{cache_path}.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"cache is locked: {cache_path}")
            time.sleep(0.05)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass


def load_cache(path: str):
    with open(path, "rb") as r:
        content_bytes = r.read()
    try:
        return msgspec.json.decode(content_bytes, type=CacheProject)
    except msgspec.ValidationError:
        # CacheItem has nullable-but-non-Optional fields (text_to_detect, translated_text);
        # fall back to CacheProject.from_dict exactly as AiNiee's CacheManager does.
        content = json.loads(content_bytes.decode("utf-8"))
        return CacheProject.from_dict(content)


def iter_items(project):
    for cache_file in project.files.values():
        for item in cache_file.items:
            yield item


def _iter_by_status(project, status):
    for item in iter_items(project):
        if item.translation_status == status and (item.source_text or "").strip():
            yield item


def iter_untranslated(project):
    return _iter_by_status(project, TranslationStatus.UNTRANSLATED)


def iter_translated_unpolished(project):
    """Items eligible for a polish pass (and resume): status == TRANSLATED."""
    return _iter_by_status(project, TranslationStatus.TRANSLATED)


def _set(project, text_index: int, text: str, status) -> bool:
    for item in iter_items(project):
        if item.text_index == text_index:
            item.translated_text = text
            item.translation_status = status
            return True
    return False


def set_translation(project, text_index: int, translated_text: str) -> bool:
    return _set(project, text_index, translated_text, TranslationStatus.TRANSLATED)


def set_polish(project, text_index: int, polished_text: str) -> bool:
    """Overwrite translated_text with the polished text and mark POLISHED
    (mirrors AiNiee's PolisherTask; export reads final_text == translated_text)."""
    return _set(project, text_index, polished_text, TranslationStatus.POLISHED)


def apply_writeback(cache_path: str, items: list[dict], setter, get_text) -> int:
    """Backup, load, apply `setter(project, text_index, get_text(item))` for each
    item, save. Shared by the translate (batch) and polish write paths."""
    if not isinstance(items, list):
        raise ValueError("writeback payload must be a JSON array")
    indexes = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict) or "text_index" not in item:
            raise ValueError(f"writeback item {position} has no text_index")
        raw = item["text_index"]
        if isinstance(raw, bool):
            raise ValueError(f"invalid text_index at item {position}: {raw!r}")
        try:
            index = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid text_index at item {position}: {raw!r}") from exc
        if index <= 0 or str(raw).strip() != str(index):
            raise ValueError(f"invalid text_index at item {position}: {raw!r}")
        indexes.append(index)
    if len(indexes) != len(set(indexes)):
        raise ValueError("duplicate text_index in writeback payload")

    with _cache_lock(cache_path):
        project = load_cache(cache_path)
        cache_indexes = [int(item.text_index) for item in iter_items(project)]
        if len(cache_indexes) != len(set(cache_indexes)):
            raise ValueError("cache contains duplicate text_index values")
        applied = 0
        for item, index in zip(items, indexes):
            if setter(project, index, get_text(item)):
                applied += 1
        if applied != len(items):
            return applied
        helpers.backup_file(cache_path)
        save_cache(project, cache_path)
        return applied
