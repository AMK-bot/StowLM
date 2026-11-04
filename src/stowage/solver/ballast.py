"""Ballast repair with exact local slopes and an independent hard watchdog.

The legacy path first tries 3.7's manifest-specific narrow-band LP with conservative margins
greater than its measured errors. Then press symmetric tank sets full, trim
each with a bounded exact peak-fill search, and correct list with a heeling
pair. Search ordering never changes the physics or acceptance limits. If necessary,
a successive linear program measures slopes of
only the currently violated exact quantities and makes bounded tank changes.
Cheap floating-equilibrium probes cover draught, trim, list and liquid-corrected
GM; GZ areas/weather and strength are recomputed when those quantities are active.
Every accepted condition passes all four physics checker parts at departure,
mid-voyage and arrival, with consumables derived by the model (D57). No failed local
model or timed-out search is an infeasibility proof.
The six legacy report keys aggregate phase-qualified intermediate evidence;
the full checker additionally enforces static rules and instruction constraints
and remains the shipping gate.

Empty/full endpoints are preferred as a secondary objective, since fewer slack
tanks reduce free surface. Actual tank geometry and slack FSM remain in every
exact probe. The activated D74 path uses the schema's explicitly labelled
synthetic pump/crane parameters, starts at arrival fills, and minimizes pumped
mass only among conditions passing every hard check. Sources:
IMO MSC.267(85), Part B 3.1; Conn, Gould & Toint, Trust Region Methods (2000),
chapter 15 (successive linear models verified against the original functions).

The public call runs in a reusable, dedicated process. Clock checks inside
Python physics raise a non-numerical deadline exception; an independent parent
watchdog can also stop a native call or blocked IPC. Only this module's recorded
worker is ever stopped. Legacy passing conditions return immediately. Activated
search retains its best fully passing condition while trying a smaller pumped
mass, so a later timeout cannot discard a known feasible result.
"""
from __future__ import annotations

import math
import json
import os
import time

import numpy as np
from pydantic import Field

from stowage.frozen import current_provenance, frozen
from stowage.model import BallastCondition, BallastOutcome, Instance, MovePlan, PartReport
from stowage.solver.ballast_deadline import (
    BallastDeadline, ExactDeadline, exact_budget, run_with_deadline,
)
from stowage.solver.ballast_search import Deadline, Found
from stowage.solver.ballast_successive import search


def _provenance(instance, plan, seed):
    return current_provenance(created_by='solver.finish_ballast', seed=seed).merge(
        instance.provenance, plan.provenance, parent_ids=[instance.id, plan.id])


def _validated_replay(instance, plan):
    from stowage.moves import apply_plan

    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError('Ballast requires a legal move sequence: '+
                         '; '.join(v.rule_id for v in replay.violations))
    return replay


