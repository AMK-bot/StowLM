"""Keep the incumbent ballast during local cargo repair; score.py fully checks.

This makes the experiment a fixed-ballast repair. A changed stow receives the
incumbent's ballast condition, then the shared runner applies the entire checker.
The stored incumbent is returned verbatim when its cargo actions match.
"""
from stowage.actions import get_action_space
from stowage.model import MovePlan


def finish(instance, plan, *, seed, finalize_s, label, context):
    del seed, finalize_s, label
    stored = context.get('stored_plan')
    if stored is None:
        return None, dict(reason='fixed-ballast repair requires a stored finished plan')
    stored = MovePlan.model_validate(stored) if isinstance(stored, dict) else stored
    space = get_action_space('boxslot')
    before = tuple(space.from_plan(instance, stored))
    after = tuple(space.from_plan(instance, plan))
    if before == after:
        return stored, dict(mode='stored incumbent; shared runner rechecks full plan')
    return plan.model_copy(update={'ballast': stored.ballast}), dict(
        mode='local cargo repair at fixed incumbent ballast; shared runner checks all rules')
