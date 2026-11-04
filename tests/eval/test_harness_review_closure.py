"""Review regressions for warm rechecking, repair attribution and partial rows."""
import os
from types import SimpleNamespace

import pytest

from eval import harness
from stowage.model import MovePlan, PlannerResult
from tests.eval.test_harness import Diagnostic
from tests.llm.test_rl_reward import complete_report, timeout_report


def setup_attempt(monkeypatch, instance, *, repaired=False):
    plan = MovePlan(id='eval-review', instance_id=instance.id, call_index=instance.call_index)
    result = PlannerResult(status='ok', plan=plan, planner='review', planner_version='1',
                           repaired=repaired, metrics={'repair_actions_kept': 2, 'repair_actions_added': 8})
    monkeypatch.setattr(harness, '_load_selected', lambda *a, **kw: instance)
    monkeypatch.setattr(harness, 'lower_bound', lambda *a: SimpleNamespace(model_dump=lambda **kw: {}))
    row = dict(spec_key='spec', split='test', rung='tiny', input_sha256='input', spec={})
    return result, row


def test_evaluation_timeout_retries_once_and_binds_latest_report(empty_ship_instance, monkeypatch):
    import stowage.checker
    result, row = setup_attempt(monkeypatch, empty_ship_instance)
    initial = timeout_report(budget=3)
    monkeypatch.setattr(harness, 'run_planner', lambda *a, **kw: (result, initial))
    calls = []
    def checker(instance, plan):
        calls.append(float(os.environ['STOWAGE_STABILITY_TIME_BUDGET_S']))
        return complete_report(instance, plan)
    monkeypatch.setattr(stowage.checker, 'check', checker)
    record = harness._evaluate(Diagnostic(), row, 0, None, True, None)
    assert calls == [6]
    assert record['report']['passed'] is True
    assert record['checker_outcome'] == 'passed'
    assert record['checker_attempts']['retries'] == 1
    assert record['checker_attempts']['initial_report']['passed'] is False
    assert record['checker_attempts']['retry_report']['passed'] is True


def test_evaluation_residual_timeout_cannot_reuse_shipping_evidence(empty_ship_instance, monkeypatch):
    import stowage.checker
    result, row = setup_attempt(monkeypatch, empty_ship_instance)
    result._record_full_check(empty_ship_instance, complete_report(empty_ship_instance, result.plan))
    assert result.shippable
    monkeypatch.setattr(harness, 'run_planner', lambda *a, **kw: (result, timeout_report()))
    monkeypatch.setattr(stowage.checker, 'check', lambda *a: timeout_report(budget=60))
    record = harness._evaluate(Diagnostic(), row, 0, None, True, None)
    assert result.shippable is False
    assert record['shippable'] is False
    assert record['checker_outcome'] == 'checker_timeout'


def test_repaired_plan_discloses_final_kept_prefix_share(empty_ship_instance, monkeypatch):
    result, row = setup_attempt(monkeypatch, empty_ship_instance, repaired=True)
    monkeypatch.setattr(harness, 'run_planner', lambda *a, **kw: (
        result, complete_report(empty_ship_instance, result.plan)))
    record = harness._evaluate(Diagnostic(), row, 0, None, True, None)
    assert record['repair_prefix'] == {'kept': 2, 'total': 10, 'share': .2}


def test_official_limited_run_requires_explicit_partial_marker(tmp_path):
    with pytest.raises(ValueError, match='partial'):
        harness.run_evaluation(Diagnostic(), run_dir=tmp_path, limits={'tiny': 1})


def test_empty_instance_infeasibility_bucket_explains_required_certificate():
    from eval.harness_metrics import aggregate_results, render_row
    summary = aggregate_results([dict(instance_id='unknown', spec_key='unknown', report=None,
                                      feasibility_status='unknown')])
    assert 'no accepted physical infeasibility certificate' in summary['feasibility_certificate_note']
    assert 'proven-infeasible 0' in render_row(summary)
    assert 'certificate' in render_row(summary)