@frozen("solver.finish_ballast", owner="4.2", implemented=True)
def finish_ballast(instance: Instance, plan: MovePlan, *, time_limit_s: float = 30.0, seed: int = 0) -> BallastOutcome:
    """Return ballast passing all D57 samples, or a fixed-stow proof/flag.

    - ``plan.ballast`` already set → ``status="kept"``, plan unchanged (the planner owns its ballast; the checker
      still judges it).
    - Otherwise validate moves first: illegal sequences raise ValueError even with no search budget.
    - Otherwise ``ok`` (ballast.source = "finisher", all D57 physics parts pass), ``infeasible_proven`` (this fixed cargo stow, with ``proof``), or
      ``not_found``. Never raises for an unballastable stow.
    Search order is deterministic for a given seed. Wall-clock cutoffs and
    cold/warm worker state can change the returned outcome; no identical-status
    guarantee is made for budget-bound calls. Respects ``time_limit_s`` for search.
    With rule freeze v1 enabled, arrival calibration is outside that clock;
    each candidate also gets D64's separately calibrated in-port acceptance
    allowance. STOWAGE_IN_PORT_WORKERS selects admitted acceptance workers.
    ``parts`` retains four legacy departure keys and arrival stability/strength;
    midpoint and arrival visibility/lashing evidence is phase-qualified and
    aggregated into the corresponding legacy part. Static rules and instruction
    constraints still require the full checker before shipping.
    """
    started = time.monotonic()
    provenance = _provenance(instance, plan, seed)
    if os.environ.get('STOWAGE_BALLAST_V4') == '1':
        from stowage.rules.catalogue import rule_freeze_v1_enabled as v4_rules_enabled
        if not v4_rules_enabled():
            from stowage.solver.ballast_v4_dispatch import finish
            return finish(instance, plan, time_limit_s=time_limit_s, seed=seed)
    if plan.ballast is not None:
        return BallastOutcome(status='kept', plan=plan, provenance=provenance,
                              wall_time_s=time.monotonic()-started)
    # Move validity is an input contract, independent of whether there is time
    # to search for ballast. Keep it outside the worker deadline so a tiny
    # budget cannot turn an invalid plan into an inconclusive physics result.
    _validated_replay(instance, plan)
    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        result = BallastOutcome(status='not_found', provenance=provenance,
                                wall_time_s=time.monotonic()-started)
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        return (_active_metadata(result, cause='timeout:search')
                if rule_freeze_v1_enabled() else result)
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        return _finish_ballast_active(instance, plan, time_limit_s=time_limit_s,
                                      seed=seed, isolated=True)
    deadline = started+time_limit_s
    try:
        # Reserve a small allowance to serialize the completed result. The
        # parent's deadline is authoritative even if a native solver hangs.
        result = run_with_deadline(_finish_ballast, (instance, plan),
            {'time_limit_s': time_limit_s, 'seed': seed,
             '_deadline': deadline-min(.05, time_limit_s*.05)}, deadline=deadline)
    except (BallastDeadline, ExactDeadline):
        result = BallastOutcome(status='not_found', provenance=provenance)
    merged = provenance.merge(result.provenance)
    updates = {'provenance': merged, 'wall_time_s': time.monotonic()-started}
    if result.plan is not None:
        updates['plan'] = result.plan.model_copy(update={
            'provenance': merged.merge(result.plan.provenance)})
    return result.model_copy(update=updates)


def _normal_margin(name, key, value, instance, report):
    if 'mrad' in key:
        scale = .05
    elif key.endswith('rad'):
        scale = max(instance.vessel.hull.max_list_rad, .01) if key == 'list_rad' else .1
    elif 'kg' in key:
        scale = max(1., abs(report.metrics.get('displacement_kg', 1.)))
    elif key == 'blind_distance_m':
        scale = max(1., report.metrics.get('limit_m', 1.))
    else:
        scale = 1.
    return float(value)/scale if math.isfinite(value) else -1e6


def _finish_ballast(instance, plan, *, time_limit_s=30., seed=0, _deadline=None):
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        return _finish_ballast_active(instance, plan, time_limit_s=time_limit_s, seed=seed)
    return _search_ballast(instance, plan, time_limit_s=time_limit_s, seed=seed,
                           _deadline=_deadline)


def _prepare_in_port(policy):
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=True):
        policy.prepare()
    return policy


def _accept_in_port(policy, masses):
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=True):
        return policy.accept(masses), policy


def _calibrate_in_port(policy, masses):
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=True):
        policy.calibrate_acceptance(masses)
    return policy


def _search_in_port(instance, plan, **kwargs):
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=True):
        return _search_ballast(instance, plan, **kwargs)


class _D74BallastOutcome(BallastOutcome):
    """Activated result extension kept inside the finisher's ownership fence.

    Declared fields survive model_dump and process transport; the activation-off
    result remains the original model with its original serialization.
    """
    not_found_cause: str | None = None
    ballast_programme: dict[str, object] = Field(default_factory=dict)


def _active_metadata(result, *, cause=None, programme=None):
    """D74 fields are populated only on the activated path."""
    updates = {'ballast_programme': dict(programme or {})}
    if result.status == 'not_found':
        updates['not_found_cause'] = cause or 'unknown_witness'
    values = {name: getattr(result, name) for name in type(result).model_fields}
    return _D74BallastOutcome.model_validate({**values, **updates})


