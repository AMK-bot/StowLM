"""Reuse stored ballast only after a fresh full check; otherwise run V4 normally."""
from stowage.checker import check
from stowage.model import MovePlan, PlannerResult


def finish(instance, plan, *, seed, finalize_s, label, context):
    stored = context.get('stored_plan')
    if stored is not None:
        stored = MovePlan.model_validate(stored) if isinstance(stored, dict) else stored
        if stored.ballast is not None:
            trial = plan.model_copy(update={'ballast': stored.ballast})
            report = check(instance, trial)
            if report.passed:
                return PlannerResult(status='ok', plan=trial, planner='fx-fvs',
                                     planner_version='1', seed=seed), {
                    'label':label, 'stored_ballast_rechecked':True}
    from planner import _finish
    return _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
