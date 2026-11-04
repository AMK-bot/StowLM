"""A* branch-and-bound over the shared feasibility constraint model.

A* prioritizes a partial plan by paid lifts plus an admissible estimate of lifts
still needed. A lower bound can underestimate: it may never overestimate, or it
could discard the optimum (Hart, Nilsson & Raphael, 1968, *A Formal Basis for the
Heuristic Determination of Minimum Cost Paths*). Here the residual counting
bound is evaluated on the actual partial world, including open covers, remaining
discharges and newly loaded through cargo. Each quay box must incur a restow-on
lift; this is disjoint from the bound's aboard-box clearance lifts. All bound
components are integer counts times
rational objective weights. Root and residual bounds are alternatives, never
added, since they can describe the same lifts.

Once current discharges finish, v2 cannot remove a hold box. Its occupied
cover/POD pairs therefore give an additional irreversible future-cover bound;
this stronger bound is specific to v2 and is not used by the physical oracle.
A short greedy public-mask rollout first seeks a valid incumbent; it does not
change the exhaustive A* frontier or any lower bound. The public solve_exact
entry point runs construction, bounds, native calls and final validation under
a hard parent watchdog. Cooperative clocks here stop normal work promptly;
the parent stops its owned worker if a native call does not return.

The default public v2 engine defines the search domain; no diagnostic options
or private engine state are used unless an explicit feasibility_config requests
a diagnostic domain. The config and actual global_screen diagnostics are
reported; diagnostic replay does not claim production G1.
APPROXIMATION: OPTIMAL concerns this ENGINE CONSTRAINT MODEL (including its
public actions, empirical linear screening and stop-at-completion convention),
not all exact physical solutions. D49 uses necessary-direction (outward) margins
for screening, not conservative inward margins. Their residual bounds are
empirically qualified, not a universal proof, so screening can still exclude a
physical optimum outside that qualification. Stopping at completion also
excludes voluntary later shifts. Actual accepted plans still require own
ballast, the full exact checker, and replay
through the same configured engine. Unknown ballast terminals retain bounds.
With rule freeze v1 active, equal weighted-move terminals are compared using
the checker's heavy-on-deck count, then killed-slot count. Equal-cost frontier
states remain searchable until those preferences cannot improve; this does not
claim the continuous ballast/stability-margin optimum.

Dominance keeps the cheapest path to a canonical physical world. Symmetry only
renames containers whose entire semantic record matches, including geometry,
DG, POD, mass and special flags, AND whose initial manifest roles and initial
onboard slot match. The latter preserves inherited-rule exemptions. Merely
sharing a category is insufficient. Such an identity permutation preserves the
engine's pending-load counts, static rules, physical moves, ballast and objective.
Quay order is irrelevant. Identity-sensitive instructions are unsupported.
The public engine does not expose its path-dependent LP reference state, so
dominance also retains the canonical semantic action history. Different action
orders are not assumed equivalent even when their physical worlds match.
Production expansion consumes legal_index/decode_index plus legal_discharges;
no complete array of Place objects is materialized. Greedy incumbent ranking
uses public box/slot index metadata and decodes only the chosen candidate.
"""
from __future__ import annotations

from fractions import Fraction
from heapq import heappop, heappush
from itertools import count
import math
from time import monotonic as perf_counter

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.bounds import lower_bound
from stowage.conventions import cells_of_slot
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.moves import apply_move, apply_plan, initial_state
from stowage.rules.catalogue import catalogue_snapshot, rule_freeze_v1_enabled
from stowage.solver.securing_geometry import securing_geometry_violation, securing_state_violations


def _weighted_bound(instance, *, minimum_future_hatches=0):
    bound = lower_bound(instance)
    w = instance.objective
    return sum((Fraction(str(weight))*count for weight, count in (
        (w.relocate, bound.relocations), (w.restow, bound.restow_lifts),
        (w.hatch_cover, bound.hatch_cover_lifts),
        (w.future_restow, bound.future_restow_lifts),
        (w.future_hatch_cover, max(bound.future_hatch_cover_lifts, minimum_future_hatches)))), Fraction(0))