def _seed_masses(instance, policy):
    """D58 then berth data are proposals, never assumed passing witnesses."""
    override = getattr(policy, 'seed_masses', None)
    if override is not None:
        return list(override())
    result = []
    from stowage.vessel.fleet_conditions import (
        empty_background_witness_tank_masses, slice_condition,
    )
    condition = slice_condition(instance.vessel)
    if condition and condition.get('empty_background_witness_ballast_masses_kg'):
        result.append(empty_background_witness_tank_masses(instance.vessel))
    # The production berth initializer records its exact witness on provenance.
    # Reuse it without rerunning an unrelated generator or changing arrival.
    from stowage.vessel.fleet_berth import ARRIVAL_PREFIX
    for parent in instance.provenance.parents:
        if parent.startswith(ARRIVAL_PREFIX):
            audit = json.loads(parent[len(ARRIVAL_PREFIX):])
            if audit.get('tank_masses_kg'):
                result.append(audit['tank_masses_kg'])
                break
    return result


def _candidate_key(masses):
    """Exact candidate identity; a completed rejection supplies no gradient."""
    return tuple(sorted(masses.items()))


def _finish_ballast_active(instance, plan, *, time_limit_s=30., seed=0, isolated=False):
    """Keep D64 arrival, D57 search and candidate-acceptance clocks separate."""
    from stowage.solver.ballast_inport import InPortFinisher, InPortUnknown
    started = time.monotonic()
    provenance = _provenance(instance, plan, seed)
    if plan.ballast is not None:
        return BallastOutcome(status='kept', plan=plan, provenance=provenance)
    _validated_replay(instance, plan)

    policy = None
    incumbent = None
    incumbent_p = math.inf

    def failure(reason, parts=None, iterations=0, cause='unknown_witness'):
        # A later timed-out or failed improvement trial cannot discard a
        # condition that already passed D57 and the complete D74 programme.
        if incumbent is not None:
            return incumbent.model_copy(update={'wall_time_s': time.monotonic()-started})
        reports = dict(parts or {})
        reports.setdefault('in_port', PartReport(name='in_port', ran=False, error=reason))
        return _active_metadata(BallastOutcome(status='not_found', parts=reports, iterations=iterations,
            provenance=provenance, wall_time_s=time.monotonic()-started), cause=cause,
            programme=getattr(policy, 'ballast_programme', {}))

    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        return failure('no search budget', cause='timeout:search')
    try:
        policy = InPortFinisher(instance, plan,
            workers=int(os.environ.get('STOWAGE_IN_PORT_WORKERS', '1')))
        # D74's shared parallel pass1 has its own calibrated plan deadline,
        # outside the search allowance. No daemon encloses a process pool.
        policy.prepare()
    except InPortUnknown as exc:
        return failure(str(exc), cause=exc.cause)
    except (BallastDeadline, ExactDeadline) as exc:
        return failure(f'arrival prepass budget exhausted ({exc})', cause='timeout:prepass')
    except (ValueError, ArithmeticError, KeyError, TypeError, RuntimeError, BallastDeadline, ExactDeadline) as exc:
        return failure(f'in-port state 0: arrival prepass unavailable ({exc})')
    search_remaining = time_limit_s
    # Retain only completed D74 rejections across searches. Unknown/timeouts
    # never enter this set, and nonideal failures never invent ideal margins.
    policy.rejected_candidates = set()
    iterations = 0
    result = None
    for attempt in range(3):
        policy.research_count = attempt
        if search_remaining <= 0:
            break
        before = time.monotonic()
        search_deadline = before+search_remaining
        kwargs = {'time_limit_s': search_remaining, 'seed': seed,
                  '_deadline': search_deadline, '_in_port': policy}
        try:
            result = (run_with_deadline(_search_in_port, (instance, plan), kwargs,
                                       deadline=search_deadline) if isolated else
                      _search_ballast(instance, plan, **kwargs))
        except (BallastDeadline, ExactDeadline):
            return failure('seagoing search budget exhausted',
                           iterations=iterations, cause='timeout:search')
        except InPortUnknown as exc:
            return failure(str(exc), iterations=iterations, cause=exc.cause)
        except (ValueError, ArithmeticError, KeyError, TypeError, RuntimeError) as exc:
            return failure(f'in-port state {policy.k_arr}: search unavailable ({exc})',
                           iterations=iterations)
        search_remaining -= time.monotonic()-before
        iterations += result.iterations
        if result.status != 'ok':
            if incumbent is not None:
                return incumbent.model_copy(update={'wall_time_s': time.monotonic()-started})
            reports = dict(result.parts)
            if policy.failure:
                reports['in_port'] = PartReport(name='in_port', ran=False, error=policy.failure)
            result = result.model_copy(update={'parts': reports, 'iterations': iterations,
                'wall_time_s': time.monotonic()-started})
            cause = getattr(result, 'not_found_cause', None)
            if cause in (None, 'unknown_witness') and policy.failure_cause:
                # A subsequent exhausted proposal list does not erase the
                # last completed rate/acceptance rejection. Timeouts retain
                # their own cause instead of being relabelled as physics.
                cause = policy.failure_cause
            if cause in (None, 'unknown_witness') and getattr(policy, 'known_passing_witness', False):
                cause = 'search_miss'
            return _active_metadata(result, cause=cause,
                                    programme=policy.ballast_programme)
        masses = result.plan.ballast.tank_masses_kg
        try:
            if isolated and policy.workers == 1:
                # First candidate pass2 timing is measured under the 10s floor;
                # the remainder uses its calibrated TOTAL allowance, charging
                # context/transfer/ranking and calibration to that same clock.
                policy = run_with_deadline(_calibrate_in_port, (policy, masses),
                                           deadline=time.monotonic()+10.)
                deadline = policy.acceptance_started+policy.acceptance_budget_s
                report, policy = run_with_deadline(_accept_in_port, (policy, masses), deadline=deadline)
            else:
                report = policy.accept(masses)
        except (BallastDeadline, ExactDeadline):
            return failure(f'in-port state {policy.k_arr}: acceptance budget exhausted',
                           result.parts, iterations, cause='timeout:acceptance')
        except InPortUnknown as exc:
            return failure(str(exc), result.parts, iterations, cause=exc.cause)
        except (ValueError, ArithmeticError, KeyError, TypeError, RuntimeError) as exc:
            return failure(f'in-port state {policy.k_arr}: acceptance unavailable ({exc})',
                           result.parts, iterations)
        reports = {**result.parts, 'in_port': report}
        if report.ran and not report.error and not any(v.severity == 'hard' for v in report.violations):
            pumped = policy.pumped_mass(masses)
            if pumped < incumbent_p:
                incumbent_p = pumped
                incumbent = _active_metadata(result.model_copy(update={
                    'parts': reports, 'iterations': iterations,
                    'wall_time_s': time.monotonic()-started}), programme=policy.ballast_programme)
            policy.incumbent_pumped_mass = incumbent_p
            policy.known_passing_witness = True
            if incumbent_p <= 1e-6:
                return incumbent  # P=0 is the exact best possible tie-break.
            continue
        # _batch_failure uses error for its completed-state diagnostic too;
        # the typed cause distinguishes that from an unavailable calculation.
        completed_rejection = policy.failure_cause and (
            policy.failure_cause.startswith('rate_infeasible:') or
            policy.failure_cause.startswith('acceptance_failure:'))
        if (completed_rejection and report.ran and
                any(v.severity == 'hard' for v in report.violations) and
                not any(v.rule_id.startswith('checker.') for v in report.violations)):
            policy.rejected_candidates.add(_candidate_key(masses))
        # State failures get at most two re-searches. An exhausted allowance
        # without a completed failing state cannot guide another search.
        if (not report.ran or policy.failure_cause in ('unknown_witness', 'timeout:acceptance')
                or attempt == 2):
            return failure(policy.failure, reports, iterations,
                           cause=policy.failure_cause or 'unknown_witness')
    return failure(policy.failure or 'seagoing search budget exhausted',
                   result.parts if result else None, iterations, cause='timeout:search')


