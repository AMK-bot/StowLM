"""Probe symmetric WING-F to HEEL transfers from a frozen near-pass recipe.

Code-only preparation; the parent admits/runs this process under the lab CPU
limit. A physics screen is never a pass: only unchanged full checker acceptance
stops the probe. Cargo stays the replay-legal 106-lift refine-first stow.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import os
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS'):
    os.environ[name] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
sys.dont_write_bytecode = True
sys.path[:0] = [str(HERE), str(ROOT/'src')]

from physics import PhysicsEvaluator
from stowage.checker import check
from stowage.io import load_instance
from stowage.model import MovePlan


def write(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=HERE/'results/heel-probe-corrected')
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(HERE):
        parser.error('output outside this experiment')
    out.mkdir(parents=True, exist_ok=True)
    print(json.dumps({'owned_pid': os.getpid()}), flush=True)
    # Resume only this frozen start, even if the running optimizer writes a
    # newer best recipe while the probe is in progress.
    frozen = out/'frozen-best-physics.json'
    frozen_plan = out/'frozen-base-plan.json'
    if not frozen.exists():
        best_bytes = (HERE/'results/optimize-narrow/best-physics.json').read_bytes()
        frozen.write_bytes(best_bytes)
    if not frozen_plan.exists():
        frozen_plan.write_bytes((HERE/'results/refine-first/candidate-00-plan.json').read_bytes())
    best = json.loads(frozen.read_text())
    base = {key: float(value) for key, value in best['tank_masses_kg'].items()}
    input_path = ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_500/random_yard/seed-185100000.json'
    instance = load_instance(input_path)
    plan = MovePlan.model_validate_json(frozen_plan.read_text())
    evaluator = PhysicsEvaluator(instance, plan)
    capacity = dict(zip(evaluator.tank_ids, map(float, evaluator.capacity_kg)))
    if not math.isclose(capacity['HEEL-P'], capacity['HEEL-S'], rel_tol=0., abs_tol=1e-8):
        raise ValueError('fraction drawdown requires equal HEEL P/S capacities')
    common_capacity = (capacity['HEEL-P']+capacity['HEEL-S'])/2
    write(out/'manifest.json', dict(
        input=str(input_path.relative_to(ROOT)), input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
        frozen_physics_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
        frozen_plan_sha256=hashlib.sha256(frozen_plan.read_bytes()).hexdigest(),
        source_sha256={name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                       for name in ('heel_probe.py', 'physics.py')},
        variants=[{'heel_fraction_increase': f, 'mass_neutral_from_wing_f': neutral}
                  for f, neutral in ((.03, True), (.045, True), (.06, True), (.03, False), (.045, False))],
        base_tank_masses_kg=base,
        initial_heel_mass_difference_kg=base['HEEL-P']-base['HEEL-S']))
    started = monotonic()
    summaries = []
    for fraction, neutral in ((.03, True), (.045, True), (.06, True), (.03, False), (.045, False)):
        name = ('wing-f-to-' if neutral else 'pure-')+f'heel-plus-{fraction:g}'
        target = out/(name+'.json')
        before = monotonic()
        masses = dict(base)
        # Add exactly the same mass to each equal-capacity tank, retaining
        # the raw transverse balance; optionally remove it from forward wings.
        added_each = common_capacity*fraction
        for side in ('P', 'S'):
            masses['HEEL-'+side] += added_each
            if neutral:
                masses['WING-F-'+side] -= added_each
        if any(value < 0. or value > capacity[key] for key, value in masses.items()):
            raise ValueError('transfer exceeds available tank capacity/contents')
        if target.exists():
            row = json.loads(target.read_text())
        else:
            result = evaluator.evaluate(masses)
            candidate = evaluator.candidate_plan(masses)
            write(out/(name+'-plan.json'), candidate.model_dump(mode='json'))
            row = dict(name=name, heel_fraction_increase=fraction, added_each_kg=added_each,
                       mass_neutral_from_wing_f=neutral,
                       mass_delta_kg=sum(masses.values())-sum(base.values()),
                       heel_mass_difference_kg=masses['HEEL-P']-masses['HEEL-S'],
                       physics=result, full_checker_passed=False,
                       full_checker_ran=False, full_checker_s=0., lifts=None)
            if result['physics_feasible']:
                check_started = monotonic()
                report = check(instance, candidate)
                row.update(full_checker_ran=True, full_checker_passed=report.passed,
                           full_checker_s=monotonic()-check_started,
                           full_checker_report=report.model_dump(mode='json'),
                           lifts=float(report.count.weighted_nonproductive) if report.passed else None)
            row['wall_s'] = monotonic()-before
            write(target, row)
        summary = dict(name=name, heel_fraction_increase=fraction,
            wall_s=row['wall_s'], physics_feasible=row['physics']['physics_feasible'],
            full_checker_ran=row['full_checker_ran'], full_checker_passed=row['full_checker_passed'],
            lifts=row['lifts'],
            negative_margins={key: value for key, value in row['physics']['raw_margins'].items() if value < 0.})
        summaries.append(summary)
        write(out/'summary.json', dict(rows=summaries, wall_s=monotonic()-started,
                                      full_checker_passes=sum(r['full_checker_passed'] for r in summaries)))
        print(json.dumps(summary), flush=True)
        if row['full_checker_passed']:
            break


if __name__ == '__main__':
    main()
