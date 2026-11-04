"""Block-level master plan for large ships: cargo groups to hatch areas, then slots area by area.

This is the published decomposition (Pacino et al. 2011; Maersk patent EP1909221, "storage areas"; Wilson & Roach 2000):
a small integer model decides how many boxes of each group go to each area under or over each hatch cover, across the
rotation. Each area is then a much smaller slot problem. Yard digging stays out of it; the terminal step picks real boxes.

An area is one hatch cover's hold, or the deck on that cover. Stacks with no cover form one area per bay.
APPROXIMATION: capacity in an area is counted in cells, so a split that cannot be packed shows up at slot level, where the
area's count is cut and the block model is solved again.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model

from master import MasterModel, stack_height_limit
from stowage.solver.twostep import category_of


@dataclass
class BlockResult:
    assignment: dict | None
    status: str
    objective: float | None
    wall_s: float
    areas: int
    groups: int
    rounds: int
    failed_areas: list


class BlockPlanner:
    def __init__(self, problem, *, fill=0.92, fixed=None, banned=None):
        self.p = problem
        self.T = T = problem.engine.T
        instance = problem.instance
        self.fill = fill
        self.fixed = dict(fixed or {})
        banned = set(banned or ())
        groups = defaultdict(list)
        for box in instance.load_list:
            if box not in self.fixed:
                groups[category_of(problem.boxes[box], instance)].append(box)
        self.groups = list(groups.values())
        ports = sorted({problem.pod(b) for b in instance.load_list})
        self.port_rank = {port: i+1 for i, port in enumerate(ports)}
        self.K = len(ports)
        area_of_stack = {}
        for j, stack_id in enumerate(T.stack_ids):
            stack = problem.stacks[stack_id]
            cover = stack.hatch_cover_id or f'bay{stack.bay}'
            area_of_stack[j] = (cover, 'deck' if stack.on_deck else 'hold')
        self.areas = sorted(set(area_of_stack.values()))
        self.area_index = {a: i for i, a in enumerate(self.areas)}
        self.area_cells = defaultdict(list)
        for j, cells in enumerate(T.stack_cells):
            self.area_cells[self.area_index[area_of_stack[j]]].extend(cells)
        self.area_slots = defaultdict(list)
        for s in range(len(T.slot_ids)):
            if s in banned:
                continue
            first = int(T.slot_stacks[s][0])
            self.area_slots[self.area_index[area_of_stack[first]]].append(s)
        self.allowed = {}
        for g, members in enumerate(self.groups):
            rows = [T.box_index[b] for b in members]
            mask = T.static[rows].all(axis=0)
            for a, slots in self.area_slots.items():
                self.allowed[g, a] = int(mask[np.array(slots, dtype=int)].sum())
        self.limits: dict = {}
        self.area_cut = defaultdict(int)
        self.area_mass, self.area_height = defaultdict(float), defaultdict(float)
        for j in range(len(T.stack_ids)):
            a = self.area_index[area_of_stack[j]]
            self.area_mass[a] += float(min(T.stack_lim_mixed[j], T.stack_lim_40[j]))
            self.area_height[a] += stack_height_limit(T, j)
        # E6 (D25): keep heavy groups out of deck areas when the holds can take them.
        threshold = T.e6_threshold
        if threshold is not None:
            hold_cells = sum(len(self.area_cells[self.area_index[a]]) for a in self.areas if a[1] == 'hold')
            heavy = [g for g, members in enumerate(self.groups)
                     if float(T.mass[[T.box_index[b] for b in members]].max()) >= threshold]
            needed = sum(int(T.units[T.box_index[self.groups[g][0]]])*len(self.groups[g]) for g in heavy)
            if needed <= hold_cells*self.fill:
                for g in heavy:
                    fits_in_hold = sum(self.allowed[g, a] for a, area in enumerate(self.areas) if area[1] == 'hold')
                    if fits_in_hold < len(self.groups[g]):
                        continue
                    for a, area in enumerate(self.areas):
                        if area[1] == 'deck':
                            self.limits[g, a] = 0

    def _position(self, area):
        p, T = self.p, self.T
        cells = [p.cells[T.cell_ids[c]] for c in self.area_cells[area]]
        return float(np.mean([c.lcg_m for c in cells])), float(np.mean([c.tcg_m for c in cells]))

    def solve_blocks(self, *, time_limit_s, workers, seed):
        p, T, instance = self.p, self.T, self.p.instance
        model = cp_model.CpModel()
        n = {}
        by_area = defaultdict(list)
        pod = lambda g: self.port_rank[p.pod(self.groups[g][0])]
        mass = lambda g: float(np.mean([T.mass[T.box_index[b]] for b in self.groups[g]]))
        units = lambda g: int(T.units[T.box_index[self.groups[g][0]]])
        for g, members in enumerate(self.groups):
            terms = []
            for a in range(len(self.areas)):
                top = min(len(members), self.allowed[g, a], self.limits.get((g, a), len(members)))
                if top <= 0:
                    continue
                n[g, a] = model.NewIntVar(0, top, f'n{g}_{a}')
                terms.append(n[g, a])
                by_area[a].append(g)
            if not terms:
                return None, 'NO_AREA', None
            model.Add(sum(terms) == len(members))
        objective = []
        present = {}
        hull = instance.vessel.hull
        centre = hull.lpp_m/2 if hull is not None else 0.0
        balance_t, balance_l = [], []
        for a in range(len(self.areas)):
            groups_here = by_area[a]
            if not groups_here:
                continue
            cells = len(self.area_cells[a])
            model.Add(sum(units(g)*n[g, a] for g in groups_here) <= int(cells*self.fill)-self.area_cut[a])
            # Stack weight is usually what fills a deck area, long before its cells run out.
            model.Add(sum(int(mass(g)/100)*n[g, a] for g in groups_here) <= int(self.area_mass[a]*self.fill/100))
            # A 40-foot box uses height in both of its stacks.
            heights = sum(int(float(T.height[T.box_index[self.groups[g][0]]])*100)*units(g)*n[g, a] for g in groups_here)
            model.Add(heights <= int(self.area_height[a]*100*self.fill))
            long_slots = sum(1 for s in self.area_slots[a] if int(T.slot_n[s]) == 2)
            model.Add(sum(n[g, a] for g in groups_here if units(g) == 2) <= long_slots)
            reefers = [g for g in groups_here if self.p.boxes[self.groups[g][0]].reefer]
            if reefers:
                model.Add(sum(n[g, a] for g in reefers) <= max(self.allowed[g, a] for g in reefers))
            lcg, tcg = self._position(a)
            for g in groups_here:
                balance_t.append(int(round(mass(g)/1000*tcg))*n[g, a])
                balance_l.append(int(round(mass(g)/1000*(lcg-centre)))*n[g, a])
            for rank in range(1, self.K+1):
                flag = model.NewBoolVar(f'p{a}_{rank}')
                present[a, rank] = flag
                here = [g for g in groups_here if pod(g) == rank]
                if here:
                    model.Add(sum(n[g, a] for g in here) <= len(p.instance.load_list)*flag)
                else:
                    model.Add(flag == 0)
                objective.append(flag)  # block stowage: one discharge port per area where possible
        covers = sorted({a[0] for a in self.areas})
        for cover in covers:
            hold = self.area_index.get((cover, 'hold'))
            deck = self.area_index.get((cover, 'deck'))
            if hold is None:
                continue
            used = model.NewBoolVar(f'u{cover}')
            for rank in range(1, self.K+1):
                if (hold, rank) in present:
                    model.AddImplication(present[hold, rank], used)
                    objective.append(2*present[hold, rank])
            objective.append(2*used)
            if deck is None:
                continue
            for rank in range(1, self.K):
                if (hold, rank) not in present:
                    continue
                later = [g for g in by_area[deck] if pod(g) > rank]
                if not later:
                    continue
                # Deck boxes still aboard when this hold opens are restowed there, 2 lifts each.
                count = model.NewIntVar(0, len(instance.load_list), f'r{cover}_{rank}')
                model.Add(count >= sum(n[g, deck] for g in later)-len(instance.load_list)*(1-present[hold, rank]))
                objective.append(2*count)
        scale = 1000
        total = scale*sum(objective)
        if balance_t:
            span = 10*sum(int(round(mass(g)/1000*50))*len(self.groups[g]) for g in range(len(self.groups)))+1
            abs_t = model.NewIntVar(0, span, 'abs_t')
            abs_l = model.NewIntVar(0, span, 'abs_l')
            model.AddAbsEquality(abs_t, sum(balance_t))
            model.AddAbsEquality(abs_l, sum(balance_l))
            total += 2*abs_t+abs_l
        model.Minimize(total)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        solver.parameters.num_workers = workers
        solver.parameters.random_seed = seed
        status = solver.Solve(model)
        name = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, name, None
        return {key: solver.Value(var) for key, var in n.items() if solver.Value(var) > 0}, name, solver.ObjectiveValue()/scale

    def fill_area(self, area, boxes, *, time_limit_s, workers, seed):
        """Slot plan for one area: the same constraint model, restricted to this area's boxes and slots."""
        model = MasterModel(self.p, boxes=boxes, slots=self.area_slots[area], hatch_terms=False, keep_heavy_in_hold=False,
                            balance_weight=0.)
        model.K = self.K
        model.port_rank = self.port_rank
        chosen, status, value, _, _ = model.solve(time_limit_s=time_limit_s, workers=workers, seed=seed)
        if chosen is None:
            return None, status
        return model.to_assignment(chosen), status


