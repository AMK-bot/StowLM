"""Boxes as jobs, stacks as machines: the box-to-slot assignment LP with securing rows at fixed motion. See NOTES.md."""
# Copied from lab/c5/c5-securing/gap.py, then changed for cycle 6.
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
    heavy_deck_weight: float = 3.      # E6: a heavy box on deck while the hold has room
    noise: float = .02                 # cost noise, so a new round reads a different LP vertex
    high_tier_rank: tuple = ()         # {rank: top deck tier allowed}, to shrink the LP on the largest maps
    fixed: dict = field(default_factory=dict)  # {box: slot} already decided by an earlier rolling step
    overage_weight: float = 400.       # lifts per unit of securing ratio over the limit, per stack


class Assignment:
    """x[b,s]=1 puts box b in slot s. Pages come from an existing page Model, so slots and eligibility are the real ones."""

    def __init__(self, model, motions, settings=None, layout=None):
        self.m, self.T, self.motions = model, model.T, motions
        self.s = settings or Settings()
        self.layout = layout or {}
        T = self.T
        self.box_pos = {b: i for i, b in enumerate(model.boxes)}
        self.slot_page = defaultdict(list)
        for j, page in enumerate(model.pages):
            cap = self.s.deck_tier_cap.get(j, len(page.slots)) if page.deck else len(page.slots)
            for k, s in enumerate(page.slots[:cap]):
                self.slot_page[int(s)].append((j, k))
        self.pair = []
        for b in model.boxes:
            long_box = T.units[b] != 1
            for s in np.flatnonzero(model.static[b]):
                s = int(s)
                if s in self.slot_page and (model.pages[self.slot_page[s][0][0]].kind == 'long') == long_box:
                    self.pair.append((b, s))
        if self.s.fixed:
            taken = {int(g) for q in self.s.fixed.values() for g in T.slot_cells[q] if g >= 0}
            self.pair = [(b, q) for b, q in self.pair
                         if (self.s.fixed[b] == q if b in self.s.fixed else not ({int(g) for g in T.slot_cells[q] if g >= 0} & taken))]
        if self.s.pairs_per_box:
            # Slots repeat by mirror and bay, so a seeded sample per box keeps the LP's shape at a fraction of its size.
            import random
            rng = random.Random(self.s.seed)
            by_box = defaultdict(list)
            for pair in self.pair:
                by_box[pair[0]].append(pair)
            self.pair = [q for b in model.boxes for q in (by_box[b] if len(by_box[b]) <= self.s.pairs_per_box
                                                          else rng.sample(by_box[b], self.s.pairs_per_box))]
        if self.s.high_tier_rank:
            rule = dict(self.s.high_tier_rank)
            self.pair = [(b, q) for b, q in self.pair if not model.pages[self.slot_page[q][0][0]].deck
                         or self.slot_page[q][0][1] <= rule.get(int(T.rank[b]), 9)]
        self.n = len(self.pair)
        self._built = None
        self.tier_height = float(np.mean([float(model.p.boxes[T.box_ids[b]].height_m) for b in model.boxes]))
        self.high = {b: float(model.p.boxes[T.box_ids[b]].height_m) > 2.7 for b in model.boxes}
        self.closed = set()
        self.row_cache = {}

    def page_rows(self, page, height=None):
        x, y = self.m._slot_xy(page, 0)
        base = max(float(self.T.stack_base_z[j]) for j in page.stacks)
        return stack_rows(self.motions, x, y, base, len(page.slots), [height or self.tier_height]*len(page.slots))

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

    def cost(self):
        T, model, s = self.T, self.m, self.s
        out = np.zeros(self.n)
        for v, (b, slot) in enumerate(self.pair):
            j, k = self.slot_page[slot][0]
            page = model.pages[j]
            z = float(T.stack_base_z[page.stacks[0]])+(k+.5)*2.6
            out[v] = s.vertical_weight*float(T.mass[b])/1000.*z
            if page.deck and T.e6_threshold is not None and T.mass[b] >= T.e6_threshold:
                out[v] += s.heavy_deck_weight
            port = self.layout.get(page.cover)
            if port is not None:
                if not page.deck and model.pod[b] != port:
                    out[v] += s.cover_port_weight
                if page.deck and model.pod[b] > port:
                    out[v] += s.deck_port_weight
        if s.noise:
            out += s.noise*np.random.default_rng(s.seed).random(self.n)
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
            high = [float(T.stack_base_z[model.pages[self.slot_page[q][0][0]].stacks[0]])+(self.slot_page[q][0][1]+.5)*2.6 for _, q in self.pair]
            total = float(sum(T.mass[b] for b in model.boxes))/1000.
        if s.lcg is not None:
            out.append(('lcg_max', [(v, mass[v]*place[v][0]) for v in range(self.n)], s.lcg[1]*total))
            out.append(('lcg_min', [(v, -mass[v]*place[v][0]) for v in range(self.n)], -s.lcg[0]*total))
        if s.tcg is not None:
            out.append(('tcg_max', [(v, mass[v]*place[v][1]) for v in range(self.n)], s.tcg*total))
            out.append(('tcg_min', [(v, -mass[v]*place[v][1]) for v in range(self.n)], s.tcg*total))
        if s.vertical_max_tm is not None:
            out.append(('vertical', [(v, mass[v]*high[v]) for v in range(self.n)], s.vertical_max_tm))
        return out

    def solve(self, lower=None, upper=None, time_limit=60.):
        c, Aub, bub, Aeq, beq = self._built or self.build()
        bounds = np.c_[np.zeros(len(c)) if lower is None else lower, np.full(len(c), np.inf) if upper is None else upper]
        started = monotonic()
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=OptimizeWarning)
            res = linprog(c, A_ub=Aub, b_ub=bub, A_eq=Aeq, b_eq=beq, bounds=bounds, method='highs',
                          options=dict(threads=1, time_limit=time_limit))
        return dict(status=int(res.status), s=monotonic()-started, fun=res.fun, x=res.x,
                    reduced=None if res.x is None else res.lower.marginals,
                    uncovered=float(res.x[self.n:self.n+len(self.m.boxes)].sum()) if res.x is not None else None)

    def tail(self, fixed, *, seconds=30., seed=0, log=None, hint=None):
        """Finish a partial assignment exactly: an integer program over every eligible pair of the unfixed boxes.

        Rows are scaled to integers rounded the safe way, so an integer point here satisfies the LP's rows.
        Slack variables count in thousandths. `fixed` is {box: slot}. Returns {box: slot} for all boxes, or None.
        """
        from ortools.sat.python import cp_model
        c, Aub, bub, Aeq, beq = self._built
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
        slack, unit = {}, {}
        rows = Aub.tocsr()
        used = rows@solid
        live = np.zeros(Aub.shape[1])
        live[free] = 1.
        touched = np.asarray(abs(rows)@live).ravel() > 0
        for i in np.flatnonzero(touched):
            cols, values = rows.indices[rows.indptr[i]:rows.indptr[i+1]], rows.data[rows.indptr[i]:rows.indptr[i+1]]
            terms = [(int(j), float(a)) for j, a in zip(cols, values) if int(j) in var]
            soft = [(int(j), float(a)) for j, a in zip(cols, values) if j >= n+nb]
            room = bub[i]-used[i]
            if sum(max(a, 0.) for _, a in terms) <= room:
                continue                       # cannot bind, whatever is chosen
            scale = 20000./max(abs(a) for _, a in terms)
            expression = sum(int(np.ceil(a*scale-1e-9))*var[j] for j, a in terms)
            for j, a in soft:
                if j not in slack:
                    slack[j] = model.NewIntVar(0, 10000000, f'z{j}')
                    unit[j] = 1e-3
                coefficient = a*scale*unit[j]
                if abs(coefficient) < 1.:       # a tonne-metre row: count this slack in row units, or it would round to 0
                    unit[j] = 1./(abs(a)*scale)
                    coefficient = float(np.sign(a))
                expression += int(np.ceil(coefficient-1e-9))*slack[j]
            model.Add(expression <= int(np.floor(room*scale+1e-9)))
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
        model.Minimize(sum(int(round(100*c[v]))*var[v] for v in var)+sum(max(1, int(round(100*unit[j]*c[j])))*slack[j] for j in slack))
        if hint is not None:
            for v in free:
                model.AddHint(var[v], int(hint[v] >= .5))
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.max_time_in_seconds = max(1., seconds)
        solver.parameters.random_seed = seed
        status = solver.Solve(model)
        if log is not None:
            log.append(dict(tail=solver.StatusName(status), left=nb-len(fixed), vars=len(var), s=round(solver.WallTime(), 2)))
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        out = dict(fixed)
        out.update({self.pair[v][0]: self.pair[v][1] for v in free if solver.Value(var[v])})
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

    def dive(self, *, deadline, tail_left=25, batch=.25, tail_s=15., retreats=6, seed=0, log=None, steps=0):
        """Dive on stacks: fix what the LP is surest of, bottom-up, re-solve, finish with `tail`. {box: slot} or None."""
        T, model = self.T, self.m
        c = (self._built or self.build())[0]
        lower, upper = np.zeros(len(c)), np.full(len(c), np.inf)
        upper[:self.n] = 1.
        self.by_slot = defaultdict(list)
        for v, (_, q) in enumerate(self.pair):
            self.by_slot[q].append(v)
        fixed, history = dict(self.s.fixed), []
        for v, (b, q) in enumerate(self.pair):
            if fixed.get(b) == q:
                lower[v] = 1.
        size = batch
        x = None
        done = 0
        while monotonic() < deadline:
            if steps and done >= steps:
                self.partial = fixed
                return None
            done += 1
            r = self.solve(lower, upper, time_limit=max(1., deadline-monotonic()))
            bad = r['x'] is None or r['uncovered'] > 1e-6
            if log is not None:
                log.append(dict(s=round(r['s'], 2), status=r['status'], fixed=len(fixed), uncovered=r['uncovered'], fun=r['fun']))
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
        if steps:
            self.partial = fixed
            return None
        finished, tries = None, 0
        while finished is None and tries < 6 and monotonic() < deadline+2*tail_s:
            finished = self.tail(fixed, seconds=tail_s, seed=seed+tries, log=log, hint=x)
            tries += 1
            if finished is None and history:
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
        by_page = defaultdict(dict)
        for b, q in assignment.items():
            j, k = self.slot_page[q][0]
            by_page[j][k] = b
        col = {j: [t[k] for k in sorted(t)] for j, t in by_page.items()}

        def over(j):
            return max(0., self.stack_ratio(model.pages[j], col[j])-limit) if model.pages[j].deck else 0.

        def ok(j, boxes):
            page = model.pages[j]
            return len(boxes) <= len(page.slots) and model.legal(page, tuple(boxes), any_port=True)

        def placed_order(j, boxes):
            ordered = sorted(boxes, key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b], -int(T.rank[b]), -model.pod[b], -float(T.mass[b]), b))
            return ordered if ok(j, ordered) else (list(boxes) if ok(j, boxes) else None)

        # E9 looks at neighbours: an OOG box and the cells its envelope reaches stay as they are.
        locked = {b for b in model.boxes if model.p.boxes[T.box_ids[b]].oog is not None}
        reach = {c for v, cells in self.oog_conflicts() if self.pair[v][0] in locked and assignment.get(self.pair[v][0]) == self.pair[v][1] for c in cells}
        moved = 0
        stall = 0
        while monotonic() < deadline:
            bad = sorted((j for j in col if over(j) > 1e-9), key=lambda j: -over(j))
            if not bad:
                return self._flat(col), 0.
            if stall == 0:
                changed = self.sweep(col, limit, locked, deadline)
                moved += changed
                bad = sorted((j for j in col if over(j) > 1e-9), key=lambda j: -over(j))
                if not bad:
                    return self._flat(col), 0.
            best = None
            for j in bad[:6]:
                now_j = over(j)
                for a in col[j]:
                    if a in locked:
                        continue
                    rest = [b for b in col[j] if b != a]
                    # Move to a free top: a one-box-shorter stack is the strongest relief there is.
                    for i in col:
                        if i == j or model.pages[i].kind != model.pages[j].kind or len(col[i]) >= len(model.pages[i].slots):
                            continue
                        page = model.pages[i]
                        q = int(page.slots[len(col[i])])
                        here = set(int(g) for g in T.slot_cells[q] if g >= 0)
                        if q not in self.slot_page or not model.static[a, q] or (here & self._occupied(col)) or (here & reach):
                            continue
                        new_i, new_j = placed_order(i, col[i]+[a]), placed_order(j, rest) if rest else []
                        if new_i is None or new_j is None:
                            continue
                        gain = now_j+over(i)-self._over(i, new_i, limit)-self._over(j, new_j, limit)
                        if gain > 1e-6 and (best is None or gain > best[0]):
                            best = (gain, i, new_i, j, new_j)
                    lighter = sorted(((float(T.mass[b]), i, b) for i in col if i != j and model.pages[i].kind == model.pages[j].kind
                                      for b in col[i] if float(T.mass[b]) < float(T.mass[a]) and b not in locked), key=lambda t: t[:2])
                    step = max(1, len(lighter)//SWAP_TRIALS)
                    for _, i, b in lighter[::step]:
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
                    if monotonic() >= deadline or (best is not None and best[0] > .02):
                        break
                if best is not None and best[0] > .02:
                    break
            if best is None:
                if stall:
                    break
                stall = 1
                continue
            stall = 0
            _, i, new_i, j, new_j = best
            col[i], col[j] = new_i, new_j
            if not col[j]:
                del col[j]
            moved += 1
        if log is not None:
            log.append(dict(exchange_moves=moved))
        return self._flat(col), sum(self._over(j, c, limit) for j, c in col.items())

    def sweep(self, col, limit, locked, deadline):
        """Very large neighbourhood: reassign a whole tier, or deck tops with the hold, by one assignment problem.

        Rows of the stacks not touched stay as they are. Each box gets a cost for each position, the excess that stack
        would then have, and `linear_sum_assignment` picks the best permutation of hundreds. See NOTES.md.
        """
        from scipy.optimize import linear_sum_assignment
        T, model = self.T, self.m
        pos_groups = []
        for kind in ('20', 'long'):
            pages = [j for j in col if model.pages[j].kind == kind]
            deck = [j for j in pages if model.pages[j].deck]
            hold = [j for j in pages if not model.pages[j].deck]
            for k in range(5, -1, -1):
                pos_groups.append([(j, k) for j in deck if k < len(col[j])])
            tops = [(j, len(col[j])-1) for j in deck]
            pos_groups.append(tops+[(j, len(col[j])-1) for j in hold])
            pos_groups.append([(j, k) for j in deck for k in range(2, len(col[j]))]+[(j, k) for j in hold for k in range(len(col[j]))])
        changed = 0
        for group in pos_groups:
            if monotonic() >= deadline or len(group) < 2 or len(group) > 140:
                continue
            group = [(j, k) for j, k in group if col[j][k] not in locked]
            boxes = [col[j][k] for j, k in group]
            base = {j: self._over(j, col[j], limit) for j, _ in group}
            cost = np.full((len(group), len(group)), 1e6)
            for p, (j, k) in enumerate(group):
                page = model.pages[j]
                q = int(page.slots[k])
                for r, b in enumerate(boxes):
                    if not model.static[b, q]:
                        continue
                    trial = list(col[j])
                    trial[k] = b
                    # Two positions of one stack can trade, so order is judged when the permutation is applied.
                    cost[p, r] = self._over(j, trial, limit)+1e-4*(b != col[j][k])
            rows, cols = linear_sum_assignment(cost)
            if cost[rows, cols].max() >= 1e6:
                continue
            new = {j: list(c) for j, c in ((j, col[j]) for j in {g[0] for g in group})}
            for p, r in zip(rows, cols):
                j, k = group[p]
                new[j][k] = boxes[r]
            if any(not model.legal(model.pages[j], tuple(c), any_port=True) for j, c in new.items()):
                # A single stack out of order: try its heavy-low order before giving up.
                fixed = {}
                for j, c in new.items():
                    if model.legal(model.pages[j], tuple(c), any_port=True):
                        fixed[j] = c
                        continue
                    order = sorted(c, key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b], -int(T.rank[b]), -model.pod[b], -float(T.mass[b]), b))
                    if not model.legal(model.pages[j], tuple(order), any_port=True):
                        fixed = None
                        break
                    fixed[j] = order
                if fixed is None:
                    continue
                new = fixed
            before = sum(base.values())
            after = sum(self._over(j, c, limit) for j, c in new.items())
            if after < before-1e-6:
                col.update(new)
                changed += 1
        return changed

    def fill_hold(self, assignment, instance, log=None):
        """E6: move a heavy deck box into a hold slot the rule says is free, stack by stack, keeping columns legal."""
        from stowage.checker.rules_weight import e6_departure_violations
        from stowage.model.ship import Placement
        import re
        T, model = self.T, self.m
        table = dict(assignment)
        for _ in range(40):
            ship = instance.onboard.model_copy(update={'placements': [Placement(container_id=T.box_ids[b], slot=T.slot_ids[q]) for b, q in table.items()]})
            found = [v for v in e6_departure_violations(instance, ship, call_index=instance.call_index) if 'opportunity' in v.message]
            if not found:
                return table, 0
            moved = False
            for v in found:
                b = T.box_index[v.containers[0]]
                goal = re.search(r'at departure: (\S+?)\.$', v.message)
                if goal is None:
                    continue
                q = T.slot_index[goal.group(1)]
                if q not in self.slot_page:
                    continue
                j_from = self.slot_page[table[b]][0][0]
                column = sorted((self.slot_page[s][0][1], c) for c, s in table.items() if self.slot_page[s][0][0] == j_from)
                rest = [c for _, c in column if c != b]
                j_to, k_to = self.slot_page[q][0]
                have = [c for c, s in table.items() if self.slot_page[s][0][0] == j_to]
                if len(have) != k_to or not model.legal(model.pages[j_to], tuple(sorted(have, key=lambda c: self.slot_page[table[c]][0][1]))+(b,), any_port=True):
                    continue
                page = model.pages[j_from]
                if rest and not model.legal(page, tuple(rest), any_port=True):
                    continue
                table[b] = q
                for k, c in enumerate(rest):
                    table[c] = int(page.slots[k])
                moved = True
                break
            if not moved:
                return table, len(found)
        return table, -1

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
            if score <= 1e-9 or monotonic() >= deadline:
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
            free = {b for j in chosen for b in by_page[j]}
            fixed = {b: q for b, q in best.items() if b not in free}
            found = self.tail(fixed, seconds=min(tail_s, max(1., deadline-monotonic())), seed=seed+turn, log=log)
            if found is None:
                continue
            value = excess(found)
            if value < score-1e-9:
                best, score = found, value
        return best, score
