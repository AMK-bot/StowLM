"""Re-run preserved fresh slices after a search change, without changing stows."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import os
import time
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
from slices import bounded_full_check


def main():
    from stowage.io import load_instance, load_plan, save_plan
    from stowage.solver.ballast import finish_ballast
    from stowage.solver.ballast_deadline import shutdown_worker
    from stowage.versions import component_versions
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent/'slices_results')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=float, default=10.)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for source in sorted(args.source.glob('*/input-plan.json')):
        instance = load_instance(source.parent/'instance.json')
        plan = load_plan(source)
        folder = args.out/source.parent.name
        folder.mkdir(parents=True, exist_ok=True)
        metadata = source.parent/'result.json'
        row = json.loads(metadata.read_text()) if metadata.is_file() else {}
        row.update(id=row.get('id', source.parent.name), source=str(source.parent), calls=[],
                   input_components=row.get('components'), run_components=component_versions(),
                   limit_s=args.limit)
        completed = []
        for repeat in range(2):
            start = time.perf_counter()
            result = finish_ballast(instance, plan, time_limit_s=args.limit, seed=0)
            call = dict(repeat=repeat, warm=bool(repeat), wall_s=time.perf_counter()-start,
                        outcome=result.model_dump(mode='json', exclude={'plan'}))
            if result.plan is not None:
                save_plan(result.plan, folder/f'finished-{repeat}.json')
                completed.append((repeat, result.plan))
            row['calls'].append(call)
            (folder/'result.json').write_text(json.dumps(row, indent=2)+'\n')
            print(json.dumps(dict(id=row['id'], repeat=repeat, status=result.status, wall_s=call['wall_s'])), flush=True)
        for repeat, final in completed:
            checked = bounded_full_check(instance, final)
            row['calls'][repeat].update(full_checker=checked, full_checker_passed=checked['passed'], full_checker_s=checked['wall_s'])
        shutdown_worker()
        (folder/'result.json').write_text(json.dumps(row, indent=2)+'\n')
        rows.append(row)
        (args.out/'results.json').write_text(json.dumps(rows, indent=2)+'\n')


if __name__ == '__main__':
    main()