def place_specials(problem, seed=0):
    """Constrained cargo goes first (commercial rule A6, D1): out-of-gauge, over-height and top-stow boxes.

    Each takes a legal open slot, on deck when it can. The stack it lands in is closed to the rest of the plan, along with
    any slot the rules now refuse a plain box in, so the block model never plans next to it.
    Returns (fixed box-to-slot assignment, slots the block model must not use).
    """
    from stowage.actions.expander import Expander
    from stowage.actions.space import Place
    from stowage.checker.rules import placement_violations
    from stowage.conventions import cells_of_slot, parse_slot
    p, T, engine = problem, problem.engine.T, problem.engine
    special = [b for b in p.instance.load_list
               if p.boxes[b].oog is not None or T.closes_stack[T.box_index[b]]]
    fixed, closed, pushed = {}, set(), 0
    ex = Expander(p.instance)
    try:
        for box in sorted(special, key=lambda b: -float(T.mass[T.box_index[b]])):
            slots = [s for s in engine.legal_slots_for_box(box) if not ({T.cell_stack[c] for c in T.slot_cells[T.slot_index[s]] if c >= 0} & closed)]
            if not slots:
                continue
            slot = max(slots, key=lambda s: (parse_slot(s).on_deck, abs(p.cells[cells_of_slot(s)[0]].tcg_m), s))
            action = Place(box, slot)
            engine.push(action)
            pushed += 1
            ex.step(action)
            fixed[box] = slot
            closed |= {int(T.cell_stack[c]) for c in T.slot_cells[T.slot_index[slot]] if c >= 0}
        banned = set()
        ship = ex.world_state().ship
        plain = next((b for b in p.instance.load_list if b not in special and p.boxes[b].length_ft == 40), None)
        short = next((b for b in p.instance.load_list if b not in special and p.boxes[b].length_ft == 20), None)
        near = [parse_slot(slot) for slot in fixed.values()]
        for s, slot in enumerate(T.slot_ids):
            stacks = {int(T.cell_stack[c]) for c in T.slot_cells[s] if c >= 0}
            if stacks & closed:
                banned.add(s)
                continue
            where = parse_slot(slot)
            # Out-of-gauge rules reach neighbouring rows and bays only, so only those slots need the exact check.
            if not any(abs(where.bay-n.bay) <= 3 and where.on_deck == n.on_deck for n in near):
                continue
            probe = plain if int(T.slot_n[s]) == 2 else short
            if probe is None:
                continue
            if any(str(getattr(v, 'rule_id', '')).startswith('expert.e9') for v in placement_violations(p.instance, ship, p.boxes[probe], slot)):
                banned.add(s)
        return fixed, banned
    finally:
        for _ in range(pushed):
            engine.pop()


