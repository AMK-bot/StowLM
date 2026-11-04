"""Transport large numerical cache inventories as verified content-addressed files.

Only the pathname and its SHA-256 cross a process boundary. The receiver validates
and decodes a single byte read, so a replacement cannot become an admitted pin.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from eval.snapshot_io import read_snapshot

LEGACY = 'CAMPAIGN_NUMERICAL_CACHE_PIN'
PIN_PATH = 'CAMPAIGN_NUMERICAL_CACHE_PIN_PATH'
PIN_SHA256 = 'CAMPAIGN_NUMERICAL_CACHE_PIN_SHA256'


def _checked_bytes(path, expected):
    try:
        snapshot = read_snapshot(path, expected_sha256=expected)
    except OSError as exc:
        raise ValueError(f'numerical cache pin file is unreadable: {path}') from exc
    except ValueError as exc:
        raise ValueError(f'numerical cache pin file SHA-256 mismatch: {path}') from exc
    return snapshot.data


def pin_environment(pin, run_directory, environment=None):
    """Atomically publish an immutable pin and return a small child environment.

    Existing addresses are verified, never replaced. A concurrent publisher can
    win the atomic link only with bytes that pass the same digest verification.
    """
    if not isinstance(pin, dict):
        raise ValueError('numerical cache pin must be a JSON object')
    raw = (json.dumps(pin, sort_keys=True, allow_nan=False, separators=(',', ':')) + '\n').encode()
    digest = hashlib.sha256(raw).hexdigest()
    folder = Path(run_directory).resolve() / 'numerical-cache-pins'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (digest + '.json')
    if not path.exists():
        fd, temporary_name = tempfile.mkstemp(prefix='.pin-', suffix='.tmp', dir=folder)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o444)
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    _checked_bytes(path, digest)
    result = dict(os.environ if environment is None else environment)
    result.pop(LEGACY, None)
    result[PIN_PATH] = str(path)
    result[PIN_SHA256] = digest
    return result


def read_pin(environment=None):
    """Validate the referenced file's exact bytes before decoding those bytes."""
    environment = os.environ if environment is None else environment
    if LEGACY in environment:
        raise ValueError('legacy numerical cache inventory environment is forbidden; use pin file path and SHA-256')
    path, digest = environment.get(PIN_PATH), environment.get(PIN_SHA256)
    if not path or not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('frozen numerical cache pin file path and SHA-256 are required')
    raw = _checked_bytes(path, digest)
    try:
        pin = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('numerical cache pin file is not valid JSON') from exc
    if not isinstance(pin, dict):
        raise ValueError('numerical cache pin file must contain a JSON object')
    return pin
