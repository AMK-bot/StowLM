"""Compare native rejection with a fresh checker on the explicit manual seed.

This is a diagnostic, NOT a mask-legal constructor or a quick-set pass.
No native function, checker, parameter or instance is changed.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
from time import monotonic


def main():
    from alns import Problem
    from catalog import ensure_safe
    from sequence import order_legal
    from stowage.actions.space import Place
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import BallastCondition, PlannerResult
    from stowage.planner import finalize

    here = Path(__file__).resolve().parent
    target = here/'results'/'small-checker-diagnostic'
    target.mkdir(parents=True, exist_ok=True)
    path = Path(ensure_safe('experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100034.json'))
    instance = load_instance(path)
    # Reuse the exact explicitly labelled manual seed, without maintaining a
    # second edited copy of its assignment.
    module = ast.parse((here/'special_chain.py').read_text())
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'propose_hand_seed')
    assignment = next(ast.literal_eval(n.value) for n in function.body
                      if isinstance(n, ast.Assign) and any(isinstance(v, ast.Name)
                         and v.id == 'slots' for v in n.targets))
    problem = Problem(instance)
    prefix, stuck = order_legal(problem, assignment)
    missing = set(assignment)-{a.box for a in prefix}
    actions = tuple(prefix)+tuple(Place(b, assignment[b]) for b in sorted(missing))
    assert set(assignment) == set(instance.load_list) and len(actions) == len(assignment)
    scored = problem.score(actions)
    if scored is None:
        raise RuntimeError('The manual diagnostic seed fails physical move replay')
    row = dict(instance_id=instance.id, input=str(path),
               input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               source='explicit manual diagnostic; not new constructor',
               native_mask_legal=stuck is None, native_stuck=stuck,
               physical_replay_lifts=scored.objective)
    assert stuck is not None  # This diagnostic must not hide the discrepancy.
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    started = monotonic()
    result = finalize(instance, PlannerResult(status='ok', plan=scored.plan,
                      planner='fx-chains.manual-diagnostic', planner_version='1', seed=0),
                      time_limit_s=300)
    row.update(finalize_s=monotonic()-started, finalize_status=result.status,
               finalize_message=result.message, finalize_metrics=result.metrics)
    (target/'finisher-result.json').write_text(result.model_dump_json(indent=2))
    plan = result.plan or scored.plan
    if plan.ballast is None:
        adjustable = {t.id for t in instance.vessel.tanks if t.adjustable}
        plan = plan.model_copy(update={'ballast': BallastCondition(source='planner',
               tank_masses_kg={t: instance.onboard.tank_masses_kg.get(t, 0.) for t in adjustable})})
        row['checked_ballast'] = 'explicit original adjustable-tank masses after failed V4'
    else:
        row['checked_ballast'] = 'V4 returned ballast'
    started = monotonic()
    report = check(instance, plan)
    row.update(check_s=monotonic()-started, checker_passed=bool(report.passed),
               violations=sorted({v.rule_id for v in report.violations}),
               lifts=float(report.count.weighted_nonproductive))
    (target/'plan.json').write_text(plan.model_dump_json(indent=2))
    (target/'check.json').write_text(report.model_dump_json(indent=2))
    (target/'summary.json').write_text(json.dumps(row, indent=2))
    print(json.dumps(row, indent=2), flush=True)


if __name__ == '__main__':
    main()