def solve_blocks(problem, *, time_limit_s, workers=4, seed=0, max_rounds=12):
    """Block model, then slot planning per area. An area that cannot be packed loses capacity and the block model reruns."""
    started = monotonic()
    fixed, banned = place_specials(problem, seed=seed)
    planner = BlockPlanner(problem, fixed=fixed, banned=banned)
    groups = planner.groups
    rounds = 0
    failed = []
    while rounds < max_rounds:
        rounds += 1
        left = time_limit_s-(monotonic()-started)
        if left <= 2:
            break
        counts, status, value = planner.solve_blocks(time_limit_s=min(left*0.3, 60.), workers=workers, seed=seed)
        if counts is None:
            return BlockResult(None, status, None, monotonic()-started, len(planner.areas), len(groups), rounds, failed)
        by_area = defaultdict(list)
        pools = {g: sorted(members, key=lambda b: (-float(planner.T.mass[planner.T.box_index[b]]), b))
                 for g, members in enumerate(groups)}
        for (g, a), count in sorted(counts.items()):
            for _ in range(count):
                by_area[a].append((g, pools[g].pop(0)))
        assignment, failed = dict(planner.fixed), []
        per_area = min(25., max(3., (time_limit_s-(monotonic()-started))*0.6/max(1, len(by_area))))
        for a, members in sorted(by_area.items(), key=lambda item: -len(item[1])):
            placed, slot_status = planner.fill_area(a, [b for _, b in members], time_limit_s=per_area, workers=workers, seed=seed)
            if placed is None:
                failed.append((planner.areas[a], len(members), slot_status))
                # This area could not be packed: lower its capacity by a tenth and solve the block model again.
                used = sum(int(planner.T.units[planner.T.box_index[b]]) for _, b in members)
                planner.area_cut[a] = int(len(planner.area_cells[a])*planner.fill)-max(0, used-max(2, used//10))
                continue
            assignment.update(placed)
        if not failed:
            return BlockResult(assignment, status, value, monotonic()-started, len(planner.areas), len(groups), rounds, [])
    return BlockResult(None, 'slot_fill_failed', None, monotonic()-started, len(planner.areas), len(groups), rounds, failed)
