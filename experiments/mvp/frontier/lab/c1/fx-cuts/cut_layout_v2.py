"""V2 capacity-aware hatch labelling, copied from this experiment's cut_layout.

Change from frozen v1: select the stack mass limit by actual 20/40 composition,
matching feasibility._engine._slot_features. The former minimum of all three
limits unnecessarily discarded legal capacity, especially for short boxes.
Also forbid short cargo above long cargo on deck and compare class order to
all lower cargo when a nonzero class tolerance would permit adjacent drift.

Vertices are interchangeable cargo groups; labels are physical hold/deck blocks.
Each used (cover, discharge port) hyperedge costs two hatch lifts, and each deck
box above an earlier hold port costs two restow lifts.  Capacity and support
make this a constrained hypergraph assignment, NOT an exact ordinary min-cut.
CP-SAT solves the labelled packing and reports its actual bound/status.

The static mask, public booking categories and stack height screen are imported
unchanged from Frontier.  The result is only a construction proposal: dynamic
DG/OOG rules, E6 completion, yard access, stability and the full checker remain
the caller's responsibility.  No planner or checker is patched here.
"""
from __future__ import annotations

from collections import defaultdict
import math
import random
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model

from experiments.mvp.strategy.headroom.layout import (
    BlockLayout, BlockModel, audit_packing, booking_category,
)
from stowage.conventions import cells_of_slot
from stowage.solver.twostep import category_of


