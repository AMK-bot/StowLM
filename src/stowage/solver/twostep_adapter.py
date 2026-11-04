"""Two-step-only scoped queries over the shared feasibility implementation.

The production engine's pre-freeze public membership/box queries enumerate the
entire manifest × open-slot domain. This adapter calls that same evaluator on
only the requested box or pair, retaining all local rules and C1/C2 screens.
The engine's immutable prefix and incremental capacity counters remain shared;
partial results never masquerade as a complete legal set or a global dead end.
Ballast finishing and the full checker still certify every reported solution.

This deliberately confined dependency on engine internals avoids changing the
shared feasibility engine while other solver executives work in parallel.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from stowage.actions import get_action_space
from stowage.actions.space import Discharge, Place
from stowage.feasibility import make_feasibility_engine
from stowage.feasibility._engine import IncrementalEngine
from stowage.feasibility.engine import IllegalActionError

ADAPTER_VERSION = "4.0.0"


class _ScopedPlanningEngine:
    """Delegate full queries; cache exact scoped queries per immutable prefix."""

    def __init__(self, native):
        self._native = native
        self._state = None
        self._boxes = {}
        self._pairs = {}
        self._discharges = None

    def __getattr__(self, name):
        return getattr(self._native, name)

    def _sync(self):
        if self._state is not self._native._cur:
            self._state = self._native._cur
            self._boxes = {}
            self._pairs = {}
            self._discharges = None
        return self._state

    def _probe(self, *, box=None, slot=None, discharges=False):
        native = self._native
        state = self._sync()
        # Cache the prefix's exact capacity counters on the real immutable
        # state, so its child can update changed stacks instead of rebuilding.
        if native._screens and not discharges and len(native.T.mass):
            native._capacity_state(state)
        probe = replace(state, legal=None, mask=None, dead=None, evaluated=False,
                        sp=dict(state.sp))
        if hasattr(state, "_capacity"):
            probe._capacity = state._capacity
        native._evaluate(probe,
            allowed_boxes=np.array([] if discharges else [box], dtype=np.int64),
            allowed_slots=(np.array([], dtype=np.int64) if discharges else
                           None if slot is None else np.array([slot], dtype=np.int64)),
            include_discharges=discharges)
        return probe

    def legal_slots_for_box(self, box):
        state = self._sync()
        native = self._native
        bi = native.T.box_index.get(box)
        if bi is None or native._complete(state):
            return ()
        if box not in self._boxes:
            probe = self._probe(box=bi)
            slots = tuple(native.T.slot_ids[int(f) % len(native.T.slot_ids)] for f in probe.mask)
            self._boxes[box] = slots
        return self._boxes[box]

    def legal_discharges(self):
        state = self._sync()
        if self._native._complete(state):
            return ()
        if self._discharges is None:
            self._discharges = tuple(self._probe(discharges=True).discharges)
        return self._discharges

    def has_open_hold_slots(self):
        """Structural frontier only: an unfilled hold slot exists at this prefix.

        This does not claim any cargo can legally use that slot. The carrier
        may skip its hold-first scan only when there is no such geometric slot;
        individual actions still use the exact scoped evaluator.
        """
        native = self._native
        slots = native._open_slots(native._cur)
        return bool(np.any(~native.T.slot_deck[slots]))

    def is_legal(self, action):
        state = self._sync()
        native = self._native
        if native._complete(state):
            return False
        if isinstance(action, Discharge):
            return action in self.legal_discharges()
        if not isinstance(action, Place):
            return False
        if action.box in self._boxes:
            return action.slot in self._boxes[action.box]
        if action not in self._pairs:
            bi = native.T.box_index.get(action.box)
            si = native.T.slot_index.get(action.slot)
            self._pairs[action] = (bi is not None and si is not None
                                   and bool(len(self._probe(box=bi, slot=si).mask)))
        return self._pairs[action]

    def push(self, action):
        if not self.is_legal(action):
            raise IllegalActionError(f"action is not legal here: {action!r}")
        native = self._native
        previous = native._cur
        child = native._child(previous, action)
        native._history.append(previous)
        native._cur = child
        native._stats.pushes += 1

    def copy(self):
        other = type(self)(self._native.copy())
        self._sync()
        other._state = self._state
        other._boxes = dict(self._boxes)
        other._pairs = dict(self._pairs)
        other._discharges = self._discharges
        return other


def make_planning_engine(instance, space=None, *, config=None):
    """Build the shared engine, adding scoped non-freeze queries locally."""
    native = make_feasibility_engine(instance, space or get_action_space("boxslot"), config=config)
    if isinstance(native, IncrementalEngine) and not native._freeze:
        return _ScopedPlanningEngine(native)
    return native
