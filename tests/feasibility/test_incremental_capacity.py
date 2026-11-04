"""C1 state counters and residual-flow reuse must match cold exact screens."""
import random

import numpy as np
import pytest

from stowage.feasibility.capacity import ClassFlow


def test_parent_flow_repair_matches_cold_flow():
    rng = np.random.default_rng(1791)
    for _ in range(150):
        flow = object.__new__(ClassFlow)
        flow.compat = rng.random((4, 5)) < .65
        flow.nb, flow.nc = flow.compat.shape
        demand = rng.integers(0, 5, 4)
        capacity = rng.integers(0, 6, 5)
        _, before = flow.flow(demand, capacity)
        saved = before.copy()
        after_demand = np.maximum(demand + rng.integers(-2, 3, 4), 0)
        after_capacity = np.maximum(capacity + rng.integers(-2, 3, 5), 0)
        expected = flow.flow(after_demand, after_capacity)[0]
        actual, repaired = flow.repair(before, after_demand, after_capacity)
        assert actual == expected
        np.testing.assert_array_equal(before, saved)
        assert not repaired[~flow.compat].any()
        assert np.all(repaired >= 0)
        assert np.all(repaired.sum(axis=0) <= after_capacity)
        assert np.all(repaired.sum(axis=1) <= after_demand)
        if actual:
            np.testing.assert_array_equal(repaired.sum(axis=1), after_demand)


@pytest.mark.parametrize("name", ["fixture_b", "rich_a"])
def test_incremental_capacity_counters_match_cold_rebuild(h, request, name, monkeypatch):
    eng = h.engine(request.getfixturevalue(name), h.SCREENS)
    rng = random.Random(197)
    for _ in range(8):
        st = eng._cur
        summary = eng._capacity_state(st)
        T, flow = eng.T, eng._flow
        usable = eng._usable(st, float(T.height.min()), float(T.share.min()))
        caps = eng._caps(st)
        rem = np.flatnonzero(st.rem)
        demand = np.bincount(flow.box_class[rem], weights=T.units[rem], minlength=flow.nb).astype(np.int64)
        capacity = np.bincount(flow.cell_class[eng._usable_cell_mask(st, usable)], minlength=flow.nc)
        for key, expected in (("usable", usable), ("caps", caps), ("rem", rem),
                              ("demand", demand), ("capacity", capacity)):
            np.testing.assert_array_equal(summary[key], expected)
        assert summary["feasible"] == flow.flow(demand, capacity)[0]
        saved = {k: v.copy() for k, v in summary.items() if isinstance(v, np.ndarray)}
        legal = eng.legal_actions()
        if not legal:
            break
        eng.push(rng.choice(legal))
        for method in ("_usable", "_caps", "_usable_cell_mask"):
            monkeypatch.setattr(eng, method, lambda *args: pytest.fail("child counters were rebuilt in full"))
        eng._capacity_state(eng._cur)
        monkeypatch.undo()
        for key, expected in saved.items():
            np.testing.assert_array_equal(summary[key], expected)
        # A warm summary/completion query cannot solve another class matching network.
        monkeypatch.setattr(flow, "flow", lambda *args: pytest.fail("cached C1 flow was recomputed"))
        assert eng._capacity_state(st) is summary
        eng._completion(st)
        monkeypatch.undo()
