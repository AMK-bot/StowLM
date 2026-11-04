"""C1 — capacity screens for the feasibility engine (plan 4.4 v2): can the boxes still to load be matched to the
free cells at all? Two necessary conditions, each checked once per state and screened per candidate in O(1)
(vectorised):

**C1a, class matching (Hall's theorem).** Group boxes by the set of cells their static rules allow, and cells by
the set of box groups that may use them. A completion needs a flow from box groups (demand = cells needed: 1 for a
20', 2 for a 40'/45') to cell groups (capacity = usable free cells) that meets every demand. Hall (1935) "On
representatives of subsets": such an assignment exists iff every set of demands fits in the cells its members may
use; max-flow checks all sets at once. Relaxed (so a pass proves nothing): the two halves of a long box may land in
unrelated cells. A candidate passes in O(1) if each cell it takes was spare or carried this box group's own flow;
otherwise its (box group, cell groups) combination gets one max-flow of its own (cached for the state).

**C1b, weight order (nested Hall).** E1/E2 cap what can still go into a stack: nothing heavier than the lightest
box already in it plus the E2 tolerance, nothing heavy above a non-heavy box. A free cell's cap is its stack's cap,
and stack height/mass room limit how many of a stack's free cells are usable at all (counted with the shortest /
lightest box still to come). Because "box fits cell" is just ``mass ≤ cap``, Hall's condition reduces to: for every
mass level ``M``, cells with cap ≥ M ≥ demand of boxes with mass ≥ M (a sorted sweep). Checked twice: all boxes vs
all cells, and the E6-heavy boxes vs hold cells. A candidate changes a few counts (its own cells, its stacks' new
cap); the post-state test is evaluated exactly per candidate with range-minimum queries over the level sweep.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_flow


# ---------------------------------------------------------------------------------------------- C1a
class ClassFlow:
    """Static grouping (shared by engine copies)."""

    def __init__(self, cellcompat: np.ndarray):
        # cellcompat: (B, C) bool — box may use cell (some statically allowed slot contains it)
        rows, self.box_class = np.unique(cellcompat, axis=0, return_inverse=True)
        self.rows = rows                         # (nb, C): cells each box class may use
        self.box_class = self.box_class.ravel()
        cols, self.cell_class = np.unique(rows.T, axis=0, return_inverse=True)  # cells grouped by column pattern
        self.cell_class = self.cell_class.ravel()
        # compat[bc, cc]
        self.compat = cols.T.astype(bool)
        self.nb, self.nc = self.compat.shape

    def flow(self, demand: np.ndarray, capacity: np.ndarray):
        """Max flow; returns (feasible, flow matrix (nb, nc))."""
        nb, nc = self.nb, self.nc
        # With only one family on either side, matching reduces exactly to filling its compatible
        # capacities. Avoid constructing a sparse graph for these common unrestricted cargo states.
        if nb == 1 or nc == 1 or not demand.any():
            f = np.zeros((nb, nc), dtype=np.int64)
            if nb == 1:
                room = np.where(self.compat[0], capacity, 0)
                before = np.cumsum(room) - room
                f[0] = np.minimum(room, np.maximum(int(demand[0]) - before, 0))
            elif nc == 1:
                need = np.where(self.compat[:, 0], demand, 0)
                before = np.cumsum(need) - need
                f[:, 0] = np.minimum(need, np.maximum(int(capacity[0]) - before, 0))
            return bool(np.array_equal(f.sum(axis=1), demand)), f
        n = nb + nc + 2
        src, snk = nb + nc, nb + nc + 1
        bi, ci = np.nonzero(self.compat & (demand[:, None] > 0) & (capacity[None, :] > 0))
        rows = np.concatenate([np.full(nb, src), bi, nb + np.arange(nc)])
        cols = np.concatenate([np.arange(nb), nb + ci, np.full(nc, snk)])
        big = int(demand.sum()) + 1
        caps = np.concatenate([demand, np.full(len(bi), big), capacity]).astype(np.int32)
        g = csr_matrix((caps, (rows, cols)), shape=(n, n))
        res = maximum_flow(g, src, snk)
        f = np.zeros((nb, nc), dtype=np.int64)
        if len(bi):
            fl = res.flow.tocsr()
            f[bi, ci] = np.asarray(fl[bi, nb + ci]).ravel()
        return res.flow_value >= demand.sum(), f

    def repair(self, f: np.ndarray, demand: np.ndarray, capacity: np.ndarray):
        """Exact warm start after demand/capacity changes; never mutate the parent's flow.

        Remove row/column overflow, then restore demand along residual augmenting paths. The
        integral capacities mean exhaustion of such paths is exactly the cold max-flow test.
        Unlike a new sparse network, a one-placement update usually needs no augmentation.
        """
        if self.nb == 1 or self.nc == 1:
            return self.flow(demand, capacity)
        F = f.copy()
        for b in np.flatnonzero(F.sum(axis=1) > demand):
            left = int(F[b].sum() - demand[b])
            for c in np.flatnonzero(F[b]):
                take = min(left, int(F[b, c]))
                F[b, c] -= take
                left -= take
                if not left:
                    break
        for c in np.flatnonzero(F.sum(axis=0) > capacity):
            left = int(F[:, c].sum() - capacity[c])
            for b in np.flatnonzero(F[:, c]):
                take = min(left, int(F[b, c]))
                F[b, c] -= take
                left -= take
                if not left:
                    break
        deficit = demand - F.sum(axis=1)
        spare = capacity - F.sum(axis=0)
        for b in np.flatnonzero(deficit):
            while deficit[b] > 0:
                prev_box, prev_cell = {int(b): None}, {}
                frontier, end = [int(b)], None
                while frontier and end is None:
                    nxt = []
                    for x in frontier:
                        for c in np.flatnonzero(self.compat[x] & (capacity > 0)):
                            c = int(c)
                            if c in prev_cell:
                                continue
                            prev_cell[c] = x
                            if spare[c] > 0:
                                end = c
                                break
                            for y in np.flatnonzero(F[:, c]):
                                y = int(y)
                                if y not in prev_box:
                                    prev_box[y] = c
                                    nxt.append(y)
                        if end is not None:
                            break
                    frontier = nxt
                if end is None:
                    return False, F
                amount = min(int(deficit[b]), int(spare[end]))
                c = end
                while prev_box[prev_cell[c]] is not None:
                    x = prev_cell[c]
                    back = prev_box[x]
                    amount = min(amount, int(F[x, back]))
                    c = back
                c = end
                while True:
                    x = prev_cell[c]
                    F[x, c] += amount
                    back = prev_box[x]
                    if back is None:
                        break
                    F[x, back] -= amount
                    c = back
                deficit[b] -= amount
                spare[end] -= amount
        return True, F

    def post_feasible(self, f: np.ndarray, demand: np.ndarray, capacity: np.ndarray, bc: int, units: int,
                      lost) -> bool:
        """Exact feasibility after box class ``bc`` needs ``units`` fewer cells and one cell of each class in
        ``lost`` disappears, warm-started from the current feasible flow ``f`` (Ford–Fulkerson): drop ``bc``'s
        surplus units (from overfull classes first), push the remaining overflow back to the box classes using
        those cells, then route each deficit unit along an augmenting path in the residual graph (box→cell where
        compatible, cell→box where that box class sends flow there) to a cell class with spare room. No path for a
        deficit ⇒ the max flow is short ⇒ infeasible."""
        F = f.copy()
        cap = capacity.copy()
        for cc in lost:
            cap[cc] -= 1
        if (cap < 0).any():
            return False
        inflow = F.sum(axis=0)
        left = units
        over = inflow - cap
        for cc in np.flatnonzero(over > 0).tolist():          # surplus of bc taken off overfull classes first
            t = min(int(over[cc]), int(F[bc, cc]), left)
            F[bc, cc] -= t
            left -= t
        for cc in np.flatnonzero(F[bc] > 0).tolist():
            if left <= 0:
                break
            t = min(int(F[bc, cc]), left)
            F[bc, cc] -= t
            left -= t
        inflow = F.sum(axis=0)
        deficit = np.zeros(self.nb, dtype=np.int64)
        for cc in np.flatnonzero(inflow > cap).tolist():
            excess = int(inflow[cc] - cap[cc])
            for x in np.flatnonzero(F[:, cc] > 0).tolist():
                if excess <= 0:
                    break
                t = min(int(F[x, cc]), excess)
                F[x, cc] -= t
                deficit[x] += t
                excess -= t
        inc = self.compat & (cap[None, :] > 0)
        for x in np.flatnonzero(deficit > 0).tolist():
            for _ in range(int(deficit[x])):
                spare = cap - F.sum(axis=0)
                # BFS over box classes; parent pointers through cell classes
                prev_box, prev_cell = {x: None}, {}
                frontier, end = [x], None
                while frontier and end is None:
                    nxt = []
                    for y in frontier:
                        for cc in np.flatnonzero(inc[y]).tolist():
                            if cc in prev_cell:
                                continue
                            prev_cell[cc] = y
                            if spare[cc] > 0:
                                end = cc
                                break
                            for z in np.flatnonzero(F[:, cc] > 0).tolist():
                                if z not in prev_box:
                                    prev_box[z] = cc
                                    nxt.append(z)
                        if end is not None:
                            break
                    frontier = nxt
                if end is None:
                    return False
                cc = end
                while True:                                     # augment one unit back along the path
                    y = prev_cell[cc]
                    F[y, cc] += 1
                    back = prev_box[y]
                    if back is None:
                        break
                    F[y, back] -= 1
                    cc = back
        return True

    def single_removal(self, f: np.ndarray, demand: np.ndarray, capacity: np.ndarray) -> np.ndarray:
        """``ok[bc, cc]``: after one unit less demand for box class ``bc`` and one cell less in class ``cc``, does
        a feasible flow still exist? Exact, from the current feasible flow ``f``: the post-change network is
        feasible iff the residual graph has a path from ``cc`` to ``bc`` or to a cell class with spare capacity
        (cancel one unit along it). Residual edges: cell→box where that box class sends flow to the cell class
        (reduce it), box→cell where compatible (increase it). One transitive closure per state."""
        nb, nc = self.nb, self.nc
        spare = capacity - f.sum(axis=0)
        F = f > 0
        inc = self.compat & (capacity[None, :] > 0)                 # box→cell increase edges
        # box X → box Y in two residual steps: X increases into cell c, Y reduces its flow on c
        adj = (inc.astype(np.int32) @ F.T.astype(np.int32)) > 0
        reach = adj | np.eye(nb, dtype=bool)
        for _ in range(max(1, int(np.ceil(np.log2(max(nb, 2)))))):
            nxt = (reach.astype(np.int32) @ reach.astype(np.int32)) > 0
            if np.array_equal(nxt, reach):
                break
            reach = nxt
        good = (inc & (spare[None, :] > 0)).any(axis=1)               # box class can send to a spare cell class
        rg = (reach.astype(np.int32) @ good.astype(np.int32)) > 0     # reaches a "good" box class
        # path from cc: first step to some X with F[X, cc]; then X reaches good or reaches bc
        target = rg[:, None] | reach                                  # (X, bc)
        via = (F.T.astype(np.int32) @ target.astype(np.int32)) > 0    # (cc, bc)
        return (spare[None, :] > 0) | F | via.T


# ---------------------------------------------------------------------------------------------- C1b
class NestedHall:
    """Per-state data for one (cells, boxes) family. ``levels`` are remaining box masses (ascending, unique)."""

    def __init__(self, masses: np.ndarray, units: np.ndarray, caps: np.ndarray, counts: np.ndarray):
        # masses/units: remaining boxes of the family; caps/counts: per stack cap and usable free cell count
        self.levels = np.unique(masses)
        L = len(self.levels)
        order = np.argsort(masses)
        cum = np.cumsum(units[order][::-1])[::-1]           # demand with mass >= sorted mass
        pos = np.searchsorted(masses[order], self.levels, side="left")
        self.dem = cum[pos] if L else np.zeros(0)
        corder = np.argsort(caps)
        ccum = np.cumsum(counts[corder][::-1])[::-1]
        cpos = np.searchsorted(caps[corder], self.levels, side="left")
        self.cells = np.where(cpos < len(caps), ccum[np.minimum(cpos, len(caps) - 1)] if len(caps) else 0, 0)
        self.surplus = (self.cells - self.dem).astype(np.int64)
        self.ok = bool(L == 0 or self.surplus.min() >= 0)
        self.min_surplus = int(self.surplus.min()) if L else 1 << 30
        # sparse table for range minimum over level indices
        self._table = [self.surplus]
        k = 1
        while (1 << k) <= L:
            prev = self._table[-1]
            self._table.append(np.minimum(prev[:-(1 << (k - 1))], prev[(1 << (k - 1)):]))
            k += 1

    def range_min(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """min surplus over level indices [a, b) (vectorised); +big for empty ranges."""
        big = np.int64(1 << 40)
        out = np.full(len(a), big)
        ne = b > a
        if not ne.any():
            return out
        aa, bb = a[ne], b[ne]
        length = bb - aa
        k = np.floor(np.log2(length)).astype(int)
        res = np.empty(len(aa), dtype=np.int64)
        for kk in np.unique(k):
            sel = k == kk
            t = self._table[kk]
            res[sel] = np.minimum(t[aa[sel]], t[bb[sel] - (1 << kk)])
        out[ne] = res
        return out

    def screen(self, m: np.ndarray, u: np.ndarray, stk_caps: list[np.ndarray], stk_new_caps: list[np.ndarray],
               stk_counts: list[np.ndarray], stk_new_counts: list[np.ndarray], in_family: np.ndarray) -> np.ndarray:
        """Post-state feasibility per candidate. For each affected stack i (one or two per candidate):
        cap C → C', usable cells n → n'. Cells lost at level M: (n − n')·[M ≤ C] + n'·[C' < M ≤ C].
        Demand removed: u·[M ≤ m] if the box belongs to this family."""
        N = len(m)
        if len(self.levels) == 0:
            return np.ones(N, dtype=bool)
        # shortcut: the most a candidate can take away is its stacks' usable cells
        worst = sum(stk_counts)
        ok = np.ones(N, dtype=bool)
        hard = np.flatnonzero(worst > self.min_surplus)
        if len(hard) == 0:
            return ok
        if len(hard) < N:
            sub = self.screen(m[hard], u[hard], [c[hard] for c in stk_caps], [c[hard] for c in stk_new_caps],
                              [c[hard] for c in stk_counts], [c[hard] for c in stk_new_counts], in_family[hard])
            ok[hard] = sub
            return ok
        lev = self.levels
        # breakpoints in level-index space: idx(x) = number of levels <= x
        pts = [np.searchsorted(lev, m, side="right")]
        for C, Cn in zip(stk_caps, stk_new_caps):
            pts.append(np.searchsorted(lev, C, side="right"))
            pts.append(np.searchsorted(lev, Cn, side="right"))
        P = np.stack(pts + [np.zeros(N, dtype=np.int64), np.full(N, len(lev))], axis=1)
        P = np.sort(P, axis=1)
        ok = np.ones(N, dtype=bool)
        for q in range(P.shape[1] - 1):
            a, b = P[:, q], P[:, q + 1]
            ne = b > a
            if not ne.any():
                continue
            mid_level = lev[np.minimum(a, len(lev) - 1)]          # any level in [a, b) has the same delta
            delta = np.where(in_family & (mid_level <= m), u, 0).astype(np.int64)
            for C, Cn, n, nn in zip(stk_caps, stk_new_caps, stk_counts, stk_new_counts):
                delta -= np.where(mid_level <= C, n - nn, 0)
                delta -= np.where((Cn < mid_level) & (mid_level <= C), nn, 0)
            rm = self.range_min(a, b)
            ok &= ~ne | (rm + delta >= 0)
        return ok


# Legacy v1 bound, re-exported for the 4.8 two-step adapter only (see ``_legacy``).
from stowage.feasibility._legacy import CompletionCapacity, completion_violations  # noqa: E402,F401