def _residual_instance(instance, state):
    aboard = {p.container_id for p in state.ship.placements}
    return instance.model_copy(update={
        'yard': state.yard, 'onboard': state.ship,
        'load_list': sorted(set(instance.load_list)-aboard),
        'discharge_list': sorted(set(instance.discharge_list)&aboard)})


def partial_lower_bound(instance, state) -> Fraction:
    """Admissible residual weighted lifts on an actual partial world.

    Paid moves are NOT counted again. Newly loaded boxes stay onboard and can
    block later discharges; boxes already discharged are absent from the
    residual discharge list. Open-cover closure is retained by the bound API.
    The immutable model_copy is intentionally not revalidated as a new input:
    a target on the quay is a legal partial state, although input snapshots
    normally require all load-list boxes in yard stacks.
    """
    residual = _residual_instance(instance, state)
    return _weighted_bound(residual) + Fraction(str(instance.objective.restow))*len(state.yard.quay_buffer)


def locked_hold_future_hatches(instance, state):
    """Future hatch lifts irrevocably forced in the public v2 action domain.

    After all current discharges, v2 has no action that removes a hold box:
    placements use open slots, and their forced cover clearance moves deck
    boxes only. Each currently occupied (cover, future POD call) therefore
    forces that cover off and on in the fixed-stow future projection. Count
    each pair once. Before discharges finish a hold blocker can leave, so this
    bound deliberately returns zero. It is NOT a full-physical-space bound:
    voluntary ship shifts in the relaxed oracle can invalidate it.
    """
    discharged = {cid for stack in state.yard.stacks for cid in stack.boxes}
    if not set(instance.discharge_list)<=discharged:
        return 0
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    pairs = set()
    for placement in state.ship.placements:
        pod = boxes[placement.container_id].pod
        call = next((i for i in range(instance.call_index+1, len(instance.rotation.calls))
                     if instance.rotation.calls[i].locode==pod), None)
        if call is None:
            continue
        for cell in cells_of_slot(placement.slot):
            stack = stacks[cells[cell].stack_id]
            if not stack.on_deck and stack.hatch_cover_id is not None:
                pairs.add((stack.hatch_cover_id, call))
    return 2*len(pairs)


def _identity_classes(instance):
    initial_slots = {p.container_id: p.slot for p in instance.onboard.placements}
    loads, discharges = set(instance.load_list), set(instance.discharge_list)
    return {box.id: (box.model_dump_json(exclude={'id'}), box.id in loads,
                     box.id in discharges, box.id in initial_slots,
                     initial_slots.get(box.id))
            for box in instance.containers}


def canonical_state_key(instance, state, *, classes=None):
    """Identity-renaming quotient, only for fully identical semantic roles."""
    classes = classes if classes is not None else _identity_classes(instance)
    return (tuple((s.key, s.length_ft, tuple(classes[c] for c in s.boxes))
                  for s in state.yard.stacks),
            tuple(sorted(classes[c] for c in state.yard.quay_buffer)),
            tuple((p.slot, classes[p.container_id]) for p in state.ship.placements),
            tuple(sorted(state.ship.covers_off.items())))


class _LegalView:
    """A public index view; Place objects are decoded only when consumed."""

    def __init__(self, engine):
        self.engine = engine
        self.indexed = all(callable(getattr(engine, name, None))
                           for name in ('legal_index', 'decode_index', 'legal_discharges'))
        if self.indexed:
            self.indices = engine.legal_index()
            self.discharges = engine.legal_discharges()
            self.fallback = ()
        else:
            self.indices, self.discharges = (), ()
            self.fallback = engine.legal_actions()

    def __len__(self):
        return len(self.indices)+len(self.discharges)+len(self.fallback)

    def iterate(self, deadline=None):
        if self.indexed:
            for flat in self.indices:
                if deadline is not None and perf_counter()>=deadline:
                    return
                yield self.engine.decode_index(int(flat))
            tail = self.discharges
        else:
            tail = self.fallback
        for action in tail:
            if deadline is not None and perf_counter()>=deadline:
                return
            yield action


