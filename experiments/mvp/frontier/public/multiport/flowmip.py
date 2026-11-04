"""Compact integer model for the k-shift problem: a flow over the states of a stack DP.

Why this works. Between two lifts a box sits still in one slot; call that a piece, running port i
to port j. With no overstowage, what sits on a box must leave no later than it and arrive no
earlier, so pieces nest. A rehandle does not break this: the box's piece ends and a new one starts.
So one stack's life is a rectangle (legs x tiers) cut guillotine-fashion, and the best stack for
given piece values comes from a DP over rectangles [i,j) x k tiers.

Two rectangles of the same type are interchangeable: what sits inside one has no effect on anything
outside it. So instead of choosing whole stacks we can count how many rectangles of each type use
each DP move. That count is a flow on the DP's state graph, integer flows split back into stacks,
and the model size depends on N and R only, not on the number of stacks. This is the standard move
of turning a DP into an extended formulation (Martin, Rardin & Campbell 1990), and it is the same
idea as arc-flow models for cutting stock (Valerio de Carvalho 1999). The MIP solver here is HiGHS
through SciPy; no Gurobi or CPLEX is available.

States and moves, one column (40' only, TR1):
    rectangle (i, j, k): legs i..j, k tiers still free above the current floor.
    move u[i,j,k,c]: the first piece on this floor runs i to c, then
        (i, c, k-1) sits on that piece, and (c, j, k) is what is left of the floor, if c < j.
Choosing the first piece removes the symmetry of cutting a floor in different orders.

Box flow: g[i,j,d] counts boxes of final destination d stowed at port i on a piece ending at j, and
each with j < d is a rehandle. Objective: the sum of those, one per unload plus reload.
"""

from __future__ import annotations

import time
from collections import defaultdict

import numpy as np
from ortools.sat.python import cp_model
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp


class Lp:
    """Small sparse model builder."""

    def __init__(self):
        self.n = 0
        self.m = 0
        self.rows, self.cols, self.vals = [], [], []
        self.lo, self.hi = [], []
        self.c = []
        self.ub = []
        self.integ = []

    def var(self, cost=0.0, ub=np.inf, integer=True) -> int:
        self.c.append(cost)
        self.ub.append(ub)
        self.integ.append(1 if integer else 0)
        self.n += 1
        return self.n - 1

    def row(self, terms: list, lo: float, hi: float) -> int:
        for j, a in terms:
            self.rows.append(self.m)
            self.cols.append(j)
            self.vals.append(a)
        self.lo.append(lo)
        self.hi.append(hi)
        self.m += 1
        return self.m - 1

    def solve(self, time_limit: float, mip_gap: float = 0.0, lp_only: bool = False):
        A = sparse.csr_matrix((self.vals, (self.rows, self.cols)), shape=(self.m, self.n))
        cons = LinearConstraint(A, np.array(self.lo), np.array(self.hi))
        options = {"time_limit": time_limit, "mip_rel_gap": mip_gap, "disp": False}
        integ = np.zeros(self.n) if lp_only else np.array(self.integ)
        return milp(np.array(self.c), constraints=cons, integrality=integ, bounds=Bounds(np.zeros(self.n), np.array(self.ub)), options=options)


