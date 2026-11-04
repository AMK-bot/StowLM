"""The runner bridge cannot turn a JSON job flag into sealed load permission."""
from types import SimpleNamespace

import pytest

from . import worker
from .common import atomic_json, sha256


def test_only_successful_plan_load_mints_process_local_runner_proof(tmp_path,monkeypatch):
    from stowage import io, model
    path=tmp_path/'synthetic.json';atomic_json(path,{})
    instance=SimpleNamespace()
    monkeypatch.setattr(worker,'_PIPELINE_LOADS',{})
    monkeypatch.setattr(worker,'_authorize_open',lambda *a,**kw:None)
    monkeypatch.setattr(io,'_check_version',lambda value:None)
    monkeypatch.setattr(model.Instance,'model_validate',lambda value:instance)
    job=dict(path=str(path),sha256=sha256(path),mode='sealed',map_id='synthetic',arm='pipeline')
    assert worker._loaded_for_pipeline(instance,job) is False
    assert worker._load(job,phase='prepass') is instance
    assert worker._loaded_for_pipeline(instance,job) is False
    assert worker._load(job,phase='plan') is instance
    assert worker._loaded_for_pipeline(instance,job) is True
    assert worker._loaded_for_pipeline(instance,dict(job,map_id='different')) is False
    assert worker._loaded_for_pipeline(SimpleNamespace(),job) is False


def test_failed_authorization_or_wrong_bytes_never_mint_runner_proof(tmp_path,monkeypatch):
    path=tmp_path/'synthetic.json';atomic_json(path,{})
    monkeypatch.setattr(worker,'_PIPELINE_LOADS',{})
    job=dict(path=str(path),sha256='0'*64,mode='sealed',map_id='synthetic',arm='pipeline')
    monkeypatch.setattr(worker,'_authorize_open',lambda *a,**kw:(_ for _ in ()).throw(ValueError('spent token')))
    with pytest.raises(Exception,match='spent token'):
        worker._load(job)
    assert worker._PIPELINE_LOADS=={}
    monkeypatch.setattr(worker,'_authorize_open',lambda *a,**kw:None)
    with pytest.raises(ValueError,match='map bytes differ'):
        worker._load(job)
    assert worker._PIPELINE_LOADS=={}


def test_manifest_must_pin_the_actual_authorized_runner_seam(monkeypatch):
    from . import driver,runner_seam
    from .prepare_launch_config import configuration
    from . import readers
    support=runner_seam.support_check()
    config=configuration()
    files={str(__import__('pathlib').Path(readers.__file__).resolve()):'synthetic'}
    with pytest.raises(driver.HarnessError,match='Authorized runner seam'):
        driver.validate_release_support(config,dict(pipeline_files=files))
    files.update({support[key]:'synthetic' for key in ('source','assembly_source','core_source')})
    driver.validate_release_support(config,dict(pipeline_files=files))
