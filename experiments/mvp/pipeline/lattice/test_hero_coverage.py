from dataclasses import dataclass
import json
from pathlib import Path
import signal

import pytest

from . import hero_coverage as coverage


class Process:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None

    def poll(self):
        return self.returncode


def setup_campaign(tmp_path, monkeypatch, count=9):
    manifest = dict(instance_sha256='hero-only', run_dir=str(tmp_path/'runs'),
        instructions=[dict(id=f'rule-{i}', key=f'key-{i}', purpose='lattice', rules=[], sentences=[f'Rule {i}']) for i in range(count)])
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(coverage, 'update_report', lambda directory: {})
    processes = []

    def spawn(job, log):
        process = Process(1000+len(processes))
        processes.append((process, Path(job)))
        return process

    scheduler = coverage.Scheduler(tmp_path, spawn=spawn, reading=lambda: {'load1': 10},
        alive=lambda pid: True, quiet=tmp_path/'quiet.flag', clock=lambda: 0)
    return scheduler, processes


def test_maximum_six_owned_workers_and_quiet_blocks_new_admissions(tmp_path, monkeypatch):
    scheduler, processes = setup_campaign(tmp_path, monkeypatch)
    assert scheduler.tick() == 'running'
    assert len(processes) == len(scheduler.state['active']) == 6
    (tmp_path/'quiet.flag').touch()
    process, job = processes[0]
    job.with_name('result.json').write_text(json.dumps(dict(id='rule-0', key='key-0', status='checked_plan')))
    process.returncode = 0
    assert scheduler.tick() == 'quiet'
    assert len(processes) == 6 and len(scheduler.state['active']) == 5
    (tmp_path/'quiet.flag').unlink()
    scheduler.tick()
    assert len(processes) == 7 and len(scheduler.state['active']) == 6


def test_stop_drains_exact_active_workers_without_signals_or_new_rows(tmp_path, monkeypatch):
    scheduler, processes = setup_campaign(tmp_path, monkeypatch)
    scheduler.tick()
    (tmp_path/'STOP').touch()
    monkeypatch.setattr(coverage, 'signal_owned', lambda *args: pytest.fail('STOP must never signal'))
    assert scheduler.tick() == 'draining'
    for process, job in processes:
        payload = json.loads(job.read_text())
        job.with_name('result.json').write_text(json.dumps(dict(key=payload['spec']['key'], id=payload['spec']['id'], status='gate_failed')))
        process.returncode = 0
    assert scheduler.tick() == 'stopped'
    assert len(processes) == 6 and len(scheduler.state['completed']) == 6


def test_resume_reserves_inherited_live_pids_and_never_repeats_completed_keys(tmp_path, monkeypatch):
    scheduler, processes = setup_campaign(tmp_path, monkeypatch, count=7)
    scheduler.tick()
    first, job = processes[0]
    job.with_name('result.json').write_text(json.dumps(dict(key='key-0', id='rule-0', status='proved_refusal')))
    first.returncode = 0
    scheduler.tick()
    resumed = coverage.Scheduler(tmp_path, spawn=scheduler.spawn, reading=lambda: {'load1': 10}, alive=lambda pid: True, quiet=tmp_path/'quiet.flag')
    resumed.tick()
    assert len(processes) == 7
    assert 'key-0' in resumed.state['completed']
    assert len(resumed.state['active']) == 6


def test_waiting_first_admission_rechecks_quiet_after_sleep_and_before_return(tmp_path):
    quiet = tmp_path/'quiet.flag'
    readings = iter([None, {'load1': 10}])
    with pytest.raises(coverage.AdmissionDeferred, match='quiet'):
        coverage.first_admission(tmp_path, quiet=quiet, reading=lambda: next(readings), sleep=lambda seconds: quiet.touch(), clock=lambda: 0)
    quiet.unlink()

    def reading():
        quiet.touch()
        return {'load1': 10}

    with pytest.raises(coverage.AdmissionDeferred, match='quiet'):
        coverage.first_admission(tmp_path, quiet=quiet, reading=reading, clock=lambda: 0)


def test_bad_fleet_telemetry_waits_without_starting_workers(tmp_path, monkeypatch):
    scheduler, processes = setup_campaign(tmp_path, monkeypatch)
    scheduler.reading = lambda: None
    assert scheduler.tick() == 'waiting_admission'
    assert not processes


