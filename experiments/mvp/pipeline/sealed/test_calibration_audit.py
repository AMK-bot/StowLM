"""Four development rule rechecks share one plan and get a fresh audit."""
import copy
import json
import os
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed import worker
from experiments.mvp.pipeline.sealed.common import object_hash
from experiments.mvp.pipeline.sealed.instructions import RULE_TYPES, select_rule
from experiments.mvp.pipeline.sealed.test_instructions import facts
from experiments.mvp.pipeline.sealed.test_worker import audit, gate, job


@pytest.fixture
def baseline(monkeypatch):
    import stowage.model
    import experiments.mvp.reader.pipeline
    raw = facts()
    raw['load_list'] = ['BOX1']
    instance = SimpleNamespace(id='synthetic', model_dump=lambda **kw: raw)
    rules = [select_rule(raw, index) for index in range(4)]
    request = dict(job(), calibration_rules=rules, expected_rule=rules[0])
    calls = []
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: instance)
    monkeypatch.setattr(worker, '_gate', lambda *a: gate())
    def plan(*args):
        calls.append('plan')
        return dict(status='checked_plan', plan={'synthetic': 'one baseline'})
    def check(*args):
        calls.append('audit')
        return dict(audit(), final_cells={'BOX1': ['0010182']})
    monkeypatch.setattr(worker, '_pipeline', plan)
    monkeypatch.setattr(worker, '_audit', check)
    monkeypatch.setattr(stowage.model.MovePlan, 'model_validate', lambda *a: None)
    monkeypatch.setattr(experiments.mvp.reader.pipeline, 'intrinsic_violations',
        lambda instance, answer, plan: ['cap binds'] if answer.type == 'max_tiers' else [])
    return request, instance, calls


def committed_row(request, tmp_path):
    row = worker.run_job(request, str(tmp_path/'planning'))
    assert row['status'] == 'checked_plan'
    row['planning_pid'] = os.getpid()+1
    target = tmp_path/'audit-target.json'
    target.write_text(json.dumps(row))
    return row, target


def test_all_four_rechecks_use_one_baseline_and_survive_fresh_audit(baseline, tmp_path):
    request, instance, calls = baseline
    row, target = committed_row(request, tmp_path)
    assert set(row['calibration_rechecks']) == set(RULE_TYPES)
    assert row['calibration_rechecks']['max_tiers']['counterfactual_instruction_recheck'] is False
    assert row['calibration_rules_sha256'] == object_hash(request['calibration_rules'])
    # The report's assigned instruction stays separate from the all-type survey.
    assert row['counterfactual_rule'] == request['expected_rule']
    assert row['counterfactual_instruction_recheck'] is True
    verified = worker.audit_job(dict(request, target=str(target)))
    assert verified['audit_verified'] is True
    assert verified['calibration_rechecks'] == row['calibration_rechecks']
    assert calls == ['plan', 'audit', 'audit']


@pytest.mark.parametrize('tamper', ['recheck', 'rule_hash', 'missing'])
def test_fresh_audit_rejects_tampered_nested_calibration(baseline, tmp_path, tamper):
    request, instance, calls = baseline
    row, target = committed_row(request, tmp_path)
    if tamper == 'recheck':
        row['calibration_rechecks']['max_tiers']['counterfactual_instruction_recheck'] = True
    elif tamper == 'rule_hash':
        row['calibration_rules_sha256'] = 'f'*64
    else:
        row.pop('calibration_rechecks')
    target.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='fresh checker.*audit differs'):
        worker.audit_job(dict(request, target=str(target)))
    assert calls.count('plan') == 1


@pytest.mark.parametrize('invalid', ['duplicate', 'missing', 'unknown', 'malformed', 'sealed', 'other_arm'])
def test_invalid_calibration_rules_fail_before_any_planning(baseline, tmp_path, invalid):
    request, instance, calls = baseline
    request = copy.deepcopy(request)
    if invalid == 'duplicate':
        request['calibration_rules'][1] = request['calibration_rules'][0]
    elif invalid == 'missing':
        request['calibration_rules'].pop()
    elif invalid == 'unknown':
        request['calibration_rules'][0]['constraint']['type'] = 'unregistered'
    elif invalid == 'malformed':
        request['calibration_rules'][0]['constraint']['bay'] = 999
    elif invalid == 'sealed':
        request['mode'] = 'sealed'
    else:
        request['arm'] = 'twostep'
    row = worker.run_job(request, str(tmp_path/'invalid'))
    assert row['status'] == 'error' and row['passed'] is False
    assert calls == []
