"""Claim regressions only: no planner, full checker, or model calls."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch

import pytest

from experiments.mvp.reader.binding_claims import HERE, sentence_target_agreement


@pytest.fixture
def labelled():
    case = json.loads((HERE/'binding-cases.json').read_text())['cases'][0]
    return dict(case, parsed=deepcopy(case['expected']), parse_exact=True)


def test_agreement_is_recomputed_not_cached(labelled):
    labelled['parse_exact'] = False
    result = sentence_target_agreement(labelled)
    assert result['parse_agreement'] and result['sentence_target_verified']
    # Agreement alone does not certify any plan.
    assert result['sentence_compliance_verified'] is False


def test_wrong_valid_bay_cannot_borrow_cached_exact_credit(labelled):
    labelled['parsed']['constraint']['bay'] = 3
    result = sentence_target_agreement(labelled)
    assert result['sentence_target_verified'] and not result['parse_agreement']
    assert 'Actual parser response differs' in result['sentence_target_failure']


def test_copied_target_cannot_replace_frozen_intended_target(labelled):
    labelled['parsed']['constraint']['bay'] = 3
    labelled['expected'] = deepcopy(labelled['parsed'])
    result = sentence_target_agreement(labelled)
    assert not result['parse_agreement']
    assert 'Copied expected answer differs' in result['sentence_target_failure']


def test_unlabelled_sentence_has_no_independent_binding_credit(labelled):
    labelled['case_id'] = 'unlabelled-live-sentence'
    labelled.pop('expected')
    result = sentence_target_agreement(labelled)
    assert not result['parse_agreement'] and not result['sentence_target_verified']


def test_changed_cohort_is_not_trusted(labelled, tmp_path):
    raw = json.dumps({'cases': [labelled]}).encode()
    cases, prereg = tmp_path/'cases.json', tmp_path/'preregister.json'
    cases.write_bytes(raw)
    prereg.write_text(json.dumps({'cases_sha256': hashlib.sha256(raw+b'changed').hexdigest()}))
    result = sentence_target_agreement(labelled, cases_path=cases, prereg_path=prereg)
    assert not result['parse_agreement']
    assert 'cohort changed' in result['sentence_target_failure']


def test_wrong_parse_audit_stops_before_checker_and_binding(labelled, tmp_path):
    from experiments.mvp.reader.binding_audit import audit
    labelled['parsed']['constraint']['bay'] = 3
    path = tmp_path/'wrong-plan.json'
    path.write_text(json.dumps(labelled))
    with patch('experiments.mvp.reader.binding_audit.load_instance') as loader, \
         patch('experiments.mvp.reader.binding_audit.check') as checker:
        result = audit(path)
    assert result['passed'] is False and result['binding'] is False
    assert result['parse_agreement'] is False
    assert result['sentence_compliance_verified'] is False
    loader.assert_not_called()
    checker.assert_not_called()


def test_wrong_parse_plan_checkpoint_is_failed_without_compiling(labelled, tmp_path, monkeypatch):
    from experiments.mvp.reader import run_binding
    labelled['parsed']['constraint']['bay'] = 3
    monkeypatch.setattr(run_binding, 'RUN', tmp_path)
    with patch('experiments.mvp.reader.pipeline.compile_constraint') as compiler, \
         patch('experiments.mvp.reader.frontier_driver.plan_and_check') as planner:
        run_binding.plan_one(labelled)
    row = json.loads((tmp_path/'plans'/f"{labelled['case_id']}.json").read_text())
    assert row['passed'] is False and row['binding'] is False
    assert row['parse_agreement'] is False and row['parse_exact'] is True
    assert 'Actual parser response differs' in row['error']
    compiler.assert_not_called()
    planner.assert_not_called()
