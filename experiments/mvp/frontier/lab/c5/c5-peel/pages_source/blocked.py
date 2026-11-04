"""Two-step under one discharge port per block: port blocking, basic practice and the D185 rule, reused unchanged.

The rule lives in ``experiments/mvp/strategy/blockrule``. Its layout restricts two-step's placement screen for one solve through
a process-local hook, so nothing in ``src/`` changes. It is a start for the search here, not a method of its own.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]


def _importable():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def blocked_plan(instance, *, seed, config, time_limit_s):
    """Two-step's result with the layout enforced. Same return type as ``TwoStepPlanner.plan``."""
    _importable()
    from experiments.mvp.strategy.blockrule.capacity_candidate import capacity_relaxed_layout
    from experiments.mvp.strategy.headroom.layout import BlockModel, constraint_rule
    from stowage.solver.twostep import TwoStepPlanner
    model = BlockModel.from_instance(instance)
    layout = capacity_relaxed_layout(instance, model=model)
    with constraint_rule(instance, layout, model=model):
        return TwoStepPlanner(feasibility_config=config).plan(instance, seed=seed, time_limit_s=time_limit_s)


def compacted(instance, plan):
    """The same stow with software's load order (D174), or the plan unchanged if that isn't cheaper."""
    _importable()
    from experiments.mvp.strategy.compact import compact_plan
    from stowage.moves import apply_plan
    candidate, _ = compact_plan(instance, plan)
    if candidate is None:
        return plan
    cost = lambda p: apply_plan(instance, p).count.weighted_nonproductive
    return candidate if cost(candidate) < cost(plan) else plan
