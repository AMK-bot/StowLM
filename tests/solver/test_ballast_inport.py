"""T6: D64 acceptance is additional to D57 and is gated by rule freeze v1."""
from __future__ import annotations

import pytest

from stowage.model import MovePlan, PartReport, TankPair, Violation
from stowage.model.fixtures import build_instance
from stowage.model.voyage import VoyageSpec, VoyageTankPolicy
from stowage.rules import catalogue
from stowage.solver import ballast


def _instance():
    from stowage.model.vessel import BallastSystem
    instance = build_instance()
    instance.vessel.ballast_system = BallastSystem(ballast_pump_m3_h=300.,
        heel_transfer_m3_h=150., max_cranes=2,
        assumption='APPROXIMATION: synthetic D74 orchestration fixture')
    instance.port.quay_crane.gross_moves_per_hour = 30.
    instance.port.quay_crane.pre_operation_allowance_h = .5
    instance.port.quay_crane.post_operation_allowance_h = .5
    ordinary = [t.model_copy(update={'id': t.id.replace('WB-', 'DB-')}, deep=True)
                for t in instance.vessel.tanks if t.adjustable]
    instance.vessel.tanks.extend(ordinary)
    instance.vessel.tank_pairs = [TankPair(id='HEEL', port_tank='WB-P', starboard_tank='WB-S')]
    instance.onboard.tank_masses_kg.update({'DB-P': 51250., 'DB-S': 51250.})
    instance.vessel.voyage_policy = VoyageTankPolicy(
        groups=[dict(id='fuel', kind='fuel_oil', tank_ids=['FO-1'])], drawdown_order=['FO-1'])
    instance.voyage = VoyageSpec.model_validate(dict(
        departure=dict(id='dep', phase='departure', call_index=0, locode='THLCH',
                       environment_id='dep', density_id='dep', water_density_kg_m3=1025.),
        arrival=dict(id='arr', phase='arrival', call_index=1, locode='THBKK',
                     environment_id='arr', density_id='arr', water_density_kg_m3=1025.)))
    return instance


def _plan(instance):
    return MovePlan(id='t6-finisher', instance_id=instance.id, call_index=0)


def _isolate(monkeypatch, *, in_port_fails=False):
    import stowage.linear.bands as bands
    import stowage.solver.ballast_proof as proof
    import stowage.solver.ballast_physics as physics
    import stowage.solver.ballast_pressed as pressed

    seen = []
    monkeypatch.setattr(proof, 'prove_infeasible', lambda *a, **kw: None)
    monkeypatch.setattr(physics, 'ExactContext', lambda *a, **kw: None)
    monkeypatch.setattr(pressed, 'search', lambda *a, **kw: [])
    monkeypatch.setattr(ballast, 'search', lambda *a, **kw: None)

    def skip_lp(*a, **kw):
        raise ValueError('synthetic test skips LP construction')
    monkeypatch.setattr(bands, 'band_for_instance', skip_lp)
    for name in ('stability', 'strength', 'visibility', 'lashing'):
        module = __import__('stowage.checker.'+name, fromlist=['check_'+name])

        def check(instance, ship, *, call_index, in_port=False, _name=name, **kw):
            seen.append((_name, in_port, call_index, ship.tank_masses_kg.copy()))
            fail = in_port and in_port_fails
            return PartReport(name=_name, margins={'test_margin': -1. if fail else 1.},
                violations=[Violation(rule_id='port.stability.cargo_operations', severity='hard',
                    call_index=call_index, message='Synthetic in-port state fails')]
                    if fail else [])
        monkeypatch.setattr(module, 'check_'+name, check)
    from stowage.checker import in_port_evaluator, stability
    from stowage.solver import ballast_inport
    # These orchestration cases replace scalar physics; R2's real path and
    # exact re-evaluation have their own D74 integration tests.
    monkeypatch.setattr(ballast_inport.InPortFinisher, '_accept_heel_path',
        lambda self, context, deadline: None)
    # Numerical orchestration is exercised below; the real bounded-pool
    # adapter has dedicated deadline tests and requires a real worker executor.
    monkeypatch.setattr(ballast_inport, 'evaluate_in_port_with_deadline',
        lambda evaluator, states, *, deadline, close=True, stop_at_first_failure=True: evaluator.evaluate(
            states, stop_at_first_failure=stop_at_first_failure))
    monkeypatch.setattr(in_port_evaluator, 'check_state',
        lambda instance, ship, context, **kw: stability.check_stability(
            instance, ship, call_index=instance.call_index, in_port=True))
    params = {'tanks': [dict(id=t.id, geometry=dict(x_min_m=10., x_max_m=20.,
        y_min_m=t.table[0].tcg_m-1., y_max_m=t.table[0].tcg_m+1.,
        z_min_m=0., z_max_m=5.)) for t in _instance().vessel.tanks]}
    monkeypatch.setattr(stability, '_context', lambda _: (None, params))

    class Evaluator:
        def __init__(self, instance, context, *, workers=1, warm_start=False, **kw):
            self.instance, self.context = instance, context
            self.workers, self.warm_start = workers, warm_start
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            pass
        def close(self):
            self._closed = True
        def evaluate(self, states, *, stop_at_first_failure=False):
            reports = []
            failure = None
            for index, ship in enumerate(states):
                report = stability.check_stability(self.instance, ship,
                    call_index=self.instance.call_index, in_port=True)
                reports.append(report)
                if not report.ran or report.error or any(v.severity == 'hard' for v in report.violations):
                    if failure is None:
                        failure = index
                    if stop_at_first_failure:
                        break
            return in_port_evaluator.TimedInPortBatchResult(True, tuple(reports),
                len(states), failure, tuple(.1 for _ in reports))
    monkeypatch.setattr(in_port_evaluator, 'InPortEvaluator', Evaluator)
    return seen


