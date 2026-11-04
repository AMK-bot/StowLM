"""Preemptive goals for the private assignment LP, with checker-labelled proxies.

Only the tier currently tested is elastic. Earlier peeled tiers are absent;
T0 and represented higher-tier rows remain hard. These linear rows do not
replace the unchanged checker, especially for whole-vessel nonlinear physics.
"""
from collections import defaultdict
from time import monotonic
import hashlib
import warnings

import numpy as np
from scipy.optimize import linprog, OptimizeWarning
from scipy.sparse import csc_matrix, coo_matrix, hstack, vstack

from tiers import load_table
from assignment_runtime import load_assignment
load_assignment()
from gap import Assignment


class ElasticAssignment(Assignment):
    def __init__(self, *args, level=0, allow_partial=False, **kwargs):
        self.level = int(level)
        if not 0 <= self.level <= 5:
            raise ValueError('level must be 0..5')
        self.allow_partial = bool(allow_partial and self.level == 3)
        self.tested = 6-self.level if self.level else None
        self.diagnostics = []
        self.table = load_table()
        self._elastic_ready = False
        self.restricted_infeasible = False
        self.restricted_proof = None
        super().__init__(*args, **kwargs)

    def _tier(self, rule):
        return int(self.table.tier_for(rule)[1:])

    def _booking_rows(self, width):
        """State-free T3 booking goals, and a conditional E6 occupancy proxy."""
        rows, bounds, info = [], [], []
        T, model = self.T, self.m
        factual = getattr(model, 'assignment_original_tables', T)
        boxes = getattr(model, 'assignment_original_boxes', model.p.boxes)
        by_box, by_cell, by_slot = defaultdict(list), defaultdict(list), defaultdict(list)
        for v, (b, q) in enumerate(self.pair):
            by_box[b].append((v, q))
            by_slot[q].append(v)
            for cell in T.slot_cells[q]:
                if cell >= 0:
                    by_cell[int(cell)].append(v)

        def add(coeff, rule, group):
            if coeff:
                rows.append(coeff); bounds.append(0.)
                info.append(dict(rule_id=rule, group=group, proxy=True))

        if self.tested == 5 and getattr(factual, 'e6_threshold', None) is not None:
            for b in model.boxes:
                if float(T.mass[b]) < factual.e6_threshold:
                    continue
                deck = [(v, 1.) for v, q in by_box[b] if bool(T.slot_deck[q])]
                if not deck:
                    continue
                coeff = list(deck)
                if factual.e6_fallback:
                    hold = [int(q) for q in np.flatnonzero(factual.static[b]) if not T.slot_deck[q]]
                    cells = {int(c) for q in hold for c in T.slot_cells[q] if c >= 0}
                    if not cells:
                        continue  # No compatible hold cell: the fallback permits deck.
                    for cell in cells:
                        coeff.extend((v, -1./len(cells)) for v in by_cell[cell])
                add(coeff, 'expert.e6.heavy_under_deck', 'compatible_hold_occupancy_proxy')

        if self.tested == 3:
            heat_cache = getattr(model, '_assignment_heat_booking_cache', {})
            for b in model.boxes:
                box = boxes[T.box_ids[b]]
                flags = {str(flag) for flag in box.special_stowage}
                predicates = []
                if 'under_deck' in flags:
                    predicates.append(('expert.e8.under_deck', lambda q: bool(T.slot_deck[q])))
                if 'on_deck' in flags:
                    predicates.append(('expert.e8.on_deck', lambda q: not bool(T.slot_deck[q])))
                if box.reefer:
                    predicates.append(('expert.physical.reefer_plug', lambda q: not any(
                        model.p.cells[T.cell_ids[int(cell)]].reefer_plug
                        for cell in T.slot_cells[q] if cell >= 0)))
                if 'away_from_heat' in flags:
                    # Native placement checks, cached on the one immutable model.
                    from stowage.checker.rules import placement_violations
                    def hot(q, box=box):
                        key = (box.id, int(q))
                        if key not in heat_cache:
                            heat_cache[key] = any(v.rule_id == 'expert.e8.away_from_heat' and v.severity == 'hard'
                                for v in placement_violations(model.p.instance, model.p.instance.onboard,
                                                              box, T.slot_ids[q]))
                        return heat_cache[key]
                    predicates.append(('expert.e8.away_from_heat', hot))
                for rule, bad in predicates:
                    add([(v, 1.) for v, q in by_box[b] if bad(q)], rule, 'booking_edge')
                if 'top_stowage' in flags:
                    # A booked top box and any occupied higher tier cannot coexist.
                    for v, q in by_box[b]:
                        for page_index, tier in self.slot_page[q]:
                            page = model.pages[page_index]
                            for upper in page.slots[tier+1:]:
                                above = by_slot[int(upper)]
                                if above:
                                    rows.append([(v, 1.)]+[(u, 1.) for u in above]); bounds.append(1.)
                                    info.append(dict(rule_id='expert.e8.top_stowage', group='booking_top', proxy=True))
            model._assignment_heat_booking_cache = heat_cache
        if not rows:
            return csc_matrix((0, width)), np.array([]), []
        ri, ci, values = [], [], []
        for r, terms in enumerate(rows):
            for c, value in terms:
                ri.append(r); ci.append(c); values.append(value)
        return coo_matrix((values, (ri, ci)), shape=(len(rows), width)).tocsc(), np.array(bounds), info

    def build(self):
        if self.level == 0:
            return super().build()
        if self._elastic_ready:
            return self._built
        c, A, b, E, e = super().build()
        added, extra_b, extra_info = self._booking_rows(len(c))
        if added.shape[0]:
            A, b = vstack([A, added], format='csc'), np.r_[b, extra_b]
            self.row_info.extend(extra_info)
            self.row_rules.extend(row['rule_id'] for row in extra_info)
        if self.allow_partial:
            # Moment windows concern loaded cargo, not the original full load
            # list. Move each target*loaded_mass to the left-hand side.
            total_t = sum(float(self.T.mass[box]) for box in self.m.boxes)/1000.
            if total_t > 0:
                A = A.tolil(copy=True)
                b = np.array(b, copy=True)
                for row, info in enumerate(self.row_info):
                    if info['group'] != 'moment_proxy':
                        continue
                    target = float(b[row])/total_t
                    info.update(partial_loaded_mass=True, full_load_bound=float(b[row]),
                                loaded_mass_target=target)
                    for v, (box, _) in enumerate(self.pair):
                        A[row, v] -= target*float(self.T.mass[box])/1000.
                    b[row] = 0.
                A = A.tocsc()
        self._original_width = len(c)
        self._raw_A, self._raw_b = A.copy(), b.copy()
        self._hard_zero = {col for col, info in self.extra_variables.items()
                           if info['group'] != 'restow_objective'}
        keep, elastic, dropped = [], [], set()
        for row, info in enumerate(self.row_info):
            tier = self._tier(info['rule_id'])
            if info['group'] == 'restow_objective' or tier == 0 or tier < self.tested:
                keep.append(row)
            elif tier == self.tested:
                keep.append(row); elastic.append(row)
            else:
                dropped.add(info['rule_id'])
        self._kept_rows, self._elastic_rows = keep, elastic
        self._dropped_rules = sorted(dropped)
        self._elastic_col = {row: len(c)+k for k, row in enumerate(elastic)}
        kept_position = {row: k for k, row in enumerate(keep)}
        tail = coo_matrix(([-1.]*len(elastic),
            ([kept_position[row] for row in elastic], list(range(len(elastic))))),
            shape=(len(keep), len(elastic))).tocsc()
        A = hstack([A[keep], tail], format='csc')
        E = hstack([E, csc_matrix((E.shape[0], len(elastic)))], format='csc')
        c = np.r_[c, np.zeros(len(elastic))]
        for col in self._hard_zero:
            c[col] = 0.
        c[self.n:self.n+len(self.m.boxes)] = 0.
        self._built = (c, A, b[keep], E, e)
        self._elastic_ready = True
        return self._built

    def _goals(self):
        c = (self._built or self.build())[0]
        out = []
        if self.allow_partial:
            for name, values in (
                ('ashore_count', [1.]*len(self.m.boxes)),
                ('ashore_TEU', [float(self.T.units[b]) for b in self.m.boxes]),
                ('ashore_grams', [round(1000*float(self.T.mass[b])) for b in self.m.boxes])):
                goal = np.zeros(len(c)); goal[self.n:self.n+len(self.m.boxes)] = values
                out.append((name, goal))
        if self._elastic_col:
            goal = np.zeros(len(c)); goal[list(self._elastic_col.values())] = 1.
            out.append((f'T{self.tested}_row_breach_proxy', goal))
        out.append(('ordinary_lift_cost_proxy', c.copy()))
        return out

    def _bounds(self, lower=None, upper=None):
        width = len((self._built or self.build())[0])
        low = np.zeros(width) if lower is None else np.array(lower, copy=True)
        high = np.full(width, np.inf) if upper is None else np.array(upper, copy=True)
        high[:self.n] = np.minimum(high[:self.n], 1.)
        high[self.n:self.n+len(self.m.boxes)] = 1. if self.allow_partial else 0.
        for col in self._hard_zero:
            high[col] = 0.
        return low, high

    def _restricted_proof(self, phase):
        """An in-memory fingerprint permits reuse only of an identical model."""
        _, A, b, E, e = self._built or self.build()
        digest = hashlib.sha256()
        for matrix in (A, E):
            matrix = matrix.tocsc()
            for values in (np.asarray(matrix.shape, dtype=np.int64), matrix.data,
                           matrix.indices, matrix.indptr):
                digest.update(values.tobytes())
        for values in (b, e, *self._bounds(), np.asarray(self.pair, dtype=np.int64)):
            digest.update(np.asarray(values).tobytes())
        proof = dict(kind='restricted_model_exhaustion', phase=phase, level=self.level,
            status='INFEASIBLE', signature=digest.hexdigest(),
            scope=('restricted assignment LP only; not map infeasibility' if phase == 'lp' else
                   'conservatively rounded restricted integer model only; not map infeasibility'))
        self.restricted_infeasible = True
        self.restricted_proof = proof
        self.diagnostics.append(proof)

    def _diagnostic(self, phase, x, goals, status, started):
        slacks = []
        if x is not None:
            measured = np.asarray(self._raw_A@x[:self._original_width]).ravel()
            for row in self._elastic_rows:
                over = max(0., float(measured[row]-self._raw_b[row]))
                if over > 1e-7:
                    info = self.row_info[row]
                    slacks.append(dict(rule_id=info['rule_id'], row=row, group=info['group'],
                        proxy=True, measured_proxy=float(measured[row]), bound=float(self._raw_b[row]),
                        slack=over, unit='LP row units'))
        table = self.table
        represented = set(self.row_rules)
        all_rules = set(table.by_rule)
        self.diagnostics.append(dict(kind='goal_program', phase=phase, level=self.level,
            tested_tier=f'T{self.tested}', status=status, elapsed_s=monotonic()-started,
            complete_only=not self.allow_partial, goals=goals, row_slacks=slacks,
            dropped_rules=self._dropped_rules,
            not_explicit_row_rules=sorted(all_rules-represented),
            scope='assignment-row proxies; unchanged checker is authoritative'))

    def solve(self, lower=None, upper=None, time_limit=60.):
        if self.level == 0:
            return super().solve(lower, upper, time_limit)
        c, A, b, E, e = self._built or self.build()
        low, high = self._bounds(lower, upper)
        started, stop, goals, last = monotonic(), monotonic()+time_limit, [], None
        for name, goal in self._goals():
            left = stop-monotonic()
            if left <= 0:
                break
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', OptimizeWarning)
                res = linprog(goal, A_ub=A, b_ub=b, A_eq=E, b_eq=e,
                    bounds=np.c_[low, high], method='highs',
                    options={'threads': 1, 'time_limit': max(.01, left)})
            goals.append(dict(name=name, optimum=None if res.fun is None else float(res.fun), status=int(res.status)))
            if res.x is None:
                break
            last = res
            if res.status != 0:
                break
            # Preemptive: later costs cannot trade against this earlier goal.
            tolerance = 1e-7
            A = vstack([A, csc_matrix(goal.reshape(1, -1))], format='csc')
            b = np.r_[b, float(res.fun)+tolerance]
        x = None if last is None else last.x
        status = goals[-1]['status'] if goals else 1
        self._diagnostic('lp', x, goals, status, started)
        return dict(status=status, s=monotonic()-started,
            fun=None if x is None else float(c@x), x=x,
            reduced=None if last is None or x is None else last.lower.marginals,
            uncovered=None if x is None else float(x[self.n:self.n+len(self.m.boxes)].sum()))

    def tail(self, fixed, *, seconds=30., seed=0, log=None, hint=None):
        if self.level == 0:
            return super().tail(fixed, seconds=seconds, seed=seed, log=log, hint=hint)
        from ortools.sat.python import cp_model
        c, A, b, E, e = self._built or self.build()
        low, high = self._bounds()
        started, stop = monotonic(), monotonic()+max(.01, seconds)
        cp = cp_model.CpModel()
        solid = np.zeros(len(c))
        fixed_cols = {}
        for v, (box, q) in enumerate(self.pair):
            if fixed.get(box) == q:
                solid[v] = 1.; fixed_cols[box] = v
        if set(fixed_cols) != set(fixed):
            return None
        occupied = {int(cell) for q in fixed.values() for cell in self.T.slot_cells[q] if cell >= 0}
        variables, units = {}, {}
        for v, (box, q) in enumerate(self.pair):
            if box in fixed or any(int(cell) in occupied for cell in self.T.slot_cells[q] if cell >= 0):
                continue
            variables[v] = cp.NewBoolVar(f'x{v}'); units[v] = 1.
        for i, box in enumerate(self.m.boxes):
            col = self.n+i
            if self.allow_partial and box not in fixed:
                variables[col] = cp.NewBoolVar(f'ashore{box}'); units[col] = 1.
        for col in range(self.n+len(self.m.boxes), len(c)):
            if high[col] > 0:
                variables[col] = cp.NewIntVar(0, 1000000000, f'z{col}'); units[col] = .001
        def constrain(matrix, rhs, equality=False):
            rows = matrix.tocsr(); used = np.asarray(rows@solid).ravel()
            for r in range(rows.shape[0]):
                a, z = rows.indptr[r:r+2]
                terms = [(int(j), float(v)) for j, v in zip(rows.indices[a:z], rows.data[a:z]) if int(j) in variables]
                room = float(rhs[r]-used[r])
                scale = 20000./max([abs(v) for _, v in terms]+[1.])
                # Keep every auxiliary coefficient nonzero after conservative
                # rounding. A 50,000-unit moment coefficient would otherwise
                # scale a -0.001 slack coefficient to zero and silently harden it.
                scale = max([scale]+[1./(abs(v)*units[j]) for j, v in terms
                                     if j >= self.n+len(self.m.boxes) and v])
                expr = sum(int(np.ceil(v*scale*units[j]-1e-9))*variables[j] for j, v in terms)
                if equality:
                    cp.Add(expr == int(round(room*scale)))
                else:
                    cp.Add(expr <= int(np.floor(room*scale+1e-9)))
        constrain(A, b); constrain(E, e, True)
        if hint is not None:
            for col in variables:
                if col < self.n and col < len(hint):
                    cp.AddHint(variables[col], int(hint[col] >= .5))
        last_x, goals, status_name = None, [], 'UNKNOWN'
        for name, goal in self._goals():
            left = stop-monotonic()
            if left <= 0:
                break
            coeff = {j: int(round(float(goal[j])*units[j]*1000)) for j in variables if goal[j]}
            objective = sum(value*variables[j] for j, value in coeff.items())
            cp.Minimize(objective)
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.max_time_in_seconds = max(.01, left)
            solver.parameters.max_number_of_conflicts = 100000
            solver.parameters.max_deterministic_time = 3.
            solver.parameters.random_seed = seed
            status = solver.Solve(cp); status_name = solver.StatusName(status)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                goals.append(dict(name=name, optimum=None, status=status_name)); break
            last_x = solid.copy()
            for col, var in variables.items():
                last_x[col] = solver.Value(var)*units[col]
            optimum = solver.Value(objective) if not isinstance(objective, int) else objective
            goals.append(dict(name=name, optimum=float(goal@last_x), status=status_name))
            if status != cp_model.OPTIMAL:
                break
            cp.Add(objective <= optimum)
        self._diagnostic('tail', last_x, goals, status_name, started)
        if log is not None:
            log.append(dict(tail=status_name, vars=len(variables), s=monotonic()-started,
                uncovered=None if last_x is None else float(last_x[self.n:self.n+len(self.m.boxes)].sum())))
        if last_x is None:
            return None
        out = dict(fixed)
        out.update({box: q for v, (box, q) in enumerate(self.pair) if last_x[v] > .5})
        return out

    def column_from(self, x, j, used, free_tier):
        if self.level == 0:
            return super().column_from(x, j, used, free_tier)
        page = self.m.pages[j]; chosen = list(free_tier)
        for k in range(len(chosen), len(page.slots)):
            q = int(page.slots[k])
            options = sorted(((x[v], self.pair[v][0]) for v in self.by_slot.get(q, [])
                              if self.pair[v][0] not in used and self.pair[v][0] not in chosen and x[v] > .05), reverse=True)
            if not options or options[0][0] < .3:
                break
            for _, box in options:
                trial = chosen+[box]
                # At L4 the securing row is an elastic goal; at L5 it is peeled.
                securing_ok = self.level >= 4 or self.stack_ratio(page, trial) <= self.s.limit+1e-8
                if securing_ok and self.m.legal(page, tuple(trial), any_port=True):
                    chosen = trial; break
            else:
                break
        return chosen[len(free_tier):]

    def dive(self, *, deadline, tail_left=25, batch=.25, tail_s=15., retreats=6, seed=0, log=None):
        if self.level == 0:
            return super().dive(deadline=deadline, tail_left=tail_left, batch=batch,
                tail_s=tail_s, retreats=retreats, seed=seed, log=log)
        c = (self._built or self.build())[0]
        lower, upper = self._bounds()
        self.by_slot = defaultdict(list)
        for v, (_, q) in enumerate(self.pair):
            self.by_slot[q].append(v)
        fixed, history, x = {}, [], None
        for node in range(80):
            if monotonic() >= deadline:
                break
            row = self.solve(lower, upper, time_limit=max(.01, deadline-monotonic()))
            if log is not None:
                log.append(dict(node=node, status=row['status'], fixed=len(fixed), uncovered=row['uncovered'], s=row['s']))
            if row['x'] is None:
                if row['status'] == 2 and not fixed and not history:
                    self._restricted_proof('lp')
                    return None
                if not history or retreats <= 0:
                    break
                retreats -= 1
                for v in history.pop():
                    lower[v] = 0.; fixed.pop(self.pair[v][0], None)
                continue
            x = row['x'][:self.n]
            left = len(self.m.boxes)-len(fixed)
            if left <= tail_left:
                break
            by_page = defaultdict(dict)
            for box, q in fixed.items():
                j, k = self.slot_page[q][0]; by_page[j][k] = box
            sure = []
            for j, page in enumerate(self.m.pages):
                have = [by_page[j][k] for k in sorted(by_page[j])]
                if len(have) < len(page.slots):
                    top = max((x[v] for v in self.by_slot.get(int(page.slots[len(have)]), [])), default=0.)
                    if top >= .3:
                        sure.append((top, -len(have), j, have))
            used, chosen = set(fixed), []
            cells = {int(g) for q in fixed.values() for g in self.T.slot_cells[q] if g >= 0}
            for _, _, j, have in sorted(sure, key=lambda t: t[:3], reverse=True):
                if len(chosen) >= max(3, int(batch*left)):
                    break
                boxes = self.column_from(x, j, used, have)
                slots = [int(q) for q in self.m.pages[j].slots[len(have):len(have)+len(boxes)]]
                here = {int(g) for q in slots for g in self.T.slot_cells[q] if g >= 0}
                if not boxes or cells.intersection(here):
                    continue
                cells.update(here)
                for box, q in zip(boxes, slots):
                    v = next(v for v in self.by_slot[q] if self.pair[v][0] == box)
                    lower[v] = 1.; chosen.append(v); fixed[box] = q; used.add(box)
            if not chosen:
                break
            history.append(chosen)
        for attempt in range(6):
            if monotonic() >= deadline+2*tail_s:
                break
            found = self.tail(fixed, seconds=tail_s, seed=seed+attempt, log=log, hint=x)
            if found is not None:
                return found
            if (not fixed and not history and self.diagnostics
                    and self.diagnostics[-1].get('phase') == 'tail'
                    and self.diagnostics[-1].get('status') == 'INFEASIBLE'):
                self._restricted_proof('tail')
                break
            if history:
                for v in history.pop():
                    fixed.pop(self.pair[v][0], None)
        return None
