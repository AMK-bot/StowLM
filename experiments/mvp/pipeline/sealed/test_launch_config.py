"""Canonical launch settings and masked adopted-reader identity, offline."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed import common, prepare_launch_config as setup, readers

KNOWN = dict(name='swa-last-5', adapter_sha256='a'*64, decoder_sha256='d'*64,
    domain_words_sha256='e'*64, decoding_policy='reader-v3-schema-mask/2-d224')


def test_prepared_config_names_registered_sealed_recipe_and_pending_public_evidence(monkeypatch):
    monkeypatch.setattr(setup, 'registered_reader_identity', lambda: (KNOWN, {'path':'adoption', 'sha256':'f'*64}))
    prereg=common.read_json(common.HERE/'preregistration.json')
    prereg['reader_adoption'].update(KNOWN)
    monkeypatch.setattr(setup, 'read_json', lambda _:prereg)
    config=setup.configuration()
    assert config['mode']=='sealed' and config['cohorts']==['tiny15','small30','envelope24']
    assert config['profile']=='standard' and config['workers']==4
    assert config['required_enabled_cores']==18
    assert config['arm_deadlines_s']==dict(pipeline=1200,instruction=1200,twostep=180,twostep_300=420)
    assert config['pipeline_manifest'].endswith('/freeze/manifest-pipeline.json')
    assert config['runtime_measurement'].endswith('/measurements/runtime-budget-D276.json')
    assert config['cap_calibration_receipt'].endswith('/calibration-reaudit-D276-v3/cap-calibration.json')
    assert 'maps' not in config and config['standing']==''
    assert config['reader_factory']==setup.FACTORY and config['reader_mode']=='live'
    assert config['parser_options']['reader_identity']==KNOWN
    assert config['confirmation_source']=='sealed_expected_match'


@pytest.mark.parametrize('field,value', [('name','adapter-v2'), ('mode','recorded'),
    ('adapter_sha256','b'*64), ('decoder_sha256','b'*64),
    ('domain_words_sha256','b'*64), ('decoding_policy','old-mask'), ('decision','D264')])
def test_config_refuses_reader_registration_drift_without_opening_maps(monkeypatch,field,value):
    prereg=common.read_json(common.HERE/'preregistration.json')
    prereg['reader_adoption'].update(KNOWN)
    prereg['reader_adoption'][field]=value
    monkeypatch.setattr(setup, 'registered_reader_identity', lambda: (KNOWN, {}))
    monkeypatch.setattr(setup, 'read_json', lambda _:prereg)
    with pytest.raises(common.HarnessError,match='reader differs'):
        setup.configuration()


def test_factory_uses_adopted_masked_reader_lazily_instead_of_legacy_generate(monkeypatch):
    from experiments.mvp.pipeline.reader_final import reader
    calls=[]
    full=dict(KNOWN,adapter_path='/recorded/adopted')
    monkeypatch.setattr(reader,'identity',lambda:full)
    monkeypatch.setattr(reader,'AdoptedReader',lambda **kw:calls.append(kw) or SimpleNamespace(masked=True))
    actual=readers.adopted_reader(adapter_sha256=KNOWN['adapter_sha256'],reader_identity=KNOWN)
    assert actual.masked and calls==[{'known':full}]


@pytest.mark.parametrize('field,value',[('adapter_sha256','b'*64),('decoder_sha256','b'*64),
    ('domain_words_sha256','b'*64),('decoding_policy','older-mask')])
def test_factory_refuses_any_pinned_reader_drift_before_gpu_construction(monkeypatch,field,value):
    from experiments.mvp.pipeline.reader_final import reader
    current=dict(KNOWN,adapter_path='/recorded/adopted')
    current[field]=value
    monkeypatch.setattr(reader,'identity',lambda:current)
    monkeypatch.setattr(reader,'AdoptedReader',lambda **kw:pytest.fail('GPU reader constructed on drift'))
    with pytest.raises(ValueError,match='differs'):
        readers.adopted_reader(adapter_sha256=KNOWN['adapter_sha256'],reader_identity=KNOWN)


def test_factory_refuses_missing_decoder_pin(monkeypatch):
    from experiments.mvp.pipeline.reader_final import reader
    monkeypatch.setattr(reader,'identity',lambda:KNOWN)
    monkeypatch.setattr(reader,'AdoptedReader',lambda **kw:pytest.fail('constructed without decoder pin'))
    with pytest.raises(ValueError,match='decoder identity'):
        readers.adopted_reader(adapter_sha256=KNOWN['adapter_sha256'])


def test_registration_and_launch_amendment_remain_hash_bound():
    prereg=common.read_json(common.HERE/'preregistration.json')
    digest=common.sha256(common.HERE/'AMENDMENT.md')
    assert prereg['amendment']['sha256']==digest
    assert (common.HERE/'AMENDMENT.sha256').read_text().split()[0]==digest
    assert prereg['reader_adoption']['name']=='swa-last-5'
    assert prereg['reader_adoption']['mode']=='live'
    assert prereg['execution']['runtime_budget']['calibration_gate_unchanged'] is True
