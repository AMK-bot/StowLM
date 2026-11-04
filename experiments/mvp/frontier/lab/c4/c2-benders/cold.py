"""Cold page construction: only instance data and this invocation's patterns.

No stored assignment, recipe, reference plan or witness is opened. Coverage and
stack conflicts form the restricted master; vertical cargo moment and projected
port/cover work guide its objective. Fitted securing and sight roofs are search
restrictions. Real replay establishes completeness; only a later full checker
can establish a pass.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import replace
import importlib.util
import math
import os
from pathlib import Path
import random
import sys
from time import monotonic
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
ROOT = LAB.parents[3]


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _runtime():
    # Set before importing numerical libraries; every private LP also requests
    # one HiGHS thread, because OMP alone does not constrain HiGHS.
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        os.environ[name] = '1'
    sys.path[:0] = [str(ROOT/'src'), str(LAB.parent), str(LAB/'c1/fx-scout'),
                   str(LAB/'c1/leader')]
    _load('_c4_cold_e9', LAB/'c1/leader/e9fix.py').install()
    rmp = _load('_c4_cold_rmp', LAB/'c2/c2-cgscale/rmp_method.py')
    mixed = _load('_c4_cold_mixed', LAB/'c2/fx-scout/pages.py')
    lash = _load('_c4_cold_lash', LAB/'c2/fx-scout/lash_oracle.py')
    physics = _load('_c4_cold_physics', LAB/'c2/fx-scout/physics.py')
    physics.Fast = lash.Fast
    repair = _load('_c4_cold_repair', LAB/'c2/c2-layout/repair.py')
    for module in (rmp.base._pages, mixed):
        if getattr(module, '_c4_single_thread', False):
            continue
        original = module.linprog
        def single_thread(*args, _original=original, **kwargs):
            kwargs['options'] = {**kwargs.get('options', {}), 'threads': 1}
            return _original(*args, **kwargs)
        module.linprog = single_thread
        module._c4_single_thread = True
    import alns
    return rmp, mixed, physics, repair, alns


def layout_for(model, closed=(), *, rotation=0):
    """Port-cover graph greedy packing, adapted from C3 layout_for (no warm I/O)."""
    capacity = defaultdict(int)
    for page in model.pages:
        if page.kind == '20' and page.cover is not None and page.cover not in closed:
            capacity[page.cover] += len(page.slots)
    need = Counter()
    for b in model.boxes:
        need[model.pod[b]] += float(model.T.units[b])
    covers = sorted(capacity, key=lambda c: (-capacity[c], str(c)))
    if covers:
        shift = rotation % len(covers)
        covers = covers[shift:]+covers[:shift]
    layout = {}
    for cover in covers:
        port = max(need, key=lambda p: (need[p], -p))
        layout[cover] = port
        need[port] -= capacity[cover]
    return layout


def _model_class(base, *, mixed):
    class ColdModel(base):
        def __init__(self, problem, *, closed_hold=(), roofs=None, deck_tiers=None,
                     kg_weight=.02, **kwargs):
            self.closed_hold = frozenset(closed_hold)
            self.cold_roofs = roofs or {}
            self.cold_cap = deck_tiers
            self.kg_weight = kg_weight
            if mixed:
                kwargs.update(roofs=roofs, mixed=True)
            else:
                kwargs['deck_tiers'] = deck_tiers
                kwargs['lift_weight'] = kg_weight
            super().__init__(problem, **kwargs)
            self.seed_chosen = getattr(self, 'seed_chosen', None)
            # Pricing representatives must have the same vertical costs. Native
            # old type signatures omit base_z when no securing oracle is present.
            refined = defaultdict(list)
            for key, indices in self.types.items():
                for k in indices:
                    page = self.pages[k]
                    z = tuple(round(self.physical_height(page, tier), 6)
                              for tier in range(len(page.slots)))
                    x = self.p.cells[self.T.cell_ids[self.T.slot_cells[page.slots[0], 0]]].lcg_m
                    refined[(key, z, round(x, 6) if self.lcg_target is not None else None)].append(k)
            self.types = refined
            self.type_of = {k: key for key, indices in refined.items() for k in indices}

        def _static(self):
            out = super()._static().copy()
            T = self.T
            forbidden = [s for s in range(len(T.slot_ids)) if any(
                not T.stack_deck[int(j)] and T.stack_cover[int(j)] in self.closed_hold
                for j in T.slot_stacks[s] if j >= 0)]
            out[:, forbidden] = False
            return out

        def port_of(self, page):
            port = self.layout.get(page.cover)
            if page.deck:
                if page.cover in self.closed_hold:
                    return None
                if self.layout_deck == 'no_restow':
                    return port if port is not None and port < max(self.pod.values()) else None
                if not self.layout_deck:
                    return None
            return port

        def _capped(self, page):
            page = super()._capped(page)
            if page is None or not page.deck:
                return page
            cap = self.cold_cap
            if isinstance(cap, (tuple, list)):
                from stowage.conventions import parse_slot
                bays = sorted({parse_slot(s).bay for i, s in enumerate(self.T.slot_ids)
                               if self.T.slot_deck[i] and self.T.slot_n[i] == 1})
                bay = parse_slot(self.T.slot_ids[page.slots[0]]).bay
                cap = cap[min(2, sum(b < bay for b in bays)*3//max(1, len(bays)))]
            if mixed:
                return self._cut(page, page.hmax, tiers=cap) if cap is not None else page
            room = min((self.cold_roofs.get(j, math.inf)-float(self.T.stack_base_z[j])
                        for j in page.stacks), default=math.inf)
            hmax = min(page.hmax, room)
            n = max(0, int((hmax+1e-9)//2.591)) if math.isfinite(hmax) else len(page.slots)
            # Pure cgscale expects a nonempty page object. Keep a bottom slot
            # under an impossible height cap when the roof leaves zero tiers;
            # its unchanged legal/pricing gates then admit no pattern.
            return replace(page, slots=page.slots[:max(1, n)], hmax=hmax)

        def physical_height(self, page, tier):
            return super().height_of(page, tier)

        def height_of(self, page, tier):
            z = self.physical_height(page, tier)
            # Optimized pure pricing hard-codes lift_weight*z**2/10. Its
            # coordinate transform makes that exactly kg_weight*physical_z,
            # matching moment_cost. Mixed pricing calls moment_cost directly.
            return z if mixed else math.sqrt(10.*max(0., z))

        def moment_cost(self, page, tier, box):
            # Nominal per-tier pricing proxy; selected full patterns are scored
            # using their actual ordinary-height cargo moment in the CP master.
            return self.kg_weight*float(self.T.mass[box])/1000.*self.physical_height(page, tier)

    return ColdModel


def _selection_valid(model, chosen):
    boxes, stacks = set(), set()
    for k in chosen or ():
        c = model.columns[k]; page = model.pages[c.page]
        if (k in model.banned or boxes.intersection(c.boxes) or stacks.intersection(page.stacks)
                or not model.legal(page, c.boxes) or not model.lash_ok(page, c.boxes)):
            return False
        boxes.update(c.boxes); stacks.update(page.stacks)
    return boxes == set(model.boxes)


def _incremental_first_fit(model, *, rng, deadline, low_kg=False,
                           kind_order=('long', '20'), special20_first=False, rarity_first=False):
    """Native mass precision, with the reference Fast gate on every append.

    The original greedy checks Fast only after filling the whole ship. Here
    each tentative full column is checked immediately, and failed attempts
    still contribute their individually gated partial columns to this pool.
    """
    greedy = _load('_c4_constructor_greedy', HERE/'greedy.py')
    previous = getattr(model, 'deadline', None)
    before = len(model.columns)
    model.deadline = deadline
    try:
        chosen = greedy.first_fit(model, set(model.boxes), set(model.stacks), rng,
            low_kg=low_kg, kind_order=kind_order,
            special20_first=special20_first, rarity_first=rarity_first)
    finally:
        if previous is None:
            del model.deadline
        else:
            model.deadline = previous
    valid = _selection_valid(model, chosen)
    return chosen if valid else None, dict(
        placed=len(model.boxes) if valid else None,
        reason='complete' if valid else str(getattr(model, 'last_refused', 'no_complete_seed')),
        partial_columns=len(model.columns)-before)


def _direct_seeds(model, *, seed, deadline, seconds=12., retries=16):
    stop = min(deadline, monotonic()+max(0., seconds))
    started = monotonic(); attempts = []
    for attempt in range(retries):
        if monotonic() >= stop:
            break
        low = bool(attempt % 2)
        order = ('20', 'long') if attempt % 4 >= 2 else ('long', '20')
        early = attempt % 8 < 4
        chosen, row = _incremental_first_fit(model, rng=random.Random(seed+attempt),
            deadline=stop, low_kg=low, kind_order=order,
            special20_first=early, rarity_first=True)
        row.update(attempt=attempt, low_kg=low, kind_order=order, special20_first=early,
                   rarity_first=True)
        attempts.append(row)
        if chosen is not None:
            model.seed_chosen = chosen
            return dict(status='complete', attempts=attempts, wall_s=monotonic()-started,
                        chosen=len(chosen), lcg_constraint='search target only; final ballast decides trim')
    return dict(status='no_complete_seed', attempts=attempts, wall_s=monotonic()-started,
                chosen=0)


def _vertical_tm(model, column):
    T, page = model.T, model.pages[column.page]
    floors = {int(j): float(T.stack_base_z[j]) for j in page.stacks}
    total = 0.
    for b, s in zip(column.boxes, page.slots):
        support = [int(j) for j in T.slot_stacks[s] if j >= 0]
        bottom = math.fsum(floors[j] for j in support)/len(support)
        total += float(T.mass[b])/1000.*(bottom+float(T.height[b])/2.)
        for j in support:
            floors[j] += float(T.height[b])
    return total


def _master(model, *, deadline, seconds, seed, hint=(), optimize=True):
    """Restricted exact cover with search-only KG and fixed cover/port charges."""
    from ortools.sat.python import cp_model
    started = monotonic()
    cp = cp_model.CpModel()
    use, by_box, by_stack = {}, defaultdict(list), defaultdict(list)
    for k, c in enumerate(model.columns):
        page = model.pages[c.page]
        if k in model.banned:
            continue
        use[k] = cp.NewBoolVar(f'c{k}')
        for b in c.boxes:
            by_box[b].append(use[k])
        for j in page.stacks:
            by_stack[j].append(use[k])
    if any(not by_box[b] for b in model.boxes):
        return None, dict(status='UNCOVERED_POOL', s=monotonic()-started)
    for b in model.boxes:
        cp.AddExactlyOne(by_box[b])
    for terms in by_stack.values():
        cp.AddAtMostOne(terms)
    terms = []
    if optimize:
        terms += [round(100*(model.kg_weight*_vertical_tm(model, model.columns[k])
                            +model.columns[k].cost))*v for k, v in use.items()]
        for cover in sorted({p.cover for p in model.pages if p.cover is not None}, key=str):
            opened = cp.NewBoolVar(f'hold-{cover}')
            hold = [k for k in use if model.pages[model.columns[k].page].cover == cover
                    and not model.pages[model.columns[k].page].deck]
            for k in hold:
                cp.AddImplication(use[k], opened)
            terms.append(200*opened)
            for port in model.ports:
                edge = cp.NewBoolVar(f'port-{cover}-{port}')
                for k in hold:
                    if any(model.pod[b] == port for b in model.columns[k].boxes):
                        cp.AddImplication(use[k], edge)
                terms.append(200*edge)
                if port < max(model.ports):
                    for k in use:
                        c = model.columns[k]; page = model.pages[c.page]
                        if page.cover != cover or not page.deck:
                            continue
                        later = sum(model.pod[b] > port for b in c.boxes)
                        if later:
                            both = cp.NewBoolVar(f'over-{cover}-{port}-{k}')
                            cp.Add(both >= use[k]+edge-1)
                            terms.append(200*later*both)
        cp.Minimize(sum(terms))
    hinted = set(hint)
    for k, variable in use.items():
        cp.AddHint(variable, int(k in hinted))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = max(.001, min(seconds, deadline-monotonic()))
    if monotonic() >= deadline:
        return None, dict(status='DEADLINE', s=monotonic()-started)
    status = solver.Solve(cp)
    chosen = [k for k, v in use.items() if solver.Value(v)] if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
    return chosen, dict(status=solver.StatusName(status), s=monotonic()-started,
                        columns=len(use), restricted_pool_only=True)


@contextmanager
def _root_recovery(rmp):
    """Private native-physics RMP hook; never modify the baseline or checker."""
    original = rmp.restricted_master
    history = []

    def recover(model, root_x, *, deadline, seconds=2.):
        started = monotonic()
        chosen, mip_row, cp_row = None, None, None
        if monotonic() < deadline:
            try:
                helper = _load('_c4_constructor_cover_mip', HERE/'cover_mip.py')
                solve = getattr(helper, 'recover', None) or getattr(helper, 'restricted_master')
                chosen, mip_row = solve(model, root_x, deadline=deadline,
                                        seconds=min(30., max(0., deadline-monotonic())))
                if not _selection_valid(model, chosen):
                    chosen = None
            except (ValueError, RuntimeError, KeyError, TypeError, OSError) as error:
                mip_row = dict(status='ERROR', error=f'{type(error).__name__}: {error}')
        if chosen is None and monotonic() < deadline:
            chosen, cp_row = original(model, root_x, deadline=deadline,
                                      seconds=min(10., max(0., deadline-monotonic())))
            if not _selection_valid(model, chosen):
                chosen = None
        row = dict(status='FEASIBLE' if chosen is not None else 'NO_RETAINED_INCUMBENT',
                   seconds=monotonic()-started, chosen=len(chosen) if chosen is not None else 0,
                   mip=mip_row, cp=cp_row, restricted_pool_only=True, certified_physics=False)
        if getattr(model, 'lcg_target', None) is not None:
            row['root_lp_lcg_excess_tm'] = sum(float(value)*model.lcg_moment(model.columns[k])
                                               for k, value in root_x.items())+float(model.fixed_moment)
        history.append(row)
        return chosen, row

    rmp.restricted_master = recover
    try:
        yield history
    finally:
        rmp.restricted_master = original


def _pack(problem, model, runtime, *, seed, deadline, max_rounds, plain=False,
          soft_only=False, native_recovery=False):
    rmp, _, _, repair, alns = runtime
    if plain:
        # Preserve the proven cold constructor exactly: native types, initial
        # first-fit pool, quadratic pricing, RMP and original eight-retreat dive.
        # New physical objectives begin only after this reference exists.
        native_started = monotonic()
        if native_recovery:
            with _root_recovery(rmp) as recovery:
                scored, row = rmp.one_stow(problem, model, seed=seed, deadline=deadline)
            row['integer_recovery'] = recovery
        else:
            scored, row = rmp.one_stow(problem, model, seed=seed, deadline=deadline)
        row.pop('lp_proved', None)
        row.update(native_cgscale_reference=True, lp_is_physical_certificate=False,
                   wall_s=monotonic()-native_started,
                   complete_replay_legal=scored is not None,
                   status='complete_replay_legal' if scored else 'no_native_cold_stow')
        return scored, row
    started = monotonic()
    active = dict(active_boxes=set(model.boxes), active_stacks=set(model.stacks))
    root = model.generate(**active, phase=1, deadline=min(deadline, started+.42*(deadline-started)),
                          max_rounds=max_rounds)
    row = dict(lp_uncovered=float(root['uncovered']), lp_rounds=root['rounds'],
               columns=len(model.columns), lp_is_physical_certificate=False)
    hint = getattr(model, 'seed_chosen', None)
    if not _selection_valid(model, hint):
        hint, row['coverage_master'] = rmp.restricted_master(model, root['x'], deadline=deadline, seconds=3.)
    chosen = hint
    if not plain and monotonic() < deadline-1.:
        # Clear the inherited coverage incumbent so it cannot bypass all cost
        # generation. Keep it separately as fallback and integer-master hint.
        model.seed_chosen = None
        if not soft_only:
            model.generate(**active, phase=3, deadline=min(deadline-1., monotonic()+.32*(deadline-monotonic())),
                           max_rounds=min(16, max_rounds))
        better, row['cost_master'] = _master(model, deadline=deadline, seconds=min(5., .3*(deadline-monotonic())),
                                            seed=seed, hint=hint or ())
        chosen = better if _selection_valid(model, better) else hint
    if not _selection_valid(model, chosen) and monotonic() < deadline:
        model.seed_chosen = None
        chosen, row['dive'] = model.dive(deadline=deadline, rng=random.Random(seed),
                                       retreats=8, rounds=min(6, max_rounds))
    if not _selection_valid(model, chosen):
        row.update(status='no_restricted_stow', wall_s=monotonic()-started)
        return None, row
    chosen, heel = model.balance(chosen, random.Random(seed), rounds=800)
    assignment = model.assignment(chosen)
    if hasattr(model, 'clear_oog'):
        assignment = repair.repair_oog(problem, model, model.clear_oog(assignment))
    if set(assignment) != set(problem.instance.load_list) or problem.mixes_hold_stack(assignment):
        row.update(status='coverage_or_hold_mix_rejected', wall_s=monotonic()-started)
        return None, row
    actions = alns.Sequencer(problem).order(assignment)
    scored = problem.score(actions) if actions is not None else None
    row.update(status='complete_replay_legal' if scored else 'replay_rejected',
               heel_search_tm=heel, wall_s=monotonic()-started, columns=len(model.columns),
               complete_replay_legal=scored is not None, lifts=scored.objective if scored else None)
    return scored, row


def _metrics(instance, scored):
    from stowage.conventions import cells_of_slot
    from stowage.moves import apply_plan
    replay = apply_plan(instance, scored.plan)
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    occupied = {c: boxes[p.container_id] for p in replay.final_state.ship.placements for c in cells_of_slot(p.slot)}
    floor = {}
    for stack in instance.vessel.stacks:
        z = stack.base_z_m
        for c in stack.cell_ids:
            floor[c] = z
            if c in occupied:
                z += occupied[c].height_m
    mass = vertical = longitudinal = 0.
    for p in replay.final_state.ship.placements:
        box = boxes[p.container_id]; support = cells_of_slot(p.slot)
        mass += box.gross_mass_kg
        vertical += box.gross_mass_kg*(math.fsum(floor[c] for c in support)/len(support)+box.height_m/2.)
        longitudinal += box.gross_mass_kg*math.fsum(cells[c].lcg_m for c in support)/len(support)
    return replay.final_state.ship, dict(cargo_kg_m=vertical/mass, cargo_lcg_m=longitudinal/mass,
        cargo_mass_kg=mass, cargo_height_convention='native ordinary height, no OOG overheight',
        parts=replay.count.model_dump(mode='json'))


def _fresh_reference(instance, scored, tables, physics, *, deadline):
    """Derive actual tank masses from this run's stow, never a saved recipe."""
    from stowage.checker import stability
    affine = _load('_c4_constructor_affine', LAB/'c2/c2-benders/ballast.py')
    native = _load('_c4_constructor_native_physics', HERE/'physics.py')
    floor = stability._minimum_loading_gm(instance)
    result = affine.solve_ballast(instance, scored.plan,
        budget_s=min(15., max(0., deadline-monotonic())), max_iterations=3,
        search_gm_floor=floor, exact_recheck=False)
    candidate = result.pop('candidate_plan', None)
    # apply_plan ignores MovePlan.ballast; the ordinary _ballast resolver in
    # PhysicsEvaluator is required before generating motions or sight roofs.
    evaluator = native.PhysicsEvaluator(instance, candidate or scored.plan)
    reference = physics.Reference(instance, evaluator.ship, tables, margin=.35)
    condition = physics.visibility._loading_condition(instance, evaluator.ship)
    return reference, dict(source='fresh current-run affine ballast' if candidate is not None else 'native instance tanks fallback',
        search_gm_floor_m=floor, ballast_applied_by='PhysicsEvaluator.ship',
        gm_m=condition.gm_m, draft_aft_m=condition.t_aft_m, draft_fwd_m=condition.t_fwd_m,
        affine=result, certified_physics=False)


