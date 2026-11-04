"""Fresh fleet admission for one-worker CPU preparation; never claims a GPU slot."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
STATE = ROOT / 'experiments/fleet/core-state.json'


def admit(path=STATE, now=None):
    state = json.loads(Path(path).read_text())
    age = (time.time() if now is None else now) - state['t']
    if (type(state['mask']) is not int or state['mask'] < 0
            or state['mask'].bit_count() < 8 or not 0 <= age < 120
            or not 0 <= state['load1'] <= 45):
        raise RuntimeError('Fleet admission denied: need >=8 mask cores, load1 <=45, age <120s')
    return {**state, 'age_seconds': round(age, 3), 'workers': 1,
            'admitted_utc': datetime.now(timezone.utc).isoformat()}


def wait_admit(max_wait=0):
    deadline = time.monotonic() + max_wait
    while True:
        try:
            return admit()
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(5, max(0, deadline-time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--wait-seconds', type=float, default=0,
                        help='Wait for admission without consuming a compute worker')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    evidence = wait_admit(args.wait_seconds)
    with (HERE / 'admission.jsonl').open('a') as out:
        out.write(json.dumps(evidence) + '\n')
    if args.command:
        command = args.command[1:] if args.command[0] == '--' else args.command
        env = dict(os.environ, UV_CACHE_DIR=str(ROOT / '.uv-cache'),
                   PYTHONDONTWRITEBYTECODE='1', TOKENIZERS_PARALLELISM='false',
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   VECLIB_MAXIMUM_THREADS='1', NUMEXPR_NUM_THREADS='1',
                   RAYON_NUM_THREADS='1')
        # subprocess.run terminates only its own exact child on timeout.
        subprocess.run(command, env=env, check=True, timeout=args.timeout)
    else:
        print(json.dumps(evidence))


if __name__ == '__main__':
    main()
