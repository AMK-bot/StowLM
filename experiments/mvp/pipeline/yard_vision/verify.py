"""Wait for fleet CPU admission, then verify this folder only."""
import json
from pathlib import Path
import subprocess
import time

from .freeze import HERE, ROOT


def admitted():
    try:
        state = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
        return (int(state['mask']).bit_count() >= 8 and state['load1'] <= 45
                and 0 <= time.time() - state['t'] < 120)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify():
    announced = False
    while not admitted():
        if not announced:
            print('Waiting for fleet CPU admission (mask >=8, load <=45, age <120s).', flush=True)
            announced = True
        time.sleep(5)
    print('Fleet CPU admission granted.', flush=True)
    result = subprocess.run(['uv', 'run', '--offline', '--no-sync', 'pytest',
                             'experiments/mvp/pipeline/yard_vision'], cwd=ROOT, capture_output=True, text=True)
    (HERE / 'verification.txt').write_text(result.stdout + result.stderr)
    print(result.stdout + result.stderr, flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(verify())
