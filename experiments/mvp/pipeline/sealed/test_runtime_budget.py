"""D276 release timing is a pinned projection, never a calibration waiver."""
import copy

import pytest

from . import budget, runtime_budget as runtime
from .common import HarnessError, atomic_json, read_json


@pytest.fixture
def recipe(tmp_path):
    registration=tmp_path/'preregistration.json'
    atomic_json(registration,dict(execution=dict(runtime_budget=dict(status=runtime.AUTHORIZATION))))
    return dict(preregistration=str(registration),profile='standard',workers=4,
        arm_deadlines_s=runtime.DEADLINES.copy(),audit_timeout_s=300,prepass_timeout_s=1800,
        twostep_s=15,twostep_finalize_s=15)


@pytest.fixture
def receipt():
    return runtime.build_receipt()


def test_registered_complete44_sample_is_accepted_as_conditional_projection(tmp_path,recipe,receipt):
    path=tmp_path/'runtime.json'
    atomic_json(path,receipt)
    result=budget.measured_budget(dict(recipe,runtime_measurement=str(path)))
    assert result['estimated_hours_range']==receipt['estimated_hours_range']
    assert result['estimated_hours']==receipt['estimated_hours_range'][1]
    assert receipt['sample_rows']==44 and len(receipt['per_cohort_arm'])==8
    assert receipt['confidence_interval'] is False and receipt['study_complete'] is False
    assert receipt['cap_calibration_waived'] is False
    assert receipt['reader_inference_measured'] is False
    assert receipt['supplemental_rows_used']==0
    assert 'unbounded' in receipt['admission_wait']


@pytest.mark.parametrize('change',[
    lambda r:r['estimated_hours_range'].__setitem__(1,1.),
    lambda r:r['all_input_hashes'].__setitem__(next(iter(r['all_input_hashes'])),'drift'),
    lambda r:r['code_sha256'].__setitem__(next(iter(r['code_sha256'])),'drift'),
    lambda r:r.__setitem__('binding_sha256','drift'),
    lambda r:r.__setitem__('cap_calibration_waived',True),
])
def test_receipt_timing_input_code_or_recipe_drift_refuses(recipe,receipt,change):
    change(receipt)
    with pytest.raises(HarnessError,match='receipt drift'):
        runtime.validate_receipt(receipt,recipe)


@pytest.mark.parametrize('key,value',[('workers',8),('profile','live'),('audit_timeout_s',200),
    ('prepass_timeout_s',1200),('arm_deadlines_s',dict(runtime.DEADLINES,pipeline=1800))])
def test_release_configuration_cannot_change_registered_recipe(recipe,receipt,key,value):
    recipe[key]=value
    with pytest.raises(HarnessError,match='runtime config differs'):
        runtime.validate_receipt(receipt,recipe)


def test_stopped_study_cannot_be_accepted_without_preopen_amendment(recipe,receipt):
    atomic_json(recipe['preregistration'],dict(execution=dict(runtime_budget=dict(status='native_development_study'))))
    with pytest.raises(HarnessError,match='registration authorization'):
        runtime.validate_receipt(receipt,recipe)


def test_missing_timing_cell_refuses_even_if_receipt_claims44_rows(monkeypatch,recipe,receipt):
    projected=runtime.projection_31.build_projection()
    projected['per_cohort_arm'].pop()
    monkeypatch.setattr(runtime.projection_31,'build_projection',lambda:projected)
    with pytest.raises(HarnessError,match='all44 measured rows'):
        runtime.validate_receipt(receipt,recipe)


def test_original_complete_recorded_config_is_checked_not_missing_top_level_config(monkeypatch,recipe,receipt):
    original=runtime.read_json
    def read(path):
        value=original(path)
        if str(path)==str(runtime.projection_31.STUDY/'run/rows.json'):
            value=copy.deepcopy(value)
            row=next(r for r in value if r.get('profile'))
            assert 'config' not in row['profile']
            row['profile']['measurement']['engine_kwargs']['config']['assignment_tries']+=1
        return value
    monkeypatch.setattr(runtime,'read_json',read)
    with pytest.raises(HarnessError,match='recorded standard profile differs'):
        runtime.validate_receipt(receipt,recipe)


def test_unrecorded_timeout_profile_is_never_invented(monkeypatch,recipe,receipt):
    projected=runtime.projection_31.build_projection()
    row=next(r for r in projected['rows'] if r['arm'] in {'pipeline','instruction'}
             and not r.get('recorded_profile_sha256'))
    row['child_deadline_censored']=False
    monkeypatch.setattr(runtime.projection_31,'build_projection',lambda:projected)
    with pytest.raises(HarnessError,match='timeout profile accounting'):
        runtime.validate_receipt(receipt,recipe)


def test_projection_artifact_must_match_rebuilt_source(monkeypatch,receipt):
    projected=runtime.projection_31.build_projection()
    projected['source']['all_input_hashes']['public-development-source']='drift'
    with pytest.raises(HarnessError,match='artifact differs'):
        runtime.build_receipt(projection=projected)