def _instance_reference(instance, scored, tables, physics):
    native = _load('_c4_constructor_native_physics', HERE/'physics.py')
    clean = scored.plan.model_copy(update={'ballast': None})
    ship = native.PhysicsEvaluator(instance, clean).ship
    condition = physics.visibility._loading_condition(instance, ship)
    return physics.Reference(instance, ship, tables, margin=.35), dict(
        source='native instance tanks on this current-run cargo stow',
        gm_m=condition.gm_m, draft_aft_m=condition.t_aft_m, draft_fwd_m=condition.t_fwd_m)


def _sorting_bound(instance, ship, model):
    """Rearrangement relaxation within broad same-geometry box groups."""
    permutation = _load('_c4_constructor_permutation', HERE/'permutation.py')
    assignment = {p.container_id: p.slot for p in ship.placements}
    positions = permutation._positions(instance, ship)
    groups = permutation._groups(model, assignment, relax_heavy=True, relax_category=True)
    total = math.fsum(model.p.boxes[b].gross_mass_kg for b in assignment)
    moment = math.fsum(m*z for ids in groups.values() for m, z in zip(
        sorted((model.p.boxes[b].gross_mass_kg for b in ids), reverse=True),
        sorted(positions[assignment[b]]['z'] for b in ids)))
    return dict(cargo_kg_m=moment/total, groups=len(groups),
        scope='fixed occupied topology and protected specials; ignoring static eligibility, E1/E2, stack limits, securing and moments')


