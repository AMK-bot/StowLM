"""D74: a single HEEL path must satisfy both worked-FSM window endpoints."""
from types import SimpleNamespace

import pytest

from stowage.checker.in_port_programme import ProgrammeParameters
from stowage.checker.stability_batch import InPortBatchResult
from stowage.model import PartReport, Violation
from stowage.solver.ballast_inport import InPortFinisher, InPortUnknown


def _case(monkeypatch, *, arrival_band=(20., 80.), departure_band=(20., 80.),
          arrival_ideal=50., departure_ideal=70.):
    policy = object.__new__(InPortFinisher)
    policy.states = [SimpleNamespace(tank_masses_kg={})]
    policy.k_arr, policy.workers = 1, 1
    policy.heel = ('HP', 'HS')
    policy.instance = SimpleNamespace(call_index=0,
        onboard=SimpleNamespace(tank_masses_kg={'HP': arrival_ideal, 'HS': 100.-arrival_ideal}))
    policy.failed_states, policy.failed_state_phases = set(), {}
    policy.failure = policy.failure_cause = None
    policy.ballast_programme = {}
    policy._changeover_result = SimpleNamespace(window_indices=(0,))
    def ship(phase, ideal):
        return SimpleNamespace(tank_masses_kg={'HP': ideal, 'HS': 100.-ideal}, phase=phase)
    endpoints = {'arrival': ship('arrival', arrival_ideal), 'departure': ship('departure', departure_ideal)}
    context = SimpleNamespace(max_fsm_tank_ids=frozenset({'HEEL', 'worked_DB'}))
    context.for_phase = lambda phase: context
    context.transferred_ship = lambda original, phase: (endpoints[phase], {})
    def proof(band, ideal):
        return PartReport(name='in_port', metrics={'heel_lo_kg': band[0], 'heel_hi_kg': band[1],
            'heel_ideal_split_kg': ideal, 'heel_interval_feasible': 1.})
    policy._window_arrival_reports = {0: proof(arrival_band, arrival_ideal)}
    policy._window_departure_reports = {0: proof(departure_band, departure_ideal)}
    policy._window_endpoint_ships = {(0, phase): value for phase, value in endpoints.items()}
    policy.arrival_reports = [proof(arrival_band, arrival_ideal)]
    policy.arrival_ships = [endpoints['arrival']]
    policy._suffix_reports = {}
    monkeypatch.setattr(policy, '_risk', lambda candidate: (0.,))
    monkeypatch.setattr(policy, '_rate_parameters', lambda: ProgrammeParameters(
        ballast_pump_m3_h=3600., heel_transfer_m3_h=3600., max_cranes=1,
        port_cranes=1, gross_moves_per_hour=3600., pre_operation_allowance_h=1.,
        post_operation_allowance_h=0., water_density_kg_m3=1.))
    import stowage.checker.in_port_heel as heel
    def chosen(context, source, mass):
        return SimpleNamespace(tank_masses_kg={'HP': mass, 'HS': 100.-mass}, phase=source.phase)
    monkeypatch.setattr(heel, 'ship_at_split', chosen)
    return policy, context


def test_window_bands_intersect_before_path_acceptance(monkeypatch):
    policy, context = _case(monkeypatch, arrival_band=(20., 40.), departure_band=(60., 80.),
                            arrival_ideal=30.)
    monkeypatch.setattr(policy, '_check_states', lambda *a, **kw: pytest.fail('No feasible intersection'))
    report = policy._accept_heel_path(context, None)
    assert report is not None and report.violations
    assert policy.failure_cause == 'rate_infeasible:R2'
    assert not policy.failed_states


@pytest.mark.parametrize('failure', [None, 'physical', 'unknown'])
def test_unchanged_selected_split_still_rechecks_other_endpoint(monkeypatch, failure):
    policy, context = _case(monkeypatch)
    seen = []
    def check(states, *, deadline=None):
        for candidate in states:
            seen.append((candidate.phase, candidate.tank_masses_kg['HP'],
                         policy._active_context.max_fsm_tank_ids))
        part = PartReport(name='in_port')
        if failure == 'physical':
            part.violations = [Violation(rule_id='port.stability.cargo_operations',
                severity='hard', call_index=0, message='departure endpoint draught')]
        if failure == 'unknown':
            part.ran, part.error = False, 'StabilityTimeout'
        return InPortBatchResult(True, (part,), len(states), 0 if failure else None)
    monkeypatch.setattr(policy, '_check_states', check)
    if failure == 'unknown':
        with pytest.raises(InPortUnknown, match='state 0.*unavailable'):
            policy._accept_heel_path(context, None)
    else:
        report = policy._accept_heel_path(context, None)
        assert (report is not None) == (failure == 'physical')
        if report is not None:
            assert policy.failure_cause == 'acceptance_failure:port.stability.cargo_operations'
    assert seen == [('departure', 50., frozenset({'HEEL', 'worked_DB'}))]
    assert not policy.failed_states  # Never feed a nonideal split back at the ideal.


def test_window_rechecks_arrival_when_chosen_matches_departure_ideal(monkeypatch):
    policy, context = _case(monkeypatch, departure_band=(70., 80.))
    seen = []
    def check(states, *, deadline=None):
        seen.extend((candidate.phase, candidate.tank_masses_kg['HP']) for candidate in states)
        return InPortBatchResult(True, tuple(PartReport(name='in_port') for _ in states), len(states), None)
    monkeypatch.setattr(policy, '_check_states', check)
    assert policy._accept_heel_path(context, None) is None
    assert seen == [('arrival', 70.)]


def test_both_window_endpoints_rechecked_with_early_failure(monkeypatch):
    policy, context = _case(monkeypatch, arrival_band=(40., 65.), departure_band=(60., 80.))
    seen = []
    def check(states, *, deadline=None):
        # Mirror the evaluator's first-failure prefix: do not execute the
        # departure endpoint after the arrival endpoint physically fails.
        seen.append((states[0].phase, states[0].tank_masses_kg['HP']))
        failure = PartReport(name='in_port', violations=[Violation(
            rule_id='port.stability.cargo_operations', severity='hard', call_index=0,
            message='first endpoint failed')])
        return InPortBatchResult(True, (failure,), len(states), 0)
    monkeypatch.setattr(policy, '_check_states', check)
    assert policy._accept_heel_path(context, None) is not None
    assert seen == [('arrival', 60.)]
    assert not policy.failed_states


def test_window_evaluation_retains_both_endpoint_proofs(monkeypatch):
    policy, context = _case(monkeypatch)
    calls = []
    def check(states, *, deadline=None):
        phase = states[0].phase
        calls.append((phase, policy._need_heel_intervals))
        report = PartReport(name='in_port', metrics={'heel_lo_kg': 20., 'heel_hi_kg': 80.,
            'heel_ideal_split_kg': 50. if phase == 'arrival' else 70.})
        return InPortBatchResult(True, (report,), len(states), None)
    monkeypatch.setattr(policy, '_check_states', check)
    assert policy._accept_window(context, None) is None
    assert calls == [('arrival', True), ('departure', True)]
    assert policy._window_arrival_reports[0].metrics['heel_ideal_split_kg'] == 50.
    assert policy._window_departure_reports[0].metrics['heel_ideal_split_kg'] == 70.
