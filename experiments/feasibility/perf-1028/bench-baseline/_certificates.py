"""Primal simplex sensitivity certificates for the necessary C2 relaxation.

Changing a nonbasic cell by d and the basic variables by -B^-1 A_cell d
preserves the LP equations. We validate bounds and physical rows of that explicit
fractional allocation; dual/reduced-cost signs alone never establish feasibility.
A failed certificate is undecided, never a rejection. This is the usual simplex
basis sensitivity identity (Bertsimas & Tsitsiklis, Introduction to Linear
Optimization, 1997, sections 3.1 and 5.1).
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class Sensitivity:
    direction: np.ndarray  # (cells+tanks, cells), physical primal displacement per kg
    eligible: np.ndarray   # only nonbasic cell columns have captured directions
    row_direction: np.ndarray  # physical-row effect, precomputed once per basis


def capture(workspace):
    h = workspace.h
    status, basic = h.getBasicVariables()
    if int(status) != 0:
        return None
    C, T = workspace.C, workspace.T
    P = C + T
    eligible = workspace._upper > workspace._lower
    eligible[basic[(basic >= 0) & (basic < C)]] = False
    physical = (basic >= 0) & (basic < P)
    directions = np.zeros((P, C))
    for col in np.flatnonzero(eligible):
        status, reduced = h.getReducedColumn(int(col))
        if int(status) != 0:
            eligible[col] = False
            continue
        directions[col, col] = 1.
        directions[basic[physical], col] = -reduced[physical]
    # The total-cargo equality must remain exact; numerical failure disables only
    # the sufficient certificate and leaves the original LP fallback available.
    eligible &= np.abs(directions[:C].sum(axis=0)) <= 1e-8
    from scipy.sparse import csr_matrix
    row_direction = csr_matrix(np.hstack([workspace.g.A, workspace.g.At])) @ directions
    return Sensitivity(directions, eligible, row_direction)


def affine(g, ref, cells):
    sensitivity = ref.sensitivity
    if sensitivity is None or not np.all(sensitivity.eligible[cells]):
        return None
    D = sensitivity.direction[:, cells]
    initial = np.r_[ref.y, ref.t]
    return initial - D @ ref.y[cells], D.sum(axis=1) / len(cells)


def intervals(g, ref, slot_cells, slot_n):
    count = len(slot_cells)
    if count == 0 or ref.sensitivity is None:
        return np.full(count, np.inf), np.full(count, -np.inf)
    C = len(ref.y)
    sensitivity = ref.sensitivity
    pad = slot_cells < 0
    cells = np.maximum(slot_cells, 0)
    eligible = np.all(pad | sensitivity.eligible[cells], axis=1)
    chosen_y = np.where(pad, 0., ref.y[cells])
    directions = np.where(pad[None], 0., sensitivity.direction[:, cells])
    c0 = np.r_[ref.y, ref.t][:, None] - (directions * chosen_y[None]).sum(axis=2)
    slope = directions.sum(axis=2) / slot_n[None]
    rowdirections = np.where(pad[None], 0., sensitivity.row_direction[:, cells])
    row0 = ref.v[:, None] - (rowdirections * chosen_y[None]).sum(axis=2)
    rowslope = rowdirections.sum(axis=2) / slot_n[None]
    # Every accepted mass preserves the original LP row bounds. Unlike the
    # historical shift heuristic, new witnesses must not expand these by the
    # broad screen slack and admit masses that the exact endpoint LP rejects.
    a = np.vstack([c0, row0])
    b = np.vstack([slope, rowslope])
    lo = np.r_[np.zeros(C), g.tank_lo, g.lo][:, None]
    hi = np.r_[np.where(ref.free, g.u0, 0.), g.tank_hi, g.hi][:, None]
    positive, negative = b > 1e-14, b < -1e-14
    flat = ~(positive | negative)
    bad = np.any(flat & ((a < lo-1e-9) | (a > hi+1e-9)), axis=0)
    with np.errstate(divide='ignore', invalid='ignore'):
        ll = np.where(positive, (lo-a)/b, np.where(negative, (hi-a)/b, -np.inf))
        uu = np.where(positive, (hi-a)/b, np.where(negative, (lo-a)/b, np.inf))
    L = np.maximum(0., np.max(ll, axis=0))
    U = np.minimum(ref.R, np.min(uu, axis=0))
    return np.where(eligible & ~bad, L, np.inf), np.where(eligible & ~bad, U, -np.inf)


def shift(g, ref, cells, m):
    pair = affine(g, ref, np.asarray(cells))
    if pair is None:
        raise ValueError('slot has no simplex primal certificate')
    c0, slope = pair
    x = c0 + m * slope
    C = len(ref.y)
    y, t = x[:C].copy(), x[C:].copy()
    fixed, free = ref.fixed.copy(), ref.free.copy()
    fixed[cells] += m / len(cells)
    free[cells] = False
    y[cells] = 0.
    return g._make(fixed, free, y, t, max(ref.R-m, 0.), warm_basis=ref.warm_basis,
                   sensitivity=ref.sensitivity)
