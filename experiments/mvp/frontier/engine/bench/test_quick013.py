"""Controller/admission helpers only; no solver or subprocess is started."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from engine.bench import admission

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('engine_quick013_tests', HERE/'quick013.py')
quick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quick)


def test_full_quick_is_the_same_fixed_21_for_both_arms():
    calls = []
    def load(name):
        calls.append(name)
        return [f'approved-{i}.json' for i in range(21)]
    assert len(quick.control_maps(load)) == 21
    assert calls == ['quick']
    with pytest.raises(ValueError, match='21-map'):
        quick.control_maps(lambda name: ['duplicate']*21)


def test_launch_commands_keep_two_workers_and_original_fresh_policy():
    def value(command, key):
        return command[command.index(key)+1]
    engine = quick.command('engine', 'engine-out', 'full-quick.txt')
    fresh = quick.command('fresh_e9', 'fresh-out', 'full-quick.txt')
    for command in (engine, fresh):
        assert command[:3] == ['nice', '-n', '5']
        assert command[4].endswith('/bench/safe_score.py')
        assert value(command, '--set') == 'full-quick.txt'
        assert value(command, '--workers') == '2'
        assert value(command, '--budget-s') == '900'
        assert value(command, '--tries') == '3'
        assert value(command, '--seed') == '0'
    assert value(engine, '--finalize-s') == '600'
    assert value(fresh, '--finalize-s') == '300'
    assert value(fresh, '--method').endswith('lab/c1/leader/method_e9_val.py:propose')


def test_environment_shares_gate_and_caps_all_threads(tmp_path):
    for arm in ('engine', 'fresh_e9'):
        env = quick.environment(arm, tmp_path/'gate')
        assert all(env[name] == '1' for name in quick.THREADS)
        assert env['PYTHONDONTWRITEBYTECODE'] == '1'
        assert env[admission.DIRECTORY_ENV] == str(tmp_path/'gate')
        assert env[admission.ARM_ENV] == arm
        assert env[admission.MARKER_ENV] == str(quick.MARKER)
        assert Path(env['STOWAGE_VESSEL_CACHE_DIR']).is_relative_to(quick.ENGINE)


def test_marker_refuses_parent_launch_and_long_poll_is_rejected(tmp_path, monkeypatch):
    marker = tmp_path/'BENCH-RUNNING'
    marker.touch()
    monkeypatch.setattr(quick, 'MARKER', marker)
    with pytest.raises(SystemExit):
        quick.main([])
    with pytest.raises(SystemExit):
        quick.main(['--poll-s', '61'])


def test_source_manifest_excludes_results_and_pytest_copies(tmp_path, monkeypatch):
    engine = tmp_path/'engine'
    for folder in ('results', '.pytest-tmp'):
        (engine/folder).mkdir(parents=True)
        (engine/folder/'copy.py').write_text('artifact=True\n')
    (engine/'planner.py').write_text('frozen=True\n')
    (engine/'tiers.toml').write_text('[tiers]\n')
    (engine/'approved-maps.json').write_text('[]\n')
    (engine/'unrelated.json').write_text('{}\n')
    (engine/'peel_ladder').mkdir()
    for name in ('copied-manifest.json', 'assignment-frozen-hashes.json'):
        (engine/'peel_ladder'/name).write_text('{}\n')
    monkeypatch.setattr(quick, 'ENGINE', engine)
    assert set(quick.engine_manifest()) == {'planner.py', 'tiers.toml', 'approved-maps.json',
        'peel_ladder/copied-manifest.json', 'peel_ladder/assignment-frozen-hashes.json'}


def test_control_source_binding_includes_parameter_toml(tmp_path, monkeypatch):
    frontier = tmp_path/'frontier'
    lab = frontier/'lab'
    for name in ('c1/leader/method_e9_val.py', 'c1/leader/method_e9_base.py',
                 'c1/leader/e9fix.py', 'ref/frontier09.py', 'score.py', 'catalog.py', 'summary.py'):
        path = lab/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# frozen\n')
    (tmp_path/'params').mkdir()
    (tmp_path/'params/native.toml').write_text('rule=1\n')
    monkeypatch.setattr(quick, 'ROOT', tmp_path)
    monkeypatch.setattr(quick, 'FRONTIER', frontier)
    monkeypatch.setattr(quick, 'LAB', lab)
    assert 'params/native.toml' in quick.control_sources()


def test_input_hash_authorizes_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(quick, 'ROOT', tmp_path)
    (tmp_path/'approved.json').write_text('{}')
    calls = []
    def authorize(path):
        calls.append(path)
        if path != 'approved.json':
            raise ValueError('outside positive provenance')
        return path
    result = quick.input_manifest(['approved.json'], authorize)
    assert len(result['approved.json']) == 64
    with pytest.raises(ValueError, match='positive provenance'):
        quick.input_manifest(['never-opened.json'], authorize)
    assert calls == ['approved.json', 'never-opened.json']


def test_load_average_is_explicit_proxy_and_handles_unavailable(monkeypatch):
    monkeypatch.setattr(quick.os, 'getloadavg', lambda: (12., 9., 8.))
    row = quick.load_proxy(['fresh_e9', 'engine'])
    assert row['load_average'] == [12., 9., 8.]
    assert row['running_arms'] == ['engine', 'fresh_e9']
    assert 'not a CPU-process count or per-map load match' in row['scope']
    def denied():
        raise OSError('unavailable')
    monkeypatch.setattr(quick.os, 'getloadavg', denied)
    assert quick.load_proxy([])['status'] == 'unavailable'


def test_outcome_decorator_never_promotes_relaxed_evidence_to_native_pass():
    completed = dict(level=3, column='T2', boxes_left_ashore=[])
    partial = dict(boxes_aboard_percent=80.)
    row = dict(passed=False, lifts=None, method_metadata=dict(engine=dict(
        completed=completed, best_safe_partial=partial, winner_lane='assignment_lp')))
    assert admission.outcome_row(row) is row
    assert row['completed'] is completed and row['best_safe_partial'] is partial
    assert row['passed'] is False and row['lifts'] is None
    assert row['winner_lane'] == 'assignment_lp'
    ordinary = dict(passed=True, lifts=9., method_metadata=dict(engine=dict(completed=None)))
    admission.outcome_row(ordinary)
    assert ordinary['completed']['level'] == 0 and ordinary['lifts'] == 9.
    rejected = dict(passed=False, method_metadata=dict(engine=dict(completed=dict(level=0))))
    admission.outcome_row(rejected)
    assert rejected['completed'] is None and rejected['passed'] is False
    assert 'independent' in rejected['outcome_reporting_warning']
    fresh = dict(passed=False, method_metadata={})
    assert admission.outcome_row(fresh) == fresh and 'completed' not in fresh


def test_install_is_idempotent_and_calls_native_runner_once(tmp_path, monkeypatch):
    monkeypatch.delenv(admission.DIRECTORY_ENV, raising=False)
    calls, writes = [], []
    row = dict(instance_id='map', passed=False, method_metadata={})
    def native(job):
        calls.append(job)
        return row, False
    module = NS(one=native, write_json=lambda path, value: writes.append((path, value)))
    monkeypatch.setattr(admission, '_score_module', None)
    admission.install(module)
    admission.install(module)
    job = ('approved.json', tmp_path, {}, {})
    assert module.one(job) == (row, False)
    assert module._engine_admission_original_one is native
    assert calls == [job]
    assert writes == [(tmp_path/'map.json', row)]


def test_two_shared_locks_and_journal_record_actual_arm_overlap(tmp_path):
    marker = tmp_path/'BENCH-RUNNING'
    directory = tmp_path/'gate'
    with admission.admitted(directory, arm='engine', marker=marker) as engine:
        assert engine['slot'] == 0
        with admission.admitted(directory, arm='fresh_e9', marker=marker) as fresh:
            assert fresh['slot'] == 1
            snapshot = admission.journal(directory)
            assert snapshot['max_admitted'] == 2
    snapshot = admission.journal(directory)
    assert snapshot['max_admitted'] == 2 and snapshot['overlap_s'] > 0.
    assert snapshot['unfinished_leases'] == []
    assert len(snapshot['owned_worker_pids']) == 1  # this helper uses no child process
    assert 'not per-map matched load' in snapshot['scope']


def test_returning_marker_prevents_new_second_slot(tmp_path, monkeypatch):
    marker = tmp_path/'BENCH-RUNNING'
    directory = tmp_path/'gate'
    class Waited(Exception):
        pass
    def stop_poll(seconds):
        assert seconds <= 60.
        raise Waited
    with admission.admitted(directory, arm='engine', marker=marker):
        marker.touch()
        monkeypatch.setattr(admission.time, 'sleep', stop_poll)
        with pytest.raises(Waited):
            with admission.admitted(directory, arm='fresh_e9', marker=marker):
                raise AssertionError('second admission must wait')
        assert not (directory/'slot-1.lock').exists()


def test_exception_releases_slot_and_preserves_native_error(tmp_path):
    with pytest.raises(RuntimeError, match='native error'):
        with admission.admitted(tmp_path, arm='engine', marker=None):
            raise RuntimeError('native error')
    with admission.admitted(tmp_path, arm='fresh_e9', marker=None) as value:
        assert value['slot'] == 0
    assert admission.journal(tmp_path)['unfinished_leases'] == []