def test_t6_seagoing_pass_cannot_authorize_failing_in_port_state(monkeypatch):
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    seen = _isolate(monkeypatch, in_port_fails=True)
    with catalogue.rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'not_found'
    assert result.plan is None
    assert any(in_port for _, in_port, _, _ in seen)
    assert any('state 0' in str(report.error) or any('state 0' in v.message
               for v in report.violations) for report in result.parts.values())


def test_t6_accepts_when_both_seagoing_and_in_port_pass(monkeypatch):
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    seen = _isolate(monkeypatch)
    with catalogue.rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'ok'
    assert any(in_port for _, in_port, _, _ in seen)
    assert sum(result.plan.ballast.tank_masses_kg[t] for t in ('WB-P', 'WB-S')) == 102500.
    assert result.plan.ballast.tank_masses_kg['DB-P'] == result.plan.ballast.tank_masses_kg['DB-S']


def test_switch_off_preserves_legacy_ballast_and_all_seagoing_reports(monkeypatch):
    instance = _instance()
    seen = _isolate(monkeypatch, in_port_fails=True)
    with catalogue.rule_freeze_v1(enabled=False):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'ok'
    assert not any(in_port for _, in_port, _, _ in seen)
    assert result.plan.ballast.model_dump() == {
        'tank_masses_kg': {'DB-P': 51250., 'DB-S': 51250., 'WB-P': 51250., 'WB-S': 51250.},
        'source': 'finisher'}
    assert set(result.parts) == {'stability', 'strength', 'visibility', 'lashing',
                                 'arrival/stability', 'arrival/strength'}
    assert len(seen) == 12
    for name, report in result.parts.items():
        if name in ('stability', 'strength', 'visibility', 'lashing'):
            assert report.margins == {'test_margin': 1., 'mid_voyage/test_margin': 1.,
                **({'arrival/test_margin': 1.} if name in ('visibility', 'lashing') else {})}
        else:
            assert report.margins == {'test_margin': 1.}
        assert report.ran and report.error is None and not report.violations


def _in_port(monkeypatch, instance, plan=None):
    from stowage.solver.ballast_inport import InPortFinisher
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: True)
    helper = InPortFinisher(instance, _plan(instance) if plan is None else plan)
    helper.prepare()
    return helper


