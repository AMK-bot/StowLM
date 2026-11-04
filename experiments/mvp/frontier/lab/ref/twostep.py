"""The shared TwoStepPlanner constructor, followed by score.py's finisher."""
from __future__ import annotations

from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig
from stowage.solver.twostep import TwoStepPlanner


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    result = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0})).plan(
        instance, seed=seed, time_limit_s=budget_s)
    context.setdefault('method_metadata', {}).update(
        twostep_status=result.status, twostep_message=result.message)
    if result.plan is None:
        return []
    return [tuple(get_action_space('boxslot').from_plan(instance, result.plan))]
