"""Regressions for v6/code3, using synthetic metadata only."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.sealed import driver, worker
from experiments.mvp.pipeline.sealed.common import atomic_json


@pytest.fixture
def active_run(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    run.mkdir()
    sealed = tmp_path / 'unopened'
    claim = tmp_path / 'CLAIM.json'
    monkeypatch.setattr(driver, 'V3', sealed)
    monkeypatch.setattr(driver, 'GLOBAL_CLAIM', claim)
    monkeypatch.setattr(driver, 'verify_freeze', lambda _: dict(run_id='fixed', mode='sealed'))
    item = dict(map_id='m', cohort='tiny15', path=str(sealed/'missing.json'), sha256='a'*64)
    atomic_json(claim, dict(run_id='fixed', out_dir=str(run)))
    atomic_json(run/'STARTED.json', dict(run_id='fixed'))
    atomic_json(run/'config.json', dict(mode='sealed', maps=[item]))
    lock = (run/'controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield run, dict(item, arm='pipeline', mode='sealed', run_dir=str(run),
                        open_log=str(run/'opened-maps.jsonl'))
    finally:
        lock.close()


@pytest.mark.parametrize('terminal', ['FAILED.json', 'INTERRUPTED.json'])
def test_review1_failed_worker_replay_cannot_reopen(active_run, terminal):
    run, job = active_run
    atomic_json(run/terminal, {})
    with pytest.raises(ValueError, match='terminal|active matching'):
        worker._authorize_open(job, Path(job['path']))


def test_review1_concurrent_duplicate_planning_is_consumed_once(active_run):
    run, job = active_run
    # Issue the expected future receipt manually so the old guard sees the
    # identical already-dispatched request, rather than failing on a missing API.
    binding = dict(run_id='fixed', map_id='m', arm='pipeline', phase='plan', job_id='j')
    from experiments.mvp.pipeline.sealed.common import object_hash
    key = object_hash({k: binding[k] for k in ('run_id', 'map_id', 'arm', 'phase')})
    job = dict(job, authorization=dict(binding, authorization_id=key))
    atomic_json(run/'authorizations'/f'{key}.json', dict(binding,
        authorization_id=key, payload_sha256=object_hash({k:v for k,v in job.items() if k != 'authorization'})))
    def attempt(_):
        try:
            worker._authorize_open(job, Path(job['path']))
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1


def test_review1_phase_and_payload_cannot_turn_reads_into_planning(active_run):
    from experiments.mvp.pipeline.sealed.authorization import issue
    run, job = active_run
    prepass = issue(job, phase='prepass', run_id='fixed', job_id='read')
    with pytest.raises(ValueError, match='phase'):
        worker._authorize_open(prepass, Path(job['path']))
    worker._authorize_open(prepass, Path(job['path']), phase='prepass')
    plan = issue(job, phase='plan', run_id='fixed', job_id='plan')
    with pytest.raises(ValueError, match='payload'):
        worker._authorize_open(dict(plan, seed=99), Path(job['path']))
    worker._authorize_open(plan, Path(job['path']))
    audit = issue(job, phase='audit', run_id='fixed', job_id='audit')
    worker._authorize_open(audit, Path(job['path']), phase='audit')
    with pytest.raises(ValueError, match='consumed'):
        worker._authorize_open(plan, Path(job['path']))
    with pytest.raises(driver.HarnessError, match='already exists'):
        issue(job, phase='plan', run_id='fixed', job_id='different-id')


def test_review1_direct_run_job_retry_never_repeats_search(active_run, monkeypatch):
    from types import SimpleNamespace
    import stowage.io
    import stowage.model
    from experiments.mvp.pipeline.sealed.authorization import issue
    from experiments.mvp.pipeline.sealed.common import sha256
    run, job = active_run
    path = Path(job['path'])
    path.parent.mkdir()
    path.write_text('{}')
    job['sha256'] = sha256(path)
    atomic_json(run/'config.json', dict(mode='sealed', maps=[job]))
    monkeypatch.setattr(stowage.io, '_check_version', lambda _: None)
    monkeypatch.setattr(stowage.model.Instance, 'model_validate', lambda _: SimpleNamespace(id='m'))
    monkeypatch.setattr(worker, '_gate', lambda _: dict(in_envelope=True))
    calls = []
    monkeypatch.setattr(worker, '_pipeline', lambda *args: calls.append(1) or
                        dict(status='no_checked_plan', plan=None))
    request = issue(job, phase='plan', run_id='fixed', job_id='trial')
    first = worker.run_job(request, str(run/'first'), parser=object())
    second = worker.run_job(request, str(run/'retry'), parser=object())
    assert first['status'] == 'no_checked_plan'
    assert second['status'] == 'error'
    assert 'consumed' in second['error']
    assert calls == [1]


def test_review5_prepass_keeps_unavailable_fixed_types(tmp_path, monkeypatch):
    maps = [dict(map_id=f'm{i}', path=f'not-read-{i}', sha256='a'*64, cohort='envelope24')
            for i in range(4)]
    monkeypatch.setattr(worker, '_load', lambda job, **kw: job['map_id'])
    monkeypatch.setattr(worker, 'public_facts', lambda value: dict(fact=value))
    def unsupported(*args):
        raise ValueError('instruction draw needs a public physical bay')
    monkeypatch.setattr(worker, 'select_rule', unsupported)
    prepared = worker.prepare_instructions(maps, tmp_path/'prepass', tmp_path/'opened.jsonl')
    assert len(prepared) == 4
    assert [m['instruction_type'] for m in prepared] == [
        'close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch']
    assert all(m['expected_rule'] is None and 'physical bay' in m['instruction_unavailable'] for m in prepared)
    assert len(json.loads((tmp_path/'prepass/instruction-draw.json').read_text())) == 4


def dry_harness(tmp_path, monkeypatch, *, envelope=False, workers=1):
    here = tmp_path/'harness'
    here.mkdir()
    source = here/'source.py'
    source.write_text('pass\n')
    protocol = dict(required_arms=['pipeline', 'twostep'], metrics=['L0'], instruction_selection={'fixed': True})
    atomic_json(here/'preregistration.json', protocol)
    (here/'PREREG.md').write_text('Fixed protocol.')
    path = tmp_path/'development.json'
    path.write_text('{}')
    from experiments.mvp.pipeline.sealed.common import sha256
    monkeypatch.setattr(driver, 'HERE', here)
    monkeypatch.setattr(driver, 'source_inventory', lambda: [source])
    config = dict(mode='dry-run', workers=workers, stubbed=['test'],
        preregistration=str(here/'preregistration.json'), maps=[dict(map_id='dev', path=str(path),
            sha256=sha256(path), cohort='envelope24' if envelope else 'tiny15')])
    run = here/'run'
    driver.freeze(config, run)
    return run


def test_review5_failed_prepass_retains_instruction_errors_and_continues(tmp_path, monkeypatch):
    run = dry_harness(tmp_path, monkeypatch, envelope=True)
    def broken(*args, **kwargs):
        raise RuntimeError('prepass process stopped')
    monkeypatch.setattr(driver, 'run_child', broken)
    driver.run(run, admission_wait=lambda _: {'synthetic': True})
    assert (run/'FAILED.json').exists()
    report = json.loads((run/'report.json').read_text())
    assert report['rows'] == report['expected_rows'] == 4
    assert not report['sealed_claim_eligible']
    rows = json.loads((run/'rows.json').read_text())
    assert all(not row['passed'] for row in rows)
    assert 'prepass' in next(row for row in rows if row['arm']=='instruction')['reason']


@pytest.mark.parametrize('failure', ['audit', 'worker_timeout', 'worker_schema'])
def test_review6_failed_and_other_completed_jobs_stay_consolidated(tmp_path, monkeypatch, failure):
    from threading import Barrier
    import subprocess
    run = dry_harness(tmp_path, monkeypatch, workers=2)
    barrier = Barrier(2)
    calls = []
    def child(job, target, *args, **kwargs):
        calls.append(job['arm'])
        barrier.wait(timeout=5)
        if job['arm'] == 'pipeline' and failure == 'worker_timeout':
            raise subprocess.TimeoutExpired(['synthetic-child'], 1)
        row = dict(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
            map_sha256=job['sha256'], status='no_checked_plan', passed=False,
            full_checker_pass=False, plan=None, level=None, wall_s=.01)
        if job['arm'] == 'pipeline' and failure == 'worker_schema':
            row.update(status='error', error='map schema invalid')
        atomic_json(target/'row.json', row)
        return row
    def audit(job, target):
        if job['arm'] == 'pipeline':
            raise ValueError('same load error' if failure == 'worker_schema' else 'fresh audit failed')
        return dict(json.loads((target/'row.json').read_text()), audit_verified=True, audit_fresh_process=True)
    monkeypatch.setattr(driver, 'run_child', child)
    monkeypatch.setattr(driver, 'audit_child', audit)
    driver.run(run, admission_wait=lambda _: {'synthetic': True})
    rows = {row['arm']: row for row in json.loads((run/'rows.json').read_text())}
    assert len(rows) == 2
    assert rows['pipeline']['status'] == 'error'
    assert rows['pipeline']['dispatched'] is True
    assert rows['pipeline']['audit_verified'] is False
    assert rows['twostep']['status'] == 'no_checked_plan'
    assert rows['twostep']['audit_verified'] is True
    assert sorted(calls) == ['pipeline', 'twostep']
    summary = json.loads((run/'report.json').read_text())
    assert summary['rows'] == summary['expected_rows'] == 2
    assert not summary['sealed_claim_eligible']
    assert not summary['complete']
    assert (run/'FAILED.json').exists() and not (run/'COMPLETE.json').exists()
    if failure == 'worker_schema':
        assert rows['pipeline']['error'] == 'map schema invalid'
        assert 'same load error' in rows['pipeline']['audit_error']
    if failure != 'worker_timeout':
        raw = json.loads((run/'jobs/0000-pipeline/row.json').read_text())
        assert raw['status'] == ('error' if failure == 'worker_schema' else 'no_checked_plan')


def test_budget_rejects_oversubscribed_worker_count():
    protocol = json.loads((driver.HERE/'preregistration.json').read_text())
    with pytest.raises(driver.HarnessError, match='workers'):
        driver.validate_config(dict(mode='dry-run', workers=17), protocol)


def test_budget_registers_loaded_projection_without_calling_it_clean_machine_timing():
    protocol = json.loads((driver.HERE/'preregistration.json').read_text())
    budget = protocol['execution']['runtime_budget']
    assert budget['status'] == 'd276_44_row_projection_authorized'
    assert budget['workers'] == 4 and budget['estimated_hours'] is None
    assert budget['calibration_gate_unchanged'] is True
    assert budget['study_complete_required'] is False
    assert budget['cpu_cores'] == 18 and budget['cpu_cores_with_gpu'] == 12
    assert budget['admission_load_limit'] == 'workers+2'
    assert '59.56' not in protocol['execution']['runtime_projection']


def test_review5_unavailable_instruction_is_no_plan_and_independently_checked(tmp_path, monkeypatch):
    from types import SimpleNamespace
    request = dict(map_id='m', cohort='envelope24', path='unused', sha256='a'*64,
        arm='instruction', mode='dry-run', cohort_index=0, expected_rule=None,
        instruction_type='close_bay', instruction_unavailable='ValueError: no geometry')
    monkeypatch.setattr(worker, '_load', lambda *args, **kw: SimpleNamespace(id='m'))
    monkeypatch.setattr(worker, '_pipeline', lambda *args: pytest.fail('unavailable condition planned'))
    def unsupported(*args):
        raise ValueError('no geometry')
    monkeypatch.setattr(worker, 'select_rule', unsupported)
    row = worker.run_job(request, str(tmp_path/'raw'))
    assert row['status'] == 'constraint_unavailable' and row['plan'] is None
    row['planning_pid'] += 1
    atomic_json(tmp_path/'raw/row.json', row)
    audited = worker.audit_job(dict(request, target=str(tmp_path/'raw/row.json')))
    assert audited['audit_verified'] and not audited['passed']


def test_review6_audit_child_keeps_raw_on_audit_process_failure(tmp_path, monkeypatch):
    job = dict(map_id='m', cohort='tiny15', arm='pipeline', sha256='a'*64, mode='dry-run')
    raw = dict(job, map_sha256=job['sha256'], status='no_checked_plan', passed=False,
               plan=None, error='original schema error')
    atomic_json(tmp_path/'row.json', raw)
    monkeypatch.setattr(driver, '_audit_child', lambda *args: (_ for _ in ()).throw(ValueError('audit crashed')))
    row = driver.audit_child(job, tmp_path)
    assert row['status'] == 'error' and not row['audit_verified']
    assert row['error'] == 'original schema error'
    assert 'audit crashed' in row['audit_error']
    assert json.loads((tmp_path/'row.json').read_text()) == raw


def test_budget_scales_measured_batch_wall_time_without_dividing_twice(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.sealed import budget
    monkeypatch.setattr(budget, 'standard_profile_hash', lambda: 'profile')
    monkeypatch.setattr(budget, 'sha256', lambda _: 'binding')
    receipt = dict(schema_version='sealed-runtime-budget/1', split='development',
        fixture_surrogate=False, workers=2, admission_load_limit=4,
        no_other_fleet_work=True, includes_serial_readings=True, sensitivity_finalize_s=300,
        includes_admission_and_audits=True, gpu_running=True, cpu_cores=12,
        profile_sha256='profile', binding_sha256='binding', twostep_s=15, twostep_finalize_s=15,
        cohorts={name: dict(maps=n, elapsed_s=seconds, all_registered_arms=True)
                 for name,n,seconds in [('tiny15',3,60),('small30',3,120),('envelope24',4,240)]})
    path=tmp_path/'measurement.json'
    atomic_json(path, receipt)
    config = dict(workers=2, twostep_s=15, twostep_finalize_s=15, runtime_measurement=str(path))
    result = budget.measured_budget(config)
    assert result['workers'] == 2
    assert result['estimated_hours'] == pytest.approx((300+1200+1440)/3600)
    atomic_json(path, dict(receipt, fixture_surrogate=True))
    with pytest.raises(budget.HarnessError, match='real admitted'):
        budget.measured_budget(config)
    with pytest.raises(budget.HarnessError, match='awaits binding'):
        budget.measured_budget(dict(workers=2))


def test_review6_pending_jobs_continue_after_individual_errors(tmp_path, monkeypatch):
    run = dry_harness(tmp_path, monkeypatch)
    calls = []
    def failing(job, target, parser=None):
        calls.append(job['arm'])
        return dict(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
            map_sha256=job['sha256'], status='error', error='synthetic load failure',
            passed=False, full_checker_pass=False, plan=None, wall_s=.01)
    result=driver.run(run, worker=failing, admission_wait=lambda _: {'synthetic': True})
    rows = json.loads((run/'rows.json').read_text())
    assert calls == ['pipeline', 'twostep']
    assert len(rows) == 2 and rows[1]['status'] == 'error'
    assert rows[1]['dispatched'] and not result['complete']


def test_review6_cancelled_controller_does_not_wait_forever_for_capacity(tmp_path):
    from experiments.mvp.pipeline.sealed import common
    waits=[]
    snapshot=common.wait_for_admission(tmp_path/'missing', wait=lambda _: waits.append(1),
                                      stop=lambda: bool(waits), poll_s=1)
    assert snapshot is None and waits == [1]


def test_review1_prepass_replay_cannot_overwrite_the_draw(active_run, monkeypatch):
    from experiments.mvp.pipeline.sealed.authorization import issue, AuthorizationError
    run, job = active_run
    job = dict(job, arm='instruction')
    request=issue(job, phase='prepass', run_id='fixed', job_id='prepass')
    # Consume the saved read request first; retry must fail before any map bytes.
    worker._authorize_open(request, Path(job['path']), phase='prepass')
    with pytest.raises(AuthorizationError, match='consumed'):
        worker.prepare_instructions([request], run/'prepass', job['open_log'])
    assert not (run/'prepass/instruction-draw.json').exists()


def test_review6_interruption_keeps_population_and_revokes_workers(tmp_path, monkeypatch):
    run=dry_harness(tmp_path, monkeypatch)
    def stub(job, target, parser=None):
        return dict(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
            map_sha256=job['sha256'], status='no_checked_plan', passed=False,
            full_checker_pass=False, plan=None, wall_s=.01)
    def interrupted(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(driver, 'as_completed', interrupted)
    with pytest.raises(KeyboardInterrupt):
        driver.run(run, worker=stub, admission_wait=lambda _: {'synthetic': True})
    assert (run/'INTERRUPTED.json').exists() and (run/'FAILED.json').exists()
    assert len(json.loads((run/'rows.json').read_text())) == 2
    assert not json.loads((run/'report.json').read_text())['sealed_claim_eligible']


def test_review1_worker_replay_preserves_committed_output(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(worker, '_load', lambda job, **kw: SimpleNamespace(id='m'))
    monkeypatch.setattr(worker, '_gate', lambda _: dict(in_envelope=False))
    request=dict(map_id='m', cohort='tiny15', arm='pipeline', path='unused',
                 sha256='a'*64, mode='dry-run')
    worker.run_job(request, str(tmp_path))
    committed=(tmp_path/'row.json').read_bytes()
    with pytest.raises(ValueError, match='refusing overwrite'):
        worker.run_job(request, str(tmp_path))
    assert (tmp_path/'row.json').read_bytes() == committed


def test_budget_development_entry_points_share_conservative_default():
    import inspect
    from experiments.mvp.pipeline.sealed import budget, dry_run
    assert inspect.signature(dry_run.run).parameters['workers'].default == budget.DEFAULT_WORKERS == 2


def test_review1_saved_request_needs_a_live_controller(active_run, monkeypatch):
    from experiments.mvp.pipeline.sealed import authorization
    run, job=active_run
    request=authorization.issue(job, phase='plan', run_id='fixed', job_id='trial')
    # Successful nonblocking flock means the owning controller is gone.
    monkeypatch.setattr(authorization.fcntl, 'flock', lambda *args: None)
    with pytest.raises(ValueError, match='No active controller'):
        worker._authorize_open(request, Path(job['path']))
    assert not (run/'consumed').exists()
