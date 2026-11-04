"""Cross-module checks on synthetic inputs, without search or sealed reads."""
from __future__ import annotations

import json
import pytest

from experiments.mvp.pipeline.sealed import driver, report, worker
from experiments.mvp.pipeline.sealed.common import HERE, HarnessError, sha256
from experiments.mvp.pipeline.sealed.instructions import RULE_TYPES


def test_preregistration_matches_instruction_and_execution_interfaces():
    protocol = json.loads((HERE / 'preregistration.json').read_text())
    assert list(protocol['arms']) == ['pipeline', 'twostep', 'instruction', 'twostep_300']
    assert protocol['instruction_selection']['types_in_order'] == list(RULE_TYPES)
    execution = protocol['execution']
    assert execution['jobs'] == dict(pipeline_no_rule=69, twostep=69,
                                     pipeline_instruction=24, twostep_300=24, total=186)
    config = dict(mode='dry-run')
    driver.validate_config(config, protocol)
    from experiments.mvp.pipeline.sealed.budget import DEFAULT_WORKERS
    assert config['workers'] == DEFAULT_WORKERS
    assert execution['workers'] == execution['sealed_workers'] == 4
    assert config['twostep_s'] + config['twostep_finalize_s'] == execution['twostep_budget_s']
    assert worker.TWOSTEP_LABEL == report.TWOSTEP_LABEL


def test_native_worker_level_and_fresh_audit_are_reportable(tmp_path):
    rows = []
    for arm in ('pipeline', 'twostep', 'instruction'):
        rows.append(dict(map_id='synthetic', cohort='envelope24', arm=arm,
            status='checked_plan', level=0, passed=True, full_checker_pass=True,
            audit_verified=True, audit_fresh_process=True,
            instruction_recheck=True if arm == 'instruction' else None,
            instruction_type='max_tiers' if arm == 'instruction' else None,
            instruction_target_count=0, no_matching_cargo=True,
            lifts_current_port=2, lifts_projected_later_port=5,
            lifts_total=7, weighted_lifts=7, wall_s=.01,
            fixture_surrogate=True))
    result = report.write_report(rows, tmp_path, metadata=dict(
        mode='dry-run', stubbed=['synthetic plans'],
        expected_maps=[dict(map_id='synthetic', cohort='envelope24')]))
    assert result['complete']
    assert result['cohorts']['envelope24']['arms']['pipeline']['passes_L0'] == 1
    assert result['cohorts']['envelope24']['arms']['instruction']['instruction_target_presence']['no_matching_cargo'] == 1
    assert result['cohorts']['envelope24']['comparisons']['instruction_vs_pipeline']['weighted_lifts']['ties'] == 1
    assert not result['sealed_claim_eligible']


def test_runtime_inventory_excludes_mutable_pipeline_verification_receipts():
    paths = set(driver.source_inventory())
    binding = HERE.parent / 'binding'
    assert binding / 'adapter.py' in paths
    assert binding / 'verification-admission.json' not in paths
    assert HERE.parent.parent / 'frontier' / 'engine' / 'native-source-manifest.json' in paths


from experiments.mvp.pipeline.sealed.test_protocol_d275 import example as pipeline_freeze


def test_task21_manifest_rejects_dirty_pipeline_bytes(pipeline_freeze,monkeypatch):
    import importlib
    ex=pipeline_freeze
    protocol=importlib.import_module('experiments.mvp.pipeline.sealed.protocol')
    monkeypatch.setattr(protocol,'verify_pipeline_manifest',lambda _:ex.verify())
    original=ex.protocol._dependencies()[0]
    links=ex.protocol._dependencies()[1]
    class Drift(RuntimeError):
        def __init__(self,drifts): self.drifts=drifts
    def guard(path,**options):
        assert options['full_weights'] is True
        drifts=[dict(path=name,reason='changed') for name,wanted in ex.data['files'].items()
                if sha256(ex.root/name)!=wanted]
        if drifts: raise Drift(drifts)
        return original(path,**options)
    monkeypatch.setattr(ex.protocol,'_dependencies',lambda:(guard,links))
    driver.verify_pipeline_manifest(ex.manifest)
    ex.evidence.write_text('changed public development evidence')
    with pytest.raises(HarnessError,match='Frozen pipeline drift'):
        driver.verify_pipeline_manifest(ex.manifest)


def test_unmeasured_runtime_profile_cannot_hide_behind_manifest(pipeline_freeze,monkeypatch):
    import importlib
    ex=pipeline_freeze
    protocol=importlib.import_module('experiments.mvp.pipeline.sealed.protocol')
    monkeypatch.setattr(protocol,'verify_pipeline_manifest',lambda _:ex.verify())
    ex.runtime['pending_measurement']=True
    with pytest.raises(HarnessError,match='provisional'):
        driver.verify_pipeline_manifest(ex.manifest)
