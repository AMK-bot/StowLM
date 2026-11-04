"""Archive hydration is strict, explicit and separate from live qualification."""
import importlib.util
from pathlib import Path
import shutil

import pytest

from stowage.linear import cache,dependencies,linear_constraints


ARCHIVE=Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification/hash-scheme-1028-review/scheme1-history'


@pytest.fixture(scope='module')
def archive():
    spec=importlib.util.spec_from_file_location('scheme1_archive_test',ARCHIVE/'scheme1_payloads.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('ship',['feeder_500','feeder_1000','feeder_1700'])
def test_original_payload_hydrates_without_becoming_current_qualification(archive,ship):
    model=archive.load_archive_model(ship)
    raw,bank,identity=archive.read_archive_inputs(ship)
    assert identity['historical_hash_scheme_version']=='1'
    assert identity['production_usable'] is False
    assert model.coefficient_certificate['builder_version']=='1.0'
    assert model.coefficient_certificate['inputs_hash']==raw['diagnostics']['coefficient_family_cache_key']
    assert model.coefficient_certificate['table_arrays_sha256']==cache._coefficient_array_digest(model)
    assert identity['response_identity'] in bank['entries']
    with pytest.raises(ValueError):model.sf_per_kg_cell.flags.writeable=True
    assert dependencies.LINEAR_COEFFICIENTS_VERSION!='1.0'
    assert cache.load_model(identity['coefficient_family_cache_key'],root=ARCHIVE/'cache') is None
    with pytest.raises(ValueError,match='lacks default margin-policy certificate'):
        linear_constraints(model,direction='necessary')


@pytest.mark.parametrize('target',['manifest.json','raw/feeder_500.json','candidate-bank.json','npz'])
def test_modified_archived_input_fails_closed(archive,tmp_path,target):
    copied=tmp_path/'history';shutil.copytree(ARCHIVE,copied)
    path=next((copied/'cache/model').glob('*.npz')) if target=='npz' else copied/target
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='checksum mismatch'):
        archive.load_archive_model('feeder_500',archive_root=copied)


def test_missing_archive_never_falls_back_to_live_cache(archive,tmp_path):
    copied=tmp_path/'history';shutil.copytree(ARCHIVE,copied)
    next((copied/'cache/model').glob('*.npz')).unlink()
    with pytest.raises(ValueError,match='missing or malformed input'):
        archive.load_archive_model('feeder_500',archive_root=copied)


@pytest.mark.parametrize('field',['builder_version','inputs_hash','table_arrays_sha256'])
def test_original_certificate_is_independently_validated(archive,monkeypatch,field):
    load=cache.load_payload
    def changed(*args,**kwargs):
        payload=load(*args,**kwargs)
        payload['metadata']['coefficient_certificate'][field]='altered'
        return payload
    monkeypatch.setattr(cache,'load_payload',changed)
    with pytest.raises(ValueError,match='original historical coefficient certificate is invalid'):
        archive.load_archive_model('feeder_500')