def build_1d(T: list, inject: dict, R: int, used: dict | None = None, dummy: dict | None = None, zero: bool = False):
    """Flow model for one column type. `inject` puts rectangles in at states, the root for TR1.

    `used` counts pieces already built elsewhere, `dummy` pieces that hold no box (empty cells).
    With zero=True pieces must match demand exactly and only pieces with demand get variables.
    Returns None when zero is impossible on sight.
    """
    N = len(T)
    used = used or {}
    dummy = dummy or {}
    demand = {(i, j): T[i][j] + dummy.get((i, j), 0) - used.get((i, j), 0) for i in range(N - 1) for j in range(i + 1, N)}
    if zero and any(v < 0 for v in demand.values()):
        return None
    lp = Lp()
    u = {}
    for k in range(1, R + 1):
        for i in range(N - 1):
            for j in range(i + 1, N):
                for c in range(i + 1, j + 1):
                    if zero and demand[(i, c)] == 0:
                        continue
                    u[(i, j, k, c)] = lp.var()
    inflow = defaultdict(list)
    outflow = defaultdict(list)
    pieces = defaultdict(list)
    for (i, j, k, c), v in u.items():
        outflow[(i, j, k)].append(v)
        pieces[(i, c)].append(v)
        if k > 1:
            inflow[(i, c, k - 1)].append(v)
        if c < j:
            inflow[(c, j, k)].append(v)
    for k in range(1, R + 1):
        for i in range(N - 1):
            for j in range(i + 1, N):
                key = (i, j, k)
                rhs = float(inject.get(key, 0))
                terms = [(v, 1.0) for v in outflow[key]] + [(v, -1.0) for v in inflow[key]]
                if terms:
                    lp.row(terms, rhs, rhs)
                elif rhs:
                    return None
    g = {}
    if zero:
        for (i, j), d in demand.items():
            if d and not pieces[(i, j)]:
                return None
            if pieces[(i, j)]:
                lp.row([(v, 1.0) for v in pieces[(i, j)]], d, d)
    else:
        for i in range(N - 1):
            for j in range(i + 1, N):
                for d in range(j, N):
                    g[(i, j, d)] = lp.var(cost=1.0 if j < d else 0.0)
        for i in range(N - 1):
            for j in range(i + 1, N):
                rhs = dummy.get((i, j), 0) - used.get((i, j), 0)
                terms = [(v, 1.0) for v in pieces[(i, j)]] + [(g[(i, j, d)], -1.0) for d in range(j, N)]
                lp.row(terms, rhs, rhs)
        for i in range(N - 1):
            for d in range(i + 1, N):
                terms = [(g[(i, j, d)], 1.0) for j in range(i + 1, d + 1)]
                terms += [(g[(h, i, d)], -1.0) for h in range(i)]
                lp.row(terms, T[i][d], T[i][d])
    return lp, u, g


def build_tr1(T: list, S: int, R: int, zero: bool = False) -> tuple:
    N = len(T)
    return build_1d(T, {(0, N - 1, R): S}, R, zero=zero)


def decompose_tr1(values: dict, S: int, R: int, N: int) -> list:
    """Split an integer flow into S stacks, each a list of pieces (i, j, tier)."""
    avail = defaultdict(list)
    for (i, j, k, c), v in values.items():
        if v > 0:
            avail[(i, j, k)].append([c, v])

    def take(i, j, k, out):
        # Depth-first over rectangles. Iterative so a 10-tier, 10-port stack cannot hit recursion limits.
        todo = [(i, j, k)]
        while todo:
            a, b, q = todo.pop()
            if q == 0:
                continue
            bucket = avail[(a, b, q)]
            c, v = bucket[-1]
            if v == 1:
                bucket.pop()
            else:
                bucket[-1][1] = v - 1
            out.append((a, c, R - q))
            todo.append((a, c, q - 1))
            if c < b:
                todo.append((c, b, q))

    stacks = []
    for _ in range(S):
        pieces = []
        take(0, N - 1, R, pieces)
        stacks.append(pieces)
    return stacks


def solve_tr1(T: list, S: int, R: int, limit: float) -> dict:
    t0 = time.perf_counter()
    N = len(T)
    lp, u, g = build_tr1(T, S, R, zero=True)
    res = lp.solve(min(limit * 0.5, 120.0))
    t_zero = time.perf_counter() - t0
    if res.x is not None and res.status in (0, 1):
        vals = {key: int(round(res.x[v])) for key, v in u.items()}
        gv = {(i, j, j): T[i][j] for i in range(N) for j in range(i + 1, N) if T[i][j]}
        return {"stacks": decompose_tr1(vals, S, R, N), "g": gv, "obj": 0, "bound": 0.0, "status": "zero", "t_zero": t_zero, "zero_status": int(res.status), "n_vars": lp.n, "n_rows": lp.m}
    zero_status = int(res.status)
    lp, u, g = build_tr1(T, S, R, zero=False)
    res = lp.solve(max(5.0, limit - (time.perf_counter() - t0)))
    if res.x is None:
        return {"stacks": None, "status": f"none:{res.status}", "t_zero": t_zero, "zero_status": zero_status, "bound": getattr(res, "mip_dual_bound", None)}
    vals = {key: int(round(res.x[v])) for key, v in u.items()}
    gv = {key: int(round(res.x[v])) for key, v in g.items() if res.x[v] > 0.5}
    return {
        "stacks": decompose_tr1(vals, S, R, N),
        "g": gv,
        "obj": int(round(res.fun)),
        "bound": float(res.mip_dual_bound) if res.mip_dual_bound is not None else None,
        "status": "optimal" if res.status == 0 else "limit",
        "t_zero": t_zero,
        "zero_status": zero_status,
        "n_vars": lp.n,
        "n_rows": lp.m,
    }


