from datetime import datetime, timedelta, timezone
import json
import threading

import pytest

from llm.exit import progress


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_started_only_at_generation_zero_and_is_idempotent(tmp_path):
    stamp = datetime(2026, 10, 30, 2, tzinfo=timezone.utc)
    progress.start_generation(tmp_path, 1, now=stamp)
    marker = tmp_path / 'STARTED'
    assert not marker.exists()
    progress.start_generation(tmp_path, 0, now=stamp)
    original = marker.read_text()
    progress.start_generation(tmp_path, 0, now=stamp + timedelta(minutes=5))
    assert marker.read_text() == original == stamp.isoformat() + '\n'
    state = json.loads((tmp_path / 'gen-000/phase-status.json').read_text())
    assert state['elapsed_s'] == 300 and state['resumes'] == 1


def test_phase_is_durable_while_running_and_records_failure(tmp_path):
    tracker = progress.start_generation(tmp_path, 0)
    with pytest.raises(RuntimeError, match='test failure'):
        with tracker.phase('dataset', rows=48):
            live = json.loads(tracker.path.read_text())
            assert live['phases'][0]['status'] == 'running'
            tracker.update(complete_maps=4)
            raise RuntimeError('test failure')
    state = json.loads(tracker.path.read_text())
    assert state['counters'] == {'complete_maps': 4}
    phase = state['phases'][0]
    assert phase['status'] == 'failed'
    assert phase['wall_s'] >= 0 and phase['error'] == 'RuntimeError: test failure'
    tracker.finish('failed')
    assert json.loads(tracker.path.read_text())['status'] == 'failed'


def test_overrun_reports_active_phase_without_fabricating_missing_times(tmp_path):
    start = datetime(2026, 10, 30, 2, tzinfo=timezone.utc)
    put(tmp_path / 'gen-000/phase-status.json', dict(generation=0, status='running',
        started_at=start.isoformat(), phases=[dict(name='decode_verify_pipeline',
        started_at=(start + timedelta(minutes=1)).isoformat(), status='running')]))
    report = progress.write_generation_report(tmp_path, now=start + timedelta(minutes=121))
    assert report['over_120_minutes'] is True
    assert report['active_phases'][0]['elapsed_s'] == 7200
    assert report['phase_wall_s']['decode_s'] is None
    assert report['pairs']['admitted'] is None
    assert (tmp_path / 'generation-0-report.json').exists()
    assert 'unmeasured' in (tmp_path / 'generation-0-report.md').read_text()


def test_complete_report_separates_worker_totals_dpo_and_source_labels(tmp_path):
    gen = tmp_path / 'gen-000'
    put(gen / 'summary.json', dict(instance_count=48, preference_pairs=12,
        preference_over_length_pairs=3, winners_by_source_detail=dict(model=8,
        polished_from_model=4, gold=10, baseline_twostep=20, polished_from_baseline_twostep=6),
        timings=dict(wall_s=3900, decode_s=1800, decode_verify_pipeline_s=1900,
            verification_worker_s=3800, dataset_s=150, child_0_train_s=800,
            child_1_train_s=820, child_1_dpo_s=150, fitness_s=.01, audit_s=0),
        children=[dict(adapter='child-0', method='sft'), dict(adapter='child-1/preference',
            method='sft_dpo', preference_training=dict(reference_prepass_s=80, train_s=60, pairs=12))],
        fitness=dict(best_adapter='parent'), audit=None))
    put(gen / 'cases/000/selection.json', dict(verification_started_monotonic=100,
        verification_finished_monotonic=150, passed=True, source='model'))
    put(gen / 'cases/001/selection.json', dict(verification_started_monotonic=110,
        verification_finished_monotonic=160, passed=True, source='gold'))
    put(tmp_path / 'sizing-after-gen0.json', dict(maps=36, k=6))
    report = progress.build_generation_report(tmp_path)
    assert report['status'] == 'completed' and not report['over_120_minutes']
    assert report['elapsed_s'] == 3900
    assert report['phase_wall_s']['verify_envelope_s'] == 60
    assert report['worker_totals_s']['verification_worker_s'] == 3800
    assert report['phase_wall_s']['dpo_reference_s'] == 80
    assert report['phase_wall_s']['dpo_update_s'] == 60
    assert report['phase_wall_s']['sft_by_child_s'] == {'child_0': 800, 'child_1': 820}
    assert report['winners']['model_alone'] == 8
    assert report['winners']['model_plus_polish'] == 4
    assert report['winners']['two_step'] == 20
    assert report['winners']['polished_two_step'] == 6
    assert report['pairs']['admitted'] == 12
    assert report['generation_1_sizing'] == {'maps': 36, 'k': 6}


