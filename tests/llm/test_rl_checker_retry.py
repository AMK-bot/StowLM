"""Shared retry evidence and adjudication, independent of numerical physics."""
import os

import pytest

from llm.rl.checker_retry import check_with_retry
from stowage.model import CheckReport, MovePlan, PartReport, Violation
from stowage.model.fixtures import build_instance


def timed_out():
    return CheckReport(passed=False, parts={'stability': PartReport(
        name='stability', ran=False, error='time budget exceeded',
        metrics={'time_budget_exceeded': 1., 'time_budget_s': 8.})})


def hard_failure():
    result = timed_out()
    violation = Violation(rule_id='objective.all_loaded', severity='hard',
                          call_index=0, message='load list incomplete')
    result.parts['moves'] = PartReport(name='moves', violations=[violation])
    result.violations = [violation]
    return result


def test_supplied_initial_evidence_requires_only_one_physical_recheck(monkeypatch):
    instance = build_instance()
    plan = MovePlan(id='retry', instance_id=instance.id, call_index=instance.call_index)
    initial = timed_out()
    final = CheckReport(passed=True)
    calls = []
    monkeypatch.setenv('STOWAGE_STABILITY_TIME_BUDGET_S', '8')
    def checker(i, p):
        calls.append(float(os.environ['STOWAGE_STABILITY_TIME_BUDGET_S']))
        return final
    attempts = check_with_retry(instance, plan, checker=checker, initial_report=initial)
    assert calls == [16]
    assert attempts.initial_report is initial and attempts.retry_report is final
    assert attempts.latest_report is final and attempts.report is final
    assert attempts.retries == 1 and attempts.retry_budget_s == 16
    assert os.environ['STOWAGE_STABILITY_TIME_BUDGET_S'] == '8'


@pytest.mark.parametrize('order', ['initial', 'retry'])
def test_evaluated_failure_has_priority_on_either_check(order):
    instance = build_instance()
    plan = MovePlan(id='retry', instance_id=instance.id, call_index=instance.call_index)
    initial = hard_failure() if order == 'initial' else timed_out()
    final = timed_out() if order == 'initial' else hard_failure()
    attempts = check_with_retry(instance, plan, checker=lambda *a: final, initial_report=initial)
    assert attempts.definitive_failure and attempts.timed_out
    assert not attempts.report.passed
    assert any(v.rule_id == 'objective.all_loaded' for v in attempts.report.violations)


def test_failed_to_run_hard_part_is_not_definitive_infeasibility():
    instance = build_instance()
    plan = MovePlan(id='retry', instance_id=instance.id, call_index=instance.call_index)
    initial = hard_failure()
    initial.parts['moves'].ran = False
    initial.parts['moves'].error = 'replay could not be evaluated'
    final = timed_out()
    attempts = check_with_retry(instance, plan, checker=lambda *a: final, initial_report=initial)
    assert not attempts.definitive_failure and attempts.timed_out


def test_supplied_passing_report_does_not_recheck():
    instance = build_instance()
    plan = MovePlan(id='retry', instance_id=instance.id, call_index=instance.call_index)
    report = CheckReport(passed=True)
    attempts = check_with_retry(instance, plan, initial_report=report,
                               checker=lambda *a: pytest.fail('no timeout to retry'))
    assert attempts.report is report and attempts.retries == 0
