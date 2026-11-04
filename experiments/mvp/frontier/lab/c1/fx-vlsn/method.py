"""Lab plugin: cyclic exchange plus bounded exact parent recombination."""
from __future__ import annotations

from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _starts(p, context):
    from alns import Sequencer
    from stowage.actions.space import Place
    starts = [getattr(s, 'actions', s) for s in (context.get('starts') or [])]
    baseline_actions = context.get('baseline', {}).get('ordered_actions')
    if baseline_actions:
        starts.insert(0, tuple(Place(a['box'], a['slot']) for a in baseline_actions))
    elif context.get('stored'):
        ordered = Sequencer(p).order(context['stored'])
        if ordered:
            starts.insert(0, ordered)
    return starts


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    from alns import Problem
    from search import run
    from recombine import recombine
    p = context.get('problem') or Problem(instance)
    starts = _starts(p, context)
    if not starts:
        return []
    began = monotonic()
    candidates, stats = run(p, starts, budget_s=budget_s*.88, seed=seed)
    if len(candidates) >= 2 and monotonic()-began < budget_s:
        fused = recombine(p, candidates[:4], deadline=began+budget_s, seed=seed)
        candidates += fused
        stats['recombination'] = getattr(recombine, 'last_stats', {})
    unique = {}
    for actions in candidates:
        key = tuple((a.box, a.slot) for a in actions)
        unique[key] = actions
    propose.last_stats = stats
    context.setdefault('method_metadata', {}).update(stats)
    return list(unique.values())


def swap_fast(instance, *, seed, budget_s, context):
    return _ablation(instance, seed, budget_s, context, 'swap', 'fast')


def cycle_replay(instance, *, seed, budget_s, context):
    return _ablation(instance, seed, budget_s, context, 'cycle', 'replay')


def swap_replay(instance, *, seed, budget_s, context):
    return _ablation(instance, seed, budget_s, context, 'swap', 'replay')


def _ablation(instance, seed, budget_s, context, neighborhood, scorer):
    from alns import Problem
    from search import run
    p = context.get('problem') or Problem(instance)
    starts = _starts(p, context)
    candidates, stats = run(p, starts, budget_s=budget_s, seed=seed,
                            neighborhood=neighborhood, scorer=scorer)
    _ablation.last_stats = stats
    context.setdefault('method_metadata', {}).update(stats)
    return candidates
