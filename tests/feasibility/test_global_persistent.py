"""Persistent C2 LP must retain a model/basis without changing the relaxation."""
import numpy as np
import pytest

from stowage.feasibility.global_bounds import GlobalScreen
from tests.feasibility.test_global_screen import make_screen, random_state, brute_slot_range


def test_c2_reuses_native_model_and_parent_basis():
    g = GlobalScreen([[1., -1., 0., 0.], [1., 1., 1., 1.]], np.zeros((2, 0)),
                     [0., 0.], [-2., 0.], [2., 40.], [], [], 10.)
    ref = g.reference(np.zeros(4), np.ones(4, dtype=bool), 12.)
    assert ref is not None
    assert g._persistent is not None
    assert ref.warm_basis is not None
    for cells in ([0], [1, 2], [2], [0, 3]):
        sol = g.slot_interval(ref, cells, 0., 12.)
        assert sol is not None
    sol = g.slot_interval(ref, [0], 0., 10.)
    child = g.combine(ref, [0], 3., *sol)
    new = g.reference(child.fixed, child.free, child.R, warm_ref=child)
    assert new is not None
    assert g._persistent.model_builds == 1
    assert g._persistent.basis_starts >= 1


def test_persistent_slot_intervals_match_cold_lp_seeded():
    rng = np.random.default_rng(743)
    checked = 0
    for _ in range(70):
        g = make_screen(rng)
        fixed, free, R = random_state(rng)
        ref = g.reference(fixed, free, R)
        if ref is None:
            continue
        f = np.flatnonzero(free)
        for cells in ([int(i)] for i in f):
            got = g.slot_interval(ref, cells, 0., min(R, g.u0))
            want = brute_slot_range(g, ref, cells, 0., min(R, g.u0))
            assert (got is None) == (want is None)
            if got is not None:
                assert (got[0][0], got[1][0]) == pytest.approx(want, abs=1e-6)
                checked += 1
        for a, b in zip(f[:-1], f[1:]):
            cells = [int(a), int(b)]
            got = g.slot_interval(ref, cells, 0., min(R, 2*g.u0))
            want = brute_slot_range(g, ref, cells, 0., min(R, 2*g.u0))
            assert (got is None) == (want is None)
            if got is not None:
                assert (got[0][0], got[1][0]) == pytest.approx(want, abs=1e-6)
                checked += 1
    assert checked > 60


def test_basis_certificate_is_a_feasible_primal_completion():
    from tests.feasibility.test_global_screen import slots_of, feasible_tol
    rng = np.random.default_rng(914)
    hits = 0
    for _ in range(80):
        g = make_screen(rng)
        fixed, free, R = random_state(rng)
        ref = g.reference(fixed, free, R)
        if ref is None:
            continue
        sc, sn = slots_of(free)
        L, U = g.basis_intervals(ref, sc, sn)
        for k in range(len(sc)):
            low, high = max(0., L[k]), min(U[k], R, g.u0*sn[k])
            if high-low <= 1e-6:
                continue
            cells = sc[k][sc[k] >= 0].tolist()
            for th in (.01, .5, .99):
                m = low + th * (high-low)
                child = g.basis_shift(ref, cells, m)
                assert feasible_tol(g, child, eps=1e-6)
                assert np.all(child.y >= -1e-7)
                assert np.all(child.y <= g.u0+1e-7)
                assert child.y.sum() == pytest.approx(child.R, abs=1e-6)
                assert np.all(child.t >= g.tank_lo-1e-7)
                assert np.all(child.t <= g.tank_hi+1e-7)
                hits += 1
                # Reusing the basis on a child must respect newly fixed bounds.
                sc2, sn2 = slots_of(child.free)
                L2, U2 = g.basis_intervals(child, sc2, sn2)
                for j in np.flatnonzero(U2-L2 > 1e-5)[:2]:
                    cs = sc2[j][sc2[j] >= 0].tolist()
                    mm = (L2[j]+U2[j])/2
                    grandchild = g.basis_shift(child, cs, mm)
                    assert feasible_tol(g, grandchild, eps=1e-6)
                    assert grandchild.y.sum() == pytest.approx(grandchild.R, abs=1e-6)
    assert hits > 30


def test_c2_without_private_highs_binding_keeps_cold_fallback():
    rng = np.random.default_rng(17)
    g = make_screen(rng)
    g._persistent_unavailable = True
    fixed = np.zeros(g.A.shape[1])
    free = np.ones(g.A.shape[1], dtype=bool)
    ref = g.reference(fixed, free, 20.)
    assert ref is not None
    assert ref.warm_basis is None
    got = g.slot_interval(ref, [0, 1], 0., 20.)
    want = brute_slot_range(g, ref, [0, 1], 0., 20.)
    assert (got[0][0], got[1][0]) == pytest.approx(want, abs=1e-6)
    L, U = g.basis_intervals(ref, np.array([[0, -1]]), np.array([1]))
    assert L[0] > U[0]  # unavailable certificate is undecided, not a rejection


