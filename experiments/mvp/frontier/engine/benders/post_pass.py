# Copied from lab/c4/c2-benders/repair.py:polish_restows; current-run plans and engine-local imports only.
from time import monotonic

def polish_restows(instance, plan, report, *, seed=0, budget_s=60.):
    """Exchange current-run boxes to cut native projected lifts after a pass.

    The incumbent must already have its ordinary successful report. Candidate
    scoring and acceptance both include D15's actual later-port projection.
    The fitted CP objective only guides search and never establishes a gain.
    """
    from stowage.checker import check
    from .physics import PhysicsEvaluator
    from . import permute as permutation
    if not report.passed:
        raise ValueError('restow polishing needs a current-run full-checker pass')
    started = monotonic()
    old_lifts = float(report.count.weighted_nonproductive)
    meta = dict(kind='projected_restow_exchange', incumbent_lifts=old_lifts,
                accepted=False, cold_current_run_only=True)
    if budget_s < 25.:
        return plan, dict(meta, status='insufficient_check_reserve', wall_s=monotonic()-started)
    evaluator = PhysicsEvaluator(instance, plan)
    masses = dict(zip(evaluator.tank_ids, map(float, evaluator.initial_masses_kg)))
    scored, row = permutation.optimize_assignment(instance, plan,
        dict(tank_masses_kg=masses), seed=seed,
        budget_s=max(1., budget_s-25.), lcg_tolerance_m=.1,
        relax_rank=True, relax_heavy=True, relax_category=True,
        context=dict(restow_first=True, cargo_kg_tolerance_m=0.,
                     reference_limit=1., balance_reference_tcg=True, tcg_band_tm=1.))
    meta['search'] = row
    if scored is None or scored.objective >= old_lifts:
        return plan, dict(meta, status='no_cheaper_replay', wall_s=monotonic()-started)
    checked = check(instance, scored.plan)
    meta.update(candidate_lifts=float(checked.count.weighted_nonproductive),
                full_checker_passed=checked.passed,
                violations=[v.model_dump(mode='json') for v in checked.violations])
    if checked.passed and checked.count.weighted_nonproductive < old_lifts:
        plan = scored.plan
        meta['accepted'] = True
        meta['accepted_report'] = checked.model_dump(mode='json')
    meta.update(status='improved' if meta['accepted'] else 'incumbent_retained',
                wall_s=monotonic()-started)
    return plan, meta