def _compact_candidates(instance, problem, runtime, *, seed, deadline, context, meta):
    """Current-run legacy continuation: mixed/sight, closed cover, soft pool."""
    started = monotonic()
    stop = min(deadline, started+90.)
    meta.update(compact_after_legacy=True, live_context_key='cold_live_scored',
                compact_budget_cap_s=90., variants=[])
    live = context.get('cold_live_scored')
    if live is None or not hasattr(live, 'plan') or live.plan.instance_id != instance.id:
        meta.update(status='missing_or_mismatched_live_candidate', complete_stows=0,
                    wall_s=monotonic()-started)
        return [], meta
    from stowage.moves import apply_plan
    replay = apply_plan(instance, live.plan)
    if (not replay.legal or {p.container_id for p in replay.final_state.ship.placements}
            != set(instance.load_list)):
        meta.update(status='live_candidate_replay_or_coverage_rejected', complete_stows=0,
                    wall_s=monotonic()-started)
        return [], meta
    rmp, mixed, physics, _, _ = runtime
    try:
        reference, note = _fresh_reference(instance, live, problem.engine.T, physics,
                                           deadline=min(stop, monotonic()+10.))
    except (ValueError, RuntimeError, ArithmeticError) as error:
        reference, note = _instance_reference(instance, live, problem.engine.T, physics)
        note['affine_reference_error'] = str(error)
    meta.update(live_reference=note, live_candidate_replay_legal=True,
                live_input_provenance='explicit in-memory candidate from this invocation legacy stage',
                work_caps=dict(variants=3, lp_rounds=40, cp_seconds=5,
                               mixed_stage_s=30, closed_stage_s=22, soft_stage_s=28))
    Pure = _model_class(rmp.Model, mixed=False)
    Mixed = _model_class(mixed.Model, mixed=True)
    tools = _load('_c4_cold_page_tools', HERE/'page_tools.py')
    base_options = dict(deck_tiers=(4, 5, 5), oracle=reference.oracle,
                        lash_limit=1., kg_weight=.02, lcg_target=None)
    warm = Pure(problem, **base_options)
    initial = tools._seed(warm, live.plan)
    live_identity = tuple(sorted((p.container_id, p.slot) for p in replay.final_state.ship.placements))
    fingerprints = {live_identity}
    records = []
    for stage, seconds in (('compact_mixed_sight', 30.), ('compact_unused_cover', 22.),
                           ('compact_soft_pool', 28.)):
        if monotonic() >= stop-1.:
            break
        options = dict(base_options)
        if stage == 'compact_mixed_sight':
            options['roofs'] = reference.roofs
            model = Mixed(problem, **options)
        elif stage == 'compact_unused_cover':
            covers = sorted({p.cover for p in warm.pages if p.cover is not None}, key=str)
            closed = () if not covers else (min(covers, key=lambda c: (
                sum(len(p.slots) for p in warm.pages if p.cover == c and not p.deck and p.kind == '20'), str(c))),)
            options.update(closed_hold=closed, layout=layout_for(warm, closed), layout_deck='no_restow')
            model = Pure(problem, **options)
        else:
            model = Pure(problem, **options)
            model.seed_chosen = tools._seed(model, live.plan)
        local_stop = min(stop, monotonic()+seconds)
        warmed = rmp.base.warm_columns(model, warm, deadline=min(local_stop, monotonic()+.5))
        try:
            scored, row = _pack(problem, model, runtime, seed=seed+len(meta['variants']),
                deadline=local_stop, max_rounds=40)
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            scored, row = None, dict(status='construction_error', error=f'{type(error).__name__}: {error}')
        row.update(kind=stage, warmed_live_columns=warmed,
                   source='current-run live legacy candidate only', full_checker_passed=None)
        meta['variants'].append(row)
        if scored is None:
            continue
        ship, metrics = _metrics(instance, scored)
        identity = tuple(sorted((p.container_id, p.slot) for p in ship.placements))
        if {b for b, _ in identity} != set(instance.load_list) or identity in fingerprints:
            row['status'] = 'duplicate_or_incomplete'
            continue
        fingerprints.add(identity)
        row.update(metrics, candidate_id=f'cold-{len(records):03d}', complete_replay_legal=True,
                   loaded=len(identity), required=len(instance.load_list))
        records.append(dict(scored=scored, candidate_id=row['candidate_id'], metadata=dict(row)))
    meta.update(status='complete' if records else 'no_new_complete_stow',
                complete_stows=len(records), wall_s=monotonic()-started,
                live_seed_columns=len(initial or ()))
    return records, meta


