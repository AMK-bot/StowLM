"""Wait for the fleet's CPU admission signal before local test/scoring work."""
import json
from pathlib import Path
import time


def wait_for_slot():
    path = Path('experiments/fleet/core-state.json')
    while True:
        try:
            state = json.loads(path.read_text())
            if int(state['mask']).bit_count() >= 8 and state['load1'] <= 45 and 0 <= time.time() - state['t'] < 120:
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(5)
