"""Cycle 4 current-run page repair, copied from Cycle 3 construct.py, without full-checker calls.

Stable imports: C2 cgscale pricing/pages, C1 Frontier Problem/Sequencer, the
authorized E9 fix and C2 layout OOG repair. No mutable engine/ code is used.
Copied/adapted ideas: cgscale's root/RMP/dive and layout's asymmetric deck
permission. Search costs and reference-motion securing filters are heuristic.
Every emitted Scored has complete concrete-box coverage and legal real replay;
only the parent's unchanged full checker can establish a pass.

generate(instance, seed, budget_s, context) -> (list[Scored], metadata)
iter_generate(...) -> iterator of (Scored, candidate_metadata)

context['settings'] may supply dictionaries containing deck_tiers,
vertical_weight, vertical_power, hold_reward, seed_offset, lcg_target,
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
    e9 = _load('_c4_page_tools_e9', LAB / 'c1/leader/e9fix.py')
    e9.install()
    pages = _load('_c4_page_tools_cg_pages', LAB / 'c2/c2-cgscale/pages.py')
    alns = _load('_c4_page_tools_stable_alns', FRONTIER / 'alns.py')
    repair = _load('_c4_page_tools_oog_repair', LAB / 'c2/c2-layout/repair.py')
    return pages, alns, repair


def _model_class(pages):
    class Model(pages.Model):
        def __init__(self, problem, *, vertical_power=1., vertical_weight=.02,
                     first_fit_noise=.25, low_first_fit=True, **kwargs):
            if not math.isfinite(vertical_power) or vertical_power <= 0.:
                raise ValueError('vertical_power must be finite and positive')
            if not math.isfinite(vertical_weight) or vertical_weight < 0.:
                raise ValueError('vertical_weight must be finite and nonnegative')
            self.vertical_power = float(vertical_power)
            self.first_fit_noise = float(first_fit_noise)
            self.low_first_fit = low_first_fit
            self.rng = random.Random(0)
            super().__init__(problem, lift_weight=vertical_weight, **kwargs)
            # One pricing DP serves a type. Its tier costs must therefore
            # match at every copied-to page, including physical base heights
            # and any fore-aft dual row; C2's shape-only type omits these.
            refined = defaultdict(list)
            for key, indices in self.types.items():
                for index in indices:
                    page = self.pages[index]
                    heights = tuple(self.physical_height(page, k) for k in range(len(page.slots)))
                    x = (self.p.cells[self.T.cell_ids[self.T.slot_cells[page.slots[0], 0]]].lcg_m
                         if self.lcg_target is not None else None)
                    refined[key+(heights, x)].append(index)
            self.types = refined
            self.type_of = {index: key for key, indices in refined.items() for index in indices}

        def port_of(self, page):
            # Adapted from c2-layout/pages.py, preserving its early-on-late
            # deck permission. No change to the underlying individual rules.
            port = self.layout.get(page.cover)
            if page.deck and self.layout_deck == 'no_restow':
                return port if port is not None and port < max(self.ports) else None
            return port if self.layout_deck or not page.deck else None

        def physical_height(self, page, tier):
            return super().height_of(page, tier)

        def height_of(self, page, tier):
            # C2's vector pricing and master costs both consume h**2/10.
            # This transformation makes both equal z**vertical_power, so
            # linear KG pricing is consistent with the master objective.
            z = max(0., self.physical_height(page, tier))
            return math.sqrt(10.*z**self.vertical_power)

        def solve_lp(self, **kwargs):
            original = pages.linprog

            def single_thread(*args, **options):
                from scipy.optimize import OptimizeWarning
                native = dict(options.pop('options', {}) or {})
                native.update(threads=1, time_limit=max(.001, self.deadline-monotonic()))
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', OptimizeWarning)
                    return original(*args, options=native, **options)

            # Only this uniquely imported experimental pages module changes;
            # no shared checker/solver provider is patched.
            pages.linprog = single_thread
            try:
                return super().solve_lp(**kwargs)
            finally:
                pages.linprog = original

        def first_fit(self, boxes, stacks, rng=None):
            if not self.low_first_fit:
                return super().first_fit(boxes, stacks, rng)
            rng = rng or self.rng
            free, opened = set(stacks), defaultdict(list)
            order = [b for b in self.order if b in boxes]
            for kind in ('long', '20'):
                for b in sorted((b for b in order if (self.T.units[b] == 1) == (kind == '20')),
                                key=lambda b: b not in self.special):
                    if monotonic() >= self.deadline:
                        return None
                    best = None
                    for index, page in enumerate(self.pages):
                        column = opened[index]
                        if (page.kind != kind or len(column) >= len(page.slots)
                                or not column and not set(page.stacks) <= free):
                            continue
                        trial = column+[b]
                        if not self.legal(page, trial) or not self.lash_ok(page, trial):
                            continue
                        # Height, rather than existing fullness, comes first.
                        # Filling the hold with heavy cargo preserves E6's
                        # constructive preference, then low roofs help roll.
                        mass_t = float(self.T.mass[b])/1000.
                        moment = mass_t*self.physical_height(page, len(column))
                        jitter = rng.uniform(0., self.first_fit_noise)*max(1., mass_t)
                        rank = (page.deck and bool(self.heavy[b]), moment+jitter,
                                page.limit_kg-sum(float(self.T.share[q]) for q in trial), index)
                        if best is None or rank < best[0]:
                            best = rank, index
                    if best is None:
                        for index, partial in opened.items():
                            if partial:
                                self.add(index, tuple(partial))
                        return None
                    index = best[1]
                    if not opened[index]:
                        free.difference_update(self.pages[index].stacks)
                    opened[index].append(b)
            chosen = []
            for index, column in opened.items():
                if column:
                    self.add(index, tuple(column))
                    key = index, tuple(column)
                    if key not in self.pool:
                        return None
                    chosen.append(self.pool[key])
            return chosen

    return Model


def default_settings():
    """Uniform family settings; caller can replace or extend this schedule."""
    return [
        dict(deck_tiers=4, vertical_weight=.02, vertical_power=1., hold_reward=3.),
        dict(deck_tiers=(4, 5, 5), vertical_weight=.02, vertical_power=1., hold_reward=3.),
        dict(deck_tiers=(4, 5, 5), vertical_weight=.06, vertical_power=1., hold_reward=1.),
        dict(deck_tiers=4, vertical_weight=.002, vertical_power=2., hold_reward=3.),
        dict(deck_tiers=(4, 5, 5), vertical_weight=.006, vertical_power=2., hold_reward=3.),
        dict(deck_tiers=5, vertical_weight=.02, vertical_power=1., hold_reward=3.),
        dict(deck_tiers=None, vertical_weight=.03, vertical_power=1., hold_reward=5.),
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
    cp.Minimize(sum(int(round(1000.*model.column_cost(model.columns[k], 3)))*x
                    for k, x in use.items()))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = max(.001, deadline-monotonic())
    solver.parameters.random_seed = seed
    status = solver.Solve(cp)
    row = dict(status=solver.StatusName(status), s=monotonic()-started, columns=len(use))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, row
    chosen = [k for k, x in use.items() if solver.Value(x)]
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
        model = Model(problem, oracle=context.get('oracle'), **options)
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
        direct = model.first_fit(set(model.boxes), set(model.stacks), model.rng)
        if direct is not None:
            selections.append(direct)
        local_selections = []
        for chosen in selections[:2]:
            if monotonic() >= attempt_deadline:
                break
            changed, local = descend(model, chosen,
                deadline=min(attempt_deadline, monotonic()+float(context.get('local_s', 4.))),
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
                deadline += pause; attempt_deadline += pause; model.deadline += pause
        try:
            root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                phase=1, deadline=min(attempt_deadline, monotonic()+max(.1, share*.35)), max_rounds=80)
            row.update(lp_uncovered=root['uncovered'], root_columns=len(model.columns))
            if root['uncovered'] <= 3. and monotonic() < attempt_deadline:
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