def test_resume_marks_inflight_phase_interrupted_and_reports_child_error(tmp_path):
    start = datetime(2026, 10, 30, 2, tzinfo=timezone.utc)
    tracker = progress.start_generation(tmp_path, 0, now=start)
    tracker.state['phases'].append(dict(name='child_1_dpo', status='running', started_at=start.isoformat()))
    tracker._save(start)
    tracker = progress.start_generation(tmp_path, 0, now=start + timedelta(minutes=2))
    phase = tracker.state['phases'][0]
    assert phase['status'] == 'interrupted' and phase['observed_elapsed_s'] == 120
    assert 'wall_s' not in phase  # An interrupted phase's actual work duration is unknown.
    put(tmp_path / 'gen-000/child-1-ready.json', dict(adapter='child-1', method='sft',
        preference_error='RuntimeError: nonfinite update'))
    report = progress.build_generation_report(tmp_path, now=start + timedelta(minutes=2))
    assert report['errors'] == ['RuntimeError: nonfinite update']
    assert report['active_phases'] == []


def test_invalid_artifact_report_survives_and_preserves_unmeasured_state(tmp_path):
    path = tmp_path / 'gen-000/summary.json'
    path.parent.mkdir(parents=True)
    path.write_text('{interrupted')
    report = progress.build_generation_report(tmp_path)
    assert report['status'] == 'not_started' and report['elapsed_s'] is None
    assert 'summary.json: JSONDecodeError:' in report['errors'][0]
    assert report['phase_wall_s']['dpo_reference_s'] is None


def test_unfinished_dpo_completion_artifact_supplies_reference_and_update_times(tmp_path):
    gen = tmp_path / 'gen-000'
    put(gen / 'child-1/preference/dpo_run.json', dict(reference_prepass_s=3, train_s=8))
    (gen / 'preferences.jsonl').write_text('{"pair":1}\n\n{"pair":2}\n')
    report = progress.build_generation_report(tmp_path)
    assert report['pairs']['admitted'] == 2
    assert report['phase_wall_s']['dpo_reference_s'] == 3
    assert report['phase_wall_s']['dpo_update_s'] == 8


def test_timezone_and_negative_generation_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='nonnegative'):
        progress.start_generation(tmp_path, -1)
    with pytest.raises(ValueError, match='timezone'):
        progress.start_generation(tmp_path, 0, now=datetime(2026, 10, 30))


def test_deployment_output_does_not_nest_and_live_counters_supply_timings(tmp_path):
    out = tmp_path / 'deployment'
    tracker = progress.start_generation(out, 0)
    tracker.update(decode_wall_s=11, timings=dict(verification_tail_wall_s=3))
    put(out / 'deployment-status.json', dict(state='generation_failed', error='exit 1'))
    report = progress.write_generation_report(out)
    assert (out / 'STARTED').exists()
    assert (out / 'generation-0-report.json').exists()
    assert not (out / 'deployment').exists()
    assert report['phase_wall_s']['decode_s'] == 11
    assert report['phase_wall_s']['verify_drain_s'] == 3
    assert report['errors'] == [dict(controller=dict(state='generation_failed', error='exit 1'))]


