"""Controller helper checks; no child processes or numerical jobs are started."""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('engine_quick012_tests', HERE/'quick012.py')
quick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quick)


def test_fresh_control_uses_only_fixed_tiny_and_small_sets():
    calls = []
    def load(name):
        calls.append(name)
        return [f'tiny-{i}' for i in range(8)] if name == 'quick_tiny' else [f'small-{i}' for i in range(10)]
    assert len(quick.control_maps(load)) == 18
    assert calls == ['quick_tiny', 'quick_small']
    with pytest.raises(ValueError, match='exactly'):
        quick.control_maps(lambda name: ['duplicate'])


def test_launch_commands_preserve_two_worker_arms_and_original_e9_policy():
    engine = quick.command('engine', 'engine-out', 'small-tiny.txt')
    control = quick.command('fresh_e9', 'fresh-out', 'small-tiny.txt')
    def value(command, key):
        return command[command.index(key)+1]
    assert value(engine, '--set') == 'quick'
    assert value(control, '--set') == 'small-tiny.txt'
    assert value(engine, '--workers') == value(control, '--workers') == '2'
    assert value(engine, '--budget-s') == '900' and value(engine, '--finalize-s') == '600'
    assert value(control, '--finalize-s') == '300'
    assert value(engine, '--tries') == value(control, '--tries') == '3'
    assert value(engine, '--seed') == value(control, '--seed') == '0'
    assert value(control, '--method').endswith('lab/c1/leader/method_e9_val.py:propose')
    assert value(control, '--finish').endswith('lab/c1/leader/method_e9_val.py:finish')


def test_thread_caps_cache_scope_and_source_manifest_exclude_results(tmp_path, monkeypatch):
    env = quick.environment('fresh_e9')
    assert all(env[name] == '1' for name in quick.THREADS)
    assert env['PYTHONDONTWRITEBYTECODE'] == '1'
    assert Path(env['STOWAGE_VESSEL_CACHE_DIR']).is_relative_to(quick.ENGINE)
    engine = tmp_path/'engine'; (engine/'results').mkdir(parents=True)
    (engine/'planner.py').write_text('frozen=True\n')
    (engine/'results/row.py').write_text('not source\n')
    monkeypatch.setattr(quick, 'ENGINE', engine)
    assert set(quick.engine_manifest()) == {'planner.py'}


def test_poll_interval_rejects_long_blocking_wait():
    with pytest.raises(SystemExit):
        quick.main(['--poll-s', '61'])



def test_load_proxy_records_time_and_active_arms_without_claiming_process_count(monkeypatch):
    monkeypatch.setattr(quick.os, 'getloadavg', lambda: (12., 10., 8.))
    row = quick.load_proxy(['fresh_e9', 'engine'])
    assert row['load_average'] == [12., 10., 8.]
    assert row['running_arms'] == ['engine', 'fresh_e9']
    assert row['unix_s'] > 0. and row['monotonic_s'] > 0.
    assert 'NOT evidence' in row['scope']
    def blocked():
        raise OSError('unavailable')
    monkeypatch.setattr(quick.os, 'getloadavg', blocked)
    row = quick.load_proxy(['engine'])
    assert row['status'] == 'unavailable' and row['load_average'] is None
