"""Incomplete or diagnostic checker evidence never receives a passing UI badge."""

import pytest

from app.backend import build_replay
from app.tests.report_helpers import bind_report
from stowage.model import CheckReport, PartReport, PlannerResult, Provenance
from stowage.moves import apply_plan

REQUIRED = ('moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints')


def passing_report(instance, plan):
    return bind_report(CheckReport(passed=True, count=apply_plan(instance, plan).count,
        parts={name: PartReport(name=name) for name in REQUIRED}), instance, plan)


@pytest.mark.parametrize('defect', ['stub', 'missing', 'not_ran', 'error', 'count', 'result_flag'])
def test_replay_never_labels_incomplete_or_tainted_report_passed(replay_case, monkeypatch, defect):
    instance, plan = replay_case
    report = passing_report(instance, plan)
    candidate = PlannerResult(status='ok', plan=plan, planner='test', planner_version='1')
    if defect == 'stub':
        report.provenance = report.provenance.merge(Provenance(stubs={'checker.test': 'diagnostic'}))
    elif defect == 'missing':
        del report.parts['stability']
    elif defect == 'not_ran':
        report.parts['stability'].ran = False
    elif defect == 'error':
        report.parts['stability'].error = 'No geometry'
    elif defect == 'count':
        report.count = None
    else:
        candidate.metrics['validated'] = False
    monkeypatch.setattr('app.checker.full.check', lambda *_: report)
    replay = build_replay(instance, plan, planner_result=candidate)
    assert replay['metadata']['validation_status'] == 'NOT VALIDATED'
    assert replay['metadata']['validation_reasons']
    assert replay['checker']['passed'] is False
    assert replay['checker']['report']['passed'] is True  # Retain authoritative raw evidence.
    assert replay['metadata']['departure_approved'] is False
    assert replay['metadata']['shippable'] is False


@pytest.mark.parametrize('field,value', [('validated', False), ('not_validated', True),
    ('validation_status', 'NOT VALIDATED'), ('validation_status', 'not_validated'),
    ('validated', 0.0), ('not_validated', 1.0)])
def test_explicit_unvalidated_marker_is_a_fail_closed_display_gate(replay_case, field, value):
    from app.validation import report_validation
    instance, plan = replay_case
    raw = passing_report(instance, plan).model_dump(mode='json')
    raw[field] = value
    result = report_validation(raw, instance=instance, plan=plan)
    assert result['validated'] is False
    assert result['status'] == 'NOT VALIDATED'
    assert result['reasons']


def test_nested_input_stub_is_unvalidated_even_with_clean_top_level(replay_case):
    from app.validation import report_validation
    instance, plan = replay_case
    raw_instance = instance.model_dump(mode='json')
    raw_instance['synthetic_nested_artifact'] = {'provenance': {'stubs': {'linear': 'test only'}}}
    result = report_validation(passing_report(instance, plan), instance=raw_instance, plan=plan)
    assert result['status'] == 'NOT VALIDATED'
    assert any('stub' in reason.lower() for reason in result['reasons'])


def test_complete_rejected_report_is_a_validated_failure(replay_case):
    from app.validation import report_validation
    instance, plan = replay_case
    report = passing_report(instance, plan)
    report.passed = False
    assert report_validation(report, instance=instance, plan=plan) == {
        'validated': True, 'status': 'FAIL', 'reasons': []}


def test_job_summary_cannot_call_an_incomplete_pipeline_report_a_pass(replay_case, monkeypatch, tmp_path):
    from app.flow import DemoService
    from app.tests.test_flow import wait_done
    instance, plan = replay_case
    report = passing_report(instance, plan)
    report.parts['stability'].ran = False
    candidate = PlannerResult(status='ok', plan=plan, planner='solver.scalable', planner_version='test')
    monkeypatch.setattr('app.flow.run_planner', lambda *a, **kw: (candidate, report))
    monkeypatch.setattr('app.checker.full.check', lambda *_: report)
    service = DemoService(instance, plan, discover=False)
    completed = wait_done(service, service.start_solve(instance.id)['id'])
    assert completed['status'] == 'done'
    summary = completed['results'][0]
    assert summary['passed'] is False
    assert summary['shippable'] is False
    assert summary['validation_status'] == 'NOT VALIDATED'
