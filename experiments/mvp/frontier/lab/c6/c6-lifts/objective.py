"""Optional fixed-charge assignment objective; the copied c5 default is untouched.

The graph counts planned-cargo current/later hatch use and D15 blockers in pure
pages. It is a search objective, not a certified map bound. See OBJECTIVE.md.
"""
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import sys
from time import monotonic
import warnings

import numpy as np
from scipy.optimize import linprog, OptimizeWarning
from scipy.sparse import coo_matrix, hstack, vstack

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from gap import Assignment as BaseAssignment


def graph_rows(pair, slot_page, pages, pods, ports, slot_cells, units, start,
               *, hatch_now, hatch_later, restow):
    """Return graph objective coefficients, sparse rows and typed indices.

    A cover/port is activated once regardless of how many boxes use it. A tier
    is charged once per earlier port if either cargo below or its hold cover
    needs access. Thus internal and cover restows are not counted twice.
    """
    costs, rows, rhs, names = [], [], [], {}

    def variable(key, cost):
        v = start + len(costs)
        names[key] = v
        costs.append(float(cost))
        return v

    def add(coefficients, bound=0.):
        combined = defaultdict(float)
        for v, a in coefficients:
            combined[v] += a
        rows.append({v: a for v, a in combined.items() if a})
        rhs.append(float(bound))

    hold, edges, tiers = defaultdict(list), defaultdict(list), defaultdict(list)
    for v, (b, q) in enumerate(pair):
        j, k = slot_page[q][0]
        page = pages[j]
        tiers[j, k].append(v)
        if not page.deck and page.cover is not None:
            hold[page.cover].append(v)
            edges[page.cover, pods[b]].append(v)
    cover_var = {c: variable(('cover', c), hatch_now) for c in hold}
    port_var = {key: variable(('port', *key), hatch_later) for key in edges}
    for kind, groups, variables in [('cover', hold, cover_var), ('port', edges, port_var)]:
        for key, members in groups.items():
            y = variables[key]
            by_box = defaultdict(list)
            for v in members:
                by_box[pair[v][0]].append(v)
            # Aggregating all candidate slots of one box is stronger than x_v<=y.
            for vs in by_box.values():
                add([(v, 1.) for v in vs] + [(y, -1.)])
            add([(y, 1.)] + [(v, -1.) for v in members])
            cover = key if kind == 'cover' else key[0]
            cells = {int(g) for v in hold[cover] for g in slot_cells[pair[v][1]] if g >= 0}
            add([(v, float(units[pair[v][0]])) for v in members] + [(y, -len(cells))])
            if kind == 'port':
                add([(y, 1.), (cover_var[cover], -1.)])
    for j, page in enumerate(pages):
        for k in range(len(page.slots)):
            at = tiers[j, k]
            if not at:
                continue
            for p in ports:
                later = [v for v in at if pods[pair[v][0]] > p]
                if not later:
                    continue
                lower = [[v for v in tiers[j, t] if pods[pair[v][0]] == p] for t in range(k)]
                lower = [vs for vs in lower if vs]
                cover = port_var.get((page.cover, p)) if page.deck else None
                if not lower and cover is None:
                    continue
                r = variable(('restow', j, k, p), restow)
                for vs in lower:
                    add([(v, 1.) for v in later + vs] + [(r, -1.)], 1.)
                if cover is not None:
                    add([(v, 1.) for v in later] + [(cover, 1.), (r, -1.)], 1.)
                add([(r, 1.)] + [(v, -1.) for v in later])
                add([(r, 1.)] + [(v, -1.) for vs in lower for v in vs]
                    + ([(cover, -1.)] if cover is not None else []))
    return np.asarray(costs), rows, np.asarray(rhs), names