def test_native_infeasibility_requires_independent_cold_confirmation(monkeypatch):
    # Production warm-started simplex returned kInfeasible at a feasible child
    # (small/1704/depth2). A numerical failure must not become a cached rejection.
    g = GlobalScreen([[1., 0.]], np.zeros((1, 0)), [0.], [-np.inf], [4.], [], [], 10.)
    ref = g.reference(np.zeros(2), np.ones(2, dtype=bool), 8.)
    assert ref is not None
    monkeypatch.setattr(g._persistent, "interval", lambda *args, **kwargs: None)
    got = g.slot_interval(ref, [0], 0., 8.)
    assert got is not None
    assert (got[0][0], got[1][0]) == pytest.approx((0., 4.))
    # Independent confirmation retains genuinely infeasible rejections.
    assert g.slot_interval(ref, [0], 5., 8.) is None


def test_native_center_failure_uses_cold_witness_without_wrong_basis(monkeypatch):
    g = GlobalScreen([[1., 0.]], np.zeros((1, 0)), [0.], [-np.inf], [4.], [], [], 10.)
    parent = g.reference(np.zeros(2), np.ones(2, dtype=bool), 8.)
    assert parent is not None
    monkeypatch.setattr(g._persistent, "center", lambda *args, **kwargs: None)
    child = g.reference(parent.fixed, parent.free, parent.R, warm_ref=parent)
    assert child is not None and g.feasible(child)
    # A cold solution has no corresponding basis in the failed native model.
    assert child.warm_basis is None and child.sensitivity is None
    assert g._persistent is None


def test_new_basis_certificate_does_not_expand_exact_lp_boundary():
    # The old broad row-screen tolerance is 1e-7 here. A new basis witness must
    # respect the actual LP boundary: x0 <= 0, not x0 <= screen tolerance.
    g = GlobalScreen([[1., 0.], [1., 1.]], np.zeros((2, 0)), [0., 0.],
                     [-np.inf, 0.], [0., 10.], [], [], 10.)
    ref = g.reference(np.zeros(2), np.ones(2, dtype=bool), 1.)
    assert ref is not None
    mass = 5e-8
    assert g.slot_interval(ref, [0], mass, mass) is None
    L, U = g.basis_intervals(ref, np.array([[0, -1]]), np.array([1]))
    assert not (L[0] <= mass <= U[0])
    assert U[0] <= 1e-9


def test_native_c2_copy_pop_parent_basis_matches_cold_engine(h, tiny_a, monkeypatch):
    import random
    from stowage.feasibility import global_bounds
    from tests.feasibility.test_engine_v2 import _synthetic_screen
    use_cold = [False]

    def factory(cls, instance, tables, *, margin_scale=1.):
        total = float(tables.mass.sum())
        g = _synthetic_screen(tables, .15*total, total+1.)
        g._persistent_unavailable = use_cold[0]
        # Exercise exact endpoint/basis witnesses rather than legacy shift paths.
        def empty(ref, sc, sn):
            return np.full(len(sc), np.inf), np.full(len(sc), -np.inf)
        g.intervals = empty
        g.local_intervals = lambda ref, sc, sn, donors: (empty(ref, sc, sn), empty(ref, sc, sn))
        return g

    monkeypatch.setattr(global_bounds.GlobalScreen, 'from_instance', classmethod(factory))
    comparisons = 0
    for seed in (19, 84):
        use_cold[0] = False
        native = h.engine(tiny_a, {'global_screen': 1})
        use_cold[0] = True
        cold = h.engine(tiny_a, {'global_screen': 1})
        rng = random.Random(seed)
        for _ in range(4):
            assert tuple(native.legal_actions()) == tuple(cold.legal_actions())
            legal = tuple(cold.legal_actions())
            if not legal:
                break
            # Sibling work mutates their shared native workspace; the original
            # immutable reference must still warm-start and answer identically.
            sibling = native.copy()
            sibling.push(rng.choice(legal))
            sibling.legal_actions()
            action = rng.choice(legal)
            native.push(action)
            cold.push(action)
            assert tuple(native.legal_actions()) == tuple(cold.legal_actions())
            native.pop()
            cold.pop()
            assert tuple(native.legal_actions()) == legal
            action = rng.choice(legal)
            native.push(action)
            cold.push(action)
            assert tuple(native.legal_actions()) == tuple(cold.legal_actions())
            comparisons += 1
        assert native._global._persistent.model_builds == 1
        assert native._global._persistent.basis_starts > 0
    assert comparisons >= 4
