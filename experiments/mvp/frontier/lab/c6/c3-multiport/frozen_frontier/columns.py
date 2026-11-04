"""Column-level master plan for large ships: how many boxes of each cargo group go in each stack column.

A column is every stack position at one row of a 40-foot bay, in the hold or on deck: two 20-foot stacks side by side along
the ship. Planning at this level is the stack-level view of Larsen & Pacino 2021 and matches block stowage practice
(a column carries one length, ideally one discharge port). Limits are exact per column, so a column plan can be packed;
that is what the coarser area model could not promise. Inside a column boxes are then sorted: later port lower, heavier
lower, which is what keeps a column free of overstows and heavy-over-light.

CP-SAT decides the counts. Objective in lifts: 2 per box that overstows inside a mixed column, 2 per hatch cover opened,
2 per cover and later port, 2 per deck box above a hold due earlier, plus a small term for balance and low weight.
APPROXIMATION: DG segregation between boxes is left to the mask; refused plans come back as no-goods.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model

from frozen_frontier.master import stack_height_limit
from stowage.conventions import parse_slot
from stowage.solver.twostep import category_of


@dataclass
class ColumnResult:
    assignment: dict | None
    status: str
    objective: float | None
    wall_s: float
    columns: int
    groups: int
    variables: int
    unplaced: int


class ColumnPlanner:
    def __init__(self, problem, *, fixed=None, banned=None, deck_tiers=None):
        self.p = problem
        self.T = T = problem.engine.T
        instance = problem.instance
        self.fixed = dict(fixed or {})
        self.banned = set(banned or ())
        groups = defaultdict(list)
        for box in instance.load_list:
            if box not in self.fixed:
                groups[category_of(problem.boxes[box], instance)].append(box)
        self.groups = list(groups.values())
        ports = sorted({problem.pod(b) for b in instance.load_list})
        self.port_rank = {port: i+1 for i, port in enumerate(ports)}
        self.K = len(ports)
        # Column key: (even bay, row, deck?). 20-foot slots attach to the 40-foot position they fall under.
        self.column_slots = defaultdict(lambda: {20: [], 40: []})
        for s, slot in enumerate(T.slot_ids):
            if s in self.banned:
                continue
            where = parse_slot(slot)
            if deck_tiers is not None and where.on_deck and int(T.slot_k[s][0]) >= deck_tiers:
                continue
            if int(T.slot_n[s]) == 2:
                self.column_slots[where.bay, where.row, where.on_deck][40].append(s)
            else:
                bay = where.bay+1 if where.bay % 4 == 1 else where.bay-1
                self.column_slots[bay, where.row, where.on_deck][20].append(s)
        self.columns = sorted(self.column_slots)
        for key in self.columns:
            for length in (20, 40):
                self.column_slots[key][length].sort(key=lambda s: (int(T.slot_k[s][0]), T.slot_ids[s]))

    def _column_stacks(self, key):
        T = self.T
        slots = self.column_slots[key][20]+self.column_slots[key][40]
        return sorted({int(j) for s in slots for j in T.slot_stacks[s] if j >= 0})

    def counts_from(self, assignment):
        """Column counts of an existing plan, used as a solver hint. 40-foot boxes on a mixed column are skipped."""
        where = {}
        for c, key in enumerate(self.columns):
            for length in (20, 40):
                for s in self.column_slots[key][length]:
                    where[self.T.slot_ids[s]] = c
        hint = defaultdict(int)
        for g, members in enumerate(self.groups):
            for box in members:
                column = where.get(assignment.get(box))
                if column is not None:
                    hint[g, column] += 1
        return hint

    def solve(self, *, time_limit_s, workers, seed, forbid=(), hint=None):
        p, T, instance = self.p, self.T, self.p.instance
        model = cp_model.CpModel()
        forbid = set(forbid)
        rows = [[T.box_index[b] for b in members] for members in self.groups]
        unit = [int(T.units[r[0]]) for r in rows]
        pods = [self.port_rank[p.pod(m[0])] for m in self.groups]
        mass = [float(np.mean(T.mass[r])) for r in rows]
        tall = [float(T.height[r[0]]) for r in rows]
        rank = [int(T.rank[r].max()) for r in rows]
        threshold = T.e6_threshold
        n, by_col, deck_heavy = {}, defaultdict(list), []
        for g, members in enumerate(self.groups):
            ok = T.static[rows[g]].all(axis=0)
            heavy = threshold is not None and float(T.mass[rows[g]].max()) >= threshold
            terms = []
            for c, key in enumerate(self.columns):
                if (g, c) in forbid:
                    continue
                slots = self.column_slots[key][40 if unit[g] == 2 else 20]
                top = int(ok[np.array(slots, dtype=int)].sum()) if slots else 0
                if top <= 0:
                    continue
                n[g, c] = model.NewIntVar(0, min(top, len(members)), f'n{g}_{c}')
                terms.append(n[g, c])
                by_col[c].append(g)
                if heavy and key[2]:
                    # E6 (D25): heavy on deck only when the holds are full, so it is a last resort.
                    deck_heavy.append(30*n[g, c])
            # A box left out is allowed but costs far more than anything else, so the model always has an answer.
            miss = model.NewIntVar(0, len(members), f'miss{g}')
            model.Add(sum(terms)+miss == len(members))
            n[g, 'miss'] = miss
        objective, balance_t, balance_l, weight_up = [], [], [], []
        hull = instance.vessel.hull
        centre = hull.lpp_m/2 if hull is not None else 0.0
        hold_pod = defaultdict(dict)
        deck_counts = defaultdict(lambda: defaultdict(list))
        for c, key in enumerate(self.columns):
            groups = by_col[c]
            if not groups:
                continue
            tiers20, tiers40 = len(self.column_slots[key][20]), len(self.column_slots[key][40])
            is40 = model.NewBoolVar(f'long{c}')
            tens = sum(n[g, c] for g in groups if unit[g] == 1)
            forties = sum(n[g, c] for g in groups if unit[g] == 2)
            model.Add(tens <= tiers20*(1-is40)) if tiers20 else model.Add(tens == 0)
            model.Add(forties <= tiers40*is40) if tiers40 else model.Add(forties == 0)
            stacks = self._column_stacks(key)
            limit_mass = min(float(min(T.stack_lim_mixed[j], T.stack_lim_40[j])) for j in stacks)*2
            limit_height = min(stack_height_limit(T, j) for j in stacks)
            model.Add(sum(int(mass[g]/100)*n[g, c] for g in groups) <= int(limit_mass*0.97/100))
            # 40-foot boxes stack one per tier; 20-foot boxes share a tier in pairs.
            model.Add(sum(int(tall[g]*100)*(2 if unit[g] == 2 else 1)*n[g, c] for g in groups) <= int(limit_height*200))
            first = [p.cells[T.cell_ids[int(x)]] for s in self.column_slots[key][40]+self.column_slots[key][20] for x in T.slot_cells[s] if x >= 0]
            lcg = float(np.mean([x.lcg_m for x in first]))
            tcg = float(np.mean([x.tcg_m for x in first]))
            z = float(np.mean([x.vcg_base_m for x in first]))
            present = {}
            for q in range(1, self.K+1):
                here = [g for g in groups if pods[g] == q]
                if not here:
                    continue
                flag = model.NewBoolVar(f'p{c}_{q}')
                present[q] = flag
                model.Add(sum(n[g, c] for g in here) <= len(instance.load_list)*flag)
            ports_here = list(present)
            if len(ports_here) > 1:
                # A mixed column overstows unless its later port is at least as heavy; charge the earlier port's boxes.
                for q in ports_here:
                    earlier = [g for g in groups if pods[g] < q]
                    if earlier:
                        count = model.NewIntVar(0, len(instance.load_list), f'o{c}_{q}')
                        model.Add(count >= sum(n[g, c] for g in earlier)-len(instance.load_list)*(1-present[q]))
                        objective.append(2*count)
            for g in groups:
                balance_t.append(int(round(mass[g]/1000*tcg))*n[g, c])
                balance_l.append(int(round(mass[g]/1000*(lcg-centre)))*n[g, c])
                weight_up.append(int(round(mass[g]/1000*z))*n[g, c])
            cover = p.stacks[T.stack_ids[stacks[0]]].hatch_cover_id or f'bay{key[0]}'
            if key[2]:
                for q in ports_here:
                    deck_counts[cover][q].extend(n[g, c] for g in groups if pods[g] == q)
            else:
                for q in ports_here:
                    hold_pod[cover].setdefault(q, []).append(present[q])
        for cover, by_port in hold_pod.items():
            used = model.NewBoolVar(f'u{cover}')
            for q, flags in by_port.items():
                due = model.NewBoolVar(f'd{cover}_{q}')
                for flag in flags:
                    model.AddImplication(flag, due)
                model.AddImplication(due, used)
                objective.append(2*due)
                later = [v for r, vs in deck_counts[cover].items() if r > q for v in vs]
                if later:
                    count = model.NewIntVar(0, len(instance.load_list), f'r{cover}_{q}')
                    model.Add(count >= sum(later)-len(instance.load_list)*(1-due))
                    objective.append(2*count)
            objective.append(2*used)
        scale = 1000
        miss_cost = 200*scale
        total = scale*(sum(objective)+sum(deck_heavy))+miss_cost*sum(n[g, 'miss'] for g in range(len(self.groups)))
        if balance_t:
            span = 10*sum(int(mass[g]/1000*60)*len(self.groups[g]) for g in range(len(self.groups)))+1
            abs_t = model.NewIntVar(0, span, 'abs_t')
            abs_l = model.NewIntVar(0, span, 'abs_l')
            model.AddAbsEquality(abs_t, sum(balance_t))
            model.AddAbsEquality(abs_l, sum(balance_l))
            total += 2*abs_t+abs_l+sum(weight_up)
        model.Minimize(total)
        if hint:
            for key, var in n.items():
                if key[1] != 'miss':
                    model.AddHint(var, hint.get(key, 0))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        solver.parameters.num_workers = workers
        solver.parameters.random_seed = seed
        status = solver.Solve(model)
        name = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, name, None, len(n)
        counts = {k: solver.Value(v) for k, v in n.items() if solver.Value(v) > 0}
        return counts, name, solver.ObjectiveValue()/scale, len(n)

    def expand(self, counts):
        """Turn column counts into a box-to-slot assignment: later port lower, heavier lower."""
        p, T = self.p, self.T
        pools = {g: sorted(m, key=lambda b: (-float(T.mass[T.box_index[b]]), b)) for g, m in enumerate(self.groups)}
        out = dict(self.fixed)
        missing = sum(v for k, v in counts.items() if k[1] == 'miss')
        per_column = defaultdict(list)
        for (g, c), count in sorted((k, v) for k, v in counts.items() if k[1] != 'miss'):
            for _ in range(count):
                per_column[c].append(pools[g].pop(0))
        for c, boxes in per_column.items():
            key = self.columns[c]
            order = sorted(boxes, key=lambda b: (-p.pod(b), -float(T.mass[T.box_index[b]]), b))
            length = 40 if int(T.units[T.box_index[boxes[0]]]) == 2 else 20
            slots = self.column_slots[key][length]
            used = set()
            for box in order:
                row = T.box_index[box]
                slot = next((s for s in slots if s not in used and T.static[row, s]), None)
                if slot is None:
                    missing += 1
                    continue
                used.add(slot)
                out[box] = T.slot_ids[slot]
        return out, missing


def solve_columns(problem, *, time_limit_s, workers=4, seed=0, deck_tiers=None, hint_assignment=None):
    from frozen_frontier.blocks import place_specials
    started = monotonic()
    fixed, banned = place_specials(problem, seed=seed)
    planner = ColumnPlanner(problem, fixed=fixed, banned=banned, deck_tiers=deck_tiers)
    left = max(10., time_limit_s-(monotonic()-started))
    hint = planner.counts_from(hint_assignment) if hint_assignment else None
    counts, status, value, variables = planner.solve(time_limit_s=left, workers=workers, seed=seed, hint=hint)
    if counts is None:
        return ColumnResult(None, status, None, monotonic()-started, len(planner.columns), len(planner.groups), variables, 0)
    assignment, missing = planner.expand(counts)
    return ColumnResult(assignment, status, value, monotonic()-started, len(planner.columns), len(planner.groups), variables, missing)
