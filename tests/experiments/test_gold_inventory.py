"""Gold inventory and resume gates; no shard is solved by these tests."""
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from eval.splits import canonical_spec, load_inventory, spec_key, split_of


@pytest.fixture(scope='module')
def driver():
    path=Path(__file__).resolve().parents[2]/'experiments/gold-plans/tiny/driver.py'
    spec=importlib.util.spec_from_file_location('gold_inventory_driver',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def inventory():
    return load_inventory()


def member(inventory, label='train'):
    return next(entry['spec'] for entry in inventory.manifest['entries']
                if entry['split']==label and entry['spec']['rung']=='tiny')


def campaign(tmp_path, spec, *, label='train'):
    group=('random_yard' if spec['generator']=='random_yard' else 'simulated_yard')+'/tiny'
    row=dict(split=label,instance='never-open.json',sha256='a'*64,
             split_spec=spec,spec_key=spec_key(spec))
    (tmp_path/'manifest.json').write_text(json.dumps({'groups':{group:{'shards':[row]}}}))
    return group


def test_gold_rejects_policy_consistent_inventory_nonmember(driver, inventory, tmp_path):
    spec={**member(inventory), 'seed':987654321}
    while split_of(spec)!='train':spec['seed']+=1
    campaign(tmp_path,spec)
    with pytest.raises(ValueError,match='inventory'):
        driver.inspect_manifest(tmp_path)


def test_gold_rejects_false_inventory_label_before_opening_shard(driver, inventory, tmp_path):
    campaign(tmp_path,member(inventory,'val'))
    with pytest.raises(ValueError,match='label'):
        driver.inspect_manifest(tmp_path)


def test_gold_selection_records_trusted_release_pin(driver, inventory, tmp_path):
    campaign(tmp_path,member(inventory))
    record=driver.inspect_manifest(tmp_path)[0]
    assert record.split_version==inventory.version
    assert record.split_entries_sha256==inventory.entries_sha256
    assert not (tmp_path/'never-open.json').exists()


def test_gold_source_epoch_includes_loader_inventory_and_production_mask(driver):
    sources=driver.source_fingerprint()['sources']
    assert {'src/stowage/io.py','src/eval/splits.py','src/eval/splits_inventory.py',
            'src/llm/stowlm/mask.py','src/llm/stowlm/format.py'}<=sources.keys()


def test_gold_record_pin_drift_rejected(driver, inventory, tmp_path):
    campaign(tmp_path,member(inventory))
    record=driver.inspect_manifest(tmp_path)[0]
    with pytest.raises(ValueError,match='pin'):
        driver._validate_record(replace(record,split_entries_sha256='0'*64))


def test_gold_resume_refuses_release_pin_drift(driver, inventory, tmp_path, monkeypatch):
    # Isolate the split-release pin from other executives' concurrent edits.
    # Production source-drift refusal is exercised independently below.
    fixed_source=driver.source_fingerprint()
    monkeypatch.setattr(driver,'source_fingerprint',lambda:fixed_source)
    campaign(tmp_path,member(inventory))
    record=driver.inspect_manifest(tmp_path)[0]
    cfg=driver.Config(output=tmp_path/'out',stop_at='2030-01-01T00:00:00+00:00',
                      hard_stop_at='2030-01-01T01:00:00+00:00')
    monkeypatch.setattr(driver,'process_one',lambda *args:dict(accepted=False,status='synthetic'))
    driver.run_one(record,cfg)
    pin_path=cfg.output/'split-release.json'
    pin=json.loads(pin_path.read_text());pin['split_entries_sha256']='0'*64
    pin_path.write_text(json.dumps(pin))
    with pytest.raises(ValueError,match='pin drift'):
        driver.run_one(record,cfg)


def test_gold_manifest_records_pin(driver, inventory, tmp_path):
    result=driver.write_manifest(tmp_path)
    assert result['split_version']==inventory.version
    assert result['split_entries_sha256']==inventory.entries_sha256


def test_gold_manifest_rejects_hash_committed_nonmember(driver, inventory, tmp_path):
    spec={**member(inventory),'seed':987654321}
    while split_of(spec)!='train':spec['seed']+=1
    gold=tmp_path/'plans'/'nonmember.json';gold.parent.mkdir()
    payload=dict(format='tiny-gold@1.0',accepted=True,split='train',split_spec=spec,
                 split_version=inventory.version,split_entries_sha256=inventory.entries_sha256)
    gold.write_text(json.dumps(payload))
    state=tmp_path/'state'/'accepted.json';state.parent.mkdir()
    state.write_text(json.dumps(dict(accepted=True,gold_file=str(gold),
                                    gold_sha256=sha256(gold.read_bytes()).hexdigest())))
    result=driver.write_manifest(tmp_path)
    assert result['accepted']==0
    assert 'inventory' in result['excluded'][0]['reason']


def test_diagnostic_and_production_have_distinct_resume_identity(driver, inventory, tmp_path):
    campaign(tmp_path,member(inventory))
    record=driver.inspect_manifest(tmp_path)[0]
    cfg=driver.Config(output=tmp_path/'out')
    assert driver.record_key(record,cfg)!=driver.record_key(record,replace(cfg,c2_off=True))


def test_production_mask_tokenizer_bytes_pin_epoch(driver, tmp_path, monkeypatch):
    import llm.stowlm.format as formatting
    local=tmp_path/'tokenizer.json';local.write_text('{"version":1}')
    monkeypatch.setattr(formatting,'DEFAULT_TOKENIZER_PATH',tmp_path)
    before=driver.source_fingerprint()
    token_key=str(local.relative_to(driver.ROOT)) if local.is_relative_to(driver.ROOT) else str(local)
    assert before['sources'][token_key]==sha256(local.read_bytes()).hexdigest()
    local.write_text('{"version":2}')
    assert driver.source_fingerprint()['sha256']!=before['sha256']


def test_manifest_uses_replay_c2_evidence_for_scalable_candidate(driver, inventory, tmp_path):
    from stowage.actions import get_action_space
    from stowage.model import CheckReport, MovePlan
    spec=member(inventory)
    space=get_action_space()
    plan=MovePlan(id='synthetic',instance_id='synthetic',call_index=0,moves=[],actions=[],action_space=f'{space.name}@{space.version}')
    report=CheckReport(passed=True)
    candidate=dict(accepted=True,shippable=True,mask_verified=True,global_screen='on',metrics={})
    payload=dict(format='tiny-gold@1.0',accepted=True,split='train',split_spec=spec,spec_key=spec_key(spec),
        split_version=inventory.version,split_entries_sha256=inventory.entries_sha256,
        plan=plan.model_dump(mode='json'),check_report=report.model_dump(mode='json'),
        gold_source='scalable',scalable=candidate,shippable=True,mask_verified=True,c2='on/default',
        component_versions=driver.component_versions(),relevant_source_sha256=driver.source_fingerprint()['sha256'],
        input_sha256='a'*64,instance_id='synthetic',objective=0.,gold_status='feasible',group='random_yard/tiny')
    gold=tmp_path/'plans'/'scalable.json';gold.parent.mkdir();gold.write_text(json.dumps(payload))
    state=tmp_path/'state'/'accepted.json';state.parent.mkdir()
    state.write_text(json.dumps(dict(accepted=True,gold_file=str(gold),gold_sha256=sha256(gold.read_bytes()).hexdigest())))
    manifest=driver.write_manifest(tmp_path)
    assert manifest['accepted']==1,manifest['excluded']


def test_gold_publication_refuses_source_change_during_publication(driver,tmp_path,monkeypatch):
    initial=driver.source_fingerprint();calls=[]
    def changing_source():
        calls.append(True)
        return initial if len(calls)==1 else {**initial,'sha256':'0'*64}
    monkeypatch.setattr(driver,'source_fingerprint',changing_source)
    with pytest.raises(ValueError,match='source or component versions changed during gold publication'):
        driver.write_manifest(tmp_path)
    assert len(calls)==2
