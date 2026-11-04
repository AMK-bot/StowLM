# Copied from experiments/mvp/frontier/lab/c1/fx-chains/chain_model.py; engine integration changes are local.
"""Conservative chain packing, followed by the unmodified native mask.

The CP model packs groups into physical cells simultaneously. Groups share
every rule-relevant attribute; a group's maximum mass is used, so arbitrary
within-group assignment respects each modelled stack mass limit. This is a
restriction, not a relaxation and not an original-map infeasibility proof.
"""
from __future__ import annotations

from collections import defaultdict
from math import ceil, floor
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker.rules import e6_completion_possible
from stowage.solver.securing_geometry import securing_state_violations


LAST_DIAGNOSTICS = {}


class ChainPacking:
    def __init__(self, problem, *, bucket_kg=2000, port_chains=True, full_hold=True):
        self.p, self.T = problem, problem.engine.T
        self.port_chains, self.full_hold = port_chains, full_hold
        T = self.T
        groups = defaultdict(list)
        for box in T.box_ids:
            b = T.box_index[box]
            special = bool(T.is_dg[b] or T.fallback[b] or T.heat[b])
            # Identical static rows include length, reefer, on/under deck,
            # crane access, IMDG static declarations and native size rules.
            key = (T.static[b].tobytes(), int(T.length[b]), int(T.rank[b]),
                   round(float(T.height[b]), 9), bool(T.closes_stack[b]),
                   problem.pod(box) if port_chains else None,
                   bool(T.e1_threshold is not None and T.mass[b] >= T.e1_threshold),
                   box if special or T.extra.get('rule_freeze_v1') or not bucket_kg
                   else ceil(float(T.mass[b])/bucket_kg))
            groups[key].append(box)
        self.groups = list(groups.values())
        self.model = cp_model.CpModel()
        self.x = {}
        self._build()

    def _build(self):
        T, p, model = self.T, self.p, self.model
        cell_terms, stack_terms = defaultdict(list), defaultdict(list)
        self.group_mass = []
        for g, members in enumerate(self.groups):
            b = T.box_index[members[0]]
            mass = max(float(T.mass[T.box_index[box]]) for box in members)
            self.group_mass.append(mass)
            for s in np.flatnonzero(T.static[b]):
                s = int(s)
                v = model.new_bool_var(f'x{g}_{s}')
                self.x[g, s] = v
                for c in T.slot_cells[s]:
                    if c >= 0:
                        cell_terms[int(c)].append((v, g))
                        stack_terms[int(T.cell_stack[c])].append((v, g, s))
            model.add(sum(self.x[g, int(s)] for s in np.flatnonzero(T.static[b])) == len(members))

        self.occ = {}
        ranks, pods, longs, heavies, closed, heights, masses = {}, {}, {}, {}, {}, {}, {}
        maxpod = max((p.pod(b) for b in T.box_ids), default=0)+1
        maxmass = ceil(max(self.group_mass, default=0)*1000)
        reps = [T.box_index[m[0]] for m in self.groups]
        freeze = bool(T.extra.get('rule_freeze_v1'))
        for c in range(len(T.cell_ids)):
            terms = cell_terms[c]
            self.occ[c] = model.new_bool_var(f'o{c}')
            model.add(self.occ[c] == sum(v for v, _ in terms))
            def feature(name, values, maximum):
                out = model.new_int_var(0, maximum, f'{name}{c}')
                model.add(out == sum(values[g]*v for v, g in terms))
                return out
            ranks[c] = feature('r', [int(T.rank[b])+1 for b in reps], 10)
            if self.port_chains:
                pods[c] = feature('p', [p.pod(m[0])+1 for m in self.groups], maxpod)
            longs[c] = feature('l', [int(T.length[b] != 20) for b in reps], 1)
            heavies[c] = feature('e', [int(T.e1_threshold is not None and T.mass[b] >= T.e1_threshold)
                                      for b in reps], 1)
            closed[c] = feature('c', [int(T.closes_stack[b]) for b in reps], 1)
            heights[c] = feature('h', [round(float(T.height[b])*1_000_000) for b in reps], 30_000_000)
            if freeze:
                masses[c] = feature('m', [ceil(m*1000) for m in self.group_mass], maxmass)

        self.roof = {}
        sight = T.extra.get('stack_visibility_top')
        weight_tol = getattr(p.engine, '_weight_tol', 0) or 0
        for j, cells in enumerate(T.stack_cells):
            if not cells:
                continue
            previous_roof = round(float(T.stack_base_z[j])*1_000_000)
            for k, c in enumerate(cells):
                roof = model.new_int_var(-100_000_000, 200_000_000, f'z{c}')
                model.add(roof == previous_roof+heights[c])
                self.roof[c] = roof
                previous_roof = roof
                if self.full_hold and not T.stack_deck[j]:
                    model.add(self.occ[c] == 1)
                if k:
                    lower = cells[k-1]
                    model.add(self.occ[c] <= self.occ[lower])
                    # No 20 above long; mixed hold columns are rejected by
                    # the checker even if the public placement mask permits.
                    model.add(longs[c] >= longs[lower]).only_enforce_if(self.occ[c])
                    if not T.stack_deck[j]:
                        model.add(longs[c] == longs[lower]).only_enforce_if(self.occ[c])
                    model.add(closed[lower]+self.occ[c] <= 1)
                    for below in cells[:k]:
                        if freeze:
                            model.add(masses[c] <= masses[below]+floor(weight_tol*1000)).only_enforce_if(self.occ[c])
                        else:
                            model.add(ranks[c] <= ranks[below]+int(T.e2_tol)).only_enforce_if(self.occ[c])
                            model.add(heavies[c] <= heavies[below])
                        if self.port_chains:
                            model.add(pods[c] <= pods[below]).only_enforce_if(self.occ[c])
            hmax = float(T.stack_hmax[j])
            if sight is not None:
                hmax = min(hmax, float(sight[j])-float(T.stack_base_z[j]))
            model.add(sum(heights[c] for c in cells) <= floor((hmax+1e-9)*1_000_000))
            short = model.new_bool_var(f'short{j}')
            long = model.new_bool_var(f'long{j}')
            vs20 = [v for v, g, s in stack_terms[j] if T.slot_n[s] == 1]
            vs40 = [v for v, g, s in stack_terms[j] if T.slot_n[s] == 2]
            model.add_max_equality(short, vs20 or [0])
            model.add_max_equality(long, vs40 or [0])
            mass_expr = sum(ceil(self.group_mass[g]*1000/int(T.slot_n[s]))*v
                            for v, g, s in stack_terms[j])
            model.add(mass_expr <= floor(float(T.stack_lim_20[j])*1000)).only_enforce_if([short, long.Not()])
            model.add(mass_expr <= floor(float(T.stack_lim_40[j])*1000)).only_enforce_if([long, short.Not()])
            model.add(mass_expr <= floor(float(T.stack_lim_mixed[j])*1000)).only_enforce_if([short, long])
            # The final placement checker chooses its twenty rating whenever
            # any twenty occurs, separately from the native mixed rating.
            model.add(mass_expr <= floor(float(T.stack_lim_20[j])*1000)).only_enforce_if(short)

        # A spanning box needs physically equal support roofs, not merely
        # the same tier ordinal. Heights use exact generated micrometres.
        slot_vars = defaultdict(list)
        for (g, s), v in self.x.items():
            slot_vars[s].append(v)
        for s, vs in slot_vars.items():
            if T.slot_n[s] != 2:
                continue
            v = model.new_bool_var(f'longslot{s}')
            model.add(v == sum(vs))
            supports = []
            for c in T.slot_cells[s]:
                j, k = int(T.cell_stack[c]), int(T.cell_k[c])
                supports.append(self.roof[T.stack_cells[j][k-1]] if k
                                else round(float(T.stack_base_z[j])*1_000_000))
            model.add(supports[0] == supports[1]).only_enforce_if(v)

        # Legacy E6 sufficient occupancy blocking: if a group uses the deck,
        # occupy at least one cell of each of its static hold opportunities.
        # Unlike full holds this also works at tiny and small fill levels.
        if not freeze and T.e6_fallback and T.e6_threshold is not None:
            for g, members in enumerate(self.groups):
                if max(p.boxes[b].gross_mass_kg for b in members) < T.e6_threshold:
                    continue
                b = T.box_index[members[0]]
                deck_vars = [v for (gg, s), v in self.x.items() if gg == g and T.slot_deck[s]]
                if not deck_vars:
                    continue
                used_deck = model.new_bool_var(f'e6deck{g}')
                model.add_max_equality(used_deck, deck_vars)
                for s in np.flatnonzero(T.static[b] & ~T.slot_deck):
                    model.add(sum(self.occ[int(c)] for c in T.slot_cells[s] if c >= 0) >= 1).only_enforce_if(used_deck)

    def assignment(self, solver):
        out = {}
        for g, members in enumerate(self.groups):
            slots = [s for (gg, s), v in self.x.items() if gg == g and solver.boolean_value(v)]
            slots.sort(key=lambda s: (int(self.T.slot_k[s, 0]), self.T.slot_ids[s]))
            boxes = sorted(members, key=lambda b: (-self.p.boxes[b].gross_mass_kg, b))
            out.update((b, self.T.slot_ids[s]) for b, s in zip(boxes, slots))
        return out

    def forbid(self, assignment):
        groupof = {b: g for g, members in enumerate(self.groups) for b in members}
        selected = {self.x[groupof[b], self.T.slot_index[s]].index for b, s in assignment.items()}
        self.model.add(sum(self.model.get_bool_var_from_proto_index(i) for i in selected) <= len(selected)-1)


