"""A fixed-matrix C2 simplex workspace, using SciPy's bundled HiGHS binding.

The matrix stays identical across a search tree: occupied cells have fixed bounds,
free cells have capacity bounds. A slot query changes those bounds, a two-cell
balance row and its objective. HiGHS then repairs the existing simplex basis instead
of repeating Python matrix assembly and cold presolve. This is the same LP as the
cold reduced-cell formulation, with occupied cell variables retained and fixed.

SciPy's binding is private, so import failure preserves the public linprog fallback.
No native ABI assumptions or extra dependency are used. The binding is also the
one used by scipy.optimize.linprog itself (``_highs_wrapper.py``).
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import csc_matrix

try:
    from scipy.optimize._highspy import _core as _h
except ImportError:  # pragma: no cover - supported older SciPy fallback
    _h = None


class PersistentLP:
    def __init__(self, screen):
        if _h is None:
            raise ImportError("SciPy does not expose its bundled HiGHS model binding")
        self.g = g = screen
        self.C, self.T = g.A.shape[1], g.At.shape[1]
        self.N = self.C + self.T + 1
        up, dn = np.isfinite(g.hi), np.isfinite(g.lo)
        cols = np.hstack([g.A, g.At])
        reward = np.where(g.lo == g.hi, 0.0, g.w)
        rows = np.vstack([cols[up], -cols[dn]])
        tau = np.concatenate([reward[up], reward[dn]])
        rows = np.column_stack([rows, tau])
        rhs = np.concatenate([g.hi[up] - g.const[up], g.const[dn] - g.lo[dn]])
        scale = np.maximum(1., np.abs(rows).max(axis=1))
        rows /= scale[:, None]
        rhs /= scale
        self.mass_row = len(rows)
        self.pair_row = self.mass_row + 1
        matrix = np.vstack([rows, np.r_[np.ones(self.C), np.zeros(self.T + 1)], np.zeros(self.N)])
        matrix = csc_matrix(matrix)
        lp = _h.HighsLp()
        lp.num_col_, lp.num_row_ = self.N, len(rows) + 2
        lp.col_cost_ = np.zeros(self.N)
        lp.col_lower_ = np.r_[np.zeros(self.C), g.tank_lo, 0.]
        lp.col_upper_ = np.r_[np.full(self.C, g.u0), g.tank_hi, 0.]
        lp.row_lower_ = np.r_[np.full(len(rows), -np.inf), 0., 0.]
        lp.row_upper_ = np.r_[rhs, 0., 0.]
        lp.a_matrix_.format_ = _h.MatrixFormat.kColwise
        lp.a_matrix_.num_col_, lp.a_matrix_.num_row_ = lp.num_col_, lp.num_row_
        lp.a_matrix_.start_ = matrix.indptr
        lp.a_matrix_.index_ = matrix.indices
        lp.a_matrix_.value_ = matrix.data
        self.h = h = _h._Highs()
        for key, value in (("output_flag", False), ("threads", 1), ("random_seed", 0),
                           ("solver", "simplex"), ("primal_feasibility_tolerance", 1e-9)):
            h.setOptionValue(key, value)
        h.passModel(lp)
        self.model_builds = 1
        self.solve_count = 0
        self.basis_starts = 0
        self._state = None
        self._pair = ()
        self._objective = ()
        self._last_cells = ()
        self._lower = self._upper = None
        self._indices = np.arange(self.C, dtype=np.int32)

    def _clear_query(self):
        for col in self._pair:
            self.h.changeCoeff(self.pair_row, int(col), 0.)
        self._pair = ()
        self.h.changeRowBounds(self.pair_row, 0., 0.)
        for col in self._objective:
            self.h.changeColCost(int(col), 0.)
        self._objective = ()
        if self._last_cells:
            idx = np.asarray(self._last_cells, dtype=np.int32)
            self.h.changeColsBounds(len(idx), idx, self._lower[idx], self._upper[idx])
        self._last_cells = ()

    def activate(self, fixed, free, R, *, basis=None, solution=None):
        # Identity is enough: Reference's arrays are immutable and replaced on push.
        state = (id(fixed), id(free), float(R))
        self._clear_query()
        if state == self._state:
            return
        self._state = state
        self._lower = np.asarray(fixed, dtype=float)
        self._upper = fixed + np.where(free, self.g.u0, 0.)
        self.h.changeColsBounds(self.C, self._indices, self._lower, self._upper)
        total = float(np.sum(fixed) + R)
        self.h.changeRowBounds(self.mass_row, total, total)
        if basis is not None:
            self.h.setBasis(basis)
            self.basis_starts += 1
        elif solution is not None:
            x = np.r_[fixed + solution.y, solution.t, 0.]
            self.h.setSolution(self.N, np.arange(self.N, dtype=np.int32), x)

    def _run(self):
        self.solve_count += 1
        self.h.run()
        if self.h.getModelStatus() != _h.HighsModelStatus.kOptimal:
            return None
        return np.asarray(self.h.getSolution().col_value)

    def reset_solution(self):
        """Discard a numerically failed solve, retaining the immutable LP matrix."""
        self.h.clearSolver()
        self._state = None

    def center(self, fixed, free, R, warm_ref=None):
        self.activate(fixed, free, R, basis=getattr(warm_ref, "warm_basis", None), solution=warm_ref)
        self.h.changeColBounds(self.N - 1, 0., 1.)
        self.h.changeColCost(self.N - 1, -1.)
        self._objective = (self.N - 1,)
        x = self._run()
        if x is None:
            return None
        y = np.where(free, np.clip(x[:self.C] - fixed, 0., self.g.u0), 0.)
        return y, x[self.C:self.C + self.T]

    def interval(self, ref, cells, m_lo, m_hi):
        self.activate(ref.fixed, ref.free, ref.R, basis=ref.warm_basis, solution=ref)
        self.h.changeColBounds(self.N - 1, 0., 0.)
        n = len(cells)
        idx = np.asarray(cells, dtype=np.int32)
        offset = ref.fixed[idx]
        self.h.changeColsBounds(n, idx, offset + m_lo / n, offset + m_hi / n)
        self._last_cells = tuple(cells)
        if n == 2:
            self.h.changeCoeff(self.pair_row, int(cells[0]), 1.)
            self.h.changeCoeff(self.pair_row, int(cells[1]), -1.)
            d = float(offset[0] - offset[1])
            self.h.changeRowBounds(self.pair_row, d, d)
            self._pair = tuple(cells)
        out = []
        for sign in (1., -1.):
            self.h.changeColsCost(n, idx, np.full(n, sign))
            self._objective = tuple(cells)
            x = self._run()
            if x is None:
                return None
            mass = float(np.sum(x[idx] - offset))
            y = np.where(ref.free, np.clip(x[:self.C] - ref.fixed, 0., self.g.u0), 0.)
            y[idx] = 0.
            out.append((mass, y, x[self.C:self.C + self.T]))
        return out[0], out[1]

    def basis(self):
        basis = self.h.getBasis()
        return basis if basis.valid else None
