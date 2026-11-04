"""Optimize concrete-box masses over a fixed occupied-slot topology.

By default only ordinary boxes of identical physical height/length, ISO type,
E2 rank, E1/E6 heavy status and reefer/empty status exchange positions. Optional
rank/heavy relaxation uses native rank and E1 ordering rows; heavy relaxation
also permits empty/loaded exchanges. Category preservation can be separately
disabled because input categories can encode rank and POD. E6 is still gated
by actual replay. DG, OOG, heat,
top-only and explicitly named instruction boxes stay fixed. Concrete static
eligibility and conservative per-stack mass rows remain enforced. This is a
restricted constructive search, never a physical infeasibility certificate.

The objective uses the native stability-loading cargo height convention,
which excludes OOG over-height. Expanded-envelope visibility KG is separately
reported. Both height fields are fixed by this occupied topology. Optional
reference-motion Fast securing rows are heuristic; the exact Fast function is
rechecked afterwards. Only the ordinary full checker can establish a pass.
"""
from __future__ import annotations

from collections import defaultdict
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent


def _construct():
    name = '_c4_permutation_construct'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / 'page_tools.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _named_ids(value, wanted):
    if isinstance(value, dict):
        return set().union(*(_named_ids(v, wanted) for v in value.values()), set())
    if isinstance(value, (tuple, list)):
        return set().union(*(_named_ids(v, wanted) for v in value), set())
    return {value} if isinstance(value, str) and value in wanted else set()


def _groups(model, assignment, *, preserve_pod=False, relax_rank=False,
            relax_heavy=False, relax_category=False):
    T, p = model.T, model.p
    named = _named_ids([c.params for c in p.instance.plan_constraints], set(assignment))
    groups = defaultdict(list)
    for box_id in sorted(assignment):
        b, box = T.box_index[box_id], p.boxes[box_id]
        protected = (T.is_dg[b] or T.fallback[b] or T.heat[b] or T.closes_stack[b]
                     or bool(box.special_stowage) or box_id in named)
        if protected:
            key = ('fixed', box_id)
        else:
            e1 = T.e1_threshold is not None and float(T.mass[b]) >= T.e1_threshold
            e6 = T.e6_threshold is not None and float(T.mass[b]) >= T.e6_threshold
            key = (box.length_ft, box.height_m, box.iso_type,
                   None if relax_rank or relax_heavy else int(T.rank[b]),
                   None if relax_heavy else bool(e1), None if relax_heavy else bool(e6),
                   box.reefer, None if relax_heavy else box.empty,
                   None if relax_category else box.category,
                   box.pod if preserve_pod else None)
        groups[key].append(box_id)
    return groups


def _positions(instance, ship):
    from stowage.conventions import cells_of_slot
    from stowage.checker.visibility import _cargo_geometry
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    occupied = {c: boxes[p.container_id] for p in ship.placements for c in cells_of_slot(p.slot)}
    floors = {}
    for stack in instance.vessel.stacks:
        z = stack.base_z_m
        for cell in stack.cell_ids:
            floors[cell] = z
            if cell in occupied:
                z += occupied[cell].height_m
    visibility = {p.slot: (bottom+top)/2
                  for p, _, _, _, bottom, top in _cargo_geometry(instance, ship)}
    output = {}
    for p in ship.placements:
        box = boxes[p.container_id]
        footprint = cells_of_slot(p.slot)
        output[p.slot] = dict(
            z=math.fsum(floors[c] for c in footprint)/len(footprint)+box.height_m/2,
            visibility_z=visibility[p.slot],
            x=math.fsum(cells[c].lcg_m for c in footprint)/len(footprint),
            y=math.fsum(cells[c].tcg_m for c in footprint)/len(footprint))
    return output


def _moments(problem, assignment, positions):
    mass = math.fsum(problem.boxes[b].gross_mass_kg for b in assignment)
    return dict(mass_kg=mass,
        stability_cargo_kg_m=math.fsum(problem.boxes[b].gross_mass_kg*positions[s]['z']
                                      for b, s in assignment.items())/mass,
        visibility_cargo_kg_m=math.fsum(problem.boxes[b].gross_mass_kg*positions[s]['visibility_z']
                                       for b, s in assignment.items())/mass,
        cargo_lcg_m=math.fsum(problem.boxes[b].gross_mass_kg*positions[s]['x']
                              for b, s in assignment.items())/mass,
        cargo_transverse_moment_tm=math.fsum(problem.boxes[b].gross_mass_kg*positions[s]['y']
                                      for b, s in assignment.items())/1000.)


