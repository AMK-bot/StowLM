"""Packing-aware hatch partition and capacity-preserving binary cut moves.

The incumbent is retained explicitly. New stows still need the lab scorer's
finisher and full checker; a cut or packing witness is never that certificate.
"""
from __future__ import annotations

from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    problem = context['problem']
    stored = context.get('stored')
    started = monotonic()
    candidates = []
    metadata = context.setdefault('method_metadata', {})
    if stored:
        # Preserve Frontier's selected load order as well as its assignment.
        from stowage.actions import get_action_space
        from stowage.model import MovePlan
        plan = context.get('stored_plan')
        if plan:
            candidates.append(tuple(get_action_space('boxslot').from_plan(
                instance, MovePlan.model_validate(plan))))
        else:
            candidates.append(stored)
    if instance.size_class == 'tiny' and stored:
        metadata['tiny_incumbent_only'] = True
        return candidates
    if instance.size_class == 'small' and stored:
        import small_cuts
        refined = small_cuts.improve(problem, stored, seed=seed,
                                     budget_s=max(0.01, budget_s * 0.55))
        metadata['small_cuts'] = dict(small_cuts.LAST_STATS)
        # One refined stow leaves room for the incumbent within three tries.
        candidates.extend(candidate for candidate in refined[:1]
                          if candidate != stored)
    remaining = budget_s - (monotonic() - started)
    if remaining > 0.2:
        from cut_layout_v3 import cut_layout
        try:
            layout = cut_layout(instance, problem=problem, seed=seed,
                                time_limit_s=remaining,
                                first_feasible=instance.size_class == 'medium')
            witness = layout.metadata.get('packing_witness')
            metadata['packing'] = {k: v for k, v in layout.metadata.items()
                                   if k != 'packing_witness'}
            if witness:
                actions = legal_order(instance, witness, seed=seed,
                                      budget_s=remaining, context=context)
                if actions is not None:
                    candidates.append(actions)
        except ValueError as error:
            metadata['packing_failure'] = str(error)
    # score.py validates, sequences and sorts all these by real replay lifts.
    return candidates


def legal_order(instance, assignment, *, seed, budget_s, context):
    """Ask the unmodified feasibility mask while ordering a new packing."""
    from sequence import order_legal
    actions, stuck = order_legal(context['problem'], assignment)
    if stuck is not None:
        context['method_metadata'].setdefault('order_failures', []).append(stuck)
        return None
    return actions
