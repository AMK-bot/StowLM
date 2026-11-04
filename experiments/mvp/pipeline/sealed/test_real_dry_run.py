"""Native-study orchestration invariants, without search or GPU inference."""
import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed import common, driver, launch, readers, real_dry_run as study
from experiments.mvp.pipeline.sealed.instructions import CAP_REDUCTION


def test_registered_cap_is_tighter_after_real_development_probe():
    assert CAP_REDUCTION==3


def test_native_signing_routes_real_signer_into_fence_without_changing_source_root(monkeypatch):
    from experiments.mvp.pipeline.sealed import native_runtime
    from experiments.mvp.pipeline.package import receipt
    calls=[]
    monkeypatch.setattr(receipt,'sign_receipt',lambda data,**kw:calls.append((data,kw)) or 'signed')
    assert native_runtime._sign_receipt({'native':True},key_dir='/outside/fence')=='signed'
    assert calls==[({'native':True},{'key_dir':common.HERE/'.keys'})]


def test_projection_uses_measured_slots_once_and_reserves_reader_and_tail():
    rows=[]
    for c in ('tiny15','small30','envelope24'):
        for a in (('pipeline','twostep','instruction','twostep_300') if c=='envelope24' else ('pipeline','twostep')):
            rows.append(dict(cohort=c,arm=a,child_elapsed_s=4.,audit_elapsed_s=1.,status='error'))
    result=study.measured_projection(rows)
    assert result['estimated_hours']==pytest.approx((186*5/4+5+1800)/3600)
    assert result['workers']==4
    assert 'unbounded' in result['admission_hours']
    assert study.measured_projection(rows[:-1])['estimated_hours'] is None
    # Source verification/package overhead belongs in the measured slot too;
    # admission is reported separately rather than treated as engine work.
    for row in rows:
        row.update(driver_elapsed_s=12.,driver_admission_wait_s=2.)
    assert study.measured_projection(rows)['estimated_hours']==pytest.approx((186*10/4+10+1800)/3600)


def test_development_admission_is_real_and_waits_on_missing_stale_busy(tmp_path):
    path=tmp_path/'fleet.json'
    states=[dict(mask=255,load1=1,t=-30),dict(mask=255,load1=46,t=100),
            dict(mask=4095,load1=12,t=100)]
    waits=[]
    def sleep(_):
        waits.append(1)
        common.atomic_json(path,states.pop(0))
    row=study.admission(path,sleep=sleep,now=lambda:100)
    assert len(waits)==3 and row['cores']==12 and row['load1']==12
    assert 'development' in row['policy']


def test_final_release_needs_all18cores_and_no_other_fleet_work(tmp_path):
    path=tmp_path/'fleet.json'
    common.atomic_json(path,dict(mask=4095,load1=4,t=100,other_fleet_work=[]))
    assert common.admission(path,workers=4,min_cores=18,now=100) is None
    common.atomic_json(path,dict(mask=(1<<18)-1,load1=6,t=100,other_fleet_work=[]))
    assert common.admission(path,workers=4,min_cores=18,now=100)['cores']==18


def test_recorded_reader_is_not_claimed_as_adopted_model_and_is_hash_bound(tmp_path):
    from experiments.mvp.pipeline.sealed.test_instructions import facts
    from experiments.mvp.reader.schema import context_from_instance
    from experiments.mvp.pipeline.sealed.instructions import select_rule,sentence_for
    raw=facts()
    raw['load_list']=['BOX1']
    context=context_from_instance(raw)
    rule=select_rule(raw,0)
    text=sentence_for(rule)
    path=tmp_path/'recordings.json'
    common.atomic_json(path,dict(readings={common.object_hash(dict(sentence=text,context=context)):rule}))
    parser=readers.development_recorded(path=path,expected_sha256=common.sha256(path))
    _,evidence=parser.parse(text,context)
    assert evidence['recorded'] and evidence['development_only']
    assert evidence['adapter_sha256'] is None and 'not adapter-v2 inference' in evidence['origin']
    with pytest.raises(ValueError,match='hash differs'):
        readers.development_recorded(path=path,expected_sha256='a'*64)


def test_missing_manifest_blocks_before_any_study_or_claim(tmp_path,monkeypatch):
    monkeypatch.setattr(driver,'verify_pipeline_manifest',lambda _:pytest.fail('absentmanifest'))
    with pytest.raises(common.HarnessError,match='manifest is absent'):
        launch.launch(tmp_path/'not-landed.json')
    assert not (tmp_path/'STARTED.json').exists()


def test_development_recordings_cannot_be_released(monkeypatch):
    from experiments.mvp.pipeline import runner
    monkeypatch.setattr(runner,'supports_sealed_evaluation',True,raising=False)
    config=dict(reader_factory='experiments.mvp.pipeline.sealed.readers:development_recorded')
    with pytest.raises(common.HarnessError,match='Development recorded reader'):
        driver.validate_release_support(config,dict(pipeline_files={}))


def test_only_pipeline_jobs_carry_development_calibration_rules(tmp_path):
    cfg=dict(mode='dry-run',twostep_s=15,twostep_finalize_s=15,twostep_budget_s=30,
        arm_deadlines_s=study.DEADLINES,maps=[dict(map_id='m',cohort='envelope24',path='x',sha256='a',
            calibration_rules=['development-only'])])
    jobs=list(driver.jobs_for(cfg,tmp_path))
    for job in jobs:
        assert ('calibration_rules' in job)==(job['arm']=='pipeline')
        assert job['wall_timeout_s']==study.DEADLINES[job['arm']]


def test_native_budget_receipt_cannot_hide_recorded_reader_or_change_deadlines(tmp_path,monkeypatch):
    from experiments.mvp.pipeline.sealed import budget
    monkeypatch.setattr(budget,'standard_profile_hash',lambda:'profile')
    monkeypatch.setattr(budget,'sha256',lambda _:'binding')
    cells=[]
    for cohort in budget.POPULATIONS:
        for arm in (('pipeline','twostep','instruction','twostep_300') if cohort=='envelope24' else ('pipeline','twostep')):
            cells.append(dict(cohort=cohort,arm=arm,n=8 if cohort=='envelope24' else 3,mean_s=10.,max_s=12.))
    receipt=dict(schema_version='sealed-runtime-budget/2',split='development',fixture_surrogate=False,
        planners_stubbed=False,workers=4,profile_sha256='profile',binding_sha256='binding',
        study_complete=True,harness_errors=0,
        twostep_s=15,twostep_finalize_s=15,arm_deadlines_s=study.DEADLINES,
        serial_reader_reserve_s=1800,reader_inference_measured=False,per_map=cells,
        final_idle_condition=dict(enabled_cores=18,max_load1=6,other_fleet_work=[]))
    path=tmp_path/'native-budget.json'
    common.atomic_json(path,receipt)
    cfg=dict(runtime_measurement=str(path),workers=4,twostep_s=15,twostep_finalize_s=15,arm_deadlines_s=study.DEADLINES)
    result=budget.measured_budget(cfg)
    assert result['estimated_hours']==pytest.approx((186*10/4+12+1800)/3600)
    common.atomic_json(path,dict(receipt,reader_inference_measured=True))
    with pytest.raises(common.HarnessError,match='Native runtime study differs'):
        budget.measured_budget(cfg)
