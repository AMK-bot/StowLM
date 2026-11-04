"""D199 one-placement decode: the outermost mask layer forbids a Place on a box the plan already placed, so an action can't be scratch paper. Like port_mask.py, no
src/ change: it wraps the K1b stack (production, look-ahead, securing) from outside, because the rule depends on the plan so far and not on the box and slot alone.
Discharge and yard actions pass through. Inner screens don't know about the ban, so a ban that strands the plan shows up as a dead end, and that's the measurement."""
from __future__ import annotations

from typing import Any

import numpy as np

from stowage.actions.space import Place
from stowage.feasibility import DeadEnd, IllegalActionError


class OnePlacementEngine:
    """A feasibility-engine proxy whose legal set excludes placing a box this plan has already placed."""

    def __init__(self, inner: Any, placed: frozenset | None = None) -> None:
        self.inner = inner
        self.placed = placed if placed is not None else frozenset(a.box for a in inner.actions() if isinstance(a, Place))
        self._boxes = np.array([b in self.placed for b in inner.index_boxes], dtype=bool)

    def __getattr__(self, name: str) -> Any:  # every other read goes to the real engine
        return getattr(self.inner, name)

    def _forbidden(self, action: Any) -> bool:
        return isinstance(action, Place) and action.box in self.placed

    def legal_index(self):
        flat = np.asarray(self.inner.legal_index())
        if not len(flat) or not self.placed:
            return flat
        return flat[~self._boxes[flat // self.inner.n_slots]]

    def legal_actions(self):
        return [a for a in self.inner.legal_actions() if not self._forbidden(a)]

    def is_legal(self, action) -> bool:
        return not self._forbidden(action) and self.inner.is_legal(action)

    def legal_slots_for_box(self, box):
        return [] if box in self.placed else self.inner.legal_slots_for_box(box)

    def legal_box_ids(self):
        return [b for b in self.inner.legal_box_ids() if b not in self.placed]

    def dead_end(self) -> DeadEnd | None:
        dead = self.inner.dead_end()
        if dead is not None or self.inner.is_complete():
            return dead
        if not len(self.legal_index()) and not len(self.inner.legal_discharges()):
            return DeadEnd(self.inner.depth, 'one placement leaves no legal action')
        return None

    def push(self, action) -> None:
        if self._forbidden(action):
            raise IllegalActionError(f'one placement forbids {action}')
        self.inner.push(action)
        if isinstance(action, Place):
            self.placed = self.placed | {action.box}
            self._boxes = np.array([b in self.placed for b in self.inner.index_boxes], dtype=bool)

    def copy(self) -> 'OnePlacementEngine':
        return OnePlacementEngine(self.inner.copy(), self.placed)


def outer_factory(factory):
    """Wraps a decoder factory: each sample keeps its own engine, inside one more filter."""
    from llm.stowlm.mask import MaskedDecoder

    def wrapped(instance, engine, codec, mask_config, n):
        return [MaskedDecoder(OnePlacementEngine(d.engine), codec, mask_config) for d in factory(instance, engine, codec, mask_config, n)]
    wrapped.name = f"one-placement-over-{getattr(factory, 'name', 'mask')}"
    return wrapped
