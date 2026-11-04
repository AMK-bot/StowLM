"""Bounded, content-addressed TOML parsing with independent mutable results.

Files are read on every call: path, size and modification time never stand in
for their contents. Only successful parses are memoised, under the SHA-256 of
the exact input bytes. Cached trees remain private; even the first caller gets
a deep copy, preserving the mutation contract of ``tomllib.load``.
"""

from __future__ import annotations

from collections import OrderedDict, namedtuple
from copy import deepcopy
from hashlib import sha256
from os import PathLike
from pathlib import Path
from threading import RLock
import tomllib
from typing import Any

_MAX_ENTRIES = 128
_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_LOCK = RLock()
_HITS = 0
_MISSES = 0
_CacheInfo = namedtuple("CacheInfo", "hits misses maxsize currsize")


def _parsed_bytes(payload: bytes) -> dict[str, Any]:
    """Return the private cached parse; public APIs copy before returning.

    Decoding and parse errors are the same as ``tomllib.load`` and are never
    cached. The lock also prevents duplicate parses for concurrent callers.
    """
    global _HITS, _MISSES
    key = sha256(payload).hexdigest()
    with _LOCK:
        if key in _CACHE:
            _HITS += 1
            parsed = _CACHE[key]
            _CACHE.move_to_end(key)
        else:
            _MISSES += 1
            parsed = tomllib.loads(payload.decode("utf-8"))
            _CACHE[key] = parsed
            while len(_CACHE) > _MAX_ENTRIES:
                _CACHE.popitem(last=False)
    return parsed


def load_bytes(payload: bytes) -> dict[str, Any]:
    """Parse UTF-8 TOML bytes, returning a fresh tree on cold and warm calls."""
    return deepcopy(_parsed_bytes(payload))


def load_value(payload: bytes, key: str, default: Any = None) -> Any:
    """Copy one top-level value from the same content-addressed parsed tree.

    This is equivalent to ``load_bytes(payload).get(key, default)`` for parsed
    values, without copying unrelated sections. A missing key returns a deep
    copy of ``default`` too, so no caller receives shared mutable cache data.
    """
    return deepcopy(_parsed_bytes(payload).get(key, default))


def load(path: str | PathLike[str]) -> dict[str, Any]:
    """Read current file bytes and parse them with content-addressed reuse."""
    return load_bytes(Path(path).read_bytes())


def clear_cache() -> None:
    """Discard this process's parsed trees and reset cache counters."""
    global _HITS, _MISSES
    with _LOCK:
        _CACHE.clear()
        _HITS = _MISSES = 0


def cache_info() -> tuple[int, int, int, int]:
    """Return hits, misses, entry limit and current entries for diagnostics."""
    with _LOCK:
        return _CacheInfo(_HITS, _MISSES, _MAX_ENTRIES, len(_CACHE))
