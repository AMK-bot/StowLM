"""Write measured end-of-campaign counts after the detached supervisor exits."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import time

import build


def report(state):
    counts = Counter()
    times = defaultdict(list)
    rows = Counter()
    accepted = True
    for split in ('test', 'showcase', 'train', 'val'):
        for path in (build.CORPUS/split).rglob('*.audit.json'):
            value = json.loads(path.read_text())
            spec = value['config']
            rung = spec.get('rung', spec.get('size'))
            counts[split] += 1
            rows[(split, spec['vessel'], rung, spec['generator'])] += 1
            times[rung].append(value['elapsed_generation_s'])
            accepted &= value['accepted'] and not value['rule_freeze_v1_enabled']
            if value.get('background_gate_required', value.get('background_checks', 0) > 0):
                accepted &= value['background_last']['accepted']
    return dict(at_utc=datetime.now(timezone.utc).isoformat(), supervisor=state,
        counts=dict(counts), all_recorded_producer_and_background_gates_pass=bool(accepted),
        plan_certification=False, rows=[dict(split=k[0], ship=k[1], rung=k[2], family=k[3], emitted=v)
                                        for k,v in sorted(rows.items())],
        generation_seconds_by_rung={r:dict(count=len(v),mean=sum(v)/len(v),total=sum(v)) for r,v in times.items()},
        final_freeze_verified=state.get('phase')=='complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--runtime', type=Path, default=Path(__file__).with_name('d84-runtime.json'))
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('D84-FINAL-REPORT.json'))
    args = parser.parse_args()
    tools = Path(__file__).resolve().parent
    reporter_name = args.runtime.stem.replace('-runtime', '') + '-reporter-runtime.json'
    build.atomic_json(tools/reporter_name, dict(pid=os.getpid(), watch=args.watch))
    while True:
        state = json.loads(args.runtime.read_text())
        if not args.watch or state.get('phase') in {'complete', 'failed'}:
            break
        time.sleep(30)
    result = report(state)
    build.atomic_json(args.output, result)
    print(json.dumps({k:v for k,v in result.items() if k not in {'rows','supervisor'}}), flush=True)


if __name__ == '__main__':
    main()
