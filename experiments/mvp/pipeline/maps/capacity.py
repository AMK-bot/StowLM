"""Wait for D206 admission; never control another session's processes."""
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

def wait_for_slot():
    while True:
        try:
            state = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
            age = time.time() - state['t']
            mask = int(state['mask'], 0) if isinstance(state['mask'], str) else int(state['mask'])
            if mask.bit_count() >= 8 and state['load1'] <= 45 and 0 <= age < 120:
                return dict(state, age_s=age)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        print('Waiting for fresh fleet capacity: >=8 cores, load1<=45.', flush=True)
        time.sleep(5)
