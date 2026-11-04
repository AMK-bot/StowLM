"""Offline orchestration checks: no native solve, model, or network."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.runner import profile_study as study


def test_admission_waits_missing_malformed_stale_and_busy(tmp_path):
    path = tmp_path / 'state.json'
    steps = iter(['{bad', json.dumps(dict(mask=255, load1=10, t=1)),
                  json.dumps(dict(mask=255, load1=46, t=1000)),
                  json.dumps(dict(mask=255, load1=20, t=1000))])
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        path.write_text(next(steps))
    result = study.wait_for_capacity(path, sleep=sleep,
                                    sample=lambda: study.telemetry(path, now=1000))
    assert len(sleeps) == 4
    assert result['load1'] == 20 and result['enabled_cores'] == 8


@pytest.mark.parametrize('raw', [dict(mask=True, load1=2, t=100),
                                  dict(mask=255, load1=True, t=100),
                                  dict(mask=255, load1='nan', t=100),
                                  dict(mask=-1, load1=2, t=100)])
def test_telemetry_refuses_invalid_values(tmp_path, raw):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(raw))
    assert study.telemetry(path, now=100)['valid'] is False


def test_six_workers_requires_sustained_distinct_fresh_low_load():
    samples = [dict(valid=True, enabled_cores=12, load1=20, reading_t=n,
                    sampled_monotonic=n) for n in (0, 30, 60)]
    assert study.worker_limit(samples) == 6
    assert study.worker_limit(samples[:2]) == 4
    samples[-1]['load1'] = 30
    assert study.worker_limit(samples) == 4
    samples[-1]['load1'] = 20
    samples[-1]['reading_t'] = 30
    assert study.worker_limit(samples) == 4


def test_profiles_record_defaults_without_ablation():
    from engine.config import Config
    from dataclasses import asdict
    default = study.study_profile('default')
    assert default['budget_s'] is default['finalize_s'] is default['config'] is None
    assert default['resolved_config'] == asdict(Config())
    assert default['resolved_config']['warm_polish'] is True
    for seconds in (60, 120, 300):
        profile = study.study_profile(str(seconds))
        assert profile['budget_s'] == seconds
        assert profile['finalize_s'] == seconds / 3
        assert profile['config']['assignment_budget_s'] == seconds
        assert profile['config']['assignment_finalize_s'] == seconds / 3
        assert profile['config']['warm_polish'] is False
        assert profile['config']['work_count_search'] is False


def test_parser_fixture_blank_is_not_a_rule():
    parser = study.SyntheticBenchmarkParser()
    answer, provenance = parser.parse('', {})
    assert answer.code == 'not_an_instruction'
    assert provenance['parser'] == 'SyntheticBenchmarkParser'
    answer, _ = parser.parse(study.SENTENCE, dict(hatch_covers=['cover']))
    assert answer.type == 'one_port_per_hatch'
    with pytest.raises(ValueError):
        parser.parse('Another arbitrary rule.', {})


def test_dev_maps_refuses_symlink_before_read(tmp_path):
    dev = tmp_path / 'dev'
    dev.mkdir()
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    (dev / 'bad.json').symlink_to(outside)
    with pytest.raises(ValueError, match='outside'):
        study.dev_maps(dev)


def test_stratified_pilot_before_repeat_seeds():
    names = ['feeder_500-random_yard-f60-r0', 'feeder_500-random_yard-f60-r1',
             'feeder_500-random_yard-f70-r0', 'feeder_500-simulated_yard-f60-r0',
             'feeder_500-simulated_yard-f70-r0']
    ordered = study.map_order([Path(name) for name in names])
    assert str(ordered[-1]) == names[1]


def test_stale_lock_recovers_without_stopping_any_process(tmp_path, monkeypatch):
    lock = tmp_path / 'coordinator.lock'
    lock.write_text('123456789')
    seen = []
    monkeypatch.setattr(study, 'recorded_pid_alive', lambda pid: seen.append(pid) or False)
    study.acquire_lock(lock)
    assert int(lock.read_text()) == study.os.getpid()
    assert seen == [123456789]


def test_live_coordinator_lock_is_preserved(tmp_path, monkeypatch):
    lock = tmp_path / 'coordinator.lock'
    lock.write_text('123456789')
    monkeypatch.setattr(study, 'recorded_pid_alive', lambda pid: True)
    with pytest.raises(ValueError, match='still running'):
        study.acquire_lock(lock)
    assert lock.read_text() == '123456789'


@pytest.fixture
def row_setup(monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner import admission, benchmark, runtime
    from stowage import frozen
    path = study.dev_maps()[0]
    job = study.make_jobs([path], ['60'], cohort_sha256='a' * 64)[1]
    monkeypatch.setattr(study, 'HERE', tmp_path)
    monkeypatch.setattr(runtime, 'configure_caches', lambda: None)
    monkeypatch.setattr(admission, '_binding_runtime_scope', lambda *args: nullcontext())
    monkeypatch.setattr(admission, 'CACHE_ROOT', tmp_path / 'cache')
    monkeypatch.setattr(admission, 'call_admitted', lambda function, *a, admission, **kw: function(*a, **kw))
    monkeypatch.setattr(frozen, 'active_stubs', lambda: False)
    monkeypatch.setattr(study, 'telemetry', lambda: dict(valid=True, load1=22., enabled_cores=12))
    def validate(instance, plan, readings):
        assert all(row['role'] == 'voyage' and row['binds'] for row in readings)
        if plan.get('id') != 'good':
            return None
        return dict(lifts=71., checker={'passed': True}, rules=[{'passed': True}])
    monkeypatch.setattr(benchmark, '_validated_plan', validate)
    monkeypatch.setattr(benchmark, '_validated_relaxed_plan', lambda *a: None)
    def direct(function, *args, timeout_s, stage, on_event, event_kw, **kwargs):
        kwargs[event_kw] = on_event
        return function(*args, **kwargs)
    admit = lambda: dict(mask=4095, enabled_cores=12, load1=21., reading_age_s=1., waited_s=0.)
    return job, tmp_path, direct, admit


def test_row_gates_callback_and_final_and_saves_evidence(row_setup):
    job, out, direct, admit = row_setup
    def execute(instance, receipt, **kwargs):
        from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
        assert verify_confirmation(receipt)['source'] == study.CONFIRMATION_SOURCE
        reading = receipt.evidence()
        assert reading['sentences'][0]['status'] == 'refused'
        assert reading['sentences'][1]['binds'] is True
        kwargs['on_incumbent'](dict(plan={'id': 'bad'}))
        kwargs['on_incumbent'](dict(plan={'id': 'good'}, lane='stub-lp'))
        return SimpleNamespace(plan={'id': 'good'}), dict(status='checked_plan', passed=True)
    row = study.execute_row(job, out, executor=execute, admit=admit, limited=direct)
    assert row['status'] == 'checked_plan' and row['full_pass'] is True
    assert row['lifts'] == 71 and row['first_legal_s'] is not None
    assert row['confirmation_source'] == study.CONFIRMATION_SOURCE
    assert len(row['confirmation']['confirmation_sha256']) == 64
    assert row['start']['load1'] == row['end']['load1'] == 22
    saved = json.loads((out / 'rows' / f'{job["job_id"]}.json').read_text())
    assert saved['plan'] == {'id': 'good'}
    assert saved['independent_check']['checker']['passed'] is True
    incumbent = json.loads((out / 'incumbents' / f'{job["job_id"]}.json').read_text())
    assert incumbent['plan'] == {'id': 'good'}


def test_claimed_pass_is_rejected_by_independent_gate(row_setup):
    job, out, direct, admit = row_setup
    execute = lambda *a, **kw: (SimpleNamespace(plan={'id': 'bad'}), dict(status='checked_plan', passed=True))
    row = study.execute_row(job, out, executor=execute, admit=admit, limited=direct)
    assert row['status'] == 'no_checked_plan' and row['full_pass'] is False
    assert row['lifts'] is None and row['plan'] is None


def test_engine_pin_change_never_salvages_checked_callback(row_setup):
    job, out, direct, admit = row_setup
    def execute(*args, **kwargs):
        kwargs['on_incumbent'](dict(plan={'id': 'good'}))
        return SimpleNamespace(plan={'id': 'good'}), dict(status='checked_plan', passed=True,
                                                         engine_manifest_after={'passed': False})
    row = study.execute_row(job, out, executor=execute, admit=admit, limited=direct)
    assert row['status'] == 'constraint_unavailable'
    assert row['full_pass'] is False and row['plan'] is row['lifts'] is None


def test_actual_default_kwargs_keep_none(row_setup):
    job, out, direct, admit = row_setup
    job['profile'] = study.study_profile('default')
    def execute(*args, **kwargs):
        assert kwargs['budget_s'] is kwargs['finalize_s'] is kwargs['config'] is None
        return None, dict(status='no_checked_plan', passed=False)
    row = study.execute_row(job, out, executor=execute, admit=admit, limited=direct)
    assert row['actual_kwargs']['config'] is None
    assert row['config']['warm_polish'] is True


def test_timeout_is_censored_even_if_callback_was_legal(row_setup):
    from experiments.mvp.pipeline.runner.limits import StageTimeout
    job, out, direct, admit = row_setup
    def execute(*args, **kwargs):
        kwargs['on_incumbent'](dict(plan={'id': 'good'}))
        raise StageTimeout('engine', 7200)
    row = study.execute_row(job, out, executor=execute, admit=admit, limited=direct)
    assert row['status'] == 'interrupted' and row['censored'] is True
    assert row['full_pass'] is False and row['checked_incumbent']['verdict']['lifts'] == 71


def auth_failed(job_id='failed', **updates):
    reading = {'sentences': [{'role': 'voyage', 'status': 'accepted'}]}
    return dict(job_id=job_id, status='error', message=study.AUTH_FAILURE,
                total_s=.2, worker_pid=123456, plan=None, lifts=None, full_pass=False,
                reading=reading, reading_sha256=study.sha256_json(reading),
                profile={'budget_s': 60}, map_sha256='maphash', **updates)


@pytest.mark.parametrize('field,value', [('confirmation_source', 'callback'), ('total_s', 1.),
                                       ('evidence', {'status': 'error'}), ('message', 'Other failure'),
                                       ('status', 'relaxed_plan'), ('reading_sha256', 'tampered')])
def test_repair_signature_does_not_match_other_failures(field, value):
    row = auth_failed()
    row[field] = value
    assert not study.eligible_auth_failure(row)


def test_repair_archives_original_preserves_success_and_uses_same_slot(tmp_path, monkeypatch):
    rows = tmp_path / 'rows'
    rows.mkdir()
    old = auth_failed()
    original = json.dumps(old, indent=3).encode() + b'\n'
    (rows / 'failed.json').write_bytes(original)
    success = b'{"job_id":"success", "status":"checked_plan", "full_pass":true}\n'
    (rows / 'success.json').write_bytes(success)
    relaxed = b'{"job_id":"relaxed", "status":"relaxed_plan", "full_pass":false}\n'
    (rows / 'relaxed.json').write_bytes(relaxed)
    job = dict(job_id='failed', profile=old['profile'], map_sha256=old['map_sha256'])
    study.write_json(tmp_path / 'jobs.json', {'jobs': [job]})
    (tmp_path / 'owned-pids.jsonl').write_text(json.dumps({'job_id': 'failed', 'pid': old['worker_pid']}) + '\n')
    monkeypatch.setattr(study, 'recorded_pid_alive', lambda pid: False)
    monkeypatch.setattr(study, 'migration_provenance', lambda job, out: dict(job, source_sha256='new'))
    summaries, called = [], []
    monkeypatch.setattr(study, 'refresh_summary', lambda *args: summaries.append(True))
    def execute(retry, out):
        called.append(retry)
        study.write_json(rows / 'failed.json', dict(job_id=retry['job_id'], status='checked_plan', confirmation_source=study.CONFIRMATION_SOURCE))
    assert study.repair_auth_failures(tmp_path, executor=execute) == 1
    assert called[0]['job_id'] == 'failed'
    assert called[0]['repair']['repair_worker_pid'] == study.os.getpid()
    archives = list((tmp_path / 'archive' / 'unconfirmed-auth').glob('*.json'))
    assert len(archives) == 1 and archives[0].read_bytes() == original
    assert (rows / 'success.json').read_bytes() == success
    assert (rows / 'relaxed.json').read_bytes() == relaxed
    assert study.repair_auth_failures(tmp_path, executor=execute) == 0
    assert len(summaries) == 1


def test_repair_never_runs_when_original_owned_pid_is_alive(tmp_path, monkeypatch):
    row = auth_failed()
    study.write_json(tmp_path / 'rows' / 'failed.json', row)
    study.write_json(tmp_path / 'jobs.json', {'jobs': [{'job_id': 'failed'}]})
    (tmp_path / 'owned-pids.jsonl').write_text(json.dumps({'job_id': 'failed', 'pid': row['worker_pid']}) + '\n')
    monkeypatch.setattr(study, 'recorded_pid_alive', lambda pid: True)
    assert study.repair_auth_failures(tmp_path, executor=lambda *a: pytest.fail('Active worker duplicated')) == 0


def test_migration_limits_drift_and_records_actual_source(tmp_path, monkeypatch):
    allowed = 'experiments/mvp/pipeline/runner/profile_study.py'
    old = dict(sha256='old', files={allowed: 'oldbytes'})
    new = dict(sha256='new', files={allowed: 'newbytes'})
    study.write_json(tmp_path / 'jobs.json', {'source_sha256': 'old'})
    study.write_json(tmp_path / 'source-snapshots' / 'old.json', old)
    monkeypatch.setattr(study, 'source_snapshot', lambda: new)
    result = study.migration_provenance({'source_sha256': 'old'}, tmp_path)
    assert result['source_sha256'] == result['execution_source_sha256'] == 'new'
    assert result['original_source_sha256'] == 'old'
    assert result['source_migration']['changed_files'] == [allowed]
    new['files']['experiments/mvp/pipeline/runner/preflight.py'] = 'unknownchange'
    with pytest.raises(ValueError, match='exceeds'):
        study.migration_provenance({'source_sha256': 'old'}, tmp_path)


def test_reviewed_allocation_migration_accepts_only_exact_pinned_hash(tmp_path, monkeypatch):
    path, digest = next(iter(study.EXTRA_MIGRATION_HASHES.items()))
    old = dict(sha256='old', files={path: 'oldimplementation'})
    new = dict(sha256='new', files={path: digest})
    study.write_json(tmp_path / 'jobs.json', {'source_sha256': 'old'})
    study.write_json(tmp_path / 'source-snapshots' / 'old.json', old)
    monkeypatch.setattr(study, 'source_snapshot', lambda: new)
    result = study.migration_provenance({'source_sha256': 'old'}, tmp_path)
    assert result['source_migration']['extra_reviewed_hashes'] == {path: digest}
    assert 'partial-plan hatch semantic correction' in result['source_migration']['reason']
    new['files'][path] = 'laterunreviewedchange'
    with pytest.raises(ValueError, match='exceeds'):
        study.migration_provenance({'source_sha256': 'old'}, tmp_path)


def test_unreviewed_source_waits_in_existing_slot_instead_of_failing(monkeypatch, tmp_path):
    calls, sleeps = [], []
    def provenance(job, out):
        calls.append(job)
        if len(calls) < 3:
            raise ValueError('Source drift exceeds the explicit confirmation migration; use a fresh study directory')
        return dict(job, execution_source_sha256='reviewed')
    monkeypatch.setattr(study, 'migration_provenance', provenance)
    result = study.wait_for_migration_provenance({'job_id': 'same-slot'}, tmp_path, sleep=sleeps.append)
    assert sleeps == [15., 15.] and result['execution_source_sha256'] == 'reviewed'


def test_nonexecuted_binding_measurement_and_tests_are_attribution_only(tmp_path, monkeypatch):
    names = ['experiments/mvp/pipeline/binding/' + name for name in
             ('measure.py', 'measurement_identity.py', 'measurement_lifecycle.py', 'test_new_hardening.py')]
    old = dict(sha256='old', files={})
    new = dict(sha256='new', files=dict.fromkeys(names, 'attributiononlychange'))
    study.write_json(tmp_path / 'jobs.json', {'source_sha256': 'old'})
    study.write_json(tmp_path / 'source-snapshots' / 'old.json', old)
    monkeypatch.setattr(study, 'source_snapshot', lambda: new)
    result = study.migration_provenance({'source_sha256': 'old'}, tmp_path)
    assert result['source_migration']['nonexecuted_attribution_changes'] == sorted(names)


def test_registered_exact_runner_hashes_unblock_stale_allowlist_only(tmp_path, monkeypatch):
    names = ['experiments/mvp/pipeline/runner/' + name for name in
             ('profiles.py', 'evidence.py', 'test_earlier_plan.py', 'test_ladder_work.py')]
    old = dict(sha256='old', files={})
    current = dict(sha256='current', files={name: f'current-{index}' for index, name in enumerate(names)})
    study.write_json(tmp_path / 'jobs.json', {'source_sha256': 'old', 'jobs': [{'job_id': 'existing', 'source_sha256': 'old'}]})
    study.write_json(tmp_path / 'source-snapshots' / 'old.json', old)
    monkeypatch.setattr(study, 'source_snapshot', lambda: current)
    study.register_migration(tmp_path)
    sidecar = json.loads((tmp_path / 'migrations' / 'reviewed-extra-hashes.json').read_text())
    assert sidecar['authorized_runner_hashes'] == current['files']
    stale_allowed = study.MIGRATION_FILES - {name.removeprefix('experiments/mvp/pipeline/') for name in names}
    monkeypatch.setattr(study, 'MIGRATION_FILES', stale_allowed)
    result = study.migration_provenance({'job_id': 'existing', 'source_sha256': 'old'}, tmp_path)
    assert result['execution_source_sha256'] == 'current'
    current['files'][names[0]] = 'future-unreviewed-change'
    current['sha256'] = 'future'
    with pytest.raises(ValueError, match='exceeds'):
        study.migration_provenance({'job_id': 'existing', 'source_sha256': 'old'}, tmp_path)


def test_reviewed_bridge_archives_exact_manifest_and_preserves_jobs_rows(tmp_path, monkeypatch):
    jobs = [{'job_id': 'unchanged', 'source_sha256': 'old', 'profile': {'budget_s': 60}, 'map_sha256': 'map'}]
    manifest = {'source_sha256': 'old', 'jobs': jobs, 'labels': ['60'], 'timeout_s': 7200}
    raw_manifest = json.dumps(manifest, indent=3).encode() + b'\n'
    (tmp_path / 'jobs.json').write_bytes(raw_manifest)
    study.write_json(tmp_path / 'rows' / 'valid.json', {'job_id': 'valid', 'status': 'checked_plan'})
    raw_row = (tmp_path / 'rows' / 'valid.json').read_bytes()
    study.write_json(tmp_path / 'activity.json', {'active': [{'pid': 12345, 'job_id': 'unchanged'}]})
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs' / 'unchanged.log').write_text('{"status": "waiting_source_review"}\n')
    monkeypatch.setattr(study, 'register_migration', lambda out: None)
    monkeypatch.setattr(study, 'source_snapshot', lambda: {'sha256': 'reviewed', 'files': {}})
    result = study.bridge_reviewed_baseline(tmp_path)
    assert result['status'] == 'bridged'
    assert (tmp_path / result['archived_manifest']).read_bytes() == raw_manifest
    updated = json.loads((tmp_path / 'jobs.json').read_text())
    assert updated['jobs'] == jobs
    assert updated['original_source_sha256'] == 'old'
    assert updated['source_sha256'] == 'reviewed'
    assert updated['source_baseline_migration']['affected_workers'] == [{'pid': 12345, 'job_id': 'unchanged'}]
    assert (tmp_path / 'rows' / 'valid.json').read_bytes() == raw_row
    # An old helper needs no new dynamic sidecar branch on this exact fast path.
    monkeypatch.setattr(study, 'MIGRATION_FILES', set())
    assert study.migration_provenance(jobs[0], tmp_path)['source_sha256'] == 'reviewed'


def test_policy_dependencies_checked_before_legacy_source_fastpath(tmp_path, monkeypatch):
    study.write_json(tmp_path / 'jobs.json', {'source_sha256': 'same'})
    monkeypatch.setattr(study, 'source_snapshot', lambda: {'sha256': 'same', 'files': {}})
    def changed():
        raise ValueError('Unreviewed hatch policy dependency: hatch.py')
    monkeypatch.setattr(study, 'policy_dependencies', changed)
    with pytest.raises(ValueError, match='Unreviewed hatch'):
        study.migration_provenance({'source_sha256': 'same'}, tmp_path)


def test_tail_watcher_waits_for_recorded_owned_processes(tmp_path, monkeypatch):
    (tmp_path / 'coordinator.lock').write_text('123')
    (tmp_path / 'owned-pids.jsonl').write_text(json.dumps({'pid': 456, 'job_id': 'unfinished'}) + '\n')
    alive = {123: True, 456: True}
    probes, sleeps = [], []
    def probe(pid):
        probes.append(pid)
        return alive[pid]
    def sleep(seconds):
        sleeps.append(seconds)
        if alive[123]:
            alive[123] = False
        else:
            alive[456] = False
    monkeypatch.setattr(study, 'recorded_pid_alive', probe)
    study.wait_for_study_tail(tmp_path, sleep=sleep)
    assert sleeps == [15., 15.]
    assert set(probes) == {123, 456}


def test_finalize_checks_raw_auth_failures_before_enriched_analysis(tmp_path, monkeypatch):
    from . import profile_finalize
    monkeypatch.setattr(profile_finalize, 'HERE', tmp_path)
    root = tmp_path / 'study'
    raw = auth_failed()
    study.write_json(root / 'rows' / 'failed.json', raw)
    original = (root / 'rows' / 'failed.json').read_bytes()
    study.write_json(root / 'jobs.json', {'jobs': [{'job_id': 'failed'}]})
    study.write_json(root / 'measurements.json', {'rows': []})
    monkeypatch.setattr(profile_finalize, 'analyze', lambda *a, **kw: pytest.fail('Analyzed an obsolete auth failure'))
    target = tmp_path / 'selection.json'
    target.write_text('preserve selected profiles')
    result = profile_finalize.finalize(root, target=target)
    assert result['status'] == 'pending'
    assert result['pending_confirmation_repairs'] == ['failed']
    assert target.read_text() == 'preserve selected profiles'
    assert (root / 'rows' / 'failed.json').read_bytes() == original


def test_finalize_blocks_mixed_hatch_portfolio_epochs_without_touching_rows(tmp_path, monkeypatch):
    from . import profile_finalize
    monkeypatch.setattr(profile_finalize, 'HERE', tmp_path)
    root = tmp_path / 'study'
    rows = [dict(job_id='legacy', rule_mode='one_port_per_hatch', status='checked_plan',
                 full_pass=True, evidence={'status': 'checked_plan'}),
            dict(job_id='portfolio', rule_mode='one_port_per_hatch', status='checked_plan',
                 full_pass=True, evidence={'status': 'checked_plan', 'portfolio_policy_version': 'logic3-hatch-portfolio-v1'})]
    study.write_json(root / 'jobs.json', {'jobs': [{'job_id': row['job_id']} for row in rows]})
    for row in rows:
        study.write_json(root / 'rows' / f'{row["job_id"]}.json', row)
    before = {path.name: path.read_bytes() for path in (root / 'rows').glob('*.json')}
    monkeypatch.setattr(profile_finalize, 'analyze', lambda *a, **kw: pytest.fail('Mixed hatch epochs analyzed'))
    target = tmp_path / 'selection.json'
    target.write_text('previous profiles')
    result = profile_finalize.finalize(root, target=target)
    assert result['status'] == 'pending'
    assert result['hatch_policy_epochs'] == ['legacy-single-mapping', 'logic3-hatch-portfolio-v1']
    assert 'quiet-clean' in result['rerun_command']
    assert target.read_text() == 'previous profiles'
    assert {path.name: path.read_bytes() for path in (root / 'rows').glob('*.json')} == before


def test_finalize_keeps_source_epochs_and_caveat_in_published_limitations(tmp_path, monkeypatch):
    from . import profile_finalize
    monkeypatch.setattr(profile_finalize, 'HERE', tmp_path)
    root = tmp_path / 'study'
    row = dict(job_id='passing', status='checked_plan', full_pass=True,
               baseline_violates_rule=True, rule_mode='one_port_per_hatch',
               execution_source_sha256='new-epoch', worker_pid=12345)
    study.write_json(root / 'rows' / 'passing.json', row)
    original = (root / 'rows' / 'passing.json').read_bytes()
    study.write_json(root / 'jobs.json', {'jobs': [{'job_id': 'passing'}]})
    caveat = 'Reviewed mixed binding epochs; comparisons may be confounded.'
    study.write_json(root / 'source-epochs.json', {'source_epochs': ['old-epoch', 'new-epoch'],
                                                  'source_epoch_caveat': caveat})
    bridge = {'from_source_sha256': 'old-epoch', 'to_source_sha256': 'new-epoch',
              'loaded_metadata_version_caveat': 'Legacy metadata helpers; unchanged compute settings.',
              'affected_workers': [{'pid': 12345, 'job_id': 'passing'}]}
    study.write_json(root / 'source-baseline-bridge.json', bridge)
    recommendation = dict(provisional=False, live={'budget_label': '60'}, standard={'budget_label': '300'},
                          binding_evidence={'baseline_plans_violating_rule': 1}, reasoning=['measured'],
                          completeness={'complete': True}, limitations=['Synthetic development data only.'])
    monkeypatch.setattr(profile_finalize, 'analyze', lambda *a, **kw: dict(recommendation=recommendation,
                        data_sha256='d'*64, budget_table=[], paired_costs=[]))
    target = tmp_path / 'selection.json'
    result = profile_finalize.finalize(root, target=target,
                    identity={'passed': True, 'engine_version': '0.13.0', 'manifest_sha256': 'a'*64})
    assert result['status'] == 'published'
    selected = json.loads(target.read_text())
    assert selected['source_epochs'] == ['new-epoch', 'old-epoch']
    assert selected['source_epoch_caveat'] == caveat
    assert caveat in selected['limitations']
    assert any('new-epoch' in text and 'old-epoch' in text for text in selected['limitations'])
    assert len(selected['source_epochs_sidecar_sha256']) == 64
    assert selected['source_baseline_migration'] == bridge
    assert bridge['loaded_metadata_version_caveat'] in selected['limitations']
    assert selected['row_loaded_metadata_caveats']['passing'] == bridge['loaded_metadata_version_caveat']
    assert (root / 'rows' / 'passing.json').read_bytes() == original
