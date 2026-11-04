"""Symmetric APT drawdown with exact forward-draft compensation.

Use native direct loading/equilibrium only for the cheap scalar root. Every
resulting recipe receives ordinary complete three-phase stability/securing;
only unchanged full-checker acceptance counts as a pass. No rules are changed.
"""
from pathlib import Path
import hashlib
import json
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

from scipy.optimize import brentq
from physics import PhysicsEvaluator
from stowage.checker import check, stability
from stowage.checker.stability_loading import loading_condition, liquid_cg
from stowage.checker.stability_equilibrium import floating_equilibrium
from stowage.io import load_instance
from stowage.model import MovePlan


def write(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    temporary.replace(path)


def main():
    print(json.dumps({'owned_pid': os.getpid()}), flush=True)
    out = HERE/'results/apt-probe'
    out.mkdir(parents=True, exist_ok=True)
    source = HERE/'results/heel-probe-corrected'
    frozen = out/'frozen-best-physics.json'
    frozen_plan = out/'frozen-base-plan.json'
    if not frozen.exists():
        frozen.write_bytes((source/'frozen-best-physics.json').read_bytes())
    if not frozen_plan.exists():
        frozen_plan.write_bytes((source/'frozen-base-plan.json').read_bytes())
    base = json.loads(frozen.read_text())['tank_masses_kg']
    input_path = ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_500/random_yard/seed-185100000.json'
    instance = load_instance(input_path)
    plan = MovePlan.model_validate_json(frozen_plan.read_text())
    evaluator = PhysicsEvaluator(instance, plan)
    capacity = dict(zip(evaluator.tank_ids, map(float, evaluator.capacity_kg)))
    geometry, params = stability._context(instance.vessel)
    method = int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1'))
    target_forward = min(instance.vessel.hull.max_draught_m,
                         instance.port.max_draught_m or instance.vessel.hull.max_draught_m)-.0003
    root_calls = 0

    def forward_draft(masses):
        nonlocal root_calls
        root_calls += 1
        ship = evaluator.ship.model_copy(update={'tank_masses_kg': {
            **evaluator.fixed_masses_kg, **masses}})
        condition = loading_condition(instance, ship, geometry=geometry, ship_params=params,
            free_surface_method=method, precomputed_soundings=True, small_tank_exemption=True)
        if condition.errors:
            raise ValueError('; '.join(condition.errors))
        if method == 1:
            equilibrium = floating_equilibrium(geometry, condition,
                instance.port.water_density_kg_m3, instance.vessel.hull.lpp_m, liquid_cg)
        elif method == 2:
            equilibrium = geometry.equilibrium(condition.mass_kg, condition.lcg_m,
                condition.tcg_m, condition.kg_m+condition.fsc_m, instance.port.water_density_kg_m3)
        else:
            raise ValueError('unknown free-surface method')
        return float(equilibrium.t_fwd_m)

    write(out/'manifest.json', dict(input=str(input_path.relative_to(ROOT)),
        input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
        frozen_physics_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
        frozen_plan_sha256=hashlib.sha256(frozen_plan.read_bytes()).hexdigest(),
        source_sha256={name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                       for name in ('apt_probe.py', 'physics.py')},
        target_forward_draft_m=target_forward,
        base_forward_draft_m=forward_draft(base), drawdown_fractions=[.002, .004, .008]))
    summaries = []
    for fraction in (.002, .004, .008):
        name = f'apt-minus-{fraction:g}'
        target = out/(name+'.json')
        if target.exists():
            row = json.loads(target.read_text())
        else:
            before = monotonic(); first_calls = root_calls
            masses = dict(base)
            for side in ('P', 'S'):
                masses['APT-'+side] -= fraction*capacity['APT-'+side]
            if min(masses['APT-P'], masses['APT-S']) < 0.:
                raise ValueError('APT drawdown exceeds available contents')
            before_p, before_s = masses['FWD-DEEP-P'], masses['FWD-DEEP-S']

            def compensate(removed_each):
                trial = dict(masses)
                trial['FWD-DEEP-P'] = before_p-removed_each
                trial['FWD-DEEP-S'] = before_s-removed_each
                return trial

            def residual(removed_each):
                return forward_draft(compensate(removed_each))-target_forward

            zero = residual(0.)
            removed_each = (brentq(residual, 0., min(before_p, before_s), xtol=1e-5)
                            if zero > 0. else 0.)
            masses = compensate(removed_each)
            native_forward = forward_draft(masses)
            root_s = monotonic()-before
            result = evaluator.evaluate(masses)
            candidate = evaluator.candidate_plan(masses)
            write(out/(name+'-plan.json'), candidate.model_dump(mode='json'))
            row = dict(name=name, apt_drawdown_fraction=fraction,
                fwd_deep_removed_each_kg=removed_each, target_forward_draft_m=target_forward,
                native_forward_draft_m=native_forward, root_calls=root_calls-first_calls,
                root_s=root_s, physics=result, full_checker_passed=False,
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
        summary = dict(name=name, wall_s=row['wall_s'], root_s=row['root_s'],
            fwd_deep_removed_each_kg=row['fwd_deep_removed_each_kg'],
            physics_feasible=row['physics']['physics_feasible'],
            full_checker_passed=row['full_checker_passed'], lifts=row['lifts'],
            negative_margins={key: value for key, value in row['physics']['raw_margins'].items() if value < 0.})
        summaries.append(summary)
        write(out/'summary.json', summaries)
        print(json.dumps(summary), flush=True)
        if row['full_checker_passed']:
            break


if __name__ == '__main__':
    main()
