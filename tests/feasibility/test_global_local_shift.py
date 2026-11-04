"""C2 local donor shift (plan 4.4): any mass inside ``local_intervals`` gives a feasible ``local_shift`` reference.
(Endpoints are excluded by a relative 1e-6: they lie exactly on the feasibility tolerance; the engine re-checks
``feasible`` after every push and re-centres if rounding ever crosses it.)"""

from __future__ import annotations

import numpy as np

from stowage.feasibility.global_bounds import GlobalScreen


def _screen(rng, C=8, K=5, T=1):
    A = rng.normal(size=(K, C))
    At = rng.normal(size=(K, T))
    const = np.zeros(K)
    y0 = rng.uniform(0, 1, size=C)
    v0 = A @ y0
    lo, hi = v0 - rng.uniform(0.2, 2, size=K), v0 + rng.uniform(0.2, 2, size=K)
    return GlobalScreen(A, At, const, lo, hi, [0.0] * T, [1.0] * T, u0=1.5)


def test_local_interval_masses_give_feasible_references():
    rng = np.random.default_rng(0)
    checked = 0
    for trial in range(120):
        g = _screen(rng)
        C = g.A.shape[1]
        ref = g.reference(np.zeros(C), np.ones(C, dtype=bool), float(rng.uniform(2, 5)))
        if ref is None:
            continue
        slot_cells = np.array([[i, -1] for i in range(C)])
        slot_n = np.ones(C, dtype=np.int64)
        donors = np.array([[j for j in range(C) if j != i][:4] for i in range(C)])
        (Lu, Uu), (Ld, Ud) = g.local_intervals(ref, slot_cells, slot_n, donors)
        for s in range(C):
            for L, U in ((Lu[s], Uu[s]), (Ld[s], Ud[s])):
                if not (np.isfinite(L) and np.isfinite(U)) or L > U:
                    continue
                lo, hi = max(L, 0.0), min(U, ref.R)
                eps = 1e-6 * max(1.0, hi - lo)          # endpoints sit on the tolerance: rounding only
                if hi - lo <= 2 * eps:
                    continue
                for m in np.linspace(lo + eps, hi - eps, 4):
                    if m > g.u0:
                        continue
                    post = g.local_shift(ref, [s], float(m), donors[s])
                    assert g.feasible(post), (trial, s, m)
                    assert abs(post.R - (ref.R - m)) < 1e-6
                    assert post.y.min() >= -1e-9 and post.y.max() <= g.u0 + 1e-9
                    checked += 1
    assert checked > 100
