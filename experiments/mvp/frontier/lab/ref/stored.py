"""Replay the stored Frontier 0.9.0 cargo plan through the shared gate."""
from __future__ import annotations

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import MovePlan


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    """Preserve stored load order when a final plan exists; never invent a stow."""
    plan = context.get('stored_plan')
    if plan is None:
        plan = context.get('baseline', {}).get('plan')
    if plan is not None:
        plan = MovePlan.model_validate(plan) if isinstance(plan, dict) else plan
        actions = tuple(get_action_space('boxslot').from_plan(instance, plan))
        context.setdefault('method_metadata', {})['stored_order_preserved'] = True
        return [actions]
    stored = context.get('stored')
    if stored is None:
        context.setdefault('method_metadata', {})['missing_stored_plan'] = True
        return []
    context.setdefault('method_metadata', {})['stored_order_preserved'] = False
    if isinstance(stored, dict):
        return [dict(stored)]
    return [tuple(Place(a['box'], a['slot']) if isinstance(a, dict) else a for a in stored)]