def _projected_work(cp, use, model, assignment):
    """Cover opening and same-stack blockers in D15's no-later-load picture.

    This is a constructive objective, not a bound: the real projection also
    screens quay-crossing paths and later-call policy. Real replay scores all
    of those before a candidate is emitted. Pure occupied-page topology makes
    ordinary support/blocker relationships fixed during these exchanges.
    """
    T, p = model.T, model.p
    slots = set(assignment.values())
    by_slot = defaultdict(list)
    for (box, slot), variable in use.items():
        by_slot[slot].append((box, variable))
    footprint = {s: set(int(j) for j in T.slot_stacks[T.slot_index[s]] if j >= 0)
                 for s in slots}
    rank = {s: max(int(k) for k in T.slot_k[T.slot_index[s]] if k >= 0) for s in slots}
    deck = {s: bool(T.slot_deck[T.slot_index[s]]) for s in slots}
    covers = {s: {T.stack_cover[j] for j in footprint[s] if T.stack_cover[j] is not None}
              for s in slots}
    terms = []
    ports = sorted(set(model.pod.values()))
    for port in ports:
        target = {s: sum(v for b, v in by_slot[s] if model.pod[T.box_index[b]] == port)
                  for s in slots}
        later = {s: sum(v for b, v in by_slot[s] if model.pod[T.box_index[b]] > port)
                 for s in slots}
        opened = {}
        for cover in sorted(set().union(*covers.values()), key=str):
            hold = [target[s] for s in slots if not deck[s] and cover in covers[s]]
            if not hold:
                continue
            flag = cp.NewBoolVar(f'project-open-{port}-{cover}')
            for row in hold:
                cp.Add(flag >= row)
            cp.Add(flag <= sum(hold))
            opened[cover] = flag
            terms.append(round(2000*p.instance.objective.future_hatch_cover)*flag)
        for s in slots:
            blockers = [target[low] for low in slots if rank[low] < rank[s]
                        and footprint[low].intersection(footprint[s])]
            causes = blockers + [opened[c] for c in covers[s] if deck[s] and c in opened]
            if not causes:
                continue
            restow = cp.NewBoolVar(f'project-restow-{port}-{s}')
            cp.Add(restow <= later[s])
            for cause in causes:
                cp.Add(restow >= later[s]+cause-1)
            terms.append(round(1000*p.instance.objective.future_restow*
                               p.instance.objective.future_restow_lifts_per_box)*restow)
    return sum(terms)


