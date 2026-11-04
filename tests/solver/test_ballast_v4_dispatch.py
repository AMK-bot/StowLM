"""The MVP finisher is explicitly enabled and leaves both defaults alone."""
from types import SimpleNamespace
import sys
import pytest

from stowage.model import BallastCondition, BallastOutcome, MovePlan
from stowage.model.fixtures import build_instance
from stowage.solver import ballast


def _problem():
    instance = build_instance()
    plan = MovePlan(id='v4-dispatch', instance_id=instance.id,
                    call_index=instance.call_index,
                    ballast=BallastCondition(tank_masses_kg={}, source='planner'))
    return instance, plan


def test_default_does_not_dispatch_v4(monkeypatch):
    monkeypatch.delenv('STOWAGE_BALLAST_V4', raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('Default must not dispatch')
    monkeypatch.setitem(sys.modules, 'stowage.solver.ballast_v4_dispatch',
                        SimpleNamespace(finish=forbidden))
    instance, plan = _problem()
    result = ballast.finish_ballast(instance, plan)
    assert result.status == 'kept'
    assert result.plan is plan


def test_opt_in_forwards_public_call(monkeypatch):
    monkeypatch.setenv('STOWAGE_BALLAST_V4', '1')
    import stowage.rules.catalogue as catalogue
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: False)
    calls = []
    expected = BallastOutcome(status='not_found')
    def fast(*args, **kwargs):
        calls.append((args, kwargs))
        return expected
    monkeypatch.setitem(sys.modules, 'stowage.solver.ballast_v4_dispatch',
                        SimpleNamespace(finish=fast))
    instance, plan = _problem()
    assert ballast.finish_ballast(instance, plan, time_limit_s=9., seed=4) is expected
    assert calls == [((instance, plan), {'time_limit_s': 9., 'seed': 4})]


def test_rule_freeze_path_keeps_legacy_dispatch(monkeypatch):
    monkeypatch.setenv('STOWAGE_BALLAST_V4', '1')
    import stowage.rules.catalogue as catalogue
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: True)
    def forbidden(*args, **kwargs):
        raise AssertionError('Activated path must not dispatch')
    monkeypatch.setitem(sys.modules, 'stowage.solver.ballast_v4_dispatch',
                        SimpleNamespace(finish=forbidden))
    instance, plan = _problem()
    assert ballast.finish_ballast(instance, plan).status == 'kept'


@pytest.mark.parametrize('budget', [0., -1., float('inf'), float('nan')])
def test_invalid_budget_never_starts_worker(monkeypatch, budget):
    from stowage.solver import ballast_v4_dispatch as dispatch
    monkeypatch.setattr(ballast, '_validated_replay', lambda *args: None)
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid budget must not start a worker')
    monkeypatch.setattr(dispatch, 'run_with_deadline', forbidden)
    result = dispatch.finish(*_problem(), time_limit_s=budget)
    assert result.status == 'not_found'
    assert result.plan is None
    assert result.not_found_cause == 'timeout:no_budget'
    assert result.diagnostics['candidate_count'] is None


def test_watchdog_timeout_never_claims_worker_progress(monkeypatch):
    from stowage.solver import ballast_v4_dispatch as dispatch
    monkeypatch.setattr(ballast, '_validated_replay', lambda *args: None)
    def timeout(*args, **kwargs):
        raise dispatch.BallastDeadline()
    monkeypatch.setattr(dispatch, 'run_with_deadline', timeout)
    result = dispatch.finish(*_problem(), time_limit_s=1.)
    assert result.status == 'not_found'
    assert result.plan is None
    assert result.not_found_cause == 'timeout:worker'
    assert result.diagnostics['candidate_count'] is None
    assert result.diagnostics['full_checker_passed'] is None
