"""Bounded cProfile of unmodified V4 on permitted stored TRAIN V6 candidates.

Direct worker-body invocation isolates cost without subprocess profiling gaps.
A POSIX alarm is a measurement stop, never a feasibility result. No checker is
modified and no profiled result counts as a certificate without full checking.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import signal
import time

ROOT = Path(__file__).resolve().parents[6]
DEFAULT_MAP = 'medium-feeder_800-f60-high-s192200100'


def load_stored(map_id=DEFAULT_MAP, *, finished=False):
    """Read only exact TRAIN maps listed in the permitted V6 cohort manifest."""
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    run = ROOT / 'experiments/mvp/scale/runs/v6'
    rows = json.loads((run/'run.json').read_text())['cohort']['maps']
    row = next(row for row in rows if row['map_id'] == map_id)
    if row['split'] != 'train':
        raise ValueError('Only TRAIN maps are permitted')
    path = ROOT / row['input']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != row['input_sha256']:
        raise ValueError('Input hash mismatch')
    result_path = run / 'maps' / map_id / ('finished.json' if finished else 'raw.json')
    return load_instance(path), PlannerResult.model_validate_json(result_path.read_text()), row


class MeasurementStop(BaseException):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--map-id', default=DEFAULT_MAP)
    parser.add_argument('--budget-s', type=float, default=600)
    parser.add_argument('--out', type=Path, default=Path(__file__).parent/'results/profile-baseline')
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    instance, result, row = load_stored(args.map_id)
    from stowage.solver.ballast_v4 import finish
    from stowage.solver.ballast_v4_evaluator import Evaluator
    original_part = Evaluator._part
    metrics = defaultdict(lambda: dict(calls=0, seconds=0.))
    started = time.monotonic()
    active = None
    diagnostics = None

    def snapshot(event):
        payload = dict(map_id=args.map_id, elapsed_s=time.monotonic()-started,
                       event=event, active=active, parts=dict(metrics), diagnostics=diagnostics)
        args.out.with_suffix('.progress.json').write_text(json.dumps(payload, indent=2, default=str)+'\n')

    def part(self, identity, phase, inst, ship, name):
        nonlocal active, diagnostics
        diagnostics = self.diagnostics
        active = dict(phase=phase, name=name, started_s=time.monotonic()-started)
        snapshot('part_start')
        before = time.monotonic()
        report = original_part(self, identity, phase, inst, ship, name)
        metrics[phase+'/'+name]['calls'] += 1
        metrics[phase+'/'+name]['seconds'] += time.monotonic()-before
        active = None
        snapshot('part_end')
        return report

    Evaluator._part = part
    def stop(signum, frame):
        raise MeasurementStop()
    signal.signal(signal.SIGALRM, stop)
    profiler = cProfile.Profile()
    outcome = None
    termination = None
    signal.setitimer(signal.ITIMER_REAL, args.budget_s)
    try:
        profiler.enable()
        outcome = finish(instance, result.plan, time_limit_s=args.budget_s, seed=0)
        termination = outcome.status
    except MeasurementStop:
        termination = 'measurement_walltime_stop'
    except Exception as error:
        termination = f'{type(error).__name__}: {error}'
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        profiler.disable()
        Evaluator._part = original_part
    elapsed = time.monotonic()-started
    snapshot(termination)
    profiler.dump_stats(str(args.out.with_suffix('.pstats')))
    with args.out.with_suffix('.txt').open('w') as stream:
        stats = pstats.Stats(profiler, stream=stream)
        stats.strip_dirs().sort_stats('cumulative').print_stats(100)
        stats.sort_stats('tottime').print_stats(60)
    payload = dict(map_id=args.map_id, input_sha256=row['input_sha256'],
                   source='src/stowage/solver/ballast_v4.py',
                   source_sha256=hashlib.sha256((ROOT/'src/stowage/solver/ballast_v4.py').read_bytes()).hexdigest(),
                   elapsed_s=elapsed, termination=termination, parts=dict(metrics),
                   active=active, diagnostics=diagnostics,
                   outcome=outcome.model_dump(mode='json') if outcome else None)
    args.out.with_suffix('.json').write_text(json.dumps(payload, indent=2, default=str)+'\n')
    print(json.dumps({k:payload[k] for k in ('map_id','elapsed_s','termination')}, indent=2))


if __name__ == '__main__':
    main()
