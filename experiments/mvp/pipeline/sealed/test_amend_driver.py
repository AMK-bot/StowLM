"""Logic2 control regressions: synthetic files, no search or sealed reads."""
import json
import subprocess
import sys

import pytest

from experiments.mvp.pipeline.sealed import common, driver
from experiments.mvp.pipeline.sealed.test_review import dry_harness


@pytest.mark.parametrize('workers,cores,load,other,ok', [
    (10,12,12,[],True), (11,12,12,[],False), (10,12,12.1,[],False),
    (16,18,18,[],True), (17,18,1,[],False), (2,12,1,['profile-study'],False),
    (2,12,1,None,False)])
def test_amend_admission_reserves_two_cores_and_excludes_other_work(tmp_path, workers, cores, load, other, ok):
    path=tmp_path/'telemetry.json'
    common.atomic_json(path, dict(mask=(1<<cores)-1, load1=load, t=100, other_fleet_work=other))
    assert (common.admission(path, now=100, workers=workers) is not None) is ok


def test_amend_hung_child_is_killed_once_and_later_trial_runs(tmp_path, monkeypatch):
    run=dry_harness(tmp_path, monkeypatch)
    cfg=common.read_json(run/'config.json')
    cfg['child_timeout_s']=.05
    common.atomic_json(run/'config.json', cfg)
    freeze=common.read_json(run/'freeze.json')
    freeze['config_sha256']=common.sha256(run/'config.json')
    freeze['run_id']=common.object_hash({k:v for k,v in freeze.items() if k!='run_id'})
    common.atomic_json(run/'freeze.json', freeze)
    original_popen=subprocess.Popen
    original_child=driver.run_child
    owned=[]
    calls=[]
    def hung(*args, **kw):
        child=original_popen([sys.executable,'-c','import time; time.sleep(30)'], **kw)
        owned.append(child)
        return child
    monkeypatch.setattr(driver.subprocess, 'Popen', hung)
    def child(job, target, timeout_s):
        calls.append(job['arm'])
        if job['arm']=='pipeline':
            return original_child(job,target,timeout_s)
        row=dict(map_id=job['map_id'], cohort=job['cohort'],arm=job['arm'],
                 map_sha256=job['sha256'],status='no_checked_plan',passed=False,
                 full_checker_pass=False,plan=None,wall_s=.01)
        common.atomic_json(target/'row.json',row)
        return row
    monkeypatch.setattr(driver,'run_child',child)
    monkeypatch.setattr(driver,'audit_child',lambda job,target:
        dict(common.read_json(target/'row.json'),audit_verified=True,audit_fresh_process=True))
    result=driver.run(run,admission_wait=lambda _: {'fixture':True})
    assert owned and all(p.poll() is not None for p in owned)
    assert calls==['pipeline','twostep']
    assert result['rows']==result['expected_rows']==2
    assert result['complete'] and not result['sealed_claim_eligible']
    rows=common.read_json(run/'rows.json')
    assert rows[0]['status']=='deadline_exceeded' and rows[0]['harness_error'] is False
    assert rows[1]['status']=='no_checked_plan'
    assert not (run/'FAILED.json').exists()
    with pytest.raises(common.HarnessError,match='reruns'):
        driver.run(run,admission_wait=lambda _: pytest.fail('no retry'))


def test_amend_failed_audit_continues_next_trial(tmp_path,monkeypatch):
    run=dry_harness(tmp_path,monkeypatch)
    calls=[]
    def child(job,target,*args):
        calls.append(job['arm'])
        row=dict(map_id=job['map_id'],cohort=job['cohort'],arm=job['arm'],
                 map_sha256=job['sha256'],status='no_checked_plan',passed=False,
                 full_checker_pass=False,plan=None,wall_s=.01)
        common.atomic_json(target/'row.json',row)
        return row
    def audit(job,target):
        if job['arm']=='pipeline':
            raise ValueError('injected fresh audit mismatch')
        return dict(common.read_json(target/'row.json'),audit_verified=True,audit_fresh_process=True)
    monkeypatch.setattr(driver,'run_child',child)
    monkeypatch.setattr(driver,'audit_child',audit)
    result=driver.run(run,admission_wait=lambda _: {'fixture':True})
    assert calls==['pipeline','twostep']
    assert not result['complete']
    rows=common.read_json(run/'rows.json')
    assert rows[0]['harness_error'] and 'audit mismatch' in rows[0]['audit_error']
    assert rows[1]['audit_verified']