def test_k_arr_is_computed_once_in_chronological_order(monkeypatch):
    from stowage.model.moves import Load
    import stowage.checker.stability as stability
    instance = _instance()
    _isolate(monkeypatch)
    plan = _plan(instance).model_copy(update={'moves': [
        Load(call_index=0, container_id=instance.containers[3].id,
             src='A-004-01-01', dst='0010182'),
        Load(call_index=0, container_id=instance.containers[1].id,
             src='B-001-01-01', dst='0030282')]})
    seen = []

    def check(inst, ship, **kw):
        state_index = len(ship.placements)
        seen.append(state_index)
        return PartReport(name='stability', margins={'synthetic': -1. if state_index else 1.},
            violations=[Violation(rule_id='port.stability.cargo_operations', severity='hard',
                call_index=0, message='first arrival-ballast failure')]
                if state_index else [])
    monkeypatch.setattr(stability, 'check_stability', check)
    helper = _in_port(monkeypatch, instance, plan)
    assert helper.k_arr == 1
    assert seen == [0, 1]
    helper.prepare()
    assert seen == [0, 1]
    assert len(helper.states) == 3


def test_projection_uses_fixed_heel_total_and_closed_form_cargo_correction(monkeypatch):
    from stowage.model import Placement
    instance = _instance()
    instance.onboard.placements = [Placement(container_id=instance.containers[2].id,
                                             slot='0010282')]
    _isolate(monkeypatch)
    helper = _in_port(monkeypatch, instance)
    projected = helper.project({'WB-P': 0., 'WB-S': 0., 'DB-P': 10000., 'DB-S': 90000.})
    assert projected['WB-P']+projected['WB-S'] == pytest.approx(102500.)
    # 12 t at +1.3 m is balanced by the fixed-total pair at ±5 m.
    assert projected['WB-P'] == pytest.approx(49690.)
    assert projected['WB-S'] == pytest.approx(52810.)
    assert projected['DB-P'] == projected['DB-S']
    assert projected['DB-P'] == pytest.approx(50000.)


def test_rev31_acceptance_uses_landed_fsm_policy_without_staging_block(monkeypatch):
    instance = _instance()
    _isolate(monkeypatch)
    helper = _in_port(monkeypatch, instance)
    report = helper.accept(instance.onboard.tank_masses_kg.copy())
    assert report.ran and report.error is None and not report.violations


def test_acceptance_checks_riskiest_states_first_and_names_original_state(monkeypatch):
    from stowage.checker.stability_batch import InPortBatchResult
    from stowage.model import Placement
    instance = _instance()
    _isolate(monkeypatch)
    helper = _in_port(monkeypatch, instance)
    box = Placement(container_id=instance.containers[2].id, slot='0010282')
    helper.states = [instance.onboard.model_copy(update={'placements': [box]*size})
                     for size in (0, 3, 1, 2)]
    helper.k_arr = 0
    monkeypatch.setattr(helper, '_risk', lambda ship: (len(ship.placements),))
    seen = []

    def check_states(states, *, deadline=None):
        seen.extend(len(ship.placements) for ship in states)
        return InPortBatchResult(True, (PartReport(name='stability'),
            PartReport(name='stability', margins={'gm_m': -.2}, violations=[Violation(
                rule_id='port.stability.cargo_operations', severity='hard', call_index=0,
                message='Synthetic failed state')])), len(states), 1)
    monkeypatch.setattr(helper, '_check_states', check_states)
    report = helper.accept(instance.onboard.tank_masses_kg.copy())
    assert seen == [3, 2, 1, 0]
    assert report.metrics['failure_state_index'] == 3
    assert 'state 3' in report.error


@pytest.mark.parametrize('state_count,workers,budget', [(1, 1, 10.), (201, 1, 40.2), (801, 4, 40.05)])
def test_acceptance_uses_own_exact_budget_without_widening(monkeypatch, state_count, workers, budget):
    from stowage.checker.stability_batch import InPortBatchResult
    import stowage.solver.ballast_inport as inport
    instance = _instance()
    _isolate(monkeypatch)
    helper = _in_port(monkeypatch, instance)
    helper.states = [instance.onboard.model_copy(deep=True) for _ in range(state_count)]
    helper.k_arr, helper.workers, helper.t_hat_accept_s = 0, workers, .1
    # Pass 1 may have a different cost; only the measured pass-2 cost budgets acceptance.
    helper.t_hat_s = 9.
    clock = [100.]
    monkeypatch.setattr(inport.time, 'monotonic', lambda: clock[0])
    seen = []

    def check_states(states, *, deadline=None):
        seen.append(deadline)
        return InPortBatchResult(True, tuple(PartReport(name='stability') for _ in states),
                                 len(states), None)
    monkeypatch.setattr(helper, '_check_states', check_states)
    report = helper.accept(instance.onboard.tank_masses_kg.copy())
    assert report.ran and not report.violations
    assert seen == [pytest.approx(100.+budget)]


