"""
core/cache.py — Compatibility cache for Black Flag.

Stores the result of a successful `adapt` run keyed by the sha256 of the
source directory's content. This prevents re-running the full Docker matrix
on unchanged sources.

Cache location: ~/.cache/blackflag/cache.json
Cache entry: sha256 → {manifest_path, score, targets, created_at}
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


_CACHE_DIR = Path.home() / ".cache" / "blackflag"
_CACHE_FILE = _CACHE_DIR / "cache.json"

# Directories and file patterns to exclude when computing the hash
_HASH_SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "bf_scripts",
    ".tox", "venv", ".venv", "env",
}
_HASH_SKIP_EXTS = {".pyc", ".pyo", ".patch"}


def compute_source_hash(source_dir: Path) -> str:
    """
    Compute a stable sha256 hash of all trackable files in *source_dir*.

    Files are hashed in sorted order to ensure determinism.
    Returns a string of the form "sha256:<hex>".
    """
    hasher = hashlib.sha256()
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(skip in path.parts for skip in _HASH_SKIP_DIRS):
            continue
        if path.suffix.lower() in _HASH_SKIP_EXTS:
            continue
        # Hash the relative path as well so renames invalidate the cache
        hasher.update(str(path.relative_to(source_dir)).encode())
        hasher.update(path.read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(data: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def cache_get(cache_key: str) -> dict | None:
    """Return cached entry for *cache_key*, or None if not found."""
    return _load_cache().get(cache_key)


def cache_set(cache_key: str, entry: dict) -> None:
    """Store *entry* under *cache_key*."""
    data = _load_cache()
    data[cache_key] = entry
    _save_cache(data)


def cache_clear(cache_key: str | None = None) -> int:
    """
    Remove cache entries.
    If *cache_key* is given, remove only that entry.
    Otherwise clear the entire cache.
    Returns the number of entries removed.
    """
    data = _load_cache()
    if cache_key:
        if cache_key in data:
            del data[cache_key]
            _save_cache(data)
            return 1
        return 0
    count = len(data)
    _save_cache({})
    return count


def cache_list() -> list[dict]:
    """Return all cache entries as a list of dicts with the key included."""
    data = _load_cache()
    return [{"key": k, **v} for k, v in data.items()]