def optimize_assignment(instance, plan, reference_physics=None, *, budget_s=10.,
                        seed=0, context=None, preserve_pod=False,
                        lcg_tolerance_m=.5, heel_limit_tm=100., use_fitted_rows=True,
                        relax_rank=False, relax_heavy=False, relax_category=False):
    """Return (Scored-or-None, metadata); every candidate has real legal replay."""
    started = monotonic()
    deadline = started+max(0., budget_s)
    context = context if context is not None else {}
    relax_rank = relax_rank or relax_heavy
    construct = _construct()
    pages, alns, _ = construct._runtime()
    from ortools.sat.python import cp_model
    from stowage.model import MovePlan, BallastCondition
    from stowage.moves import apply_plan
    if isinstance(plan, (str, Path)):
        raise TypeError('cold exchange requires a current-run MovePlan object, not a file path')
    if isinstance(plan, dict):
        plan = MovePlan.model_validate(plan.get('plan', plan.get('candidate_plan', plan)))
    if isinstance(reference_physics, (str, Path)):
        raise TypeError('cold exchange requires current-run physics, not a file path')
    if isinstance(reference_physics, dict) and 'physics' in reference_physics:
        reference_physics = reference_physics['physics']
    problem = context.get('problem') or alns.Problem(instance)
    context['problem'] = problem
    model = construct._model_class(pages)(problem, deck_tiers=None, low_first_fit=False)
    model.deadline = deadline
    chosen = construct._seed(model, plan)
    metadata = dict(entry='permutation.py:optimize_assignment', certified_physics=False,
        full_checker_passed=None, preserved='occupied slots, physical heights, length, rank and heavy status',
        status='unknown', search_cuts=[])
    if chosen is None:
        metadata.update(reason='seed outside legal pure-page family', wall_s=monotonic()-started)
        return None, metadata
    replay = apply_plan(instance, plan)
    assignment = model.assignment(chosen)
    positions = _positions(instance, replay.final_state.ship)
    baseline = _moments(problem, assignment, positions)
    groups = _groups(model, assignment, preserve_pod=preserve_pod,
                     relax_rank=relax_rank, relax_heavy=relax_heavy,
                     relax_category=relax_category)
    T = model.T
    cp = cp_model.CpModel()
    use, by_box, by_slot = {}, defaultdict(list), defaultdict(list)
    for ids in groups.values():
        slots = [assignment[b] for b in ids]
        for b in ids:
            for slot in slots:
                if not model.static[T.box_index[b], T.slot_index[slot]]:
                    continue
                variable = cp.NewBoolVar(f'{b}@{slot}')
                use[b, slot] = variable
                by_box[b].append(variable); by_slot[slot].append(variable)
                cp.AddHint(variable, int(assignment[b] == slot))
    if any(not by_box[b] for b in assignment) or any(not by_slot[s] for s in assignment.values()):
        metadata.update(reason='static eligibility leaves an uncovered row', wall_s=monotonic()-started)
        return None, metadata
    for variables in by_box.values():
        cp.AddExactlyOne(variables)
    for variables in by_slot.values():
        cp.AddExactlyOne(variables)
    if relax_rank:
        # Source Model.legal requires every upper rank <= every lower rank
        # plus the unchanged E2 tolerance. Every concrete box still has its
        # native rank; only which occupied slot holds that rank can change.
        rank_at = {slot: sum(int(T.rank[T.box_index[b]])*x
                             for (b, destination), x in use.items() if destination == slot)
                   for slot in assignment.values()}
        rank_rows = 0
        for k in chosen:
            c = model.columns[k]
            slots = [T.slot_ids[s] for s in model.pages[c.page].slots[:len(c.boxes)]]
            for upper in range(1, len(slots)):
                for lower in range(upper):
                    cp.Add(rank_at[slots[upper]] <= rank_at[slots[lower]]+int(T.e2_tol))
                    rank_rows += 1
        metadata.update(relax_rank=True, exact_e2_rows=rank_rows,
                        preserved='occupied slots, physical heights, length and E1/E6 heavy status; exact E2 rows')
    if relax_heavy:
        heavy_at = {slot: sum(int(T.e1_threshold is not None and T.mass[T.box_index[b]] >= T.e1_threshold)*x
                              for (b, destination), x in use.items() if destination == slot)
                    for slot in assignment.values()}
        heavy_rows = 0
        for k in chosen:
            c = model.columns[k]
            slots = [T.slot_ids[s] for s in model.pages[c.page].slots[:len(c.boxes)]]
            for upper in range(1, len(slots)):
                for lower in range(upper):
                    cp.Add(heavy_at[slots[upper]] <= heavy_at[slots[lower]])
                    heavy_rows += 1
        metadata.update(relax_heavy=True, exact_e1_rows=heavy_rows,
                        preserved='occupied slots, physical heights and length; exact E1/E2 rows; E6 replay gate')
    stack_terms, stack_limit = defaultdict(list), {}
    mass_g = {b: int(round(problem.boxes[b].gross_mass_kg*1000.)) for b in assignment}
    for (b, slot), variable in use.items():
        bi, si = T.box_index[b], T.slot_index[slot]
        share_g = math.ceil(float(T.share[bi])*1000.-1e-8)
        for j in T.slot_stacks[si, :int(T.slot_n[si])]:
            j = int(j)
            stack_terms[j].append(share_g*variable)
            stack_limit[j] = float(T.stack_lim_20[j] if T.slot_n[si] == 1 else T.stack_lim_40[j])
    for j, terms in stack_terms.items():
        if math.isfinite(stack_limit[j]):
            cp.Add(sum(terms) <= math.floor(stack_limit[j]*1000.+1e-8))
    x_coeff = {(b, s): mass_g[b]*int(round(positions[s]['x']*1000.)) for b, s in use}
    y_coeff = {(b, s): mass_g[b]*int(round(positions[s]['y']*1000.)) for b, s in use}
    baseline_x = sum(x_coeff[b, s] for b, s in assignment.items())
    dx = int(math.floor(sum(mass_g.values())*lcg_tolerance_m*1000.))
    cp.Add(sum(x_coeff[key]*x for key, x in use.items()) >= baseline_x-dx)
    cp.Add(sum(x_coeff[key]*x for key, x in use.items()) <= baseline_x+dx)
    dy = int(math.floor(heel_limit_tm*1e9))
    cp.Add(sum(y_coeff[key]*x for key, x in use.items()) >= -dy)
    cp.Add(sum(y_coeff[key]*x for key, x in use.items()) <= dy)
    z_coeff = {(b, s): mass_g[b]*int(round(positions[s]['z']*1000.)) for b, s in use}
    if context.get('restow_first', False):
        baseline_z = sum(z_coeff[b, s] for b, s in assignment.items())
        tolerance = float(context.get('cargo_kg_tolerance_m', 0.))
        cp.Add(sum(z_coeff[key]*x for key, x in use.items()) <=
               baseline_z+round(baseline['mass_kg']*tolerance*1e6))
        work = _projected_work(cp, use, model, assignment)
        cp.Minimize(1000000*work+sum(round(z_coeff[key]/1e6)*x for key, x in use.items()))
        metadata['objective'] = 'projected cover/stack restows, then native vertical moment; replay decides actual lifts'
        metadata['cargo_kg_tolerance_m'] = tolerance
    else:
        cp.Minimize(sum(z_coeff[key]*x for key, x in use.items()))

    oracle = context.get('oracle')
    reference_masses = None
    if reference_physics is not None:
        reference_masses = reference_physics['tank_masses_kg']
        state = replay.final_state.ship.model_copy(update={'tank_masses_kg': {
            **replay.final_state.ship.tank_masses_kg, **reference_masses}})
        lash = construct._load('_c4_permutation_fast_lash', construct.LAB / 'c1/fx-scout/lash_oracle.py')
        oracle = lash.Fast(instance, state)
        if context.get('balance_reference_tcg', False):
            from stowage.checker import stability
            from stowage.checker.stability_loading import loading_condition
            geometry, parameters = stability._context(instance.vessel)
            loading = loading_condition(instance, state, geometry=geometry, ship_params=parameters)
            if not loading.errors:
                target_tm = baseline['cargo_transverse_moment_tm']-loading.tcg_m*loading.mass_kg/1000.
                target = int(round(target_tm*1e9))
                width = int(round(float(context.get('tcg_band_tm', 2.))*1e9))
                transverse = sum(y_coeff[key]*x for key, x in use.items())
                cp.Add(transverse >= target-width)
                cp.Add(transverse <= target+width)
                metadata.update(reference_tcg_before_m=loading.tcg_m,
                                cargo_transverse_target_tm=target_tm, tcg_band_tm=width/1e9)
            else:
                metadata['reference_tcg_target_errors'] = list(loading.errors)
    deck_columns = [model.columns[k] for k in chosen if model.pages[model.columns[k].page].deck]
    reference_before = {}
    if oracle is not None:
        for c in deck_columns:
            slots = [T.slot_ids[s] for s in model.pages[c.page].slots[:len(c.boxes)]]
            reference_before[c.page] = oracle.ratio([T.box_ids[b] for b in c.boxes], slots)
    limit = context.get('reference_limit', max(reference_before.values(), default=1.))
    metadata.update(baseline=baseline, variables=len(use), groups=len(groups),
        category_preserved=not relax_category, empty_status_preserved=not relax_heavy,
        movable_groups=[len(ids) for ids in groups.values() if len(ids) > 1],
        reference_fast_before=max(reference_before.values(), default=None), reference_fast_limit=limit,
        mass_rounding='share grams rounded up, limits down; search restriction only')
    unconstrained_moment = math.fsum(
        mass*z for ids in groups.values()
        for mass, z in zip(sorted((problem.boxes[b].gross_mass_kg for b in ids), reverse=True),
                           sorted(positions[assignment[b]]['z'] for b in ids)))
    metadata['group_sorting_relaxation'] = dict(
        cargo_kg_m=unconstrained_moment/baseline['mass_kg'],
        improvement_room_m=baseline['stability_cargo_kg_m']-unconstrained_moment/baseline['mass_kg'],
        scope='these groups only, ignoring slot eligibility, stack mass, E2 ordering, moment and motion constraints')
    if oracle is not None and use_fitted_rows:
        scale = 100000000.
        for c in deck_columns:
            slots = [T.slot_ids[s] for s in model.pages[c.page].slots[:len(c.boxes)]]
            ids = [T.box_ids[b] for b in c.boxes]
            masses = [problem.boxes[b].gross_mass_kg for b in ids]
            heights = [problem.boxes[b].height_m for b in ids]
            lengths = [problem.boxes[b].length_ft for b in ids]
            original = reference_before[c.page]
            weights = []
            for k in range(len(ids)):
                trial = list(masses); trial[k] += 100.
                weights.append(max(0., (oracle.ratio_of(trial, heights, lengths, slots)-original)/100.))
            terms, baseline_lhs = [], 0
            for k, slot in enumerate(slots):
                baseline_lhs += round(weights[k]*masses[k]*scale)
                for b in assignment:
                    if (b, slot) in use:
                        terms.append(int(round(weights[k]*problem.boxes[b].gross_mass_kg*scale))*use[b, slot])
            cp.Add(sum(terms) <= baseline_lhs+math.floor((limit-original)*scale+1e-6))
    metadata['setup_s'] = monotonic()-started
    best = None
    for iteration in range(3):
        remaining = deadline-monotonic()
        if remaining <= 0.:
            metadata['reason'] = 'construction budget exhausted'
            break
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = remaining if iteration == 2 else max(.001, .65*remaining)
        solver.parameters.random_seed = seed+iteration
        status = solver.Solve(cp)
        metadata.setdefault('solves', []).append(dict(status=solver.StatusName(status),
            s=solver.WallTime(), objective=solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None))
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            metadata['reason'] = 'restricted assignment model has no retained incumbent; no physical proof'
            break
        candidate = {b: slot for (b, slot), x in use.items() if solver.Value(x)}
        at_slot = {slot: b for b, slot in candidate.items()}
        invalid_columns = []
        for k in chosen:
            original = model.columns[k]
            page = model.pages[original.page]
            slots = [T.slot_ids[s] for s in page.slots[:len(original.boxes)]]
            boxes = tuple(T.box_index[at_slot[slot]] for slot in slots)
            if not model.legal(page, boxes):
                pairs = [(at_slot[slot], slot) for slot in slots]
                cp.Add(sum(use[pair] for pair in pairs) <= len(pairs)-1)
                invalid_columns.append(pairs)
        if invalid_columns:
            metadata['search_cuts'].extend(dict(kind='original_column_gate_no_good',
                certified_physics=False, assignment=pairs) for pairs in invalid_columns)
            continue
        candidate_plan = problem.score(alns.Sequencer(problem).order(candidate))
        if candidate_plan is None:
            pairs = list(candidate.items())
            cp.Add(sum(use[pair] for pair in pairs) <= len(pairs)-1)
            metadata['search_cuts'].append(dict(kind='deterministic_replay_no_good',
                certified_physics=False, assignment=pairs,
                scope='this constructive replay only, not proof of physical infeasibility'))
            metadata['reason'] = 'candidate failed real expansion/replay; search-only no-repeat cut'
            continue
        new_chosen = construct._seed(model, candidate_plan.plan)
        if new_chosen is None:
            metadata['reason'] = 'candidate failed original exact column gates'
            break
        after = {}
        bad = []
        if oracle is not None:
            for k in new_chosen:
                c = model.columns[k]
                if not model.pages[c.page].deck:
                    continue
                slots = [T.slot_ids[s] for s in model.pages[c.page].slots[:len(c.boxes)]]
                ratio = oracle.ratio([T.box_ids[b] for b in c.boxes], slots)
                after[c.page] = ratio
                if ratio > limit+1e-8:
                    bad.append([(T.box_ids[b], slot) for b, slot in zip(c.boxes, slots)])
        if bad:
            for pairs in bad:
                cp.Add(sum(use[pair] for pair in pairs) <= len(pairs)-1)
                metadata['search_cuts'].append(dict(kind='reference_fast_column_no_good',
                    certified_physics=False, assignment=pairs))
            continue
        replay_after = apply_plan(instance, candidate_plan.plan)
        if (not replay_after.legal or set(candidate) != set(instance.load_list)
                or set(candidate.values()) != set(assignment.values())):
            metadata['reason'] = 'candidate lost full coverage or occupied topology'
            break
        metrics = _moments(problem, candidate, positions)
        metrics.update(stability_cargo_kg_change_m=metrics['stability_cargo_kg_m']-baseline['stability_cargo_kg_m'],
            visibility_cargo_kg_change_m=metrics['visibility_cargo_kg_m']-baseline['visibility_cargo_kg_m'])
        metadata.pop('reason', None)
        metadata.update(status='candidate', candidate=metrics, complete_replay_legal=True,
            reference_fast_after=max(after.values(), default=None), lifts=candidate_plan.objective,
            changed_boxes=sum(assignment[b] != candidate[b] for b in assignment))
        if reference_masses is not None:
            with_ballast = candidate_plan.plan.model_copy(update={'ballast': BallastCondition(
                tank_masses_kg=reference_masses, source='finisher')})
            candidate_plan = alns.Scored(candidate_plan.actions, candidate_plan.objective, with_ballast)
        best = candidate_plan
        break
    metadata['wall_s'] = monotonic()-started
    return best, metadata