def test_in_port_failure_feeds_named_state_to_at_most_two_researches(monkeypatch):
    from stowage.model import BallastCondition, BallastOutcome
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch, in_port_fails=True)
    attempts = []

    def search(inst, plan, *, _in_port=None, **kw):
        assert _in_port.prepared
        assert _in_port.k_arr == 0
        attempts.append(set(_in_port.failed_states))
        candidate = plan.model_copy(update={'ballast': BallastCondition(
            tank_masses_kg={t.id: 51250. for t in inst.vessel.tanks if t.adjustable},
            source='finisher')})
        return BallastOutcome(status='ok', plan=candidate)
    monkeypatch.setattr(ballast, '_search_ballast', search)
    with catalogue.rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'not_found' and result.plan is None
    assert attempts == [set(), {0}, {0}]
    assert any('state 0' in str(report.error) for report in result.parts.values())


def test_acceptance_budget_timeout_never_returns_passing_report(monkeypatch):
    from stowage.solver.ballast_deadline import ExactDeadline
    instance = _instance()
    _isolate(monkeypatch)
    helper = _in_port(monkeypatch, instance)
    helper.k_arr = 0

    def timeout(states, *, deadline=None):
        raise ExactDeadline
    monkeypatch.setattr(helper, '_check_states', timeout)
    report = helper.accept(instance.onboard.tank_masses_kg.copy())
    assert not report.ran
    assert 'budget' in report.error
    assert 'state 0' in report.error


def test_presearch_and_acceptance_use_configured_parallel_evaluator(monkeypatch):
    from stowage.checker import in_port_evaluator
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: True)
    workers_seen = []

    class Evaluator:
        enabled = True

        def __init__(self, instance, context, *, workers, warm_start=False, **kw):
            workers_seen.append(workers)
            self.workers, self.warm_start = workers, warm_start

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass
        def close(self):
            self._closed = True

        def evaluate(self, states, *, stop_at_first_failure):
            assert stop_at_first_failure
            return in_port_evaluator.TimedInPortBatchResult(True,
                tuple(PartReport(name='stability') for _ in states), len(states), None,
                tuple(.1 for _ in states))
    monkeypatch.setattr(in_port_evaluator, 'InPortEvaluator', Evaluator)
    helper = InPortFinisher(instance, _plan(instance), workers=4)
    helper.prepare()
    helper.k_arr = 0
    helper.accept(instance.onboard.tank_masses_kg.copy())
    assert workers_seen == [4, 4]


def test_failing_state_margin_guides_search_with_heel_excluded(monkeypatch):
    import numpy as np
    import stowage.checker.stability as stability
    instance = _instance()
    _isolate(monkeypatch)

    def check(inst, ship, *, in_port=False, **kw):
        margin = min(ship.tank_masses_kg[t]/102500. for t in ('DB-P', 'DB-S'))-.7
        return PartReport(name='stability', margins={'gm_m': margin if in_port else 1.},
            violations=[Violation(rule_id='port.stability.cargo_operations', severity='hard',
                call_index=0, message='Synthetic worked-ballast margin')]
                if in_port and margin < 0 else [])
    monkeypatch.setattr(stability, 'check_stability', check)
    helper = _in_port(monkeypatch, instance)
    helper.failed_states.add(0)
    searched = []

    def search(fills, evaluate, *args, **kw):
        assert all(len(fill) == 2 for fill in fills)  # Only DB-P/S are search variables.
        key = 'in_port/state_0/gm_m'
        searched.append(evaluate(np.array([.6, .6]))[key])
        selective = evaluate(np.array([.65, .65]), active_keys=(key,))
        assert selective[key] == pytest.approx(-.05)
        assert 'in_port/rate' not in selective  # Pump delay supplies no hard gradient.
        evaluate(np.array([.8, .8]))
    monkeypatch.setattr(ballast, 'search', search)
    result = ballast._search_ballast(instance, _plan(instance), time_limit_s=1., _in_port=helper)
    assert searched == [pytest.approx(-.1)]
    assert result.status == 'ok'
    masses = result.plan.ballast.tank_masses_kg
    assert masses['DB-P'] == masses['DB-S'] == pytest.approx(82000.)
    assert masses['WB-P']+masses['WB-S'] == pytest.approx(102500.)


