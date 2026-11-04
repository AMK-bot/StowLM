"""Instruction filter for StowLM's feasibility mask (task 9.3 prep), composed around the production mask unchanged.

The production mask (``llm.stowlm.mask.MaskedDecoder``) reads everything it allows from the feasibility engine:
the legal set's index arrays (``legal_index`` / ``legal_discharges``), ``legal_actions``, ``is_legal``, ``push`` /
``copy`` and ``dead_end``. :class:`InstructionEngine` wraps that engine and removes every ``Place(box, slot)`` that
``ConstrainedInstance.forbids`` rejects, so the mask's token trie is built over (legal set) minus (forbidden
placements): a forbidden slot's tokens get -inf logits and StowLM can never write them. Everything else, including
discharges and the mask's own screens, passes through untouched. Nothing in ``src/llm/stowlm`` is edited.

``instruction_decoder_factory(constrained)`` plugs into ``StowLMPlanner(decoder_factory=...)``, the planner's existing
seam for alternative mask backends. If an instruction removes every remaining legal placement, the wrapped engine
reports a dead end ("instruction leaves no legal action"), which the planner treats like any other mask dead end.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from stowage.actions.space import Place
from stowage.feasibility import DeadEnd, IllegalActionError

from llm.instructions.apply import ConstrainedInstance


class _Table:
    """Forbidden (box index, slot index) pairs as a flat boolean array, shared by every copy of one engine."""

    def __init__(self, engine: Any, constrained: ConstrainedInstance) -> None:
        boxes, slots = engine.index_boxes, engine.index_slots
        self.n_slots = len(slots)
        self.forbidden = np.zeros(len(boxes) * self.n_slots, dtype=bool)
        for b, box in enumerate(boxes):
            for s, slot in enumerate(slots):
                if constrained.forbids(box, slot):
                    self.forbidden[b * self.n_slots + s] = True
        self.n_forbidden = int(self.forbidden.sum())


class InstructionEngine:
    """A feasibility-engine proxy whose legal set excludes placements forbidden by the instruction constraints."""

    def __init__(self, inner: Any, constrained: ConstrainedInstance, table: _Table | None = None) -> None:
        self.inner = inner
        self.constrained = constrained
        self.table = table if table is not None else (_Table(inner, constrained) if hasattr(inner, "index_boxes")
                                                      else None)

    def __getattr__(self, name: str) -> Any:  # every other read goes to the real engine
        return getattr(self.inner, name)

    def _forbidden(self, action: Any) -> bool:
        return isinstance(action, Place) and self.constrained.forbids(action.box, action.slot) is not None

    # --- the legal set, filtered ---------------------------------------------------------------------------------
    def legal_index(self):
        flat = np.asarray(self.inner.legal_index())
        if self.table is None or not len(flat):
            return flat
        return flat[~self.table.forbidden[flat]]

    def legal_actions(self):
        return [a for a in self.inner.legal_actions() if not self._forbidden(a)]

    def is_legal(self, action) -> bool:
        return not self._forbidden(action) and self.inner.is_legal(action)

    def legal_slots_for_box(self, box):
        return [s for s in self.inner.legal_slots_for_box(box) if not self.constrained.forbids(box, s)]

    def legal_box_ids(self):
        return [b for b in self.inner.legal_box_ids() if self.legal_slots_for_box(b)]

    def dead_end(self) -> DeadEnd | None:
        dead = self.inner.dead_end()
        if dead is not None or self.inner.is_complete():
            return dead
        if not len(self.legal_index()) and not len(self.inner.legal_discharges()):
            return DeadEnd(self.inner.depth, "instruction leaves no legal action")
        return None

    # --- state changes -------------------------------------------------------------------------------------------
    def push(self, action) -> None:
        if self._forbidden(action):
            raise IllegalActionError(f"instruction forbids {action}")
        self.inner.push(action)

    def copy(self) -> InstructionEngine:
        return InstructionEngine(self.inner.copy(), self.constrained, self.table)


def wrap_engine(engine: Any, constrained: ConstrainedInstance) -> InstructionEngine:
    if engine.instance.id != constrained.instance.id:
        raise ValueError("engine and constraints belong to different instances")
    return InstructionEngine(engine, constrained)


def instruction_decoder_factory(constrained: ConstrainedInstance):
    """``decoder_factory`` for ``StowLMPlanner``: n production ``MaskedDecoder``s over instruction-filtered engines."""
    from llm.stowlm.mask import MaskedDecoder

    def factory(instance, engine, codec, mask_config, n):
        wrapped = wrap_engine(engine, constrained)
        return [MaskedDecoder(wrapped if i == 0 else wrapped.copy(), codec, mask_config) for i in range(n)]

    return factory
