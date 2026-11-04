"""Moment-aware physical page master; copied C3 pure-page restrictions.

Moment rows are construction targets, never certificates for righting-area
feasibility. The unchanged checker and voyage phases remain the acceptance gate.
"""
from collections import defaultdict
from math import fsum, isfinite
import warnings

import numpy as np
from scipy.optimize import linprog, OptimizeWarning
from scipy.sparse import csc_matrix, eye, hstack, vstack

from frozen_securing.pages_base import Model as BaseModel, Page, Pattern, UNCOVERED


class Model(BaseModel):
    def __init__(self, problem, *, closed_hold=(), ordinary_heavy_hold=True,
                 vertical_weight=.02, cargo_vmoment_max=None, lcg_min=None,
                 lcg_max=None, tcg_min=None, tcg_max=None, moment_penalty=10.,
                 moment_geometry='stability',
                 **kwargs):
        self.closed_hold = frozenset(closed_hold)
        self.ordinary_heavy_hold = ordinary_heavy_hold
        self.vertical_weight = float(vertical_weight)
        self.cargo_vmoment_max = cargo_vmoment_max
        self.lcg_min = lcg_min
        self.lcg_max = lcg_max if lcg_max is not None else kwargs.get('lcg_target')
        self.tcg_min, self.tcg_max = tcg_min, tcg_max
        self.moment_penalty = float(moment_penalty)
        if moment_geometry not in ('stability', 'envelope'):
            raise ValueError('moment_geometry must be stability or envelope')
        self.moment_geometry = moment_geometry
        for value in (vertical_weight, moment_penalty):
            if not isfinite(value) or value < 0:
                raise ValueError('moment weights must be finite and nonnegative')
        for value in (cargo_vmoment_max, lcg_min, self.lcg_max, tcg_min, tcg_max):
            if value is not None and not isfinite(value):
                raise ValueError('moment targets must be finite')
        kwargs['lcg_target'] = self.lcg_max
        super().__init__(problem, **kwargs)
        self.fixed_columns = []
        self.moment_duals = {}
        self.moment_slacks = {}
        self.geometry_cache = {}
        # A dual-priced pattern is copied only to geometrically identical pages.
        # C3's shape-only cache could copy a low-page price onto a higher page.
        refined = defaultdict(list)
        for key, indices in self.types.items():
            for j in indices:
                page = self.pages[j]
                geometry = tuple(self._slot_xy(page, k) for k in range(len(page.slots)))
                base = max(float(self.T.stack_base_z[s]) for s in page.stacks)
                refined[key + (geometry, base)].append(j)
        self.types = refined
        self.type_of = {j: key for key, indices in refined.items() for j in indices}
        self.pricing_cache.clear()

    def _static(self):
        out = super()._static().copy()
        T = self.T
        if self.closed_hold:
            forbidden = [s for s in range(len(T.slot_ids))
                         if any(not T.stack_deck[int(j)] and T.stack_cover[int(j)] in self.closed_hold
                                for j in T.slot_stacks[s] if j >= 0)]
            out[:, forbidden] = False
            if self.ordinary_heavy_hold and T.e6_threshold is not None:
                # Called before BaseModel creates self.boxes.
                for name in self.p.instance.load_list:
                    b = T.box_index[name]
                    box = self.p.boxes[name]
                    if (T.mass[b] >= T.e6_threshold and not box.dg and not box.oog
                            and not box.special_stowage):
                        out[b, T.slot_deck] = False
        return out

    def port_of(self, page):
        port = self.layout.get(page.cover)
        if page.deck:
            if page.cover in self.closed_hold:
                return None
            if self.layout_deck == 'no_restow':
                return port if port is not None and port < max(self.ports) else None
            if not self.layout_deck:
                return None
        return port

    def hold_gain(self, box):
        gain = super().hold_gain(box)
        if self.closed_hold and self.T.e6_threshold is not None and self.T.mass[box] >= self.T.e6_threshold:
            gain += 100.
        return gain

    def _slot_xy(self, page, tier):
        T = self.T
        s = page.slots[tier]
        cells = [self.p.cells[T.cell_ids[int(c)]] for c in T.slot_cells[s] if c >= 0]
        return fsum(c.lcg_m for c in cells)/len(cells), fsum(c.tcg_m for c in cells)/len(cells)

    def _box_offsets(self, b):
        if self.moment_geometry == 'stability':
            return 0., 0.
        oog = self.p.boxes[self.T.box_ids[b]].oog
        if not oog:
            return 0., 0.
        return ((oog.over_length_fore_m-oog.over_length_aft_m)/2,
                (oog.over_width_port_m-oog.over_width_starboard_m)/2)

    def _physical_height(self, b):
        return (float(self.p.boxes[self.T.box_ids[b]].height_m)
                if self.moment_geometry == 'stability' else float(self.T.height[b]))

    def column_moments(self, column):
        """Cargo mass moments, in tonnes and tonne-metres.

        Default matches stability_loading's nominal body half-height and real
        support heights. Optional envelope geometry includes declared OOG CG
        offsets, as used by visibility and its native securing loading model.
        """
        key = column.page, column.boxes
        if key not in self.geometry_cache:
            page, T = self.pages[column.page], self.T
            z = max(float(T.stack_base_z[j]) for j in page.stacks)
            total = np.zeros(4)
            for k, b in enumerate(column.boxes):
                mass = float(T.mass[b])/1000.
                height = self._physical_height(b)
                x, y = self._slot_xy(page, k)
                dx, dy = self._box_offsets(b)
                total += mass*np.array((1., z+height/2, x+dx, y+dy))
                z += height
            self.geometry_cache[key] = tuple(float(v) for v in total)
        return self.geometry_cache[key]

    def selection_moments(self, chosen):
        total = np.sum([self.column_moments(self.columns[k]) for k in chosen], axis=0) if chosen else np.zeros(4)
        mass, vertical, longitudinal, transverse = (float(v) for v in total)
        return dict(mass_t=mass, vertical_tm=vertical, longitudinal_tm=longitudinal,
                    transverse_tm=transverse, vcg_m=vertical/mass if mass else 0.,
                    lcg_m=longitudinal/mass if mass else 0., tcg_m=transverse/mass if mass else 0.)

    def _target_specs(self):
        # name, component, sign, mass-coefficient, RHS.
        specs = []
        if self.cargo_vmoment_max is not None:
            specs.append(('vertical_max', 1, 1., 0., self.cargo_vmoment_max))
        for name, component, lower, upper in (('lcg', 2, self.lcg_min, self.lcg_max),
                                              ('tcg', 3, self.tcg_min, self.tcg_max)):
            if upper is not None:
                specs.append((name+'_max', component, 1., -upper, 0.))
            if lower is not None:
                specs.append((name+'_min', component, -1., lower, 0.))
        return specs

    def moment_rows(self, *, fixed_columns=(), columns=None):
        """List (name, {column_index: coefficient}, rhs), after fixed charges.

        Pass only VARIABLE indices as columns; fixed_columns are deducted from
        RHS and omitted from coefficients. Units of every row are tonne-metres.
        """
        fixed_columns = set(fixed_columns)
        use = [k for k in (range(len(self.columns)) if columns is None else columns)
               if k not in fixed_columns]
        out = []
        for name, component, sign, mass_coefficient, rhs in self._target_specs():
            def coefficient(k):
                g = self.column_moments(self.columns[k])
                return sign*g[component]+mass_coefficient*g[0]
            out.append((name, {k: coefficient(k) for k in use},
                        rhs-fsum(coefficient(k) for k in fixed_columns)))
        return out

    def moment_targets_ok(self, chosen, *, tolerance_tm=1e-5):
        return all(fsum(coeff[k] for k in chosen if k in coeff) <= rhs+tolerance_tm
                   for _, coeff, rhs in self.moment_rows(columns=chosen))

    def lcg_moment(self, column):
        g = self.column_moments(column)
        return g[2]-(self.lcg_target or 0.)*g[0]

    def _column_cost(self, column, phase):
        if phase in (1, 2):
            return 0. if phase == 1 else column.cost
        page = self.pages[column.page]
        total = .02*column.cost+self.vertical_weight*self.column_moments(column)[1]
        total += (self.stack_cost*len(page.stacks) if page.deck else
                  -sum(self.hold_gain(b) for b in column.boxes))
        return total

    def moment_cost(self, page, tier, box):
        """Compatibility only: linear nominal tier moment; master uses whole columns."""
        base = max(float(self.T.stack_base_z[j]) for j in page.stacks)
        return self.vertical_weight*float(self.T.mass[box])/1000.*(base+tier*2.591+self._physical_height(box)/2)

    def solve_lp(self, *, active_boxes, active_stacks, hold_full, phase):
        boxes = sorted(active_boxes, key=self.row.get)
        stacks = sorted(active_stacks)
        if self.master_matrix_size < len(self.columns) or self.master_matrix is None:
            ri, ci = [], []
            for index in range(self.master_matrix_size, len(self.columns)):
                c = self.columns[index]
                for b in c.boxes:
                    ri.append(self.row[b]); ci.append(index-self.master_matrix_size)
                for j in self.pages[c.page].stacks:
                    ri.append(len(self.boxes)+self.stack_row[j]); ci.append(index-self.master_matrix_size)
            extra = csc_matrix((np.ones(len(ri)), (ri, ci)),
                               shape=(len(self.boxes)+len(self.stacks), len(self.columns)-self.master_matrix_size))
            self.master_matrix = extra if self.master_matrix is None else hstack([self.master_matrix, extra], format='csc')
            self.master_matrix_size = len(self.columns)
        selected = [self.row[b] for b in boxes]+[len(self.boxes)+self.stack_row[j] for j in stacks]
        inactive = np.ones(len(self.boxes)+len(self.stacks)); inactive[selected] = 0.
        compatible = np.asarray(self.master_matrix.T@inactive).ravel() == 0.
        if self.banned:
            compatible[list(self.banned)] = False
        use = np.flatnonzero(compatible).tolist()
        guards = self.moment_rows(fixed_columns=self.fixed_columns, columns=use)
        A = self.master_matrix[selected, :][:, use]
        slack = vstack([eye(len(boxes), format='csc'), csc_matrix((len(stacks), len(boxes)))], format='csc')
        A = hstack([A, slack, csc_matrix((len(selected), len(guards)))], format='csc')
        cost = ([self.column_cost(self.columns[k], phase) for k in use]
                +[UNCOVERED]*len(boxes)+[self.moment_penalty]*len(guards))
        upper, rhs = A[len(boxes):], np.ones(len(stacks))
        for i, (_, coefficients, bound) in enumerate(guards):
            row = np.zeros(len(cost))
            row[:len(use)] = [coefficients.get(k, 0.) for k in use]
            row[len(use)+len(boxes)+i] = -1.
            upper = vstack([upper, csc_matrix(row)], format='csc')
            rhs = np.append(rhs, bound)
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Unrecognized options detected.*', category=OptimizeWarning)
            res = linprog(cost, A_eq=A[:len(boxes)], b_eq=np.ones(len(boxes)), A_ub=upper,
                          b_ub=rhs, bounds=(0, None), method='highs', options={'threads': 1})
        if res.status != 0:
            raise RuntimeError(f'master LP status {res.status}: {res.message}')
        value = np.zeros(len(self.boxes))
        for i, b in enumerate(boxes):
            value[self.row[b]] = res.eqlin.marginals[i]
        stack_price = {j: float(res.ineqlin.marginals[i]) for i, j in enumerate(stacks)}
        self.moment_duals = {name: float(res.ineqlin.marginals[len(stacks)+i])
                             for i, (name, _, _) in enumerate(guards)}
        self.moment_slacks = {name: float(res.x[len(use)+len(boxes)+i])
                              for i, (name, _, _) in enumerate(guards)}
        self.lcg_price = self.moment_duals.get('lcg_max', 0.)
        x = {use[i]: float(res.x[i]) for i in range(len(use)) if res.x[i] > 1e-7}
        tail = res.x[len(use):len(use)+len(boxes)]
        self.short = {b: float(v) for b, v in zip(boxes, tail) if v > 1e-6}
        self.actual_uncovered = float(tail.sum())
        # Keep coverage pricing running when coverage is complete but its target
        # is violated. No claim of global infeasibility follows from this family.
        uncovered = self.actual_uncovered+sum(self.moment_slacks.values())
        return float(res.fun), x, value, stack_price, uncovered

    def price(self, page_index, value, *, boxes=None, phase=1):
        """Ordered knapsack with physical mass×height and moment-row duals.

        High-cube count determines exact cumulative ordinary-box height. Exotic
        previous-height effects use that same proxy for DP ranking, then every
        returned pattern's reduced gain is recomputed from its exact geometry.
        This is heuristic pricing for OOG, not an exhaustion certificate.
        """
        T, page = self.T, self.pages[page_index]
        kind_type = self.type_of[page_index]
        order = self.order if boxes is None else boxes
        heights = range(1, len(page.slots)+1) if self.oracle is not None and page.deck else (len(page.slots),)
        base = max(float(T.stack_base_z[j]) for j in page.stacks)
        out = {}
        vertical = (self.vertical_weight if phase == 3 else 0.)-self.moment_duals.get('vertical_max', 0.)
        for height in heights:
            limit, cached = self._pricing(page_index, height)
            items = [b for b in order if b in cached]
            best = np.full((height+1, height+1, limit+1), -np.inf)
            best[0, 0, 0] = 0.
            taken = []
            for b in items:
                _, weights, groups = cached[b]
                high = int(self._physical_height(b) > 2.7)
                mass = float(T.mass[b])/1000.
                half = self._physical_height(b)/2
                dx, dy = self._box_offsets(b)
                flags = np.zeros(best.shape, dtype=bool)
                updates = []
                for weight, tiers in groups:
                    lower_high = np.arange(height+1-high)[None, :]
                    z = base+tiers[:, None]*2.591+lower_high*.305+half
                    gain = np.full(z.shape, float(value[self.row[b]])) - vertical*mass*z
                    if phase == 3 and not page.deck:
                        gain += self.hold_gain(b)
                    for name, component, sign, mc, _ in self._target_specs():
                        if component == 1:
                            continue
                        coordinates = np.asarray([self._slot_xy(page, int(k))[component-2]
                                                  +(dx if component == 2 else dy) for k in tiers])
                        gain += self.moment_duals.get(name, 0.)*mass*(sign*coordinates[:, None]+mc)
                    source = best[tiers, :height+1-high, :limit+1-weight]
                    candidate = source+gain[:, :, None]
                    destination = best[tiers+1, high:, weight:]
                    better = candidate > destination+1e-12
                    updates.append((weight, tiers, candidate, better))
                for weight, tiers, candidate, better in updates:
                    destination = best[tiers+1, high:, weight:]
                    np.copyto(destination, candidate, where=better)
                    best[tiers+1, high:, weight:] = destination
                    flags[tiers+1, high:, weight:] = better
                taken.append((b, flags, high, weights))
            counts = range(1, height+1) if height == len(page.slots) and len(heights) == 1 else (height,)
            for count in counts:
                view = best[count].copy()
                for hc in range(height+1):
                    if hc > count or (count-hc)*2.591+hc*2.896 > page.hmax+1e-9:
                        view[hc] = -np.inf
                view[:, int(limit*self.factor[(kind_type, count)])+1:] = -np.inf
                if not np.isfinite(view).any():
                    continue
                hc, weight = np.unravel_index(int(np.argmax(view)), view.shape)
                chosen, k, h, w = [], count, int(hc), int(weight)
                for b, flags, high, weights in reversed(taken):
                    if k and flags[k, h, w]:
                        chosen.append(b)
                        k, h, w = k-1, h-high, w-weights[k-1]
                chosen = tuple(reversed(chosen))
                column = Pattern(page_index, chosen, 2.*self.restows(chosen))
                total = sum(value[self.row[b]] for b in chosen)
                if phase == 3:
                    # Stack charge is deducted separately by BaseModel.generate.
                    total -= self.vertical_weight*self.column_moments(column)[1]+.02*column.cost
                    if not page.deck:
                        total += sum(self.hold_gain(b) for b in chosen)
                for name, component, sign, mc, _ in self._target_specs():
                    g = self.column_moments(column)
                    total += self.moment_duals.get(name, 0.)*(sign*g[component]+mc*g[0])
                out[count] = (float(total), chosen)
        return out

    def _cp_moment_rows(self, solver_model, variables, *, fixed_columns=()):
        """Conservative integer rounding for construction targets, not a proof."""
        import math
        for _, coefficients, rhs in self.moment_rows(fixed_columns=fixed_columns,
                                                     columns=variables):
            solver_model.Add(sum(math.ceil(1000*coefficients[k]-1e-9)*variables[k]
                                 for k in variables) <= math.floor(1000*rhs+1e-9))

    def first_fit(self, boxes, stacks, rng=None):
        chosen = super().first_fit(boxes, stacks, rng)
        if chosen is not None:
            combined = list(self.fixed_columns)+list(chosen)
            covered = {b for k in combined for b in self.columns[k].boxes}
            if covered == set(self.boxes) and not self.moment_targets_ok(combined):
                return None
        return chosen