def test_public_active_path_owns_search_and_serial_acceptance_watchdogs(monkeypatch):
    import time
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    phases = []

    def watchdog(function, args=(), kwargs=None, *, deadline):
        phases.append((function.__name__, deadline-time.monotonic()))
        return function(*args, **(kwargs or {}))
    monkeypatch.setattr(ballast, 'run_with_deadline', watchdog)
    with catalogue.rule_freeze_v1(enabled=True):
        result = ballast.finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'ok'
    assert [name for name, _ in phases] == ['_search_in_port', '_calibrate_in_port', '_accept_in_port']
    assert .9 < phases[0][1] <= 1.
    assert 9.9 < phases[1][1] <= 10.
    assert 9.9 < phases[2][1] <= phases[1][1]


def test_owned_phase_wrapper_reinstates_activation_and_restores_it(monkeypatch):
    seen = []

    class Policy:
        def prepare(self):
            seen.append(catalogue.rule_freeze_v1_enabled())
    with catalogue.rule_freeze_v1(enabled=False):
        ballast._prepare_in_port(Policy())
        assert not catalogue.rule_freeze_v1_enabled()
    assert seen == [True]


def test_d74_keeps_pass1_and_pass2_timing_separate(monkeypatch):
    from stowage.checker import in_port_evaluator
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    calls = []
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: True)

    class Evaluator:
        def __init__(self, instance, context, *, workers=1, warm_start=False, **kw):
            self.context = context
            self.workers, self.warm_start = workers, warm_start
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            pass
        def close(self):
            self._closed = True
        def evaluate(self, states, *, stop_at_first_failure=False):
            calls.append((len(states), stop_at_first_failure,
                          self.context.max_fsm_tank_ids))
            # Distinct departure cost controls acceptance without changing pass 1.
            departure = 'DB-P' in self.context.max_fsm_tank_ids
            reports = tuple(PartReport(name='stability') for _ in states)
            return in_port_evaluator.TimedInPortBatchResult(True, reports,
                len(states), None, tuple(9. if departure else .125 for _ in states))
    monkeypatch.setattr(in_port_evaluator, 'InPortEvaluator', Evaluator)
    helper = InPortFinisher(instance, _plan(instance))
    helper.prepare()
    assert helper.k_arr == len(helper.states) == 1
    assert helper.t_hat_s == .125
    assert helper.t_hat_state_count == 1
    # Exercise a departure suffix with changed ordinary ballast. Keep the
    # already-certified arrival calibration and inspect the phase's real FSM set.
    helper.k_arr = 0
    candidate = dict(instance.onboard.tank_masses_kg, **{'DB-P': 60000., 'DB-S': 60000.})
    report = helper.accept(candidate)
    assert report.ran and report.error is None
    assert helper.k_arr == 0 and helper.t_hat_s == .125 and helper.t_hat_state_count == 1
    assert helper.t_hat_accept_s == 9.
    assert calls[0][2] == frozenset({'WB-P', 'WB-S', 'FO-1'})
    assert calls[-1][2] == calls[0][2] | {'DB-P', 'DB-S'}
    assert 'DB-P' not in calls[0][2]


def test_rev31_pass1_calibration_is_candidate_independent(monkeypatch):
    from stowage.checker import in_port_evaluator
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    phases = []
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: True)

    class Evaluator:
        def __init__(self, instance, context, *, workers=1, warm_start=False, **kw):
            self.context = context
            self.workers, self.warm_start = workers, warm_start
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            pass
        def close(self):
            self._closed = True
        def evaluate(self, states, *, stop_at_first_failure=False):
            phases.append(self.context.max_fsm_tank_ids)
            report = PartReport(name='stability', margins={'gm_m': -.1},
                violations=[Violation(rule_id='port.stability.cargo_operations',
                    severity='hard', call_index=0, message='arrival physical failure')])
            return in_port_evaluator.TimedInPortBatchResult(True, (report,),
                len(states), 0, (.2,))
    monkeypatch.setattr(in_port_evaluator, 'InPortEvaluator', Evaluator)
    helper = InPortFinisher(instance, _plan(instance))
    helper.prepare()
    for mass in (60000., 70000.):
        helper.accept(dict(instance.onboard.tank_masses_kg,
                           **{'DB-P': mass, 'DB-S': mass}))
        helper.prepare()
        assert helper.k_arr == 0 and helper.t_hat_s == .2
    assert phases == [frozenset({'WB-P', 'WB-S', 'FO-1'}),
                      frozenset({'WB-P', 'WB-S', 'FO-1', 'DB-P', 'DB-S'}),
                      frozenset({'WB-P', 'WB-S', 'FO-1', 'DB-P', 'DB-S'})]