def test_deferred_worker_requeues_without_becoming_a_failed_or_impossible_result(tmp_path, monkeypatch):
    scheduler, processes = setup_campaign(tmp_path, monkeypatch, count=1)
    scheduler.tick()
    process, job = processes[0]
    job.with_name('result.json').write_text(json.dumps(dict(key='key-0', id='rule-0', status='deferred', no_engine_started=True)))
    process.returncode = 0
    (tmp_path/'quiet.flag').touch()
    scheduler.tick()
    assert scheduler.state['completed'] == {}
    assert scheduler.state['queued'] == ['rule-0']


def test_owned_group_signal_denial_and_missing_group_are_tolerated(monkeypatch):
    for error in (PermissionError(), ProcessLookupError()):
        def denied(pid, chosen):
            raise error
        monkeypatch.setattr(coverage.os, 'killpg', denied)
        assert coverage.signal_owned(Process(123), signal.SIGTERM) is False


def test_selected_profile_requires_independent_1800_wall(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.runner import profiles

    @dataclass
    class Config:
        budget: int = 1

    calibration = tmp_path/'profile.json'
    calibration.write_text('{}')
    raw = dict(budget_s=300., finalize_s=100., engine_timeout_s=1800., config=Config(), seed=0, name='standard')
    manifest = dict(profile=dict(raw, config={'budget': 1}), profile_selection_path=str(calibration),
                    profile_selection_sha256=coverage.sha256_file(calibration))
    monkeypatch.setattr(profiles, 'get_profile', lambda name: raw)
    coverage.profile_matches(manifest)
    raw['engine_timeout_s'] = 900.
    with pytest.raises(ValueError, match='hard wall1800'):
        coverage.profile_matches(manifest)


def test_spawn_and_launch_detach_with_only_the_exact_recorded_process(tmp_path, monkeypatch):
    (tmp_path/'manifest.json').write_text('{}')
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process(456)

    monkeypatch.setattr(coverage.subprocess, 'Popen', popen)
    receipt = coverage.launch(tmp_path)
    coverage.spawn_worker(tmp_path/'job.json', tmp_path/'worker.log')
    assert receipt['pid'] == 456
    assert all(kwargs['start_new_session'] is True for _, kwargs in calls)
    assert '--run' in calls[0][0] and '--worker' in calls[1][0]
    assert receipt['stop_command'] == f'touch {tmp_path / "STOP"}'


def test_report_counts_publication_receipts_not_staged_gate_acceptance(tmp_path):
    run_dir = tmp_path/'runs'
    run_dir.mkdir()
    spec = dict(id='rule', key='key', purpose='lattice', sentences=['Rule'])
    catalogue = tmp_path/'main-catalogue.json'
    catalogue.write_text(json.dumps({'key': 'pinned-publication'}))
    (tmp_path/'manifest.json').write_text(json.dumps(dict(instance_sha256='hero', run_dir=str(run_dir), instructions=[spec], existing_catalogue_path=str(catalogue))))
    (run_dir/'key.json').write_text(json.dumps(dict(spec, status='checked_plan')))
    (tmp_path/'state.json').write_text(json.dumps(dict(pid=123, status='running', active=[dict(pid=456, key='key')], queued=[], completed={})))
    data = coverage.update_report(tmp_path)
    assert data['staged_checked'] == 1 and data['stored'] == data['published'] == 0
    assert data['supervisor_pid'] == 123 and data['active'][0]['pid'] == 456
    publications = tmp_path/'publications'
    publications.mkdir()
    receipt = publications/'key.json'
    receipt.write_text(json.dumps(dict(spec, status='checked_plan', publication={'status': 'rejected'})))
    assert coverage.update_report(tmp_path)['stored'] == 0
    receipt.write_text(json.dumps(dict(spec, status='checked_plan', publication={'status': 'published', 'added_to_catalogue': True, 'entry_sha256': 'pinned-publication'})))
    assert coverage.update_report(tmp_path)['stored'] == 1
    catalogue.write_text('{}')
    assert coverage.update_report(tmp_path)['stored'] == 0


def worker_job(tmp_path):
    manifest = dict(instance_path='instance', base_plan={}, run_dir=str(tmp_path/'runs'),
                    store_dir=str(tmp_path/'staged-store'), fixtures_dir=str(tmp_path/'fixtures'), state_path='fleet',
                    source_identity={'source_identity_sha256': 'source'}, profile={'name': 'standard'},
                    publish_store_dir=str(tmp_path/'main-store'))
    path = tmp_path/'manifest.json'
    path.write_text(json.dumps(manifest))
    job = tmp_path/'job.json'
    job.write_text(json.dumps(dict(manifest_path=str(path), manifest_sha256=coverage.sha256_file(path),
        spec={'id': 'rule', 'key': 'expected'}, attempt='attempt', quiet_path=str(tmp_path/'quiet.flag'))))
    return job


def test_worker_rejects_wrong_result_key_and_restores_process_hooks(tmp_path, monkeypatch):
    job = worker_job(tmp_path)
    monkeypatch.setattr(coverage, 'profile_matches', lambda manifest: None)
    monkeypatch.setattr(coverage, 'validate_job_identity', lambda manifest, spec: None)
    monkeypatch.setattr(coverage, 'capture_source_identity', lambda: {'source_identity_sha256': 'source'})
    monkeypatch.setattr(coverage.batch, 'capacity_reading', lambda state: {'load1': 10})
    monkeypatch.setattr(coverage, 'update_report', lambda directory: {})
    monkeypatch.setattr(coverage.batch, 'compute', lambda job: dict(key='foreign', status='checked_plan'))
    original = coverage.batch.admit, coverage.batch.refresh_progress
    result = coverage.run_worker(job)
    assert result['status'] == 'worker_error' and 'key differs' in result['reason']
    assert (coverage.batch.admit, coverage.batch.refresh_progress) == original
    assert not (tmp_path/'publications').exists()


def test_running_release_admissions_finish_after_quiet_and_restore_hooks(tmp_path, monkeypatch):
    job = worker_job(tmp_path)
    monkeypatch.setattr(coverage, 'profile_matches', lambda manifest: None)
    monkeypatch.setattr(coverage, 'validate_job_identity', lambda manifest, spec: None)
    monkeypatch.setattr(coverage, 'capture_source_identity', lambda: {'source_identity_sha256': 'source'})
    monkeypatch.setattr(coverage, 'update_report', lambda directory: {})
    monkeypatch.setattr(coverage.batch, 'capacity_reading', lambda state: dict(load1=10))
    original_calls = []
    monkeypatch.setattr(coverage.batch, 'admit', lambda *args, **kwargs: original_calls.append('release') or {'load1': 11})
    original = coverage.batch.admit, coverage.batch.refresh_progress

    def compute(job):
        assert coverage.batch.admit('fleet')['load1'] == 10
        (tmp_path/'quiet.flag').touch()
        assert coverage.batch.admit('fleet')['load1'] == 11
        assert 'campaign_id' not in job and job['store_dir'] == str(tmp_path/'staged-store')
        return dict(key='expected', status='gate_failed')

    monkeypatch.setattr(coverage.batch, 'compute', compute)
    assert coverage.run_worker(job)['status'] == 'gate_failed'
    assert original_calls == ['release']
    assert (coverage.batch.admit, coverage.batch.refresh_progress) == original


def test_manifest_and_instance_baseline_pins_reject_mutation_before_compute(tmp_path, monkeypatch):
    job = worker_job(tmp_path)
    (tmp_path/'manifest.json').write_text('{}')
    monkeypatch.setattr(coverage, 'update_report', lambda directory: {})
    monkeypatch.setattr(coverage.batch, 'compute', lambda job: pytest.fail('must not compute changed manifest'))
    assert 'manifest changed' in coverage.run_worker(job)['reason']


def test_launch_refuses_a_live_recorded_supervisor(tmp_path, monkeypatch):
    (tmp_path/'manifest.json').write_text('{}')
    (tmp_path/'launch.json').write_text(json.dumps({'pid': 123}))
    monkeypatch.setattr(coverage, 'pid_alive', lambda pid: True)
    monkeypatch.setattr(coverage.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('duplicate launch'))
    with pytest.raises(RuntimeError, match='still alive'):
        coverage.launch(tmp_path)


@pytest.mark.parametrize('mutation', ['instance', 'base', 'key', 'source'])
def test_instance_baseline_key_and_source_pins_are_checked_before_admission(tmp_path, monkeypatch, mutation):
    from types import SimpleNamespace
    from stowage import io
    from .store import entry_key
    instance = {'id': 'hero'}
    monkeypatch.setattr(io, 'load_instance', lambda path: SimpleNamespace(model_dump=lambda **kwargs: instance))
    manifest = dict(instance_path='unused', instance_sha256=coverage.sha256_json(instance),
        base_plan={'id': 'base', 'instance_id': 'hero'})
    manifest['base_plan_sha256'] = coverage.sha256_json(manifest['base_plan'])
    spec = dict(rules=[], key=entry_key(manifest['instance_sha256'], []))
    coverage.validate_job_identity(manifest, spec)
    if mutation == 'instance':
        instance['id'] = 'foreign'
    elif mutation == 'base':
        manifest['base_plan']['id'] = 'changed'
    elif mutation == 'key':
        spec['key'] = 'foreign'
    else:
        manifest['source_identity'] = {'gate_source_files': {'experiments/mvp/pipeline/lattice/hero_coverage.py': 'stale'}}
    with pytest.raises(ValueError, match='changed'):
        coverage.validate_job_identity(manifest, spec)


def test_quiet_transition_refreshes_progress_once_without_polling_all_rows_each_tick(tmp_path, monkeypatch):
    scheduler, _ = setup_campaign(tmp_path, monkeypatch, count=1)
    reports = []
    monkeypatch.setattr(coverage, 'update_report', lambda directory: reports.append(scheduler.state['status']))
    scheduler.tick()
    # Mimic the report artifact the real reporter writes.
    (tmp_path/'progress.json').write_text('{}')
    reports.clear()
    scheduler.tick()
    assert reports == []
    (tmp_path/'quiet.flag').touch()
    scheduler.tick()
    assert reports == ['quiet']
    scheduler.tick()
    assert reports == ['quiet']


def test_worker_publication_receives_actual_before_and_after_identity(tmp_path, monkeypatch):
    from importlib import import_module
    job = worker_job(tmp_path)
    monkeypatch.setattr(coverage, 'profile_matches', lambda manifest: None)
    monkeypatch.setattr(coverage, 'validate_job_identity', lambda manifest, spec: None)
    monkeypatch.setattr(coverage, 'update_report', lambda directory: {})
    monkeypatch.setattr(coverage.batch, 'capacity_reading', lambda state: {'load1': 10})
    timeline = []
    def identity():
        timeline.append('identity')
        return {'source_identity_sha256': 'source'}
    monkeypatch.setattr(coverage, 'capture_source_identity', identity)
    def compute(job):
        timeline.append('compute')
        return dict(key='expected', status='checked_plan')
    monkeypatch.setattr(coverage.batch, 'compute', compute)
    def publish(key, **kwargs):
        timeline.append('publish')
        assert key == 'expected'
        assert kwargs['coverage_start_identity'] == kwargs['coverage_end_identity'] == {'source_identity_sha256': 'source'}
        assert kwargs['staging_store'] == str(tmp_path/'staged-store')
        assert kwargs['store_dir'] == str(tmp_path/'main-store')
        assert kwargs['run_dir'] == str(tmp_path/'runs')
        return dict(status='published', key=key, added_to_catalogue=True, entry_sha256='pin')
    monkeypatch.setattr(import_module(coverage.__package__+'.hero_coverage_publish'), 'publish_staged', publish)
    result = coverage.run_worker(job)
    assert result['status'] == 'checked_plan', result.get('reason')
    assert timeline == ['identity', 'compute', 'identity', 'publish']
    assert json.loads((tmp_path/'publications/expected.json').read_text())['publication']['entry_sha256'] == 'pin'


def test_first_admission_measures_wait_with_a_fake_clock(tmp_path):
    times = iter([10., 15.])
    values = iter([None, {'load1': 20}])
    admitted = coverage.first_admission(tmp_path, quiet=tmp_path/'quiet', reading=lambda: next(values),
        sleep=lambda seconds: None, clock=lambda: next(times))
    assert admitted['waited_s'] == 5 and admitted['load1'] == 20


@pytest.mark.parametrize('kind,proved,expected', [('proved_full_load', True, True),
    ('solver_timeout', True, False), (None, True, False), ('proved_full_load', False, False)])
def test_only_explicit_hatch_refusal_with_fresh_proof_can_be_promoted(tmp_path, monkeypatch, kind, proved, expected):
    from importlib import import_module
    from types import SimpleNamespace
    from stowage import io
    from . import store
    instance = {'id': 'hero'}
    monkeypatch.setattr(io, 'load_instance', lambda path: SimpleNamespace(model_dump=lambda **kwargs: instance))
    rule = {'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'one_port_per_hatch'}}
    digest = coverage.sha256_json(instance)
    key = store.entry_key(digest, [rule])
    run_dir = tmp_path/'runs'
    run_dir.mkdir()
    profile = dict(name='standard', budget_s=300., finalize_s=100., engine_timeout_s=1800.)
    manifest = dict(run_dir=str(run_dir), store_dir=str(tmp_path/'staging-store'),
        instance_path='unused', instance_sha256=digest, profile=profile)
    spec = dict(key=key, rules=[rule])
    row = dict(key=key, status='budget_failed', canonical_rules=[rule], instance_sha256=digest,
        binding_source_sha256='binding', binding_source_after_sha256='binding', engine_version='0.13.0',
        engine_manifest_sha256='engine', profile=profile,
        binding_evidence=dict(status='constraint_unavailable', refusal_kind=kind))
    target = run_dir/(key+'.json')
    target.write_text(json.dumps(row))
    raw = target.read_bytes()
    identity = dict(binding_source_sha256='binding', engine_version='0.13.0', engine_manifest_sha256='engine')
    monkeypatch.setattr(coverage, 'capture_source_identity', lambda: identity)
    publisher = import_module(coverage.__package__+'.hero_coverage_publish')
    proof_calls = []
    def proof(candidate):
        proof_calls.append(candidate)
        if not proved:
            raise ValueError('UNKNOWN is not a proof')
        return dict(necessary_proof={'proven_impossible': True, 'needed': 10, 'open': 0})
    monkeypatch.setattr(publisher, '_refusal_evidence', proof)
    stored = []
    # The production helper uses its package-local store module.
    monkeypatch.setattr(import_module(coverage.__package__+'.store'), 'save_entry',
        lambda candidate, **kwargs: stored.append((candidate, kwargs)))
    result = coverage.normalize_proved_refusal(manifest, spec, identity, identity)
    assert bool(result) is expected and bool(stored) is expected
    if expected:
        assert json.loads(target.read_text())['status'] == 'proved_refusal'
        assert Path(result['original_run_path']).read_bytes() == raw
        assert stored[0][1]['store_dir'] == str(tmp_path/'staging-store')
        assert stored[0][0]['instance'] == instance
    else:
        assert target.read_bytes() == raw
        if kind != 'proved_full_load':
            assert not proof_calls


@pytest.mark.parametrize('flag', ['quiet.flag', 'STOP'])
def test_cached_success_cannot_bypass_quiet_or_stop_before_first_admission(tmp_path, monkeypatch, flag):
    job = worker_job(tmp_path)
    monkeypatch.setattr(coverage, 'profile_matches', lambda manifest: None)
    monkeypatch.setattr(coverage, 'validate_job_identity', lambda manifest, spec: None)
    monkeypatch.setattr(coverage, 'capture_source_identity', lambda: {'source_identity_sha256': 'source'})
    monkeypatch.setattr(coverage, 'update_report', lambda directory: {})
    monkeypatch.setattr(coverage.batch, 'capacity_reading', lambda state: {'load1': 10})
    monkeypatch.setattr(coverage.batch, 'compute', lambda job: pytest.fail('cached compute must wait for quiet-aware admission'))
    monkeypatch.setattr(coverage, 'normalize_proved_refusal', lambda *args: pytest.fail('no proof before admission'))
    (tmp_path/flag).touch()
    result = coverage.run_worker(job)
    assert result['status'] == 'deferred' and result['no_engine_started'] is True
    assert not (tmp_path/'publications').exists()
