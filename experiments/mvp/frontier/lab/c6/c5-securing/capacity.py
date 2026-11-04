"""Aggregate securing capacity: is there room on deck for the mass the hold can't take? A relaxation of every stow.

Stack columns are grouped by (base, tiers, height n). x[c,t,n,k] counts incidences of box class c on tier k of stacks of
type t standing n high. Summing each stack's securing rows over a group gives rows a real stow must meet, so an LP with
no fractional box-to-slot detail still bounds every plan. See NOTES.md for the assumptions the proof uses.
"""
from collections import defaultdict
import itertools
import json
import math
from pathlib import Path
import sys
import warnings

import numpy as np
from scipy.optimize import linprog, OptimizeWarning
from scipy.sparse import coo_matrix

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stow import LAB
from rows import stack_rows, CG
from e9fix import install
from alns import Problem
from stowage.checker import lashing
from stowage.checker.visibility import _geometry
from stowage.model import Instance

ROOT = LAB.parents[3]
BUCKET = 500.
KEEP = ('tension', 'rod')


class Unit:
    """A motion with unit transverse acceleration, or none: rows then split into acceleration part and gravity part."""
    def __init__(self, at):
        self.a = at

    def at(self, x, y, z):
        return 0., self.a, 0.


def split_rows(tiers, heights):
    """C,D,Bt with row = a_k*C[r,k]*m_k + D[r,k]*m_k + Bt[r,k]*wind. Tension and rod rows only: other rows are dropped."""
    one, _, _, names = stack_rows(Unit(1.), 0., 0., 0., tiers, list(heights))
    zero, bt, _, _ = stack_rows(Unit(0.), 0., 0., 0., tiers, list(heights))
    use = [i for i, n in enumerate(names) if n[0] in KEEP]
    return (one-zero)[use], zero[use], bt[use], [names[i] for i in use]


class Coefficients:
    """Lower bounds on every row coefficient, over all height combinations and an acceleration interval."""
    cache = {}

    def __init__(self, tiers, values, own=None):
        key = (tiers, tuple(values))
        if key not in self.cache:
            combos = []
            for heights in itertools.product(values, repeat=tiers):
                c, d, b, names = split_rows(tiers, heights)
                offset = np.r_[0., np.cumsum(heights[:-1])]+CG*np.array(heights)
                combos.append((heights, c, d, b, offset))
            self.cache[key] = (combos, names)
        self.combos, self.names = self.cache[key]
        self.values, self.tiers = own or values, tiers

    def bound(self, base, low, high):
        """A[r,k,h], B[r,k,h] for own height h, sound for every a_k in [low(z), high(z)]."""
        shape = (len(self.names), self.tiers, len(self.values))
        A, B = np.full(shape, np.inf), np.full(shape, np.inf)
        for heights, c, d, b, offset in self.combos:
            lo = np.array([low.at(0., 0., base+z)[1] for z in offset])
            hi = np.array([high.at(0., 0., base+z)[1] for z in offset])
            value = np.minimum(c*lo, c*hi)+d
            for k, h in enumerate(heights):
                # An envelope height binds any class at that tier: it is the same box, measured taller.
                for i in ([self.values.index(h)] if h in self.values else range(len(self.values))):
                    A[:, k, i] = np.minimum(A[:, k, i], value[:, k])
                    B[:, k, i] = np.minimum(B[:, k, i], b[:, k])
        return A, B


def scantling_cb(hull):
    geometry, _ = _geometry(hull.hull_offsets_ref.split('#')[0].split('/')[-1].removesuffix('.toml'))
    volume = geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
    return volume/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)


def motion_pair(instance, gm_low, gm_high):
    """Least and most transverse acceleration on deck for GM in the interval and any legal mean draught.

    At deck height a rises with GM (roll angle and roll acceleration both rise) and falls with draught (the rotation
    centre rises), so the two ends are (low GM, deepest) and (high GM, draught near zero).
    """
    hull = instance.vessel.hull
    cb = scantling_cb(hull)
    deepest = min(hull.max_draught_m, hull.scantling_draught_m*2.)
    return lashing._motions(hull, deepest, gm_low, cb), lashing._motions(hull, 1e-3, gm_high, cb)


