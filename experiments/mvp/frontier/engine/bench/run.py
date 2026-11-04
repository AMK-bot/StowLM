"""Run the unchanged lab scorecard over bounded two-worker shards."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
FRONTIER = ENGINE.parent
ROOT = FRONTIER.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(FRONTIER / 'lab'))
from catalog import load_set


def shards(paths, count):
    if count < 1:
        raise ValueError('shards must be positive')
    if len(paths) != len(set(paths)):
        raise ValueError('duplicate map paths')
    return [paths[i::count] for i in range(min(count, len(paths)))]


def score_command(list_path, out, args):
    method = ENGINE / 'lab_method.py'
    return ['nice', '-n', '5', sys.executable, str(FRONTIER / 'lab/score.py'),
            '--set', str(list_path), '--method', f'{method}:propose',
            '--finish', f'{method}:finish', '--budget-s', str(args.budget_s),
            '--finalize-s', str(args.finalize_s), '--tries', str(args.tries),
            '--seed', str(args.seed), '--workers', '2', '--out', str(out)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--set', default='val_small', help='val_small, val_tiny, medium, a lab set, or an approved list')
    parser.add_argument('--out', required=True)
    parser.add_argument('--procs', type=int, choices=(1, 2), default=2,
                        help='scorecard parent processes; 2 workers each, at most 4 busy processes')
    parser.add_argument('--shards', type=int, default=4)
    parser.add_argument('--budget-s', type=float, default=900.)
    parser.add_argument('--finalize-s', type=float, default=600.)
    parser.add_argument('--tries', type=int, default=3)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--dry-run', action='store_true', help='validate and print commands without running solvers')
    args = parser.parse_args(argv)
    if not args.dry_run and (FRONTIER / 'lab/c1/leader/BENCH-RUNNING').exists():
        parser.error('leader benchmark is active; this two-worker launcher must wait')
    if args.shards < 1 or args.budget_s < 0 or args.finalize_s <= 0 or args.tries < 1:
        parser.error('positive shards, finalize-s and tries; nonnegative budget-s required')
    out = Path(args.out).resolve()
    if not out.is_relative_to(ENGINE.resolve()):
        parser.error('outputs must stay inside engine/')
    selection = HERE / f'{args.set}.txt' if args.set in ('val_small', 'val_tiny', 'medium') else args.set
    paths = load_set(selection)
    if not paths:
        parser.error('empty map set')
    # The shared runner hashes the adapter, but does not recurse into packages.
    # Stamp dependency changes into that file before it computes its cache key.
    sys.path.insert(0, str(FRONTIER))
    from engine.provenance import stamp_adapter
    stamp_adapter()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        env[name] = '1'
    env['PYTHONPATH'] = os.pathsep.join(str(p) for p in (ROOT / 'src', FRONTIER, FRONTIER / 'lab', ROOT))
    jobs = []
    for index, group in enumerate(shards(paths, args.shards)):
        list_path = out / f'shard-{index:03d}.txt'
        list_path.write_text(''.join(path + '\n' for path in group))
        jobs.append((index, score_command(list_path, out / f'shard-{index:03d}', args)))
    if args.dry_run:
        for _, command in jobs:
            print(json.dumps(command))
        return 0
    started = perf_counter()

    def run(job):
        index, command = job
        with (out / f'shard-{index:03d}.log').open('a') as log:
            # Only this child PID is owned here. No process-pattern cleanup is used.
            child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            print(f'shard {index}: PID {child.pid}', flush=True)
            return dict(shard=index, pid=child.pid, returncode=child.wait())

    with ThreadPoolExecutor(max_workers=args.procs) as pool:
        runs = list(pool.map(run, jobs))
    record = dict(selection=str(selection), maps=len(paths), workers_per_process=2,
                  procs=args.procs, elapsed_s=perf_counter()-started, runs=runs)
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    return int(any(row['returncode'] for row in runs))


if __name__ == '__main__':
    raise SystemExit(main())