def test_failed_run_without_summary_includes_bounded_generation_log_tail(tmp_path):
    put(tmp_path / 'deployment-status.json', dict(state='failed', returncode=1))
    (tmp_path / 'generation.log').write_text('earlier\n' * 10000 + 'RuntimeError: GPU failed\n')
    report = progress.build_generation_report(tmp_path)
    log_error = report['errors'][-1]
    assert log_error['log_path'] == str(tmp_path / 'generation.log')
    assert log_error['log_tail'].endswith('RuntimeError: GPU failed')
    assert len(log_error['log_tail'].splitlines()) <= 40
    assert len(log_error['log_tail']) <= 16384


def test_active_dpo_stage_metadata_counters_and_root_error_are_reported(tmp_path):
    tracker = progress.start_generation(tmp_path, 0)
    with tracker.phase('child_1_dpo', child=1, stage='reference_prepass'):
        tracker.update(dpo_stage='reference_prepass_started', pairs=16)
        put(tmp_path / 'generation-error.json', dict(error='reference interrupted'))
        report = progress.build_generation_report(tmp_path)
        assert report['active_phases'][0]['metadata'] == dict(child=1, stage='reference_prepass')
        assert report['counters'] == dict(dpo_stage='reference_prepass_started', pairs=16)
        assert report['errors'] == [dict(error='reference interrupted')]


def test_phase_rss_distinguishes_observed_peak_from_lifetime_high_water(tmp_path, monkeypatch):
    samples = iter([100, 350, 120])
    monkeypatch.setattr(progress, '_current_rss_bytes', lambda: next(samples))
    monkeypatch.setattr(progress, '_lifetime_peak_rss_bytes', lambda: 9000)
    tracker = progress.start_generation(tmp_path, 0)
    with tracker.phase('train') as phase:
        # Sample an allocation that has been released before exit.
        sampler = progress._PhaseMemory.__new__(progress._PhaseMemory)
        sampler.tracker, sampler.phase = tracker, phase
        sampler.sample()
        tracker.update(done=1)
        active = progress.build_generation_report(tmp_path)['active_phases'][0]
        assert active['rss']['rss_sampled_peak_bytes'] == 350
    report = progress.write_generation_report(tmp_path)
    memory = report['phase_rss'][0]
    assert memory['rss_entry_bytes'] == 100
    assert memory['rss_exit_bytes'] == 120
    assert memory['rss_sampled_peak_bytes'] == 350
    assert memory['rss_samples'] == 3
    assert memory['process_lifetime_peak_rss_exit_bytes'] == 9000
    assert 'not a guaranteed instantaneous peak' in report['rss_note']
    assert 'CPU workers excluded' in report['rss_note']


def test_nested_phase_samplers_stop_on_failure_and_keep_independent_peaks(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, '_current_rss_bytes', lambda: 12345)
    tracker = progress.start_generation(tmp_path, 0)
    before = {thread.ident for thread in threading.enumerate() if thread.name == 'exit-phase-rss'}
    with pytest.raises(RuntimeError, match='failed'):
        with tracker.phase('outer'):
            with tracker.phase('inner'):
                assert sum(thread.name == 'exit-phase-rss' for thread in threading.enumerate()) >= 2
                raise RuntimeError('failed')
    after = {thread.ident for thread in threading.enumerate() if thread.name == 'exit-phase-rss'}
    assert after == before
    phases = json.loads(tracker.path.read_text())['phases']
    assert [phase['status'] for phase in phases] == ['failed', 'failed']
    assert all(phase['rss_sampled_peak_bytes'] == 12345 for phase in phases)


def test_missing_current_rss_is_unmeasured_not_lifetime_peak(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, '_current_rss_bytes', lambda: None)
    monkeypatch.setattr(progress, '_lifetime_peak_rss_bytes', lambda: 9000)
    tracker = progress.start_generation(tmp_path, 0)
    with tracker.phase('unsupported'):
        pass
    memory = progress.build_generation_report(tmp_path)['phase_rss'][0]
    assert memory['rss_entry_bytes'] is None and memory['rss_exit_bytes'] is None
    assert memory['rss_sampled_peak_bytes'] is None and memory['rss_samples'] == 0
    assert memory['process_lifetime_peak_rss_exit_bytes'] == 9000


def test_native_current_rss_reads_real_positive_memory():
    value = progress._current_rss_bytes()
    assert value is None or value > 0
