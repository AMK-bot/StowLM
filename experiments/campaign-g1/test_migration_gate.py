"""Source identity alone must never admit an unfinished rule migration."""
import importlib.util
from pathlib import Path

import pytest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def driver(name):
    spec=importlib.util.spec_from_file_location('migration_gate_'+name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def no_numerical_work(freeze,monkeypatch):
    pin=dict(schema_version=1,sources={},files={},sha256='empty-test-cache')
    monkeypatch.setattr(freeze,'snapshot_numerical_caches',lambda root:pin)
    monkeypatch.setattr(freeze,'verify_numerical_caches',lambda pin:pin)


def test_production_freeze_refuses_present_but_unproved_rules(monkeypatch):
    freeze=driver('freeze');no_numerical_work(freeze,monkeypatch)
    # Empty source paths isolate admission: this is not a real freeze or campaign.
    with pytest.raises(ValueError,match='migration.*not ready'):
        freeze.create_manifest(ROOT,paths=[])


def test_diagnostic_freeze_preserves_all_missing_obligations(monkeypatch):
    freeze=driver('freeze');no_numerical_work(freeze,monkeypatch)
    frozen=freeze.create_manifest(ROOT,paths=[],diagnostic=True)
    migration=frozen['migration']
    assert migration['passed'] is False
    assert frozen['evidence_eligible'] is False
    assert migration['post_freeze']['status']=='pending_post_freeze'
    assert migration['post_freeze']['complete'] is False
    canaries=driver('migration_canaries')
    expected=canaries.canary_report(ROOT)
    assert migration['canaries']['missing_cases']==expected['missing_cases']
    assert len(expected['missing_cases'])==len(canaries.CANARIES)
    actual={(row['code'],row['detail']) for row in migration['issues']}
    static=driver('migration_contract').static_contract_report(ROOT,frozen['required_rules']['policy']['required_rule_ids'])
    assert {(row['code'],row['detail']) for row in static['issues']} <= actual
    assert {('canary.'+row['case'],','.join(row['missing'])) for row in expected['issues']} <= actual


def test_g1_pass_boolean_and_yield_cannot_waive_migration_inventory():
    policy=driver('rule_policy')
    summary=dict(epoch_id='e',g1_met=True,min_yield={'tiny':.5},solve_yield={'tiny':1.0})
    with pytest.raises(ValueError,match='migration evidence'):
        policy.validate_g1_approval(summary,'e',['tiny'])


def test_diagnostic_allow_drift_never_waives_migration_proof_drift(tmp_path,monkeypatch):
    freeze=driver('freeze');no_numerical_work(freeze,monkeypatch)
    first=dict(passed=False,component_epoch={},canaries={},issues=[{'code':'missing','detail':'proof'}])
    monkeypatch.setattr(freeze,'migration_gate',lambda *a,**k:first)
    path=tmp_path/'freeze.json'
    freeze.atomic_json(path,freeze.create_manifest(ROOT,paths=[],diagnostic=True))
    second={**first,'issues':[{'code':'missing','detail':'changed proof'}]}
    monkeypatch.setattr(freeze,'migration_gate',lambda *a,**k:second)
    with pytest.raises(ValueError,match='migration gate inputs/proofs differ'):
        freeze.check_manifest(path,allow_drift=True,diagnostic=True)


def test_readiness_reports_exact_missing_canaries_without_freezing(capsys):
    import json
    freeze=driver('freeze')
    assert freeze.main(['--readiness'])==1
    result=json.loads(capsys.readouterr().out)
    assert result['passed'] is False
    assert result['canaries']['missing_cases']==list(driver('migration_canaries').CANARIES)
    assert result['post_freeze']['status']=='pending_post_freeze'


@pytest.mark.parametrize('drift',[True,False])
def test_approval_rechecks_source_and_cache_after_inventory_work(tmp_path,monkeypatch,drift):
    import hashlib
    import json
    from types import SimpleNamespace
    policy=driver('rule_policy')
    def save(name,value):
        path=tmp_path/name;raw=json.dumps(value).encode();path.write_bytes(raw)
        return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
    frozen=dict(epoch_id='e',diagnostic=False,evidence_eligible=True)
    declaration={'epoch_id':'e','diagnostic':False,'evidence_eligible':True,
        'freeze_manifest':save('freeze.json',frozen)}
    for key in ('instance_manifest','campaign_manifest'):
        declaration[key]=save(key+'.json',{})
    calls=[]
    def check(path):
        calls.append(path)
        if len(calls)>1 and drift:raise RuntimeError('source/cache drift during migration admission')
        return {**frozen,'_manifest_sha256':declaration['freeze_manifest']['sha256']}
    evidence=SimpleNamespace(assert_component_epoch=lambda *a:None,evidence_report=lambda *a,**k:{
        'complete':True,'bindings':{value['path']:value['sha256'] for key,value in declaration.items()
            if key in ('instance_manifest','campaign_manifest')}})
    modules={'freeze':SimpleNamespace(check_manifest=check),'migration_evidence':evidence}
    monkeypatch.setattr(policy,'_module',lambda name:modules[name])
    summary=dict(migration_evidence=save('journal.json',declaration),solve_yield={'tiny':1},d62_crossing={'tiny':{}})
    if drift:
        with pytest.raises(RuntimeError,match='drift during migration admission'):
            policy._produce_live_migration_report(summary,'e')
    else:
        assert policy._produce_live_migration_report(summary,'e')['complete'] is True
    assert len(calls)==2
