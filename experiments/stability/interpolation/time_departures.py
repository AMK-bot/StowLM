"""Refresh production full-check timings without rerunning reference sampling.

Run with OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1.
The production builder calls load_vessel(name, with_hull=True). One process,
one numerical worker; the underlying native thread count is not inspected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter

from validate import FLEET, departure_timings

ROOT = Path(__file__).resolve().parent


def main():
    started = perf_counter()
    for name in FLEET:
        path = ROOT/f'validation_{name}.json'
        payload = json.loads(path.read_text())
        payload.setdefault('superseded_initial_timings', []).extend(payload['timings'])
        result = departure_timings(name)
        result['production_loader'] = 'load_vessel(name, with_hull=True)'
        result['thread_environment'] = {key: os.environ.get(key) for key in
            ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}
        payload['timings'] = [result]
        path.write_text(json.dumps(payload, indent=2)+'\n')
        print(name, 'median', result['median_seconds'], 'max', result['max_seconds'],
              'direct', result['direct_seconds'], 'table', result['metrics'].get('geometry_table_used'),
              'exact_loading', result['metrics'].get('geometry_loading_equilibrium_direct'), flush=True)
    print('elapsed_seconds', perf_counter()-started)


if __name__ == '__main__':
    main()
