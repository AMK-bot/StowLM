"""Twenty freshly generated, reconditioned six-export slices on whole ships.

The generators' public APIs retain production hulls, hull-following tanks and
fixed synthetic cargo; no vessel/loadcase changes or relaxed physics are made.
C2 screening is disabled only while constructing a complete legal/static stow:
full exact checker results are recorded before and after ballast finishing.
Source: stowage.vessel.fleet_slices and the generator module contracts.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import resource
import time

ROOT = Path(__file__).resolve().parents[3]
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
# Use cache_warm's production roots, without experiment-specific substitutions.


def bounded_full_check(instance, plan):
    """Run the unchanged full checker, with its own bounded worker lifetime."""
    from stowage.checker.full import check
    from stowage.solver.ballast_deadline import BallastDeadline, run_with_deadline, shutdown_worker
    shutdown_worker()
    started = time.monotonic()
    try:
        report = run_with_deadline(check, (instance, plan), deadline=started+30.)
        return dict(status='completed', wall_s=time.monotonic()-started,
                    passed=report.passed, report=report.model_dump(mode='json'))
    except BallastDeadline:
        return dict(status='timeout', wall_s=time.monotonic()-started, passed=None,
                    timeout_s=30., error='Full checker did not finish within 30 seconds')
    except Exception as exc:
        return dict(status='error', wall_s=time.monotonic()-started, passed=None,
                    error=f'{type(exc).__name__}: {exc}')
    finally:
        # The helper stops only its own recorded process handle.
        shutdown_worker()


def run_one(generator, index, out, limit=10.):
    from stowage.actions import get_action_space
    from stowage.checker.rules import check_static_rules
    from stowage.feasibility import make_feasibility_engine, FeasibilityConfig
    from stowage.generate.random_yard import generate_random_instance, RANDOM_YARD_VERSION
    from stowage.generate.instances import generate_simulated_instance, INSTANCES_VERSION
    from stowage.io import save_instance, save_plan
    from stowage.moves import apply_plan
    from stowage.solver.ballast import finish_ballast
    from stowage.versions import component_versions
    seed = 42000 + index
    fleet = ('feeder_500', 'feeder_800', 'feeder_1700')[index % 3]
    identifier = f'{generator}-{index:02d}-{fleet}'
    folder = out / identifier
    folder.mkdir(parents=True, exist_ok=True)
    result = dict(id=identifier, generator=generator, index=index, seed=seed, fleet=fleet,
                  generator_version=RANDOM_YARD_VERSION if generator == 'random' else INSTANCES_VERSION,
                  components=component_versions(), calls=[])
    print(json.dumps(dict(phase='generation', **result)), flush=True)
    started = time.perf_counter()
    kwargs = dict(seed=seed, vessel_name=fleet, max_attempts=200)
    instance = (generate_random_instance('tiny', blocking_ratio=.2, **kwargs) if generator == 'random'
                else generate_simulated_instance('tiny', noise=.2, **kwargs))
    result.update(generation_s=time.perf_counter()-started, target_count=len(instance.load_list),
                  fixed_load_count=len(instance.vessel.fixed_loads),
                  adjustable_tanks=sum(t.adjustable for t in instance.vessel.tanks))
    save_instance(instance, folder/'instance.json')
    engine = make_feasibility_engine(instance, get_action_space(), config=FeasibilityConfig(extra={'global_screen': 0.}))
    deadline = time.perf_counter()+30.
    nodes = 0
    def dfs():
        nonlocal nodes
        nodes += 1
        if time.perf_counter() > deadline:
            return None
        if engine.is_complete():
            p = engine.to_plan()
            replay = apply_plan(instance, p)
            if replay.legal:
                static = check_static_rules(instance, replay.final_state.ship, call_index=instance.call_index)
                if static.ran and not static.violations:
                    return p
            return None
        for action in engine.legal_actions():
            engine.push(action)
            p = dfs()
            if p is not None:
                return p
            engine.pop()
        return None
    started = time.perf_counter()
    plan = dfs()
    result.update(construct_s=time.perf_counter()-started, nodes=nodes,
                  construction='L1/L2+C1; global_screen disabled only for construction; exact static and full checker retained')
    if plan is None:
        result['construction_failed'] = True
    else:
        save_plan(plan, folder/'input-plan.json')
        replay = apply_plan(instance, plan)
        static = check_static_rules(instance, replay.final_state.ship, call_index=instance.call_index)
        result.update(legal=replay.legal, loaded_count=replay.count.loads,
                      static=static.model_dump(mode='json'), input_has_ballast=plan.ballast is not None,
                      arrival_tank_masses_kg=instance.onboard.tank_masses_kg)
        baseline = bounded_full_check(instance, plan)
        result.update(baseline_full_s=baseline['wall_s'], baseline_passed=baseline['passed'],
                      baseline=baseline)
        (folder/'baseline.json').write_text(json.dumps(result, indent=2)+'\n')
        finished_plans = []
        for repeat in range(2):
            started = time.perf_counter()
            outcome = finish_ballast(instance, plan, time_limit_s=limit, seed=0)
            call = dict(repeat=repeat, warm=bool(repeat), wall_s=time.perf_counter()-started,
                        outcome=outcome.model_dump(mode='json', exclude={'plan'}))
            if outcome.plan is not None:
                save_plan(outcome.plan, folder/f'finished-{repeat}.json')
                finished_plans.append((repeat, outcome.plan))
            result['calls'].append(call)
            (folder/'result.json').write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(dict(phase='call', id=identifier, repeat=repeat, wall_s=call['wall_s'],
                                 status=outcome.status, full_checker_passed=call.get('full_checker_passed'))), flush=True)
        # Preserve the second finisher's warm worker: independent validation
        # happens after both calls, with a fresh owned worker per full check.
        for repeat, finished_plan in finished_plans:
            checked = bounded_full_check(instance, finished_plan)
            result['calls'][repeat].update(full_checker_s=checked['wall_s'],
                full_checker_passed=checked['passed'], full_checker=checked)
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
    (folder/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT/'experiments/ballast/effectiveness/slices_results')
    parser.add_argument('--limit', type=float, default=10.)
    parser.add_argument('--generator', choices=('random', 'simulated', 'both'), default='both')
    parser.add_argument('--start-index', type=int, default=0)
    parser.add_argument('--count', type=int, default=10)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results = []
    for generator in (('random', 'simulated') if args.generator == 'both' else (args.generator,)):
        for index in range(args.start_index, args.start_index+args.count):
            try:
                result = run_one(generator, index, args.out, args.limit)
            except Exception as exc:
                import traceback
                result = dict(generator=generator, index=index, error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
                print(json.dumps(result), flush=True)
            results.append(result)
            (args.out/'results.json').write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps(dict(phase='resources', elapsed_s=time.perf_counter()-started,
                         peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9)), flush=True)


if __name__ == '__main__':
    main()
