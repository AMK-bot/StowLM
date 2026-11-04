"""D74 finisher regressions: unavailable physics is never negative feedback."""
from types import SimpleNamespace

import pytest

from stowage.model import PartReport, Violation
from stowage.solver.ballast_inport import InPortFinisher
from stowage.checker.stability_batch import InPortBatchResult


def _policy(monkeypatch, count=2):
    import stowage.solver.ballast_inport as module
    ship = SimpleNamespace(tank_masses_kg={})
    policy = object.__new__(InPortFinisher)
    policy.instance = SimpleNamespace(onboard=ship)
    policy.states = [ship]*count
    policy.k_arr, policy.workers, policy.t_hat_s = 0, 4, .1
    policy.t_hat_state_count = 2
    policy.failed_states = set()
    policy.failure = policy.failure_cause = None
    policy._active_context = None
    context = SimpleNamespace(for_phase=lambda phase: context,
        transferred_ship=lambda ship, phase: (ship, {}))
    monkeypatch.setattr(policy, '_context_for', lambda masses: context)
    monkeypatch.setattr(policy, '_risk', lambda ship: (0.,))
    monkeypatch.setattr(policy, '_accept_rate', lambda masses: None)
    monkeypatch.setattr(policy, '_accept_window', lambda context, deadline: None)
    monkeypatch.setattr(policy, '_accept_heel_path', lambda context, deadline: None)
    return policy, context


@pytest.mark.parametrize('unknown', [PartReport(name='stability', ran=False, error='StabilityTimeout'),
    PartReport(name='stability', violations=[Violation(rule_id='checker.internal_error',
        severity='hard', call_index=0, message='unavailable')])])
def test_acceptance_unknown_does_not_record_failed_state(monkeypatch, unknown):
    policy, _ = _policy(monkeypatch)
    monkeypatch.setattr(policy, '_check_states', lambda states, deadline=None:
        InPortBatchResult(True, (unknown,), len(states), 0))
    report = policy.accept({})
    assert not report.ran
    assert not policy.failed_states
    assert 'unavailable' in report.error
    assert not report.violations


def test_feedback_unknown_raises_without_synthetic_margin(monkeypatch):
    import stowage.solver.ballast_inport as module
    import stowage.checker.in_port_evaluator as evaluator
    policy, _ = _policy(monkeypatch)
    policy.failed_states = {0}
    monkeypatch.setattr(evaluator, 'check_state', lambda *args, **kw:
        PartReport(name='stability', ran=False, error='StabilityTimeout'))
    unknown = getattr(module, 'InPortUnknown', RuntimeError)
    with pytest.raises(unknown, match='state 0.*unavailable'):
        policy.constraint_margins({})


def test_parallel_setup_checks_deadline_between_transfers(monkeypatch):
    import stowage.solver.ballast_inport as module
    policy, context = _policy(monkeypatch, count=50)
    clock, seen = [0.], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    def transfer(ship, phase):
        clock[0] += 1.
        seen.append(clock[0])
        return ship, {}
    context.transferred_ship = transfer
    monkeypatch.setattr(policy, '_check_states', lambda *args, **kw:
        pytest.fail('budget already expired during setup'))
    report = policy.accept({})
    assert not report.ran
    assert len(seen) <= 10
    assert report.metrics['elapsed_s'] <= 10.


def test_acceptance_budget_uses_pass2_time_and_window_count(monkeypatch):
    policy, _ = _policy(monkeypatch, count=100)
    policy.t_hat_accept_s = .2
    policy._pump_window_lifts = 10
    assert policy.acceptance_budget_s == pytest.approx(11.)
    policy.t_hat_s = 100.
    assert policy.acceptance_budget_s == pytest.approx(11.)


def test_acceptance_calibrates_only_first_eight_pass2_states(monkeypatch):
    from stowage.checker.in_port_evaluator import TimedInPortBatchResult
    policy, _ = _policy(monkeypatch, count=100)
    policy.t_hat_s, policy.t_hat_accept_s = 9., 0.
    policy.t_hat_accept_state_count = 0
    calls = []
    def states(ships, *, deadline=None):
        calls.append(len(ships))
        return TimedInPortBatchResult(True, tuple(PartReport(name='stability') for _ in ships),
            len(ships), None, tuple(.2 if len(calls) == 1 else 99. for _ in ships))
    monkeypatch.setattr(policy, '_check_states', states)
    report = policy.accept({})
    assert report.ran
    assert policy.t_hat_accept_s == pytest.approx(.2)
    assert policy.t_hat_accept_state_count == 8
    assert calls == [8, 92]


