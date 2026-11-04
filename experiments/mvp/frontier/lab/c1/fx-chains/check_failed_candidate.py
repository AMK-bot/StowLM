"""Full-check a saved complete stow at original tanks after V4 timed out.

This is one explicit liquid state, not a fixed-stow or map infeasibility proof.
"""
import hashlib
import json
from pathlib import Path
from time import monotonic


def main():
    from alns import Problem, _mask_ok
    from catalog import ensure_safe
    from stowage.actions.space import Place
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import BallastCondition

    here = Path(__file__).resolve().parent
    evidence = next((here/'results'/'candidates').glob('D192.medium-feeder_500-f60-high-s192200000.s0.b30.*.json'))
    data = json.loads(evidence.read_text())
    path = Path(ensure_safe(data['input']))
    instance = load_instance(path)
    problem = Problem(instance)
    actions = tuple(Place(a['box'], a['slot']) for a in data['candidates'][0])
    mask_legal = _mask_ok(problem, actions)
    scored = problem.score(actions)
    assert mask_legal and scored is not None and len(actions) == len(instance.load_list)
    adjustable = {t.id for t in instance.vessel.tanks if t.adjustable}
    plan = scored.plan.model_copy(update={'ballast': BallastCondition(source='planner',
                tank_masses_kg={t: instance.onboard.tank_masses_kg.get(t, 0.) for t in adjustable})})
    started = monotonic()
    report = check(instance, plan)
    row = dict(instance_id=instance.id, input=str(path), input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               source_evidence=str(evidence), native_mask_legal=mask_legal, boxes=len(actions),
               prior_v4='300-second timeout:worker; no finished ballast returned',
               checked_liquid_state='explicit original onboard adjustable-tank masses; fixed tanks unchanged',
               checker_passed=bool(report.passed), check_s=monotonic()-started,
               violations=sorted({v.rule_id for v in report.violations}),
               lifts=float(report.count.weighted_nonproductive),
               scope='one explicit tank vector; no fixed-stow or map infeasibility claim')
    target = here/'results'/'failed-medium-check'
    target.mkdir(parents=True, exist_ok=True)
    (target/'plan.json').write_text(plan.model_dump_json(indent=2))
    (target/'check.json').write_text(report.model_dump_json(indent=2))
    (target/'summary.json').write_text(json.dumps(row, indent=2))
    print(json.dumps(row, indent=2), flush=True)


if __name__ == '__main__':
    main()
