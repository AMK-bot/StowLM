"""Controller wiring uses the same cohort validator and recorded admission."""
import importlib.util
import json
from pathlib import Path
import threading

import pytest
from experiments.mvp.hybrid.test_validation import manifest_for,row as sample_row

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hybrid_run_tested',HERE/'run.py')
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def test_fixed_validation_maps_and_output_scope():
    paths = run.inputs()
    assert len(paths) == len(set(paths)) == 40
    assert len(run.validation_inputs()) == 55
    assert len(run.selection()['groups']['entering']) == 32
    assert len(run.selection()['groups']['aa']) == 8
    assert all('/corpus/val/small/' in p for p in paths)
    with pytest.raises(ValueError,match='output must stay'):
        run.contained(HERE.parent/'other')


def job_for(manifest,arm='none'):
    iid,inp = next(iter(manifest['maps'].items()))
    return dict(index=0,arm=arm,instance_id=iid,input=inp,runtime=manifest['runtime'],
                candidate_paths=[],binding=run.expected_binding(manifest,inp))


def test_resume_delegates_to_strict_shared_validator(tmp_path):
    manifest = manifest_for()
    job = job_for(manifest)
    target = tmp_path/'none'/f"{job['instance_id']}.json"
    run.publish(target,sample_row(0,5,'none',manifest))
    assert run.reusable(target,manifest=manifest,job=job)
    broken = json.loads(target.read_text())
    broken['audit']['stored'] = 6
    run.publish(target,broken)
    assert not run.reusable(target,manifest=manifest,job=job)
    (tmp_path/'QUARANTINED.json').write_text('{}')
    with pytest.raises(ValueError,match='QUARANTINED'):
        run.reusable(target,manifest=manifest,job=job)


def test_trial_launches_only_after_admission_and_failed_audit_stops_queue(tmp_path,monkeypatch):
    manifest = manifest_for()
    job = job_for(manifest)
    calls = []
    admission = sample_row(0,5,'none',manifest)['admission']
    def admit(**kwargs):
        calls.append('admit')
        return admission
    def launch(out,env,mode,jobpath,logfile,job,started=None):
        calls.append(mode)
        assert calls[0] == 'admit'
        data = json.loads(jobpath.read_text())
        target = Path(data['target'])
        if mode == 'plan':
            run.publish(target,sample_row(0,5,'none',manifest))
        else:
            run.publish(target.with_suffix('.audit.json'),dict(passed=False,fresh_process=True))
        if started:
            started.set()
        return dict(returncode=0,timed_out=False)
    monkeypatch.setattr(run,'wait_for_admission',admit)
    monkeypatch.setattr(run,'launch',launch)
    monkeypatch.setattr(run,'verify_seal',lambda value:True)
    monkeypatch.setattr(run,'sha',lambda path:'a'*64)
    monkeypatch.setattr(run,'event',lambda *a,**k:None)
    began = threading.Event()
    assert run.trial(job,tmp_path,{},manifest,began) is False
    assert calls == ['admit','plan','audit'] and began.is_set()
    result = json.loads((tmp_path/'none'/f"{job['instance_id']}.json").read_text())
    assert result['admission'] == admission and result['core_mask_start'] == admission['core_mask']
    assert (tmp_path/'STOP').exists()


def test_stop_never_publishes_jobs_or_launches(tmp_path,monkeypatch):
    (tmp_path/'STOP').touch()
    monkeypatch.setattr(run,'launch',lambda *a:pytest.fail('started after STOP'))
    event = threading.Event()
    assert run.trial({},tmp_path,{},manifest={},started=event) == 'stopped'
    assert event.is_set() and not (tmp_path/'jobs').exists()


def test_hold_never_publishes_jobs_or_launches(tmp_path,monkeypatch):
    (tmp_path/'HOLD').touch()
    monkeypatch.setattr(run,'launch',lambda *a:pytest.fail('started during HOLD'))
    event = threading.Event()
    assert run.trial({},tmp_path,{},manifest={},started=event) == 'stopped'
    assert event.is_set() and not (tmp_path/'jobs').exists()


def test_default_only_arms_and_never_constructs_executor(tmp_path,monkeypatch):
    manifest = manifest_for()
    out = tmp_path/'held'
    monkeypatch.setattr(run,'OUTPUT_ROOT',tmp_path)
    monkeypatch.setattr(run.sys,'argv',['run.py','--out',str(out)])
    monkeypatch.setattr(run,'build_manifest',lambda *args:manifest)
    monkeypatch.setattr(run,'ThreadPoolExecutor',lambda *args:pytest.fail('armed a live queue'))
    monkeypatch.setattr(run,'environment',lambda *args:pytest.fail('prepared trial environment while held'))
    run.main()
    assert (out/'HOLD').exists()
    assert json.loads((out/'ARMED.json').read_text())['state'].startswith('held')
    assert not list((out/'jobs').glob('*.json'))


def test_event_accepts_probe_timestamp(tmp_path):
    run.event(tmp_path,'admission_probe',utc='probe-time',load_1m=40,core_mask=None,allowed=False)
    evidence = json.loads((tmp_path/'events.jsonl').read_text())
    assert evidence['utc'] == 'probe-time' and evidence['allowed'] is False


def test_readonly_legacy_job_directory_is_rejected_before_launch(tmp_path,monkeypatch):
    out = tmp_path/'legacy'
    jobs = out/'jobs'
    jobs.mkdir(parents=True)
    jobs.chmod(0o555)
    monkeypatch.setattr(run,'OUTPUT_ROOT',tmp_path)
    monkeypatch.setattr(run.sys,'argv',['run.py','--out',str(out),'--workers','4'])
    monkeypatch.setattr(run,'build_manifest',lambda *a:pytest.fail('read-only jobs reached cohort launch'))
    try:
        with pytest.raises(ValueError,match='read-only'):
            run.main()
    finally:
        jobs.chmod(0o755)
