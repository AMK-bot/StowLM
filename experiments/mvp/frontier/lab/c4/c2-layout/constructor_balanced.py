"""Cycle 3 low-KG page construction, without full-checker calls.

Stable imports: C2 cgscale pricing/pages, C1 Frontier Problem/Sequencer, the
authorized E9 fix and C2 layout OOG repair. No mutable engine/ code is used.
Copied/adapted ideas: cgscale's root/RMP/dive and layout's asymmetric deck
permission. Search costs and reference-motion securing filters are heuristic.
Every emitted Scored has complete concrete-box coverage and legal real replay;
only the parent's unchanged full checker can establish a pass.

generate(instance, seed, budget_s, context) -> (list[Scored], metadata)
iter_generate(...) -> iterator of (Scored, candidate_metadata)

context['settings'] may supply dictionaries containing deck_tiers,
vertical_weight, hold_reward, seed_offset, lcg_target,
lash_limit, layout, layout_deck, first_fit_noise, polish, attempt_s, rmp_s.
context['seed_plans'] supplies raw MovePlans (or their JSON dictionaries).
context['oracle'] supplies an optional fixed-motion securing oracle. It is
search guidance and never a certificate. context['problem'] may reuse the
parent's stable Problem; omit it to rebuild after E9 installation.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import random
import sys
from time import monotonic
import warnings

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
FRONTIER = LAB.parent
ROOT = LAB.parents[3]


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _runtime():
    """No numerical imports or solves occur merely by importing this file."""
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        os.environ[name] = '1'
    for path in (ROOT / 'src', FRONTIER):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    e9 = _load('_c4_construct_e9', LAB / 'c1/leader/e9fix.py')
    e9.install()
    pages = _load('_c4_construct_cg_pages', HERE / 'pages_sight.py')
    alns = _load('_c4_construct_stable_alns', FRONTIER / 'alns.py')
    repair = _load('_c4_construct_oog_repair', HERE / 'repair.py')
    return pages, alns, repair


def _model_class(pages):
    return pages.Model


def default_settings():
    """Uniform family settings; caller can replace or extend this schedule."""
    return [
        dict(deck_tiers=(3,4,5), vertical_weight=.02, hold_reward=2.),
        dict(deck_tiers=(4,5,5), vertical_weight=.04, hold_reward=1.),
        dict(deck_tiers=None, vertical_weight=.02, hold_reward=3.),
        dict(deck_tiers=(3,5,5), vertical_weight=.08, hold_reward=1.),
    ]


def _selection_valid(model, chosen):
    boxes, stacks = set(), set()
    for index in chosen:
        column = model.columns[index]
        page = model.pages[column.page]
        if (index in model.banned or boxes.intersection(column.boxes)
                or stacks.intersection(page.stacks)
                or not model.legal(page, column.boxes) or not model.lash_ok(page, column.boxes)):
            return False
        boxes.update(column.boxes)
        stacks.update(page.stacks)
    return boxes == set(model.boxes)


def _integer(model, *, deadline, seed, hint=(), exclude=()):
    from ortools.sat.python import cp_model
    started = monotonic()
    cp = cp_model.CpModel()
    use, cover, stacks = {}, defaultdict(list), defaultdict(list)
    for k, column in enumerate(model.columns):
        if k in model.banned:
            continue
        variable = cp.NewBoolVar(f'p{k}')
        use[k] = variable
        for box in column.boxes:
            cover[box].append(variable)
        for stack in model.pages[column.page].stacks:
            stacks[stack].append(variable)
        if k in hint:
            cp.AddHint(variable, 1)
        if k % 256 == 0 and monotonic() >= deadline:
            return None, dict(status='BUILD_DEADLINE', s=monotonic()-started)
    if any(not cover[b] for b in model.boxes):
        return None, dict(status='UNCOVERED_POOL', s=monotonic()-started)
    for b in model.boxes:
        cp.AddExactlyOne(cover[b])
    for variables in stacks.values():
        cp.AddAtMostOne(variables)
    for selection in exclude:
        cp.Add(sum(use[k] for k in selection if k in use) <= len(selection)-1)
    # Cost is guidance, not the checked lift objective or a physical bound.
    objective = sum(int(round(1000.*model.column_cost(model.columns[k], 3)))*x
                    for k, x in use.items())
    moment_slacks = []
    for name, coefficients, rhs in model.moment_rows(columns=use):
        # Soft construction targets retain a complete fallback. They are not
        # necessary physics inequalities or certified map lower bounds.
        scale = 100
        bound = max(1, sum(abs(round(scale*v)) for v in coefficients.values())+abs(round(scale*rhs)))
        slack = cp.NewIntVar(0, bound, 'target_'+name)
        cp.Add(sum(round(scale*v)*use[k] for k,v in coefficients.items()) <= round(scale*rhs)+slack)
        objective += 100*slack
        moment_slacks.append((name,slack))
    cp.Minimize(objective)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = max(.001, deadline-monotonic())
    solver.parameters.random_seed = seed
    status = solver.Solve(cp)
    row = dict(status=solver.StatusName(status), s=monotonic()-started, columns=len(use))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, row
    chosen = [k for k, x in use.items() if solver.Value(x)]
    row['moment_slacks_tm'] = {name:solver.Value(v)/100 for name,v in moment_slacks}
    return (chosen if _selection_valid(model, chosen) else None), row


def _warm(model, previous, *, deadline, limit=6000):
    if previous is None:
        return 0
    where = {(page.kind, page.stacks): k for k, page in enumerate(model.pages)}
    added = 0
    for column in previous.columns[-limit:]:
        if monotonic() >= deadline:
            break
        index = where.get((previous.pages[column.page].kind, previous.pages[column.page].stacks))
        if index is not None and len(column.boxes) <= len(model.pages[index].slots):
            added += bool(model.add(index, column.boxes))
    return added


def _seed(model, plan):
    from stowage.moves import apply_plan
    from stowage.model import MovePlan
    if hasattr(plan, 'plan'):
        plan = plan.plan
    if isinstance(plan, dict):
        plan = MovePlan.model_validate(plan)
    replay = apply_plan(model.p.instance, plan)
    if not replay.legal:
        return None
    T = model.T
    assignments = {T.slot_index[q.slot]: T.box_index[q.container_id]
                   for q in replay.final_state.ship.placements
                   if q.container_id in model.p.instance.load_list}
    chosen, covered = [], set()
    for k, page in enumerate(model.pages):
        pairs = [(tier, assignments[s]) for tier, s in enumerate(page.slots) if s in assignments]
        if not pairs or any(b in covered for _, b in pairs):
            continue
        if [tier for tier, _ in pairs] != list(range(len(pairs))):
            continue
        boxes = tuple(b for _, b in pairs)
        model.add(k, boxes)
        if (k, boxes) in model.pool:
            chosen.append(model.pool[k, boxes])
            covered.update(boxes)
    return chosen if _selection_valid(model, chosen) else None


def _column_geometry(model, page_index, boxes):
    """Exact geometric-half-height cargo moments for a pure whole page."""
    page, T = model.pages[page_index], model.T
    cells = [model.p.cells[T.cell_ids[c]] for c in T.slot_cells[page.slots[0], :int(T.slot_n[page.slots[0]])]]
    x = math.fsum(cell.lcg_m for cell in cells)/len(cells)
    y = math.fsum(cell.tcg_m for cell in cells)/len(cells)
    z = max(float(T.stack_base_z[j]) for j in page.stacks)
    vertical = longitudinal = transverse = mass = 0.
    for b in boxes:
        box = model.p.boxes[T.box_ids[b]]
        height = box.height_m+(box.oog.over_height_m if box.oog else 0.)
        m = box.gross_mass_kg
        vertical += m*(z+height/2)
        dx = (box.oog.over_length_fore_m-box.oog.over_length_aft_m)/2 if box.oog else 0.
        dy = (box.oog.over_width_port_m-box.oog.over_width_starboard_m)/2 if box.oog else 0.
        longitudinal += m*(x+dx)
        transverse += m*(y+dy)
        mass += m
        z += height
    return vertical, longitudinal, transverse, z, mass


def descend(model, chosen, *, deadline, passes=24, lcg_tolerance_m=.5,
            heel_limit_tm=100., roof_tolerance_m=0., protect_special=True, focus_boxes=()):
    """Lower exact cargo moment by legal swaps/transfers of whole-page boxes.

    Every move updates both affected whole columns and preserves concrete-box
    coverage. LCG and heel guards are search restrictions, not certificates.
    This avoids solving a large cold integer master to change two boxes.
    """
    started = monotonic()
    if not _selection_valid(model, chosen):
        return list(chosen), dict(status='invalid_seed', moves=0)
    columns = [model.columns[k] for k in chosen]
    geometry = [_column_geometry(model, c.page, c.boxes) for c in columns]
    total = [math.fsum(g[j] for g in geometry) for j in (0, 1, 2)]
    mass = math.fsum(g[4] for g in geometry)
    initial_moment, initial_lcg = total[0], total[1]
    roof_max = max(g[3] for g in geometry)+roof_tolerance_m
    protected = ({b for b in model.boxes if model.T.is_dg[b] or model.T.heat[b]
                  or model.T.fallback[b] or model.T.closes_stack[b]
                  or model.p.boxes[model.T.box_ids[b]].oog is not None}
                 if protect_special else set())
    moves = []
    focus = set(focus_boxes)
    examined = 0
    for iteration in range(passes):
        best = None
        for ia, a in enumerate(columns):
            pa = model.pages[a.page]
            for ib in range(ia+1, len(columns)):
                if monotonic() >= deadline:
                    break
                b = columns[ib]
                pb = model.pages[b.page]
                if pa.kind != pb.kind:
                    continue
                old_v = geometry[ia][0]+geometry[ib][0]
                old_x = geometry[ia][1]+geometry[ib][1]
                old_y = geometry[ia][2]+geometry[ib][2]

                def consider(one, two, kind):
                    nonlocal best, examined
                    examined += 1
                    if iteration == 0 and focus and not any(
                            model.T.box_ids[old] in focus and old != new
                            for old, new in list(zip(a.boxes, one))+list(zip(b.boxes, two))):
                        return
                    if (len(one) > len(pa.slots) or len(two) > len(pb.slots)
                            or not model.legal(pa, one) or not model.legal(pb, two)
                            or not model.lash_ok(pa, one) or not model.lash_ok(pb, two)):
                        return
                    ga = _column_geometry(model, a.page, one)
                    gb = _column_geometry(model, b.page, two)
                    gain = old_v-ga[0]-gb[0]
                    if gain <= 1e-5 or max(ga[3], gb[3]) > roof_max+1e-8:
                        return
                    new_x = total[1]-old_x+ga[1]+gb[1]
                    new_y = total[2]-old_y+ga[2]+gb[2]
                    if (abs(new_x-initial_lcg) > mass*lcg_tolerance_m+1e-6
                            or abs(new_y) > heel_limit_tm*1000.+1e-6):
                        return
                    rank = (gain, -abs(new_y), -max(ga[3], gb[3]))
                    if best is None or rank > best[0]:
                        best = rank, ia, ib, tuple(one), tuple(two), ga, gb, kind

                # Whole-column exchange, then single concrete-box exchanges.
                if not protected.intersection(a.boxes+b.boxes):
                    consider(b.boxes, a.boxes, 'whole_page_swap')
                for i in range(len(a.boxes)):
                    if monotonic() >= deadline:
                        break
                    if a.boxes[i] in protected:
                        continue
                    for j in range(len(b.boxes)):
                        if b.boxes[j] in protected:
                            continue
                        one, two = list(a.boxes), list(b.boxes)
                        one[i], two[j] = two[j], one[i]
                        consider(one, two, 'box_swap')
                # Lowering a roof may require changing the tier counts too.
                if (len(a.boxes) > 1 and len(b.boxes) < len(pb.slots)
                        and not (not pa.deck and pb.deck)):
                    for i, moved in enumerate(a.boxes):
                        if moved in protected:
                            continue
                        for j in range(len(b.boxes)+1):
                            consider(a.boxes[:i]+a.boxes[i+1:],
                                     b.boxes[:j]+(moved,)+b.boxes[j:], 'box_transfer')
                if (len(b.boxes) > 1 and len(a.boxes) < len(pa.slots)
                        and not (not pb.deck and pa.deck)):
                    for i, moved in enumerate(b.boxes):
                        if moved in protected:
                            continue
                        for j in range(len(a.boxes)+1):
                            consider(a.boxes[:j]+(moved,)+a.boxes[j:],
                                     b.boxes[:i]+b.boxes[i+1:], 'box_transfer')
            if monotonic() >= deadline:
                break
        if best is None:
            if iteration == 0 and focus:
                focus.clear()
                continue
            break
        rank, ia, ib, one, two, ga, gb, kind = best
        for j in range(3):
            total[j] += ga[j]+gb[j]-geometry[ia][j]-geometry[ib][j]
        first_page, second_page = columns[ia].page, columns[ib].page
        model.add(first_page, one)
        model.add(second_page, two)
        columns[ia] = model.columns[model.pool[first_page, one]]
        columns[ib] = model.columns[model.pool[second_page, two]]
        geometry[ia], geometry[ib] = ga, gb
        moves.append(dict(kind=kind, gain_kg_m=rank[0], cargo_kg_change_m=(total[0]-initial_moment)/mass))
        if monotonic() >= deadline:
            break
    result = [model.pool[c.page, c.boxes] for c in columns]
    assert _selection_valid(model, result)
    return result, dict(status='improved' if moves else 'local_stop', moves=moves,
        examined=examined, s=monotonic()-started,
        cargo_kg_change_m=(total[0]-initial_moment)/mass,
        cargo_moment_change_kg_m=total[0]-initial_moment,
        lcg_change_m=(total[1]-initial_lcg)/mass, heel_tm=total[2]/1000.)


def refine(instance, plan, *, seed=0, budget_s=8., context=None, settings=None):
    """Target one complete stow directly; return (Scored-or-None, metadata)."""
    context = context if context is not None else {}
    started = monotonic()
    pages, alns, repair = _runtime()
    problem = context.get('problem') or alns.Problem(instance)
    context['problem'] = problem
    model = _model_class(pages)(problem, oracle=context.get('oracle'),
        **(settings or dict(deck_tiers=(4, 5, 5), vertical_weight=.02, hold_reward=3.)))
    model.deadline = started+budget_s
    if model.oracle is not None:
        model.lash_limit = math.inf
    chosen = _seed(model, plan)
    if chosen is None:
        return None, dict(status='seed_outside_selected_page_family', s=monotonic()-started)
    baseline = _selection_metrics(model, chosen)
    reference_before = None
    if model.oracle is not None:
        reference_before = max((model.oracle.ratio(
            [model.T.box_ids[b] for b in model.columns[k].boxes],
            [model.T.slot_ids[s] for s in model.pages[model.columns[k].page].slots[:len(model.columns[k].boxes)]])
            for k in chosen if model.pages[model.columns[k].page].deck), default=0.)
        model.lash_limit = float(context.get('reference_lash_limit', reference_before+1e-8))
    changed, row = descend(model, chosen, deadline=model.deadline,
        passes=int(context.get('descent_passes', 24)),
        lcg_tolerance_m=float(context.get('lcg_tolerance_m', .5)),
        heel_limit_tm=float(context.get('heel_limit_tm', 100.)),
        roof_tolerance_m=float(context.get('roof_tolerance_m', 0.)),
        protect_special=context.get('protect_special', True), focus_boxes=context.get('focus_boxes', ()))
    scored, metrics = _scored(model, changed, alns, repair, seed=seed,
                              repair_oog=context.get('repair_oog', True))
    metadata = dict(row, **metrics, s=monotonic()-started,
                    reference_securing_ratio_before=reference_before)
    if scored is not None:
        guards = _postprocess_guards(baseline, metrics, context)
        metadata['postprocess_guards'] = guards
        metadata['search_guards_retained'] = all(guards.values())
        if not all(guards.values()):
            metadata['status'] = 'postprocess_guard_failed'
            # These are search preferences, not physical acceptance rules.
            # Preserve the concrete replay-legal plan for exact assessment.
            if context.get('require_postprocess_guards', False):
                return None, metadata
        if model.oracle is not None:
            assignment = {a.box: a.slot for a in scored.actions}
            after_chosen = _seed(model, scored.plan)
            if after_chosen is None:
                metadata['status'] = 'postprocess_reference_securing_rejected'
                return None, metadata
            metadata['reference_securing_ratio_after'] = max((model.oracle.ratio(
                [model.T.box_ids[b] for b in model.columns[k].boxes],
                [model.T.slot_ids[s] for s in model.pages[model.columns[k].page].slots[:len(model.columns[k].boxes)]])
                for k in after_chosen if model.pages[model.columns[k].page].deck), default=0.)
    return scored, metadata


def _selection_metrics(model, chosen):
    geometry = [_column_geometry(model, model.columns[k].page, model.columns[k].boxes) for k in chosen]
    mass = math.fsum(g[4] for g in geometry)
    return dict(cargo_kg_m=math.fsum(g[0] for g in geometry)/mass,
                cargo_lcg_m=math.fsum(g[1] for g in geometry)/mass,
                cargo_transverse_moment_tm=math.fsum(g[2] for g in geometry)/1000.,
                roof_max_m=max(g[3] for g in geometry))


def _postprocess_guards(baseline, final, context):
    return dict(lcg=abs(final['cargo_lcg_m']-baseline['cargo_lcg_m']) <= context.get('lcg_tolerance_m', .5)+1e-8,
                heel=abs(final['cargo_transverse_moment_tm']) <= context.get('heel_limit_tm', 100.)+1e-8,
                roof=final['roof_max_m'] <= baseline['roof_max_m']+context.get('roof_tolerance_m', 0.)+1e-8,
                vertical_moment=final['cargo_kg_m'] <= baseline['cargo_kg_m']+1e-8)


def _scored(model, chosen, alns, repair, *, seed, repair_oog=True):
    if not _selection_valid(model, chosen):
        return None, {}
    if hasattr(model, 'fixed_columns'):
        model.fixed_columns = []  # whole-map postprocessing frees prior dive prefixes
    chosen, heel = model.balance(chosen, random.Random(seed))
    assignment = model.clear_oog(model.assignment(chosen))
    if repair_oog:
        assignment = repair.repair_oog(model.p, model, assignment)
    if set(assignment) != set(model.p.instance.load_list) or model.p.mixes_hold_stack(assignment):
        return None, {}
    actions = alns.Sequencer(model.p).order(assignment)
    scored = model.p.score(actions) if actions is not None else None
    if scored is None:
        return None, {}
    from stowage.moves import apply_plan
    from stowage.checker.visibility import _cargo_geometry
    replay = apply_plan(model.p.instance, scored.plan)
    if not replay.legal:
        return None, {}
    aboard = {p.container_id for p in replay.final_state.ship.placements}
    if not set(model.p.instance.load_list) <= aboard:
        return None, {}
    geometry = list(_cargo_geometry(model.p.instance, replay.final_state.ship))
    mass = math.fsum(box.gross_mass_kg for _, box, *_ in geometry)
    vertical = math.fsum(box.gross_mass_kg*(bottom+top)/2
                        for _, box, _, _, bottom, top in geometry)
    lcg = math.fsum(box.gross_mass_kg*(x+((box.oog.over_length_fore_m-box.oog.over_length_aft_m)/2 if box.oog else 0.))
                    for _, box, x, _, _, _ in geometry)/mass
    transverse = math.fsum(box.gross_mass_kg*(y+((box.oog.over_width_port_m-box.oog.over_width_starboard_m)/2 if box.oog else 0.))
                          for _, box, _, y, _, _ in geometry)/1000.
    roofs = [top for _, _, _, _, _, top in geometry]
    return scored, dict(complete_replay_legal=True, full_checker_passed=None,
        lifts=scored.objective, cargo_kg_m=vertical/mass, cargo_vertical_moment_kg_m=vertical,
        cargo_lcg_m=lcg, cargo_transverse_moment_tm=transverse,
        roof_max_m=max(roofs, default=0.), heel_tm=transverse, balance_heel_tm=heel,
        cargo_kg_convention='visibility expanded-envelope geometric half-height; search proxy, not stability KG')


def iter_generate(instance, seed=0, budget_s=60., context=None):
    """Yield early complete stows; caller's work while paused does not use budget."""
    context = context if context is not None else {}
    started = monotonic()
    deadline = started+max(0., budget_s)
    meta = context.setdefault('construct_metadata', {})
    meta.update(attempts=[], complete_replay_legal_stows=0, full_checker_passes=None,
                entry='construct.py:iter_generate', family='pure stack pages; heuristic search')
    pages, alns, repair = _runtime()
    Model = _model_class(pages)
    problem = context.get('problem')
    if problem is None:
        problem = alns.Problem(instance)
        context['problem'] = problem
    if instance.onboard.placements:
        meta['reason'] = 'empty-arrival family only'
        return
    settings = context.get('settings', default_settings())
    previous = context.get('_construct_previous')
    emitted = set()
    metadata_by_assignment = context.setdefault('_construct_candidate_metadata', {})
    for attempt, requested in enumerate(settings):
        if monotonic() >= deadline:
            break
        options = dict(requested)
        share = min(float(options.pop('attempt_s', context.get('attempt_s', 20.))),
                    max(0., deadline-monotonic()))
        attempt_deadline = min(deadline, monotonic()+share)
        polish = options.pop('polish', True)
        rmp_s = float(options.pop('rmp_s', context.get('rmp_s', 2.)))
        retry_seed = seed+int(options.pop('seed_offset', attempt))
        row = dict(attempt=attempt, seed=retry_seed, settings=dict(options), candidates=[])
        meta['attempts'].append(row)
        oracle_mode = options.pop('oracle_mode','reference')
        model = Model(problem, oracle=context.get('oracle') if oracle_mode=='reference' else None, **options)
        model.rng = random.Random(retry_seed)
        model.deadline = attempt_deadline
        row['warm_columns'] = _warm(model, previous,
            deadline=min(attempt_deadline, monotonic()+min(1., share*.05)))
        selections = []
        seed_selections = set()
        for plan in context.get('seed_plans', ()):
            chosen = _seed(model, plan)
            if chosen is not None:
                selections.append(chosen)
                seed_selections.add(tuple(sorted(chosen)))
        if context.get('relaxed_seed_targets',False):
            # Optional moment targets are soft in this whole-map CP. Retain a
            # complete legal-column hint even when it misses a global target;
            # actual physics and all loads still require native full checking.
            from pages_base import Model as SeedBase
            direct = SeedBase.first_fit(model,set(model.boxes),set(model.stacks),model.rng)
        else:
            direct = model.first_fit(set(model.boxes), set(model.stacks), model.rng)
        if direct is not None:
            selections.append(direct)
        local_selections = []
        for chosen in selections[:2]:
            if monotonic() >= attempt_deadline:
                break
            changed, local = descend(model, chosen,
                deadline=min(attempt_deadline, monotonic()+float(context.get('local_s', 4.))),
                passes=int(context.get('descent_passes',4)),
                lcg_tolerance_m=float(context.get('lcg_tolerance_m', .5)),
                heel_limit_tm=float(context.get('heel_limit_tm', 100.)))
            row.setdefault('local', []).append(local)
            if changed != chosen:
                local_selections.append(changed)
        selections = local_selections+selections
        seen_selections = set()

        def candidates(chosen, lane):
            identity = tuple(sorted(chosen))
            if identity in seen_selections:
                return None
            seen_selections.add(identity)
            scored, diagnostics = _scored(model, chosen, alns, repair, seed=retry_seed,
                                         repair_oog=context.get('repair_oog', True))
            if scored is None:
                return None
            key = tuple(sorted((a.box, a.slot) for a in scored.actions))
            if key in emitted:
                return None
            emitted.add(key)
            diagnostics.update(attempt=attempt, seed=retry_seed, lane=lane, settings=row['settings'])
            metadata_by_assignment[key] = diagnostics
            meta['complete_replay_legal_stows'] += 1
            row['candidates'].append(diagnostics)
            return scored, diagnostics

        for chosen in selections:
            if tuple(sorted(chosen)) in seed_selections and not context.get('emit_seeds', False):
                continue
            candidate = candidates(chosen, 'local_vertical_descent' if chosen in local_selections
                                   else 'low_first_fit')
            if candidate is not None:
                before = monotonic()
                yield candidate
                pause = monotonic()-before
                if context.get('pause_budget',True):
                    deadline += pause; attempt_deadline += pause; model.deadline += pause
        try:
            root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                phase=1, deadline=min(attempt_deadline, monotonic()+max(.1, share*.35)), max_rounds=32)
            actual_uncovered = getattr(model,'actual_uncovered',root['uncovered'])
            row.update(lp_uncovered=root['uncovered'], actual_uncovered=actual_uncovered,
                       root_columns=len(model.columns), moment_slacks=dict(model.moment_slacks))
            if actual_uncovered <= 3. and monotonic() < attempt_deadline:
                # C2's incumbent shortcut bypasses phase-3 costs. Clear it
                # intentionally, while retaining every validated seed column.
                model.seed_chosen = None
                if polish:
                    model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                        phase=3, deadline=min(attempt_deadline, monotonic()+share*.25), max_rounds=12)
                for variant in range(int(context.get('pool_variants', 2))):
                    if monotonic() >= attempt_deadline:
                        break
                    chosen, cp_row = _integer(model, deadline=min(attempt_deadline, monotonic()+rmp_s),
                        seed=retry_seed+variant, hint=selections[0] if selections else (),
                        exclude=() if variant == 0 else selections)
                    row.setdefault('rmp', []).append(cp_row)
                    if chosen is None:
                        break
                    selections.append(chosen)
                    candidate = candidates(chosen, 'low_KG_restricted_master')
                    if candidate is not None:
                        before = monotonic()
                        yield candidate
                        pause = monotonic()-before
                        if context.get('pause_budget',True):
                            deadline += pause; attempt_deadline += pause; model.deadline += pause
                if not row['candidates'] and monotonic() < attempt_deadline:
                    chosen, dive = model.dive(deadline=attempt_deadline, rng=model.rng)
                    row['dive'] = dive
                    if chosen is not None:
                        candidate = candidates(chosen, 'low_KG_dive')
                        if candidate is not None:
                            before = monotonic()
                            yield candidate
                            pause = monotonic()-before
                            if context.get('pause_budget',True):
                                deadline += pause; attempt_deadline += pause; model.deadline += pause
        except (RuntimeError, ValueError, ArithmeticError) as error:
            row['reason'] = f'{type(error).__name__}: {error}'
        row['columns'] = len(model.columns)
        previous = model
        context['_construct_previous'] = model
    meta['construction_s'] = max(0., budget_s-(deadline-monotonic()))


