"""Optional process coalescing for expensive disposable vessel-cache misses.

A per-target advisory file lock lets one process populate an entry while peers
wait without doing the same build. Callers must re-read and validate the entry
after acquisition. This supplies no cache or safety verdict of its own.

Locks are opt-in with ``STOWAGE_VESSEL_SINGLEFLIGHT=1``. An unavailable lock
falls back to the caller's ordinary cache/build behavior. Lock files stay in
place: unlinking a lock could let different processes lock different inodes.
The OS releases an acquired lock when its descriptor closes or process exits.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator


def lock_path(target: Path) -> Path:
    """Return the stable lock name for one already content-addressed target."""
    target = Path(target)
    return target.with_name("." + target.name + ".lock")


@contextmanager
def cache_singleflight(target: Path, *, enabled: bool | None = None) -> Iterator[bool]:
    """Yield whether a per-target exclusive lock was acquired.

    Cache/source validation belongs to the caller. Only lock-setup failures
    fall back; exceptions from the guarded builder propagate unchanged.
    """
    if enabled is None:
        enabled = os.environ.get("STOWAGE_VESSEL_SINGLEFLIGHT", "0").lower() in {
            "1", "true", "on",
        }
    if not enabled:
        yield False
        return
    descriptor = None
    try:
        import fcntl

        path = lock_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except (ImportError, OSError):
        if descriptor is not None:
            os.close(descriptor)
        yield False
        return
    except BaseException:
        # Cancellation is not an optional-cache failure. Release a descriptor
        # opened before an interrupted blocking flock, then preserve the signal.
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    try:
        yield True
    finally:
        # Closing releases flock even if the guarded build raised. Keep the
        # stable lock inode for waiters and later processes.
        os.close(descriptor)