def iter_legal_actions(engine, *, deadline=None):
    """Yield every public legal Place, then discharge, without eager decoding.

    Old protocol-only test doubles retain a legal_actions fallback. Production
    v2.3 never calls that object-materializing API. A caller interrupting this
    iterator must retain its prefix lower bound, since candidates remain unseen.
    """
    if deadline is not None and perf_counter()>=deadline:
        return
    yield from _LegalView(engine).iterate(deadline)


def _record_securing_rejection(metrics, reason):
    if metrics is not None:
        metrics['securing_geometry_rejections'] = metrics.get('securing_geometry_rejections', 0) + 1
        reasons = metrics.setdefault('securing_geometry_rejection_reasons', {})
        reasons[reason] = reasons.get(reason, 0) + 1


def _greedy_seed(instance, engine, ex, state, *, deadline, weights, progress=None, metrics=None):
    """A bounded depth-first incumbent attempt, never a lower-bound argument.

    Prefer accessible boxes, avoid forced restows, then load later destinations
    first so earlier discharges can sit above them. Every step uses the public
    mask; failure merely leaves the original A* frontier untouched.
    """
    from stowage.actions.space import Place
    calls = instance.rotation.calls
    freeze_enabled = rule_freeze_v1_enabled()
    def rank_place(cid, slot):
        box = ex.boxes[cid]
        # C5 exports may have no yard/ship location. Mirror Expander._place's
        # truck source only for activated, declared direct-delivery load cargo;
        # other missing locations retain their legacy error instead of gaining
        # an invented truck source. Instance validation derives this flag from
        # the current port's primary-class policy.
        where, source = (ex.where.get(cid, ('truck', 'truck'))
                         if freeze_enabled and box.direct_delivery and cid in ex.load_set
                         else ex.where[cid])
        blockers = len(ex.yard[source])-ex.yard[source].index(cid)-1 if where=='yard' else 0
        pod = next((i for i in range(instance.call_index+1, len(calls)) if calls[i].locode==box.pod), len(calls))
        stacks = [ex.stacks[ex.cells[c].stack_id] for c in cells_of_slot(slot)]
        hold = any(not stack.on_deck for stack in stacks)
        if hold:
            reason = securing_geometry_violation(instance, ex, Place(cid, slot))
            if reason is not None:
                _record_securing_rejection(metrics, reason)
                return None
        # This is seed ordering only, not a constraint or objective bound. Read
        # the same physical deck occupancy without allocating a candidate Place.
        covers = {stack.hatch_cover_id for stack in stacks if not stack.on_deck}
        forces = any(cell in ex.occ for cover in covers if cover in ex.covers
                     for sid in ex.covers[cover].deck_stack_ids for cell in ex.stacks[sid].cell_ids)
        legacy = (blockers, forces, hold, -pod, -box.gross_mass_kg, (0, cid, slot))
        if not freeze_enabled:
            return legacy
        # D69: the quay is on the high-y side when berthed to port. Fill
        # farther rows first; the public mask remains the hard crossing gate.
        transverse = ex.cells[cells_of_slot(slot)[0]].tcg_m
        far_row = transverse if instance.port.berthing_side == 'port' else -transverse
        return (blockers, forces, far_row, hold, -pod, -box.gross_mass_kg, (0, cid, slot))
    def rank(action):
        return rank_place(action.box, action.slot) if isinstance(action, Place) else (-1, 0, 0, 0, 0, space_key(action))
    space_key = get_action_space().sort_key
    actions, g = [], Fraction(0)
    while perf_counter()<deadline:
        if engine.is_complete():
            return g, engine, ex, state, tuple(actions)
        if progress is not None:
            progress('seed_search_expansion', seed_steps=len(actions))
        legal = _LegalView(engine)
        if not legal or perf_counter()>=deadline:
            return None
        if progress is not None:
            progress('seed_ranking', seed_steps=len(actions))
        if legal.indexed and all(hasattr(engine, name) for name in ('index_boxes', 'index_slots', 'n_slots')):
            # Rank scalar metadata, decode ONLY the selected Place. Discharges
            # are already objects and keep their documented relative ordering.
            best_rank, selected, selected_flat = None, None, None
            boxes, slots, n_slots = engine.index_boxes, engine.index_slots, engine.n_slots
            for flat in legal.indices:
                if perf_counter()>=deadline:
                    return None
                flat = int(flat)
                candidate_rank = rank_place(boxes[flat//n_slots], slots[flat%n_slots])
                if candidate_rank is None:
                    continue
                if best_rank is None or candidate_rank<best_rank:
                    best_rank, selected_flat = candidate_rank, flat
            for candidate in legal.discharges:
                candidate_rank = rank(candidate)
                if best_rank is None or candidate_rank<best_rank:
                    best_rank, selected, selected_flat = candidate_rank, candidate, None
            action = selected if selected_flat is None else engine.decode_index(selected_flat)
        else:
            best_rank, action = None, None
            for candidate in legal.iterate(deadline):
                candidate_rank = rank(candidate)
                if candidate_rank is not None and (best_rank is None or candidate_rank<best_rank):
                    best_rank, action = candidate_rank, candidate
        if action is None or perf_counter()>=deadline:
            return None
        if progress is not None:
            progress('seed_apply', seed_steps=len(actions))
        engine.push(action)
        moves = ex.step(action)
        for move in moves:
            state = apply_move(instance, state, move)
            g += weights[move.kind]
        actions.append(action)
        if progress is not None:
            progress('seed_search_expansion', seed_steps=len(actions))
    return None


def solve_engine(instance, *, time_limit_s, max_nodes, seed, num_workers,
                 default_relocations_only=False, feasibility_config=None):
    """Best-first B&B; exact only relative to the explicitly reported engine."""
    from stowage.solver.exact import ExactSearchResult, _validate_terminal
    from stowage.solver.securing_geometry import level_four_tiebreak
    started = perf_counter()
    deadline = started+time_limit_s
    config = feasibility_config if feasibility_config is not None else FeasibilityConfig()
    production_config = config == FeasibilityConfig()
    # Capture activation once per solve, from the same shared catalogue
    # snapshot used by the production feasibility engines.
    freeze_enabled = rule_freeze_v1_enabled(catalogue=catalogue_snapshot().catalogue)
    space = get_action_space()
    def new_engine():
        return make_feasibility_engine(instance, space) if feasibility_config is None else make_feasibility_engine(instance, space, config=config)
    root_engine = new_engine()
    construction_s = perf_counter()-started
    root_state = initial_state(instance)
    root_bound = _weighted_bound(instance)
    classes = _identity_classes(instance)
    def physical_key(state):
        return canonical_state_key(instance, state, classes=classes)
    def key(state, actions):
        history = []
        for action in actions:
            encoded = space.encode(action)
            history.append((encoded['op'], classes[action.box],
                            getattr(action, 'slot', None), getattr(action, 'to', None)))
        return physical_key(state), tuple(history)
    w = instance.objective
    raw = {'relocate': w.relocate, 'restow_shift': w.restow, 'restow_off': w.restow,
           'restow_on': w.restow, 'hatch_cover_off': w.hatch_cover,
           'hatch_cover_on': w.hatch_cover, 'load': 0., 'discharge': 0.}
    weights = {kind: Fraction(str(value)) for kind, value in raw.items()}
    def cost(moves):
        return sum((weights[m.kind] for m in moves), Fraction(0))
    serial = count()
    heuristic_cache = {}
    best = {key(root_state, ()): Fraction(0)}
    def entry(g, engine, ex, state, actions):
        physical = physical_key(state)
        if physical not in heuristic_cache:
            residual = _residual_instance(instance, state)
            heuristic_cache[physical] = _weighted_bound(residual,
                minimum_future_hatches=locked_hold_future_hatches(instance, state))
            heuristic_cache[physical] += Fraction(str(w.restow))*len(state.yard.quay_buffer)
        f = max(root_bound, g+heuristic_cache[physical])
        aboard = {p.container_id for p in state.ship.placements}
        progress = len(set(instance.load_list)&aboard)+len(set(instance.discharge_list)-aboard)
        return f, -progress, g, next(serial), engine, ex, state, actions
    queue = [entry(Fraction(0), root_engine, Expander(instance, root_state), root_state, ())]
    incumbent, plan, unknown_floor, interrupted_floor = None, None, None, None
    incumbent_soft = None
    def cannot_improve(f):
        if incumbent is None:
            return False
        # Equal-cost states may improve activated level-four preferences.
        # Both metrics are nonnegative, so (0, 0) is already unbeatable.
        return f > incumbent or (f == incumbent and
            (not freeze_enabled or incumbent_soft == (0, 0)))
    expanded = 0
    metrics = {'algorithm': 'astar_branch_and_bound', 'cp_sat_implemented': False,
               'num_workers': num_workers, 'check_physics': True, 'shippable': False,
               'checker_passed': False,
               'optimality_scope': 'engine_model_weighted_lifts', 'tertiary_certified': False,
               'physical_domain_optimality_certified': False,
               'objective_components_certified': ['weighted_nonproductive_lifts'],
               'domain_restrictions': ['public_engine_actions', 'stop_at_manifest_completion'],
               'action_space_scope': 'engine_model_public_v2',
               'default_relocations_only_requested': default_relocations_only,
               'feasibility_config': {'stability_margin_scale': config.stability_margin_scale,
                                      'lookahead': config.lookahead, 'extra': dict(config.extra)},
               'mask_verified': False, 'ballast_unknown': 0, 'ballast_attempts': 0,
               'securing_geometry_screen': 'necessary_irreversible_hold_prefix_and_final_stow',
               'securing_geometry_rule_id': 'iacs.member_irs.container_securing',
               'configured_engine_replay_verified': False,
               'g1_status': 'not_verified',
               'dominance_scope': 'physical_state_and_semantic_action_history',
               'exact_rejections': 0, 'mask_rejections': 0, 'dominance_pruned': 0,
               'symmetry': 'identical_semantics_manifest_role_and_initial_ship_slot',
               'independent_lower_bound': float(root_bound)}
    metrics['engine_construction_s'] = construction_s
    metrics['global_screen'] = getattr(root_engine, 'diagnostics', {}).get('global_screen', 'not_reported')
    # APPROXIMATION: D49's necessary-direction relaxation has empirical error
    # qualification only. Expose that restriction separately from full-checker
    # acceptance of the returned witness; acceptance is not a physical optimum.
    screen = metrics['global_screen']
    metrics['screening_guarantee'] = ('empirical_necessary_relaxation' if screen == 'on'
        else 'inactive' if screen.startswith('off') else 'not_reported')
    if screen == 'on':
        metrics['domain_restrictions'].append('empirically_qualified_linear_screen')
    from stowage.solver.exact_deadline import publish_checkpoint
    publish_checkpoint(ExactSearchResult('TIME_LIMIT', None, None, float(queue[0][0]),
        None, 0, perf_counter()-started, {**metrics, 'phase': 'initialized'}))
    last_progress_time, last_progress_phase = perf_counter(), 'initialized'
    def progress(phase, *, force=False, **details):
        """Publish telemetry with ONLY the prior fully validated incumbent.

        The root bound remains valid while a current node, native mask call or
        checker is unfinished. Do not infer a frontier bound from an incomplete
        successor list, or attach the unchecked candidate to this checkpoint.
        """
        nonlocal last_progress_time, last_progress_phase
        metrics.update(details)
        metrics['phase'] = phase
        now = perf_counter()
        if not force and phase==last_progress_phase and now-last_progress_time<1.:
            return
        value = None if incumbent is None else float(incumbent)
        gap = None if incumbent is None else 0. if incumbent==0 else max(0., float((incumbent-root_bound)/incumbent))
        publish_checkpoint(ExactSearchResult('TIME_LIMIT' if plan is None else 'FEASIBLE',
            value, plan, float(root_bound), gap, expanded, now-started,
            {**metrics, 'checkpoint_validated_incumbent': plan is not None}))
        last_progress_time, last_progress_phase = now, phase
    terminal_cache = {}
    seed_node = None
    if max_nodes>=100 and time_limit_s>=2 and perf_counter()<started+time_limit_s*.35:
        seeded = _greedy_seed(instance, root_engine.copy(), Expander(instance, root_state), root_state,
                              deadline=min(deadline, started+time_limit_s*.35), weights=weights,
                              progress=progress, metrics=metrics)
        if seeded is not None:
            sg, se, sx, ss, sa = seeded
            seed_node = entry(sg, se, sx, ss, sa)
            best[key(ss, sa)] = sg
            metrics['incumbent_seed_complete'] = True
            metrics['incumbent_seed_actions'] = len(sa)
    while seed_node is not None or (queue and perf_counter()<deadline and expanded<max_nodes):
        if seed_node is not None:
            f, _, g, _, engine, ex, state, actions = seed_node
            seed_node = None
        else:
            f, _, g, _, engine, ex, state, actions = heappop(queue)
        if best.get(key(state, actions)) != g:
            continue
        if cannot_improve(f):
            heappush(queue, entry(g, engine, ex, state, actions))
            break
        expanded += 1
        if engine.is_complete():
            final_ex = ex.copy()
            closing = final_ex.finish()
            candidate = space.expand(instance, actions)
            assert candidate.moves == final_ex.moves
            replay = apply_plan(instance, candidate)
            assert replay.legal and replay.executed == len(final_ex.moves)
            objective = g+cost(closing)+sum((Fraction(str(w.future_restow))*x.restow_lifts
                +Fraction(str(w.future_hatch_cover))*x.hatch_cover_lifts for x in replay.count.future), Fraction(0))
            assert math.isclose(float(objective), replay.count.weighted_nonproductive, rel_tol=1e-12, abs_tol=1e-9)
            soft = level_four_tiebreak(instance, replay.final_state.ship) if freeze_enabled else None
            if (incumbent is None or objective<incumbent or
                    freeze_enabled and objective == incumbent and soft < incumbent_soft):
                # D64 acceptance depends on intermediate lifts, just as the
                # engine's main dominance relation does. A final stow alone
                # cannot certify that a different action order also fails.
                terminal_key = key(replay.final_state, actions)
                if terminal_key not in terminal_cache:
                    progress('terminal_check', force=True, candidate_objective=float(objective))
                    securing_failures = securing_state_violations(instance, final_ex)
                    if securing_failures:
                        for reason in securing_failures:
                            _record_securing_rejection(metrics, reason)
                        verdict, ballast, provenance = 'no', None, candidate.provenance
                    else:
                        verdict, ballast, provenance = _validate_terminal(instance, candidate,
                            replay.final_state.ship, physics=True, static=True,
                            remaining=lambda: deadline-perf_counter(), seed=seed, metrics=metrics,
                            progress=progress)
                    # Every accepted identity-bearing witness must itself pass
                    # the full checker; never reuse a semantic YES certificate.
                    # UNKNOWN is only a budget/availability outcome, not proof
                    # about another candidate, even with the same history.
                    if verdict == 'no':
                        terminal_cache[terminal_key] = verdict, ballast, provenance
                else:
                    verdict, ballast, provenance = terminal_cache[terminal_key]
                if verdict=='yes':
                    candidate = candidate.model_copy(update={'ballast': ballast, 'provenance': provenance.merge(candidate.provenance)})
                    try:
                        progress('mask_replay', force=True, candidate_objective=float(objective))
                        verify = new_engine()
                        for action in space.from_plan(instance, candidate):
                            verify.push(action)
                        if not verify.is_complete():
                            raise ValueError('canonical mask replay incomplete')
                    except ValueError as error:
                        metrics['mask_rejections'] += 1
                        metrics['mask_status'] = str(error)
                        verdict = 'unknown'
                if verdict=='yes':
                    incumbent, plan = objective, candidate
                    incumbent_soft = soft
                    if freeze_enabled:
                        metrics['heavy_on_deck_count'], metrics['killed_slots'] = soft
                        metrics['objective_order'] = ['weighted_nonproductive_lifts',
                                                      'heavy_on_deck_count', 'killed_slots']
                    metrics['configured_engine_replay_verified'] = metrics['checker_passed'] = True
                    metrics['mask_verified'] = metrics['shippable'] = production_config
                    metrics['g1_status'] = 'verified' if production_config else 'configured_mask_verified_not_production'
                    progress('validated_incumbent', force=True)
                elif verdict=='unknown':
                    unknown_floor = objective if unknown_floor is None else min(unknown_floor, objective)
            continue
        # The public index mask is the shared model: exact local placement
        # rules, room/capacity and D49 necessary-direction linear rows.
        progress('search_expansion')
        legal = _LegalView(engine)
        dead = engine.dead_end() if not legal else None
        if dead is not None and 'checker.missing_vessel_data' in dead.rule_ids:
            metrics['model_unavailable'] = True
            metrics['model_unavailable_reason'] = dead.reason
            interrupted_floor = f if interrupted_floor is None else min(interrupted_floor, f)
            continue
        for action in legal.iterate(deadline):
            if perf_counter()>=deadline:
                interrupted_floor = f if interrupted_floor is None else min(interrupted_floor, f)
                break
            metrics['generated_actions'] = metrics.get('generated_actions', 0)+1
            progress('search_expansion')
            if default_relocations_only:
                from stowage.actions.space import Discharge, Relocate
                if isinstance(action, Relocate) or isinstance(action, Discharge) and action.to is not None:
                    continue
            reason = securing_geometry_violation(instance, ex, action)
            if reason is not None:
                _record_securing_rejection(metrics, reason)
                continue
            child_engine, child_ex, child_state = engine.copy(), ex.copy(), state
            child_engine.push(action)
            moves = child_ex.step(action)
            for move in moves:
                child_state = apply_move(instance, child_state, move)
            g2 = g+cost(moves)
            child_actions = (*actions, action)
            canonical = key(child_state, child_actions)
            if canonical in best and g2>=best[canonical]:
                metrics['dominance_pruned'] += 1
                continue
            item = entry(g2, child_engine, child_ex, child_state, child_actions)
            if cannot_improve(item[0]):
                continue
            best[canonical] = g2
            heappush(queue, item)
        if perf_counter()>=deadline:
            # The lazy iterator may stop before decoding the next index.
            interrupted_floor = f if interrupted_floor is None else min(interrupted_floor, f)
    while queue and best.get(key(queue[0][6], queue[0][7])) != queue[0][2]:
        heappop(queue)
    floors = ([queue[0][0]] if queue else []) + ([unknown_floor] if unknown_floor is not None else [])
    if interrupted_floor is not None:
        floors.append(interrupted_floor)
    if incumbent is not None and all(x>=incumbent for x in floors):
        status, bound = 'OPTIMAL', incumbent
    elif incumbent is not None:
        status, bound = 'FEASIBLE', min(incumbent, max(root_bound, min(floors)))
    elif not floors:
        status, bound = 'INFEASIBLE', root_bound
    else:
        status, bound = 'TIME_LIMIT', max(root_bound, min(floors))
    value = None if incumbent is None else float(incumbent)
    gap = None if incumbent is None else 0. if incumbent==0 else max(0., float((incumbent-bound)/incumbent))
    metrics['states_seen'] = len(best)
    metrics['termination'] = 'node_limit' if expanded>=max_nodes else 'deadline' if perf_counter()>=deadline else 'bound'
    return ExactSearchResult(status, value, plan, float(bound), gap, expanded, perf_counter()-started, metrics)
