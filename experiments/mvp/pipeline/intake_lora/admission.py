"""Wait for fleet CPU capacity; this module never claims a GPU slot."""
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
THREAD_ENV = {
    'PYTHONDONTWRITEBYTECODE': '1', 'TOKENIZERS_PARALLELISM': 'false',
    'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
    'VECLIB_MAXIMUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1', 'RAYON_NUM_THREADS': '1',
}


def admit(path=STATE, now=None):
    """Missing, malformed, stale and denied state all mean wait, never raise."""
    try:
        state = json.loads(Path(path).read_text())
        stamp = float(state['t'])
        mask = state['mask']
        mask = int(mask, 0) if isinstance(mask, str) else mask
        load = float(state['load1'])
        age = (time.time() if now is None else now) - stamp
        if (type(mask) is not int or mask <= 0 or mask.bit_count() < 8
                or not 0 <= age < 120 or not 0 <= load <= 45):
            return None
        return {'mask': mask, 'load1': load, 'age_seconds': age, 'workers': 1,
                'admitted_utc': datetime.now(timezone.utc).isoformat()}
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def wait_admit(path=STATE, *, poll_seconds=5, sleep=time.sleep, clock=time.time):
    while True:
        evidence = admit(path, now=clock())
        if evidence is not None:
            return evidence
        sleep(poll_seconds)


def environment():
    return {**os.environ, **THREAD_ENV, 'UV_CACHE_DIR': str(ROOT / '.uv-cache')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=STATE)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    evidence = wait_admit(args.state)
    if not args.command:
        print(json.dumps(evidence))
        return
    command = args.command[1:] if args.command[0] == '--' else args.command
    # subprocess.run's timeout stops only its exact owned child.
    subprocess.run(command, cwd=ROOT, env=environment(), timeout=args.timeout, check=True)


if __name__ == '__main__':
    main()
