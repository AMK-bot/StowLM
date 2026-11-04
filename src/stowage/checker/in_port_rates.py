"""D74 delay reporting and hard programme checks after independent state checks.

APPROXIMATION: checking both fully charged-FSM endpoint fills represents the
intermediate pumping window; nonlinear KM between fills is not searched.
The HEEL sweep uses linearised bands; every changed split is checked exactly.
"""
from __future__ import annotations

from time import monotonic

from stowage.checker.in_port_heel import heel_outer_band, ship_at_split
from stowage.checker.in_port_lazy import lazy_heel_path
from stowage.checker.in_port_programme import (
    changeover_programme, programme_parameters,
)
from stowage.checker.stability_batch import _failed
from stowage.model import PartReport


def check_programme(instance, plan, states, selected_ships, selected_reports,
                    context, *, k, workers, evaluator_factory, merge,
                    telemetry=None, prepass_error=False):
    """Return actual ships, their matching stability reports, and typed fields.

    ``merge(report,index,phase,physical=...)`` retains numerical errors as well
    as physical violations in the caller's report. A None index is plan-level.
    Runtime component measurements never enter deterministic report metrics.
    The returned actual ships use the chosen HEEL path for sampled strength.
    Reports track that exact split and operational ballast endpoint, never the
    other pumping endpoint or a stale ideal-split equilibrium.
    """
    selected_reports = list(selected_reports)
    started = monotonic()
    costs = dict(r1_s=0., window_s=0., interval_s=0., sweep_s=0., exact_s=0.)
    if prepass_error:
        # A numerical failure does not establish the physical changeover k.
        # Never turn that unknown into a rate-infeasible search diagnosis.
        if telemetry is not None:
            telemetry.update({f'programme_{key}': value for key,value in costs.items()})
            telemetry.update(programme_total_s=monotonic()-started,
                programme_skipped='per_state_internal_error')
        return selected_ships, selected_reports, None
    parameters = programme_parameters(instance)
    point = monotonic()
    r1 = changeover_programme(instance, context.departure_ship, k=k,
                              lift_count=len(plan.moves), parameters=parameters)
    costs['r1_s'] = monotonic()-point
    pair = context.pair
    ideals = [ship.tank_masses_kg.get(pair.port_tank, 0.) for ship in selected_ships]
    fields = dict(r1.metrics, heel_step_limit_t=parameters.heel_step_limit_kg/1000.,
        heel_max_ideal_step_t=max((abs(b-a) for a,b in zip(ideals, ideals[1:])),
                                 default=0.)/1000.,
        heel_max_step_t=None, heel_path_feasible=None,
        heel_path_linearised_miss=False)

    def finish(ships):
        if telemetry is not None:
            telemetry.update({f'programme_{key}': value for key,value in costs.items()})
            telemetry['programme_total_s'] = monotonic()-started
        return ships, selected_reports, fields

    rate_report = PartReport(name='ballast_programme',
        metrics={'ballast_delay_h': fields['ballast_delay_h']})
    merge(rate_report, None, 'ballast_programme', physical=False)

    def programme_failure(report):
        # Window endpoint and chosen-path physical failures belong to D74's
        # hard programme rule. Numerical diagnostics keep their checker IDs.
        violations = []
        for violation in report.violations:
            if violation.rule_id.startswith('checker.'):
                violations.append(violation)
            else:
                violations.append(violation.model_copy(update={'rule_id': 'port.ballast.transfer_rate'}))
        return report.model_copy(update={'violations': violations})
    # Neither an internal error nor an already failed physical state is proof
    # that a rate-constrained path is impossible. Keep its original diagnosis.
    if any(_failed(report) for report in selected_reports):
        return finish(selected_ships)

    windows = set(r1.window_indices)
    window_ships, window_reports = {}, {}
    worked = context.for_phase('departure')
    point = monotonic()
    for phase in ('arrival', 'departure'):
        indices = list(r1.window_indices)
        if not indices:
            continue
        ships = [context.transferred_ship(states[i], phase=phase)[0] for i in indices]
        with evaluator_factory(instance, worked, workers=workers,
                               warm_start=False, heel_intervals=True) as evaluator:
            batch = evaluator.evaluate(ships)
            reports = batch.reports
        if (phase == 'departure' and telemetry is not None
                and telemetry.get('t_hat_accept_s') is None):
            # B.6 has no pass2 sample at the no-changeover sentinel. Label this
            # actual departure-window sample explicitly, never reuse pass1.
            telemetry.update(t_hat_accept_s=batch.measured_state_time_s,
                t_hat_accept_source='window_departure_no_suffix',
                t_hat_accept_state_count=batch.measured_state_count)
        if len(reports) != len(indices):
            raise RuntimeError('Incomplete D74 pumping-window endpoint evaluation.')
        for i,ship,report in zip(indices,ships,reports,strict=True):
            window_ships[i,phase], window_reports[i,phase] = ship, report
            if phase == ('arrival' if i < k else 'departure'):
                selected_reports[i] = report
            merge(programme_failure(report), i, f'window.{phase}', physical=False)
    costs['window_s'] = monotonic()-point
    if any(_failed(report) for report in window_reports.values()):
        return finish(selected_ships)

    bands, certificates, outer_bands = [], [], {}
    for i in range(len(states)):
        if i not in windows:
            outer = heel_outer_band(context, selected_ships[i], selected_reports[i])
            outer_bands[i] = outer
            bands.append((outer.lo_kg, outer.hi_kg))
            certificates.append(outer.ideal_certified)
            continue
        proofs = [window_reports[i,phase] for phase in ('arrival','departure')]
        certificates.append(False)  # Already exact: both endpoint bands intersect.
        if any(report.metrics.get('heel_interval_feasible') != 1. for report in proofs):
            bands.append(None)
        else:
            # One continuous HEEL path must be safe at both window endpoints.
            bands.append((max(report.metrics['heel_lo_kg'] for report in proofs),
                          min(report.metrics['heel_hi_kg'] for report in proofs)))
    reports_by_state = {}
    class UnavailableInterval(Exception):
        pass
    def refine(indices):
        point = monotonic()
        refined = {}
        for phase in ('arrival', 'departure'):
            group = [i for i in indices if phase == ('arrival' if i < k else 'departure')]
            if not group:
                continue
            with evaluator_factory(instance, context.for_phase(phase), workers=workers,
                                   warm_start=False, heel_intervals=False) as evaluator:
                reports = evaluator.evaluate_intervals(
                    [selected_ships[i] for i in group],
                    [selected_reports[i] for i in group]).reports
            if len(reports) != len(group):
                raise RuntimeError('Incomplete D74 HEEL interval evaluation.')
            for i,report in zip(group,reports,strict=True):
                reports_by_state[i] = report
                merge(report, i, f'{phase}.heel_interval', physical=True)
                refined[i] = ((report.metrics['heel_lo_kg'], report.metrics['heel_hi_kg'])
                    if report.metrics.get('heel_interval_feasible') == 1. else None)
        costs['interval_s'] += monotonic()-point
        if any(_failed(report) for report in reports_by_state.values()):
            # All numerical diagnostics were merged. They do not establish an
            # empty physical band or a rate-infeasible path.
            raise UnavailableInterval
        return refined

    point = monotonic()
    try:
        lazy = lazy_heel_path(bands, ideals, certified_ideals=certificates,
            exact_indices=windows, refine=refine,
            arrival_split_kg=instance.onboard.tank_masses_kg.get(pair.port_tank,0.),
            parameters=parameters, call_index=instance.call_index)
    except UnavailableInterval:
        return finish(selected_ships)
    costs['sweep_s'] = monotonic()-point-costs['interval_s']
    path, bands = lazy.path, lazy.intervals
    if telemetry is not None:
        telemetry.update(programme_interval_state_count=len(lazy.refined_indices),
            programme_interval_unrefined_count=len(lazy.unrefined_indices),
            programme_interval_refinement_rounds=lazy.refinement_rounds)
    fields.update(path.metrics)
    merge(PartReport(name='ballast_programme', violations=list(path.violations)),
          path.failure_index, 'ballast_programme.heel', physical=False)
    if not path.feasible:
        return finish(selected_ships)

    chosen = [ship_at_split(context, ship, mass)
              for ship,mass in zip(selected_ships,path.path_kg,strict=True)]
    for i,(ship,band,ideal) in enumerate(zip(chosen,bands,ideals,strict=True)):
        metrics = dict(port_mass_kg=ship.tank_masses_kg[pair.port_tank],
            starboard_mass_kg=ship.tank_masses_kg[pair.starboard_tank],
            ideal_port_mass_kg=ideal)
        if i in lazy.unrefined_indices:
            metrics.update(outer_bands[i].metrics())
        else:
            metrics.update(heel_lo_kg=band[0], heel_hi_kg=band[1],
                           heel_interval_is_outer_bound=0.)
        if i:
            metrics['heel_step_kg'] = abs(path.path_kg[i]-path.path_kg[i-1])
        else:
            metrics['heel_pre_operation_transfer_kg'] = abs(
                path.path_kg[0]-instance.onboard.tank_masses_kg.get(pair.port_tank,0.))
        merge(PartReport(name='ballast_programme',metrics=metrics),i,'heel_path',physical=True)
    point = monotonic()
    tasks = []
    for i in range(len(states)):
        phase = 'arrival' if i < k else 'departure'
        endpoints = ('arrival','departure') if i in windows else (phase,)
        for endpoint in endpoints:
            source = window_ships[i,endpoint] if i in windows else selected_ships[i]
            if path.path_kg[i] != source.tank_masses_kg[pair.port_tank]:
                tasks.append((i,endpoint,ship_at_split(context,source,path.path_kg[i]),
                              'departure' if i in windows else endpoint))
    for fsm in ('arrival','departure'):
        group = [task for task in tasks if task[3] == fsm]
        if not group:
            continue
        with evaluator_factory(instance, context.for_phase(fsm), workers=workers,
                               warm_start=False, heel_intervals=False) as evaluator:
            reports = evaluator.evaluate([task[2] for task in group]).reports
        if len(reports) != len(group):
            raise RuntimeError('Incomplete exact D74 chosen-path evaluation.')
        for (i,phase,ship,_),report in zip(group,reports,strict=True):
            if phase == ('arrival' if i < k else 'departure'):
                selected_reports[i] = report
            merge(programme_failure(report), i, f'heel_path.{phase}', physical=False)
    costs['exact_s'] = monotonic()-point
    if telemetry is not None:
        telemetry['programme_exact_state_count'] = len(tasks)
        telemetry['programme_window_state_count'] = 2*len(windows)
    return finish(chosen)
