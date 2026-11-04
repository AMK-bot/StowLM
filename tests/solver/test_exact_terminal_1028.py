"""Exact terminal effort allocation and condition-qualified rejection evidence.

Tiny checker/finisher doubles isolate the scheduling and acceptance contract;
these tests do not claim physical feasibility for the fixture vessel.
"""
from __future__ import annotations

import pytest

from stowage.model import BallastCondition, BallastOutcome, CheckReport, MovePlan, PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.solver.exact import _record_terminal_report, _validate_terminal


def failure(rule='iscode.b2_3.containership_second_check'):
    return Violation(rule_id=rule, severity='hard', call_index=0, message='Synthetic terminal rejection')


def rejected_report():
    breach = failure()
    return CheckReport(passed=False, violations=[breach, breach], parts={
        'stability': PartReport(name='stability', violations=[breach, breach]),
        **{f'condition:test:{phase}:stability': PartReport(name='stability', violations=[breach])
           for phase in ('departure', 'mid_voyage', 'arrival')}})


def terminal(monkeypatch, *, checker_reports, finisher, remaining=80., progress=None):
    import stowage.checker as checker
    import stowage.solver.ballast as ballast
    instance = build_instance()
    plan = MovePlan(id='exact-terminal-1028', instance_id=instance.id, call_index=instance.call_index, moves=[])
    reports = iter(checker_reports)
    monkeypatch.setattr(checker, 'check', lambda *a, **kw: next(reports))
    monkeypatch.setattr(ballast, 'finish_ballast', finisher)
    metrics = {'ballast_attempts': 0, 'ballast_unknown': 0, 'exact_rejections': 0}
    verdict, masses, provenance = _validate_terminal(instance, plan, instance.onboard,
        physics=True, static=False, remaining=lambda: remaining, seed=7, metrics=metrics,
        progress=progress)
    return verdict, masses, metrics


def test_full_check_evidence_counts_distinct_rules_and_preserves_every_voyage_state():
    metrics = {}
    _record_terminal_report(rejected_report(), metrics)
    assert metrics['terminal_checker_calls'] == 1
    assert metrics['terminal_failure_rule_counts'] == {'iscode.b2_3.containership_second_check': 1}
    expected = {f'condition:test:{phase}:stability|iscode.b2_3.containership_second_check': 1
                for phase in ('departure', 'mid_voyage', 'arrival')}
    assert metrics['terminal_failure_state_rule_counts'] == expected
    assert len(metrics['terminal_last_failure']) == 3


def test_finisher_has_time_for_multiple_exact_checks_and_retains_validation_budget(monkeypatch):
    allocated = []
    def finish(instance, plan, *, time_limit_s, seed):
        allocated.append(time_limit_s)
        assert seed == 7
        assert 2. < time_limit_s < 80.
        return BallastOutcome(status='ok', plan=plan.model_copy(update={
            'ballast': BallastCondition(source='finisher', tank_masses_kg={})}))
    verdict, masses, metrics = terminal(monkeypatch,
        checker_reports=[rejected_report(), CheckReport(passed=True)], finisher=finish)
    assert verdict == 'yes' and masses.source == 'planner'
    assert metrics['terminal_checker_calls'] == 2
    assert metrics['ballast_status_counts'] == {'ok': 1}
    assert metrics['ballast_last_budget_s'] == allocated[0]


@pytest.mark.parametrize('remaining', [.25, 1., 4.])
def test_short_budget_keeps_time_for_mandatory_validation(monkeypatch, remaining):
    def finish(instance, plan, *, time_limit_s, **kw):
        assert 0. < time_limit_s < remaining
        return BallastOutcome(status='not_found')
    verdict, masses, metrics = terminal(monkeypatch, checker_reports=[rejected_report()],
        finisher=finish, remaining=remaining)
    assert verdict == 'unknown' and masses is None
    assert metrics['ballast_unknown'] == 1
    assert metrics['ballast_status_counts'] == {'not_found': 1}


def test_finisher_success_never_overrides_independent_full_checker_failure(monkeypatch):
    def finish(instance, plan, **kw):
        return BallastOutcome(status='ok', plan=plan.model_copy(update={
            'ballast': BallastCondition(source='finisher', tank_masses_kg={})}))
    verdict, masses, metrics = terminal(monkeypatch,
        checker_reports=[rejected_report(), rejected_report()], finisher=finish)
    assert verdict == 'unknown' and masses is None
    assert metrics['exact_rejections'] == 1 and metrics['ballast_unknown'] == 1
    assert metrics['terminal_failure_rule_counts']['iscode.b2_3.containership_second_check'] == 2


def test_completed_failure_evidence_is_published_before_finisher_can_stall(monkeypatch):
    checkpoints = []
    def finish(instance, plan, **kw):
        assert checkpoints and checkpoints[-1]['terminal_checker_calls'] == 1
        assert checkpoints[-1]['terminal_failure_state_rule_counts']
        return BallastOutcome(status='not_found')
    # The real progress callback captures a metric snapshot in the watchdog.
    # Here, intercept its caller's metrics from the closure without timing sleeps.
    import stowage.solver.exact as exact
    original = exact._record_terminal_report
    observed = {}
    def record(report, metrics):
        original(report, metrics)
        observed.update(metrics)
    monkeypatch.setattr(exact, '_record_terminal_report', record)
    terminal(monkeypatch, checker_reports=[rejected_report()], finisher=finish,
        progress=lambda phase, **kw: checkpoints.append(dict(observed)))


@pytest.mark.parametrize('ran', [True, False])
def test_static_rejection_and_missing_part_evidence_survive_before_full_checker(monkeypatch, ran):
    import stowage.checker as checker
    import stowage.checker.rules as rules
    instance = build_instance()
    plan = MovePlan(id='static-terminal-1028', instance_id=instance.id, call_index=instance.call_index, moves=[])
    part = PartReport(name='rules', ran=ran, error=None if ran else 'synthetic unavailable catalog',
        violations=[failure('expert.physical.stack_weight')] if ran else [])
    monkeypatch.setattr(rules, 'check_static_rules', lambda *args, **kwargs: part)
    def forbidden(*args, **kwargs):
        raise AssertionError('Static refusal must precede numerical full checking')
    monkeypatch.setattr(checker, 'check', forbidden)
    metrics, checkpoints = {}, []
    verdict, ballast, provenance = _validate_terminal(instance, plan, instance.onboard,
        physics=True, static=True, remaining=lambda: 10., seed=0, metrics=metrics,
        progress=lambda *a, **kw: checkpoints.append(dict(metrics)))
    assert verdict == ('no' if ran else 'unknown') and ballast is None
    assert checkpoints[-1]['terminal_static_checks'] == 1
    if ran:
        assert metrics['terminal_static_failure_rule_counts'] == {'expert.physical.stack_weight': 1}
    else:
        assert metrics['terminal_part_error_counts'] == {'rules': 1}
        assert metrics['terminal_last_part_errors'] == [{'part': 'rules', 'error': 'synthetic unavailable catalog'}]


def test_full_checker_unavailable_condition_part_retains_error_identity():
    metrics = {}
    _record_terminal_report(CheckReport(passed=False, parts={
        'condition:test:arrival:lashing': PartReport(name='lashing', ran=False, error='synthetic missing gear')}), metrics)
    assert metrics['terminal_part_error_counts'] == {'condition:test:arrival:lashing': 1}
    assert metrics['terminal_last_part_errors'] == [
        {'part': 'condition:test:arrival:lashing', 'error': 'synthetic missing gear'}]