@pytest.mark.parametrize('first_failure', [None, 3])
def test_d74_pass1_uses_chronological_chunks_and_stops_at_first_failure(monkeypatch, first_failure):
    from types import SimpleNamespace
    from stowage.checker import in_port_evaluator, stability_states
    from stowage.model.moves import Load
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: True)
    move = Load(call_index=0, container_id=instance.containers[3].id,
                src='A-004-01-01', dst='0010182')
    plan = _plan(instance).model_copy(update={'moves': [move]*9})

    class Builder:
        def __init__(self, instance):
            self.state = SimpleNamespace(ship=instance.onboard)
        def push(self, move, *, move_index):
            return self.state
    monkeypatch.setattr(stability_states, 'InPortStateBuilder', Builder)
    calls = []

    class Evaluator:
        def __init__(self, instance, context, *, workers=1, warm_start=False, **kw):
            self.workers, self.warm_start = workers, warm_start
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            pass
        def close(self):
            self._closed = True
        def evaluate(self, states, *, stop_at_first_failure=False):
            calls.append((len(states), stop_at_first_failure))
            offset = sum(size for size, _ in calls[:-1])
            reports = tuple(PartReport(name='stability',
                violations=[Violation(rule_id='port.stability.cargo_operations',
                    severity='hard', call_index=0, message='synthetic failed sample')]
                    if offset+index == first_failure else [])
                for index, _ in enumerate(states))
            local_failure = (first_failure-offset if first_failure is not None and
                offset <= first_failure < offset+len(states) else None)
            if local_failure is not None and stop_at_first_failure:
                reports = reports[:local_failure+1]
            times = tuple((offset+i+1)/100 for i in range(len(reports)))
            return in_port_evaluator.TimedInPortBatchResult(True, reports,
                len(states), local_failure, times)
    monkeypatch.setattr(in_port_evaluator, 'InPortEvaluator', Evaluator)
    helper = InPortFinisher(instance, plan)
    helper.prepare()
    assert calls == ([(4, True), (4, True), (2, True)] if first_failure is None else [(4, True)])
    assert helper.k_arr == (10 if first_failure is None else first_failure)
    assert helper.t_hat_state_count == (8 if first_failure is None else 4)
    assert helper.t_hat_s == pytest.approx(.045 if first_failure is None else .025)
    assert helper.acceptance_budget_s == 10.


