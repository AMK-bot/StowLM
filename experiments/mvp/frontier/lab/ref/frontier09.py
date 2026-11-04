"""Remeasure Frontier 0.9.0 itself, with its complete portfolio and finisher."""
from __future__ import annotations

from stowage.actions import get_action_space


def _actions(instance, plan):
    space = get_action_space('boxslot')
    return tuple(space.from_plan(instance, plan))


def _key(instance, plan):
    space = get_action_space('boxslot')
    return [space.encode(a) for a in _actions(instance, plan)]


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    import planner

    # The original portfolio requests two CP workers. Each lab worker has one
    # compute slot, so change this imported call only for this process and call.
    solve_master = planner.solve_master

    def single_worker_master(*args, **kwargs):
        kwargs['workers'] = 1
        return solve_master(*args, **kwargs)

    planner.solve_master = single_worker_master
    try:
        result, record = planner.plan_and_check(
            instance, search_s=budget_s, finalize_s=context.get('finalize_s', 300.),
            seed=seed, tries=context.get('tries', 3))
    finally:
        planner.solve_master = solve_master
    context.setdefault('method_metadata', {})['frontier09'] = record
    if result is None or result.plan is None:
        return []
    context['_frontier09_result'] = result
    context['_frontier09_actions'] = _key(instance, result.plan)
    return [_actions(instance, result.plan)]


def finish(instance, plan, *, seed, finalize_s, label, context):
    """Reuse this method's already finished plan; score.py still fully checks it.

    Use explicitly with --finish ref/frontier09.py:finish. The action comparison
    rejects a changed load order or stow from another stage or another map.
    """
    result = context.get('_frontier09_result')
    if result is None or result.plan is None:
        raise ValueError('frontier09.finish requires frontier09.propose in the same map run')
    if _key(instance, plan) != context.get('_frontier09_actions'):
        raise ValueError('frontier09.finish cannot reuse a result for changed actions')
    return result, {'label': label, 'reused_frontier09_finish': True}
