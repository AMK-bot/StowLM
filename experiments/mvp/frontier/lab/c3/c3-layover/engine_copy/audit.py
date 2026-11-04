"""Independent re-check of stored frontier plans: fresh process, no planner code, full checker, move replay, mask replay.

Usage: audit.py <run_dir> [--workers 3]. Writes <run_dir>/AUDIT.json. A plan counts only if the stored number reproduces.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import glob
import json
import multiprocessing as mp
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[3]


def one(path):
    from stowage.actions import get_action_space
    from stowage.checker import check
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.moves import apply_plan
    row = json.load(open(path))
    out = dict(instance_id=row['instance_id'], stored=row.get('weighted'), stored_passed=row.get('passed'))
    if 'plan' not in row:
        return out
    started = time.perf_counter()
    instance = load_instance(ROOT/row['input'])
    plan = MovePlan.model_validate(row['plan'])
    report = check(instance, plan)
    replay = apply_plan(instance, plan)
    space = get_action_space('boxslot')
    actions = list(space.from_plan(instance, plan))
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={'global_screen': 0}))
    mask_ok = True
    for action in actions:
        if not engine.is_legal(action):
            mask_ok = False
            break
        engine.push(action)
    placed = {p.container_id for p in replay.final_state.ship.placements}
    out.update(passed=bool(report.passed), weighted=float(report.count.weighted_nonproductive), legal=bool(replay.legal),
               mask_ok=mask_ok, complete=mask_ok and engine.is_complete(), all_loaded=set(instance.load_list) <= placed,
               restow_now=report.count.restow_lifts, relocations=report.count.relocations,
               hatch_now=report.count.hatch_cover_lifts,
               restow_later=sum(f.restow_lifts for f in report.count.future),
               hatch_later=sum(f.hatch_cover_lifts for f in report.count.future),
               seconds=round(time.perf_counter()-started, 1))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run')
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    files = sorted(f for f in glob.glob(f'{args.run}/*.json') if not f.endswith('AUDIT.json'))
    rows = []
    with ProcessPoolExecutor(args.workers, mp_context=mp.get_context('spawn')) as pool:
        for future in as_completed([pool.submit(one, f) for f in files]):
            rows.append(future.result())
    rows.sort(key=lambda r: r['instance_id'])
    Path(args.run, 'AUDIT.json').write_text(json.dumps(rows, indent=1))
    good = [r for r in rows if r.get('passed') and r.get('legal') and r.get('complete') and r.get('all_loaded') and r.get('weighted') == r.get('stored')]
    print(f'{len(good)}/{len(rows)} reproduce: passed, legal, mask-complete, all loaded, same moves')
    for r in rows:
        if r not in good:
            print(r)
    if good:
        keys = ['relocations', 'restow_now', 'hatch_now', 'restow_later', 'hatch_later', 'weighted']
        print({k: round(sum(r[k] for r in good)/len(good), 2) for k in keys})


if __name__ == '__main__':
    main()
