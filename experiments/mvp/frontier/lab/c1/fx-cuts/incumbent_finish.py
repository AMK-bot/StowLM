"""Check supplied incumbent ballast first, then use the unchanged V4 finisher.

The scorecard independently runs the full checker on the returned plan. Changed
cargo plans may reuse tank masses only after their own full-checker pass. This
measures refinement with an already solved incumbent, never standalone
construction wall time. A rejected tank seed falls back to ordinary V4.
"""
from stowage.actions import get_action_space
from stowage.model import MovePlan


def finish(instance, plan, *, seed, finalize_s, label, context):
    incumbent_lifts = context.get('stored_lifts')
    original = context.get('stored_plan')
    if original is not None:
        original = MovePlan.model_validate(original)
        space = get_action_space('boxslot')
        if tuple(space.from_plan(instance, plan)) == tuple(space.from_plan(instance, original)):
            return original, dict(reused_finished_incumbent=True,
                                  full_checker_required=True)
        if original.ballast is not None:
            from stowage.checker import check
            warm = plan.model_copy(update={'ballast': original.ballast})
            report = check(instance, warm)
            if report.passed and (incumbent_lifts is None or
                                  report.count.weighted_nonproductive <= incumbent_lifts):
                return warm, dict(reused_incumbent_tank_masses=True,
                                  changed_cargo_full_checker_passed=True)
            context['method_metadata'].setdefault('tank_seed_rejections', []).append(
                sorted({v.rule_id for v in report.violations}))
    from planner import _finish
    result, metadata = _finish(instance, plan, seed=seed,
                               finalize_s=finalize_s, label=label)
    if (result is not None and incumbent_lifts is not None
            and metadata.get('weighted', float('inf')) > incumbent_lifts):
        return None, dict(metadata, message='finished lifts exceed supplied incumbent',
                          rejected_worse_than_incumbent=True)
    return result, metadata