def test_r1_pumped_mass_is_internal_transfer_lower_bound(monkeypatch):
    policy, _ = _policy(monkeypatch)
    policy.heel = ('HP', 'HS')
    policy.tanks = {tid: SimpleNamespace(id=tid, adjustable=True) for tid in ('HP', 'HS', 'A', 'B')}
    policy.instance.onboard.tank_masses_kg = {'A': 300000., 'B': 100000.}
    assert policy.pumped_mass({'A': 100000., 'B': 300000.}) == 200000.


def _rate_policy(monkeypatch):
    policy, _ = _policy(monkeypatch, count=6)
    policy.heel = ('HP', 'HS')
    policy.tanks = {tid: SimpleNamespace(id=tid, adjustable=True) for tid in ('HP', 'HS', 'A', 'B')}
    policy.instance.onboard.tank_masses_kg = {'A': 550000., 'B': 550000.}
    policy.instance.call_index = 0
    policy.instance.vessel = SimpleNamespace(tanks=list(policy.tanks.values()),
        tank_pairs=[SimpleNamespace(role='anti_heeling', port_tank='HP', starboard_tank='HS')],
        ballast_system=SimpleNamespace(ballast_pump_m3_h=300., heel_transfer_m3_h=150., max_cranes=2))
    policy.instance.port = SimpleNamespace(water_density_kg_m3=1025., quay_crane=SimpleNamespace(
        count=2, gross_moves_per_hour=30., pre_operation_allowance_h=.5,
        post_operation_allowance_h=.5))
    return policy


def test_r1_rate_margin_and_shared_metrics(monkeypatch):
    policy = _rate_policy(monkeypatch)
    assert policy.rate_lp_data()['capacity_kg'] == pytest.approx(153750.)
    assert policy.rate_margin({'A': 0., 'B': 0.}) < 0
    programme = policy.programme_metrics({'A': 0., 'B': 0.})
    assert programme['pumped_mass_t'] == pytest.approx(1100.)
    assert programme['abs_change_t'] == pytest.approx(1100.)
    assert programme['pump_time_required_h'] == pytest.approx(1100000./307500.)
    assert programme['pump_time_available_h'] == .5
    assert programme['rate_check_rule'] == 'port.ballast.transfer_rate'


def test_r1_reports_delay_without_rejecting_changeover(monkeypatch):
    policy = _rate_policy(monkeypatch)
    assert InPortFinisher._accept_rate(policy, {'A': 0., 'B': 0.}) is None
    assert policy.ballast_programme['ballast_delay_h'] == pytest.approx(1100000./307500.-.5)
    assert policy._changeover_result.window_indices == (0,)  # Delayed k=0 still checks both endpoints.
    assert policy.failure_cause is None


@pytest.mark.parametrize('sentinel', [False, True])
def test_large_window_calibrates_departure_before_provisional_floor(monkeypatch, sentinel):
    """A passing 25.2s window fits its 40.4s budget, not the 10s floor."""
    import stowage.solver.ballast_inport as module
    from stowage.checker.in_port_evaluator import TimedInPortBatchResult
    policy, context = _policy(monkeypatch, count=101)
    policy.k_arr, policy.workers = (101 if sentinel else 100), 1
    policy.t_hat_accept_s, policy.t_hat_accept_state_count = 0., 0
    policy._changeover_result = SimpleNamespace(window_indices=tuple(range(100)))
    policy._pump_window_lifts = 100
    policy.states = [SimpleNamespace(index=i) for i in range(101)]
    context.transferred_ship = lambda ship, phase: (
        SimpleNamespace(index=ship.index, phase=phase), {})
    monkeypatch.setattr(policy, '_accept_window', lambda ctx, deadline:
        InPortFinisher._accept_window(policy, ctx, deadline))
    clock, checked = [0.], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    def states(ships, *, deadline=None):
        times = []
        for ship in ships:
            checked.append((ship.index, ship.phase))
            dt = .05 if ship.phase == 'arrival' else .2
            times.append(dt)
            clock[0] += dt
            module._checkpoint(deadline)
        return TimedInPortBatchResult(True,
            tuple(PartReport(name='stability') for _ in ships), len(ships), None, tuple(times))
    monkeypatch.setattr(policy, '_check_states', states)
    policy.calibrate_acceptance({})
    assert len(checked) == (8 if sentinel else 1)
    assert all(phase == 'departure' for _, phase in checked)
    assert clock[0] < 10.
    report = policy.accept({})
    assert report.ran, report.error
    assert policy.t_hat_accept_s == pytest.approx(.2)
    assert policy.t_hat_accept_state_count == (8 if sentinel else 1)
    assert report.metrics['acceptance_budget_s'] == pytest.approx(40. if sentinel else 40.4)
    assert checked[0][1] == 'departure'
    assert len(checked) == len(set(checked)) == (200 if sentinel else 201)
    assert report.metrics['elapsed_s'] > 10.