class Assignment(BaseAssignment):
    """Drop-in optional LP arm, with coupled Boolean auxiliaries in its tail.

    ``hard_securing`` and ``hard_moments`` prohibit their base slack in both LP
    and integer tail. Keep these True for a feasibility-preserving neighbourhood.
    A changed LP objective alone is not guaranteed to reproduce a cold pass.
    """
    def __init__(self, *args, hard_securing=False, hard_moments=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hard_securing = hard_securing
        self.hard_moments = hard_moments

    def cost(self):
        # Fixed charges replace the base layout's per-box port surcharges.
        layout = self.layout
        self.layout = {}
        try:
            return super().cost()
        finally:
            self.layout = layout

    def build(self):
        settings = self.s
        self.s = replace(settings, restow_weight=0.)
        try:
            c, aub, bub, aeq, beq = super().build()
        finally:
            self.s = settings
        self.base_columns = len(c)
        obj = self.m.p.instance.objective
        extra, rows, rhs, self.graph_variables = graph_rows(
            self.pair, self.slot_page, self.m.pages, self.m.pod,
            sorted(self.m.ports), self.T.slot_cells, self.T.units, len(c),
            hatch_now=2.*float(obj.hatch_cover),
            hatch_later=2.*float(obj.future_hatch_cover),
            restow=float(obj.future_restow)*obj.future_restow_lifts_per_box)
        ncols = len(c) + len(extra)
        ri, rj, values = [], [], []
        for i, coefficients in enumerate(rows):
            for v, a in coefficients.items():
                ri.append(i); rj.append(v); values.append(a)
        graph = coo_matrix((values, (ri, rj)), shape=(len(rows), ncols)).tocsc()
        aub = vstack([hstack([aub, coo_matrix((aub.shape[0], len(extra)))]), graph]).tocsc()
        aeq = hstack([aeq, coo_matrix((aeq.shape[0], len(extra)))]).tocsc()
        self.hard_zero = set(self.overage.values()) if self.hard_securing else set()
        if self.hard_moments:
            # Base appends exactly one auxiliary per moment row, at the end.
            count = len(self._moments())
            self.hard_zero.update(range(len(c)-count, len(c)))
        self._built = (np.r_[c, extra], aub, np.r_[bub, rhs], aeq, beq)
        return self._built

    def solve(self, lower=None, upper=None, time_limit=60.):
        c, aub, bub, aeq, beq = self._built or self.build()
        lo = np.zeros(len(c)) if lower is None else np.array(lower, copy=True)
        hi = np.full(len(c), np.inf) if upper is None else np.array(upper, copy=True)
        hi[self.base_columns:] = 1.
        for j in self.hard_zero:
            hi[j] = 0.
        started = monotonic()
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=OptimizeWarning)
            result = linprog(c, A_ub=aub, b_ub=bub, A_eq=aeq, b_eq=beq,
                             bounds=np.c_[lo, hi], method='highs',
                             options=dict(threads=1, time_limit=max(.01, time_limit)))
        return dict(status=int(result.status), s=monotonic()-started,
                    fun=result.fun, x=result.x,
                    reduced=None if result.x is None else result.lower.marginals,
                    uncovered=None if result.x is None else float(result.x[self.n:self.n+len(self.m.boxes)].sum()))

    def tail(self, fixed, *, seconds=30., seed=0, log=None, hint=None):
        """Solve all graph coupling exactly, including fixed-only activation rows.

        Base soft variables use thousandths and graph variables are Boolean.
        Floating coefficients round conservatively; original rows are also
        checked after solving. All boxes are aboard or this returns None.
        """
        from ortools.sat.python import cp_model
        started = monotonic()
        c, aub, bub, _, _ = self._built or self.build()
        n, nb = self.n, len(self.m.boxes)
        taken = {int(g) for q in fixed.values() for g in self.T.slot_cells[q] if g >= 0}
        solid = np.zeros(len(c))
        for v, (b, q) in enumerate(self.pair):
            if fixed.get(b) == q:
                solid[v] = 1.
        if set(fixed) - {self.pair[v][0] for v in np.flatnonzero(solid[:n])}:
            return None
        free = [v for v, (b, q) in enumerate(self.pair) if b not in fixed
                and not ({int(g) for g in self.T.slot_cells[q] if g >= 0} & taken)]
        cp = cp_model.CpModel()
        variables = {v: cp.NewBoolVar(f'x{v}') for v in free}
        divisors = {v: 1 for v in free}
        by_box = defaultdict(list)
        for v in free:
            by_box[self.pair[v][0]].append(variables[v])
        for b in self.m.boxes:
            if b not in fixed:
                if not by_box[b]:
                    return None
                cp.AddExactlyOne(by_box[b])
        for v in range(n+nb, len(c)):
            if v in self.hard_zero:
                continue
            if v < self.base_columns:
                variables[v] = cp.NewIntVar(0, 10000000, f'z{v}')
                divisors[v] = 1000
            else:
                variables[v] = cp.NewBoolVar(f'g{v}')
                divisors[v] = 1
        matrix = aub.tocsr()
        used = matrix @ solid
        for i in range(matrix.shape[0]):
            indices = matrix.indices[matrix.indptr[i]:matrix.indptr[i+1]]
            data = matrix.data[matrix.indptr[i]:matrix.indptr[i+1]]
            terms = [(int(v), float(a)/divisors[v]) for v, a in zip(indices, data) if v in variables]
            room = float(bub[i]-used[i])
            if not terms:
                if room < -1e-7:
                    return None
                continue
            scale = 200000./max(abs(a) for _, a in terms)
            cp.Add(sum(int(np.ceil(a*scale-1e-9))*variables[v] for v, a in terms)
                   <= int(np.floor(room*scale+1e-9)))
        cp.Minimize(sum(int(round(100000*c[v]/divisors[v]))*var for v, var in variables.items()))
        if hint is not None:
            for v in free:
                cp.AddHint(variables[v], int(hint[v] >= .5))
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.max_time_in_seconds = max(.01, seconds-(monotonic()-started))
        solver.parameters.random_seed = seed
        status = solver.Solve(cp)
        if log is not None:
            log.append(dict(tail=solver.StatusName(status), vars=len(variables),
                            objective='fixed charge graph', s=monotonic()-started))
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        values = np.array(solid, copy=True)
        for v, var in variables.items():
            values[v] = solver.Value(var)/divisors[v]
        if np.max(matrix@values-bub, initial=0.) > 1e-6:
            return None
        answer = dict(fixed)
        answer.update({self.pair[v][0]: self.pair[v][1] for v in free if solver.Value(variables[v])})
        return answer if set(answer) == set(self.m.boxes) else None
