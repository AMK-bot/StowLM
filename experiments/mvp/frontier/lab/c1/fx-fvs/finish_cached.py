"""Use a supplied stored ballast candidate; score.py performs the full check.

For fast ablations only. A failed check rejects this candidate; the proposal
list also contains the original stored order as a fallback. This adapter
performs no new ballast optimization and must not be benchmarked as cold solving.
"""
from stowage.model import MovePlan, PlannerResult


def finish(instance, plan, *, seed, finalize_s, label, context):
    stored = context.get('stored_plan')
    if stored is None:
        raise ValueError('cached-ballast adapter needs an existing finished plan')
    stored = MovePlan.model_validate(stored) if isinstance(stored, dict) else stored
    if stored.ballast is None:
        raise ValueError('stored plan has no ballast candidate')
    plan = plan.model_copy(update={'ballast': stored.ballast})
    return PlannerResult(status='ok', plan=plan, planner='fx-fvs',
                         planner_version='1', seed=seed), {
        'label':label, 'reused_ballast_candidate':True,
        'full_check_by':'lab.score.evaluate'}
