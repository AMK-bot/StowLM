"""Fresh Frontier 0.9.0 plus only the E9 mask fix, at the budgets that made runs/val-0.9: same search rule, 300 s of V4."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import method_e9_base as base


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    search_s = min(120., max(20., 6.*len(instance.load_list)))
    context['finalize_s'] = 300.
    context['tries'] = 3
    return base.propose(instance, seed=seed, budget_s=search_s, context=context)


finish = base.finish
