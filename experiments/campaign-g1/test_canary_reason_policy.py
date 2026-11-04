"""Canary targets, declared refusal fallout and mask reasons stay distinct."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
TARGET = 'port.e3.quay_crossing'
SECONDARY = 'objective.all_loaded'


def module():
    spec = importlib.util.spec_from_file_location('tested_canary_reason_policy', HERE/'migration_canaries.py')
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


def checker(ids=(), *, passed=False):
    from stowage.model import CheckReport, PartReport, Violation
    return CheckReport(passed=passed,
        violations=[Violation(rule_id=rule, severity='hard', call_index=0,
                              message='synthetic canary policy observation') for rule in ids],
        parts={name:PartReport(name=name, ran=True) for name in
               ('moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints', 'voyage')})


@pytest.mark.parametrize('ids,eligible', [
    ([TARGET], True), ([TARGET, SECONDARY], True), ([SECONDARY], False),
    ([TARGET, 'expert.physical.stack_weight'], False),
    ([TARGET, SECONDARY, 'iacs.member_irs.container_securing'], False),
])
def test_refusal_requires_its_primary_and_only_declared_optional_secondary(ids, eligible):
    observed = module()._checker_observation(checker(ids), TARGET, 'deny',
        expected_rules=[TARGET], expected_secondary_rules=[SECONDARY])
    assert observed['eligible'] is eligible
    assert observed['expected_hard_rule_ids'] == [TARGET]
    assert observed['expected_secondary_rule_ids'] == [SECONDARY]


def test_optional_refusal_secondary_does_not_relax_clean_control():
    m = module()
    assert m._checker_observation(checker(passed=True), TARGET, 'accept',
        expected_rules=[TARGET], expected_secondary_rules=[SECONDARY])['eligible']
    assert not m._checker_observation(checker([SECONDARY]), TARGET, 'accept',
        expected_rules=[TARGET], expected_secondary_rules=[SECONDARY])['eligible']


@pytest.mark.parametrize('reasons,eligible', [
    ([TARGET], True), ([], False), ([SECONDARY], False),
    (['expert.physical.size_fit'], False), ([TARGET, SECONDARY], False),
])
def test_mask_uses_its_declared_reasons_never_checker_secondaries(reasons, eligible):
    observed = dict(status='rejected', production_c2_active=True, rule_ids=reasons)
    assert module()._mask_matches(observed, TARGET, 'deny',
        expected_rules=[TARGET, SECONDARY], expected_mask_reasons=[TARGET]) is eligible


def test_declared_mask_reason_does_not_relax_production_c2_requirement():
    observed = dict(status='rejected', production_c2_active=False, rule_ids=[TARGET])
    assert not module()._mask_matches(observed, TARGET, 'deny', expected_mask_reasons=[TARGET])


def test_variant_records_and_enforces_both_independent_expectations(tmp_path, monkeypatch):
    import stowage.checker
    from stowage.model import MovePlan
    from stowage.model.fixtures import build_instance
    m = module()
    instance = build_instance()
    plan = MovePlan(id='policy-unit', instance_id=instance.id, call_index=0, moves=[])
    report = checker([TARGET, SECONDARY])
    report.provenance.versions = m.component_versions()
    monkeypatch.setattr(stowage.checker, 'check', lambda *args:report)
    monkeypatch.setattr(stowage.checker, 'report_matches_inputs', lambda *args:True)
    monkeypatch.setattr(m, '_mask_observation', lambda *args:dict(
        status='rejected', production_c2_active=True, rule_ids=[TARGET]))
    result = m._variant(tmp_path/'case', dict(input=instance.model_dump(mode='json'),
        plan=plan.model_dump(mode='json'), expected_rules=[TARGET],
        expected_secondary_rules=[SECONDARY], expected_mask_reasons=[TARGET]), TARGET, 'deny')
    assert result['eligible']
    assert result['expected_rules'] == [TARGET]
    assert result['expected_secondary_rules'] == [SECONDARY]
    assert result['expected_mask_reasons'] == [TARGET]
    assert result['checker_observation']['expected_secondary_rule_ids'] == [SECONDARY]


@pytest.mark.parametrize('reasons,matches', [([TARGET],True), ([],False),
    ([SECONDARY],False), (['expert.physical.size_fit'],False)])
def test_diagnostic_variant_records_reasons_without_qualifying_evidence(tmp_path,monkeypatch,reasons,matches):
    import stowage.checker
    from stowage.model import MovePlan
    from stowage.model.fixtures import build_instance
    spec=importlib.util.spec_from_file_location('tested_preflip_reason_policy',HERE/'preflip_dry_run.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    instance=build_instance();plan=MovePlan(id='unit-diagnostic',instance_id=instance.id,call_index=0,moves=[])
    report=checker([TARGET,SECONDARY])
    monkeypatch.setattr(stowage.checker,'check',lambda *a:report)
    monkeypatch.setattr(stowage.checker,'report_matches_inputs',lambda *a:True)
    monkeypatch.setattr(m,'mask_observation',lambda *a:dict(status='rejected',rule_ids=reasons,production_c2_active=False))
    result=m.variant(tmp_path/'diagnostic',dict(input=instance.model_dump(mode='json'),plan=plan.model_dump(mode='json')),
        TARGET,[TARGET],'deny',expected_secondary_rules=[SECONDARY],expected_mask_reasons=[TARGET])
    assert result['local_behavior_matches'] is matches
    assert result['expected_secondary_rules']==[SECONDARY]
    assert result['expected_mask_reasons']==[TARGET]
    assert result['gate_expectation_passed'] is False and result['evidence_eligible'] is False
    assert result['mask']['production_c2_active'] is False
