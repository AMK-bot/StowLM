"""Read fleet capacity without reserving cores; unavailable capacity means wait."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time


def admission(path: str | Path, now: float | None = None) -> dict:
    """Return a JSON-able capacity reading; bad or stale readings refuse admission."""
    result = {'admitted': False, 'reason': '', 'cores': None, 'load1': None, 'age_s': None}
    try:
        state = json.loads(Path(path).read_text())
        mask = state['mask']
        if isinstance(mask, str):
            mask = int(mask, 16) if mask.lower().startswith('0x') else int(mask)
        if isinstance(mask, bool) or not isinstance(mask, int) or mask < 0:
            raise ValueError('mask must be a nonnegative integer')
        if isinstance(state['load1'], bool) or isinstance(state['t'], bool):
            raise ValueError('load1 and t must be numbers')
        load = float(state['load1'])
        timestamp = float(state['t'])
        clock = time.time() if now is None else float(now)
        if not all(math.isfinite(x) for x in (load, timestamp, clock)) or load < 0:
            raise ValueError('invalid load or timestamp')
        age = clock - timestamp
        result.update(cores=mask.bit_count(), load1=load, age_s=age)
        reasons = []
        if result['cores'] < 8:
            reasons.append('fewer than 8 cores')
        if load > 45:
            reasons.append('load1 above 45')
        if not 0 <= age < 120:
            reasons.append('reading is stale or in the future')
        result.update(admitted=not reasons, reason='; '.join(reasons))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        result['reason'] = f'capacity unavailable: {error}'
    return result


def wait_for_admission(path: str | Path, interval: float = 10) -> dict:
    while True:
        result = admission(path)
        if result['admitted']:
            return result
        print(json.dumps(result, sort_keys=True), flush=True)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default='experiments/fleet/core-state.json')
    parser.add_argument('--now', type=float)
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--interval', type=float, default=10)
    args = parser.parse_args()
    if args.interval <= 0 or not math.isfinite(args.interval):
        parser.error('--interval must be positive and finite')
    if args.wait and args.now is not None:
        parser.error('--now is for a one-shot check only')
    result = wait_for_admission(args.state, args.interval) if args.wait else admission(args.state, args.now)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
