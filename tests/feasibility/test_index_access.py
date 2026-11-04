"""Index-array access for the 5.1 mask (plan 4.4): ``legal_index`` / ``legal_pairs`` / ``legal_discharges`` /
``decode_index`` / ``index_of`` describe exactly ``legal_actions()``, in the same order, at every state."""

from __future__ import annotations

import random

import numpy as np
import pytest

from stowage.actions.space import Discharge, Place, Relocate


def _check(e):
    flat = e.legal_index()
    assert flat.dtype == np.int64 and not flat.flags.writeable
    assert np.all(np.diff(flat) > 0)
    places = [e.decode_index(f) for f in flat.tolist()]
    assert tuple(places) + e.legal_discharges() == e.legal_actions()
    b, s = e.legal_pairs()
    assert [(e.index_boxes[i], e.index_slots[j]) for i, j in zip(b.tolist(), s.tolist())] == \
        [(a.box, a.slot) for a in places]
    for a in places[:20]:
        assert e.index_of(a) is not None and int(flat[np.searchsorted(flat, e.index_of(a))]) == e.index_of(a)
    assert all(isinstance(a, Discharge) for a in e.legal_discharges())
    assert e.index_of(Relocate("X", "A-001-01")) is None


@pytest.mark.parametrize("name", ["fixture_a", "fixture_b", "rich_a", "rich_b", "tiny_a", "tiny_b", "crowded_1"])
def test_index_equals_legal_actions(h, name, request):
    inst = request.getfixturevalue(name)
    e = h.make(inst, h.SCREENS) if hasattr(h, "make") else None
    if e is None:
        from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
        e = make_feasibility_engine(inst, h.SPACE, config=FeasibilityConfig(extra=dict(h.SCREENS)))
    rng = random.Random(0)
    for _ in range(40):
        _check(e)
        acts = e.legal_actions()
        if not acts:
            break
        e.push(rng.choice(acts))
    # the index path alone (no legal_actions call) gives the same answers
    e2 = e.copy()
    while e2.depth:
        e2.pop()
    for _ in range(10):
        flat = e2.legal_index()
        if not len(flat) and not e2.legal_discharges():
            break
        a = e2.decode_index(int(flat[len(flat) // 2])) if len(flat) else e2.legal_discharges()[0]
        assert e2.is_legal(a)
        e2.push(a)
