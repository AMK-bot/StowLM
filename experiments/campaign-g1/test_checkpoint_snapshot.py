"""A validated checkpoint returns its checked bytes, not a pathname reread."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture
def campaign(monkeypatch):
    spec = importlib.util.spec_from_file_location('r2_checkpoint_campaign', HERE/'campaign.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'freeze_check', lambda *_: {})
    monkeypatch.setattr(module, 'runner_check', lambda *_: None)
    return module


def test_checkpoint_writer_hashes_exact_result_bytes(campaign, tmp_path, monkeypatch):
    result = tmp_path/'result.json'
    original = json.dumps({'attempt_id':'one','adapter_completed':True,'evidence_eligible':True}).encode()
    result.write_bytes(original)
    sha = campaign.sha
    def replace_before_independent_hash(path):
        if Path(path) == result:
            result.write_text(json.dumps({'attempt_id':'one','adapter_completed':True,'diagnostic':True}))
        return sha(path)
    monkeypatch.setattr(campaign,'sha',replace_before_independent_hash)
    campaign.write_done(tmp_path/'done.json', {'epoch':'one'}, result)
    marker=json.loads((tmp_path/'done.json').read_bytes())
    assert marker['result_sha256'] == hashlib.sha256(original).hexdigest()


def test_campaign_resume_returns_verified_row_without_rereading(campaign, tmp_path, monkeypatch):
    directory=tmp_path/'4.7/one'
    result=directory/'result.json'
    original={'attempt_id':'one','adapter_completed':True,'evidence_eligible':True,'stage':'4.7'}
    campaign.atomic_json(result,original)
    identity={'epoch_id':'e'}
    job={'stage':'4.7','id':'one','input':{'path':'unused','sha256':'input','rung':'tiny'}}
    marker=directory/'done.json'
    campaign.write_done(marker,dict(campaign=identity,job=job),result)
    valid_done=campaign.valid_done
    def validated_then_replaced(path, expected):
        valid=valid_done(path,expected)
        campaign.atomic_json(result,{**original,'diagnostic':True,'evidence_eligible':False})
        return valid
    monkeypatch.setattr(campaign,'valid_done',validated_then_replaced)
    restored=campaign.run_shard(SimpleNamespace(),identity,job,tmp_path)
    assert restored['evidence_eligible'] is True
    assert restored.get('diagnostic') is not True


def test_candidate_classification_uses_the_hashed_artifact_bytes(campaign, tmp_path, monkeypatch):
    path = tmp_path/'candidate.json'
    original = json.dumps({'instance_id':'synthetic','moves':[]}).encode()
    path.write_bytes(original)
    sha = campaign.sha
    def change_between_hash_and_classification(file):
        digest = sha(file)
        if Path(file) == path:
            path.write_text('{"metadata":"replacement"}')
        return digest
    monkeypatch.setattr(campaign,'sha',change_between_hash_and_classification)
    inventory=campaign.artifact_inventory(tmp_path)
    assert inventory[0]['sha256'] == hashlib.sha256(original).hexdigest()
    assert inventory[0]['candidate'] is True


def test_summary_checkpoint_refuses_replaced_validated_dependencies(campaign, tmp_path):
    dependency = tmp_path/'stage-done.json'
    dependency.write_text('{"validated":true}')
    digest = campaign.sha(dependency)
    dependency.write_text('{"validated":false}')
    result = tmp_path/'summary/result.json'
    campaign.atomic_json(result, {'attempt_id':'summary-one'})
    with pytest.raises(ValueError, match='dependency changed'):
        campaign.write_done(tmp_path/'summary/done.json', {'epoch':'e'}, result,
            [dependency], expected_dependencies={str(dependency):digest})
