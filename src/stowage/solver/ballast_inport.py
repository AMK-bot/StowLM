"""D64/D74 finisher using 4.1's liquid, programme and state-timing APIs."""
from __future__ import annotations

import math
from statistics import median
import time
from types import SimpleNamespace

import numpy as np

from stowage.model import PartReport
from stowage.solver.ballast_deadline import (
    BallastDeadline, ExactDeadline, evaluate_in_port_with_deadline, exact_budget,
)


class InPortUnknown(RuntimeError):
    """Incomplete physics is compute-undecided, never negative feedback."""

    def __init__(self, message, *, cause='unknown_witness'):
        super().__init__(message)
        self.cause = cause


def _unavailable(report):
    return (not report.ran or report.error is not None or
            any(v.rule_id.startswith('checker.') for v in report.violations))


def _timed_out(message):
    return any(token in message.lower() for token in ('timeout', 'time budget', 'deadline'))


def _checkpoint(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise ExactDeadline


def _within(deadline, function, *args, **kwargs):
    """Protect finite Python setup as well as the exact evaluator phase."""
    if deadline is None:
        return function(*args, **kwargs)
    with exact_budget(deadline):
        return function(*args, **kwargs)


class InPortFinisher:
    """Immutable arrival changeover/calibration, candidate-specific pass 2."""

    def __init__(self, instance, plan, *, workers=1):
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError('workers must be a positive integer')
        self.instance, self.plan, self.workers = instance, plan, workers
        self.states, self.k_arr = [], 0
        self.failure, self.research_count = None, 0
        self.failure_cause = None
        self.failed_states = set()
        self.failed_state_phases = {}
        self.prepared = False
        self._arrival_pass = False
        self.t_hat_s, self.t_hat_state_count = 0., 0
        self.t_hat_accept_s, self.t_hat_accept_state_count = 0., 0
        self.ballast_programme = {}
        self._pump_window_lifts = 0
        self._active_context = None
        self._candidate_key, self._candidate_context = None, None
        self.tanks = {t.id: t for t in instance.vessel.tanks}
        pairs = [p for p in instance.vessel.tank_pairs if p.role == 'anti_heeling']
        if len(pairs) != 1:
            raise ValueError('D64 requires exactly one explicit anti-heeling TankPair')
        self.heel = (pairs[0].port_tank, pairs[0].starboard_tank)
        self.total = sum(instance.onboard.tank_masses_kg.get(tid, 0.) for tid in self.heel)
        from stowage.checker.in_port_liquids import _mirrored
        ordinary = {t.id: t for t in instance.vessel.tanks
                    if t.adjustable and t.id not in self.heel}
        self.symmetric = []
        # APPROXIMATION: non-HEEL departure ballast uses equal mirrored fills
        # (D64 rev3.1); real crews may use other ballast pairs.
        for tid in sorted(tuple(ordinary)):
            if tid not in ordinary:
                continue
            tank = ordinary.pop(tid)
            if all(abs(row.tcg_m) < 1e-9 for row in tank.table):
                continue
            matches = [t for t in ordinary.values() if _mirrored(tank, t)]
            if len(matches) != 1:
                raise ValueError(f'non-HEEL tank {tid} needs one unambiguous symmetric mate')
            mate = matches[0]
            ordinary.pop(mate.id)
            self.symmetric.append((tid, mate.id))

    def _symmetric_masses(self, masses):
        result = {**self.instance.onboard.tank_masses_kg, **masses}
        for p, s in self.symmetric:
            capacity = min(self.tanks[tid].capacity_m3*self.tanks[tid].content_density_kg_m3
                           for tid in (p, s))
            result[p] = result[s] = float(np.clip(
                (result.get(p, 0.)+result.get(s, 0.))/2., 0., capacity))
        # Search has no HEEL degrees of freedom: its actual arrival total is
        # retained and 4.1 alone redistributes it from the exact loading moment.
        for tid in self.heel:
            result[tid] = self.instance.onboard.tank_masses_kg.get(tid, 0.)
        return result

    def _context_for(self, masses):
        from stowage.checker.in_port_liquids import prepare_context
        key = tuple(sorted(masses.items()))
        if key != self._candidate_key:
            ship = self.states[-1] if self.states else self.instance.onboard
            departure = ship.model_copy(update={'tank_masses_kg': dict(masses)})
            self._candidate_context = prepare_context(self.instance, departure)
            self._candidate_key = key
        return self._candidate_context

    def redistribute(self, ship, masses):
        context = self._context_for({**self.instance.onboard.tank_masses_kg, **masses})
        return context.transferred_ship(ship, phase='departure')[0].tank_masses_kg

    def project(self, masses):
        symmetric = self._symmetric_masses(masses)
        ship = self.states[-1] if self.states else self.instance.onboard
        return self.redistribute(ship, symmetric)

    def _rate_parameters(self):
        from stowage.checker.in_port_programme import programme_parameters
        try:
            return programme_parameters(self.instance)
        except (ValueError, AttributeError, TypeError) as exc:
            raise InPortUnknown(f'D74 programme parameters unavailable: {exc}') from exc

    def _changeover(self, masses):
        from stowage.checker.in_port_programme import changeover_programme
        departure = SimpleNamespace(tank_masses_kg={**self.instance.onboard.tank_masses_kg, **masses})
        return changeover_programme(self.instance, departure, k=self.k_arr,
            lift_count=len(self.states)-1, parameters=self._rate_parameters())

    def _pump_changes(self, masses):
        arrival = self.instance.onboard.tank_masses_kg
        changes = [masses.get(tid, arrival.get(tid, 0.))-arrival.get(tid, 0.)
            for tid, tank in self.tanks.items() if tank.adjustable and tid not in self.heel]
        return sum(max(0., d) for d in changes), sum(max(0., -d) for d in changes)

    def pumped_mass(self, masses):
        """D74's lower bound on pumped mass, in kg (internal transfer once)."""
        return max(self._pump_changes(masses))

    def rate_lp_data(self):
        p = self._rate_parameters()
        available = p.pre_operation_allowance_h+self.k_arr*p.t_lift_s/3600.
        if self.k_arr == len(self.states):
            available += p.post_operation_allowance_h
        capacity = p.water_density_kg_m3*p.ballast_pump_m3_h*available
        return dict(capacity_kg=capacity, arrival_masses=dict(self.instance.onboard.tank_masses_kg),
            heel_ids=self.heel)

    def rate_margin(self, masses):
        capacity = self.rate_lp_data()['capacity_kg']
        pumped = self.pumped_mass(masses)
        if capacity == 0:
            return 0. if pumped == 0 else -1.
        return (capacity-pumped)/capacity

    def programme_metrics(self, masses):
        return dict(self._changeover(masses).metrics)

    def _check_states(self, states, *, deadline=None):
        from stowage.checker.in_port_evaluator import InPortEvaluator
        _checkpoint(deadline)
        evaluator = getattr(self, '_prepass_evaluator', None) if self._arrival_pass else None
        if evaluator is None:
            evaluator = _within(deadline, InPortEvaluator, self.instance, self._active_context,
                workers=self.workers, warm_start=not self._arrival_pass,
                heel_intervals=getattr(self, '_need_heel_intervals', True))
            if self._arrival_pass:
                self._prepass_evaluator = evaluator
        if deadline is not None:
            _checkpoint(deadline)
            return evaluate_in_port_with_deadline(evaluator, states, deadline=deadline,
                close=not self._arrival_pass)
        with evaluator:
            return evaluator.evaluate(states, stop_at_first_failure=True)

    def prepare(self):
        try:
            return self._prepare()
        except (BallastDeadline, ExactDeadline) as exc:
            self.failure_cause = 'timeout:prepass'
            raise InPortUnknown('arrival prepass budget exhausted', cause='timeout:prepass') from exc

    def _prepare(self):
        if self.prepared:
            return self
        from stowage.checker.stability_states import InPortStateBuilder
        from stowage.checker.in_port_prepass import arrival_prepass, InPortPrepassTimeout
        started = time.monotonic()
        # No unmeasured prepass is allowed. Calibration starts under the floor;
        # once measured, its formula replaces that provisional allowance.
        deadline = started+30.
        builder = InPortStateBuilder(self.instance)
        self.states = [builder.state.ship]
        for i, move in enumerate(self.plan.moves):
            _checkpoint(deadline)
            self.states.append(builder.push(move, move_index=i).ship)
            _checkpoint(deadline)
        context = _within(deadline, self._context_for, self._symmetric_masses({}))
        _checkpoint(deadline)
        self._active_context = context.for_phase('arrival')
        arrival = []
        for ship in self.states:
            _checkpoint(deadline)
            arrival.append(_within(deadline, context.transferred_ship, ship, phase='arrival')[0])
            _checkpoint(deadline)
        self._arrival_pass, self._calibrating = True, False
        try:
            # The shared 4.1 helper owns chronological chunks and earliest k;
            # 4.2 wraps each chunk with its owned-pool deadline adapter, including
            # caller-side preparation in the same plan clock.
            measured = []
            def evaluate(chunk, *, stop_at_first_failure):
                nonlocal deadline
                _checkpoint(deadline)
                batch = self._check_states(chunk, deadline=deadline)
                measured.extend(getattr(batch, 'state_times_s', ())[:8-len(measured)])
                if measured:
                    deadline = started+max(30., 2.*median(measured)*len(self.states)/self.workers)
                    pool = getattr(getattr(self, '_prepass_evaluator', None), '_pool', None)
                    if pool is not None and hasattr(pool, 'deadline'):
                        pool.deadline = deadline
                _checkpoint(deadline)
                return batch
            prepass = arrival_prepass(evaluate, arrival, workers=self.workers,
                calibrated_deadline=True)
            self.t_hat_s = prepass.measured_state_time_s
            self.t_hat_state_count = prepass.measured_state_count
            if not math.isfinite(self.t_hat_s) or self.t_hat_s <= 0:
                raise InPortUnknown('pass-1 state timing calibration is unavailable')
            self.pass1_budget_s = max(30., 2.*self.t_hat_s*len(self.states)/self.workers)
            deadline = started+self.pass1_budget_s
            _checkpoint(deadline)
            reports = list(prepass.reports)
            self.k_arr = len(self.states)
            for index, report in enumerate(reports):
                if _unavailable(report):
                    message = report.error or '; '.join(v.rule_id for v in report.violations)
                    raise InPortUnknown(f'in-port state {index}: arrival calculation unavailable: '+
                        message, cause='timeout:prepass' if _timed_out(message) else 'unknown_witness')
                if any(v.severity == 'hard' for v in report.violations):
                    self.k_arr = min(self.k_arr, index)
            self.arrival_reports, self.arrival_ships = reports, arrival
            self.prepared = True
        except (BallastDeadline, ExactDeadline, InPortPrepassTimeout) as exc:
            self.failure_cause = 'timeout:prepass'
            raise InPortUnknown('arrival prepass budget exhausted', cause='timeout:prepass') from exc
        finally:
            self._arrival_pass = self._calibrating = False
            evaluator = getattr(self, '_prepass_evaluator', None)
            self._prepass_evaluator = None
            if evaluator is not None:
                evaluator.close()
        return self

    def _moment(self, ship):
        # APPROXIMATION: model sounding centres below are used for risk ranking
        # only. 4.1's exact geometry/soundings own HEEL and acceptance physics.
        from stowage.checker.stability_loading import loading_condition
        condition = loading_condition(self.instance, ship.model_copy(update={'tank_masses_kg': {}}))
        if condition.errors:
            raise ValueError('; '.join(condition.errors))
        mass = condition.mass_kg
        x, y = mass*condition.lcg_m, mass*condition.tcg_m
        for tid, weight in ship.tank_masses_kg.items():
            tank = self.tanks[tid]
            volume = weight/tank.content_density_kg_m3
            volumes = [row.volume_m3 for row in tank.table]
            x += weight*float(np.interp(volume, volumes, [r.lcg_m for r in tank.table]))
            y += weight*float(np.interp(volume, volumes, [r.tcg_m for r in tank.table]))
            mass += weight
        return mass, x/mass, y/mass

    def _risk(self, ship):
        mass, lcg, tcg = self._moment(ship)
        hull = self.instance.vessel.hull
        length = hull.lpp_m if hull is not None else 1.
        # APPROXIMATION: these trim/list-moment/mean-draught surrogates only
        # order exact checks; they never authorize or reject a state.
        trim = abs(lcg-length/2.)/max(length, 1.)
        return max(trim, abs(tcg)/max(length, 1.)), trim, abs(mass*tcg), mass

    @property
    def acceptance_budget_s(self):
        # 4.1 defines n as the state count, so the inclusive suffix is n-k.
        return max(10., 2.*getattr(self, 't_hat_accept_s', 0.)*
            (len(self.states)-self.k_arr+getattr(self, '_pump_window_lifts', 0))/self.workers)

    def _ranked_suffix(self, context, deadline):
        return self._ranked_departure(context, range(self.k_arr, len(self.states)), deadline)

    def _ranked_departure(self, context, indices, deadline):
        indexed = []
        for i in indices:
            _checkpoint(deadline)
            ship = _within(deadline, context.transferred_ship, self.states[i], phase='departure')[0]
            _checkpoint(deadline)
            indexed.append((i, ship))
        ranked = []
        for item in indexed:
            _checkpoint(deadline)
            ranked.append((_within(deadline, self._risk, item[1]), item))
            _checkpoint(deadline)
        _within(deadline, ranked.sort, key=lambda item: item[0], reverse=True)
        _checkpoint(deadline)
        return [item for _, item in ranked]

    def _batch_failure(self, indexed, batch, *, phase='departure', feedback=True):
        if batch.failure_index is None:
            if len(batch.reports) != len(indexed):
                raise InPortUnknown('incomplete pass-2 acceptance')
            return None
        index = indexed[batch.failure_index][0]
        failed = batch.reports[batch.failure_index]
        if _unavailable(failed):
            message = failed.error or '; '.join(v.rule_id for v in failed.violations)
            cause = 'timeout:acceptance' if _timed_out(message) else 'unknown_witness'
            raise InPortUnknown(f'in-port state {index}: calculation unavailable: '+message, cause=cause)
        if feedback:
            self.failed_states.add(index)
            if not hasattr(self, 'failed_state_phases'):
                self.failed_state_phases = {}
            self.failed_state_phases[index] = phase
        self.failure_cause = 'acceptance_failure:'+next((v.rule_id for v in failed.violations
            if v.severity == 'hard'), 'port.stability.cargo_operations')
        self.failure = f'in-port state {index} failed: '+'; '.join(v.rule_id for v in failed.violations)
        report = failed.model_copy(deep=True, update={'name': 'in_port', 'error': self.failure})
        report.metrics['failure_state_index'] = float(index)
        return report

    def _accept_rate(self, masses):
        self._changeover_result = self._changeover(masses)
        self.ballast_programme = dict(self._changeover_result.metrics)
        # D74 user decision: ballast pumping can delay the operation, but the
        # rate alone cannot reject it. The shared helper owns ballast_delay_h
        # and the hard endpoint obligations, including the delayed k=0 state.
        self._pump_window_lifts = int(self.ballast_programme['pump_window_lifts'])
        return None

    def _accept_window(self, context, deadline):
        # APPROXIMATION: D74 endpoint checks bound the partial-fill pumping
        # window. Actual intermediate fills are not separately equilibrated.
        indices = self._changeover_result.window_indices
        self._active_context = context.for_phase('departure')
        self._window_arrival_reports = {}
        self._window_departure_reports = {}
        self._window_endpoint_ships = {}
        for phase in ('arrival', 'departure'):
            # One continuous path must lie inside BOTH endpoint bands, with
            # candidate-worked maximum FSM on each endpoint's actual fills.
            self._need_heel_intervals = True
            indexed = []
            for i in indices:
                _checkpoint(deadline)
                calibrated = getattr(self, '_window_departure_calibration', {}).get(i)
                if phase == 'departure' and calibrated is not None:
                    candidate, report = calibrated
                    self._window_endpoint_ships[i, phase] = candidate
                    self._window_departure_reports[i] = report
                    continue
                candidate = _within(deadline, context.transferred_ship, self.states[i], phase=phase)[0]
                _checkpoint(deadline)
                indexed.append((i, candidate))
                self._window_endpoint_ships[i, phase] = candidate
            ranked = []
            for item in indexed:
                _checkpoint(deadline)
                ranked.append((_within(deadline, self._risk, item[1]), item))
                _checkpoint(deadline)
            _within(deadline, ranked.sort, key=lambda item: item[0], reverse=True)
            _checkpoint(deadline)
            indexed = [item for _, item in ranked]
            if indexed:
                batch = self._check_states([ship for _, ship in indexed], deadline=deadline)
                proofs = (self._window_arrival_reports if phase == 'arrival'
                          else self._window_departure_reports)
                proofs.update((item[0], report) for item, report in zip(indexed, batch.reports))
                failure = self._batch_failure(indexed, batch, phase=phase)
                if failure is not None:
                    return failure
        self._need_heel_intervals = True
        return None

    def _accept_heel_path(self, context, deadline):
        """4.1 owns interval physics/path; 4.2 owns sequencing and deadline."""
        from stowage.checker.in_port_programme import heel_path
        from stowage.checker.in_port_heel import ship_at_split
        reports = {i: report for i, report in enumerate(self.arrival_reports) if i < self.k_arr}
        reports.update(self._suffix_reports)
        intervals, ideals = [], []
        selected = []
        window = set(self._changeover_result.window_indices)
        for i in range(len(self.states)):
            _checkpoint(deadline)
            phase = 'arrival' if i < self.k_arr else 'departure'
            phases = ('arrival', 'departure') if i in window else (phase,)
            endpoints, bands = [], []
            for endpoint in phases:
                _checkpoint(deadline)
                if i in window:
                    proofs = (self._window_arrival_reports if endpoint == 'arrival'
                              else self._window_departure_reports)
                    report = proofs.get(i)
                    candidate = self._window_endpoint_ships.get((i, endpoint))
                else:
                    report = reports.get(i)
                    candidate = (self.arrival_ships[i] if endpoint == 'arrival' else
                        _within(deadline, context.transferred_ship, self.states[i], phase='departure')[0])
                if report is None or candidate is None or _unavailable(report):
                    raise InPortUnknown(f'in-port state {i}: {endpoint} HEEL path calculation unavailable')
                fields = ('heel_lo_kg', 'heel_hi_kg', 'heel_ideal_split_kg')
                own_ideal = candidate.tank_masses_kg.get(self.heel[0])
                if (any(key not in report.metrics or not math.isfinite(report.metrics[key]) for key in fields)
                        or own_ideal is None or not math.isfinite(own_ideal)):
                    raise InPortUnknown(f'in-port state {i}: {endpoint} HEEL interval unavailable')
                bands.append((report.metrics['heel_lo_kg'], report.metrics['heel_hi_kg']))
                fsm = 'departure' if i in window or endpoint == 'departure' else 'arrival'
                endpoints.append((context.for_phase(fsm), candidate, own_ideal))
                if endpoint == phase:
                    # Match 4.1: target the selected actual phase's ideal,
                    # independently of the additional window endpoint band.
                    ideals.append(own_ideal)
            intervals.append((max(band[0] for band in bands), min(band[1] for band in bands)))
            selected.append(endpoints)
            _checkpoint(deadline)
        path = _within(deadline, heel_path, intervals, ideals,
            arrival_split_kg=self.instance.onboard.tank_masses_kg.get(self.heel[0], 0.),
            parameters=self._rate_parameters(), call_index=self.instance.call_index)
        _checkpoint(deadline)
        self.ballast_programme.update(path.metrics)
        if not path.feasible:
            self.failure_cause = 'rate_infeasible:R2'
            self.failure = f'in-port state {path.failure_index}: '+path.violations[0].message
            return PartReport(name='in_port', violations=list(path.violations), error=self.failure)
        ranked = []
        for i, endpoints in enumerate(selected):
            for state_context, ship, own_ideal in endpoints:
                _checkpoint(deadline)
                # changed_indices only compares the selected phase's ideal.
                # The other window endpoint can require an exact check even
                # when the selected split was unchanged by the sweep.
                if path.path_kg[i] == own_ideal:
                    continue
                candidate = _within(deadline, ship_at_split, state_context, ship, path.path_kg[i])
                ranked.append((_within(deadline, self._risk, candidate), i, state_context, candidate))
                _checkpoint(deadline)
        _within(deadline, ranked.sort, key=lambda item: item[0], reverse=True)
        _checkpoint(deadline)
        # Contexts vary across the changeover/window. Preserve global risk
        # order by flushing each consecutive same-FSM group independently.
        self._need_heel_intervals = False
        offset = 0
        while offset < len(ranked):
            _checkpoint(deadline)
            _, _, state_context, _ = ranked[offset]
            group, end = [], offset
            while end < len(ranked) and ranked[end][2].max_fsm_tank_ids == state_context.max_fsm_tank_ids:
                _, index, _, ship = ranked[end]
                group.append((index, ship))
                end += 1
            self._active_context = state_context
            batch = self._check_states([ship for _, ship in group], deadline=deadline)
            # A failure at a rate-constrained nonideal split cannot be fed
            # back as a constraint at the ideal split. It remains a completed
            # acceptance rejection; another split is never claimed impossible.
            failure = self._batch_failure(group, batch, feedback=False)
            if failure is not None:
                return failure
            offset = end
        self._need_heel_intervals = True
        return None

    def calibrate_acceptance(self, masses):
        """Calibrate pass 2 under the 10s floor, charging setup to acceptance.

        The public W1 caller watches this phase separately, then uses the
        measured total deadline for the remaining work. Reports are cached only
        for this one acceptance, so calibration is not evaluated a second time.
        """
        self.acceptance_started = time.monotonic()
        self._acceptance_cache = None
        self._suffix_reports = {}
        self._window_departure_calibration = {}
        self._need_heel_intervals = True
        key = tuple(sorted(masses.items()))
        deadline = self.acceptance_started+self.acceptance_budget_s
        _checkpoint(deadline)
        report = _within(deadline, self._accept_rate, masses)
        _checkpoint(deadline)
        if report is not None:
            self._acceptance_cache = (key, None, [], 0, report)
            return self
        context = _within(deadline, self._context_for,
            {**self.instance.onboard.tank_masses_kg, **masses})
        _checkpoint(deadline)
        self._active_context = context.for_phase('departure')
        indexed = self._ranked_suffix(context, deadline)
        checked = 0
        if not getattr(self, 't_hat_accept_s', 0.):
            # Measure pass 2 before the complete two-endpoint window can spend
            # the provisional floor. At k=n+1 there is no suffix: match 4.1's
            # explicitly labelled maximum-FSM departure-window timing source.
            sample = indexed[:8] if indexed else self._ranked_departure(context,
                self._changeover_result.window_indices, deadline)[:8]
        else:
            sample = []
        if sample:
            batch = self._check_states([ship for _, ship in sample], deadline=deadline)
            if indexed:
                self._suffix_reports.update((item[0], part) for item, part in zip(sample, batch.reports))
                checked = len(batch.reports)
                self.t_hat_accept_source = 'departure_suffix'
            else:
                self._window_departure_calibration.update((item[0], (item[1], part))
                    for item, part in zip(sample, batch.reports))
                self.t_hat_accept_source = 'window_departure_no_suffix'
            report = self._batch_failure(sample, batch)
            timing = getattr(batch, 'measured_state_time_s', 0.)
            if math.isfinite(timing) and timing > 0:
                self.t_hat_accept_s = timing
                self.t_hat_accept_state_count = batch.measured_state_count
            elif report is None:
                raise InPortUnknown('pass-2 state timing calibration is unavailable')
        _checkpoint(self.acceptance_started+self.acceptance_budget_s)
        self._acceptance_cache = (key, context, indexed, checked, report)
        return self

    def accept(self, masses):
        key = tuple(sorted(masses.items()))
        cached = getattr(self, '_acceptance_cache', None)
        report = None
        try:
            if cached is None or cached[0] != key:
                self.calibrate_acceptance(masses)
                cached = self._acceptance_cache
            _, context, indexed, checked, report = cached
            deadline = self.acceptance_started+self.acceptance_budget_s
            if context is not None:
                self._active_context = context.for_phase('departure')
            _checkpoint(deadline)
            if report is None:
                report = self._accept_window(context, deadline)
                _checkpoint(deadline)
            if report is None and checked < len(indexed):
                remaining = indexed[checked:]
                batch = self._check_states([ship for _, ship in remaining], deadline=deadline)
                self._suffix_reports.update((item[0], part) for item, part in zip(remaining, batch.reports))
                report = self._batch_failure(remaining, batch)
            _checkpoint(deadline)
            if report is None:
                report = self._accept_heel_path(context, deadline)
                _checkpoint(deadline)
            if report is None:
                self.failure = self.failure_cause = None
                report = PartReport(name='in_port')
        except (BallastDeadline, ExactDeadline) as exc:
            detail = f' ({exc})' if str(exc) else ''
            self.failure = f'in-port state {self.k_arr}: acceptance budget exhausted{detail}'
            self.failure_cause = 'timeout:acceptance'
            report = PartReport(name='in_port', ran=False, error=self.failure)
        except InPortUnknown as exc:
            self.failure = str(exc)
            self.failure_cause = exc.cause
            report = PartReport(name='in_port', ran=False, error=self.failure)
        finally:
            self._acceptance_cache = None
        report.metrics.update(k_arr=float(self.k_arr), t_hat_s=self.t_hat_s,
            t_hat_state_count=float(self.t_hat_state_count), workers=float(self.workers),
            remaining_state_count=float(len(self.states)-self.k_arr),
            t_hat_accept_s=getattr(self, 't_hat_accept_s', 0.),
            t_hat_accept_state_count=float(getattr(self, 't_hat_accept_state_count', 0)),
            t_hat_accept_source_pass2=float(getattr(self, 't_hat_accept_state_count', 0) > 0),
            t_hat_accept_source_window_departure=float(
                getattr(self, 't_hat_accept_source', '') == 'window_departure_no_suffix'),
            acceptance_budget_s=self.acceptance_budget_s,
            elapsed_s=time.monotonic()-self.acceptance_started)
        return report

    def constraint_margins(self, masses):
        from stowage.checker.in_port_evaluator import check_state
        context = self._context_for({**self.instance.onboard.tank_masses_kg, **masses})
        context = context.for_phase('departure')
        margins = {}
        for index in sorted(self.failed_states):
            phase = getattr(self, 'failed_state_phases', {}).get(index, 'departure')
            candidate = context.transferred_ship(self.states[index], phase=phase)[0]
            report = check_state(self.instance, candidate, context, warm_start=False)
            if _unavailable(report):
                message = report.error or '; '.join(v.rule_id for v in report.violations)
                raise InPortUnknown(f'in-port state {index}: feedback calculation unavailable: '+
                    message, cause='timeout:search' if _timed_out(message) else 'unknown_witness')
            prefix = f'in_port/state_{index}/'
            margins.update({prefix+key: float(value) for key, value in report.margins.items()})
        return margins
