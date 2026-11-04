"""D190 K1c: the D185 port rule as a mask constraint. Same rule and same `forbids` as block-rule, through the instruction filter the
mask already composes (production -> instruction -> look-ahead -> securing). No src/ change. A placement the rule forbids gets no tokens."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]


class PortConstraint:
    """The small part of ConstrainedInstance the instruction engine reads: .instance and .forbids(box, slot)."""

    def __init__(self, instance):
        from experiments.mvp.strategy.blockrule.rule import port_blocking_layout
        from experiments.mvp.strategy.headroom.layout import BlockModel, forbids
        self.instance, self.model = instance, BlockModel.from_instance(instance)
        self.layout = port_blocking_layout(instance, self.model)
        self._forbids = forbids

    @lru_cache(maxsize=None)
    def forbids(self, box, slot):
        return self._forbids(self.instance, self.layout, box, slot, self.model)


def wrap_engine(engine):
    from llm.instructions.mask_filter import InstructionEngine
    instance = engine.instance
    return InstructionEngine(engine, PortConstraint(instance))


def audit(instance, plan):
    """Violations of the rule in a finished plan; empty means the plan obeys it."""
    from experiments.mvp.strategy.blockrule.rule import port_blocking_layout
    from experiments.mvp.strategy.headroom.layout import audit_layout
    return audit_layout(instance, plan, port_blocking_layout(instance))
