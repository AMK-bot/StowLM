"""Master plan as a constraint model: which cargo group goes in which ship slot, for the whole rotation at once.

This is the carrier step of the published two-phase methods (Pacino et al. 2011; Maersk patent EP1909221: class counts to
storage areas across the rotation, block stowage by discharge port). Ours differs in two labelled ways: it works directly
at slot level instead of on aggregated areas, and its objective is written in this project's lift units:

    2 per box overstowing a box that leaves earlier in its stack (restow at the later port, off and on)
    2 per hatch cover opened this call, 2 per cover and later port with hold cargo due
    2 per deck box still aboard when the hold below it has to be opened

Yard digging is deliberately absent here: as in the published decomposition, the terminal step picks real boxes within
each group. ``Tables`` is the feasibility engine's own static data, so size, reefer, IMDG category and tier limits here are
the mask's, not a copy. CP-SAT (Google OR-Tools) is a constraint solver: it searches integer assignments and proves
bounds. See https://developers.google.com/optimization/cp/cp_solver.

APPROXIMATION: mass and height limits use group means with a margin; DG segregation between boxes, exact kg order and full
physics stay with the mask, finisher and checker. ``solve_master`` learns from mask rejections through no-good cuts.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import os
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model

from stowage.actions.space import Place
from stowage.solver.twostep import category_of


@dataclass
class MasterResult:
    assignment: dict | None
    status: str
    objective: float | None
    bound: float | None
    wall_s: float
    groups: int
    variables: int
    cuts: int = 0


# The mask's sight cap is a necessary bound over all trims; the real check depends on the finished trim and draught.
# Staying a margin below it keeps plans that the ballast finisher can actually make pass.
SIGHT_MARGIN_M = float(os.environ.get('FRONTIER_SIGHT_MARGIN_M', 0.))


def stack_height_limit(T, j):
    """Tallest stack allowed: the structural limit or the SOLAS line-of-sight cap (D55), whichever is lower."""
    limit = float(T.stack_hmax[j])
    sight = T.extra.get('stack_visibility_top')
    if sight is not None:
        limit = min(limit, float(sight[j])-float(T.stack_base_z[j])-SIGHT_MARGIN_M)
    return limit


class MasterModel:
    def __init__(self, problem, *, margin=0.97, balance_weight=0.0005, keep_heavy_in_hold=True, boxes=None, slots=None,
                 hatch_terms=True, deck_tiers=None):
        self.p = problem
        self.T = problem.engine.T
        self.margin = margin
        self.balance_weight = balance_weight
        self.hatch_terms = hatch_terms
        T, instance = self.T, problem.instance
        groups = defaultdict(list)
        for box in (instance.load_list if boxes is None else boxes):
            groups[category_of(problem.boxes[box], instance)].append(box)
        self.groups = list(groups.values())
        ports = sorted({problem.pod(b) for b in instance.load_list})
        scope = None if slots is None else np.array(sorted(slots), dtype=int)
        self.port_rank = {port: i+1 for i, port in enumerate(ports)}
        self.K = len(ports)
        self.allowed = []
        for members in self.groups:
            rows = [T.box_index[b] for b in members]
            allowed = np.flatnonzero(T.static[rows].all(axis=0))
            if scope is not None:
                allowed = np.intersect1d(allowed, scope)
            if deck_tiers is not None:
                # Lower deck stacks cast smaller blind sectors (SOLAS V/22); see sight.py.
                low = ~T.slot_deck[allowed] | (T.slot_k[allowed, 0] < deck_tiers)
                allowed = allowed[low]
            # E6 (D25): a heavy box may sit on deck only if no compatible hold slot stays empty at departure.
            # The mask enforces that as a completion condition, so keep heavy groups in the hold when they fit.
            threshold = T.e6_threshold
            if keep_heavy_in_hold and threshold is not None and float(T.mass[rows].max()) >= threshold:
                hold = allowed[~T.slot_deck[allowed]]
                if len(hold) >= len(members):
                    allowed = hold
            self.allowed.append(allowed)
        self.cuts: set = set()

    def _build(self):
        p, T, instance = self.p, self.T, self.p.instance
        model = cp_model.CpModel()
        x = {}
        cell_terms = defaultdict(list)
        n_cells = len(T.cell_ids)
        q_terms = defaultdict(list)
        rank_terms = defaultdict(list)
        over20 = defaultdict(list)
        is40 = defaultdict(list)
        stack_mass = defaultdict(list)
        stack_height = defaultdict(list)
        balance_t, balance_l = [], []
        hull = instance.vessel.hull
        centre = hull.lpp_m/2 if hull is not None else 0.0
        for g, members in enumerate(self.groups):
            b0 = T.box_index[members[0]]
            q = self.port_rank[p.pod(members[0])]
            mean_mass = float(np.mean([T.mass[T.box_index[b]] for b in members]))
            rank = max(int(T.rank[T.box_index[b]]) for b in members)
            height = float(T.height[b0])
            units = int(T.units[b0])
            for s in self.allowed[g]:
                s = int(s)
                if (g, s) in self.cuts:
                    continue
                var = model.NewBoolVar(f'x{g}_{s}')
                x[g, s] = var
                cells = [int(c) for c in T.slot_cells[s] if c >= 0]
                for c in cells:
                    cell_terms[c].append(var)
                    q_terms[c].append(q*var)
                    rank_terms[c].append((rank+1)*var)
                    (is40 if units == 2 else over20)[c].append(var)
                    j = int(T.cell_stack[c])
                    stack_mass[j].append(int(round(mean_mass/len(cells)/100))*var)
                    stack_height[j].append(int(round(height*100/len(cells)))*var)
                lcg = float(np.mean([p.cells[T.cell_ids[c]].lcg_m for c in cells]))
                tcg = float(np.mean([p.cells[T.cell_ids[c]].tcg_m for c in cells]))
                balance_t.append(int(round(mean_mass/1000*tcg))*var)
                balance_l.append(int(round(mean_mass/1000*(lcg-centre)))*var)
            model.Add(sum(x[g, int(s)] for s in self.allowed[g] if (g, int(s)) in x) == len(members))
        occ, Q, R = {}, {}, {}
        if self.hatch_terms:
            live_cells = range(n_cells)
        else:
            live_cells = sorted(cell_terms)
        for c in live_cells:
            occ[c] = model.NewBoolVar(f'o{c}')
            model.Add(sum(cell_terms[c]) == occ[c])
            Q[c] = model.NewIntVar(0, self.K, f'q{c}')
            model.Add(Q[c] == sum(q_terms[c]))
            R[c] = model.NewIntVar(0, 4, f'r{c}')
            model.Add(R[c] == sum(rank_terms[c]))
        objective = []
        over = {}
        for j, full_cells in enumerate(T.stack_cells):
            cells = [c for c in full_cells if c in occ]
            limit = float(min(T.stack_lim_mixed[j], T.stack_lim_40[j]))
            if stack_mass[j]:
                model.Add(sum(stack_mass[j]) <= int(limit*self.margin/100))
                model.Add(sum(stack_height[j]) <= int(stack_height_limit(T, j)*100))
            if not T.stack_deck[j]:
                # The lashing check refuses hold stacks that mix 20- and 40-foot boxes (IRS special consideration).
                shorts = [v for c in cells for v in over20[c]]
                longs = [v for c in cells for v in is40[c]]
                if shorts and longs:
                    short_stack = model.NewBoolVar(f'short{j}')
                    model.Add(sum(shorts) <= len(cells)*short_stack)
                    model.Add(sum(longs) <= len(cells)*(1-short_stack))
            earliest_below = None
            for k, c in enumerate(cells):
                if k:
                    below = cells[k-1]
                    model.AddImplication(occ[c], occ[below])
                    model.Add(R[c] <= R[below]+int(T.e2_tol))
                    if over20[c] and is40[below]:
                        model.Add(sum(over20[c])+sum(is40[below]) <= 1)
                    flag = model.NewBoolVar(f'ov{c}')
                    over[c] = flag
                    model.Add(Q[c]-earliest_below <= self.K*flag)
                    objective.append(2*flag)
                    # Empty cells hold 0 here, which would fake an early port; only occupied cells count.
                    shifted = model.NewIntVar(0, self.K+1, f's{c}')
                    model.Add(shifted >= Q[c])
                    model.Add(shifted >= (self.K+1)*(1-occ[c]))
                    model.Add(shifted <= Q[c]+(self.K+1)*(1-occ[c]))
                    low = model.NewIntVar(0, self.K+1, f'm{c}')
                    model.AddMinEquality(low, [earliest_below, shifted])
                    earliest_below = low
                else:
                    earliest_below = model.NewIntVar(0, self.K+1, f'm{c}')
                    model.Add(earliest_below >= Q[c])
                    model.Add(earliest_below >= (self.K+1)*(1-occ[c]))
                    model.Add(earliest_below <= Q[c]+(self.K+1)*(1-occ[c]))
        for cover in instance.vessel.hatch_covers if self.hatch_terms else ():
            holds = [T.stack_index[s] for s in cover.hold_stack_ids if s in T.stack_index]
            decks = [T.stack_index[s] for s in cover.deck_stack_ids if s in T.stack_index]
            hold_cells = [c for j in holds for c in T.stack_cells[j]]
            deck_cells = [c for j in decks for c in T.stack_cells[j]]
            if not hold_cells:
                continue
            used = model.NewBoolVar(f'h{cover.id}')
            for c in hold_cells:
                model.AddImplication(occ[c], used)
            objective.append(2*used)
            present = {}
            for rank in range(1, self.K+1):
                flag = model.NewBoolVar(f'hp{cover.id}_{rank}')
                present[rank] = flag
                objective.append(2*flag)
                for c in hold_cells:
                    equal = model.NewBoolVar(f'he{c}_{rank}')
                    model.Add(Q[c] == rank).OnlyEnforceIf(equal)
                    model.Add(Q[c] != rank).OnlyEnforceIf(equal.Not())
                    model.AddImplication(equal, flag)
            for c in deck_cells:
                for rank in range(1, self.K):
                    # This deck box stays aboard while hold cargo below it leaves at ``rank``: restowed there.
                    later = model.NewBoolVar(f'dl{c}_{rank}')
                    model.Add(Q[c] > rank).OnlyEnforceIf(later)
                    model.Add(Q[c] <= rank).OnlyEnforceIf(later.Not())
                    penalty = model.NewBoolVar(f'dp{c}_{rank}')
                    model.AddBoolAnd([later, present[rank]]).OnlyEnforceIf(penalty)
                    model.AddBoolOr([later.Not(), present[rank].Not(), penalty])
                    objective.append(2*penalty)
        scale = 10000
        total = scale*sum(objective)
        if balance_t:
            span = sum(abs(int(round(float(T.mass[T.box_index[b]])/1000*30))) for b in instance.load_list)+1
            abs_t = model.NewIntVar(0, span, 'abs_t')
            model.AddAbsEquality(abs_t, sum(balance_t))
            abs_l = model.NewIntVar(0, 10*span, 'abs_l')
            model.AddAbsEquality(abs_l, sum(balance_l))
            weight = max(1, int(self.balance_weight*scale))
            total += weight*(5*abs_t+abs_l)
        model.Minimize(total)
        return model, x, scale

    def solve(self, *, time_limit_s, workers, seed, hint=None):
        model, x, scale = self._build()
        if hint:
            for (g, s), var in x.items():
                model.AddHint(var, 1 if hint.get(g, {}).get(s) else 0)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        solver.parameters.num_workers = workers
        solver.parameters.random_seed = seed
        status = solver.Solve(model)
        name = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, name, None, None, len(x)
        chosen = defaultdict(list)
        for (g, s), var in x.items():
            if solver.BooleanValue(var):
                chosen[g].append(s)
        return dict(chosen), name, solver.ObjectiveValue()/scale, solver.BestObjectiveBound()/scale, len(x)

    def to_assignment(self, chosen):
        """Within a group give the heaviest box the lowest slot, so kg order holds inside a column."""
        T = self.T
        out = {}
        for g, slots in chosen.items():
            members = sorted(self.groups[g], key=lambda b: (-float(T.mass[T.box_index[b]]), b))
            ordered = sorted(slots, key=lambda s: (int(T.slot_k[s][0]), T.slot_ids[s]))
            for box, s in zip(members, ordered):
                out[box] = T.slot_ids[s]
        return out


def first_rejection(problem, actions):
    """Index of the first action the mask refuses, or None."""
    engine, pushed = problem.engine, 0
    try:
        for i, action in enumerate(actions):
            if not engine.is_legal(action):
                return i
            engine.push(action)
            pushed += 1
        return None
    finally:
        for _ in range(pushed):
            engine.pop()


def solve_master(problem, *, time_limit_s, workers=4, seed=0, max_cuts=30, deck_tiers=None):
    """Solve, replay through the mask in a clean load order, cut each refused group-slot pair, and re-solve."""
    from frozen_frontier.alns import Sequencer
    started = monotonic()
    master = MasterModel(problem, deck_tiers=deck_tiers)
    if master.solve(time_limit_s=min(5., time_limit_s*0.2), workers=workers, seed=seed)[1] == 'INFEASIBLE':
        master = MasterModel(problem, margin=1.0, keep_heavy_in_hold=False, deck_tiers=deck_tiers)
    sequencer = Sequencer(problem)
    best = None
    first_budget = time_limit_s*0.6
    cuts = 0
    while True:
        remaining = time_limit_s-(monotonic()-started)
        if remaining <= 1:
            break
        budget = first_budget if cuts == 0 else min(remaining, max(5., time_limit_s*0.1))
        chosen, status, value, bound, variables = master.solve(time_limit_s=budget, workers=workers, seed=seed)
        if chosen is None:
            return best or MasterResult(None, status, None, None, monotonic()-started, len(master.groups), variables, cuts)
        assignment = master.to_assignment(chosen)
        ordered = sequencer.order(assignment)
        if ordered is None:
            return best or MasterResult(None, 'no_order', value, bound, monotonic()-started, len(master.groups), variables, cuts)
        rejected = first_rejection(problem, ordered)
        if rejected is None:
            return MasterResult(assignment, status, value, bound, monotonic()-started, len(master.groups), variables, cuts)
        action = ordered[rejected]
        where = next(g for g, members in enumerate(master.groups) if action.box in members)
        master.cuts.add((where, problem.engine.T.slot_index[action.slot]))
        cuts += 1
        if cuts > max_cuts:
            break
    return best or MasterResult(None, 'cuts_exhausted' if cuts > max_cuts else 'time_limit', None, None, monotonic()-started,
                                len(master.groups), 0, cuts)
