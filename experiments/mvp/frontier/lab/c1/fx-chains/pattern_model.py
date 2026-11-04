"""Random column generation with an exact grouped pattern-packing master.

A pattern is a whole bottom-up homogeneous-size column, or two physical
columns occupied together by long boxes. A CP-SAT master selects disjoint
patterns and covers every cargo group exactly. Pattern generation is bounded
and conservative: failure is neither a bound nor map infeasibility. Native
mask replay and the full checker remain the acceptance gates.
"""
from collections import Counter, defaultdict
import random
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model


class Patterns:
    def __init__(self, problem, seed=0, bucket_kg=2000):
        self.p, self.T = problem, problem.engine.T
        self.rng = random.Random(seed)
        T = self.T
        self.freeze = bool(T.extra.get('rule_freeze_v1'))
        groups = defaultdict(list)
        for b, box in enumerate(T.box_ids):
            # POD is deliberately absent: overstows have a real lift cost,
            # but are legal and must not exclude a feasible packing.
            unique = bool(T.is_dg[b] or T.fallback[b] or T.heat[b] or self.freeze)
            key = (T.static[b].tobytes(), int(T.length[b]), int(T.rank[b]),
                   float(T.height[b]), bool(T.closes_stack[b]),
                   bool(T.e1_threshold is not None and T.mass[b] >= T.e1_threshold),
                   box if unique or not bucket_kg else int(float(T.mass[b])//bucket_kg))
            groups[key].append(box)
        self.groups = list(groups.values())
        self.rep = [T.box_index[ids[0]] for ids in self.groups]
        self.demand = [len(ids) for ids in self.groups]
        self.mass = [max(float(T.mass[T.box_index[b]]) for b in ids) for ids in self.groups]
        self.height = [float(T.height[b]) for b in self.rep]
        self.rank = [int(T.rank[b]) for b in self.rep]
        self.heavy = [bool(T.e1_threshold is not None and T.mass[b] >= T.e1_threshold)
                      for b in self.rep]
        self.closes = [bool(T.closes_stack[b]) for b in self.rep]
        self.kg_tol = getattr(problem.engine, '_weight_tol', None)
        self.units = []
        # Independent single twenty-foot columns.
        for j, cells in enumerate(T.stack_cells):
            slots = [T.slot_index[T.cell_ids[c]] for c in cells
                     if T.cell_ids[c] in T.slot_index]
            if len(slots) == len(cells) and slots:
                self.units.append(((j,), tuple(slots), 1))
        # Long boxes use a fixed pair for this complete pattern. Overlapping
        # pairs are alternatives; resource rows prevent choosing both.
        pairs = defaultdict(dict)
        for s in range(len(T.slot_ids)):
            if T.slot_n[s] != 2:
                continue
            js = tuple(int(j) for j in T.slot_stacks[s])
            ks = tuple(int(k) for k in T.slot_k[s])
            if ks[0] == ks[1]:
                pairs[js][ks[0]] = s
        for js, at in pairs.items():
            if abs(float(T.stack_base_z[js[0]])-float(T.stack_base_z[js[1]])) > 1e-6:
                continue
            slots = []
            while len(slots) in at:
                slots.append(at[len(slots)])
            if slots:
                self.units.append((js, tuple(slots), 2))
        self.allowed = []
        opportunity = [0]*len(self.groups)
        for js, slots, width in self.units:
            available = []
            for s in slots:
                here = [g for g, b in enumerate(self.rep)
                        if int(T.units[b]) == width and T.static[b, s]]
                available.append(here)
                for g in here:
                    opportunity[g] += 1
            self.allowed.append(available)
        self.rarity = [(1+self.demand[g])/(1+opportunity[g])
                       for g in range(len(self.groups))]
        self.patterns = []
        self.seen = set()

    def _add(self, unit, sequence):
        key = (unit, tuple(sequence))
        if not sequence or key in self.seen:
            return
        self.seen.add(key)
        js, slots, width = self.units[unit]
        self.patterns.append({'unit': unit, 'stacks': js,
                              'slots': slots[:len(sequence)],
                              'sequence': tuple(sequence),
                              'counts': dict(Counter(sequence))})

    def generate(self, per_unit, deadline):
        """Greedy randomized chains, with rare-group anchors at all depths."""
        T = self.T
        sight = T.extra.get('stack_visibility_top')
        for u, (js, slots, width) in enumerate(self.units):
            if monotonic() >= deadline:
                break
            hmax = min(float(T.stack_hmax[j]) for j in js)
            if sight is not None:
                hmax = min(hmax, *(float(sight[j])-float(T.stack_base_z[j]) for j in js))
            limit = min(float(T.stack_lim_20[j] if width == 1 else T.stack_lim_40[j]) for j in js)
            anchors = sorted({g for here in self.allowed[u] for g in here},
                             key=lambda g: (-self.rarity[g], -self.mass[g]))
            # Each anchor gets a few depths; ordinary random draws supply the
            # rest. Prefixes are retained, giving empty upper space cheaply.
            jobs = [(None, -1)]*per_unit
            for g in anchors[:max(8, per_unit//2)]:
                places = [k for k, here in enumerate(self.allowed[u]) if g in here]
                if places:
                    jobs.append((g, self.rng.choice(places)))
            for attempt, (anchor, position) in enumerate(jobs):
                if monotonic() >= deadline:
                    return
                counts = Counter()
                seq, mass, height = [], 0., 0.
                minimum, rankmin, nonheavy = float('inf'), 1 << 20, False
                # Distinct randomized target mixes rather than fresh noise at
                # every slot produce cohesive, varied whole-column patterns.
                preference = [self.rng.expovariate(1.) for _ in self.groups]
                for k, s in enumerate(slots):
                    candidates = []
                    for g in self.allowed[u][k]:
                        if counts[g] >= self.demand[g]:
                            continue
                        if mass+self.mass[g]/width > limit+1e-6 or height+self.height[g] > hmax+1e-9:
                            continue
                        if seq:
                            if self.freeze:
                                if self.kg_tol is None or self.mass[g] > minimum+self.kg_tol:
                                    continue
                            elif self.rank[g] > rankmin+int(T.e2_tol) or (self.heavy[g] and nonheavy):
                                continue
                        if anchor is not None and k < position:
                            # Preserve the anchor's all-lower weight predicate,
                            # remaining height/mass, and top-stowage position.
                            if self.closes[g] or height+self.height[g]+self.height[anchor] > hmax+1e-9:
                                continue
                            if mass+(self.mass[g]+self.mass[anchor])/width > limit+1e-6:
                                continue
                            if self.freeze:
                                if self.kg_tol is None or self.mass[anchor] > self.mass[g]+self.kg_tol:
                                    continue
                            elif (self.rank[anchor] > self.rank[g]+int(T.e2_tol)
                                  or (self.heavy[anchor] and not self.heavy[g])):
                                continue
                            if g == anchor and counts[g]+1 >= self.demand[g]:
                                continue
                        if anchor is not None and k == position and g != anchor:
                            continue
                        candidates.append(g)
                    if not candidates:
                        break
                    mode = attempt % 5
                    def value(g):
                        if mode == 0:
                            return (-self.rank[g], -self.mass[g], -preference[g])
                        if mode == 1:
                            return (-self.rarity[g]*preference[g], -self.rank[g], self.mass[g])
                        if mode == 2:
                            return (self.mass[g]*(.5+preference[g]), -self.rarity[g], -self.rank[g])
                        return (-preference[g]*(1+self.demand[g])*(.05+self.rarity[g]),
                                -self.rank[g], self.mass[g])
                    g = min(candidates, key=value)
                    seq.append(g)
                    counts[g] += 1
                    mass += self.mass[g]/width
                    height += self.height[g]
                    minimum, rankmin = min(minimum, self.mass[g]), min(rankmin, self.rank[g])
                    nonheavy |= not self.heavy[g]
                    self._add(u, seq)
                    if self.closes[g]:
                        break

    def master(self, *, full_hold):
        model = cp_model.CpModel()
        selected = [model.new_bool_var(f'pattern{i}') for i in range(len(self.patterns))]
        group_terms, stack_terms, fill_terms = defaultdict(list), defaultdict(list), defaultdict(list)
        for i, pattern in enumerate(self.patterns):
            v = selected[i]
            for g, count in pattern['counts'].items():
                group_terms[g].append(count*v)
            for j in pattern['stacks']:
                stack_terms[j].append(v)
                fill_terms[j].append(len(pattern['sequence'])*v)
        for g, demand in enumerate(self.demand):
            model.add(sum(group_terms[g]) == demand)
        for j in range(len(self.T.stack_ids)):
            model.add(sum(stack_terms[j]) <= 1)
            if full_hold and not self.T.stack_deck[j]:
                model.add(sum(fill_terms[j]) == len(self.T.stack_cells[j]))
        # Feasibility first. Final lift count is obtained only by native replay.
        return model, selected

    def assignment(self, solver, selected):
        by_group = defaultdict(list)
        for v, pattern in zip(selected, self.patterns):
            if solver.boolean_value(v):
                for g, s in zip(pattern['sequence'], pattern['slots']):
                    by_group[g].append(int(s))
        out = {}
        for g, members in enumerate(self.groups):
            slots = sorted(by_group[g], key=lambda s: (int(self.T.slot_deck[s]),
                           int(self.T.slot_k[s, 0]), self.T.slot_ids[s]))
            boxes = sorted(members, key=lambda b: (-self.p.pod(b),
                           -self.p.boxes[b].gross_mass_kg, b))
            if len(slots) != len(boxes):
                raise ValueError('pattern master returned incomplete group cover')
            out.update((b, self.T.slot_ids[s]) for b, s in zip(boxes, slots))
        return out


def construct(instance, problem, seed, budget_s):
    """Return independently native-validated assignments, ready for finishing."""
    from chain_model import validate
    started = monotonic()
    deadline = started+max(0., budget_s)
    detail = {'model': 'randomized_homogeneous_column_pattern_cover', 'attempts': [],
              'restrictions': ['homogeneous length columns', 'fixed long support pairs',
                               'group maximum mass', 'bounded sampled patterns'],
              'omissions': ['physics and ballast', 'full voyage checker']}
    construct.last_diagnostics = detail
    if instance.onboard.placements or instance.discharge_list:
        detail['unsupported'] = 'nonempty arrival or discharges'
        return []
    if budget_s <= .1:
        return []
    patterns = Patterns(problem, seed=seed)
    detail['groups'] = len(patterns.groups)
    detail['units'] = len(patterns.units)
    # Full holds are a sufficient legacy E6 proposal, never a necessity claim.
    hold_cells = sum(len(cells) for j, cells in enumerate(patterns.T.stack_cells)
                     if not patterns.T.stack_deck[j])
    fill_hold = (not patterns.freeze and len(patterns.T.box_ids) > 60
                 and sum(int(n) for n in patterns.T.units) >= hold_cells)
    rounds = [(64, fill_hold), (128, False), (256, False)]
    for arm, (samples, full_hold) in enumerate(rounds):
        remaining = deadline-monotonic()
        if remaining < .1:
            break
        generation_end = min(deadline, monotonic()+max(.05, remaining*.22))
        patterns.generate(samples, generation_end)
        model, selected = patterns.master(full_hold=full_hold)
        row = {'samples_per_unit': samples, 'full_hold': full_hold,
               'patterns': len(selected), 'solutions': []}
        detail['attempts'].append(row)
        arm_end = min(deadline, monotonic()+max(.1, remaining/(len(rounds)-arm)))
        for repeat in range(8):
            seconds = arm_end-monotonic()
            if seconds < .05:
                break
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.max_time_in_seconds = seconds
            solver.parameters.random_seed = (seed+arm*31+repeat) % 2_147_483_647
            solver.parameters.stop_after_first_solution = True
            status = solver.solve(model)
            item = {'status': solver.status_name(status), 'solve_s': solver.wall_time,
                    'branches': solver.num_branches, 'conflicts': solver.num_conflicts}
            row['solutions'].append(item)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break
            assignment = patterns.assignment(solver, selected)
            good, validation = validate(problem, assignment)
            item['validation'] = validation
            if good:
                detail['wall_s'] = monotonic()-started
                return [assignment]
            chosen = [v for v in selected if solver.boolean_value(v)]
            model.add(sum(chosen) <= len(chosen)-1)
    detail['wall_s'] = monotonic()-started
    return []


def propose(instance, *, seed, budget_s, context):
    from alns import Problem
    problem = context.get('problem') or Problem(instance)
    out = construct(instance, problem, seed, budget_s)
    context.setdefault('method_metadata', {})['pattern_model'] = construct.last_diagnostics
    return out
