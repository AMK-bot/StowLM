"""D64 every-lift cargo-operation replay and sampled harbour strength.

APPROXIMATION: MovePlan.moves is one total order; simultaneous cranes are not
represented. D74 bounds pumping time, verifies both window endpoints, and uses
linearised HEEL bands with exact chosen-path checks. Only strength/static
P7--P9 are sampled; equilibrium P1--P6 checks every state.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from contextlib import ExitStack
import os
from time import monotonic

import numpy as np

from stowage.checker.rules import check_static_rules
from stowage.checker.slots import cells_of_slot
from stowage.checker.stability_states import InPortStateBuilder
from stowage.checker.strength import check_strength
from stowage.model import PartReport, Violation
from stowage.moves import IllegalMoveError

# Lazy factory imports keep legacy full.check independent of optional replay
# machinery, and give orchestration tests an explicit numerical seam.
def prepare_context(instance, departure_ship):
    from stowage.checker.in_port_liquids import prepare_context as prepare
    return prepare(instance, departure_ship)


def InPortEvaluator(instance, context, *, workers=1, warm_start=False,
                    heel_intervals=False):
    from stowage.checker.in_port_evaluator import InPortEvaluator as Evaluator
    return Evaluator(instance, context, workers=workers, warm_start=warm_start,
                     heel_intervals=heel_intervals)


_RULE = 'port.stability.cargo_operations'
_STACK_RULES = frozenset({'expert.physical.stack_weight', 'expert.physical.tier_height'})
_NOTE = ('note: APPROXIMATION: MovePlan.moves is one total lift order, not concurrent cranes; '
         'ballast changes instantaneously at k without pump-rate timing')
_RATE_NOTE = ('note: APPROXIMATION: one total lift order; synthetic D74 pump/crane rates; '
              'worked-FSM window endpoints represent intermediate tank fills; '
              'linearised HEEL bands require exact chosen-path checks')


def _failed(report):
    return not report.ran or report.error is not None or any(v.severity == 'hard' for v in report.violations)


def _run(instance, function, *args, **kwargs):
    """A broken sample must retain k and cannot suppress the other samples."""
    try:
        report = function(*args, **kwargs)
        if not isinstance(report, PartReport):
            raise TypeError('in-port checker part did not return PartReport')
        return report
    except Exception as error:
        message = f'{type(error).__name__}: {error}'
        return PartReport(name='in_port', ran=False, error=message,
            violations=[Violation(rule_id='checker.internal_error', severity='hard',
                call_index=instance.call_index, message=message)])


def _strength_numerical_errors(report):
    """Normalize legacy numerical diagnostics only in the gated D64 caller.

    The primitive historically labels input and solver errors together. Keep
    real input gaps (missing hull/tank/weight data) distinct from these known
    failed calculations; an unavailable numerical readout is not a P7 breach.
    """
    if report.ran:
        return report
    numerical = (
        'Numerical load-curve imbalance ',
        'S1 required strength readouts ',
        'S1 requires finite SF/BM readouts ',
        'Strength input/equilibrium error: coupled tank/hull floating equilibrium did not converge',
        'Strength input/equilibrium error: no converged floating equilibrium',
        'Strength input/equilibrium error: nonfinite ',
        'Strength input/equilibrium error: invalid two-point Gauss load grid',
        'Strength input/equilibrium error: grid is not consecutive two-point Gauss panels',
    )
    return report.model_copy(update={'violations': [violation.model_copy(
        update={'rule_id': 'checker.internal_error', 'severity': 'hard'})
        if violation.message.startswith(numerical) else violation
        for violation in report.violations]})


def _strength_context(instance):
    """Pass an explicitly regenerated harbour hull, without mutating the vessel."""
    from stowage.checker.strength import _geometry
    from stowage.checker.strength_limits import permissible_stations
    hull = instance.vessel.hull
    if hull is None or not hull.hull_offsets_ref:
        raise ValueError('In-port strength requires a declared hull geometry reference.')
    params, geometry = _geometry(PurePosixPath(hull.hull_offsets_ref.split('#')[0]).stem)
    harbour = hull.model_copy(update={'stations': permissible_stations(params, geometry, in_port=True)})
    return instance.model_copy(update={'vessel': instance.vessel.model_copy(update={'hull': harbour})})


def _strength_risk(instance, ship):
    """Max station ratio of a cheap longitudinal beam, only for top-three sampling.

    APPROXIMATION: rank states using upright point weights and affine buoyancy
    balancing their mass and longitudinal moment. This uncalibrated surrogate
    cannot accept/reject any state. Exact strength decides sampled P7/P8.
    """
    hull = instance.vessel.hull
    if hull is None or not hull.stations:
        raise ValueError('Strength sampling requires permissible stations.')
    vessel = instance.vessel
    points = [(hull.lightship_lcg_m, hull.lightship_mass_kg)]
    points.extend((x, mass) for x, y, z, mass in vessel.fixed_loads)
    cells = {cell.id: cell for cell in vessel.cells}
    boxes = {box.id: box for box in instance.containers}
    for placement in ship.placements:
        footprint = cells_of_slot(placement.slot)
        mass = boxes[placement.container_id].gross_mass_kg / len(footprint)
        points.extend((cells[cid].lcg_m, mass) for cid in footprint)
    tanks = {tank.id: tank for tank in vessel.tanks}
    for tid, mass in ship.tank_masses_kg.items():
        if mass:
            tank = tanks[tid]
            table = tank.table
            # Sounding centres are used only in this ranking approximation.
            row = min(table, key=lambda row: abs(row.volume_m3 - mass/tank.content_density_kg_m3))
            points.append((row.lcg_m, mass))
    covers = {cover.id: cover for cover in vessel.hatch_covers}
    for cid, location in ship.covers_off.items():
        cover = covers[cid]
        points.append((cover.lcg_m, -cover.mass_kg))
        if location != 'quay':
            points.append((covers[location].lcg_m, cover.mass_kg))
    length = hull.lpp_m
    mass = sum(m for _, m in points)
    moment = sum(x*m for x, m in points)
    b = 12*(moment-mass*length/2)/length**3
    a = mass/length-b*length/2
    # Prefix integrals avoid a state x station x cargo Python loop on full ships.
    points.sort(key=lambda point: point[0])
    px, pm = np.asarray(points, dtype=float).T
    x = np.asarray([station.x_m for station in hull.stations])
    count = np.searchsorted(px, x, side='right')
    masses = np.r_[0., np.cumsum(pm)][count]
    moments = np.r_[0., np.cumsum(px*pm)][count]
    sf = 9.80665*(a*x+b*x*x/2-masses)
    bm = -9.80665*(a*x*x/2+b*x**3/6-x*masses+moments)
    sf_limit = np.where(sf >= 0, [s.max_shear_n for s in hull.stations],
                       [-s.min_shear_n for s in hull.stations])
    bm_limit = np.where(bm >= 0, [s.max_bending_nm for s in hull.stations],
                       [-s.min_bending_nm for s in hull.stations])
    return float(max(np.max(np.abs(sf)/sf_limit), np.max(np.abs(bm)/bm_limit)))


def _sample_indices(instance, moves, states, strength_context):
    """Arrival/end, hatch events, every discharge, bay completions, top three."""
    samples = {0, len(states)-1}
    last_by_bay = {}
    for i, move in enumerate(moves, start=1):
        if move.kind in {'discharge', 'hatch_cover_off', 'hatch_cover_on'}:
            samples.add(i)
        for field in ('src', 'dst'):
            slot = getattr(move, field, '')
            if isinstance(slot, str) and len(slot) == 7 and slot.isdigit():
                for cid in cells_of_slot(slot):
                    last_by_bay[cid[:3]] = i
    samples.update(last_by_bay.values())
    risks = [(_strength_risk(strength_context, ship), i) for i, ship in enumerate(states)]
    samples.update(i for _, i in sorted(risks, key=lambda pair: (-pair[0], pair[1]))[:3])
    return sorted(samples)


def replay(instance, plan, departure_ship=None, *, workers=None, warm_start=False, telemetry=None):
    """Own one activated replay pool; phase views retain ordered cold evidence."""
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if not rule_freeze_v1_enabled():
        return _replay(instance,plan,departure_ship,workers=workers,
            warm_start=warm_start,telemetry=telemetry,evaluator_factory=InPortEvaluator)
    with ExitStack() as lifetime:
        owner = None
        def evaluator_factory(instance,context,**kwargs):
            nonlocal owner
            if owner is None:
                candidate = InPortEvaluator(instance,context,**kwargs)
                # Small orchestration seams need not implement phase views.
                if not hasattr(candidate,'for_context'):
                    return candidate
                owner = lifetime.enter_context(candidate)
            return owner.for_context(context,
                heel_intervals=kwargs.get('heel_intervals',False))
        return _replay(instance,plan,departure_ship,workers=workers,
            warm_start=warm_start,telemetry=telemetry,evaluator_factory=evaluator_factory)


def _replay(instance, plan, departure_ship=None, *, workers=None, warm_start=False,
            telemetry=None, evaluator_factory):
    """Return aggregate in-port evidence, with k as a zero-based state index.

    s0 is arrival; si is after moves[i-1]. k is the first failed arrival-fill
    state, or n+1 if the complete arrival series passes. Probe failures replaced
    by a passing departure suffix are retained as margins, not violations.
    Numerical/unavailable arrival errors always fail closed. Cold replay is the
    deterministic production default. Explicit warm continuation has only the
    separately tested verdict-equivalence contract. Runtime measurements go to
    the caller's optional telemetry dictionary, never the serialized report.
    """
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    d74 = rule_freeze_v1_enabled()
    if d74 and warm_start:
        raise ValueError('D74 pass1 requires independent cold state evaluations.')
    builder = InPortStateBuilder(instance)
    states, executed = [builder.state.ship], []
    for index, move in enumerate(plan.moves):
        try:
            states.append(builder.push(move, move_index=index).ship)
            executed.append(move)
        except IllegalMoveError:
            break  # Full replay owns the illegal-lift diagnostic.
    if departure_ship is None:
        from stowage.checker.full import _ballast
        departure_ship, problems = _ballast(instance, plan, states[-1])
        if problems:
            return PartReport(name='in_port', ran=False, error='Invalid departure ballast.', violations=problems)
    context = prepare_context(instance, departure_ship)
    # D64 rev3.1: arrival FSM is independent of the departure candidate.
    # Only pass 2 charges the additional tanks worked at the changeover.
    arrival_context = context.for_phase('arrival')
    workers = int(os.environ.get('STOWAGE_IN_PORT_WORKERS', '1')) if workers is None else workers
    arrival_ships, arrival_metadata = zip(*(context.transferred_ship(ship, phase='arrival') for ship in states))
    prepass_started = monotonic()
    with evaluator_factory(instance, arrival_context, workers=workers, warm_start=warm_start) as evaluator:
        if d74:
            from stowage.checker.in_port_prepass import arrival_prepass
            arrival_batch = arrival_prepass(evaluator, arrival_ships, workers=workers)
            arrival = list(arrival_batch.reports)
            k = arrival_batch.k_arr
        else:
            arrival_batch = evaluator.evaluate(arrival_ships)
            arrival = list(arrival_batch.reports)
            if len(arrival) != len(states):
                raise RuntimeError('Incomplete in-port arrival evaluation.')
            k = next((i for i, report in enumerate(arrival) if _failed(report)), len(states))
    if telemetry is not None:
        telemetry['pass1_wall_s'] = monotonic()-prepass_started
    departure_ships, departure_metadata, departure, departure_batch = (), (), [], None
    if k < len(states):
        departure_context = context.for_phase('departure')
        departure_ships, departure_metadata = zip(*(context.transferred_ship(ship, phase='departure') for ship in states[k:]))
        with evaluator_factory(instance, departure_context, workers=workers, warm_start=warm_start) as evaluator:
            departure_batch = evaluator.evaluate(departure_ships)
            departure = list(departure_batch.reports)
            if len(departure) != len(states)-k:
                raise RuntimeError('Incomplete in-port departure evaluation.')
    result = PartReport(name='in_port', metrics={'k': float(k), 'k_arr': float(k),
        'state_count': float(len(states)), 'arrival_states_evaluated': float(len(arrival)),
        'departure_states_evaluated': float(len(departure)), _RATE_NOTE if d74 else _NOTE: 1.})
    if telemetry is not None:
        telemetry.update(t_hat_s=arrival_batch.measured_state_time_s,
            t_hat_state_count=arrival_batch.measured_state_count,
            workers=workers, remaining_state_count=len(states)-k,
            warm_start=bool(warm_start),
            # n counts replay states; n-k includes s_k through the final state.
            acceptance_budget_s=max(10., 2.*arrival_batch.measured_state_time_s*(len(states)-k)/workers))
        if d74:
            # B.6 supersedes the rev3.1 pass1-derived acceptance estimate.
            telemetry.pop('acceptance_budget_s', None)
            telemetry.update(t_hat_accept_s=(departure_batch.measured_state_time_s
                if departure_batch is not None else None),
                t_hat_accept_source='pass2' if departure_batch is not None else 'no_pass2_states',
                t_hat_accept_state_count=(departure_batch.measured_state_count
                    if departure_batch is not None else 0))
    if warm_start:
        result.metrics['note: warm continuation promises verdict equivalence only; near-limit margins use cold direct solves'] = 1.

    def merge(report, i, phase, *, selected=True, physical=True):
        prefix = f'state:{i}.{phase}' if i is not None else phase
        result.metrics.update({f'{prefix}.{key}': value for key, value in report.metrics.items()})
        result.margins.update({f'{prefix}.{key}': value for key, value in report.margins.items()})
        if not selected:
            return
        result.ran = result.ran and report.ran and report.error is None
        if report.error:
            result.error = '; '.join(filter(None, (result.error, f'state={i}: {report.error}')))
        violations = list(report.violations)
        if (not report.ran or report.error) and not any(v.rule_id.startswith('checker.') for v in violations):
            violations.append(Violation(rule_id='checker.internal_error', severity='hard',
                call_index=instance.call_index, message=report.error or 'In-port calculation unavailable.'))
        for violation in violations:
            rule = violation.rule_id
            if physical and not rule.startswith('checker.'):
                rule = _RULE
            result.violations.append(violation.model_copy(update={'rule_id': rule,
                'move_index': (i-1 if i else None) if i is not None else violation.move_index,
                'message': f'[in_port state={i} k={k} phase={phase}] {violation.message}'}))
            if i is not None:
                result.metrics.setdefault('failure.state_index', float(i))
                result.metrics.setdefault('failure.move_index', float(i-1))
            result.metrics.setdefault('failure.k', float(k))

    for i, report in enumerate(arrival):
        report.metrics.update(arrival_metadata[i])
        # A numerical error is not evidence for a physical ballast changeover.
        internal = not report.ran or report.error is not None or any(v.rule_id.startswith('checker.') for v in report.violations)
        merge(report, i, 'arrival', selected=i < k or internal)
    for offset, report in enumerate(departure):
        report.metrics.update(departure_metadata[offset])
        merge(report, k+offset, 'departure')
    selected_ships = list(arrival_ships[:k]) + list(departure_ships)
    selected_stability_reports = list(arrival[:k]) + departure
    if d74:
        from stowage.checker.in_port_rates import check_programme
        from stowage.checker.in_port_report import attach_programme
        try:
            selected_ships, selected_stability_reports, programme = check_programme(instance, plan, states,
                selected_ships, selected_stability_reports, context, k=k,
                workers=workers, evaluator_factory=evaluator_factory, merge=merge,
                telemetry=telemetry, prepass_error=not result.ran)
            if programme is not None and telemetry is not None:
                count = len(states)-k+int(programme['pump_window_lifts'])
                telemetry['acceptance_state_count'] = count
                measured = telemetry.get('t_hat_accept_s')
                if measured is not None or count == 0:
                    telemetry['acceptance_budget_s'] = max(10.,2.*(measured or 0.)*count/workers)
            if programme is not None:
                result = attach_programme(result, programme)
        except Exception as error:
            message = f'Ballast programme: {type(error).__name__}: {error}'
            merge(PartReport(name='in_port', ran=False, error=message,
                violations=[Violation(rule_id='checker.internal_error', severity='hard',
                    call_index=instance.call_index, message=message)]), None,
                  'ballast_programme', physical=False)
    try:
        strength_context = _strength_context(instance)
        samples = _sample_indices(instance, executed, selected_ships, strength_context)
    except Exception as error:
        message = f'Strength sampling: {type(error).__name__}: {error}'
        merge(PartReport(name='in_port', ran=False, error=message,
            violations=[Violation(rule_id='checker.internal_error', severity='hard',
                call_index=instance.call_index, message=message)]), 0, 'sampling')
        return result
    result.metrics['strength_sample_count'] = float(len(samples))
    for i in samples:
        phase = 'arrival' if i < k else 'departure'
        # Explicit harbour stations are already on strength_context.hull.
        strength = _run(instance, check_strength, strength_context, selected_ships[i],
                        call_index=instance.call_index, in_port=False)
        strength = _strength_numerical_errors(strength)
        if d74:
            from stowage.checker.in_port_consistency import check_strength_consistency
            strength = check_strength_consistency(strength, selected_stability_reports[i],
                                                   call_index=instance.call_index)
        merge(strength, i, f'{phase}.strength')
        if any(move.kind in {'discharge', 'restow_shift', 'restow_off', 'restow_on'} for move in executed):
            rules = _run(instance, check_static_rules, instance, selected_ships[i],
                         call_index=instance.call_index)
            rules = rules.model_copy(update={'violations': [v for v in rules.violations if v.rule_id in _STACK_RULES or v.rule_id.startswith('checker.')],
                'metrics': {}, 'margins': {key: value for key, value in rules.margins.items() if key in {'stack_mass_kg', 'stack_height_m'}}})
            merge(rules, i, f'{phase}.static')
    return result