def test_window_arrival_endpoint_uses_candidate_worked_tank_fsm(monkeypatch):
    policy, context = _policy(monkeypatch, count=3)
    policy._changeover_result = SimpleNamespace(window_indices=(1,))
    context.max_fsm_tank_ids = frozenset({'HEEL', 'changed_DB'})
    calls = []
    def states(ships, *, deadline=None):
        calls.append((policy._active_context.max_fsm_tank_ids, policy._need_heel_intervals))
        return InPortBatchResult(True, tuple(PartReport(name='stability') for _ in ships),
            len(ships), None)
    monkeypatch.setattr(policy, '_check_states', states)
    assert InPortFinisher._accept_window(policy, context, None) is None
    assert calls == [(frozenset({'HEEL', 'changed_DB'}), True),
        (frozenset({'HEEL', 'changed_DB'}), True)]


def test_r2_rechecks_nonideal_splits_and_never_uses_ideal_feedback(monkeypatch):
    from stowage.checker.in_port_programme import ProgrammeParameters
    import stowage.checker.in_port_heel as heel
    policy, context = _policy(monkeypatch, count=3)
    policy.instance.call_index = 0
    policy.instance.onboard.tank_masses_kg = {'HP': 0.}
    policy.heel = ('HP', 'HS')
    policy.arrival_reports, policy.arrival_ships = [], []
    policy.ballast_programme = {}
    policy._window_arrival_reports = {}
    policy._changeover_result = SimpleNamespace(window_indices=())
    context.max_fsm_tank_ids = frozenset({'HEEL'})
    policy.states = [SimpleNamespace(tank_masses_kg={'HP': 0. if i == 0 else 10.})
                     for i in range(3)]
    policy._suffix_reports = {i: PartReport(name='stability', metrics={
        'heel_lo_kg': float(i), 'heel_hi_kg': 10., 'heel_ideal_split_kg': 0. if i == 0 else 10.})
        for i in range(3)}
    parameters = ProgrammeParameters(ballast_pump_m3_h=3600., heel_transfer_m3_h=3600.,
        max_cranes=1, port_cranes=1, gross_moves_per_hour=3600.,
        pre_operation_allowance_h=1./3600., post_operation_allowance_h=0., water_density_kg_m3=1.)
    monkeypatch.setattr(policy, '_rate_parameters', lambda: parameters)
    splits = []
    monkeypatch.setattr(heel, 'ship_at_split', lambda context, ship, split:
        splits.append(split) or ship)
    failure = PartReport(name='stability', margins={'gm_m': -.1}, violations=[Violation(
        rule_id='port.stability.cargo_operations', severity='hard', call_index=0, message='P1')])
    monkeypatch.setattr(policy, '_check_states', lambda states, deadline=None:
        InPortBatchResult(True, (failure,), len(states), 0))
    report = InPortFinisher._accept_heel_path(policy, context, None)
    assert splits
    assert report.violations
    assert policy.failure_cause == 'acceptance_failure:port.stability.cargo_operations'
    assert not policy.failed_states


def test_pass1_setup_deadline_is_typed_compute_undecided(monkeypatch):
    import stowage.solver.ballast_inport as module
    import stowage.checker.stability_states as states
    policy, _ = _policy(monkeypatch)
    policy.prepared = False
    policy.plan = SimpleNamespace(moves=[object()]*50)
    clock, calls = [0.], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    class Builder:
        def __init__(self, instance):
            self.state = SimpleNamespace(ship=instance.onboard)
        def push(self, move, *, move_index):
            clock[0] += 1.
            calls.append(move_index)
            return self.state
    monkeypatch.setattr(states, 'InPortStateBuilder', Builder)
    from stowage.solver.ballast_inport import InPortUnknown
    with pytest.raises(InPortUnknown) as error:
        policy.prepare()
    assert error.value.cause == 'timeout:prepass'
    assert len(calls) == 30
    assert not policy.failed_states
