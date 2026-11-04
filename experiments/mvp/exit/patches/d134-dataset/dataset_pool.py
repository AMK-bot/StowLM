"""D134 bounded, ordered CPU replay pool; no model weights or GPU imports."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ProcessPoolExecutor
import math
import multiprocessing
import os

_NATIVE_THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                   'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'TOKENIZERS_PARALLELISM',
                   'STOWAGE_IN_PORT_WORKERS')
_TOKENIZER = None


def worker_tokenizer(explicit=None):
    """Load one CPU tokenizer per process, reusing it across all admission groups."""
    global _TOKENIZER
    if explicit is not None:
        return explicit
    if _TOKENIZER is None:
        from llm.stowlm.format import load_tokenizer
        _TOKENIZER = load_tokenizer()
    return _TOKENIZER


@contextmanager
def ordered_pool(workers):
    """Spawn instead of inheriting a resident Metal model; map preserves row order.

    Environment is set *before spawn*, so native libraries imported by Python's
    child bootstrap already see a one-thread budget. It is restored after join.
    The main process only loads dataset/serialization code, never model weights.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError('dataset workers must be a positive integer')
    previous = {name: os.environ.get(name) for name in _NATIVE_THREADS}
    try:
        for name in _NATIVE_THREADS:
            os.environ[name] = 'false' if name == 'TOKENIZERS_PARALLELISM' else '1'
        if workers == 1:
            yield lambda function, jobs: list(map(function, jobs))
        else:
            with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context('spawn')) as pool:
                yield lambda function, jobs: list(pool.map(function, jobs, chunksize=1))
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def choose_workers(args):
    """Spend only already reserved cores/RAM and currently free fleet capacity.

    No lease increase is implicit: an active generation reservation owns this
    pool, and must reserve host + replay workers before workers can be >1.
    Unknown/malformed capacity falls back to the existing serial implementation.
    """
    requested = getattr(args, 'dataset_workers', 0)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
        raise ValueError('dataset workers must be zero (auto) or a positive integer')
    cap = requested or min(6, max(1, getattr(args, 'workers', 1)))
    allocation = getattr(args, '_reservation', None)
    result = dict(workers=1, requested=requested, native_threads=1,
                  policy='existing lease and free ledger capacity; unknown capacity => serial')
    if allocation is None or not getattr(allocation, 'active', False):
        return {**result, 'reason':'no active loop reservation'}
    try:
        from llm.exit.resources import background_cores
        background = background_cores()
        if background is None:
            raise ValueError('nonintegral ledger cores')
        # The existing GPU-generation lease budgets 64 GB for the resident model
        # plus 2 GB per CPU replay worker, same as the verification reservation.
        lease_cores = max(1, math.floor(allocation.cores)-1)
        lease_ram = max(1, math.floor((allocation.ram_gb-64)/2))
        fleet_cores = max(1, math.floor(16-background)-1)
        workers = max(1, min(cap, lease_cores, lease_ram, fleet_cores))
        return {**result, 'workers':workers, 'background_cores':background,
                'lease_cores':allocation.cores, 'lease_ram_gb':allocation.ram_gb,
                'worker_caps':dict(requested=cap, lease_cores=lease_cores,
                                   lease_ram=lease_ram, fleet_cores=fleet_cores)}
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as error:
        return {**result, 'reason':f'capacity unavailable: {type(error).__name__}: {error}'}