def generate(instance, seed=0, budget_s=60., context=None):
    """Return low cargo-KG/roof candidates first, with per-attempt metadata."""
    context = context if context is not None else {}
    found = list(iter_generate(instance, seed=seed, budget_s=budget_s, context=context))
    found.sort(key=lambda pair: (pair[1]['cargo_kg_m'], pair[1]['roof_max_m'], pair[0].objective))
    context['construct_metadata']['candidates'] = [row for _, row in found]
    return [scored for scored, _ in found], context['construct_metadata']


def propose(instance, *, seed, budget_s, context):
    """Scorecard-compatible constructor; finishing remains the parent's gate."""
    candidates, metadata = generate(instance, seed=seed, budget_s=budget_s, context=context)
    context.setdefault('method_metadata', {})['construct'] = metadata
    return [candidate.actions for candidate in candidates]


def main():
    """Reconstruct a frozen C2 archive, repair/descend, and save every stow."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, default=0, help='zero-based frozen medium12 index')
    parser.add_argument('--input', help='override index with an exact frozen medium12 input path')
    parser.add_argument('--seed-plan', type=Path, help='raw plan JSON or a row with plan')
    parser.add_argument('--archive-dir', type=Path, default=LAB / 'c2/c2-cgscale/results/score-medium12-rmp')
    parser.add_argument('--reference-physics', type=Path, help='same-vessel tank_masses_kg for heuristic securing oracle')
    parser.add_argument('--refine-only', action='store_true')
    parser.add_argument('--budget-s', type=float, default=30.)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--deck-tiers', default='none', help='none, integer, or forward,middle,aft')
    parser.add_argument('--lcg-tolerance-m', type=float, default=.5)
    parser.add_argument('--heel-limit-tm', type=float, default=100.)
    parser.add_argument('--focus-boxes', default='', help='comma-separated boxes prioritized in first descent move')
    parser.add_argument('--out', type=Path, default=HERE / 'results/construct-cli')
    args = parser.parse_args()
    inputs = [line.strip() for line in (LAB / 'sets/medium12.txt').read_text().splitlines()
              if line.strip() and not line.lstrip().startswith('#')]
    if args.input is not None:
        if args.input not in inputs:
            parser.error('--input must appear in the frozen medium12 list')
        relative = args.input
    elif 0 <= args.index < len(inputs):
        relative = inputs[args.index]
    else:
        parser.error('--index is outside frozen medium12')
    output = args.out.resolve()
    if not output.is_relative_to(HERE):
        parser.error('output must stay inside c3/c2-benders')
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR', str(HERE / 'results/vessel-cache'))
    pages, alns, _ = _runtime()
    from stowage.model import Instance, MovePlan
    from stowage.actions.space import Place
    from stowage.moves import apply_plan
    from stowage.checker.visibility import _cargo_geometry
    path = (ROOT / relative).resolve()
    allowed = ROOT / 'experiments/mvp/strategy/blockrule/maps/train/medium'
    if not path.is_relative_to(allowed):
        raise ValueError('unexpected path in frozen medium12 list')
    instance = Instance.model_validate_json(path.read_text())
    problem = alns.Problem(instance)
    if args.seed_plan:
        raw = json.loads(args.seed_plan.read_text())
        plan = MovePlan.model_validate(raw.get('plan', raw))
        seed_source = str(args.seed_plan)
    else:
        source = args.archive_dir / f'{instance.id}.json'
        archived = json.loads(source.read_text())
        if archived.get('input_sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('archive/input binding mismatch')
        actions = archived.get('method_metadata', {}).get('replay_actions', [])
        if not actions:
            raise ValueError('this archived map supplies no complete replay actions')
        scored = problem.score(tuple(Place(a['box'], a['slot']) for a in actions))
        if scored is None:
            raise ValueError('archived actions fail current real replay')
        plan, seed_source = scored.plan, str(source)
    replay = apply_plan(instance, plan)
    wanted = set(instance.load_list)
    if not replay.legal or not wanted <= {p.container_id for p in replay.final_state.ship.placements}:
        raise ValueError('archived seed is not a complete replay-legal stow')
    (output / 'archived-seed.json').write_text(plan.model_dump_json(indent=2)+'\n')
    geometry = list(_cargo_geometry(instance, replay.final_state.ship))
    mass = math.fsum(b.gross_mass_kg for _, b, *_ in geometry)
    moment = math.fsum(b.gross_mass_kg*(lo+hi)/2 for _, b, _, _, lo, hi in geometry)
    baseline = dict(cargo_kg_m=moment/mass, cargo_vertical_moment_kg_m=moment,
                    lifts=float(replay.count.weighted_nonproductive))
    context = dict(problem=problem, seed_plans=[plan],
        lcg_tolerance_m=args.lcg_tolerance_m, heel_limit_tm=args.heel_limit_tm,
        focus_boxes=[q for q in args.focus_boxes.split(',') if q])
    if args.reference_physics:
        reference = json.loads(args.reference_physics.read_text())
        masses = reference['tank_masses_kg']
        if not set(masses) <= {t.id for t in instance.vessel.tanks}:
            raise ValueError('reference tank IDs are not from this vessel')
        ship = replay.final_state.ship.model_copy(update={'tank_masses_kg': {
            **replay.final_state.ship.tank_masses_kg, **masses}})
        oracle_module = _load('_c4_construct_lash_oracle', LAB / 'c1/fx-scout/lash_oracle.py')
        context['oracle'] = oracle_module.Fast(instance, ship)
    caps = None if args.deck_tiers == 'none' else tuple(map(int, args.deck_tiers.split(',')))
    if caps is not None and len(caps) == 1:
        caps = caps[0]
    elif caps is not None and len(caps) != 3:
        parser.error('--deck-tiers needs one or three integers')
    summary = dict(owned_pid=os.getpid(), instance_id=instance.id, input=relative,
        input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), seed_source=seed_source,
        baseline=baseline, candidates=[], full_checker_passes=None,
        source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (Path(__file__), HERE / 'pages_sight.py',
                                 FRONTIER / 'alns.py', HERE / 'repair.py')})
    print(json.dumps(dict(owned_pid=os.getpid(), baseline=baseline)), flush=True)
    started = monotonic()

    def save(scored, row):
        if scored is None:
            summary.setdefault('rejected', []).append(row)
            return
        index = len(summary['candidates'])
        row = dict(row, cargo_kg_change_m=row['cargo_kg_m']-baseline['cargo_kg_m'],
                   cargo_moment_change_kg_m=row['cargo_vertical_moment_kg_m']-moment)
        before_slots = {p.container_id: p.slot for p in replay.final_state.ship.placements}
        after_slots = {a.box: a.slot for a in scored.actions}
        row['focus_changes'] = {box: [before_slots.get(box), after_slots.get(box)]
                                for box in context['focus_boxes']}
        (output / f'candidate-{index:02d}.json').write_text(json.dumps(dict(
            plan=scored.plan.model_dump(mode='json'), metadata=row), indent=2, sort_keys=True)+'\n')
        (output / f'candidate-{index:02d}-plan.json').write_text(scored.plan.model_dump_json(indent=2)+'\n')
        summary['candidates'].append(row)
        print(json.dumps(row), flush=True)

    try:
        if args.refine_only:
            scored, row = refine(instance, plan, seed=args.seed, budget_s=args.budget_s,
                context=context, settings=dict(deck_tiers=caps, vertical_weight=.02, hold_reward=3.))
            save(scored, row)
        else:
            context['settings'] = [dict(deck_tiers=caps, vertical_weight=.03, hold_reward=3.)]
            for scored, row in iter_generate(instance, seed=args.seed, budget_s=args.budget_s, context=context):
                save(scored, row)
        summary['construction'] = context.get('construct_metadata')
    finally:
        summary['wall_s'] = monotonic()-started
        (output / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    main()
