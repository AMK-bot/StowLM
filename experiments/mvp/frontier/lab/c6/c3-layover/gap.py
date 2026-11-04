"""Frozen C5 assignment LP, extended to fixed arrival prefixes and count-bounded search. See LP.md."""
from collections import defaultdict
from dataclasses import dataclass, field
from time import monotonic
import warnings

import numpy as np
from scipy.optimize import linprog, OptimizeWarning
from scipy.sparse import coo_matrix

from stowage.checker import lashing
from rows import stack_rows

UNCOVERED = 1000.
HIGH_CUBE = 2.896
SWAP_TRIALS = 30
ROW_NAMES = ('tension', 'rod', 'rack+', 'rack-', 'comp', 'long_rack', 'shear')


@dataclass
class Settings:
    limit: float = 1.                  # securing rows read <= limit, below 1 to leave room for rounding
    vertical_weight: float = .002      # lifts per tonne-metre: keeps KG low without outbidding a lift
    cover_port_weight: float = 2.      # a hold box under a cover laid out for another port
    deck_port_weight: float = 2.       # a deck box staying past the port its cover opens at
    restow_weight: float = 2.          # soft row: a later-leaving box directly above an earlier one
    moment_weight: float = .1          # lifts per tonne-metre outside a moment target
    deck_tier_cap: dict = field(default_factory=dict)  # page index -> usable tiers, for the price-of-securing arms
    lcg: tuple | None = None           # cargo LCG window, m
    tcg: float | None = None           # cargo TCG half-width, m
    vertical_max_tm: float | None = None
    pairs_per_box: int = 0             # 0 keeps every eligible slot
    seed: int = 0
    joint_loading: object = None       # callable(solver) -> physical joint LP row block
    joint_tail_solver: str = 'milp'
    mip_nodes: int = 2000
    mip_improving_solutions: int = 1
    mip_simplex_iterations: int = 20000
    work_bounded: bool = True
    max_lp_calls: int = 40
    lp_maxiter: int = 20000
    max_dive_steps: int = 40
    max_tail_calls: int = 6
    cp_deterministic_time: float = 2.
    cp_conflicts: int = 100000
    max_exchange_trials: int = 4000
    max_exchange_moves: int = 30
    # Contributions outside model.boxes. Values are tonnes and tonne-metres.
    background_mass_t: float = 0.
    background_moments_tm: tuple = (0., 0., 0.)
    overage_weight: float = 400.       # lifts per unit of securing ratio over the limit, per stack