# ---------------------------------------------------------------------------------------------
# Two sizes (TR3, TR4)
#
# Rules: a cell holds one 40' or two 20'; no 20' on a 40'; nothing hangs; cells are full or empty.
# Empty cells are dummy 40' pieces one leg long, as in the paper's BS model; they sort to the top.
#
# Most of a TR4 ship can be pure: a 20'-only stack is two independent columns and a 40'-only stack
# is one, so each size alone is a TR1 problem, which HiGHS solves in seconds. Only the small change
# from leg to leg in the number of 20' cells needs mixed stacks. So the solve has two stages:
#   A. A few mixed stacks, by CP-SAT, that use real routes and leave each pure fleet exactly the
#      cells it needs on every leg.
#   B. One integer flow per size for the pure stacks, on what the mixed stacks left.
# Splitting loses optimality: a value here is an upper bound, and only a zero is a proof.
#
# VCG is not in the model. Sorting heavy boxes low inside each route is done afterwards; the checker
# tests it on every plan.
# ---------------------------------------------------------------------------------------------

S20_, S40_ = 0, 1


def mixed_stacks(T: dict, empty: list, fleets: tuple, R: int, limit_s: float, seed: int):
    """Stage A. One mixed stack is h[x][c] and b[l].

    h[x][c] is the lowest tier with a stop at port c in column x; every tier above it stops too, which
    is what nesting means. b[l] is how many tiers hold 20' on leg l. Rules:
      - if b changes at c, every tier from the lower b up has a stop at c in both columns;
      - above the 20' zone the columns stop together: h0 == h1, or both are inside the zone.
    Pieces follow: tiers on which i and j are consecutive stops, clipped by the zone.
    """
    N = len(T[S20_])
    k20, k40, mix = fleets
    demand = {
        (s, i, j): T[s][i][j] + (empty[i] if s == S40_ and j == i + 1 else 0)
        for s in (S20_, S40_)
        for i in range(N - 1)
        for j in range(i + 1, N)
    }
    cells = []
    for leg in range(N - 1):
        on20 = sum(T[S20_][i][j] for i in range(leg + 1) for j in range(leg + 1, N))
        cells.append(on20 // 2 - k20 * R)
        if cells[-1] < 0 or cells[-1] > mix * R:
            return None
    mod = cp_model.CpModel()
    zero = mod.NewConstant(0)
    capR = mod.NewConstant(R)
    H, B = [], []
    terms = defaultdict(list)

    def piece(key, lim, top):
        if demand[key] == 0:
            mod.Add(lim <= top)
            return
        diff = mod.NewIntVar(-R, R, "")
        mod.Add(diff == lim - top)
        a = mod.NewIntVar(0, R, "")
        mod.AddMaxEquality(a, [diff, zero])
        terms[key].append(a)

    for _ in range(mix):
        h = [[mod.NewIntVar(0, R, "") for _ in range(N)] for _ in range(2)]
        b = [mod.NewIntVar(0, R, "") for _ in range(N - 1)]
        H.append(h)
        B.append(b)
        for x in (0, 1):
            mod.Add(h[x][0] == 0)
            mod.Add(h[x][N - 1] == 0)
        for c in range(1, N - 1):
            same = mod.NewBoolVar("")
            mod.Add(b[c - 1] == b[c]).OnlyEnforceIf(same)
            mod.Add(b[c - 1] != b[c]).OnlyEnforceIf(same.Not())
            for x in (0, 1):
                mod.Add(h[x][c] <= b[c - 1]).OnlyEnforceIf(same.Not())
                mod.Add(h[x][c] <= b[c]).OnlyEnforceIf(same.Not())
            eq = mod.NewBoolVar("")
            mod.Add(h[0][c] == h[1][c]).OnlyEnforceIf(eq)
            mod.Add(h[0][c] <= b[c]).OnlyEnforceIf(eq.Not())
            mod.Add(h[1][c] <= b[c]).OnlyEnforceIf(eq.Not())
        for i in range(N - 1):
            mn = [capR, capR]
            bmin = b[i]
            bmax = b[i]
            for j in range(i + 1, N):
                if j - 1 > i:
                    for x in (0, 1):
                        nm = mod.NewIntVar(0, R, "")
                        mod.AddMinEquality(nm, [mn[x], h[x][j - 1]])
                        mn[x] = nm
                    v1 = mod.NewIntVar(0, R, "")
                    mod.AddMinEquality(v1, [bmin, b[j - 1]])
                    bmin = v1
                    v2 = mod.NewIntVar(0, R, "")
                    mod.AddMaxEquality(v2, [bmax, b[j - 1]])
                    bmax = v2
                tops = []
                for x in (0, 1):
                    top = mod.NewIntVar(0, R, "")
                    mod.AddMaxEquality(top, [h[x][i], h[x][j]])
                    tops.append(top)
                    lim = mod.NewIntVar(0, R, "")
                    mod.AddMinEquality(lim, [mn[x], bmin])
                    piece((S20_, i, j), lim, top)
                low = mod.NewIntVar(0, R, "")
                mod.AddMinEquality(low, [mn[0], mn[1]])
                top = mod.NewIntVar(0, R, "")
                mod.AddMaxEquality(top, [tops[0], tops[1], bmax])
                piece((S40_, i, j), low, top)
    for leg in range(N - 1):
        mod.Add(sum(b[leg] for b in B) == cells[leg])
    for key, lst in terms.items():
        mod.Add(sum(lst) <= demand[key])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = limit_s
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    status = solver.Solve(mod)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return "infeasible" if status == cp_model.INFEASIBLE else "timeout"
    stacks = []
    for h, b in zip(H, B):
        h0 = [solver.Value(v) for v in h[0]]
        h1 = [solver.Value(v) for v in h[1]]
        bv = [solver.Value(v) for v in b]
        stacks.append(vector_pieces(h0, h1, bv, N, R))
    return stacks


def vector_pieces(h0: list, h1: list, b: list, N: int, R: int) -> tuple:
    pieces = []
    for t in range(R):
        for x, h in ((0, h0), (1, h1)):
            stops = [c for c in range(N) if c in (0, N - 1) or h[c] <= t]
            for u, v in zip(stops, stops[1:]):
                if t < min(b[u:v]):
                    pieces.append((S20_, u, v, t, x))
        stops = [c for c in range(N) if c in (0, N - 1) or h0[c] <= t]
        for u, v in zip(stops, stops[1:]):
            if t >= max(b[u:v]):
                pieces.append((S40_, u, v, t, -1))
    return tuple(sorted(pieces))


def fleet_base(T: dict, empty: list, S: int, R: int) -> tuple:
    N = len(T[S20_])
    cells20, cells40 = [], []
    for leg in range(N - 1):
        on20 = sum(T[S20_][i][j] for i in range(leg + 1) for j in range(leg + 1, N))
        on40 = sum(T[S40_][i][j] for i in range(leg + 1) for j in range(leg + 1, N))
        cells20.append(on20 // 2)
        cells40.append(on40 + empty[leg])
    return min(cells20) // R, min(cells40) // R


def try_fleet(T: dict, empty: list, fleets: tuple, R: int, zero: bool, budget: float, seed: int) -> dict:
    """One fleet split: mixed stacks first, then one flow per size for the pure stacks."""
    t0 = time.perf_counter()
    N = len(T[S20_])
    k20, k40, mix = fleets
    used = {S20_: defaultdict(int), S40_: defaultdict(int)}
    stacks = []
    if mix:
        got = mixed_stacks(T, empty, fleets, R, max(2.0, budget * 0.4), seed)
        if got is None or isinstance(got, str):
            return {"fail": f"A:{got}"}
        stacks = list(got)
        for pat in stacks:
            for s, i, j, _t, _c in pat:
                used[s][(i, j)] += 1
    dummy = {(i, i + 1): empty[i] for i in range(N - 1) if empty[i]}
    gv = {}
    cost = 0
    root = (0, N - 1, R)
    columns = {}
    for s, count, dum in ((S40_, k40, dummy), (S20_, 2 * k20, {})):
        built = build_1d(T[s], {root: count} if count else {}, R, used=used[s], dummy=dum, zero=zero)
        if built is None:
            return {"fail": f"B{s}:none"}
        lp1, u, g = built
        left = budget - (time.perf_counter() - t0)
        res1 = lp1.solve(max(3.0, left * (0.5 if s == S40_ else 0.95)))
        if res1.x is None:
            return {"fail": f"B{s}:{res1.status}"}
        vals = {key: int(round(res1.x[x])) for key, x in u.items()}
        columns[s] = decompose_tr1(vals, count, R, N)
        if not zero:
            for key, x in g.items():
                if res1.x[x] > 0.5:
                    gv[(s,) + key] = int(round(res1.x[x]))
            cost += int(round(res1.fun))
    if zero:
        gv = {(s, i, j, j): T[s][i][j] for s in (S20_, S40_) for i in range(N) for j in range(i + 1, N) if T[s][i][j]}
    for col in columns[S40_]:
        stacks.append(tuple(sorted((S40_, i, j, t, -1) for i, j, t in col)))
    cols20 = columns[S20_]
    for a, b in zip(cols20[0::2], cols20[1::2]):
        stacks.append(tuple(sorted([(S20_, i, j, t, 0) for i, j, t in a] + [(S20_, i, j, t, 1) for i, j, t in b])))
    return {"stacks": stacks, "g": gv, "obj": cost, "fleets": fleets}


def solve_tr4(T: dict, empty: list, S: int, R: int, limit: float, seed: int = 1) -> dict:
    """Try the purest fleet first; widen the mixed part until a plan appears."""
    t0 = time.perf_counter()
    b20, b40 = fleet_base(T, empty, S, R)
    configs = []
    for da, db in [(0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (1, 2), (2, 2), (3, 2), (2, 3), (3, 3)]:
        k20, k40 = b20 - da, b40 - db
        if k20 >= 0 and k40 >= 0:
            configs.append((k20, k40, S - k20 - k40))
    best = None
    fails = []
    # Zero first: it needs no box flow, so it is fast, and a zero ends the search.
    round_no = 0
    while best is None and time.perf_counter() - t0 < 0.25 * limit and round_no < 2:
        round_no += 1
        for fleets in configs:
            if time.perf_counter() - t0 > 0.25 * limit:
                break
            out = try_fleet(T, empty, fleets, R, True, 0.1 * limit, seed + round_no)
            if "fail" in out:
                fails.append(("zero", fleets, out["fail"]))
            else:
                best = out
                break
    if best is None:
        for fleets in configs:
            left = limit - (time.perf_counter() - t0)
            if left < 5 and best is not None:
                break
            out = try_fleet(T, empty, fleets, R, False, max(15.0, (0.7 if best is None else 0.5) * left), seed)
            if "fail" in out:
                fails.append(("flow", fleets, out["fail"]))
            elif best is None or out["obj"] < best["obj"]:
                best = out
                if best["obj"] <= 2:
                    break
            if time.perf_counter() - t0 > limit:
                break
    if best is None:
        return {"stacks": None, "status": "none", "fails": fails}
    return {
        "stacks": best["stacks"],
        "g": best["g"],
        "obj": best["obj"],
        "status": "zero" if best["obj"] == 0 else "flow",
        "fleets": best["fleets"],
        "fails": fails,
        "t": time.perf_counter() - t0,
    }
