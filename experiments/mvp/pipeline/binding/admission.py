"""Fleet-owned CPU admission. Missing/stale telemetry means wait, never crash."""
from __future__ import annotations

import json
from pathlib import Path
import time

STATE = Path(__file__).resolve().parents[3] / 'fleet' / 'core-state.json'


def wait_for_capacity(*, state_path=None, poll_s=2.):
    started = time.monotonic()
    while True:
        try:
            row = json.loads(Path(state_path or STATE).read_text())
            mask = row['mask']
            mask = int(mask, 0) if isinstance(mask, str) else int(mask)
            age = time.time() - float(row['t'])
            if mask >= 0 and mask.bit_count() >= 8 and float(row['load1']) <= 45 and 0 <= age < 120:
                return dict(mask=mask, enabled_cores=mask.bit_count(), load1=float(row['load1']),
                            reading_age_s=age, waited_s=time.monotonic()-started)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        time.sleep(poll_s)