@pytest.mark.slow
def test_t13_real_delayed_changeover_retains_hard_window_checks(monkeypatch, record_property):
    """A reported R1 delay never exempts the unsafe arrival window endpoint."""
    import runpy
    import time
    from pathlib import Path
    from stowage.solver.ballast_inport import InPortFinisher
    from stowage.model import BallastOutcome
    from stowage.checker.full import check
    from stowage.vessel.fleet_manual import manual_port
    helper = runpy.run_path(str(Path(__file__).parents[1]/'checker'/'test_in_port_acceptance.py'))
    started = time.monotonic()
    with catalogue.rule_freeze_v1(enabled=True):
        instance, saved = helper['saved_pilot']('000')
        # The saved diagnostic predates D74's crane fields; use its same
        # public production port with the current declared timing parameters.
        instance.port = manual_port(instance.port.locode)
        heel = {tid for pair in instance.vessel.tank_pairs
                for tid in (pair.port_tank, pair.starboard_tank)}
        # A deliberately heavy symmetric arrival condition triggers k=0.
        # Its saved departure solution passed rev3.1, but moving that much
        # water before lift zero reports delay; the worked-FSM endpoint still
        # needs an independent physical certificate.
        for tank in instance.vessel.tanks:
            if tank.adjustable and tank.id not in heel:
                instance.onboard.tank_masses_kg[tank.id] = (
                    .99*tank.capacity_m3*tank.content_density_kg_m3)
        plan = saved.model_copy(update={'ballast': None})
        monkeypatch.setattr(ballast, '_search_ballast',
            lambda *a, **kw: BallastOutcome(status='ok', plan=saved))
        result = ballast._finish_ballast(instance, plan, time_limit_s=30.)
        assert result.status == 'not_found', result.model_dump()
        assert result.not_found_cause == 'acceptance_failure:port.stability.cargo_operations'
        assert result.ballast_programme['ballast_delay_h'] > 0.
        assert result.plan is None
        report = result.parts['in_port']
        assert report.ran
        assert any(v.rule_id == 'port.stability.cargo_operations' for v in report.violations)
        assert report.metrics['k_arr'] < len(plan.moves)+1
        oracle = InPortFinisher(instance, plan)
        oracle.prepare()
        checked = oracle.accept(saved.ballast.tank_masses_kg)
        assert checked.ran and any(v.rule_id == 'port.stability.cargo_operations'
                                  for v in checked.violations), checked.model_dump()
        full = check(instance, saved)
        assert not full.passed, full.model_dump()
        # The full checker attributes endpoint failures to the hard D74
        # programme rule; distinguish this physical window from old R1 timing.
        assert any(v.rule_id == 'port.ballast.transfer_rate' and 'phase=window.' in v.message
                   for part in full.parts.values() for v in part.violations), full.model_dump()
        assert sum(saved.ballast.tank_masses_kg[tid] for tid in heel) == pytest.approx(
            sum(instance.onboard.tank_masses_kg[tid] for tid in heel))
    record_property('elapsed_s', time.monotonic()-started)


def test_symmetry_projection_respects_both_capacities_at_full_endpoint(monkeypatch):
    import math
    instance = _instance()
    port = next(tank for tank in instance.vessel.tanks if tank.id == 'DB-P')
    starboard = next(tank for tank in instance.vessel.tanks if tank.id == 'DB-S')
    # Reflected polygon integration can differ by a handful of floating-point
    # units; averaging nominal full masses must not exceed the smaller tank.
    starboard.capacity_m3 = math.nextafter(math.nextafter(port.capacity_m3, math.inf), math.inf)
    _isolate(monkeypatch)
    helper = _in_port(monkeypatch, instance)
    candidate = helper.project({'DB-P': port.capacity_m3*port.content_density_kg_m3,
                               'DB-S': starboard.capacity_m3*starboard.content_density_kg_m3})
    assert candidate['DB-P'] == candidate['DB-S']
    assert candidate['DB-P'] <= port.capacity_m3*port.content_density_kg_m3
    assert candidate['DB-S'] <= starboard.capacity_m3*starboard.content_density_kg_m3


def test_parallel_acceptance_stays_outside_daemon_search_worker(monkeypatch):
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)
    monkeypatch.setenv('STOWAGE_IN_PORT_WORKERS', '4')
    calls = []

    def watchdog(function, args=(), kwargs=None, *, deadline):
        calls.append(function.__name__)
        return function(*args, **(kwargs or {}))

    def accept(policy, masses):
        assert policy.workers == 4
        return PartReport(name='in_port')
    monkeypatch.setattr(ballast, 'run_with_deadline', watchdog)
    monkeypatch.setattr(InPortFinisher, 'accept', accept)
    with catalogue.rule_freeze_v1(enabled=True):
        result = ballast.finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'ok'
    assert calls == ['_search_in_port']


def test_crashed_acceptance_pool_returns_state_named_not_found(monkeypatch):
    from concurrent.futures.process import BrokenProcessPool
    from stowage.solver.ballast_inport import InPortFinisher
    instance = _instance()
    _isolate(monkeypatch)

    def crash(policy, masses):
        raise BrokenProcessPool('synthetic owned worker crashed')
    monkeypatch.setattr(InPortFinisher, 'accept', crash)
    with catalogue.rule_freeze_v1(enabled=True):
        result = ballast._finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert result.status == 'not_found' and result.plan is None
    assert 'state 1' in result.parts['in_port'].error
    assert 'acceptance unavailable' in result.parts['in_port'].error