class Assignment:
    """x[b,s]=1 puts box b in slot s. Pages come from an existing page Model, so slots and eligibility are the real ones."""

    def __init__(self, model, motions, settings=None, layout=None):
        self.m, self.T, self.motions = model, model.T, motions
        self.s = settings or Settings()
        self.layout = layout or {}
        T = self.T
        self.fixed_pairs = {int(b): int(q) for b, q in getattr(model, 'fixed_pairs', {}).items()}
        self.work = dict(lp_calls=0, dive_steps=0, tail_calls=0, exchange_trials=0, exchange_moves=0)
        self.box_pos = {b: i for i, b in enumerate(model.boxes)}
        self.slot_page = defaultdict(list)
        for j, page in enumerate(model.pages):
            cap = self.s.deck_tier_cap.get(j, len(page.slots)) if page.deck else len(page.slots)
            for k, s in enumerate(page.slots[:cap]):
                self.slot_page[int(s)].append((j, k))
        self.pair = []
        for b in model.boxes:
            long_box = T.units[b] != 1
            for s in ([self.fixed_pairs[b]] if b in self.fixed_pairs else np.flatnonzero(model.static[b])):
                s = int(s)
                if s in self.slot_page and (model.pages[self.slot_page[s][0][0]].kind == 'long') == long_box:
                    self.pair.append((b, s))
        if self.s.pairs_per_box:
            # Slots repeat by mirror and bay, so a seeded sample per box keeps the LP's shape at a fraction of its size.
            import random
            rng = random.Random(self.s.seed)
            by_box = defaultdict(list)
            for pair in self.pair:
                by_box[pair[0]].append(pair)
            self.pair = [q for b in model.boxes for q in (by_box[b] if len(by_box[b]) <= self.s.pairs_per_box
                                                          else rng.sample(by_box[b], self.s.pairs_per_box))]
        self.n = len(self.pair)
        self.pair_index = {pair: v for v, pair in enumerate(self.pair)}
        if any((b, q) not in self.pair_index for b, q in self.fixed_pairs.items()):
            raise ValueError('fixed arrival pair is absent from full physical pages')
        self._built = None
        self.joint_aux_start, self.joint_spec = None, None
        self.last_joint_solution = None
        self.tier_height = float(np.mean([float(model.p.boxes[T.box_ids[b]].height_m) for b in model.boxes])) if model.boxes else 2.591
        self.high = {b: float(model.p.boxes[T.box_ids[b]].height_m) > 2.7 for b in model.boxes}
        self.closed = set()
        self.row_cache = {}

    def prefix_length(self, j):
        page = self.m.pages[j]
        fixed = {q: b for b, q in self.fixed_pairs.items()}
        n = 0
        while n < len(page.slots) and int(page.slots[n]) in fixed:
            n += 1
        return n

    def preserve_prefix(self, j, boxes):
        """Reinsert the immutable prefix, leaving the supplied order only in the free suffix."""
        page = self.m.pages[j]
        fixed = {q: b for b, q in self.fixed_pairs.items()}
        prefix = [fixed[int(q)] for q in page.slots[:self.prefix_length(j)]]
        return prefix+[b for b in boxes if b not in self.fixed_pairs]

    def _fixed_bounds(self, lower, upper):
        for b, q in self.fixed_pairs.items():
            v = self.pair_index[(b, q)]
            lower[v] = upper[v] = 1.
            upper[self.n+self.box_pos[b]] = 0.
        if self.joint_spec is not None:
            # A joint construction must carry every box. Its root LP is the
            # necessary relaxation of exactly the same complete integer tail.
            upper[self.n:self.n+len(self.m.boxes)] = 0.
            for j, (lo, hi) in enumerate(self.joint_spec['bounds']):
                index = self.joint_aux_start+j
                lower[index] = max(lower[index], lo)
                upper[index] = min(upper[index], hi)
        return lower, upper

    def page_rows(self, page, height=None):
        x, y = self.m._slot_xy(page, 0)
        base = max(float(self.T.stack_base_z[j]) for j in page.stacks)
        heights = [height or self.tier_height]*len(page.slots)
        fixed = {q: b for b, q in self.fixed_pairs.items()}
        for k, q in enumerate(page.slots):
            if int(q) in fixed:
                heights[k] = float(self.m.p.boxes[self.T.box_ids[fixed[int(q)]]].height_m)
        return stack_rows(self.motions, x, y, base, len(page.slots), heights)

    def roof_room(self, page, tier):
        """Metres of stack height the sight line allows up to this tier, or None without a sight restriction."""
        if not getattr(self.m, 'sight_frames', None) or not page.deck:
            return None
        base = max(float(self.T.stack_base_z[j]) for j in page.stacks)
        box = next(b for b in self.m.boxes if (self.T.units[b] == 1) == (page.kind == '20'))
        return self.m.sight_limit(page, tier, box)-base

    def stack_ratio(self, page, boxes):
        """Largest securing row of a concrete bottom-up column, at the boxes' own heights."""
        if not page.deck or not boxes:
            return 0.
        heights = tuple(round(float(self.m.p.boxes[self.T.box_ids[b]].height_m), 3) for b in boxes)
        key = (page.slots[0], heights)
        if key not in self.row_cache:
            x, y = self.m._slot_xy(page, 0)
            base = max(float(self.T.stack_base_z[j]) for j in page.stacks)
            self.row_cache[key] = stack_rows(self.motions, x, y, base, len(boxes), list(heights))
        A, Bt, Bl, names = self.row_cache[key]
        force = [self.box_force(b) for b in boxes]
        return float(np.max(A@np.array([f[0] for f in force])+Bt@np.array([f[1] for f in force])+Bl@np.array([f[2] for f in force])))

    def box_force(self, b):
        c = self.m.p.boxes[self.T.box_ids[b]]
        h, oog = float(c.height_m), c.oog
        el = lashing.LENGTH[c.length_ft]+(oog.over_length_fore_m+oog.over_length_aft_m if oog else 0.)
        ew = lashing.WIDTH+(oog.over_width_port_m+oog.over_width_starboard_m if oog else 0.)
        return (max(c.gross_mass_kg, 2500 if c.length_ft == 20 else 3500), max(15700 if c.length_ft == 20 else 31600, el*h*1000.),
                max(6400., ew*h*1000.))

    def slot_cg_z(self, b, slot):
        """Exact retained prefix elevation; free preceding tiers use the LP reference height."""
        j, k = self.slot_page[slot][0]
        page = self.m.pages[j]
        fixed = {q: box for box, q in self.fixed_pairs.items()}
        before = sum(float(self.m.p.boxes[self.T.box_ids[fixed[int(q)]]].height_m)
                     if int(q) in fixed else self.tier_height for q in page.slots[:k])
        return (max(float(self.T.stack_base_z[t]) for t in page.stacks)+before+
                .5*float(self.m.p.boxes[self.T.box_ids[b]].height_m))

    def cost(self):
        T, model, s = self.T, self.m, self.s
        out = np.zeros(self.n)
        for v, (b, slot) in enumerate(self.pair):
            j, k = self.slot_page[slot][0]
            page = model.pages[j]
            z = self.slot_cg_z(b, slot)
            out[v] = s.vertical_weight*float(T.mass[b])/1000.*z
            port = self.layout.get(page.cover)
            if port is not None:
                if not page.deck and model.pod[b] != port:
                    out[v] += s.cover_port_weight
                if page.deck and model.pod[b] > port:
                    out[v] += s.deck_port_weight
        return out

    def build(self):
        T, model, s = self.T, self.m, self.s
        n, nb = self.n, len(model.boxes)
        by_slot = defaultdict(list)
        for v, (_, slot) in enumerate(self.pair):
            by_slot[slot].append(v)
        ports = sorted(model.ports)
        ri, rj, rv, rhs, extra_cost = [], [], [], [], []
        self.securing_rows = defaultdict(list)
        self.overage = {}
        next_extra = n+nb

        def add(pairs, bound):
            pairs = [(v, c) for v, c in pairs if abs(c) > 1e-9]
            for v, c in pairs:
                ri.append(len(rhs)); rj.append(v); rv.append(c)
            rhs.append(bound)
        by_cell = defaultdict(list)
        for slot, vs in by_slot.items():
            for c in T.slot_cells[slot]:
                if c >= 0:
                    by_cell[int(c)].extend(vs)
        for vs in by_cell.values():
            add([(v, 1.) for v in vs], 1.)
        for v, cells in self.oog_conflicts():
            for c in cells:
                add([(v, 1.)]+[(u, 1.) for u in by_cell.get(c, []) if u != v], 1.)
        ranks = sorted({int(T.rank[b]) for b in model.boxes})
        for j, page in enumerate(model.pages):
            tiers = [by_slot.get(int(q), []) for q in page.slots]
            if not any(tiers):
                continue
            for k in range(len(tiers)-1):
                up, down = tiers[k+1], tiers[k]
                if not up:
                    continue
                add([(v, 1.) for v in up]+[(v, -1.) for v in down], 0.)         # support
                add([(v, 1.) for v in up if T.closes_stack[self.pair[v][0]]]*0+  # stack closers end a stack
                    [(v, 1.) for v in up]+[(v, 1.) for v in down if T.closes_stack[self.pair[v][0]]], 1.)
                # E2: a box more than the tolerance above class r needs a lower box above class r. E1 alike.
                for r in ranks[:-1]:
                    add([(v, 1.) for v in up if T.rank[self.pair[v][0]] > r+T.e2_tol]
                        + [(v, -1.) for v in down if T.rank[self.pair[v][0]] > r], 0.)
                if T.e1_threshold is not None:
                    add([(v, 1.) for v in up if model.heavy[self.pair[v][0]]]
                        + [(v, -1.) for v in down if model.heavy[self.pair[v][0]]], 0.)
                # Restow, soft: leaving after port p, directly above a box leaving by p.
                for p in ports[:-1]:
                    later_up = [(v, 1.) for v in up if model.pod[self.pair[v][0]] > p]
                    if later_up:
                        add(later_up+[(v, -1.) for v in down if model.pod[self.pair[v][0]] > p]+[(next_extra, -1.)], 0.)
                        extra_cost.append(s.restow_weight)
                        next_extra += 1
            everything = [v for vs in tiers for v in vs]
            add([(v, float(T.share[self.pair[v][0]])/page.limit_kg) for v in everything], 1.)
            add([(v, float(T.height[self.pair[v][0]])/page.hmax) for v in everything], 1.)
            add([(v, 1.) for v in everything if T.is_dg[self.pair[v][0]]], 1.)
            if not page.deck:
                continue
            for k in range(len(tiers)):
                cap = self.roof_room(page, k)
                if cap is None or not tiers[k]:
                    continue
                big = max(float(T.height[self.pair[v][0]]) for v in tiers[k])*(k+1)
                add([(v, float(T.height[self.pair[v][0]])/big) for t in tiers[:k+1] for v in t]+[(v, 1.) for v in tiers[k]], cap/big+1.)
            A, Bt, Bl, names = self.page_rows(page)
            Ah, Bth, Blh, _ = self.page_rows(page, HIGH_CUBE)
            over = next_extra
            self.overage[j] = over
            extra_cost.append(s.overage_weight)
            next_extra += 1
            for r, name in enumerate(names):
                if name[0] not in ROW_NAMES:
                    continue
                self.securing_rows[j].append(len(rhs))
                pairs = [(over, -1.)]
                for k, vs in enumerate(tiers):
                    for v in vs:
                        b = self.pair[v][0]
                        mass, wt, wl = self.box_force(b)
                        if self.high[b]:
                            pairs.append((v, Ah[r, k]*mass+Bth[r, k]*wt+Blh[r, k]*wl))
                        else:
                            pairs.append((v, A[r, k]*mass+Bt[r, k]*wt+Bl[r, k]*wl))
                add(pairs, s.limit)
        for name, coefficient, bound in self._moments():
            add(coefficient+[(next_extra, -1.)], bound)
            extra_cost.append(s.moment_weight)
            next_extra += 1
        self.joint_aux_start = next_extra
        self.joint_spec = None
        if s.joint_loading is not None:
            joint = s.joint_loading(self) if callable(s.joint_loading) else s.joint_loading
            self.joint_spec = dict(joint)
            from scipy.sparse import csr_matrix
            Ap = csr_matrix(joint['A_pair'])
            Aa = csr_matrix(joint['A_aux'])
            bjoint = np.asarray(joint['b'], float)
            maux = Aa.shape[1]
            if Ap.shape != (Aa.shape[0], n) or bjoint.shape != (Aa.shape[0],):
                raise ValueError('joint loading rows have inconsistent pair/aux dimensions')
            if len(joint['bounds']) != maux or len(joint['quantum']) != maux or len(joint['cost']) != maux:
                raise ValueError('joint loading auxiliary metadata dimensions differ')
            aux_integrality = np.asarray(joint.get('aux_integrality', np.zeros(maux)), int)
            if aux_integrality.shape != (maux,) or not np.isin(aux_integrality, [0, 1]).all():
                raise ValueError('joint physical auxiliary integrality must be zero/one per column')
            self.joint_spec['aux_integrality'] = aux_integrality
            for (lo, hi), quantum in zip(joint['bounds'], joint['quantum']):
                if not (0. <= lo <= hi < np.inf and 0. < quantum < np.inf):
                    raise ValueError('joint physical auxiliary variables need finite nonnegative bounds/positive quantum')
            self.joint_row_start = len(rhs)
            for i in range(Aa.shape[0]):
                pairs = [(int(j), float(a)) for j, a in zip(Ap[i].indices, Ap[i].data)]
                pairs += [(next_extra+int(j), float(a)) for j, a in zip(Aa[i].indices, Aa[i].data)]
                add(pairs, float(bjoint[i]))
            extra_cost.extend(map(float, joint['cost']))
            next_extra += maux
        total = next_extra
        Aub = coo_matrix((rv, (ri, rj)), shape=(len(rhs), total)).tocsc()
        Aeq = coo_matrix((np.ones(n+nb), ([self.box_pos[b] for b, _ in self.pair]+list(range(nb)), list(range(n+nb)))),
                         shape=(nb, total)).tocsc()
        c = np.r_[self.cost(), np.full(nb, UNCOVERED), np.array(extra_cost)]
        self._built = (c, Aub, np.array(rhs, float), Aeq, np.ones(nb))
        return self._built

    def oog_conflicts(self):
        """For each OOG placement, cells its envelope reaches at its own tier and one either side.

        E9 tests real envelopes; reaching one tier up or down covers the high-cube offset between neighbouring stacks.
        Conservative, and the checker decides. Cell geometry follows `rules_special._killed_cells`.
        """
        from stowage.checker.rules_special import _clearance_parameter
        from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M, cells_of_slot, parse_slot
        T, model, p = self.T, self.m, self.m.p
        out = []
        boxes = {b for b in model.boxes if p.boxes[T.box_ids[b]].oog is not None}
        if not boxes:
            return out
        try:
            clear = float(_clearance_parameter(p.instance, 'expert.e9.over_width_length', 'oog_clearance', 'minimum_clearance_m') or 0.)
        except (TypeError, ValueError):
            clear = 0.
        by_id = {T.cell_ids[c]: c for c in range(len(T.cell_ids))}
        where = {cid: parse_slot(cid) for cid in by_id if cid in p.cells}
        for v, (b, slot) in enumerate(self.pair):
            if b not in boxes:
                continue
            box = p.boxes[T.box_ids[b]]
            o = box.oog
            if not any((o.over_width_port_m, o.over_width_starboard_m, o.over_length_fore_m, o.over_length_aft_m)):
                continue
            ids = cells_of_slot(T.slot_ids[slot])
            physical = [p.cells[c] for c in ids]
            x = sum(c.lcg_m for c in physical)/len(physical)
            y = sum(c.tcg_m for c in physical)/len(physical)
            half = CONTAINER_LENGTH_M[box.length_ft]/2
            ex = (x-half-o.over_length_aft_m-clear, x+half+o.over_length_fore_m+clear)
            ey = (y-CONTAINER_WIDTH_M/2-o.over_width_starboard_m-clear, y+CONTAINER_WIDTH_M/2+o.over_width_port_m+clear)
            own = parse_slot(ids[0])
            hit = []
            for cid, a in where.items():
                if a.on_deck != own.on_deck or abs(a.tier-own.tier) > 2 or cid in ids:
                    continue
                c = p.cells[cid]
                if (c.lcg_m-3.029 < ex[1] and c.lcg_m+3.029 > ex[0] and c.tcg_m-1.219 < ey[1] and c.tcg_m+1.219 > ey[0]):
                    hit.append(by_id[cid])
            if hit:
                out.append((v, hit))
        return out

    def _moments(self):
        T, model, s = self.T, self.m, self.s
        out = []
        mass = [float(T.mass[b])/1000. for b, _ in self.pair]
        if s.lcg is not None or s.tcg is not None or s.vertical_max_tm is not None:
            place = [model._slot_xy(model.pages[self.slot_page[q][0][0]], self.slot_page[q][0][1]) for _, q in self.pair]
            high = [self.slot_cg_z(b, q) for b, q in self.pair]
            total = float(sum(T.mass[b] for b in model.boxes))/1000.+s.background_mass_t
            bx, by, bz = s.background_moments_tm
        if s.lcg is not None:
            out.append(('lcg_max', [(v, mass[v]*place[v][0]) for v in range(self.n)], s.lcg[1]*total-bx))
            out.append(('lcg_min', [(v, -mass[v]*place[v][0]) for v in range(self.n)], -s.lcg[0]*total+bx))
        if s.tcg is not None:
            out.append(('tcg_max', [(v, mass[v]*place[v][1]) for v in range(self.n)], s.tcg*total-by))
            out.append(('tcg_min', [(v, -mass[v]*place[v][1]) for v in range(self.n)], s.tcg*total+by))
        if s.vertical_max_tm is not None:
            out.append(('vertical', [(v, mass[v]*high[v]) for v in range(self.n)], s.vertical_max_tm-bz))
        return out

    def solve(self, lower=None, upper=None, time_limit=60.):
        c, Aub, bub, Aeq, beq = self._built or self.build()
        bounds = np.c_[np.zeros(len(c)) if lower is None else lower, np.full(len(c), np.inf) if upper is None else upper]
        self._fixed_bounds(bounds[:, 0], bounds[:, 1])
        started = monotonic()
        if self.work['lp_calls'] >= self.s.max_lp_calls:
            return dict(status=1, s=0., fun=None, x=None, reduced=None, uncovered=None, work_exhausted='lp_calls')
        self.work['lp_calls'] += 1
        options = dict(threads=1, maxiter=self.s.lp_maxiter)
        if not self.s.work_bounded:
            options['time_limit'] = time_limit
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=OptimizeWarning)
            res = linprog(c, A_ub=Aub, b_ub=bub, A_eq=Aeq, b_eq=beq, bounds=bounds, method='highs',
                          options=options)
        if res.x is not None and self.joint_spec is not None:
            self.last_joint_solution = np.asarray(res.x[self.joint_aux_start:], float).copy()
        return dict(status=int(res.status), s=monotonic()-started, fun=res.fun, x=res.x,
                    reduced=None if res.x is None else res.lower.marginals,
                    uncovered=float(res.x[self.n:self.n+len(self.m.boxes)].sum()) if res.x is not None else None)

    def tail(self, fixed, *, seconds=30., seed=0, log=None, hint=None):
        """Finish a partial assignment exactly: an integer program over every eligible pair of the unfixed boxes.

        Rows are scaled to integers rounded the safe way, so an integer point here satisfies the LP's rows.
        Each shared slack has its own physical quantum, never a coefficient
        rounded to zero. `fixed` is {box: slot}; returns every box or None.
        """
        from ortools.sat.python import cp_model
        fixed = dict(fixed)
        if any(b in fixed and fixed[b] != q for b, q in self.fixed_pairs.items()):
            raise ValueError('tail cannot move fixed arrival cargo')
        fixed.update(self.fixed_pairs)
        if self.work['tail_calls'] >= self.s.max_tail_calls:
            return None
        self.work['tail_calls'] += 1
        c, Aub, bub, Aeq, beq = self._built or self.build()
        if self.joint_spec is not None and self.s.joint_tail_solver == 'milp':
            return self._tail_milp(fixed, log=log)
        T = self.T
        n, nb = self.n, len(self.m.boxes)
        taken = {int(g) for q in fixed.values() for g in T.slot_cells[q] if g >= 0}
        solid = np.zeros(Aub.shape[1])
        for v, (b, q) in enumerate(self.pair):
            if fixed.get(b) == q:
                solid[v] = 1.
        free = [v for v, (b, q) in enumerate(self.pair) if b not in fixed
                and not ({int(g) for g in T.slot_cells[q] if g >= 0} & taken)]
        model = cp_model.CpModel()
        var = {v: model.NewBoolVar(f'x{v}') for v in free}
        physical, physical_quantum = {}, {}
        if self.joint_spec is not None:
            for j, ((lo, hi), quantum) in enumerate(zip(self.joint_spec['bounds'], self.joint_spec['quantum'])):
                index = self.joint_aux_start+j
                low = int(np.ceil(lo/quantum-1e-9)); high = int(np.floor(hi/quantum+1e-9))
                if low > high:
                    return None
                physical[index] = model.NewIntVar(low, high, f'u{j}')
                if self.joint_spec['aux_integrality'][j]:
                    steps = int(round(1./quantum))
                    if not np.isclose(steps*quantum, 1., atol=1e-12):
                        raise ValueError('integer physical CP auxiliary needs a grid dividing one')
                    units = model.NewIntVar(int(np.ceil(lo)), int(np.floor(hi)), f'physical_integer{j}')
                    model.Add(physical[index] == steps*units)
                physical_quantum[index] = float(quantum)
        soft_end = self.joint_aux_start
        def is_soft(j):
            return n+nb <= j < soft_end
        rows = Aub.tocsr()
        used = rows@solid
        live = np.zeros(Aub.shape[1])
        live[free] = 1.
        live[list(physical)] = 1.
        touched = np.asarray(abs(rows)@live).ravel() > 0
        # Constant hard rows still matter: do not silently accept an illegal fixed prefix.
        for i in np.flatnonzero(~touched):
            cols = rows.indices[rows.indptr[i]:rows.indptr[i+1]]
            if not any(is_soft(j) for j in cols) and used[i] > bub[i]+1e-7:
                if log is not None:
                    log.append(dict(tail='fixed hard row violated', row=int(i)))
                return None
        # A fixed physical quantum of .001 can round a negative slack
        # coefficient to ZERO when moment rows have large box coefficients.
        # Choose one coarser physical quantum for each shared soft variable,
        # so every row retains at least one negative integer unit. This keeps
        # conservative physical coefficient rounding without hardening a soft
        # target. Securing rows retain their original .001 quantum in practice.
        prepared, quantum = [], {}
        for i in range(rows.shape[0]):
            cols, values = rows.indices[rows.indptr[i]:rows.indptr[i+1]], rows.data[rows.indptr[i]:rows.indptr[i+1]]
            terms = [(int(j), float(a)) for j, a in zip(cols, values) if int(j) in var]
            soft = [(int(j), float(a)) for j, a in zip(cols, values) if is_soft(j)]
            physical_terms = [(int(j), float(a)) for j, a in zip(cols, values) if int(j) in physical]
            room = float(bub[i]-used[i])
            physical_upper = sum(max(a*float(self.joint_spec['bounds'][j-self.joint_aux_start][0]),
                                     a*float(self.joint_spec['bounds'][j-self.joint_aux_start][1]))
                                 for j, a in physical_terms)
            if sum(max(a, 0.) for _, a in terms)+physical_upper <= room:
                continue                       # cannot bind, whatever is chosen
            if not terms and not soft and not physical_terms:
                continue                       # fixed hard rows checked above
            scale = 20000./max((abs(a) for _, a in terms), default=1.)
            if physical_terms:
                # Joint loading rows are normalized. Extra precision avoids
                # losing their tank/draft increments on the declared grids.
                scale = max(scale, 1e12)
                span = sum(abs(a) for _, a in terms)+sum(
                    abs(a)*self.joint_spec['bounds'][j-self.joint_aux_start][1] for j, a in physical_terms)
                scale = min(scale, 2.**50/max(1., span))
            integers = [(j, int(np.ceil(a*scale-1e-9))) for j, a in terms]
            integers += [(j, int(np.ceil(a*scale*physical_quantum[j]-1e-9))) for j, a in physical_terms]
            bound = int(np.floor(room*scale+1e-9))
            for j, a in soft:
                if a >= 0.:
                    raise ValueError('tail expects nonnegative soft variables with negative row coefficients')
                quantum[j] = max(quantum.get(j, .001), 2./(scale*abs(a)))
            prepared.append((integers, soft, scale, bound))
        soft_coefficients, soft_upper = [], defaultdict(int)
        for terms, soft, scale, bound in prepared:
            coefficients = [(j, int(np.ceil(a*scale*quantum[j]-1e-9))) for j, a in soft]
            maximum_excess = max(0, sum(max(a, 0)*(int(np.floor(self.joint_spec['bounds'][j-self.joint_aux_start][1]/physical_quantum[j]+1e-9))
                                                 if j in physical else 1) for j, a in terms)-bound)
            for j, a in coefficients:
                assert a <= -1, 'soft row coefficient vanished during integer scaling'
                # Each slack alone can cover the entire row's worst possible
                # excess, so this finite bound never converts a soft row hard.
                soft_upper[j] = max(soft_upper[j], (maximum_excess+(-a)-1)//(-a)+1)
            soft_coefficients.append(coefficients)
        slack = {j: model.NewIntVar(0, upper, f'z{j}') for j, upper in soft_upper.items()}
        for (terms, _, _, bound), coefficients in zip(prepared, soft_coefficients):
            expression = sum(a*(physical[j] if j in physical else var[j]) for j, a in terms)+sum(a*slack[j] for j, a in coefficients)
            model.Add(expression <= bound)
        by_box = defaultdict(list)
        for v in free:
            by_box[self.pair[v][0]].append(v)
        for b in self.m.boxes:
            if b not in fixed:
                if not by_box[b]:
                    if log is not None:
                        log.append(dict(tail='box has no free slot', box=int(b)))
                    return None
                model.AddExactlyOne([var[v] for v in by_box[b]])
        # A common objective scale preserves actual lift costs across physical
        # slack units. The former .1*c[j] truncated moment penalties to zero.
        model.Minimize(sum(int(round(10000*c[v]))*var[v] for v in var)+
                       sum(max(1, int(round(10000*c[j]*quantum[j])))*slack[j] for j in slack)+
                       sum(int(round(10000*c[j]*physical_quantum[j]))*physical[j] for j in physical))
        if hint is not None:
            for v in free:
                model.AddHint(var[v], int(hint[v] >= .5))
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        if self.s.work_bounded:
            solver.parameters.max_deterministic_time = self.s.cp_deterministic_time
            solver.parameters.max_number_of_conflicts = self.s.cp_conflicts
        else:
            solver.parameters.max_time_in_seconds = max(1., seconds)
        solver.parameters.random_seed = seed
        status = solver.Solve(model)
        if log is not None:
            log.append(dict(tail=solver.StatusName(status), left=nb-len(fixed), vars=len(var), s=round(solver.WallTime(), 2),
                            slack_quanta={str(j): quantum[j] for j in slack},
                            objective_lifts=solver.ObjectiveValue()/10000. if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None))
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        if self.joint_spec is not None:
            self.last_joint_solution = np.array([solver.Value(physical[self.joint_aux_start+j])*physical_quantum[self.joint_aux_start+j]
                                                 for j in range(len(physical))], float)
        out = dict(fixed)
        out.update({self.pair[v][0]: self.pair[v][1] for v in free if solver.Value(var[v])})
        return out

    def _tail_milp(self, fixed, *, log=None):
        """Exact joint row model: binary box edges, continuous ballast/draught.

        HiGHS node count bounds integer search. A feasible incumbent is usable
        even at the node limit; neither model status nor optimality proves any
        statement about the nonlinear checker or the whole map.
        """
        from scipy.optimize import milp, Bounds, LinearConstraint
        c, Aub, bub, Aeq, beq = self._built
        nb = len(self.m.boxes)
        lower, upper = np.zeros(len(c)), np.full(len(c), np.inf)
        upper[:self.n] = 1.
        upper[self.n:self.n+nb] = 0.  # tail is completion, never boxes ashore
        self._fixed_bounds(lower, upper)
        for v, (b, q) in enumerate(self.pair):
            if b in fixed:
                lower[v] = upper[v] = float(fixed[b] == q)
        integrality = np.zeros(len(c), np.uint8); integrality[:self.n] = 1
        integrality[self.joint_aux_start:] = self.joint_spec['aux_integrality']
        started = monotonic()
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Unrecognized options detected')
            result = milp(c, integrality=integrality, bounds=Bounds(lower, upper),
                constraints=[LinearConstraint(Aub, np.full(len(bub), -np.inf), bub),
                             LinearConstraint(Aeq, beq, beq)],
                options=dict(threads=1, node_limit=int(self.s.mip_nodes), mip_rel_gap=0.,
                             mip_max_improving_sols=int(self.s.mip_improving_solutions),
                             simplex_iteration_limit=int(self.s.mip_simplex_iterations)))
        feasible = result.x is not None
        if feasible:
            x = np.asarray(result.x, float)
            feasible = (np.isfinite(x).all() and
                        np.max(lower-x, initial=0.) <= 1e-6 and
                        np.max(x-upper, initial=0.) <= 1e-6 and
                        np.max(Aub@x-bub, initial=0.) <= 1e-6 and
                        np.max(np.abs(Aeq@x-beq), initial=0.) <= 1e-6 and
                        np.max(np.abs(x[integrality > 0]-np.rint(x[integrality > 0])), initial=0.) <= 1e-6)
        if log is not None:
            log.append(dict(tail='MILP', status=int(result.status), incumbent=bool(feasible),
                left=nb-len(fixed), node_limit=int(self.s.mip_nodes),
                integer_auxiliary_variables=int(sum(self.joint_spec['aux_integrality'])),
                improving_solutions_limit=int(self.s.mip_improving_solutions),
                simplex_iteration_limit=int(self.s.mip_simplex_iterations),
                message=str(result.message),
                nodes=int(result.mip_node_count) if getattr(result, 'mip_node_count', None) is not None else None,
                objective_lifts=float(result.fun) if result.fun is not None else None,
                s=monotonic()-started))
        if not feasible:
            return None
        out = {b: q for v, (b, q) in enumerate(self.pair) if x[v] > .5}
        if len(out) != nb or any(out.get(b) != q for b, q in fixed.items()):
            return None
        self.last_joint_solution = x[self.joint_aux_start:].copy()
        return out

    def column_from(self, x, j, used, free_tier):
        """Read a stack's next tiers off the LP marginals, bottom-up, keeping it legal."""
        T, model, page = self.T, self.m, self.m.pages[j]
        chosen = list(free_tier)
        for k in range(len(chosen), len(page.slots)):
            q = int(page.slots[k])
            options = sorted(((x[v], self.pair[v][0]) for v in self.by_slot.get(q, []) if self.pair[v][0] not in used and x[v] > .05), reverse=True)
            if not options or options[0][0] < .3:
                break
            for _, b in options:
                trial = chosen+[b]
                if model.legal(page, tuple(trial), any_port=True) and self.stack_ratio(page, trial) <= self.s.limit+.1:
                    chosen = trial
                    break
            else:
                break
        return chosen[len(free_tier):]

    def dive(self, *, deadline, tail_left=25, batch=.25, tail_s=15., retreats=6, seed=0, log=None):
        """Dive on stacks: fix what the LP is surest of, bottom-up, re-solve, finish with `tail`. {box: slot} or None."""
        T, model = self.T, self.m
        c = (self._built or self.build())[0]
        lower, upper = np.zeros(len(c)), np.full(len(c), np.inf)
        upper[:self.n] = 1.
        self.by_slot = defaultdict(list)
        for v, (_, q) in enumerate(self.pair):
            self.by_slot[q].append(v)
        fixed, history = dict(self.fixed_pairs), []
        self._fixed_bounds(lower, upper)
        size = batch
        x = None
        while (self.work['dive_steps'] < self.s.max_dive_steps and
               (self.s.work_bounded or monotonic() < deadline)):
            self.work['dive_steps'] += 1
            r = self.solve(lower, upper, time_limit=max(1., deadline-monotonic()))
            bad = r['x'] is None or r['uncovered'] > 1e-6
            if log is not None:
                log.append(dict(s=round(r['s'], 2), status=r['status'], fixed=len(fixed), uncovered=r['uncovered'], fun=r['fun']))
            if self.joint_spec is not None and r['status'] == 2 and not history:
                if log is not None:
                    log.append(dict(local_joint_lp_infeasible=True,
                                    reason='complete root relaxation of this bounded joint model',
                                    map_infeasibility_proven=False))
                return None
            if bad:
                if not history or retreats <= 0:
                    break
                retreats -= 1
                for v in history.pop():
                    lower[v] = 0.
                    fixed.pop(self.pair[v][0], None)
                size = max(.02, size/2)
            else:
                x = r['x'][:self.n]
            left = len(model.boxes)-len(fixed)
            if left <= tail_left:
                break
            by_page = defaultdict(dict)
            for b, q in fixed.items():
                j, k = self.slot_page[q][0]
                by_page[j][k] = b
            sure = []
            for j, page in enumerate(model.pages):
                have = [by_page[j][k] for k in sorted(by_page[j])] if j in by_page else []
                if len(have) >= len(page.slots) or int(page.slots[0]) not in self.slot_page:
                    continue
                q = int(page.slots[len(have)])
                top = max((x[v] for v in self.by_slot.get(q, [])), default=0.)
                if top >= .3:
                    sure.append((top, -len(have), j, have))
            sure.sort(key=lambda t: t[:3], reverse=True)
            goal = max(3, int(size*left))
            used, vs = set(fixed), []
            cells = {int(g) for q in fixed.values() for g in T.slot_cells[q] if g >= 0}
            for _, _, j, have in sure:
                if len(vs) >= goal:
                    break
                page = model.pages[j]
                boxes = self.column_from(x, j, used, have)
                slots = [int(q) for q in page.slots[len(have):len(have)+len(boxes)]]
                here = {int(g) for q in slots for g in T.slot_cells[q] if g >= 0}
                if not boxes or here & cells:
                    continue
                cells |= here
                for b, q in zip(boxes, slots):
                    v = next(v for v in self.by_slot[q] if self.pair[v][0] == b)
                    vs.append(v); lower[v] = 1.
                    fixed[b] = q
                    used.add(b)
            if not vs:
                break
            history.append(vs)
            size = min(batch, size*1.25)
        finished, tries = None, 0
        while finished is None and tries < self.s.max_tail_calls and (self.s.work_bounded or monotonic() < deadline+2*tail_s):
            # Reserve the last joint attempt for all free boxes. A bounded
            # greedy retreat must not consume every tail on the same bad dive;
            # true arrival prefixes remain fixed by the model and these bounds.
            tail_fixed = (dict(self.fixed_pairs) if self.joint_spec is not None and
                          tries+1 == self.s.max_tail_calls else fixed)
            finished = self.tail(tail_fixed, seconds=tail_s, seed=seed+tries, log=log, hint=x)
            tries += 1
            if finished is None:
                if not history:
                    break                       # identical full tail is not a new search neighbourhood
                for v in history.pop():
                    fixed.pop(self.pair[v][0], None)
        return finished

    def overages(self, assignment):
        """Exact ratio of each deck stack in {box: slot}, at the boxes' own heights."""
        by_page = defaultdict(dict)
        for b, q in assignment.items():
            j, k = self.slot_page[q][0]
            by_page[j][k] = b
        return {j: self.stack_ratio(self.m.pages[j], [t[k] for k in sorted(t)]) for j, t in by_page.items() if self.m.pages[j].deck}

    def exchange(self, assignment, *, deadline, windows=None, log=None):
        """Exact local search on {box: slot}: swap a box of an overloaded stack with a lighter one, or move it to a free top.

        Each trial reads the stack rows at true heights and keeps every column legal. Returns (assignment, excess).
        """
        T, model, limit = self.T, self.m, self.s.limit+.03
        if any(assignment.get(b) != q for b, q in self.fixed_pairs.items()):
            raise ValueError('exchange requires every fixed arrival pair')
        by_page = defaultdict(dict)
        for b, q in assignment.items():
            j, k = self.slot_page[q][0]
            by_page[j][k] = b
        col = {j: [t[k] for k in sorted(t)] for j, t in by_page.items()}

        def over(j):
            return max(0., self.stack_ratio(model.pages[j], col[j])-limit) if model.pages[j].deck else 0.

        def ok(j, boxes):
            page = model.pages[j]
            return (len(boxes) <= len(page.slots) and boxes == self.preserve_prefix(j, boxes)
                    and model.legal(page, tuple(boxes), any_port=True))

        def placed_order(j, boxes):
            ordered = self.preserve_prefix(j, sorted(boxes, key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b], -int(T.rank[b]), -model.pod[b], -float(T.mass[b]), b)))
            return ordered if ok(j, ordered) else (list(boxes) if ok(j, boxes) else None)

        def lever(j, k):
            return model._slot_xy(model.pages[j], k)
        moved = 0
        while (self.work['exchange_trials'] < self.s.max_exchange_trials and
               self.work['exchange_moves'] < self.s.max_exchange_moves and
               (self.s.work_bounded or monotonic() < deadline)):
            bad = sorted((j for j in col if over(j) > 1e-9), key=lambda j: -over(j))
            if not bad:
                return self._flat(col), 0.
            best = None
            for j in bad[:6]:
                now_j = over(j)
                for a in col[j]:
                    if a in self.fixed_pairs:
                        continue
                    rest = [b for b in col[j] if b != a]
                    # Move to a free top: a one-box-shorter stack is the strongest relief there is.
                    for i in col:
                        if i == j or model.pages[i].kind != model.pages[j].kind or len(col[i]) >= len(model.pages[i].slots):
                            continue
                        page = model.pages[i]
                        q = int(page.slots[len(col[i])])
                        if q not in self.slot_page or not model.static[a, q] or (set(int(g) for g in T.slot_cells[q] if g >= 0) & self._occupied(col)):
                            continue
                        if self.work['exchange_trials'] >= self.s.max_exchange_trials:
                            break
                        self.work['exchange_trials'] += 1
                        new_i, new_j = placed_order(i, col[i]+[a]), placed_order(j, rest) if rest else []
                        if new_i is None or new_j is None:
                            continue
                        gain = now_j+over(i)-self._over(i, new_i, limit)-self._over(j, new_j, limit)
                        if gain > 1e-6 and (best is None or gain > best[0]):
                            best = (gain, i, new_i, j, new_j)
                    lighter = sorted(((float(T.mass[b]), i, b) for i in col if i != j and model.pages[i].kind == model.pages[j].kind
                                      for b in col[i] if b not in self.fixed_pairs and float(T.mass[b]) < float(T.mass[a])), key=lambda t: t[:2])
                    step = max(1, len(lighter)//SWAP_TRIALS)
                    for _, i, b in lighter[::step]:
                        if self.work['exchange_trials'] >= self.s.max_exchange_trials:
                            break
                        self.work['exchange_trials'] += 1
                        if True:
                            new_j = placed_order(j, rest+[b])
                            if new_j is None:
                                continue
                            new_i = placed_order(i, [c for c in col[i] if c != b]+[a])
                            if new_i is None:
                                continue
                            gain = now_j+over(i)-self._over(i, new_i, limit)-self._over(j, new_j, limit)
                            gain -= .002*(model.pod[a] != model.pod[b])
                            if gain > 1e-6 and (best is None or gain > best[0]):
                                best = (gain, i, new_i, j, new_j)
                    if (not self.s.work_bounded and monotonic() >= deadline) or self.work['exchange_trials'] >= self.s.max_exchange_trials or (best is not None and best[0] > .02):
                        break
                if best is not None and best[0] > .02:
                    break
            if best is None:
                break
            _, i, new_i, j, new_j = best
            col[i], col[j] = new_i, new_j
            if not col[j]:
                del col[j]
            moved += 1
            self.work['exchange_moves'] += 1
        if log is not None:
            log.append(dict(exchange_moves=moved))
        return self._flat(col), sum(self._over(j, c, limit) for j, c in col.items())

    def _over(self, j, boxes, limit):
        page = self.m.pages[j]
        return max(0., self.stack_ratio(page, boxes)-limit) if page.deck and boxes else 0.

    def _occupied(self, col):
        T = self.T
        return {int(g) for j, boxes in col.items() for q in self.m.pages[j].slots[:len(boxes)] for g in T.slot_cells[int(q)] if g >= 0}

    def _flat(self, col):
        return {b: int(self.m.pages[j].slots[k]) for j, boxes in col.items() for k, b in enumerate(boxes)}

    def repair(self, assignment, *, deadline, rounds=30, seed=0, size=10, tail_s=6., log=None):
        """Large neighbourhood repair: free the stacks over the limit and some others, solve them exactly, keep any gain."""
        import random
        rng = random.Random(seed)
        T, model = self.T, self.m
        best = dict(assignment)

        def excess(table):
            return sum(max(0., r-self.s.limit-.03) for r in self.overages(table).values())
        score = excess(best)
        pages = {j: [q for q in page.slots] for j, page in enumerate(model.pages)}
        for turn in range(rounds):
            if score <= 1e-9 or (not self.s.work_bounded and monotonic() >= deadline):
                break
            ratios = self.overages(best)
            worst = sorted((j for j, r in ratios.items() if r > self.s.limit+.03), key=lambda j: -ratios[j])[:4]
            by_page = defaultdict(list)
            for b, q in best.items():
                by_page[self.slot_page[q][0][0]].append(b)
            others = [j for j in by_page if j not in worst]
            light = sorted((j for j in others if model.pages[j].deck), key=lambda j: ratios.get(j, 0.))[:size]
            holds = [j for j in others if not model.pages[j].deck]
            rng.shuffle(holds)
            rng.shuffle(light)
            chosen = set(worst+light[:size//2]+holds[:size//2])
            free = {b for j in chosen for b in by_page[j] if b not in self.fixed_pairs}
            fixed = {b: q for b, q in best.items() if b not in free}
            found = self.tail(fixed, seconds=min(tail_s, max(1., deadline-monotonic())), seed=seed+turn, log=log)
            if found is None:
                continue
            value = excess(found)
            if value < score-1e-9:
                best, score = found, value
        return best, score
