"""CPU admission without importing a training or GPU module."""
import json
from pathlib import Path
import time

STATE = Path(__file__).resolve().parents[3] / 'fleet/core-state.json'


def wait_for_capacity():
    while True:
        try:
            state = json.loads(STATE.read_text())
            age = time.time() - float(state['t'])
            mask = state['mask']
            mask = int(mask, 0) if isinstance(mask, str) else int(mask)
            if 0 <= age < 120 and mask.bit_count() >= 8 and 0 <= float(state['load1']) <= 45:
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        print('Waiting for fresh CPU admission (8 cores, load <=45).', flush=True)
        time.sleep(10)
