"""Never-promoted perf-1028 archive; opt in with --run-feasibility-archive.

Historical shared-source pins intentionally remain fail-closed. This experiment
is excluded before import from the default current-source qualification suite.
"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pytest

pytestmark = pytest.mark.feasibility_archive

from tests.feasibility.test_global_screen import make_screen as _reference_make_screen, random_state, brute_slot_range


_HELPER_PATH = Path(__file__).resolve().parents[2] / "experiments/feasibility/perf-1028/candidate.py"
_SPEC = importlib.util.spec_from_file_location("_perf_1028_witness_candidate", _HELPER_PATH)
_HELPER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _HELPER
_SPEC.loader.exec_module(_HELPER)
_CLASSES = _HELPER.get_candidate_classes()
global_bounds = _CLASSES.global_module
GlobalScreen, UNKNOWN_INTERVAL = _CLASSES.GlobalScreen, _CLASSES.UNKNOWN_INTERVAL


def make_screen(rng):
    """Reuse random coefficients, but always instantiate the isolated candidate."""
    reference = _reference_make_screen(rng)
    return GlobalScreen(reference.A, reference.At, reference.const, reference.lo, reference.hi,
                        reference.tank_lo, reference.tank_hi, reference.u0, reference.names)


def test_endpoint_witnesses_certify_entire_requested_interval_without_a_solve():
    g = GlobalScreen([[1., 1., 1., 1.]], np.zeros((1, 0)), [0.], [12.], [12.], [], [], 10.)
    g.optimized = True
    ref = g.reference(np.zeros(4), np.ones(4, dtype=bool), 12.)
    before = g.lp_calls
    sol = g.slot_interval(ref, [0], 0., 10.)
    assert g.lp_calls == before
    assert g.witness_hits == 1
    assert [sol[0][0], sol[1][0]] == [0., 10.]
    for m, y, t in sol:
        child = g.combine(ref, [0], m, *sol)
        assert child.y.sum() == pytest.approx(child.R, abs=1e-9)
        assert np.all(child.v >= g.lo) and np.all(child.v <= g.hi)


def test_endpoint_reuse_never_extends_a_tight_lp_boundary():
    g = GlobalScreen([[1., 0.], [1., 1.]], np.zeros((2, 0)), [0., 0.],
                     [-np.inf, 1.], [0., 1.], [], [], 10.)
    g.optimized = True
    ref = g.reference(np.zeros(2), np.ones(2, dtype=bool), 1.)
    assert g._witness_interval(ref, [0], 0., 5e-8) is None
    assert g.slot_interval(ref, [0], 5e-8, 5e-8) is None


def test_witness_pool_is_scoped_to_exact_immutable_state_and_revalidates_rows():
    g = GlobalScreen([[1., 1., 1.]], np.zeros((1, 0)), [0.], [6.], [6.], [], [], 10.)
    g.optimized = True
    ref = g.reference(np.zeros(3), np.ones(3, dtype=bool), 6.)
    sol = g.slot_interval(ref, [0], 0., 6.)
    g._remember_endpoints(ref, [0], sol)
    assert len(g._endpoint_witnesses) == 2
    child = g.combine(ref, [0], 3., *sol)
    g._prepare_endpoint_state(child)
    assert not g._endpoint_witnesses
    assert g._endpoint_state[0] is child.fixed
    # Even a malformed imported candidate allocation is no authority to admit.
    child.y = np.full_like(child.y, np.nan)
    g._endpoint_witnesses = [(np.array([0., np.nan, 0.]), np.zeros(0))]
    assert g._witness_interval(child, [1], 0., 2.) is None


@pytest.mark.parametrize("optimized", [False, True])
def test_native_and_endpoint_reuse_match_independent_cold_lp(optimized):
    rng = np.random.default_rng(1028)
    checked = 0
    for _ in range(50):
        g = make_screen(rng)
        g.optimized = optimized
        fixed, free, R = random_state(rng)
        ref = g.reference(fixed, free, R)
        if ref is None:
            continue
        indices = np.flatnonzero(free)
        slots = [[int(i)] for i in indices] + [[int(a), int(b)] for a, b in zip(indices[:-1], indices[1:])]
        for cells in slots:
            upper = min(R, g.u0 * len(cells))
            got = g.slot_interval(ref, cells, 0., upper)
            want = brute_slot_range(g, ref, cells, 0., upper)
            assert got is not UNKNOWN_INTERVAL
            assert (got is None) == (want is None)
            if got is not None:
                assert (got[0][0], got[1][0]) == pytest.approx(want, abs=1e-6)
                checked += 1
    assert checked > 35


def test_endpoint_optimization_retains_physical_basis_certificates():
    from tests.feasibility.test_global_screen import slots_of, feasible_tol
    rng = np.random.default_rng(2810)
    hits = 0
    for _ in range(35):
        g = make_screen(rng)
        g.optimized = True
        fixed, free, R = random_state(rng)
        ref = g.reference(fixed, free, R)
        if ref is None:
            continue
        sc, sn = slots_of(free)
        lower, upper = g.basis_intervals(ref, sc, sn)
        for k in range(len(sc)):
            lo, hi = max(0., lower[k]), min(upper[k], R, g.u0 * sn[k])
            if hi - lo < 1e-6:
                continue
            child = g.basis_shift(ref, sc[k][sc[k] >= 0].tolist(), (lo + hi) / 2.)
            assert feasible_tol(g, child, eps=1e-6)
            assert child.y.sum() == pytest.approx(child.R, abs=1e-6)
            assert np.all(child.y >= -1e-7) and np.all(child.y <= g.u0 + 1e-7)
            hits += 1
    assert hits > 10


def test_two_cell_endpoint_witness_has_equal_shares_and_adjustable_tanks():
    g = GlobalScreen([[1., 1., 1., 1.], [1., -1., 0., 0.]], [[1.], [2.]],
                     [0., 0.], [10., -10.], [20., 10.], [1.], [3.], 10.)
    g.optimized = True
    ref = g.reference(np.zeros(4), np.ones(4, dtype=bool), 12.)
    before = g.lp_calls
    sol = g.slot_interval(ref, [0, 1], 0., 12.)
    assert sol is not None and sol is not UNKNOWN_INTERVAL
    assert g.lp_calls == before
    assert g.witness_hits == 1
    for mass, y, t in sol:
        allocation = y.copy()
        allocation[[0, 1]] = mass / 2.
        assert allocation[0] == allocation[1]
        assert t[0] >= 1. and t[0] <= 3.
        v = g.A @ allocation + g.At @ t + g.const
        assert np.all(v >= g.lo) and np.all(v <= g.hi)
        assert allocation.sum() == pytest.approx(ref.R, abs=1e-9)


@pytest.mark.parametrize("status", [1, 3, 4, 99, None])
def test_witness_miss_keeps_unknown_solver_result_undecided(monkeypatch, status):
    g = GlobalScreen([[1., 0.]], np.zeros((1, 0)), [0.], [-np.inf], [0.], [], [], 10.)
    g.optimized = True
    g._persistent_unavailable = True
    ref = g.reference(np.zeros(2), np.ones(2, dtype=bool), 1.)
    result = SimpleNamespace(success=False, status=status)
    monkeypatch.setattr(global_bounds, "linprog", lambda *args, **kwargs: result)
    # Requested upper endpoint violates x0<=0: a witness miss must call the
    # unchanged LP path, whose numerical/limit result cannot reject a slot.
    assert g.slot_interval(ref, [0], 0., 1.) is UNKNOWN_INTERVAL
    assert g.witness_hits == 0
    assert g.lp_indeterminate == 1


def test_no_new_witness_can_use_out_of_window_or_nonfinite_ballast():
    g = GlobalScreen([[1., 1.]], [[0.]], [0.], [1.], [1.], [0.], [1.], 10.)
    g.optimized = True
    ref = g.reference(np.zeros(2), np.ones(2, dtype=bool), 1.)
    for value in (-1., 2., np.nan, np.inf):
        ref.t = np.array([value])
        assert g._witness_interval(ref, [0], 0., 1.) is None


def test_parent_completion_reused_after_releasing_fixed_cargo():
    g = GlobalScreen([[1., 1., 1.], [1., -1., 0.]], np.zeros((2, 0)), [0., 0.],
                     [12., -10.], [12., 10.], [], [], 10.)
    g.optimized = True
    fixed, free = np.array([3., 0., 0.]), np.array([False, True, True])
    parent = g.reference(fixed, free, 9.)
    calls = g.lp_calls
    child = g.reference(np.zeros(3), np.ones(3, dtype=bool), 12., warm_ref=parent)
    assert child is not None
    assert g.lp_calls == calls and g.parent_witness_hits == 1
    assert np.all(child.v >= g.lo) and np.all(child.v <= g.hi)
    assert child.y.sum() == pytest.approx(child.R, abs=1e-9)
    # Re-centering explicitly requested by the engine must still do its work.
    g.reference(child.fixed, child.free, child.R, warm_ref=child)
    assert g.lp_calls > calls


def test_parent_completion_cannot_bypass_changed_child_bounds(monkeypatch):
    g = GlobalScreen([[1., 0.], [1., 1.]], np.zeros((2, 0)), [0., 0.],
                     [-np.inf, 1.], [0., 1.], [], [], 10.)
    g.optimized = True
    ref = g.reference(np.zeros(2), np.ones(2, dtype=bool), 1.)
    # This child fixes positive mass in the forbidden first cell. No parent
    # witness can survive it; the independent cold LP must retain the rejection.
    assert g.reference(np.array([.5, 0.]), np.array([False, True]), .5, warm_ref=ref) is None
    assert g.parent_witness_hits == 0
