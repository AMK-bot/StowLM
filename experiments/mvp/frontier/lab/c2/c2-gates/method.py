"""Mask experiment adapters; all acceptance still belongs to score.py's checker.

reference/propose compare 200 identical iterations, including identical cooling.
live uses the original wall-time cooling and respects the supplied time budget.
The inherited ballast adapter isolates search, as in C1 fx-vlsn/admit.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
for path in (LAB / 'c1/fx-vlsn', LAB / 'c1/leader', HERE):
    sys.path.insert(0, str(path))


def _run(instance, seed, budget_s, context, *, accelerated, fixed):
    from e9fix import install
    from search_kernel import run
    from stowage.actions.space import Place
    install()
    rows = context.get('baseline', {}).get('ordered_actions')
    if not rows:
        context['method_metadata']['complete_replay_legal_stows'] = 0
        return []
    starts = [tuple(Place(a['box'], a['slot']) for a in rows)]
    p = context['problem']
    mask = None
    if accelerated:
        from mask import PrefixMask
        mask = PrefixMask(p)
    # The fixed-work comparison needs a generous wall budget in both arms;
    # the kernel still stops at that budget if it cannot finish the work.
    candidates, stats = run(p, starts, seed=seed, budget_s=budget_s,
                            mask=mask, max_iterations=200 if fixed else None)
    payload = [[[a.box, a.slot] for a in actions] for actions in candidates]
    meta = context['method_metadata']
    meta.update(search=stats, accelerated=accelerated,
                work_protocol='200 iterations' if fixed else 'wall budget',
                candidate_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
                complete_replay_legal_stows=len(candidates), inherited_ballast=True)
    if mask is not None:
        meta['mask_stats'] = dict(mask.stats)
    return candidates


def propose(instance, *, seed, budget_s, context):
    return _run(instance, seed, budget_s, context, accelerated=True, fixed=True)


def reference(instance, *, seed, budget_s, context):
    return _run(instance, seed, budget_s, context, accelerated=False, fixed=True)


def live(instance, *, seed, budget_s, context):
    return _run(instance, seed, budget_s, context, accelerated=True, fixed=False)


def finish(instance, plan, *, seed, finalize_s, label, context):
    from stowage.model import MovePlan
    stored = context.get('stored_plan')
    if stored is None:
        return None, dict(message='no inherited ballast certificate')
    stored = MovePlan.model_validate(stored) if isinstance(stored, dict) else stored
    return plan.model_copy(update={'ballast': stored.ballast}), dict(inherited_ballast=True)
