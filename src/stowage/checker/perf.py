"""Scoped numerical reuse controls; reports and canonical validation are never cached.

The reference context bypasses six optional numerical caches: interpolation
stencils, base submerged integration, strength roots, visibility intersections,
strength cuts and slot syntax. Canonical projection/baseline resolution and the
parsed-parameter LRU are shared. Context variables isolate concurrent calls.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar


_ENABLED = ContextVar("checker_numerical_reuse_enabled", default=True)
_CACHE = ContextVar("checker_evaluation_cache", default=None)


def reuse_enabled() -> bool:
    return _ENABLED.get()


def current_cache() -> dict | None:
    return _CACHE.get() if reuse_enabled() else None


@contextmanager
def evaluation_cache():
    """Give one full evaluation a fresh, disposable numerical workspace."""
    token = _CACHE.set({} if reuse_enabled() else None)
    try:
        yield
    finally:
        _CACHE.reset(token)


@contextmanager
def without_reuse():
    """Bypass six numerical caches; retain shared canonical/parsing paths."""
    token = _ENABLED.set(False)
    try:
        yield
    finally:
        _ENABLED.reset(token)