def validate(problem, assignment):
    """Native mask replay and final carrier checks; never mutates its root."""
    from sequence import order_legal
    actions, stuck = order_legal(problem, assignment)
    if stuck is not None:
        return False, {'kind': 'native_order_stuck', **stuck}
    engine, pushed = problem.engine, 0
    ex = Expander(problem.instance)
    try:
        for i, action in enumerate(actions):
            if not engine.is_legal(action):
                return False, {'kind': 'mask_rejection', 'index': i,
                               'box': action.box, 'slot': action.slot,
                               'dead_end': str(engine.dead_end()) if hasattr(engine, 'dead_end') else None}
            engine.push(action)
            pushed += 1
            ex.step(action)
        if not engine.is_complete():
            return False, {'kind': 'native_incomplete'}
        reasons = securing_state_violations(problem.instance, ex)
        if reasons:
            return False, {'kind': 'securing_geometry', 'reasons': list(reasons)}
        ship = problem.instance.onboard.model_copy(update={'placements': [
            __import__('stowage.model', fromlist=['Placement']).Placement(container_id=b, slot=s)
            for b, s in assignment.items()]})
        if not e6_completion_possible(problem.instance, ship, (), call_index=problem.instance.call_index):
            return False, {'kind': 'e6_departure'}
        score = problem.score(actions)
        if score is None:
            return False, {'kind': 'physical_replay'}
        return True, {'kind': 'mask_and_carrier_complete', 'lifts': score.objective}
    finally:
        for _ in range(pushed):
            engine.pop()


