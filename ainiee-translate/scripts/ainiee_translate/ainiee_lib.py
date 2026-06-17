"""Optional AiNiee fallback loader.

When AINIEE_REPO is set, put it on sys.path and import its headless
Domain/Cache types. Self-contained formats do not need this module.
"""
import os
import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class AiNiee:
    FileReader: Any
    FileOutputer: Any
    CacheManager: Any
    CacheProject: Any
    CacheItem: Any
    TranslationStatus: Any


def repo_path() -> str:
    repo = os.environ.get("AINIEE_REPO")
    if not repo:
        raise RuntimeError("AINIEE_REPO is required only for PDF/Office fallback formats.")
    return repo


def load() -> AiNiee:
    repo = repo_path()
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from ModuleFolders.Domain.FileReader.FileReader import FileReader
    from ModuleFolders.Domain.FileOutputer.FileOutputer import FileOutputer
    from ModuleFolders.Service.Cache.CacheManager import CacheManager
    from ModuleFolders.Service.Cache.CacheProject import CacheProject
    from ModuleFolders.Service.Cache.CacheItem import CacheItem, TranslationStatus
    return AiNiee(FileReader, FileOutputer, CacheManager, CacheProject, CacheItem, TranslationStatus)
