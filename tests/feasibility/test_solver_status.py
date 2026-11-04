"""An inconclusive LP solve cannot establish necessary-screen infeasibility."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.feasibility import global_bounds
from tests.feasibility.test_engine_v2 import _synthetic_screen


UNKNOWN_STATUSES = [1, 3, 4, 99, None]


def _failed_result(status):
    result = SimpleNamespace(success=False)
    if status is not None:
        result.status = status
    return result


def _screen():
    screen = global_bounds.GlobalScreen([[1., 0.]], np.zeros((1, 0)), [0.], [-np.inf], [4.], [], [], 10.)
    screen._persistent_unavailable = True
    return screen


@pytest.mark.parametrize("status", UNKNOWN_STATUSES)
def test_cold_center_unknown_status_is_undecided(monkeypatch, status):
    screen = _screen()
    monkeypatch.setattr(global_bounds, "linprog", lambda *args, **kwargs: _failed_result(status))
    fixed, free = np.zeros(2), np.ones(2, dtype=bool)
    with pytest.raises(global_bounds.LPIndeterminate):
        screen._center_cold(fixed, free, 8.)
    assert screen.lp_indeterminate == 1
    result = screen.reference(fixed, free, 8.)
    assert isinstance(result, global_bounds.UnknownReference)
    assert not hasattr(result, "y")  # An unknown is never a fabricated primal certificate.
    assert screen.lp_indeterminate == 2


@pytest.mark.parametrize("status", UNKNOWN_STATUSES)
@pytest.mark.parametrize("endpoint", [0, 1])
def test_cold_endpoint_unknown_status_is_undecided(monkeypatch, status, endpoint):
    screen = _screen()
    ref = screen.reference(np.zeros(2), np.ones(2, dtype=bool), 8.)
    real_linprog = global_bounds.linprog
    calls = 0

    def solver(*args, **kwargs):
        nonlocal calls
        index = calls % (endpoint + 1)
        calls += 1
        if index == endpoint:
            return _failed_result(status)
        return real_linprog(*args, **kwargs)

    monkeypatch.setattr(global_bounds, "linprog", solver)
    with pytest.raises(global_bounds.LPIndeterminate):
        screen._slot_interval_cold(ref, [0], 0., 8.)
    assert screen.lp_indeterminate == 1
    assert screen.slot_interval(ref, [0], 0., 8.) is global_bounds.UNKNOWN_INTERVAL
    assert screen.lp_indeterminate == 2


def test_proven_infeasible_status_still_rejects(monkeypatch):
    screen = _screen()
    ref = screen.reference(np.zeros(2), np.ones(2, dtype=bool), 8.)
    monkeypatch.setattr(global_bounds, "linprog", lambda *args, **kwargs: _failed_result(2))
    assert screen.reference(ref.fixed, ref.free, ref.R) is None
    assert screen.slot_interval(ref, [0], 0., 8.) is None
    assert screen.lp_indeterminate == 0


@pytest.mark.parametrize("status", UNKNOWN_STATUSES)
def test_unknown_center_status_does_not_cut_engine_actions(h, tiny_a, monkeypatch, status):
    """Limits, numerical trouble and unrecognised results preserve C1 survivors."""
    result = SimpleNamespace(success=False)
    if status is not None:
        result.status = status
    monkeypatch.setattr(global_bounds, "linprog", lambda *args, **kwargs: result)

    def factory(cls, instance, tables, *, margin_scale=1.0):
        screen = _synthetic_screen(tables, side_limit=1e12, R_total=float(tables.mass.sum()) + 1)
        screen._persistent_unavailable = True
        return screen

    monkeypatch.setattr(global_bounds.GlobalScreen, "from_instance", classmethod(factory))
    engine = h.engine(tiny_a, {"global_screen": 1})
    off = h.engine(tiny_a, {"global_screen": 0})
    expected = tuple(off.legal_actions())
    assert expected
    assert tuple(engine.legal_actions()) == expected
    assert engine.completion_check()["c2"] is True
    assert engine.diagnostics["lp_indeterminate"] >= 1

    child = engine.copy()
    child.push(expected[0])
    off.push(expected[0])
    assert tuple(child.legal_actions()) == tuple(off.legal_actions())
    assert child.completion_check()["c2"] is True
    fixed, free, remaining = child._global_state(child._cur.ex)
    np.testing.assert_array_equal(child._cur.ref.fixed, fixed)
    np.testing.assert_array_equal(child._cur.ref.free, free)
    assert child._cur.ref.R == remaining
    child.pop()
    assert tuple(child.legal_actions()) == expected
    assert tuple(engine.legal_actions()) == expected


@pytest.mark.parametrize("status", UNKNOWN_STATUSES)
def test_unknown_recenter_status_does_not_cut_engine_actions(h, tiny_a, monkeypatch, status):
    def factory(cls, instance, tables, *, margin_scale=1.0):
        screen = _synthetic_screen(tables, side_limit=1e12, R_total=float(tables.mass.sum()) + 1)
        screen._persistent_unavailable = True
        screen.intervals = lambda ref, cells, n: (np.full(len(cells), np.inf), np.full(len(cells), -np.inf))
        return screen

    monkeypatch.setattr(global_bounds.GlobalScreen, "from_instance", classmethod(factory))
    engine = h.engine(tiny_a, {"global_screen": 1})
    off = h.engine(tiny_a, {"global_screen": 0})
    original_witness = engine._cur.ref
    engine._cur.ref.centered = False
    monkeypatch.setattr(global_bounds, "linprog", lambda *args, **kwargs: _failed_result(status))
    assert tuple(engine.legal_actions()) == tuple(off.legal_actions())
    assert engine.completion_check()["c2"] is True
    assert engine.diagnostics["recenters"] >= 1
    assert engine.diagnostics["lp_indeterminate"] >= 1
    assert not engine._cur.c2_dead
    assert engine._cur.ref is original_witness


@pytest.mark.parametrize("status", UNKNOWN_STATUSES)
@pytest.mark.parametrize("endpoint", [0, 1])
def test_unknown_endpoint_status_does_not_cut_engine_actions(h, tiny_a, monkeypatch, status, endpoint):
    def empty(ref, cells, n):
        return np.full(len(cells), np.inf), np.full(len(cells), -np.inf)

    def factory(cls, instance, tables, *, margin_scale=1.0):
        screen = _synthetic_screen(tables, side_limit=1e12, R_total=float(tables.mass.sum()) + 1)
        screen._persistent_unavailable = True
        screen.intervals = empty
        screen.local_intervals = lambda ref, cells, n, donors: (empty(ref, cells, n), empty(ref, cells, n))
        screen.basis_intervals = empty
        return screen

    monkeypatch.setattr(global_bounds.GlobalScreen, "from_instance", classmethod(factory))
    engine = h.engine(tiny_a, {"global_screen": 1})
    off = h.engine(tiny_a, {"global_screen": 0})
    expected = tuple(off.legal_actions())
    assert expected
    real_linprog = global_bounds.linprog

    def solver(c, *args, **kwargs):
        if endpoint == 1 and c[-1] == 1.:
            return real_linprog(c, *args, **kwargs)
        return _failed_result(status)

    monkeypatch.setattr(global_bounds, "linprog", solver)
    assert tuple(engine.legal_actions()) == expected
    assert engine.diagnostics["lp_indeterminate"] >= 1
    assert engine._cur.c2_sols
    assert all(sol is global_bounds.UNKNOWN_INTERVAL for sol in engine._cur.c2_sols.values())
    before_push = engine._global.lp_indeterminate

    def forbidden_combine(*args, **kwargs):
        pytest.fail("An unknown endpoint result is not a primal witness to combine")

    monkeypatch.setattr(engine._global, "combine", forbidden_combine)
    child = engine.copy()
    child.push(expected[0])
    assert child._global.lp_indeterminate > before_push  # Re-solve the actual child state.
    assert isinstance(child._cur.ref, global_bounds.UnknownReference)
    off.push(expected[0])
    assert tuple(child.legal_actions()) == tuple(off.legal_actions())
    assert child.completion_check()["c2"] is True
    assert not child._cur.c2_dead
    child.pop()
    assert tuple(child.legal_actions()) == expected
    assert tuple(engine.legal_actions()) == expected