def _plain_stage_deadline(*, started, now, deadline, budget_s, boxes):
    if boxes > 150:
        return min(deadline, started+min(120., max(50., .4*budget_s)))
    return min(deadline, now+min(50., max(30., .30*budget_s)))


def cold_candidates(instance, seed=0, budget_s=120., context=None):
    """Return (records, aggregate); record['scored'] is a complete replay-legal Scored.

    Only _cold_problem (created here after E9), bounded scalar options and the
    method_metadata output are normally consulted. The explicit compact mode
    additionally permits cold_live_scored from this invocation's legacy stage;
    its instance identity, real replay and full coverage are revalidated.
    Stored rows and general warm plan/assignment/recipe fields are ignored.
    """
    started = monotonic(); deadline = started+max(0., float(budget_s))
    context = context if context is not None else {}
    meta = dict(entry='cold.py:cold_candidates', cold_start=True, full_checker_passes=None,
                complete_stows=0, variants=[], seed=seed, budget_s=budget_s,
                stored_data_consulted=False, physics_certificates=False)
    context.setdefault('method_metadata', {})['cold'] = meta
    if instance.onboard.placements:
        meta.update(status='unsupported_nonempty_arrival', wall_s=monotonic()-started)
        return [], meta
    if budget_s <= 0:
        meta.update(status='budget_exhausted', wall_s=monotonic()-started)
        return [], meta
    runtime = _runtime(); rmp, mixed, physics, _, alns = runtime
    # An externally supplied Problem might predate E9 or carry warm caches.
    problem = alns.Problem(instance)
    context['_cold_problem'] = problem
    context['problem'] = problem
    if context.get('cold_compact_after_legacy', False):
        return _compact_candidates(instance, problem, runtime, seed=seed, deadline=deadline,
                                   context=context, meta=meta)
    max_variants = max(1, min(10, int(context.get('cold_max_variants', 6))))
    max_candidates = max(1, min(10, int(context.get('cold_max_candidates', 5))))
    max_rounds = max(1, min(150, int(context.get('cold_max_rounds', 60))))
    meta['work_caps'] = dict(variants=max_variants, candidates=max_candidates, lp_rounds_per_stage=max_rounds,
                             cost_rounds=16, cp_seconds=5, dive_retreats=8, balance_rounds=800,
                             native_reference_lp_rounds=400, native_reference_cost_rounds=30,
                             native_reference_balance_rounds=4000,
                             direct_greedy_retries=16, direct_greedy_s=min(30., max(0., float(context.get('cold_greedy_s', 12.)))))
    records, fingerprints, previous = [], set(), None
    current_plan = None
    reference = None; default_reference = None; base_lcg = None
    physics_complete = False
    Pure = _model_class(rmp.Model, mixed=False)
    Mixed = _model_class(mixed.Model, mixed=True)
    for attempt in range(max_variants):
        if monotonic() >= deadline-1. or len(records) >= max_candidates:
            break
        plain = attempt == 0
        c1_fallback = attempt == 3 and not physics_complete
        native = attempt <= 1 or attempt == 2 or c1_fallback
        design = dict(deck_tiers=None, kg_weight=.02)
        kind = 'plain'
        use_mixed = False
        if not plain:
            design.update(deck_tiers=(4, 5, 5), kg_weight=.06,
                          oracle=reference.oracle if reference else None, lash_limit=1.,
                          roofs=reference.roofs if reference else {},
                          lcg_target=base_lcg if attempt < 3 else base_lcg-1.5 if base_lcg else None)
            kind = 'physics_low_kg'
            if attempt == 1:
                kind = 'native_instance_physics'
                # First find a native-geometry packing at the live reference
                # motions. The new KG objective must not block completeness.
                selected_reference = default_reference or reference
                design.update(lcg_target=base_lcg-1.5 if base_lcg is not None else None,
                              oracle=selected_reference.oracle if selected_reference else None,
                              roofs=selected_reference.roofs if selected_reference else {})
            if attempt == 2:
                kind = 'native_affine_physics'
                design.update(lash_limit=1., lcg_target=base_lcg-1.5 if base_lcg is not None else None)
            elif attempt == 3:
                if c1_fallback:
                    kind = 'c1_native_fallback'
                    selected_reference = default_reference or reference
                    design.update(oracle=selected_reference.oracle if selected_reference else None)
                else:
                    kind = 'closed_cover'
                    temporary = Pure(problem, **design)
                    covers = sorted({p.cover for p in temporary.pages if p.cover is not None}, key=str)
                    closed = () if not covers else (min(covers, key=lambda c: (
                        sum(len(p.slots) for p in temporary.pages if p.cover == c and not p.deck and p.kind == '20'), str(c))),)
                    design.update(closed_hold=closed, layout=layout_for(temporary, closed, rotation=seed),
                                  layout_deck='no_restow')
            elif attempt >= 4:
                kind = 'mixed_physics' if attempt == 4 else 'mixed_port_cover'
                use_mixed = True
                if attempt > 4:
                    temporary = Mixed(problem, **design)
                    design.update(layout=layout_for(temporary, rotation=seed), layout_deck='no_restow')
        build_started = monotonic()
        if native:
            native_options = {} if plain else {k: v for k, v in design.items()
                                               if k not in ('kg_weight', 'roofs')}
            if c1_fallback:
                c1_pages = _load('_c4_constructor_c1_pages', LAB/'c1/fx-scout/pages.py')
                c1_method = _load('_c4_constructor_c1_method', LAB/'c1/fx-scout/method.py')
                original_lp = c1_pages.linprog
                def one_thread_lp(*args, _original=original_lp, **kwargs):
                    kwargs['options'] = {**kwargs.get('options', {}), 'threads': 1}
                    return _original(*args, **kwargs)
                c1_pages.linprog = one_thread_lp
                model = c1_pages.Model(problem, **native_options)
            else:
                model = rmp.Model(problem, **native_options)
            if not plain:
                original_dive = model.dive
                def larger_tail(*args, _dive=original_dive, _n=len(model.boxes), **kwargs):
                    kwargs['tail_boxes'] = min(40, max(10, _n//3))
                    return _dive(*args, **kwargs)
                model.dive = larger_tail
        else:
            model = (Mixed if use_mixed else Pure)(problem, **design)
        # Reuse only columns generated during this same invocation, through the
        # destination model's original gates; never read an incumbent file.
        warmed = 0
        if previous is not None and not use_mixed and not native:
            warmed = rmp.base.warm_columns(model, previous, deadline=min(deadline, monotonic()+1.))
        if attempt == 2 and not native and current_plan is not None:
            tools = _load('_c4_cold_page_tools', HERE/'page_tools.py')
            model.seed_chosen = tools._seed(model, current_plan)
        remaining = deadline-monotonic()
        allowance = min(50., max(30., .30*budget_s)) if plain else min(60., max(10., remaining/(max_variants-attempt)))
        if attempt in (1, 2):
            allowance = min(70., max(10., .30*budget_s))
        if c1_fallback:
            allowance = min(80., max(0., remaining))
        local_deadline = min(deadline, monotonic()+allowance)
        if plain:
            local_deadline = _plain_stage_deadline(started=started, now=monotonic(),
                deadline=deadline, budget_s=budget_s, boxes=len(instance.load_list))
        direct = None
        if native and not plain:
            greedy = _load('_c4_constructor_greedy', HERE/'greedy.py')
            model.deadline = local_deadline
            model.first_fit = lambda boxes, stacks, rng=None, _model=model, _greedy=greedy: (
                _greedy.first_fit(_model, boxes, stacks, rng,
                                  special20_first=True, rarity_first=True))
            direct = _direct_seeds(model, seed=seed+attempt, deadline=local_deadline,
                seconds=min(30., max(0., float(context.get('cold_greedy_s', 12.)))))
        try:
            pack_runtime = (SimpleNamespace(one_stow=c1_method.one_stow), *runtime[1:]) if c1_fallback else runtime
            scored, row = _pack(problem, model, pack_runtime, seed=seed+5 if c1_fallback else seed+attempt,
                                deadline=local_deadline, max_rounds=max_rounds, plain=native,
                                soft_only=attempt == 2 and not native,
                                native_recovery=native and not plain and not c1_fallback)
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            scored, row = None, dict(status='construction_error', error=f'{type(error).__name__}: {error}')
        row.update(variant=attempt, kind=kind, mixed=use_mixed, warmed_columns=warmed,
                   direct_greedy=direct,
                   reference_kind='native instance tanks' if attempt == 1 or c1_fallback else 'fresh affine ballast' if not plain else None,
                   model_family=('unmodified_cgscale_rmp' if plain else 'c1_pages_with_current_run_greedy'
                                 if c1_fallback else 'cgscale_rmp_with_current_run_greedy' if native else 'cold_low_kg'),
                   model_build_s=monotonic()-build_started-row.get('wall_s', 0.),
                   settings={} if plain else {k: v for k, v in design.items() if k not in ('oracle', 'roofs')})
        meta['variants'].append(row)
        previous = model if not use_mixed else previous
        if scored is None:
            continue
        ship, metrics = _metrics(instance, scored)
        row.update(metrics)
        if context.get('cold_sorting_bound', True):
            row['group_sorting_relaxation'] = _sorting_bound(instance, ship, model)
        if {p.container_id for p in ship.placements} != set(instance.load_list):
            row.update(status='final_coverage_rejected', complete_replay_legal=False)
            continue
        assignment = tuple(sorted((p.container_id, p.slot) for p in ship.placements))
        if assignment in fingerprints:
            row['duplicate_assignment'] = True
            continue
        fingerprints.add(assignment)
        row.update(candidate_id=f'cold-{len(records):03d}', full_checker_passed=None,
                   complete_replay_legal=True, loaded=len(assignment),
                   required=len(instance.load_list))
        records.append(dict(scored=scored, candidate_id=row['candidate_id'], metadata=dict(row)))
        current_plan = scored.plan
        physics_complete = physics_complete or not plain
        reference_seed = scored
        # A baseline stow can violate an immutable securing bound before the
        # ballast stage gets a chance to improve its cargo. Emit a separate,
        # freshly replayed descent candidate before downstream screening.
        refine_s = max(0., min(15., float(context.get('cold_refine_s', 10.))))
        if native and refine_s and len(records) < max_candidates and monotonic() < deadline-3.:
            refine_row = dict(variant=attempt, kind='native_vertical_descent',
                              source_candidate_id=row['candidate_id'], full_checker_passed=None)
            try:
                tools = _load('_c4_cold_page_tools', HERE/'page_tools.py')
                refined, descent = tools.refine(instance, scored.plan, seed=seed+100+attempt,
                    budget_s=min(refine_s, max(0., deadline-monotonic()-1.)),
                    context=dict(problem=problem, lcg_tolerance_m=2., heel_limit_tm=100.),
                    settings=dict(deck_tiers=None, vertical_weight=.02, hold_reward=3.))
                refine_row['descent'] = descent
                if refined is not None:
                    refined_ship, refined_metrics = _metrics(instance, refined)
                    identity = tuple(sorted((p.container_id, p.slot) for p in refined_ship.placements))
                    if (identity not in fingerprints and {b for b, _ in identity} == set(instance.load_list)):
                        fingerprints.add(identity)
                        refine_row.update(refined_metrics, candidate_id=f'cold-{len(records):03d}',
                            complete_replay_legal=True, loaded=len(identity), required=len(instance.load_list),
                            lifts=refined.objective)
                        if context.get('cold_sorting_bound', True):
                            refine_row['group_sorting_relaxation'] = _sorting_bound(instance, refined_ship, model)
                        records.append(dict(scored=refined, candidate_id=refine_row['candidate_id'],
                                            metadata=dict(refine_row)))
                        reference_seed = refined
                    else:
                        refine_row['status'] = 'duplicate_or_incomplete'
                else:
                    refine_row['status'] = 'no_refined_candidate'
            except (ValueError, RuntimeError, KeyError, TypeError) as error:
                refine_row.update(status='refinement_error', error=f'{type(error).__name__}: {error}')
            meta['variants'].append(refine_row)
        base_lcg = base_lcg if base_lcg is not None else metrics['cargo_lcg_m']
        try:
            default_reference, row['instance_tank_reference'] = _instance_reference(
                instance, reference_seed, problem.engine.T, physics)
            reference, row['reference'] = _fresh_reference(instance, reference_seed, problem.engine.T,
                                                         physics, deadline=deadline)
            row['reference_source'] = row['reference']['source']
        except (ValueError, RuntimeError, ArithmeticError) as error:
            row['reference_error'] = str(error)
    meta.update(status='complete' if records else 'no_complete_stow', complete_stows=len(records),
                wall_s=monotonic()-started, numerical_threads=1)
    return records, meta
