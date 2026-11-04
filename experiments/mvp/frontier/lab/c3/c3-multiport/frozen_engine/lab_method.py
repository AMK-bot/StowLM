# Adapted from lab/ref/frontier09.py: reuse the internally finished plan, then independently check in score.py.
"""Shared scorecard adapters for the complete cold-start engine."""
from pathlib import Path
import sys

# score.py imports this file with a generated name and places engine/ first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from frozen_engine.planner import plan_and_check
from stowage.actions import get_action_space


def _actions(instance, plan):
    return tuple(get_action_space('boxslot').from_plan(instance, plan))


def _key(instance, plan):
    space = get_action_space('boxslot')
    return [space.encode(action) for action in _actions(instance, plan)]


def propose(instance, *, seed, budget_s, context):
    result, record = plan_and_check(instance, budget_s=budget_s,
                                    finalize_s=context.get('finalize_s', 600.), seed=seed)
    context.setdefault('method_metadata', {})['engine'] = record
    if result is None or result.plan is None:
        return []
    context['_engine_result'] = result
    context['_engine_actions'] = _key(instance, result.plan)
    return [_actions(instance, result.plan)]


def finish(instance, plan, *, seed, finalize_s, label, context):
    result = context.get('_engine_result')
    if result is None or result.plan is None:
        raise ValueError('engine.finish requires engine.propose in the same map run')
    if _key(instance, plan) != context.get('_engine_actions'):
        raise ValueError('cannot reuse engine result after cargo actions changed')
    return result, dict(label=label, reused_engine_finish=True)

# Engine bundle SHA256: 9b14af4758150afd7ae53d39e8b76d52903d3cea435c5f401cd4e68de6e0d564