def cut_layout(instance, *, seed=0, time_limit_s=10., model=None, problem=None,
               balance_weight=0.002, permission_weight=0.05, margin=1.,
               monotone_stacks=False, deck_tiers=None, broaden=False,
               first_feasible=False):
    """Return a BlockLayout carrying a complete ``packing_witness`` in metadata.

    ``problem`` can reuse lab score's alns.Problem and its static arrays.  Raises
    ValueError when no witness was found inside the budget; this is not an
    infeasibility claim about the call.  ``broaden`` adds the other zone of each
    used cover, allowing the normal carrier to adjust hold/deck counts.

    Group mass uses its maximum, so the mass limits are conservative. Heights
    count in every physical stack touched (a long box uses full height twice).
    ``monotone_stacks`` forbids port overstow, otherwise a soft edge charges it.
    """
    from alns import Problem
    from master import stack_height_limit

    started = monotonic()
    p = problem or Problem(instance)
    T = p.engine.T
    blocks = model or BlockModel.from_instance(instance)
    cp = cp_model.CpModel()
    groups = defaultdict(list)
    for box in instance.load_list:
        groups[category_of(p.boxes[box], instance)].append(box)
    members = list(groups.values())
    retained = [placement for placement in instance.onboard.placements
                if placement.container_id not in instance.discharge_list]
    ports = sorted({p.pod(b) for b in instance.load_list}
                   | {p.pod(item.container_id) for item in retained})
    port_rank = {port: rank + 1 for rank, port in enumerate(ports)}
    K = len(ports)
    initial = {}
    initial_mass, initial_height = defaultdict(float), defaultdict(float)
    initial_long, initial_short = defaultdict(bool), defaultdict(bool)
    for placement in retained:
        box = placement.container_id
        row = T.box_index[box]
        footprint = cells_of_slot(placement.slot)
        for cid in footprint:
            c = T.cell_index[cid]
            j = int(T.cell_stack[c])
            initial[c] = box
            initial_mass[j] += float(T.mass[row]) / len(footprint)
            initial_height[j] += float(T.height[row])
            (initial_long if len(footprint) > 1 else initial_short)[j] = True

    x, by_group = {}, defaultdict(list)
    by_cell, rank_cell, port_cell = defaultdict(list), defaultdict(list), defaultdict(list)
    long_stack, short_stack = defaultdict(list), defaultdict(list)
    long_cell, short_cell = defaultdict(list), defaultdict(list)
    mass_stack, height_stack, closes_cell = defaultdict(list), defaultdict(list), defaultdict(list)
    present_terms, deck_terms, permissions = defaultdict(list), defaultdict(list), defaultdict(list)
    transverse, longitudinal = [], []
    centre = instance.vessel.hull.lpp_m / 2 if instance.vessel.hull is not None else 0.
    group_port = []
    for g, ids in enumerate(members):
        rows = [T.box_index[b] for b in ids]
        mask = T.static[rows].all(axis=0)
        q = port_rank[p.pod(ids[0])]
        group_port.append(q)
        max_mass = float(T.mass[rows].max())
        mean_mass = float(T.mass[rows].mean())
        height = float(T.height[rows].max())
        weight_rank = int(T.rank[rows].max()) + 1
        for s0 in np.flatnonzero(mask):
            s = int(s0)
            footprint = [int(c) for c in T.slot_cells[s] if c >= 0]
            if any(c in initial for c in footprint):
                continue
            if deck_tiers is not None and bool(T.slot_deck[s]) and max(T.slot_k[s]) >= deck_tiers:
                continue
            var = cp.NewBoolVar(f'x{g}_{s}')
            x[g, s] = var
            by_group[g].append(var)
            for c in footprint:
                j = int(T.cell_stack[c])
                by_cell[c].append(var)
                rank_cell[c].append(weight_rank * var)
                port_cell[c].append(q * var)
                mass_stack[j].append(int(math.ceil(max_mass / len(footprint) / 10)) * var)
                height_stack[j].append(int(math.ceil(height * 1000)) * var)
                (long_stack if len(footprint) > 1 else short_stack)[j].append(var)
                (long_cell if len(footprint) > 1 else short_cell)[c].append(var)
                if bool(T.closes_stack[rows].any()):
                    closes_cell[c].append(var)
            touched = blocks.blocks_of_slot(T.slot_ids[s])
            covers = {bid.rsplit('/', 1)[0] for bid in touched}
            for bid in touched:
                permissions[booking_category(p.boxes[ids[0]], instance), bid].append(var)
            for cover in covers:
                if bool(T.slot_deck[s]):
                    deck_terms[cover, q].append(var)
                else:
                    present_terms[cover, q].append(var)
            coords = [p.cells[T.cell_ids[c]] for c in footprint]
            transverse.append(int(round(mean_mass / 1000 * np.mean([c.tcg_m for c in coords]))) * var)
            longitudinal.append(int(round(mean_mass / 1000 * (np.mean([c.lcg_m for c in coords]) - centre))) * var)
        if not by_group[g]:
            raise ValueError(f'no static group domain for group {g}')
        cp.Add(sum(by_group[g]) == len(ids))

    occupied, rank_at, pod_at = {}, {}, {}
    for c in range(len(T.cell_ids)):
        occupied[c] = cp.NewBoolVar(f'o{c}')
        cp.Add(occupied[c] == int(c in initial) + sum(by_cell[c]))
        rank_at[c] = cp.NewIntVar(0, 4, f'r{c}')
        pod_at[c] = cp.NewIntVar(0, K, f'q{c}')
        row = T.box_index[initial[c]] if c in initial else None
        cp.Add(rank_at[c] == (int(T.rank[row]) + 1 if row is not None else 0) + sum(rank_cell[c]))
        cp.Add(pod_at[c] == (port_rank[p.pod(initial[c])] if c in initial else 0) + sum(port_cell[c]))
    objective = []
    for j, cells in enumerate(T.stack_cells):
        _add_stack_mass_limits(
            cp, mass_terms=mass_stack[j], short_terms=short_stack[j],
            long_terms=long_stack[j], fixed_short=initial_short[j],
            fixed_long=initial_long[j], initial_mass_kg=initial_mass[j],
            limit20=float(T.stack_lim_20[j]), limit40=float(T.stack_lim_40[j]),
            limit_mixed=float(T.stack_lim_mixed[j]), margin=margin,
            allow_mixed=bool(T.stack_deck[j]), name=str(j))
        hmax = stack_height_limit(T, j)
        if math.isfinite(hmax):
            cp.Add(sum(height_stack[j]) <= math.floor((hmax - initial_height[j]) * 1000))
        short_at = {c: int(c in initial and p.boxes[initial[c]].length_ft == 20)
                    + sum(short_cell[c]) for c in cells}
        long_at = {c: int(c in initial and p.boxes[initial[c]].length_ft != 20)
                   + sum(long_cell[c]) for c in cells}
        _add_stack_order_constraints(cp, cells=cells, rank_at=rank_at,
                                     short_at=short_at, long_at=long_at,
                                     tolerance=int(T.e2_tol))
        for below, above in zip(cells, cells[1:]):
            cp.Add(occupied[above] <= occupied[below])
            if closes_cell[below]:
                cp.Add(sum(closes_cell[below]) + occupied[above] <= 1)
            # Restow edge: a later port above an earlier port causes a lift.
            # Adjacent edges are a surrogate when several intervening ports mix.
            if monotone_stacks:
                cp.Add(pod_at[above] <= pod_at[below])
            else:
                over = cp.NewBoolVar(f'over{above}')
                cp.Add(pod_at[above] <= pod_at[below] + K * over)
                objective.append(2000 * over)

    present = {}
    covers = sorted({block.hatch_group for block in blocks.blocks if block.zone == 'hold'})
    for cover in covers:
        used = cp.NewBoolVar(f'used_{cover}')
        flags = []
        for q in range(1, K + 1):
            terms = present_terms[cover, q]
            fixed = any(blocks.cell_to_block[T.cell_ids[c]] == f'{cover}/hold'
                        and port_rank[p.pod(b)] == q for c, b in initial.items())
            flag = cp.NewBoolVar(f'present_{cover}_{q}')
            if fixed:
                cp.Add(flag == 1)
            elif terms:
                cp.AddMaxEquality(flag, terms)
            else:
                cp.Add(flag == 0)
            present[cover, q] = flag
            flags.append(flag)
            objective.append(2000 * flag)
            later = [v for (other, r), vs in deck_terms.items() if other == cover and r > q for v in vs]
            if later:
                count = cp.NewIntVar(0, len(instance.load_list), f'restow_{cover}_{q}')
                cp.Add(count == sum(later)).OnlyEnforceIf(flag)
                cp.Add(count == 0).OnlyEnforceIf(flag.Not())
                objective.append(2000 * count)
        cp.AddMaxEquality(used, flags)
        objective.append(2000 * used)
    permission_flags = []
    for index, terms in enumerate(permissions.values()):
        flag = cp.NewBoolVar(f'permission{index}')
        cp.AddMaxEquality(flag, terms)
        permission_flags.append(flag)
    objective.extend(int(round(1000 * permission_weight)) * flag for flag in permission_flags)
    for label, terms in (('t', transverse), ('l', longitudinal)):
        if terms:
            # A simple safe bound independent of vessel dimensions.
            bound = int(sum(abs(int(term.coefficient)) for term in terms)) + 1
            absolute = cp.NewIntVar(0, bound, f'balance_{label}')
            cp.AddAbsEquality(absolute, sum(terms))
            objective.append(max(0, int(round(1000 * balance_weight))) * absolute)
    cp.Minimize(sum(objective))
    # A physical greedy packing breaks group/slot symmetry for the first CP
    # incumbent. A partial hint is useful even when the greedy pass dead-ends;
    # it is never returned as a certificate unless CP completes the assignment.
    hint = _packing_hint(p, members, x, initial, initial_mass, initial_height,
                         initial_long, initial_short, seed, margin,
                         monotone_stacks, max(0., min(1., time_limit_s / 10)))
    for key in hint:
        cp.AddHint(x[key], 1)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = int(seed) % 2147483647
    solver.parameters.stop_after_first_solution = bool(first_feasible)
    solver.parameters.max_time_in_seconds = max(.001, time_limit_s - (monotonic() - started))
    status = solver.Solve(cp)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        raise ValueError(f'cut packing {solver.StatusName(status)} after {monotonic() - started:.3f}s '
                         f'({len(x)} variables, greedy hint {len(hint)}/{len(instance.load_list)})')
    selected = defaultdict(list)
    for (g, s), var in x.items():
        if solver.BooleanValue(var):
            selected[g].append(s)
    positions, allowed = {}, defaultdict(set)
    for g, slots in selected.items():
        ids = sorted(members[g], key=lambda b: (-float(T.mass[T.box_index[b]]), b))
        slots.sort(key=lambda s: (max(T.slot_k[s]), T.slot_ids[s]))
        for box, s in zip(ids, slots):
            positions[box] = T.slot_ids[s]
            allowed[booking_category(p.boxes[box], instance)].update(blocks.blocks_of_slot(T.slot_ids[s]))
    if broaden:
        for permitted in allowed.values():
            used_covers = {bid.rsplit('/', 1)[0] for bid in permitted}
            permitted.update(block.id for block in blocks.blocks if block.hatch_group in used_covers)
    layout = BlockLayout({cat: frozenset(ids) for cat, ids in allowed.items()},
                         'capacity-hypergraph-cut', dict(
                             source_version='cut-layout-v2-composition-mass-and-order',
                             source='grouped supported slot CP-SAT; constrained hypergraph objective',
                             status=solver.StatusName(status), objective=solver.ObjectiveValue() / 1000,
                             bound=solver.BestObjectiveBound() / 1000,
                             wall_s=monotonic() - started, variables=len(x), groups=len(members),
                             packing_witness=positions, static_packing_witness=True,
                             conservative_group_mass=True, broaden=broaden,
                             first_feasible=first_feasible, greedy_hint_size=len(hint),
                             monotone_stacks=monotone_stacks,
                             feasibility='geometry/mass/height/class order only; full checker required'))
    issues = audit_packing(instance, positions, layout, blocks)
    if issues:
        raise ValueError('cut witness audit failed: ' + '; '.join(issues[:5]))
    return layout


