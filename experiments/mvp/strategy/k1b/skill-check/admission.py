"""Fail-closed admission from the leader's fresh, sanctioned core-state file."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import time

DEFAULT_PATH = Path(__file__).resolve().parents[5] / 'experiments/fleet/core-state.json'
MAX_AGE_S = 120
POLICY = 'D206'
MIN_CORES = 8
MAX_LOAD1 = 45


def numeric(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _assess(payload, read_at, source):
    row = dict(source=str(source), source_payload=payload, read_at=read_at,
               measured_at=datetime.fromtimestamp(read_at, timezone.utc).isoformat(),
               t=None, load1=None, core_mask=None, core_count=None, age_s=None, errors=[], admitted=False,
               policy=POLICY, min_cores=MIN_CORES, max_load1=MAX_LOAD1)
    if not isinstance(payload, dict):
        row['errors'].append('core-state payload must be an object')
        return row
    row.update(t=payload.get('t'), load1=payload.get('load1'), core_mask=payload.get('mask'))
    if not numeric(row['t']):
        row['errors'].append('t must be a finite numeric UNIX timestamp')
    else:
        row['age_s'] = read_at - row['t']
        if row['t'] < 0:
            row['errors'].append('t must be nonnegative')
        if row['age_s'] < 0:
            row['errors'].append('core-state timestamp is in the future')
        elif row['age_s'] >= MAX_AGE_S:
            row['errors'].append('core-state is stale: age must be < 120 seconds')
    if not numeric(row['load1']) or row['load1'] < 0:
        row['errors'].append('load1 must be finite and nonnegative')
    elif row['load1'] > MAX_LOAD1:
        row['errors'].append('load1 exceeds 45')
    if not numeric(row['core_mask']) or row['core_mask'] < 0 or row['core_mask'] != int(row['core_mask']):
        row['errors'].append('mask must be a finite nonnegative integer')
    else:
        row['core_count'] = int(row['core_mask']).bit_count()
        if row['core_count'] < MIN_CORES:
            row['errors'].append('mask has fewer than 8 recommended cores')
    row['admitted'] = not row['errors']
    return row


def probe(path=DEFAULT_PATH, *, now_s=None):
    """Read the source once; no unavailable or old measurement grants admission."""
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeError) as error:
        read_at = time.time() if now_s is None else now_s
        row = _assess(None, read_at, path)
        row['errors'] = [f'core-state read failed: {type(error).__name__}: {error}']
        return row
    return _assess(payload, time.time() if now_s is None else now_s, path)


def validate_snapshot(snapshot, *, now_s=None):
    """Recheck exact released evidence at actual trial start, without replacing it."""
    read_at = time.time() if now_s is None else now_s
    row = _assess(snapshot.get('source_payload'), read_at, snapshot.get('source', DEFAULT_PATH))
    row['released_read_at'] = snapshot.get('read_at')
    row['released_age_s'] = snapshot.get('age_s')
    if snapshot.get('admitted') is not True:
        row['errors'].append('released admission was not admitted')
        row['admitted'] = False
    return row
