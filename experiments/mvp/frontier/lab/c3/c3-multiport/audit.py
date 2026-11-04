"""Fresh-process full-check and independent conditional graph-count audit."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from time import perf_counter

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
for path in (ROOT / 'src', ROOT / 'experiments/mvp/frontier', HERE):
    sys.path.insert(0, str(path))
from stowage.checker import check
from stowage.io import load_instance
from stowage.model import MovePlan
from graph_model import assignment_bound


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / 'results/main'
    records = []
    started = perf_counter()
    for arm in ('frontier09', 'engine', 'graph'):
        for path in sorted((output / arm).glob('*.json')):
            row = json.loads(path.read_text())
            if not row['passed']:
                continue
            instance = load_instance(ROOT / row['input'])
            plan = MovePlan.model_validate(row['plan'])
            report = check(instance, plan)
            bound = assignment_bound(instance, row['assignment'])
            restow = sum(f.restow_lifts for f in report.count.future)
            record = dict(arm=arm, id=instance.id, passed=bool(report.passed),
                          lifts=float(report.count.weighted_nonproductive),
                          conditional_restow_bound=bound['restow_lifts'], restow_later=restow,
                          conditional_hatch_bound=bound['hatch_later'],
                          hatch_later=sum(f.hatch_cover_lifts for f in report.count.future))
            record['agrees'] = (report.passed and record['lifts'] == row['lifts']
                                and bound['restow_lifts'] == restow
                                and bound['hatch_later'] == record['hatch_later'])
            records.append(record)
            (output / 'audit.json').write_text(json.dumps(dict(records=records,
                all_agree=all(r['agrees'] for r in records), wall_s=perf_counter()-started), indent=2) + '\n')
            print(f'{arm} {instance.id}: {record["agrees"]}', flush=True)
    assert records and all(r['agrees'] for r in records)


if __name__ == '__main__':
    main()
