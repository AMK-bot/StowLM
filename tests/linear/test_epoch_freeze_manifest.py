"""Finalized producer membership cannot disappear from the live guard."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stowage.linear import dependencies


OWNER=Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification'


@pytest.fixture
def finalized(monkeypatch,tmp_path):
    spec=importlib.util.spec_from_file_location('manifest_guard_test',OWNER/'freeze.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    home=tmp_path/'run/closure';producer=tmp_path/'run/generated.py'
    producer.parent.mkdir();producer.write_text('LIMIT=2\n')
    baseline=dict(hash_scheme_version=dependencies.LINEAR_DEPENDENCY_HASH_SCHEME_VERSION,
        python_sources={},input_bytes={},profiles={},runtime_inputs={})
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'HERE',home)
    monkeypatch.setattr(module,'snapshot',lambda:dict(baseline))
    monkeypatch.setattr(module,'dependencies',lambda:vars(dependencies))
    module.capture(home);module.freeze_producers([producer],home)
    assert module.check(output=home)['passed']
    return SimpleNamespace(module=module,home=home,producer=producer,
        manifest=home/'run-producers.json')


@pytest.mark.parametrize('action',['delete','move','content'])
def test_finalized_manifest_disappearance_or_mutation_aborts(finalized,action):
    if action=='delete':finalized.manifest.unlink()
    elif action=='move':finalized.manifest.rename(finalized.home/'moved-manifest.json')
    else:
        value=json.loads(finalized.manifest.read_text())
        value['semantic_sources']={};value['sources']={}
        finalized.manifest.write_text(json.dumps(value))
    finalized.producer.write_text('LIMIT=99\n')
    result=finalized.module.check(output=finalized.home)
    assert result['passed'] is False
    assert json.loads((finalized.home.parent/'ABORT').read_text())['passed'] is False
    with pytest.raises(RuntimeError,match='source guard failed'):
        finalized.module.assert_current()


def test_finalized_manifest_raw_audit_pin_is_immutable(finalized):
    value=json.loads(finalized.manifest.read_text())
    value['sources']['run/generated.py']='0'*64
    finalized.manifest.write_text(json.dumps(value))
    assert finalized.module.check(output=finalized.home)['passed'] is False
    assert (finalized.home.parent/'ABORT').exists()


def test_only_private_bootstrap_check_before_manifest_is_allowed(monkeypatch,tmp_path):
    spec=importlib.util.spec_from_file_location('bootstrap_guard_test',OWNER/'freeze.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    state=dict(hash_scheme_version='2',python_sources={},input_bytes={},profiles={},runtime_inputs={})
    monkeypatch.setattr(module,'snapshot',lambda:dict(state))
    home=tmp_path/'closure';module.capture(home)
    assert module.check(output=home,_bootstrap=True)['passed']
    assert module.check(output=home)['passed'] is False
    assert (home.parent/'ABORT').exists()


@pytest.mark.parametrize('missing',['finalized.json','run-producers.json'])
def test_finalization_receipt_or_manifest_is_required(finalized,missing):
    (finalized.home/missing).unlink()
    assert finalized.module.check(output=finalized.home)['passed'] is False
    assert (finalized.home.parent/'ABORT').exists()


def test_finalization_does_not_mutate_baseline(finalized):
    state=json.loads((finalized.home/'start.json').read_text())
    assert 'producer_manifest_sha256' not in state
    receipt=json.loads((finalized.home/'finalized.json').read_text())
    assert receipt['start_sha256']==finalized.module.raw_hash(finalized.home/'start.json')
    assert receipt['producer_manifest_sha256']==finalized.module.raw_hash(finalized.manifest)
