"""Adapter for the frozen ladder: use native interfaces without replacing engine."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_engine():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        os.environ[key] = '1'
    os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR', str(HERE/'runtime-cache'))
    return sys.modules['engine']
