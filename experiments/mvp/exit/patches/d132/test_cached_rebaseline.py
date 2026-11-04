"""CPU-only regression checks for the frozen D131 baseline receipt."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[5]
SPEC=importlib.util.spec_from_file_location('cached_rebaseline',ROOT/'experiments/mvp/exit/rebaseline_on_host.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def measured():
    ready=json.loads((ROOT/'experiments/mvp/exit/deployment-d131/rebaseline-ready.json').read_text())
    expected={k:v for k,v in ready['specification'].items() if k!='decoded_source_pins'}
    return ready,expected


def test_identical_specification(measured):
    ready,expected=measured
    assert MODULE._cached_specification_matches({'specification':expected},expected)


def test_exact_operational_transition(measured):
    ready,expected=measured
    assert MODULE._cached_specification_matches(ready,expected)


@pytest.mark.parametrize('mutation',('checker','adapter','maps','receipt','inventory','decoded'))
def test_changed_evidence_refused(measured,mutation):
    original,expected=measured
    ready=copy.deepcopy(original)
    if mutation=='checker':ready['specification']['current_sources']['src/stowage/checker.py']='changed'
    elif mutation=='adapter':ready['specification']['parent_identity']={}
    elif mutation=='maps':ready['specification']['maps']=[]
    elif mutation=='receipt':ready['compatibility_receipt']['activation_sha256']='changed'
    elif mutation=='inventory':ready['specification']['unexpected']=True
    else:ready['decoded_source_pins']={}
    assert not MODULE._cached_specification_matches(ready,expected)


def test_cached_relaunch_never_reserves_gpu(measured,monkeypatch,tmp_path):
    ready,expected=measured
    from llm.exit.runner import source_pins
    assert source_pins()==expected['current_sources']
    class Resources:
        @staticmethod
        def require_current_parent(parent):pass
        @staticmethod
        def reservation(*args,**kwargs):raise AssertionError('cached audit must not reserve GPU')
    services=dict(completion_ready=lambda *args:True,resources=Resources,
        preflight_mlx_device=lambda:dict(cpu_test_only=True),
        frozen_audit_paths=lambda *args:[Path(item['path']) for item in expected['maps']],
        source_pins=source_pins,adapter_identity=lambda parent:expected['parent_identity'],
        checker_epoch=lambda:'cached-not-remeasured')
    monkeypatch.setattr(MODULE,'_services',lambda:services)
    (tmp_path/'rebaseline-ready.json').write_text(json.dumps(ready))
    args=SimpleNamespace(out=tmp_path,parent=Path(expected['parent']),
        corpus=ROOT/'experiments/mvp/corpus',audit_list=ROOT/'experiments/mvp/valsplit/audit_small30.txt',
        code_ready=None,workers=1)
    assert MODULE.run(args)==ready
