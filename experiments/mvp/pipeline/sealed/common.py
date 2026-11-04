"""Dependency-free control plane; no map or model imports."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
THREAD_VARIABLES = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                    'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS',
                    'POLARS_MAX_THREADS', 'RAYON_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS')


class HarnessError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def object_hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def resolve(path):
    return (ROOT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def exclusive_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise HarnessError(f'One-run claim already exists: {path}') from exc


def cpu_environment():
    env = dict(os.environ)
    env.update({name: '1' for name in THREAD_VARIABLES})
    env.update(PYTHONDONTWRITEBYTECODE='1', UV_CACHE_DIR=str(ROOT / '.uv-cache'))
    env['PYTHONPATH'] = os.pathsep.join(str(ROOT / p) for p in
        ('src', 'experiments/mvp/frontier', 'experiments/mvp/frontier/lab', '.'))
    return env


def admission(path, *, now=None, workers=2, min_cores=None):
    """Return a fresh qualifying snapshot or None; never raise for capacity."""
    try:
        row = read_json(path)
        mask, load = row['mask'], float(row['load1'])
        age = (time.time() if now is None else now) - float(row['t'])
        if (type(workers) is not int or workers < 1 or type(mask) is not int or mask < 0
                or not workers <= mask.bit_count() - 2
                or (min_cores is not None and mask.bit_count() < min_cores)
                or not math.isfinite(load) or not 0 <= load <= workers + 2 or not 0 <= age < 120
                or row.get('other_fleet_work') != []):
            return None
        return dict(row, cores=mask.bit_count(), age_s=age)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def wait_for_admission(path=None, *, workers=2, min_cores=None, wait=time.sleep, now=time.time, poll_s=20, stop=None):
    path = resolve(path or 'experiments/fleet/core-state.json')
    while True:
        if stop is not None and stop():
            return None  # Explicit controller cancellation, never busy-capacity rejection.
        snapshot = admission(path, now=now(), workers=workers, min_cores=min_cores)
        if snapshot is not None:
            return snapshot
        wait(poll_s)