def test_amendment_is_frozen_before_any_dispatch(tmp_path,monkeypatch):
    run=dry_harness(tmp_path,monkeypatch)
    frozen=common.read_json(run/'freeze.json')
    assert frozen['metadata'][str(driver.PROTOCOL_HOME/'AMENDMENT.md')]==common.sha256(driver.PROTOCOL_HOME/'AMENDMENT.md')
    assert frozen['metadata'][str(driver.PROTOCOL_HOME/'AMENDMENT.sha256')]


def test_amend_sensitivity_is_envelope_only_and_fixed_300_seconds(tmp_path):
    cfg=dict(mode='dry-run',twostep_budget_s=30,twostep_s=15,twostep_finalize_s=15,
        maps=[dict(map_id='t',path='t',sha256='a',cohort='tiny15'),
              dict(map_id='e',path='e',sha256='b',cohort='envelope24')])
    jobs=list(driver.jobs_for(cfg,tmp_path))
    assert len(jobs)==6
    sensitivity=[j for j in jobs if j['arm']=='twostep_300']
    assert len(sensitivity)==1 and sensitivity[0]['map_id']=='e'
    assert sensitivity[0]['twostep_finalize_s']==300
    assert not any('reader_factory' in j or 'parser_factory' in j for j in jobs)


def test_amend_unmeasured_development_cap_blocks_release():
    from experiments.mvp.pipeline.sealed.calibration import verify_calibration
    with pytest.raises(common.HarnessError,match='awaits audited'):
        verify_calibration(None)


def test_amend_profile_selection_cannot_drift_through_unlisted_runtime_data(tmp_path,monkeypatch):
    from experiments.mvp.pipeline.sealed import budget
    run=dry_harness(tmp_path,monkeypatch)
    identity=common.read_json(run/'freeze.json')
    manifest=tmp_path/'manifest.json'
    common.atomic_json(manifest,dict(tree_sha256='b'*64))
    cfg=common.read_json(run/'config.json')
    cfg.update(mode='sealed',pipeline_manifest=str(manifest))
    common.atomic_json(run/'config.json',cfg)
    identity.update(mode='sealed',standard_profile_sha256='before-selection-change',
        config_sha256=common.sha256(run/'config.json'),pipeline_files={},
        manifest_sha256=common.sha256(manifest),tree_digest='b'*64)
    monkeypatch.setattr(driver,'verify_pipeline_manifest',lambda _: (dict(tree_sha256='b'*64),{}))
    identity['run_id']=common.object_hash({k:v for k,v in identity.items() if k!='run_id'})
    common.atomic_json(run/'freeze.json',identity)
    monkeypatch.setattr(budget,'standard_profile_hash',lambda:'after-selection-change')
    with pytest.raises(common.HarnessError,match='profile changed'):
        driver.verify_freeze(run)


def test_amend_failed_audit_invalid_metric_keeps_raw_and_still_writes_report(tmp_path,monkeypatch):
    run=dry_harness(tmp_path,monkeypatch)
    def child(job,target,*args):
        row=dict(map_id=job['map_id'],cohort=job['cohort'],arm=job['arm'],map_sha256=job['sha256'],
            ship_id='wrong_unverified_ship',status='no_checked_plan',plan=None,passed=False,
            lifts_total=-1,wall_s=.01)
        common.atomic_json(target/'row.json',row)
        return row
    monkeypatch.setattr(driver,'run_child',child)
    monkeypatch.setattr(driver,'audit_child',lambda *args:(_ for _ in ()).throw(ValueError('bad audit data')))
    result=driver.run(run,admission_wait=lambda _: {'fixture':True})
    assert result['rows']==2 and result['provisional']
    rows=common.read_json(run/'rows.json')
    assert all(r['status']=='error' and r['lifts_total'] is None for r in rows)
    assert rows[0]['unverified_metrics']['lifts_total']==-1
    assert common.read_json(run/'jobs/0000-pipeline/row.json')['lifts_total']==-1


def test_amend_measured_profile_configuration_requires_exact_byte_pin(tmp_path,monkeypatch):
    selected=tmp_path/'experiments/mvp/pipeline/runner/fixtures/profile-selection.json'
    selected.parent.mkdir(parents=True)
    selected.write_text('{}\n')
    monkeypatch.setattr(driver,'ROOT',tmp_path)
    monkeypatch.setattr(driver,'SOURCE_ROOTS',())
    assert driver.source_inventory()==[selected]