def construct(instance, problem, seed, budget_s):
    """Return complete mask-legal assignments; physics/full check still pending."""
    global LAST_DIAGNOSTICS
    started = monotonic()
    deadline = started+max(0, budget_s)
    LAST_DIAGNOSTICS = {'model': 'grouped_conservative_chain_packing', 'attempts': [],
                        'omissions': ['ballast and full physics', 'full voyage checker'],
                        'restrictions': ['full hold for sufficient E6', 'group maximum mass',
                                         'optional port chains', 'integer micrometre geometry']}
    construct.last_diagnostics = LAST_DIAGNOSTICS
    if (instance.onboard.placements or instance.discharge_list
            or (problem.engine.T.extra.get('rule_freeze_v1') and
                getattr(problem.engine, '_weight_tol', None) is None)):
        LAST_DIAGNOSTICS['unsupported'] = 'nonempty arrival or discharges'
        return []
    result = []
    # Begin with coarser interchangeable cargo, then reduce conservative mass
    # padding. Failure of any arm is not an original-map infeasibility proof.
    T = problem.engine.T
    units = sum(int(v) for v in T.units)
    hold_cells = sum(len(cells) for j, cells in enumerate(T.stack_cells) if not T.stack_deck[j])
    use_full_hold = units >= hold_cells and len(T.box_ids) > 60
    arms = [(2000, True, use_full_hold), (2000, False, False), (0, False, False)]
    for arm, (bucket, chain, full_hold) in enumerate(arms):
        remaining = deadline-monotonic()
        if remaining <= .2:
            break
        packing = ChainPacking(problem, bucket_kg=bucket, port_chains=chain, full_hold=full_hold)
        row = {'bucket_kg': bucket, 'port_chains': chain, 'full_hold': full_hold, 'groups': len(packing.groups),
               'variables': len(packing.x), 'rounds': []}
        LAST_DIAGNOSTICS['attempts'].append(row)
        arm_deadline = min(deadline, monotonic()+max(1, remaining/(len(arms)-arm)))
        for iteration in range(12):
            seconds = arm_deadline-monotonic()
            if seconds <= .05:
                break
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = seconds
            solver.parameters.num_workers = 1
            solver.parameters.random_seed = (seed+arm*13+iteration) % 2_147_483_647
            solver.parameters.stop_after_first_solution = True
            status = solver.solve(packing.model)
            detail = {'status': solver.status_name(status), 'solve_s': solver.wall_time,
                      'branches': solver.num_branches, 'conflicts': solver.num_conflicts}
            row['rounds'].append(detail)
            if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
                break
            assignment = packing.assignment(solver)
            good, checked = validate(problem, assignment)
            detail['validation'] = checked
            if good:
                result.append(assignment)
                LAST_DIAGNOSTICS['wall_s'] = monotonic()-started
                return result
            packing.forbid(assignment)
    LAST_DIAGNOSTICS['wall_s'] = monotonic()-started
    return result
