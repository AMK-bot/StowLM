"""C2 unit tests: ``GlobalScreen`` on small synthetic matrices vs brute-force LPs (scipy linprog / HiGHS).

Rows (synthetic, not a real ship): a longitudinal moment row with a tank, a transverse moment row, a stack-mass row
over cells 0–2 and a displacement row. Six cells, two adjustable tanks.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import linprog

from stowage.feasibility.global_bounds import GlobalScreen

C = 6
U0 = 10.0
LEVER_X = np.array([-3.0, -1.0, 1.0, 3.0, -2.0, 2.0])
LEVER_Y = np.array([-1.0, 1.0, -1.0, 1.0, 1.0, -1.0])
TOL = 1e-5


def make_screen(rng: np.random.Generator) -> GlobalScreen:
    A = np.vstack([LEVER_X, LEVER_Y, [1, 1, 1, 0, 0, 0], np.ones(C)])
    At = np.array([[4.0, -2.0], [0.5, -0.5], [0.0, 0.0], [1.0, 1.0]])
    const = np.array([rng.uniform(-5, 5), rng.uniform(-2, 2), 0.0, 5.0])
    lo = np.array([-rng.uniform(2, 8), -rng.uniform(1, 4), -np.inf, 5.0])
    hi = np.array([rng.uniform(2, 8), rng.uniform(1, 4), rng.uniform(12, 25), 80.0])
    return GlobalScreen(A, At, const, lo, hi, [0.0, 0.0], [rng.uniform(2, 6), rng.uniform(2, 6)], U0,
                        ("lcg", "tcg", "stack0", "disp"))


def _rows(g, base, cols):
    up, dn = np.isfinite(g.hi), np.isfinite(g.lo)
    return (np.vstack([cols[up], -cols[dn]]),
            np.concatenate([g.hi[up] - base[up], base[dn] - g.lo[dn]]))


def feasible_tol(g, ref, eps=1e-9) -> bool:
    """``g.feasible`` plus float round-off: interval endpoints land exactly on ``lo - slack`` / ``hi + slack``."""
    sl = g._slack() + eps
    return bool(np.all(ref.v >= g.lo - sl) and np.all(ref.v <= g.hi + sl))


def brute_feasible(g, fixed, free, R) -> bool:
    idx = np.flatnonzero(free)
    base = g.A @ fixed + g.const
    A_ub, b_ub = _rows(g, base, np.hstack([g.A[:, idx], g.At]))
    nT = g.At.shape[1]
    A_eq = np.concatenate([np.ones(len(idx)), np.zeros(nT)])[None]
    res = linprog(np.zeros(len(idx) + nT), A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=[R],
                  bounds=[(0, U0)] * len(idx) + list(zip(g.tank_lo, g.tank_hi)), method="highs")
    return res.status == 0


def brute_slot_range(g, ref, cells, m_lo, m_hi):
    free = ref.free.copy()
    free[cells] = False
    idx = np.flatnonzero(free)
    base = g.A @ ref.fixed + g.const
    cols = np.hstack([g.A[:, idx], g.At, g.A[:, cells].mean(axis=1)[:, None]])
    A_ub, b_ub = _rows(g, base, cols)
    nT = g.At.shape[1]
    A_eq = np.concatenate([np.ones(len(idx)), np.zeros(nT), [1.0]])[None]
    bounds = [(0, U0)] * len(idx) + list(zip(g.tank_lo, g.tank_hi)) + [(m_lo, m_hi)]
    out = []
    for sign in (1.0, -1.0):
        c = np.zeros(len(idx) + nT + 1)
        c[-1] = sign
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=[ref.R], bounds=bounds, method="highs")
        if res.status != 0:
            return None
        out.append(res.x[-1])
    return out[0], out[1]


def random_state(rng):
    free = rng.random(C) < 0.7
    if not free.any():
        free[rng.integers(C)] = True
    fixed = np.where(free, 0.0, rng.uniform(0, U0, C))
    R = rng.uniform(0, U0 * free.sum())
    return fixed, free, R


def check_ref(g, ref, fixed, free, R):
    assert g.feasible(ref)
    assert np.isclose(ref.y.sum(), R, atol=1e-6)
    assert np.all(ref.y >= -1e-9) and np.all(ref.y <= U0 + 1e-9)
    assert np.all(ref.y[~free] == 0)
    v = g.A @ (fixed + ref.y) + g.At @ ref.t + g.const
    assert np.allclose(ref.v, v, atol=1e-6)
    assert np.all(ref.t >= g.tank_lo - 1e-9) and np.all(ref.t <= g.tank_hi + 1e-9)


def slots_of(free):
    """20' slots (one free cell) and 40' slots (pairs of free cells) as (S, 2) -1 padded + slot_n."""
    f = np.flatnonzero(free).tolist()
    rows = [[c, -1] for c in f] + [[a, b] for i, a in enumerate(f) for b in f[i + 1:]]
    sc = np.array(rows, dtype=np.int64).reshape(-1, 2)
    return sc, (sc >= 0).sum(axis=1)


def test_reference_iff_brute_lp():
    rng = np.random.default_rng(0)
    agree = {True: 0, False: 0}
    for trial in range(300):
        g = make_screen(rng)
        fixed, free, R = random_state(rng)
        ref = g.reference(fixed, free, R)
        want = brute_feasible(g, fixed, free, R)
        assert (ref is not None) == want, (trial, fixed, free, R)
        agree[want] += 1
        if ref is not None:
            check_ref(g, ref, fixed, free, R)
    assert agree[True] > 30 and agree[False] > 30, agree   # both outcomes exercised


def _refs(n, seed):
    rng = np.random.default_rng(seed)
    out = []
    while len(out) < n:
        g = make_screen(rng)
        fixed, free, R = random_state(rng)
        ref = g.reference(fixed, free, R)
        if ref is not None and free.sum() >= 2:
            out.append((g, ref, rng))
    return out


def test_intervals_shift_feasible():
    hit = 0
    for g, ref, rng in _refs(60, 1):
        sc, sn = slots_of(ref.free)
        L, U = g.intervals(ref, sc, sn)
        for k in range(len(sc)):
            # 20' slot: m = the reference's own mass there leaves the reference unchanged, so it is inside
            if sn[k] == 1:
                ys = float(ref.y[sc[k, 0]])
                assert L[k] <= ys + 1e-6 and U[k] >= ys - 1e-6, (k, ys, L[k], U[k])
            lo, hi = max(L[k], 0.0), min(U[k], ref.R)
            if lo > hi:
                continue
            cells = [int(c) for c in sc[k] if c >= 0]
            for m in {lo, hi, lo + (hi - lo) * rng.random()}:
                new = g.shift(ref, cells, m)
                hit += 1
                assert feasible_tol(g, new), (k, m, L[k], U[k])
                if lo < m < hi:
                    assert g.feasible(new)
                assert np.all(new.y >= -1e-9) and np.all(new.y <= U0 * (1 + 1e-9) + 1e-9)
                assert np.isclose(new.y.sum(), ref.R - m, atol=1e-6)
                fixed = ref.fixed.copy()
                fixed[cells] += m / len(cells)
                assert np.allclose(new.v, g.A @ (fixed + new.y) + g.At @ new.t + g.const, atol=1e-6)
    assert hit > 300


def test_slot_interval_matches_lp():
    checked = 0
    for g, ref, rng in _refs(40, 2):
        sc, _ = slots_of(ref.free)
        for row in sc[: 6]:
            cells = [int(c) for c in row if c >= 0]
            m_lo, m_hi = 0.0, ref.R
            got = g.slot_interval(ref, cells, m_lo, m_hi)
            want = brute_slot_range(g, ref, cells, m_lo, m_hi)
            assert (got is None) == (want is None), (cells, got, want)
            if got is None:
                continue
            checked += 1
            assert got[0][0] == pytest.approx(want[0], abs=TOL)
            assert got[1][0] == pytest.approx(want[1], abs=TOL)
            # combine: interior masses give a feasible reference for the post-state
            for th in (0.0, 0.3, 0.5, 1.0):
                m = got[0][0] + th * (got[1][0] - got[0][0])
                new = g.combine(ref, cells, m, got[0], got[1])
                assert feasible_tol(g, new, 1e-6), (cells, m)
                assert np.isclose(new.y.sum(), ref.R - m, atol=1e-5)
                assert np.all(new.y[cells] == 0) and np.all(new.y <= U0 + 1e-6)
    assert checked > 50


def test_release_keeps_rows():
    n = 0
    for g, ref, rng in _refs(40, 3):
        occupied = np.flatnonzero(ref.fixed > 0).tolist()
        if not occupied:
            continue
        pick = occupied[: 1 + int(rng.integers(len(occupied)))]
        released = [([c], float(ref.fixed[c])) for c in pick]
        new = g.release(ref, released)
        n += 1
        assert np.allclose(new.v, ref.v)
        assert np.allclose(new.v, g.A @ (new.fixed + new.y) + g.At @ new.t + g.const, atol=1e-6)
        assert new.R == pytest.approx(ref.R + sum(m for _, m in released))
        assert np.isclose(new.y.sum(), new.R, atol=1e-6)
        assert all(new.free[c] for c in pick)
        assert g.feasible(new)
    assert n > 10
