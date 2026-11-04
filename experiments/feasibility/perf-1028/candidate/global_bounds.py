"""C2 — global stability/strength screen for the feasibility engine (plan 4.4 v2).

**Question asked at every state:** can the boxes still to be loaded, treated as divisible mass, be spread over the
free cells (each cell carrying at most ``u0`` kg, the largest per-cell share of any box in the instance), together
with some adjustable ballast, so that every 3.7 linear stability/strength row (GM, trim, list, draught,
displacement, cargo mass, tank windows, shear force and bending moment per station — D49 outward margins qualified
on independent physical holdouts) and every stack-mass row holds? This *fractional completion* is a linear program (LP); if it
has no solution, no real completion exists either (a real completion is one particular fractional one), so
rejecting is empirically justified within the qualified domain. If it has one, that proves nothing about the real stow — the checker still judges the final
plan. Source: LP relaxation of an assignment problem, Bertsimas & Tsitsiklis, *Introduction to Linear Optimization*
(1997), ch. 1; HiGHS via scipy.

**Making it fast (once per state, not per candidate).** We keep one feasible fractional completion — the
*reference* ``(y, t)``: mass ``y_c`` per free cell, ballast ``t`` — and test each candidate "box of mass ``m`` into
slot ``s``" with a closed form: put ``m`` in ``s`` and scale every other free cell's mass by
``λ = (R − m)/(R − y_s)`` so the total still equals the remaining mass ``R``. Every row value is then an affine
function of ``m`` (``c0 + m·g``), so per open slot we get an interval of masses for which the shifted reference
is still feasible: a *sufficient* test, vectorised over slots. A second sufficient certificate changes
nonbasic cells and repairs basic variables through the stored simplex basis. Both certificates construct
explicit feasible primal allocations; failure of either is undecided. A candidate outside their intervals gets
the exact answer from a warm-started LP of its own slot (min and max feasible ``m``; feasible masses form an interval because the LP's
feasible set is convex). After a push the shifted reference (or a convex combination of the two LP endpoint
solutions) is a valid reference for the new state, so no LP is needed on the common path. The reference is
re-centred (an LP maximising the smallest row slack, in units of one full cell's effect) when it gets too tight.

APPROXIMATION (labelled): the cell cap ``u0`` is the largest per-cell share over all boxes of the instance, not of
the boxes still to come (a weaker but still valid relaxation); stack-mass rows use the largest of a stack's mixed /
20' / 40' limits (optimistic, valid for rejection); both halves of a long box are split equally (as 3.7 does).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from scipy.optimize import OptimizeWarning, linprog

GLOBAL_RULE_IDS = ("iscode.stability.margin", "iacs.strength.margin")
_TOL = 1e-7


class LPIndeterminate(RuntimeError):
    """The solver did not establish either an optimum or infeasibility."""


@dataclass(frozen=True)
class UnknownReference:
    """An undecided relaxation, deliberately carrying no primal/basis witness."""

    fixed: np.ndarray
    free: np.ndarray
    R: float


@dataclass(frozen=True)
class _UnknownInterval:
    """Undecided slot masses; no endpoint allocation may be inferred."""


UNKNOWN_INTERVAL = _UnknownInterval()


@dataclass
class Reference:
    """One feasible fractional completion. Arrays are replaced, never mutated in place (copies share them)."""

    fixed: np.ndarray   # (C,) kg of departure cargo fixed in each cell
    free: np.ndarray    # (C,) bool: cell may receive mass in the model
    y: np.ndarray       # (C,) reference mass in free cells (0 elsewhere)
    t: np.ndarray       # (T,) reference ballast in adjustable tanks
    R: float            # remaining mass (sum of y)
    v: np.ndarray       # (K,) row values A(fixed + y) + At t + const
    Y: np.ndarray       # (K,) A y
    centered: bool = False
    warm_basis: object = None  # immutable native basis snapshot for child-state simplex repair
    sensitivity: object = None  # fixed-basis physical primal displacement certificates


class GlobalScreen:
    """Rows ``lo ≤ A·cellmass + At·t + const ≤ hi`` over the engine's cell order.

    Build with ``from_instance`` (3.7 linear model) or directly from matrices (tests). Physical rows are
    immutable; the mutable simplex workspace is shared across references, each of which retains its own
    immutable parent basis/sensitivity snapshot.
    """

    def __init__(self, A, At, const, lo, hi, tank_lo, tank_hi, u0, names=()):
        self.A = np.asarray(A, dtype=float)
        self.At = np.asarray(At, dtype=float).reshape(self.A.shape[0], -1)
        self.const = np.asarray(const, dtype=float)
        self.lo = np.asarray(lo, dtype=float)
        self.hi = np.asarray(hi, dtype=float)
        self.tank_lo = np.asarray(tank_lo, dtype=float)
        self.tank_hi = np.asarray(tank_hi, dtype=float)
        self.u0 = float(u0)
        self.names = tuple(names)
        self.lp_calls = 0
        self.lp_time_s = 0.0
        self.lp_indeterminate = 0
        # Diagnostic switch: False retains the pre-1028 native formulation.
        self.optimized = False
        self.witness_hits = 0
        self.parent_witness_hits = 0
        self._endpoint_state = None
        self._endpoint_witnesses = []
        self._endpoint_donors = {}
        self._normalized_A = None
        self._persistent = None
        self._persistent_unavailable = False
        # slack unit per row: the effect of one full cell (≥ tiny), so τ is "cells of slack"
        self.w = np.maximum(np.abs(self.A).max(axis=1) * self.u0, 1e-6)

    # ------------------------------------------------------------------ construction from 3.7
    @classmethod
    def from_instance(cls, instance, tables, *, margin_scale: float = 1.0) -> GlobalScreen:
        """Raises ValueError if the 3.7 model can't be built (e.g. missing strength stations)."""
        from stowage.linear import band_for_instance, build_linear_model, linear_constraints

        # cheap pre-check: 3.7 needs strength stations; failing here avoids minutes of hydrostatics first
        if not getattr(instance.vessel.hull, "stations", None):
            raise ValueError("linear model requires permissible strength stations")
        band = band_for_instance(instance)
        model = build_linear_model(instance, band=band)
        # D49: rejecting a completion needs outward (necessary) bounds. Inward
        # reserves belong to constructing plans, and can falsely prune here.
        cs = linear_constraints(model, direction="necessary", margin_scale=margin_scale)
        C = len(tables.cell_ids)
        col = np.array([tables.cell_index[c] for c in model.cell_ids])
        A = np.zeros((len(cs.names), C))
        A[:, col] = cs.a_cell
        tanks = np.flatnonzero(model.tank_adjustable)
        windows = getattr(model, "_tank_mass_bounds_kg", {})
        bounds = [windows.get(model.tank_ids[i], (0.0, float(model.tank_capacity_kg[i]))) for i in tanks]
        keep = np.isfinite(cs.lb) | np.isfinite(cs.ub)
        # stack-mass rows (largest applicable limit: optimistic, valid for rejection)
        J = len(tables.stack_ids)
        S = np.zeros((J, C))
        for j, cells in enumerate(tables.stack_cells):
            S[j, cells] = 1.0
        caps = np.maximum.reduce([tables.stack_lim_mixed, tables.stack_lim_20, tables.stack_lim_40])
        A_all = np.vstack([A[keep], S])
        At_all = np.vstack([cs.a_tank[keep][:, tanks], np.zeros((J, len(tanks)))])
        const = np.concatenate([cs.const[keep], np.zeros(J)])
        lo = np.concatenate([cs.lb[keep], np.full(J, -np.inf)])
        hi = np.concatenate([cs.ub[keep], caps])
        names = tuple(n for n, k in zip(cs.names, keep) if k) + tuple(f"stack_mass@{s}" for s in tables.stack_ids)
        u0 = float(tables.share.max()) if len(tables.share) else 0.0
        return cls(A_all, At_all, const, lo, hi, [b[0] for b in bounds], [b[1] for b in bounds], u0, names)

    # ------------------------------------------------------------------ references
    def reference(self, fixed: np.ndarray, free: np.ndarray, R: float, *, warm_ref: Reference | None = None) -> Reference | UnknownReference | None:
        """A witness, an explicit undecided marker, or None only for proven LP infeasibility."""
        if self.optimized and isinstance(warm_ref, Reference):
            # An explicit request to re-center the same witness still solves.
            # A changed child may retain the parent's complete allocation:
            # only the split between fixed cargo and divisible mass changes.
            same = fixed is warm_ref.fixed and free is warm_ref.free and R == warm_ref.R
            if not same:
                y = warm_ref.fixed + warm_ref.y - fixed
                self._repair_mass(y, np.flatnonzero(free), R)
                if self._valid_primal(fixed, free, y, warm_ref.t, R):
                    self.parent_witness_hits += 1
                    return self._make(fixed, free, y, warm_ref.t, R,
                                      warm_basis=warm_ref.warm_basis, sensitivity=warm_ref.sensitivity)
        try:
            sol = self._center(fixed, free, R,
                               warm_ref=None if isinstance(warm_ref, UnknownReference) else warm_ref)
        except LPIndeterminate:
            if self._persistent is not None:
                self._persistent.reset_solution()
            return UnknownReference(fixed, free, float(R))
        if sol is None:
            return None
        y, t = sol
        basis = self._persistent.basis() if self._persistent is not None else None
        sensitivity = None
        if self._persistent is not None:
            from stowage.feasibility._certificates import capture
            sensitivity = capture(self._persistent)
        return self._make(fixed, free, y, t, R, centered=True, warm_basis=basis, sensitivity=sensitivity)

    def _make(self, fixed, free, y, t, R, centered=False, warm_basis=None, sensitivity=None) -> Reference:
        Y = self.A @ y
        v = self.A @ fixed + Y + self.At @ t + self.const
        return Reference(fixed, free, y, t, float(R), v, Y, centered, warm_basis, sensitivity)

    def _repair_mass(self, y, donors, R):
        """Adjust one donor to remove redistribution roundoff, then validate separately."""
        residual = float(R) - float(y.sum())
        if residual and np.isfinite(residual):
            room = self.u0 - y[donors] if residual >= 0. else y[donors]
            repair = donors[room >= abs(residual)]
            if len(repair):
                y[repair[0]] += residual

    def _valid_primal(self, fixed, free, y, tanks, R, *, base=None):
        """Validate an allocation against the original LP, without screen slack."""
        if (not np.isfinite(R) or not np.all(np.isfinite(y)) or not np.all(np.isfinite(tanks))
                or np.any(y < 0.) or np.any(y > self.u0) or np.any(y[~free] != 0.)
                or np.any(tanks < self.tank_lo) or np.any(tanks > self.tank_hi)
                or abs(float(y.sum()) - R) > 1e-9):
            return False
        if base is None:
            base = self.A @ fixed + self.const
        v = base + self.A @ y + self.At @ tanks
        return bool(np.all(np.isfinite(v)) and np.all(v >= self.lo) and np.all(v <= self.hi))

    def feasible(self, ref: Reference) -> bool:
        # This predicate answers whether C2 may reject, not whether an
        # undecided solve supplied a primal certificate. Never invent one.
        if isinstance(ref, UnknownReference):
            return True
        return bool(np.all(ref.v >= self.lo - self._slack()) and np.all(ref.v <= self.hi + self._slack()))

    def _slack(self):
        return _TOL * np.maximum(1.0, np.abs(np.where(np.isfinite(self.hi), self.hi, 0)) +
                                 np.abs(np.where(np.isfinite(self.lo), self.lo, 0)))

    # ------------------------------------------------------------------ screen
    def intervals(self, ref: Reference, slot_cells: np.ndarray, slot_n: np.ndarray):
        """For each slot (rows of ``slot_cells``, -1 padded), the masses ``[L, U]`` for which the shifted
        reference stays feasible. Returns (L, U) arrays (empty interval: L > U)."""
        n_s = len(slot_cells)
        if n_s == 0:
            return np.zeros(0), np.zeros(0)
        pad = slot_cells < 0
        sc = np.maximum(slot_cells, 0)
        ycell = np.where(pad, 0.0, ref.y[sc])                   # (S,2)
        ys = ycell.sum(axis=1)                                  # (S,)
        Acells = self.A[:, sc]                                  # (K,S,2)
        Acells = np.where(pad[None], 0.0, Acells)
        a_s = Acells.sum(axis=2) / slot_n[None, :]              # (K,S)
        Acy = (Acells * ycell[None]).sum(axis=2)                # (K,S)
        Z = ref.Y[:, None] - Acy                                # (K,S)
        D = ref.R - ys                                          # (S,)
        ok_d = D > 1e-9
        Dsafe = np.where(ok_d, D, 1.0)
        g = a_s - Z / Dsafe[None, :]
        c0 = (ref.v - ref.Y)[:, None] + Z * (ref.R / Dsafe)[None, :]
        slack = self._slack()[:, None]
        lo = (self.lo[:, None] - slack - c0)
        hi = (self.hi[:, None] + slack - c0)
        with np.errstate(divide="ignore", invalid="ignore"):
            pos, neg = g > 1e-15, g < -1e-15
            L = np.where(pos, lo / g, np.where(neg, hi / g, -np.inf))
            U = np.where(pos, hi / g, np.where(neg, lo / g, np.inf))
        # rows with g≈0 must already hold
        flat_bad = (~pos & ~neg) & ((c0 < self.lo[:, None] - slack) | (c0 > self.hi[:, None] + slack))
        Ls = np.nanmax(np.where(np.isnan(L), -np.inf, L), axis=0)
        Us = np.nanmin(np.where(np.isnan(U), np.inf, U), axis=0)
        Ls = np.where(flat_bad.any(axis=0), np.inf, Ls)
        # scaling the other cells up (m < y_s) must respect the cell cap u0
        ymax = self._ymax_excluding(ref, slot_cells)
        with np.errstate(divide="ignore", invalid="ignore"):
            cap_lo = np.where(ymax > 1e-9, ref.R - self.u0 * D / np.where(ymax > 1e-9, ymax, 1.0), -np.inf)
        Ls = np.maximum(Ls, cap_lo)
        Us = np.minimum(Us, ref.R + 1e-9)
        # all remaining mass sits in this slot: only m == R keeps the shift valid
        Ls = np.where(ok_d, Ls, np.maximum(Ls, ref.R - 1e-6))
        return Ls, Us

    def _row_interval(self, c0: np.ndarray, g: np.ndarray):
        """Masses m with lo ≤ c0 + m·g ≤ hi on every row (columns = slots): (L, U); empty when L > U."""
        slack = self._slack()[:, None]
        lo = self.lo[:, None] - slack - c0
        hi = self.hi[:, None] + slack - c0
        with np.errstate(divide="ignore", invalid="ignore"):
            pos, neg = g > 1e-15, g < -1e-15
            L = np.where(pos, lo / g, np.where(neg, hi / g, -np.inf))
            U = np.where(pos, hi / g, np.where(neg, lo / g, np.inf))
        flat_bad = (~pos & ~neg) & ((c0 < self.lo[:, None] - slack) | (c0 > self.hi[:, None] + slack))
        Ls = np.nanmax(np.where(np.isnan(L), -np.inf, L), axis=0)
        Us = np.nanmin(np.where(np.isnan(U), np.inf, U), axis=0)
        return np.where(flat_bad.any(axis=0), np.inf, Ls), Us

    def local_intervals(self, ref: Reference, slot_cells: np.ndarray, slot_n: np.ndarray, donors: np.ndarray):
        """Local shift (sufficient test, like ``intervals``): put the box's mass m in the slot and balance the
        difference against the slot's nearby free cells ``donors`` (S, k; -1 padded) — taken from them in
        proportion to their reference mass when m ≥ the slot's reference mass y_s, given to them in proportion to
        their remaining cap room when m ≤ y_s. Row values stay nearly unchanged, so far more candidates pass than
        under the ship-wide scaling. Returns ((L_up, U_up), (L_down, U_down)) per slot."""
        pad = slot_cells < 0
        sc = np.maximum(slot_cells, 0)
        ycell = np.where(pad, 0.0, ref.y[sc])
        ys = ycell.sum(axis=1)
        Acells = np.where(pad[None], 0.0, self.A[:, sc])
        a_s = Acells.sum(axis=2) / slot_n[None, :]
        base = ref.v[:, None] - (Acells * ycell[None]).sum(axis=2)
        dm = donors >= 0
        D = np.maximum(donors, 0)
        ok_d = dm & ref.free[D]
        yd = np.where(ok_d, ref.y[D], 0.0)
        rd = np.where(ok_d, np.maximum(self.u0 - ref.y[D], 0.0), 0.0)
        Wy, Wr = yd.sum(axis=1), rd.sum(axis=1)
        AD = self.A[:, D]                                         # (K, S, k)
        ay = (AD * yd[None]).sum(axis=2) / np.maximum(Wy, 1e-12)[None, :]
        ar = (AD * rd[None]).sum(axis=2) / np.maximum(Wr, 1e-12)[None, :]
        Lu, Uu = self._row_interval(base + ys[None, :] * ay, a_s - ay)
        Ld, Ud = self._row_interval(base + ys[None, :] * ar, a_s - ar)
        Lu, Uu = np.maximum(Lu, ys), np.minimum(Uu, np.where(Wy > 0, ys + Wy, ys))
        Ld, Ud = np.maximum(Ld, np.where(Wr > 0, ys - Wr, ys)), np.minimum(Ud, ys)
        return (Lu, Uu), (Ld, Ud)

    def local_shift(self, ref: Reference, cells: list[int], m: float, donors: np.ndarray) -> Reference:
        """The reference after the local shift of ``local_intervals`` (caller checked the interval)."""
        y = ref.y.copy()
        ys = float(y[cells].sum())
        y[cells] = 0.0
        D = donors[(donors >= 0)]
        D = D[ref.free[D]]
        if m >= ys:
            w = y[D]
            if w.sum() > 0:
                y[D] -= (m - ys) * w / w.sum()
        else:
            r = np.maximum(self.u0 - y[D], 0.0)
            if r.sum() > 0:
                y[D] += (ys - m) * r / r.sum()
        y = np.clip(y, 0.0, self.u0)
        fixed = ref.fixed.copy()
        fixed[cells] += m / len(cells)
        free = ref.free.copy()
        free[cells] = False
        y[~free] = 0.0
        return self._make(fixed, free, y, ref.t, max(ref.R - m, 0.0), warm_basis=ref.warm_basis, sensitivity=ref.sensitivity)

    def basis_intervals(self, ref, slot_cells, slot_n):
        """Sufficient simplex primal certificates; empty means undecided, never infeasible."""
        from stowage.feasibility._certificates import intervals
        return intervals(self, ref, slot_cells, slot_n)

    def basis_shift(self, ref, cells, m):
        """Reference from a mass inside ``basis_intervals`` (caller checked interval)."""
        from stowage.feasibility._certificates import shift
        return shift(self, ref, cells, m)

    @staticmethod
    def _ymax_excluding(ref: Reference, slot_cells: np.ndarray) -> np.ndarray:
        y = ref.y
        if len(y) == 0:
            return np.zeros(len(slot_cells))
        order = np.argsort(-y, kind="stable")[:3]
        top = [(int(i), float(y[i])) for i in order]
        out = np.zeros(len(slot_cells))
        for i, cells in enumerate(slot_cells):
            for ci, val in top:
                if ci not in cells:
                    out[i] = val
                    break
        return out

    def shift(self, ref: Reference, cells: list[int], m: float) -> Reference:
        """The shifted reference after a box of mass ``m`` goes into ``cells`` (caller checked the interval)."""
        y = ref.y.copy()
        ys = float(y[cells].sum())
        D = ref.R - ys
        y[cells] = 0.0
        lam = (ref.R - m) / D if D > 1e-9 else 0.0
        y *= lam
        fixed = ref.fixed.copy()
        fixed[cells] += m / len(cells)
        free = ref.free.copy()
        free[cells] = False
        return self._make(fixed, free, y, ref.t, max(ref.R - m, 0.0), warm_basis=ref.warm_basis, sensitivity=ref.sensitivity)

    def release(self, ref: Reference, cells_masses: list[tuple[list[int], float]]) -> Reference:
        """Boxes leaving the ship (restowed off): their mass moves from fixed into the reference allocation of
        the same cells, so every row value is unchanged and the reference stays feasible."""
        if not cells_masses:
            return ref
        fixed, free, y = ref.fixed.copy(), ref.free.copy(), ref.y.copy()
        R = ref.R
        for cells, m in cells_masses:
            fixed[cells] -= m / len(cells)
            y[cells] += m / len(cells)
            free[cells] = True
            R += m
        return Reference(fixed, free, y, ref.t, R, ref.v, self.A @ y, ref.centered, ref.warm_basis, ref.sensitivity)

    def combine(self, ref: Reference, cells: list[int], m: float, lo_sol, hi_sol) -> Reference:
        """Reference after a push resolved by the slot LP: convex combination of its two endpoint solutions."""
        (m0, y0, t0), (m1, y1, t1) = lo_sol, hi_sol
        th = 0.0 if abs(m1 - m0) < 1e-12 else min(1.0, max(0.0, (m - m0) / (m1 - m0)))
        y = (1 - th) * y0 + th * y1
        t = (1 - th) * t0 + th * t1
        fixed = ref.fixed.copy()
        fixed[cells] += m / len(cells)
        free = ref.free.copy()
        free[cells] = False
        y[cells] = 0.0
        return self._make(fixed, free, y, t, max(ref.R - m, 0.0), warm_basis=ref.warm_basis, sensitivity=ref.sensitivity)

    # ------------------------------------------------------------------ LPs
    def _lp(self, c, A_ub, b_ub, A_eq, b_eq, bounds):
        import time

        t0 = time.perf_counter()
        self.lp_calls += 1
        scale = np.maximum(1.0, np.abs(A_ub).max(axis=1)) if len(A_ub) else np.ones(0)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=OptimizeWarning)
            res = linprog(c, A_ub=A_ub / scale[:, None] if len(A_ub) else None,
                          b_ub=b_ub / scale if len(A_ub) else None, A_eq=A_eq, b_eq=b_eq, bounds=bounds,
                          method="highs", options={"threads": 1, "random_seed": 0,
                                                   "primal_feasibility_tolerance": 1e-9})
        self.lp_time_s += time.perf_counter() - t0
        # scipy.optimize.linprog: status 2 means proven infeasible; 1 is
        # iteration/time limit, 3 unbounded, 4 numerical difficulty. All
        # other/missing statuses are inconclusive for a necessary screen.
        # D23/D49: absence of a solver witness is not proof of infeasibility.
        if not res.success and getattr(res, "status", None) != 2:
            self.lp_indeterminate += 1
            raise LPIndeterminate(f"C2 LP undecided (status={getattr(res, 'status', None)!r})")
        return res

    def _rows(self, base, cols_A, extra_cols=None):
        """Inequality matrix for lo ≤ base + cols·x ≤ hi over finite bounds."""
        up, dn = np.isfinite(self.hi), np.isfinite(self.lo)
        M = cols_A if extra_cols is None else np.hstack([cols_A, extra_cols])
        A_ub = np.vstack([M[up], -M[dn]])
        b_ub = np.concatenate([self.hi[up] - base[up], base[dn] - self.lo[dn]])
        return A_ub, b_ub, up, dn

    def _workspace(self):
        if self._persistent is None and not self._persistent_unavailable:
            from stowage.feasibility._lp_highs import PersistentLP
            try:
                self._persistent = PersistentLP(self)
            except ImportError:
                self._persistent_unavailable = True
        return self._persistent

    def _timed_native(self, operation, calls, *args, **kwargs):
        import time
        t0 = time.perf_counter()
        previous = self._persistent.solve_count
        try:
            return operation(*args, **kwargs)
        finally:
            self.lp_calls += self._persistent.solve_count - previous
            self.lp_time_s += time.perf_counter() - t0

    def _center(self, fixed, free, R, warm_ref=None):
        workspace = self._workspace()
        if workspace is not None:
            sol = self._timed_native(workspace.center, 1, fixed, free, R, warm_ref=warm_ref)
            if sol is not None:
                return sol
            # A failed warm-started simplex can report infeasibility because
            # of numerical state. Only the independent reduced-cell LP may
            # confirm a rejection. Its witness has no native simplex basis.
            sol = self._center_cold(fixed, free, R)
            if sol is not None:
                self._persistent = None
            return sol
        return self._center_cold(fixed, free, R)

    def _center_cold(self, fixed, free, R):
        idx = np.flatnonzero(free)
        base = self.A @ fixed + self.const
        cols = np.hstack([self.A[:, idx], self.At])
        nT = self.At.shape[1]
        A_ub, b_ub, up, dn = self._rows(base, cols)
        # Exact equalities (notably cargo_mass) cannot gain slack. Including
        # them in this objective forces tau=0 and leaves an arbitrary corner
        # reference, causing unnecessary candidate LPs. Keep both inequality
        # sides intact; exclude only their slack reward, never their bounds.
        slack_weight = np.where(self.lo == self.hi, 0.0, self.w)
        tau = np.concatenate([slack_weight[up], slack_weight[dn]])[:, None]
        A_ub = np.hstack([A_ub, tau])
        nvar = len(idx) + nT + 1
        A_eq = np.zeros((1, nvar))
        A_eq[0, :len(idx)] = 1.0
        bounds = [(0.0, self.u0)] * len(idx) + list(zip(self.tank_lo, self.tank_hi)) + [(0.0, 1.0)]
        c = np.zeros(nvar)
        c[-1] = -1.0
        res = self._lp(c, A_ub, b_ub, A_eq, [R], bounds)
        if not res.success:
            return None
        y = np.zeros(len(fixed))
        y[idx] = np.clip(res.x[:len(idx)], 0.0, self.u0)
        return y, res.x[len(idx):len(idx) + nT]

    def slot_interval(self, ref: Reference, cells: list[int], m_lo: float, m_hi: float):
        """Endpoint witnesses, UNKNOWN_INTERVAL if undecided, or None only if proven infeasible."""
        try:
            return self._slot_interval(ref, cells, m_lo, m_hi)
        except LPIndeterminate:
            if self._persistent is not None:
                self._persistent.reset_solution()
            return UNKNOWN_INTERVAL

    def _slot_interval(self, ref, cells, m_lo, m_hi):
        if self.optimized:
            # A witness at each bound is enough: all intermediate allocations
            # are their convex combinations, and the query bounds themselves
            # prove the two endpoints optimal. No cached verdict is reused.
            witnessed = self._witness_interval(ref, cells, m_lo, m_hi)
            if witnessed is not None:
                self.witness_hits += 1
                return witnessed
        workspace = self._workspace()
        if workspace is not None:
            sol = self._timed_native(workspace.interval, 2, ref, cells, m_lo, m_hi)
            if sol is not None:
                if self.optimized:
                    self._remember_endpoints(ref, cells, sol)
                return sol
            # Real production child states have returned native kInfeasible
            # while this mathematically identical cold formulation succeeds.
            # Never cache that numerical failure as a necessary rejection.
            sol = self._slot_interval_cold(ref, cells, m_lo, m_hi)
            if sol is not None:
                workspace.reset_solution()
                if self.optimized:
                    self._remember_endpoints(ref, cells, sol)
            return sol
        sol = self._slot_interval_cold(ref, cells, m_lo, m_hi)
        if self.optimized and sol is not None:
            self._remember_endpoints(ref, cells, sol)
        return sol

    def _prepare_endpoint_state(self, ref):
        old = self._endpoint_state
        if old is None or old[0] is not ref.fixed or old[1] is not ref.free or old[2] != ref.R:
            self._endpoint_state = (ref.fixed, ref.free, ref.R)
            self._endpoint_witnesses = []

    def _remember_endpoints(self, ref, cells, sol):
        self._prepare_endpoint_state(ref)
        for mass, y_other, t in sol:
            y = y_other.copy()
            y[cells] = mass / len(cells)
            self._endpoint_witnesses.append((y, t))
        # These are candidate primal allocations only. Every reuse checks the
        # complete query constraints again, including cells, tanks and rows.
        self._endpoint_witnesses = self._endpoint_witnesses[-8:]

    def _witness_interval(self, ref, cells, m_lo, m_hi):
        self._prepare_endpoint_state(ref)
        key = tuple(cells)
        if key not in self._endpoint_donors:
            if self._normalized_A is None:
                self._normalized_A = self.A * (self.u0 / self.w)[:, None]
            normalized = self._normalized_A
            target = normalized[:, cells].mean(axis=1)
            distance = np.sum(normalized * normalized, axis=0) - 2. * (target @ normalized)
            self._endpoint_donors[key] = np.argsort(distance, kind="stable")
        order = self._endpoint_donors[key]
        order = order[ref.free[order] & ~np.isin(order, cells)]
        sources = [(ref.y, ref.t), *reversed(self._endpoint_witnesses)]
        out = []
        base = self.A @ ref.fixed + self.const
        for mass in (m_lo, m_hi):
            found = None
            for source, tanks in sources:
                if found is not None:
                    break
                delta = mass - float(source[cells].sum())
                for width in (4, 16, 64, len(order)):
                    donors = order[:width]
                    weights = source[donors] if delta >= 0. else self.u0 - source[donors]
                    available = float(weights.sum())
                    if available < abs(delta) or (available == 0. and delta != 0.):
                        continue
                    y = source.copy()
                    y[cells] = mass / len(cells)
                    if available:
                        y[donors] -= delta * weights / available
                    # Remove roundoff in the mass-preserving redistribution;
                    # this changes an allocation, never a feasibility limit.
                    self._repair_mass(y, donors, ref.R)
                    # Reuse is deliberately stricter than the legacy row
                    # screen: an endpoint must obey the original LP bounds.
                    if not self._valid_primal(ref.fixed, ref.free, y, tanks, ref.R, base=base):
                        continue
                    y[cells] = 0.
                    found = (float(mass), y, tanks)
                    break
            if found is None:
                return None
            out.append(found)
        return out[0], out[1]

    def _slot_interval_cold(self, ref, cells, m_lo, m_hi):
        free = ref.free.copy()
        free[cells] = False
        idx = np.flatnonzero(free)
        base = self.A @ ref.fixed + self.const
        a_s = self.A[:, cells].mean(axis=1)[:, None]
        cols = np.hstack([self.A[:, idx], self.At, a_s])
        A_ub, b_ub, _, _ = self._rows(base, cols)
        nT = self.At.shape[1]
        nvar = len(idx) + nT + 1
        A_eq = np.zeros((1, nvar))
        A_eq[0, :len(idx)] = 1.0
        A_eq[0, -1] = 1.0
        bounds = [(0.0, self.u0)] * len(idx) + list(zip(self.tank_lo, self.tank_hi)) + [(m_lo, m_hi)]
        out = []
        for sign in (1.0, -1.0):
            c = np.zeros(nvar)
            c[-1] = sign
            res = self._lp(c, A_ub, b_ub, A_eq, [ref.R], bounds)
            if not res.success:
                return None
            y = np.zeros(len(ref.fixed))
            y[idx] = np.clip(res.x[:len(idx)], 0.0, self.u0)
            out.append((float(res.x[-1]), y, res.x[len(idx):len(idx) + nT]))
        return out[0], out[1]


# Legacy v1 LP screen, re-exported for the 4.8 two-step adapter only (see ``_legacy``).
from stowage.feasibility._legacy import GlobalBounds  # noqa: E402,F401