def _search_ballast(instance, plan, *, time_limit_s=30., seed=0, _deadline=None, _in_port=None):
    """Worker implementation; directly callable by physics-isolated unit tests."""
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength
    from stowage.checker.visibility import check_visibility
    from stowage.checker.lashing import check_lashing
    from stowage.linear.model import build_linear_model, solve_ballast_lp
    from stowage.linear.bands import band_for_instance
    from stowage.solver.ballast_physics import ExactContext
    from stowage.solver.ballast_proof import prove_infeasible
    from stowage.solver.ballast_inport import InPortUnknown, _unavailable

    started = time.monotonic()
    deadline = started+time_limit_s if _deadline is None else _deadline
    provenance = _provenance(instance, plan, seed)
    parts, best_parts, accepted = {}, {}, None
    iterations, best_merit = 0, math.inf
    def outcome(status, **kwargs):
        result = BallastOutcome(status=status, wall_time_s=time.monotonic()-started,
                                provenance=provenance, **kwargs)
        return _active_metadata(result) if _in_port is not None else result
    if plan.ballast is not None:
        return outcome('kept', plan=plan)
    replay = _validated_replay(instance, plan)
    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        return outcome('not_found')
    try:
        with exact_budget(deadline):
            ship = replay.final_state.ship
            proof = prove_infeasible(instance, ship)
            if proof is not None:
                return outcome('infeasible_proven', proof=proof)
            tanks = sorted((t for t in instance.vessel.tanks if t.adjustable and
                            (_in_port is None or t.id not in _in_port.heel)), key=lambda t:t.id)
            capacity = np.array([t.capacity_m3*t.content_density_kg_m3 for t in tanks])
            initial = np.array([ship.tank_masses_kg.get(t.id, 0.) for t in tanks])/capacity
            initial = np.clip(initial, 0., 1.)
            fills = []
            context = None
            fixed = {t.id: ship.tank_masses_kg.get(t.id,0.) for t in instance.vessel.tanks if not t.adjustable}
            self_adjustable = {t.id: t.adjustable for t in instance.vessel.tanks}
            cache = {}
            checkers = {'stability': check_stability, 'strength': check_strength,
                        'visibility': check_visibility, 'lashing': check_lashing}
            loading_keys = {'stability/'+key for key in ('gm_m','trim_m','trim_min_m','displacement_minimum_kg','displacement_maximum_kg','list_rad',
                'draught_aft_m','draught_fwd_m','draught_fwd_min_m','port_draught_m')}
            weather_keys = {'stability/'+key for key in ('weather_beam_draught',
                'weather_kg_lower','weather_kg_upper','weather_period','weather_r')}

            def evaluate(fractions, active_keys=None):
                nonlocal iterations, parts, best_parts, best_merit, accepted
                if time.monotonic() >= deadline:
                    raise Deadline
                fractions = np.clip(np.asarray(fractions,dtype=float),0.,1.)
                if _in_port is not None:
                    projected = _in_port.project({t.id: float(x*c) for t,x,c in zip(tanks,fractions,capacity)})
                    fractions = np.array([projected[t.id]/c for t,c in zip(tanks,capacity)])
                selected = None if active_keys is None else set(active_keys)
                key = (fractions.tobytes(), None if selected is None else tuple(sorted(selected)))
                if key in cache:
                    return cache[key]
                iterations += 1
                margins = {}
                if context is not None and (selected is None or selected & (loading_keys|weather_keys)):
                    try:
                        fast_selected = None if selected is None else selected-{'stability/weather_table_applicability'}
                        margins = context.margins(fractions, active_keys=fast_selected)
                        if (selected is not None and 'stability/weather_table_applicability' in selected
                                and any(v < 0. for k,v in margins.items() if k in weather_keys)):
                            # A failed necessary range condition makes the
                            # Code's conjunction false without another GZ curve.
                            margins['stability/weather_table_applicability'] = -1.
                    except (ValueError, ArithmeticError):
                        # No equilibrium here is not an impossibility proof.
                        margins = {}
                    if (selected is not None and selected <= (loading_keys|weather_keys|{'stability/weather_table_applicability'})
                            and selected <= margins.keys()):
                        cache[key] = margins
                        return margins
                    if selected is None and any(v < -1e-9 for v in margins.values()):
                        # These are exact loading clearances, not a completed
                        # stability report: areas/weather remain unevaluated.
                        merit = sum(min(v,0.)**2 for v in margins.values())
                        if merit < best_merit:
                            best_merit = merit
                            best_parts = {'stability': PartReport(name='stability', ran=False,
                                error='Exact loading probes only; full GZ/weather check not completed',
                                margins={k.split('/',1)[1]: v*instance.vessel.hull.max_list_rad
                                    if k.endswith('/list_rad') else v for k,v in margins.items()})}
                        cache[key] = margins
                        return margins
                masses = {t.id: float(x*c) for t,x,c in zip(tanks,fractions,capacity)}
                if _in_port is not None:
                    masses = {tid: value for tid,value in _in_port.project(masses).items()
                              if self_adjustable[tid]}
                candidate = ship.model_copy(update={'tank_masses_kg': {**fixed, **masses}})
                if _in_port is not None:
                    if selected is None and _candidate_key(masses) in getattr(
                            _in_port, 'rejected_candidates', set()):
                        # A full R2/nonideal rejection must advance to another
                        # seed even when no valid ideal-split feedback exists.
                        return {}
                    if _in_port.pumped_mass(masses) >= getattr(
                            _in_port, 'incumbent_pumped_mass', math.inf)-1e-6:
                        # Objective pruning only: this trial cannot improve an
                        # already fully accepted condition. It is neither a
                        # physical rejection nor an infeasibility argument.
                        return margins
                from stowage.solver.ballast_arrival import arrival_condition, mid_voyage_condition
                reports = {}
                checked_reports = []
                conditions = [('', instance, candidate, plan.call_index, checkers)]
                try:
                    mid_instance, mid_ship = mid_voyage_condition(instance, candidate)
                    conditions.append(('mid_voyage/', mid_instance, mid_ship,
                        mid_instance.call_index, checkers))
                    arrival_instance, arrival_ship = arrival_condition(instance, candidate)
                    conditions.append(('arrival/', arrival_instance, arrival_ship,
                        arrival_instance.call_index, checkers))
                except (ValueError, ArithmeticError, KeyError, TypeError) as exc:
                    if _in_port is not None:
                        raise InPortUnknown(f'D57 voyage conditions unavailable: {exc}') from exc
                    margins['arrival/unavailable'] = -1.
                    reports['arrival/stability'] = PartReport(name='stability', ran=False, error=str(exc))
                for phase, condition_instance, condition_ship, call_index, condition_checkers in conditions:
                    for name, checker in condition_checkers.items():
                        if time.monotonic() >= deadline:
                            raise Deadline
                        report_key = phase+name
                        if selected is not None and not any(k.startswith(report_key+'/') for k in selected):
                            continue
                        report = checker(condition_instance, condition_ship, call_index=call_index)
                        if _in_port is not None and _unavailable(report):
                            # Stop before extracting/caching any margin from an
                            # incomplete calculation. Unknown has no gradient.
                            parts = {**reports, report_key: report}
                            detail = report.error or '; '.join(v.message for v in report.violations)
                            timed = any(word in detail.lower() for word in
                                        ('timeout', 'time budget', 'deadline'))
                            raise InPortUnknown(f'{report_key} unavailable: {detail}',
                                cause='timeout:search' if timed else 'unknown_witness')
                        checked_reports.append(report)
                        # Retain the six public report keys used by D50 callers.
                        # Midpoint reports and arrival visibility/lashing merge
                        # into their legacy departure part, with phase-qualified
                        # evidence. Search margins below keep distinct phases.
                        legacy_key = report_key if not phase or (phase == 'arrival/' and
                            name in ('stability', 'strength')) else name
                        if legacy_key not in reports:
                            reports[legacy_key] = report.model_copy(deep=True)
                        else:
                            aggregate = reports[legacy_key]
                            aggregate.ran = aggregate.ran and report.ran
                            aggregate.violations.extend(v.model_copy(update={
                                'message': f'[{phase.rstrip("/")}] {v.message}'})
                                for v in report.violations)
                            if report.error:
                                aggregate.error = '; '.join(filter(None, (
                                    aggregate.error, f'[{phase.rstrip("/")}] {report.error}')))
                            aggregate.metrics.update({phase+k: v for k,v in report.metrics.items()})
                            for k,v in report.margins.items():
                                aggregate.margins[phase+k] = v
                                aggregate.margins[k] = min(aggregate.margins.get(k,v),v)
                        for k,v in report.margins.items():
                            label = report_key+'/'+k
                            if selected is None or label in selected:
                                margins[label] = _normal_margin(name,k,v,condition_instance,report)
                        if name == 'strength' and condition_instance.vessel.hull is not None:
                            for station in condition_instance.vessel.hull.stations:
                                position = format(station.x_m,'.12g')
                                for prefix, lower, upper in (
                                    ('sf_n',station.min_shear_n,station.max_shear_n),
                                    ('bm_nm',station.min_bending_nm,station.max_bending_nm)):
                                    value = report.metrics.get(prefix+'@'+position)
                                    if value is None:
                                        continue
                                    for side, clearance, scale in (
                                        ('upper',upper-value,abs(upper)),
                                        ('lower',value-lower,abs(lower))):
                                        label = f'{report_key}/{prefix}@{position}/{side}'
                                        if selected is None or label in selected:
                                            margins[label] = clearance/max(scale,1.)
                        if (not phase and name == 'stability' and context is not None and
                                report.margins.get('weather_table_applicability',0.) < 0.):
                            margins.update(context.margins(fractions, active_keys=weather_keys))
                        # Discrete applicability and unavailable calculations have
                        # no invented gradient. They remain hard negative flags.
                        failures = [v for v in report.violations if v.severity == 'hard']
                        if not report.ran or (failures and not any(v < 0 for k,v in margins.items() if k.startswith(report_key+'/'))):
                            margins[report_key+'/unavailable' if not report.ran else report_key+'/hard_violation'] = -1.
                        if _in_port is not None and failures:
                            # A departure-ballast trial ends at its first
                            # completed physical failure, across all D57 phases.
                            parts = reports
                            merit = sum(min(v, 0.)**2 for v in margins.values())
                            if merit < best_merit:
                                best_merit, best_parts = merit, reports.copy()
                            cache[key] = margins
                            return margins
                if _in_port is not None:
                    in_port_margins = _in_port.constraint_margins(masses)
                    margins.update({k:v for k,v in in_port_margins.items()
                                    if selected is None or k in selected})
                if selected is not None:
                    cache[key] = margins
                    return margins
                parts = reports
                merit = sum(min(v,0.)**2 for v in margins.values())
                if merit < best_merit:
                    best_merit, best_parts = merit, reports.copy()
                if (not any(v < 0 for k,v in margins.items() if k.startswith('in_port/')) and
                        len(checked_reports) == 12) and all(p.ran and not any(v.severity=='hard' for v in p.violations)
                                              for p in checked_reports):
                    accepted = plan.model_copy(update={'ballast': BallastCondition(
                        tank_masses_kg=masses,source='finisher'), 'provenance': provenance})
                    raise Found
                cache[key] = margins
                return margins

            if _in_port is not None:
                # D74: zero-pumping arrival first, then the supplied D58 and
                # berth proposals. Do not pay for the LP before these trials.
                evaluate(initial)
                fills.append(initial)
                for proposal in _seed_masses(instance, _in_port):
                    trial = np.array([proposal.get(t.id, ship.tank_masses_kg.get(t.id, 0.))
                                      for t in tanks])/capacity
                    evaluate(trial)
                    fills.append(trial)

            # Start with 3.7's conservative construction LP as plan 4.2
            # requires. Local failure/time expiry is inconclusive; exact search
            # still explores fills outside this deliberately narrow LP band.
            anchor = initial
            try:
                # Full manifest held fixed; local tank windows and trim narrow
                # the hydrostatic fit. Local LP rejection cannot prove anything
                # about fills outside these explicitly modelled windows.
                lp_deadline = min(deadline, time.monotonic()+min(5., time_limit_s*.15))
                with exact_budget(lp_deadline):
                    if instance.vessel.hull is None:
                        raise ValueError('Missing hull data')
                    windows = {t.id: (max(0., x-.25)*c, min(1., x+.25)*c)
                               for t,x,c in zip(tanks,anchor,capacity)}
                    if _in_port is not None:
                        projected = _in_port.project({t.id: float(x*c)
                            for t,x,c in zip(tanks,anchor,capacity)})
                        windows.update({tid: (projected[tid], projected[tid])
                                        for tid in _in_port.heel})
                    band = band_for_instance(instance, ballast_windows_kg=windows,
                        max_abs_trim_m=min(instance.vessel.hull.max_trim_m, .5))
                    model = build_linear_model(instance, band=band)
                    if _in_port is None:
                        lp = solve_ballast_lp(model, ship, instance,
                            direction='conservative', margin_scale=1.001)
                    else:
                        from stowage.solver.ballast_rate_lp import solve_rate_ballast_lp
                        data = _in_port.rate_lp_data()
                        arrival_masses = dict(data['arrival_masses'])
                        # HEEL is outside P. Pin it to the same exact split as
                        # the local model windows, retaining its arrival total.
                        arrival_masses.update({tid: projected[tid] for tid in _in_port.heel})
                        lp = solve_rate_ballast_lp(model, ship, instance,
                            arrival_masses=arrival_masses, heel_ids=_in_port.heel,
                            capacity_kg=data['capacity_kg'], symmetry_pairs=_in_port.symmetric,
                            direction='conservative', margin_scale=1.001)
                    if lp.status == 'optimal':
                        trial = np.array([lp.tank_masses_kg.get(t.id,0.) for t in tanks])/capacity
                        if _in_port is not None:
                            evaluate(trial)
                        fills.append(trial)
            except (ExactDeadline, ValueError, ArithmeticError, KeyError, TypeError, FileNotFoundError):
                pass
            # Check the LP seed immediately: a useful LP result must not sit
            # behind an expensive enumeration of pressed-tank branches.
            if fills:
                evaluate(fills[0])
            # Test the actual replayed fills before constructing alternative
            # tank basins; an already passing departure needs no repair.
            evaluate(initial)
            # The full checker can accept an already feasible seed without
            # building the separate cached moments/equilibrium repair context.
            # Only failed seeds need the cheaper selective loading probes.
            try:
                context = ExactContext(instance, ship, deadline) if _in_port is None and tanks and instance.vessel.hull is not None else None
            except (ValueError, ArithmeticError, KeyError, TypeError, FileNotFoundError):
                context = None
            from stowage.solver.ballast_pressed import search as pressed_search
            pressed_deadline = time.monotonic()+.6*max(0., deadline-time.monotonic())
            try:
                fills.extend(pressed_search(tanks, initial, evaluate, context, pressed_deadline))
            except Deadline:
                pass
            measured = [(sum(min(v,0.)**2 for v in margins.values()), key[0])
                        for key,margins in cache.items() if key[1] is None]
            fills.extend(np.frombuffer(raw,dtype=float).copy()
                         for _,raw in sorted(measured,key=lambda row:row[0])[:3])
            fills.append(initial)

            fsm = np.array([max(point.fsm_m4 for point in t.table)*t.content_density_kg_m3 for t in tanks])
            slack_weights = 1.+fsm/max(float(fsm.max(initial=0.)),1.)
            search(fills, evaluate, deadline, seed, slack_weights=slack_weights)
            proof = prove_infeasible(instance, ship, exact_parts=best_parts)
            if proof is not None:
                return outcome('infeasible_proven', proof=proof, parts=best_parts, iterations=iterations)
    except Found:
        return outcome('ok', plan=accepted, parts=parts, iterations=iterations)
    except InPortUnknown as exc:
        if _in_port is None:
            raise
        unknown_parts = {**parts, 'search': PartReport(name='search', ran=False, error=str(exc))}
        return _active_metadata(outcome('not_found', parts=unknown_parts, iterations=iterations),
                                cause=exc.cause)
    except (Deadline, ExactDeadline):
        if _in_port is not None:
            return _active_metadata(outcome('not_found', parts=best_parts or parts,
                                           iterations=iterations), cause='timeout:search')
    return outcome('not_found', parts=best_parts or parts, iterations=iterations)
