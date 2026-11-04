"""Record the real offline storyboard on a current synthetic planning input.

This is an opt-in integration smoke, not a default test or a feasibility claim.
Both planners use DemoService's normal pipeline, checker, ballast and repair.
Search limits are cooperative; a timed-out observation never claims success.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from time import monotonic, sleep

from app.backend import make_demo_plan
from app.comparison import build_comparison
from app.flow import DemoService
from app.server import json_bytes
from stowage.checker import report_matches_inputs
from stowage.io import load_instance
from stowage.model import CheckReport, Instance, MovePlan


def _write(path, value):
    path.write_bytes(json_bytes(value))


def _source_pins():
    paths = sorted({*Path('src/stowage').rglob('*.py'), *Path('src/stowage').rglob('*.json'),
                    *Path('src/stowage').rglob('*.toml'), *Path('params').rglob('*.toml'),
                    *Path('app').rglob('*.py'), *Path('app/static').glob('*')})
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--instance', type=Path)
    source.add_argument('--generate-small', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--time-limit-s', type=float, default=5.)
    parser.add_argument('--max-wait-s', type=float, default=60.)
    parser.add_argument('--primary', choices=('solver.scalable', 'solver.exact', 'baseline.twostep'), default='solver.scalable')
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()
    if not 0 < args.time_limit_s <= 30 or not 0 < args.max_wait_s <= 120:
        parser.error('Use a planning limit in (0,30] and a bounded wait in (0,120] seconds')
    args.output.mkdir(parents=True, exist_ok=True)
    # This file records this process only; never stop another session's PID.
    (args.output / 'pid').write_text(str(os.getpid()))
    started = monotonic()
    before = _source_pins()
    if args.generate_small:
        from stowage.generate.instances import generate_simulated_instance
        instance = generate_simulated_instance('small', seed=args.seed, noise=.2,
            without_dg=True, max_attempts=3)
    else:
        instance = load_instance(args.instance)
    if instance.size_class not in {'tiny', 'small'}:
        parser.error('Live UI smoke accepts tiny/small inputs')
    _write(args.output / 'instance.json', instance.model_dump(mode='json'))
    generation_s = monotonic() - started
    print(f'Input ready: {instance.id}; {len(instance.load_list)} loads; {generation_s:.3f}s', flush=True)
    initial = make_demo_plan(instance)
    _write(args.output / 'initial-plan.json', initial.model_dump(mode='json'))
    # Isolate this request from unrelated campaign discovery; the CLI/browser
    # still uses the ordinary registered-instance and live-planning service.
    service = DemoService(instance, initial, discover=False)
    job = service.start_solve(instance.id, planner=args.primary, compare=True,
        seed=args.seed, time_limit_s=args.time_limit_s)
    deadline = monotonic() + args.max_wait_s
    previous = None
    while True:
        job = service.job(job['id'])
        _write(args.output / 'job.json', job)
        if job['stage'] != previous:
            print(f"{job['elapsed_s']:.2f}s: {job['stage']}", flush=True)
            previous = job['stage']
        if job['status'] in {'done', 'error'} or monotonic() >= deadline:
            break
        sleep(.1)
    summaries = []
    replays = []
    for plan_id in job.get('plan_ids', []):
        replay = service.replay(plan_id)
        replays.append(replay)
        planner = replay['metadata']['planner']
        raw_report = replay['checker'].get('report')
        bound = bool(raw_report and report_matches_inputs(CheckReport.model_validate(raw_report),
            Instance.model_validate(replay['instance']), MovePlan.model_validate(replay['plan'])))
        phases = sorted({c['phase'] for c in replay['checker'].get('conditions', [])})
        _write(args.output / f'{planner}.result.json', replay['planner_result'])
        _write(args.output / f'{planner}.replay.json', replay)
        summaries.append(dict(planner=planner, status=replay['planner_result']['status'],
            bound=bound, phases=phases, shippable=replay['metadata']['shippable'],
            executed=replay['metadata']['executed_moves'], planned=replay['metadata']['planned_moves'],
            count=replay['frames'][-1]['counts'], blockers=replay['metadata']['shipping_blockers']))
    comparison = build_comparison(*replays) if len(replays) == 2 else None
    if comparison is not None:
        _write(args.output / 'comparison.json', comparison)
    after = _source_pins()
    drift = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    expected = {args.primary, 'solver.scalable' if args.primary == 'baseline.twostep' else 'baseline.twostep'}
    success = (job['status'] == 'done' and len(summaries) == 2
        and {r['planner'] for r in summaries} == expected
        and all(r['bound'] and {'arrival', 'departure'} <= set(r['phases']) for r in summaries)
        and not drift)
    requests_completed = (job['status'] == 'done' and
        {r['planner'] for r in job.get('results', [])} == expected)
    _write(args.output / 'summary.json', dict(storyboard_exercised=success,
        requests_completed=requests_completed, comparison_ready=success,
        physical_approval=bool(success and comparison and comparison['comparable']),
        elapsed_s=monotonic()-started, generation_s=generation_s, job=job,
        results=summaries, source_drift=drift, source_pins=after,
        notice='Actual planner outputs; only canonical shippable results approve. No browser verification.'))
    print(f'Outputs: {args.output}; storyboard_exercised={success}; elapsed={monotonic()-started:.3f}s', flush=True)
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