def _add_stack_mass_limits(cp, *, mass_terms, short_terms, long_terms,
                           fixed_short=False, fixed_long=False,
                           initial_mass_kg=0., limit20, limit40, limit_mixed,
                           margin=1., allow_mixed=True, name=''):
    """E7 composition limits; mass_terms are in conservative 10-kg units.

    This matches _engine._slot_features for the resulting composition: short
    only uses limit20, long only limit40, and both use limit_mixed. Empty
    stacks need no mass bound. Hold no-mixing is a separate lashing condition.
    """
    has_short = cp.NewBoolVar(f'has_short{name}')
    has_long = cp.NewBoolVar(f'has_long{name}')
    for flag, fixed, terms in ((has_short, fixed_short, short_terms),
                               (has_long, fixed_long, long_terms)):
        if fixed:
            cp.Add(flag == 1)
        elif terms:
            cp.AddMaxEquality(flag, terms)
        else:
            cp.Add(flag == 0)
    if not allow_mixed:
        cp.Add(has_short + has_long <= 1)
    for capacity, conditions in (
            (limit20, [has_short, has_long.Not()]),
            (limit40, [has_long, has_short.Not()]),
            (limit_mixed, [has_short, has_long])):
        if math.isfinite(capacity):
            cp.Add(sum(mass_terms) <= math.floor(
                (capacity * margin - initial_mass_kg) / 10)).OnlyEnforceIf(conditions)
    return has_short, has_long


