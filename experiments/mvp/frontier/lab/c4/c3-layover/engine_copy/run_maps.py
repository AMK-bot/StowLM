"""Run the frontier planner on a list of maps. One JSON per map with the finished plan stored; resumable.

Usage: run_maps.py <list.txt> <out_dir> [--search-s 120] [--finalize-s 120] [--workers 2] [--seed 0]
Lists hold repo-relative instance paths. Validation maps were used to find the rules this planner had to learn (see
README), so validation numbers are development numbers. Test maps are read-only and are the clean measure.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))


def source_sha256():
    """Everything the planner runs on outside ``src/``, so a row shows which code produced it."""
    strategy = HERE.parent/'strategy'
    digest = hashlib.sha256()
    for name in ('alns.py', 'blocked.py', 'blocks.py', 'columns.py', 'master.py', 'planner.py', 'sequence.py', 'sight.py'):
        digest.update((HERE/name).read_bytes())
    for path in (strategy/'compact.py', strategy/'blockrule/rule.py', strategy/'blockrule/capacity_candidate.py', strategy/'headroom/layout.py'):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def one(job):
    from stowage.io import load_instance
    from planner import plan_and_check, NAME, VERSION
    rel, out_dir, search_s, finalize_s, seed = job
    instance = load_instance(ROOT/rel)
    target = Path(out_dir)/f'{instance.id}.json'
    if target.exists():
        return instance.id, 'cached'
    started = time.perf_counter()
    row = dict(instance_id=instance.id, input=rel, method=NAME, version=VERSION, n_boxes=len(instance.load_list),
               input_sha256=hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(), source_sha256=source_sha256(),
               search_s=search_s, finalize_s=finalize_s, seed=seed)
    try:
        # Search time scales with the call: 6 s per box, capped by --search-s.
        search_s = min(search_s, max(20., 6.*len(instance.load_list)))
        result, record = plan_and_check(instance, search_s=search_s, finalize_s=finalize_s, seed=seed)
        row.update(record)
        row['passed'] = result is not None
        if result is not None:
            row['weighted'] = next(t for t in reversed(record['tries']) if t['passed'])['weighted']
            row['plan'] = json.loads(result.plan.model_dump_json())
    except Exception as error:
        row.update(passed=False, error=f'{type(error).__name__}: {error}')
    row['wall_s'] = round(time.perf_counter()-started, 1)
    target.write_text(json.dumps(row, indent=1))
    return instance.id, row.get('weighted'), row.get('twostep_objective'), row['wall_s']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('list')
    parser.add_argument('out')
    parser.add_argument('--search-s', type=float, default=120.)
    parser.add_argument('--finalize-s', type=float, default=120.)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    paths = [p for p in Path(args.list).read_text().split() if p and not p.endswith('.audit.json')]
    jobs = [(p, str(out), args.search_s, args.finalize_s, args.seed) for p in paths]
    # Pool workers are daemonic and the finisher starts its own processes, so use an executor.
    with ProcessPoolExecutor(args.workers, mp_context=mp.get_context('spawn'), max_tasks_per_child=1) as pool:
        for future in as_completed([pool.submit(one, job) for job in jobs]):
            print(future.result(), flush=True)


if __name__ == '__main__':
    main()
