"""Frontier 0.9.0's own large-map path, `plan_big`, with its stated budgets. Same reuse rule as ref/frontier09.py."""
from __future__ import annotations

from stowage.actions import get_action_space


def _actions(instance, plan):
    return tuple(get_action_space('boxslot').from_plan(instance, plan))


def _key(instance, plan):
    space = get_action_space('boxslot')
    return [space.encode(a) for a in _actions(instance, plan)]


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    import planner

    result, record = planner.plan_big(instance, seed=seed)
    context.setdefault('method_metadata', {})['frontier09_big'] = record
    if result is None or result.plan is None:
        return []
    context['_big_result'] = result
    context['_big_actions'] = _key(instance, result.plan)
    return [_actions(instance, result.plan)]


def finish(instance, plan, *, seed, finalize_s, label, context):
    result = context.get('_big_result')
    if result is None or _key(instance, plan) != context.get('_big_actions'):
        raise ValueError('frontier09_big.finish needs frontier09_big.propose for the same map and actions')
    return result, {'label': label, 'reused_frontier09_big_finish': True}