def _add_stack_order_constraints(cp, *, cells, rank_at, short_at, long_at,
                                 tolerance=0):
    """Core physical order, assuming occupied cells have bottom-up support.

    E2 compares an upper box to the lowest class anywhere below it. With zero
    tolerance adjacent comparisons imply this; positive tolerance needs every
    lower comparison so tolerance cannot accumulate up the stack. Twenty-foot
    boxes cannot appear above any long box, including in mixed deck stacks.
    """
    for index, above in enumerate(cells):
        lower = cells[:index]
        for below in lower:
            cp.Add(short_at[above] + long_at[below] <= 1)
        for below in (lower if tolerance else lower[-1:]):
            cp.Add(rank_at[above] <= rank_at[below] + tolerance)


def _packing_hint(p, groups, variables, initial, initial_mass, initial_height,
                  initial_long, initial_short, seed, margin, monotone, budget):
    """Bounded greedy hint with the same local resource constraints as CP."""
    from master import stack_height_limit
    T = p.engine.T
    started, rng, best = monotonic(), random.Random(seed), []
    domains = defaultdict(list)
    for g, s in variables:
        domains[g].append(s)
    info = []
    for g, ids in enumerate(groups):
        rows = [T.box_index[b] for b in ids]
        info.append((float(T.mass[rows].max()), float(T.height[rows].max()),
                     int(T.rank[rows].max()), bool(T.closes_stack[rows].any()),
                     p.pod(ids[0]), int(T.units[rows[0]])))
    for attempt in range(12):
        if monotonic() - started >= budget:
            break
        occupied = dict(initial)
        mass, height = defaultdict(float, initial_mass), defaultdict(float, initial_height)
        # Bits 1/2 record whether a stack contains short/long cargo. A mixed
        # deck stack is 3; hold stacks retain the no-mixing restriction.
        lengths = {j: 2 for j, used in initial_long.items() if used}
        for j, used in initial_short.items():
            if used:
                lengths[j] = lengths.get(j, 0) | 1
        rank = {c: int(T.rank[T.box_index[b]]) for c, b in initial.items()}
        pod = {c: p.pod(b) for c, b in initial.items()}
        closes = {c for c, b in initial.items() if T.closes_stack[T.box_index[b]]}
        pending = [(g, k) for g, ids in enumerate(groups) for k in range(len(ids))]
        rng.shuffle(pending)
        if attempt % 3 == 0:
            pending.sort(key=lambda item: (-info[item[0]][2], -info[item[0]][5], -info[item[0]][0]))
        elif attempt % 3 == 1:
            pending.sort(key=lambda item: (-info[item[0]][5], -info[item[0]][2], -info[item[0]][0]))
        else:
            pending.sort(key=lambda item: (len(domains[item[0]]), -info[item[0]][2], -info[item[0]][0]))
        chosen = []
        noise = {s: rng.random() for _, s in variables}
        for g, _ in pending:
            m, h, r, closed, q, units = info[g]
            candidates = []
            for s in domains[g]:
                cells = [int(c) for c in T.slot_cells[s] if c >= 0]
                okay = True
                for c in cells:
                    j, k = int(T.cell_stack[c]), int(T.cell_k[c])
                    composition = lengths.get(j, 0) | units
                    limit = float(T.stack_lim_20[j] if composition == 1 else
                                  T.stack_lim_40[j] if composition == 2 else T.stack_lim_mixed[j])
                    if c in occupied or mass[j] + m / units > limit * margin or height[j] + h > stack_height_limit(T, j):
                        okay = False
                        break
                    if not T.stack_deck[j] and j in lengths and lengths[j] != units:
                        okay = False
                        break
                    if k:
                        below = T.stack_cells[j][k - 1]
                        support = T.stack_cells[j][:k]
                        if (below not in occupied or below in closes
                                or r > min(rank[c] for c in support) + T.e2_tol
                                or (units == 1 and lengths.get(j, 0) & 2)
                                or (monotone and q > pod[below])):
                            okay = False
                            break
                if okay:
                    candidates.append(s)
            if not candidates:
                break
            s = min(candidates, key=lambda s: (bool(T.slot_deck[s]), max(T.slot_k[s]), noise[s]))
            chosen.append((g, s))
            for c0 in T.slot_cells[s]:
                if c0 < 0:
                    continue
                c, j = int(c0), int(T.cell_stack[c0])
                occupied[c], rank[c], pod[c] = g, r, q
                mass[j] += m / units
                height[j] += h
                lengths[j] = lengths.get(j, 0) | units
                if closed:
                    closes.add(c)
        if len(chosen) > len(best):
            best = chosen
        if len(best) == len(p.instance.load_list):
            break
    return best
