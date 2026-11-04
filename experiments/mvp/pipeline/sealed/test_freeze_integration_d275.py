"""Staged controller uses p21 rather than its obsolete source census."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType

import pytest

from experiments.mvp.pipeline.sealed import budget, calibration, common


@pytest.fixture
def sealed(tmp_path,monkeypatch):
    name='experiments.mvp.pipeline.sealed._driver_freeze_d275'
    spec=importlib.util.spec_from_file_location(name,common.HERE/'fixtures/d275/driver.py')
    driver=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,name,driver)
    spec.loader.exec_module(driver)
    here=tmp_path/'sealed'
    here.mkdir()
    for file in ('PREREG.md','preregistration.json','AMENDMENT.md','AMENDMENT.sha256'):
        shutil.copyfile(common.HERE/'fixtures/d275'/file,here/file)
    corpus=tmp_path/'corpus'
    envelope=tmp_path/'envelope'
    corpus.mkdir(); envelope.mkdir()
    common.atomic_json(corpus/'manifest.json',{'metadata_only':True})
    common.atomic_json(envelope/'MANIFEST.json',{'metadata_only':True})
    manifest=tmp_path/'freeze/runs/epoch/manifest.json'
    manifest.parent.mkdir(parents=True)
    data=dict(scope='pipeline',schema_version='stowlm.pipeline-freeze/1',tree_sha256='b'*64)
    common.atomic_json(manifest,data)
    evidence=tmp_path/'public/runs/evidence.json'
    evidence.parent.mkdir(parents=True)
    common.atomic_json(evidence,{'synthetic':True})
    pins={str(evidence):common.sha256(evidence)}
    module=ModuleType('experiments.mvp.pipeline.sealed.protocol')
    calls=[]
    def verify(path):
        assert Path(path)==manifest
        calls.append(path)
        return data,dict(pins)
    module.verify_pipeline_manifest=verify
    monkeypatch.setitem(sys.modules,module.__name__,module)
    monkeypatch.setattr(driver,'HERE',here)
    monkeypatch.setattr(driver,'PROTOCOL_HOME',here)
    monkeypatch.setattr(driver,'V3',corpus)
    monkeypatch.setattr(driver,'ENVELOPE',envelope)
    monkeypatch.setattr(driver,'GLOBAL_CLAIM',here/'SEALED-CLAIM.json')
    monkeypatch.setattr(driver,'source_inventory',lambda:pytest.fail('legacy source census reached'))
    monkeypatch.setattr(driver,'validate_config',lambda *_:None)
    maps=[dict(map_id='synthetic',cohort='tiny15',path=str(corpus/'DO-NOT-OPEN.json'),sha256='c'*64)]
    monkeypatch.setattr(driver,'declared_sealed_maps',lambda:maps)
    monkeypatch.setattr(budget,'measured_budget',lambda _:dict(workers=1,estimated_hours=1.))
    monkeypatch.setattr(budget,'standard_profile_hash',lambda:'registered-profile-hash')
    monkeypatch.setattr(calibration,'verify_calibration',lambda _:dict(files={}))
    for file in ('runtime.json','calibration.json'):
        common.atomic_json(here/file,{'synthetic':True})
    config=dict(mode='sealed',workers=1,preregistration=str(here/'preregistration.json'),
        pipeline_manifest=str(manifest),runtime_measurement=str(here/'runtime.json'),
        cap_calibration_receipt=str(here/'calibration.json'))
    return driver,here,config,manifest,module,calls,pins


def test_sealed_freeze_and_each_opening_delegate_to_authoritative_guard_without_legacy_census(sealed):
    driver,here,config,manifest,module,calls,pins=sealed
    output=here/'runs/final'
    identity=driver.freeze(config,output)
    assert identity['code']=={} and identity['pipeline_files']==pins
    assert identity['manifest_sha256']==common.sha256(manifest)
    assert identity['tree_digest']=='b'*64
    assert driver.verify_freeze(output)==identity
    assert len(calls)==2
    assert not driver.GLOBAL_CLAIM.exists()
    assert not (Path(config['pipeline_manifest']).parents[3]/'corpus/DO-NOT-OPEN.json').exists()


def test_authoritative_drift_refuses_before_claim_and_any_map_read(sealed,monkeypatch):
    driver,here,config,manifest,module,calls,pins=sealed
    output=here/'runs/final'
    driver.freeze(config,output)
    class Drift(RuntimeError):
        drifts=[dict(path='source.py',reason='changed')]
    monkeypatch.setattr(module,'verify_pipeline_manifest',lambda _:(_ for _ in ()).throw(Drift()))
    with pytest.raises(Drift):
        driver.run(output,release_sealed=True)
    assert not driver.GLOBAL_CLAIM.exists() and not (output/'STARTED.json').exists()


def test_local_registered_config_still_cannot_change_behind_authoritative_manifest(sealed):
    driver,here,config,manifest,module,calls,pins=sealed
    output=here/'runs/final'
    driver.freeze(config,output)
    changed=common.read_json(output/'config.json')
    changed['workers']=2
    common.atomic_json(output/'config.json',changed)
    with pytest.raises(common.HarnessError,match='Frozen config changed'):
        driver.verify_freeze(output)
    assert len(calls)==1
