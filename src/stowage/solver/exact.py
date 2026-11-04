"""Two clearly scoped exact yardsticks for one-call weighted lifts (task 4.6).

The default operational path is A* branch-and-bound in ``exact_engine``. It
enumerates shared-engine legal actions, ranks partial plans by paid lifts plus
an admissible residual counting bound, and merges only provably interchangeable
semantic states. Source: Hart, Nilsson & Raphael (1968),
https://ai.stanford.edu/~nilsson/OnlinePubs-Nils/PublishedPapers/astar.pdf.
Its certificate is relative to the reported engine configuration, including
D49's empirically qualified necessary-direction linear screen and stopping when
the manifest is complete. APPROXIMATION: those restrictions can exclude physical
optima; an engine certificate does not certify the full physical domain. It is
not a proof over every exact physically feasible plan.

The explicit ``check_physics=False`` path below is a separate UNSHIPPABLE
Dijkstra oracle for ``bounds.bruteforce`` comparisons. It searches the full
boxslot graph, including voluntary relocation targets and ship moves after a
departure becomes possible; one shift now can save two later-port lifts.
Source: Dijkstra (1959), *A note on two problems in connexion with graphs*.
``default_relocations_only`` removes target overrides and labels its smaller
search domain. Both paths assert exact move-engine counts and action replay.

All required boxes must load and discharge: shut-outs cannot buy fewer lifts.
Rational nonnegative weights avoid big-M constants and rounding. Operational
terminals first try current ballast, then 4.2's conservative 3.7 tank-mass LP
and exact correction search. Every accepted witness passes the full checker.
Unknown ballast searches retain bounds instead of becoming infeasibility cuts.
Accepted operational plans replay through the same public v2 engine. Default
configuration verifies gate G1; an explicit diagnostic feasibility_config is
reported separately and does not claim production G1. OPTIMAL concerns weighted lifts only;
continuous ballast and D11's tertiary margin optimum remain unproved.

D51 accepts admissible A* as the task's exact-search method. It does not waive
the current-checker, full-domain, tiny/small or D11 tie-break requirements.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction
from heapq import heappop, heappush
from itertools import count
import math
from time import monotonic as perf_counter
from typing import Any, Literal

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place, Relocate, RestowOff
from stowage.bounds import lower_bound
from stowage.conventions import format_slot, parse_slot
from stowage.frozen import current_provenance
from stowage.model import BallastCondition, Instance, MovePlan, PlannerResult
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state
from stowage.solver.exact_deadline import in_worker, run_with_deadline


@dataclass(frozen=True)
class ExactSearchResult:
    """OPTIMAL concerns weighted lifts in metrics['action_space_scope'] only."""
    status: Literal['OPTIMAL', 'FEASIBLE', 'TIME_LIMIT', 'INFEASIBLE']
    objective: float | None
    plan: MovePlan | None
    lower_bound: float
    gap: float | None
    expanded_nodes: int
    wall_time_s: float
    metrics: dict[str, Any] = field(default_factory=dict)


def _optimality_label(status, metrics):
    optimality = ('optimal for the move model' if status == 'OPTIMAL'
                  else 'optimality unproved for the move model')
    checked = 'yes' if metrics.get('checker_passed', False) else 'no'
    return f'{optimality}; checker-passed: {checked}'


def _label_result(result):
    return replace(result, metrics={**result.metrics,
        'optimality_label': _optimality_label(result.status, result.metrics)})


def _key(state):
    return (tuple((s.key, s.length_ft, tuple(s.boxes)) for s in state.yard.stacks),
            tuple(sorted(state.yard.quay_buffer)),
            tuple((p.slot, p.container_id) for p in state.ship.placements),
            tuple(sorted(state.ship.covers_off.items())))


def _slots(instance):
    cells = {c.id for c in instance.vessel.cells}
    forty = []
    for cell in sorted(cells):
        p = parse_slot(cell)
        if p.bay < 998 and format_slot(p.bay + 2, p.row, p.tier) in cells:
            forty.append(format_slot(p.bay + 1, p.row, p.tier))
    return {20: tuple(sorted(cells)), 40: tuple(forty), 45: tuple(forty)}


def _actions(instance, ex, slots, keys, default_only):
    for cid, (where, src) in sorted(ex.where.items()):
        if (where == 'yard' and cid in ex.load_set) or where in ('ship', 'quay'):
            for slot in slots[ex.boxes[cid].length_ft]:
                if where != 'ship' or slot != src:
                    yield Place(cid, slot)
        if where == 'ship':
            yield RestowOff(cid)
            if ex.boxes[cid].pod == instance.port.locode:
                yield Discharge(cid)
                if not default_only:
                    for key in keys:
                        yield Discharge(cid, key)
        if where == 'yard' and ex.yard[src][-1] == cid and not default_only:
            for key in keys:
                if key != src:
                    yield Relocate(cid, key)


def _record_terminal_part_errors(parts, metrics):
    errors = []
    counts = metrics.setdefault('terminal_part_error_counts', {})
    for name, part in sorted(parts.items()):
        if not part.ran or part.error:
            counts[name] = counts.get(name, 0) + 1
            errors.append({'part': name, 'error': (part.error or 'part did not run')[:500]})
    metrics['terminal_last_part_errors'] = errors[:32]


def _record_terminal_report(report, metrics):
    """Retain bounded failure evidence even when no incumbent can be shipped.

    D57 exposes condition-qualified parts alongside aggregate parts. Count each
    hard rule once per checker call, and retain the qualified part identity so
    departure, mid-voyage and arrival failures remain distinguishable.
    """
    metrics['terminal_checker_calls'] = metrics.get('terminal_checker_calls', 0) + 1
    _record_terminal_part_errors(report.parts, metrics)
    rules = metrics.setdefault('terminal_failure_rule_counts', {})
    for rule in sorted({v.rule_id for v in report.violations if v.severity == 'hard'}):
        rules[rule] = rules.get(rule, 0) + 1
    qualified = {name.rsplit(':', 1)[-1] for name in report.parts if name.startswith('condition:')}
    states = metrics.setdefault('terminal_failure_state_rule_counts', {})
    failures = []
    for name, part in sorted(report.parts.items()):
        if not name.startswith('condition:') and name in qualified:
            continue  # These aggregate copies carry the same condition failures.
        for rule in sorted({v.rule_id for v in part.violations if v.severity == 'hard'}):
            key = f'{name}|{rule}'
            states[key] = states.get(key, 0) + 1
            failures.append({'part': name, 'rule_id': rule})
    metrics['terminal_last_failure'] = failures[:32]


def _validate_terminal(instance, plan, ship, *, physics, static, remaining, seed, metrics, progress=None):
    """Return yes/no/unknown; only a proof may return no for ballast."""
    ballast, report, provenance = None, None, plan.provenance
    if remaining() <= 0:
        return 'unknown', None, provenance
    if physics or static:
        from stowage.checker.rules import check_static_rules
        part = check_static_rules(instance, ship, call_index=instance.call_index)
        metrics['terminal_static_checks'] = metrics.get('terminal_static_checks', 0) + 1
        _record_terminal_part_errors({'rules': part}, metrics)
        static_rules = metrics.setdefault('terminal_static_failure_rule_counts', {})
        for rule in sorted({v.rule_id for v in part.violations if v.severity == 'hard'}):
            static_rules[rule] = static_rules.get(rule, 0) + 1
        if progress is not None:
            progress('terminal_check', force=True)
        if not part.ran:
            return 'unknown', None, provenance
        if any(v.severity == 'hard' for v in part.violations):
            return 'no', None, provenance
    if not physics:
        return 'yes', None, provenance
    from stowage.checker import check
    from stowage.solver.ballast import finish_ballast
    if remaining() <= 0:
        return 'unknown', None, provenance
    ballast = BallastCondition(source='planner', tank_masses_kg={
        t.id: instance.onboard.tank_masses_kg.get(t.id, 0.)
        for t in instance.vessel.tanks if t.adjustable})
    checked_at = perf_counter()
    report = check(instance, plan.model_copy(update={'ballast': ballast}))
    metrics['terminal_checker_wall_time_s'] = metrics.get('terminal_checker_wall_time_s', 0.) + perf_counter()-checked_at
    _record_terminal_report(report, metrics)
    provenance = provenance.merge(report.provenance)
    if report.passed:
        return 'yes', ballast, provenance
    # D57 checks every voyage state; two seconds can expire during one exact
    # evaluation before ballast search even starts. Use at most the public
    # finisher's normal 30 s budget, leaving half the remaining solve budget
    # for independent full-checker validation, mask replay and other stows.
    # This changes effort allocation only, never a constraint or certificate.
    budget = min(remaining()*.5, 30.)
    if budget <= 0:
        return 'unknown', None, provenance
    metrics['ballast_attempts'] += 1
    metrics['ballast_last_budget_s'] = budget
    if progress is not None:
        progress('terminal_check', force=True)
    outcome = finish_ballast(instance, plan, time_limit_s=budget, seed=seed)
    statuses = metrics.setdefault('ballast_status_counts', {})
    statuses[outcome.status] = statuses.get(outcome.status, 0) + 1
    provenance = provenance.merge(outcome.provenance)
    if progress is not None:
        progress('terminal_check', force=True)
    if outcome.status in ('ok', 'kept') and outcome.plan is not None and outcome.plan.ballast is not None:
        ballast = outcome.plan.ballast.model_copy(update={'source': 'planner'})
        provenance = provenance.merge(outcome.plan.provenance)
        if remaining() <= 0:
            return 'unknown', None, provenance
        checked_at = perf_counter()
        report = check(instance, plan.model_copy(update={'ballast': ballast}))
        metrics['terminal_checker_wall_time_s'] += perf_counter()-checked_at
        _record_terminal_report(report, metrics)
        provenance = provenance.merge(report.provenance)
        if report.passed:
            return 'yes', ballast, provenance
        metrics['exact_rejections'] += 1
    elif outcome.status == 'infeasible_proven':
        return 'no', None, provenance
    metrics['ballast_unknown'] += 1
    return 'unknown', None, provenance


def _validate_mask(instance, plan, space, metrics):
    """Replay each candidate, not just the first cheapest physical departure.

    A bounded completion witness is conservative. Rejection therefore retains
    an unknown lower bound, rather than becoming a false infeasibility proof.
    """
    try:
        from stowage.feasibility import make_feasibility_engine
        engine = make_feasibility_engine(instance, space)
        for action in space.from_plan(instance, plan):
            engine.push(action)
        if not engine.is_complete():
            raise ValueError('feasibility replay did not reach a complete departure')
        metrics['mask_verified'] = True
        return True
    except NotImplementedError:
        metrics['mask_status'] = 'unavailable'
        return True
    except ValueError as error:
        metrics['mask_status'] = str(error)
        metrics['mask_rejections'] = metrics.get('mask_rejections', 0) + 1
        return False


def solve_exact(instance: Instance, *, time_limit_s: float = 5., max_nodes: int = 10_000,
                check_physics: bool = True, check_static_rules: bool = False,
                default_relocations_only: bool = False, seed: int = 0,
                num_workers: int = 1, feasibility_config=None) -> ExactSearchResult:
    """Solve under one hard parent wall-clock budget, including native calls.

    On expiry TIME_LIMIT retains only a published, fully validated incumbent.
    Operational witnesses include ballast, exact checker and default-mask replay;
    explicit relaxed witnesses remain unshippable. One owned worker is stopped,
    never unrelated machine processes. Fork preserves frozen overrides.
    """
    _validate_arguments(instance, time_limit_s, max_nodes, num_workers)
    if feasibility_config is not None and not check_physics:
        raise ValueError('feasibility_config applies only to operational engine solves')
    started = perf_counter()
    deadline = started+time_limit_s
    kwargs = dict(time_limit_s=time_limit_s, max_nodes=max_nodes,
                  check_physics=check_physics, check_static_rules=check_static_rules,
                  default_relocations_only=default_relocations_only, seed=seed,
                  num_workers=num_workers, feasibility_config=feasibility_config, _deadline=deadline)
    if in_worker():
        return _label_result(_solve_exact(instance, **kwargs))
    outcome = run_with_deadline(_solve_exact, (instance,), kwargs, deadline=deadline)
    finished = perf_counter()
    expired = outcome.timed_out or finished>=deadline
    result = outcome.checkpoint if expired else outcome.value
    if result is None:
        requested_config = {'mode': 'default'} if feasibility_config is None else {
            'stability_margin_scale': feasibility_config.stability_margin_scale,
            'lookahead': feasibility_config.lookahead, 'extra': dict(feasibility_config.extra)}
        result = ExactSearchResult('TIME_LIMIT', None, None, 0., None, 0, 0., {
            'check_physics': check_physics, 'shippable': False, 'mask_verified': False,
            'optimality_scope': 'engine_model_weighted_lifts' if check_physics else 'weighted_lifts_only',
            'physical_domain_optimality_certified': False,
            'tertiary_certified': False,
            'objective_components_certified': ['weighted_nonproductive_lifts'],
            'domain_restrictions': ['public_engine_actions', 'stop_at_manifest_completion'] if check_physics else [],
            'screening_guarantee': 'not_reported',
            'global_screen': 'unavailable: worker did not publish completed initialization',
            'requested_feasibility_config': requested_config,
            'phase': 'initialization_or_no_validated_incumbent', 'num_workers': 1})
    metrics = {**result.metrics, 'watchdog_timeout': expired,
               'watchdog_worker_pid': outcome.worker_pid,
               # The worker cannot publish final telemetry after a watchdog
               # kill. Use the parent clock, including worker shutdown time.
               'budget_overrun_s': max(0., finished-deadline),
               'termination': 'deadline' if expired else result.metrics.get('termination', 'bound')}
    return _label_result(replace(result, status='TIME_LIMIT' if expired else result.status,
                                wall_time_s=finished-started, metrics=metrics))


def _validate_arguments(instance, time_limit_s, max_nodes, num_workers):
    if num_workers != 1:
        raise ValueError('enumerative exact search requires num_workers=1')
    if max_nodes < 1 or not math.isfinite(time_limit_s) or time_limit_s <= 0:
        raise ValueError('search limits must be finite and positive')
    if instance.plan_calls or instance.forecasts or instance.plan_constraints:
        raise ValueError('exact search supports one call without forecasts or instructions')
    w = instance.objective
    if any(not math.isfinite(v) or v < 0 for v in (w.relocate, w.restow, w.hatch_cover, w.future_restow, w.future_hatch_cover)):
        raise ValueError('exact search requires finite nonnegative objective weights')


def _solve_exact(instance: Instance, *, time_limit_s: float = 5., max_nodes: int = 10_000,
                check_physics: bool = True, check_static_rules: bool = False,
                default_relocations_only: bool = False, seed: int = 0,
                num_workers: int = 1, _deadline: float | None = None,
                feasibility_config=None) -> ExactSearchResult:
    """Search engine-model actions, or all boxslot actions in relaxed mode.

    One worker only. Multi-call forecasts and instructions are rejected because
    their history-dependent contracts are absent from these physical labels.
    Passing check_physics=False explicitly requests an unshippable relaxation.
    """
    if num_workers != 1:
        raise ValueError('enumerative exact search requires num_workers=1')
    if max_nodes < 1 or not math.isfinite(time_limit_s) or time_limit_s <= 0:
        raise ValueError('search limits must be finite and positive')
    if instance.plan_calls or instance.forecasts or instance.plan_constraints:
        raise ValueError('exact search supports one call without forecasts or instructions')
    started = perf_counter()
    deadline = started + time_limit_s if _deadline is None else _deadline
    w = instance.objective
    raw = {'relocate': w.relocate, 'restow_shift': w.restow, 'restow_off': w.restow,
           'restow_on': w.restow, 'hatch_cover_off': w.hatch_cover,
           'hatch_cover_on': w.hatch_cover, 'load': 0., 'discharge': 0.}
    if any(not math.isfinite(v) or v < 0 for v in (*raw.values(), w.future_restow, w.future_hatch_cover)):
        raise ValueError('exact search requires finite nonnegative objective weights')
    if check_physics:
        from stowage.solver.exact_engine import solve_engine
        return solve_engine(instance, time_limit_s=max(0., deadline-perf_counter()), max_nodes=max_nodes,
                            seed=seed, num_workers=num_workers,
                            default_relocations_only=default_relocations_only,
                            feasibility_config=feasibility_config)
    weights = {k: Fraction(str(v)) for k, v in raw.items()}
    def edge_cost(moves):
        return sum((weights[m.kind] for m in moves), Fraction(0))
    independent = lower_bound(instance).weighted
    space, slots = get_action_space(), _slots(instance)
    keys = tuple(f'{b.id}-{bay:03d}-{row:02d}' for b in instance.port.yard.blocks
                 for bay in range(1, b.n_bays + 1) for row in range(1, b.n_rows + 1))
    state = initial_state(instance)
    best = {_key(state): Fraction(0)}
    serial = count()
    def entry(cost, ex, state, actions):
        # Productive loads have zero cost. Prefer progress among equal-cost
        # labels so an incumbent does not wait behind every partial stow.
        # Cost stays first: Dijkstra's lower-bound certificate is unchanged.
        aboard = {p.container_id for p in state.ship.placements}
        progress = len(set(instance.load_list) & aboard) + len(set(instance.discharge_list) - aboard)
        return cost, -progress, next(serial), ex, state, actions
    queue = [entry(Fraction(0), Expander(instance, state), state, ())]
    incumbent, incumbent_plan, unknown_floor = None, None, None
    expanded = 0
    metrics = {'check_physics': check_physics, 'shippable': False,
               'optimality_scope': 'weighted_lifts_only', 'tertiary_certified': False,
               'action_space_scope': 'default_only' if default_relocations_only else 'full_boxslot',
               'algorithm': 'dijkstra', 'cp_sat_implemented': False, 'num_workers': 1,
               'ballast_unknown': 0, 'ballast_attempts': 0, 'exact_rejections': 0,
               'mask_verified': False, 'independent_lower_bound': independent}
    terminal_cache = {}
    while queue:
        if perf_counter() >= deadline or expanded >= max_nodes:
            break
        cost, _, _, ex, state, actions = heappop(queue)
        if best.get(_key(state)) != cost:
            continue
        if incumbent is not None and cost >= incumbent:
            heappush(queue, entry(cost, ex, state, actions))
            break
        expanded += 1
        aboard = {p.container_id for p in state.ship.placements}
        complete = (set(instance.load_list) <= aboard and not set(instance.discharge_list) & aboard
                    and not state.yard.quay_buffer)
        if complete:
            final_ex, final_state = ex.copy(), state
            try:
                closing = final_ex.finish()
                for move in closing:
                    final_state = apply_move(instance, final_state, move)
            except (ValueError, IllegalMoveError):
                complete = False
            if complete:
                plan = space.expand(instance, actions)
                assert plan.moves == final_ex.moves, 'expanded moves differ from search model'
                replay = apply_plan(instance, plan)
                assert replay.legal and replay.executed == len(final_ex.moves)
                objective = cost + edge_cost(closing)
                objective += sum((Fraction(str(w.future_restow)) * f.restow_lifts
                                  + Fraction(str(w.future_hatch_cover)) * f.hatch_cover_lifts
                                  for f in replay.count.future), Fraction(0))
                assert math.isclose(float(objective), replay.count.weighted_nonproductive, rel_tol=1e-12, abs_tol=1e-9)
                if incumbent is None or objective < incumbent:
                    terminal_key = tuple((p.slot, p.container_id) for p in final_state.ship.placements)
                    if terminal_key not in terminal_cache:
                        terminal_cache[terminal_key] = _validate_terminal(
                            instance, plan, final_state.ship, physics=check_physics,
                            static=check_static_rules, remaining=lambda: deadline-perf_counter(), seed=seed, metrics=metrics)
                    verdict, ballast, provenance = terminal_cache[terminal_key]
                    candidate = plan.model_copy(update={'ballast': ballast, 'provenance': provenance})
                    if verdict == 'yes' and check_physics and not _validate_mask(instance, candidate, space, metrics):
                        metrics['rejected_candidate_objective'] = float(objective)
                        verdict = 'unknown'
                    if verdict == 'unknown':
                        unknown_floor = objective if unknown_floor is None else min(unknown_floor, objective)
                    if verdict == 'yes':
                        incumbent = objective
                        incumbent_plan = candidate
                        metrics['shippable'] = check_physics
                        from stowage.solver.exact_deadline import publish_checkpoint
                        checkpoint_gap = 0. if incumbent==0 else max(0., (float(incumbent)-independent)/float(incumbent))
                        publish_checkpoint(ExactSearchResult('FEASIBLE', float(incumbent), incumbent_plan,
                            min(float(incumbent), independent), checkpoint_gap, expanded,
                            perf_counter()-started, {**metrics, 'phase': 'validated_relaxed_witness'}))
        for action in _actions(instance, ex, slots, keys, default_relocations_only):
            if perf_counter() >= deadline:
                # Preserve this prefix's bound if its successor list was cut.
                heappush(queue, entry(cost, ex, state, actions))
                break
            child, child_state = ex.copy(), state
            try:
                moves = child.step(action)
                for move in moves:
                    child_state = apply_move(instance, child_state, move)
            except (ValueError, IllegalMoveError):
                continue
            new_cost = cost + edge_cost(moves)
            if incumbent is not None and new_cost >= incumbent:
                continue
            key = _key(child_state)
            if key in best and new_cost >= best[key]:
                continue
            best[key] = new_cost
            heappush(queue, entry(new_cost, child, child_state, (*actions, action)))
    while queue and best.get(_key(queue[0][4])) != queue[0][0]:
        heappop(queue)
    floors = ([queue[0][0]] if queue else []) + ([unknown_floor] if unknown_floor is not None else [])
    certified = incumbent is not None and all(v >= incumbent for v in floors)
    if certified:
        status, bound = 'OPTIMAL', float(incumbent)
    elif incumbent is not None:
        status, bound = 'FEASIBLE', min(float(incumbent), max(independent, float(min(floors))))
    elif not queue and unknown_floor is None:
        status, bound = 'INFEASIBLE', independent
    else:
        status, bound = 'TIME_LIMIT', max(independent, float(min(floors)))
    value = None if incumbent is None else float(incumbent)
    gap = None if incumbent is None else (0. if incumbent == 0 else max(0., (value-bound)/value))
    metrics['states_seen'] = len(best)
    metrics['termination'] = 'node_limit' if expanded >= max_nodes else 'deadline' if perf_counter() >= deadline else 'bound'
    if incumbent_plan is None and metrics.get('mask_rejections'):
        metrics['gate_failure'] = True
    return ExactSearchResult(status, value, incumbent_plan, bound, gap, expanded, perf_counter()-started, metrics)


class ExactPlanner:
    """Planner wrapper: full-checker approved, own-ballast incumbents only."""
    name = 'solver.exact'
    version = 'astar-v2.3-4.0-securing-budget'
    sets_ballast = True
    # Deadlines can change the best incumbent reached, even with one worker.
    deterministic = False

    def __init__(self, *, num_workers: int = 1, max_nodes: int = 10_000,
                 default_relocations_only: bool = False, feasibility_config=None):
        if num_workers != 1:
            raise ValueError('exact search requires num_workers=1')
        self.num_workers, self.max_nodes = num_workers, max_nodes
        self.default_relocations_only = default_relocations_only
        self.feasibility_config = feasibility_config

    def plan(self, instance: Instance, *, seed: int = 0,
             time_limit_s: float | None = None) -> PlannerResult:
        started = perf_counter()
        provenance = current_provenance(created_by=self.name, seed=seed).merge(instance.provenance, parent_ids=[instance.id])
        try:
            result = solve_exact(instance, time_limit_s=30. if time_limit_s is None else time_limit_s,
                                 max_nodes=self.max_nodes, check_physics=True,
                                 default_relocations_only=self.default_relocations_only,
                                 seed=seed, num_workers=self.num_workers,
                                 feasibility_config=self.feasibility_config)
            metrics = {**result.metrics, 'optimality_status': result.status, 'objective': result.objective,
                       'lower_bound': result.lower_bound, 'gap': result.gap, 'expanded_nodes': result.expanded_nodes,
                       'deterministic': 0}
            metrics['optimality_label'] = _optimality_label(result.status, metrics)
            plan = result.plan
            if plan is not None:
                plan = plan.model_copy(update={'provenance': provenance.merge(plan.provenance)})
                provenance = provenance.merge(plan.provenance)
            return PlannerResult(status='timeout' if metrics.get('watchdog_timeout') or metrics.get('termination') == 'deadline' else 'error' if metrics.get('gate_failure') or metrics.get('model_unavailable') else 'ok' if plan is not None else 'infeasible' if result.status == 'INFEASIBLE' else 'timeout',
                                 plan=plan, planner=self.name, planner_version=self.version, seed=seed,
                                 wall_time_s=result.wall_time_s, metrics=metrics, provenance=provenance,
                                 message=metrics['optimality_label'] + '. Tertiary margin optimum is not proved.')
        except Exception as error:
            label = 'optimality unproved for the move model; checker-passed: no'
            return PlannerResult(status='error', planner=self.name, planner_version=self.version,
                                 seed=seed, wall_time_s=perf_counter()-started, provenance=provenance,
                                 metrics={'optimality_label': label, 'checker_passed': False},
                                 message=f'{label}. {type(error).__name__}: {error}')