class Capacity:
    def __init__(self, instance, problem, strict=False):
        self.instance, self.p = instance, problem
        self.T = T = problem.engine.T
        self.boxes = [T.box_index[b] for b in instance.load_list]
        self.heights = sorted({round(float(problem.boxes[T.box_ids[b]].height_m), 3) for b in self.boxes})
        # Strict: lever arms also range over OOG envelope heights, which the checker uses for those boxes.
        self.levers = sorted(set(self.heights) | ({round(float(T.height[b]), 3) for b in self.boxes} if strict else set()))
        # A guard the proof needs: every cell in one long slot, both ends at one tier, so a chain never changes column.
        use = defaultdict(int)
        self.aligned = True
        for s in range(len(T.slot_ids)):
            if T.slot_n[s] == 2:
                self.aligned &= bool(T.slot_k[s, 0] == T.slot_k[s, 1])
                for c in T.slot_cells[s]:
                    if c >= 0:
                        use[int(c)] += 1
        self.aligned &= all(v == 1 for v in use.values())
        self.groups()

    def groups(self):
        T, p = self.T, self.p
        self.classes = defaultdict(list)
        for b in self.boxes:
            c = p.boxes[T.box_ids[b]]
            self.classes[(int(T.units[b]), int(c.length_ft), int(T.rank[b]), bool(T.e1_threshold is not None and T.mass[b] >= T.e1_threshold),
                          round(float(c.height_m), 3), int(float(T.mass[b])//BUCKET), c.oog is not None)].append(b)
        self.keys = sorted(self.classes)
        self.types = defaultdict(list)
        self.tier_of = {}
        for j in range(len(T.stack_ids)):
            cells = [int(c) for c in T.stack_cells[j] if c >= 0]
            kind = (bool(T.stack_deck[j]), round(float(T.stack_base_z[j]), 3), len(cells))
            self.types[kind].append(j)
        self.slots_of = defaultdict(list)
        for s in range(len(T.slot_ids)):
            for c in T.slot_cells[s]:
                if c >= 0:
                    self.slots_of[int(c)].append(s)

    def eligible(self, key, stacks, tier):
        """How many of these columns could take a box of this class at this tier."""
        T = self.T
        members = self.classes[key]
        count = 0
        for j in stacks:
            cells = [int(c) for c in T.stack_cells[j] if c >= 0]
            if tier < len(cells):
                want = [s for s in self.slots_of[cells[tier]] if (T.slot_n[s] == 1) == (key[0] == 1)]
                count += any(T.static[b, s] for b in members for s in want)
        return count

    def force(self, b):
        c = self.p.boxes[self.T.box_ids[b]]
        h, oog = float(c.height_m), c.oog
        el = lashing.LENGTH[c.length_ft]+(oog.over_length_fore_m+oog.over_length_aft_m if oog else 0.)
        mass = max(c.gross_mass_kg, 2500 if c.length_ft == 20 else 3500)
        wind = max(15700 if c.length_ft == 20 else 31600, el*h*1000.)
        dx = abs(oog.over_length_fore_m-oog.over_length_aft_m)/2 if oog else 0.
        return mass, wind, dx/(12.192 if c.length_ft == 45 else lashing.LENGTH[c.length_ft])

    def class_row(self, key, a, b):
        """Least value over the class of A*mass + B*wind for one row, tier and own-height index."""
        out = math.inf
        for box in self.classes[key]:
            mass, wind, skew = self.force(box)
            v = a*mass+b*wind
            out = min(out, v*(1-2*skew) if v > 0 else v*(1+2*skew))
        return out

    def build(self, gm_low, gm_high, limit=1.):
        T = self.T
        low, high = motion_pair(self.instance, gm_low, gm_high)
        V, c = {}, []

        def var(name, cost=0.):
            V[name] = len(c)
            c.append(cost)
            return V[name]
        eq_i, eq_j, eq_v, eq_b = [], [], [], []
        ub_i, ub_j, ub_v, ub_b = [], [], [], []

        def eq(pairs, rhs):
            for j, v in pairs:
                eq_i.append(len(eq_b)); eq_j.append(j); eq_v.append(v)
            eq_b.append(rhs)

        def ub(pairs, rhs):
            for j, v in pairs:
                ub_i.append(len(ub_b)); ub_j.append(j); ub_v.append(v)
            ub_b.append(rhs)
        cover = defaultdict(list)
        ranks = sorted({int(T.rank[b]) for b in self.boxes})
        share = {k: min(float(T.share[b]) for b in self.classes[k]) for k in self.keys}
        rate = lambda stacks: sum(max(float(T.stack_lim_20[j]), float(T.stack_lim_40[j]), float(T.stack_lim_mixed[j])) for j in stacks)
        for kind, stacks in sorted(self.types.items()):
            deck, base, tiers = kind
            count = len(stacks)
            if deck:
                A, B = Coefficients(tiers, self.levers, self.heights).bound(base, low, high)
            # Hold columns stay one group at their tier count: nothing there asks for a height split.
            for n in (range(1, tiers+1) if deck else (tiers,)):
                u = var((kind, n, 'columns'))
                tier_vars = []
                for k in range(n):
                    row = []
                    for key in self.keys:
                        e = self.eligible(key, stacks, k)
                        if e:
                            v = var((kind, n, k, key))
                            row.append((v, key))
                            cover[key].append(v)
                    tier_vars.append(row)
                    if deck:
                        eq([(v, 1.) for v, _ in row]+[(u, -1.)], 0.)
                    else:
                        ub([(v, 1.) for v, _ in row]+[(u, -1.)], 0.)
                for k in range(n-1):
                    if not deck:
                        ub([(v, 1.) for v, _ in tier_vars[k+1]]+[(v, -1.) for v, _ in tier_vars[k]], 0.)
                    for r in ranks[:-1]:
                        ub([(v, 1.) for v, q in tier_vars[k+1] if q[2] > r+T.e2_tol]+[(v, -1.) for v, q in tier_vars[k] if q[2] > r], 0.)
                    if T.e1_threshold is not None:
                        ub([(v, 1.) for v, q in tier_vars[k+1] if q[3]]+[(v, -1.) for v, q in tier_vars[k] if q[3]], 0.)
                everything = [(v, q) for row in tier_vars for v, q in row]
                ub([(v, q[4]) for v, q in everything]+[(u, -max(float(T.stack_hmax[j]) for j in stacks))], 0.)
                ub([(v, share[q]) for v, q in everything]+[(u, -rate(stacks)/count)], 0.)
                if deck and n >= 2:
                    for r in range(A.shape[0]):
                        ub([(v, self.class_row(q, A[r, k, self.heights.index(q[4])], B[r, k, self.heights.index(q[4])]))
                            for k, row in enumerate(tier_vars) for v, q in row]+[(u, -limit)], 0.)
            ub([(V[(kind, n, 'columns')], 1.) for n in (range(1, tiers+1) if deck else (tiers,))], float(count))
            for k in range(tiers):
                for key in self.keys:
                    e = self.eligible(key, stacks, k)
                    here = [V[(kind, n, k, key)] for n in (range(1, tiers+1) if deck else (tiers,)) if (kind, n, k, key) in V]
                    if here:
                        ub([(v, 1.) for v in here], float(e))
        short = {}
        for key in self.keys:
            need = float(len(self.classes[key])*key[0])
            short[key] = var(('short', key), 1./key[0])
            eq([(v, 1.) for v in cover[key]]+[(short[key], 1.)], need)
        n = len(c)
        self.V = V
        return (np.array(c), coo_matrix((ub_v, (ub_i, ub_j)), shape=(len(ub_b), n)).tocsr(), np.array(ub_b),
                coo_matrix((eq_v, (eq_i, eq_j)), shape=(len(eq_b), n)).tocsr(), np.array(eq_b))

    def solve(self, gm_low, gm_high, limit=1.):
        c, G, h, A, b = self.build(gm_low, gm_high, limit)
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=OptimizeWarning)
            res = linprog(c, A_ub=G, b_ub=h, A_eq=A, b_eq=b, bounds=(0, None), method='highs', options=dict(threads=1, time_limit=120.))
        out = dict(gm=(gm_low, gm_high), status=int(res.status), uncovered=None if res.x is None else float(res.fun), s=None)
        if res.x is None:
            return out
        # Safe dual bound: any multipliers give a lower bound once reduced costs are charged at variable upper limits.
        y, lam = res.eqlin.marginals, -res.ineqlin.marginals
        lam = np.maximum(lam, 0.)
        reduced = c-A.T@y+G.T@lam
        limit_x = float(sum(len(v)*k[0] for k, v in self.classes.items()))+len(self.T.stack_ids)
        out['safe_lower_bound'] = float(y@b-lam@h+np.minimum(reduced, 0.).sum()*limit_x)
        out['x'] = res.x
        return out

    def profile(self, solved):
        """Target tonnes per tier and columns per height from an aggregate solution, for steering the dive."""
        tiers, heights = defaultdict(float), defaultdict(float)
        for name, v in self.V.items():
            value = float(solved['x'][v])
            if name[0] == 'short' or value < 1e-9:
                continue
            kind = name[0]
            if name[2] == 'columns':
                if kind[0]:
                    heights[name[1]] += value
            elif kind[0]:
                key = name[3]
                tiers[name[2]] += value*key[5]*BUCKET/1000./(2 if key[0] == 2 else 1)
        return dict(tonnes_by_tier={k: round(tiers[k], 1) for k in sorted(tiers)}, columns_by_height={k: round(heights[k], 2) for k in sorted(heights)})


def intervals(hull):
    cap = float(hull.max_gm_lashing_m)
    edges = [float(hull.min_gm_m)]+[e for e in (.5, 1., 1.5, 2., 2.5, 3., 4., 5.) if float(hull.min_gm_m) < e < cap]+[cap]
    return list(zip(edges, edges[1:]))


def stack_witness(cap, gm, plan_assignment):
    """Control: the per-stack rows behind the aggregate must hold on a real passing stow. Returns the worst row value."""
    T = cap.T
    low, high = motion_pair(cap.instance, *gm)
    where = defaultdict(dict)
    for box, slot in plan_assignment.items():
        s = T.slot_index[slot]
        for c in T.slot_cells[s]:
            if c >= 0:
                where[int(T.cell_stack[c])][int(T.cell_k[c])] = T.box_index[box]
    worst = -math.inf
    for kind, stacks in cap.types.items():
        deck, base, tiers = kind
        if not deck:
            continue
        A, B = Coefficients(tiers, cap.levers, cap.heights).bound(base, low, high)
        for j in stacks:
            col = where.get(j, {})
            if len(col) < 2:
                continue
            rows = np.zeros(A.shape[0])
            for k, b in col.items():
                c = cap.p.boxes[T.box_ids[b]]
                mass, wind, skew = cap.force(b)
                i = cap.heights.index(round(float(c.height_m), 3))
                v = A[:, k, i]*mass+B[:, k, i]*wind
                rows += np.where(v > 0, v*(1-2*skew), v*(1+2*skew))
            worst = max(worst, float(rows.max()))
    return worst


def main():
    install()
    out = []
    lists = dict(failed=HERE/'failed20.txt')
    for tag, path in lists.items():
        for line in open(path):
            line = line.strip()
            instance = Instance.model_validate_json((ROOT/line).read_text())
            cap = Capacity(instance, Problem(instance), strict=True)
            row = dict(tag=tag, instance=instance.id, boxes=len(cap.boxes), classes=len(cap.keys), aligned=cap.aligned, by_gm=[])
            for gm in intervals(instance.vessel.hull):
                solved = cap.solve(*gm)
                x = solved.pop('x', None)
                if x is not None:
                    solved['x'] = x
                    solved.update(cap.profile(solved))
                    del solved['x']
                row['by_gm'].append(solved)
            row['min_uncovered'] = min((q['uncovered'] for q in row['by_gm'] if q['uncovered'] is not None), default=None)
            row['min_safe_bound'] = min((q.get('safe_lower_bound', -1) for q in row['by_gm']), default=None)
            out.append(row)
            print(tag, instance.id[-32:], row['boxes'], row['classes'], row['aligned'], [(round(q['gm'][1], 2), None if q['uncovered'] is None else round(q['uncovered'], 2)) for q in row['by_gm']], flush=True)
            (HERE/'capacity.json').write_text(json.dumps(out, indent=1, default=str)+'\n')


if __name__ == '__main__':
    main()
