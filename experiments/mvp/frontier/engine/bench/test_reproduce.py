"""F2 load qualification and artifact identity; never starts CPU burners/solvers."""
from pathlib import Path
import importlib.util
import json

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('engine_reproduce_tests', HERE/'reproduce.py')
reproduce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reproduce)


def test_ps_cpu_time_days_and_process_start_identity():
    assert reproduce.cpu_seconds('01:02.50') == 62.5
    assert reproduce.cpu_seconds('1-02:03:04') == 93784.
    parsed = reproduce.parse_ps('42 7 01:02.50 Sun Nov 1 14:22:00 2026 python worker.py\n')
    assert parsed[42]['cpu_s'] == 62.5
    assert parsed[42]['started'] == 'Sun Nov 1 14:22:00 2026'
    assert parsed[42]['command'] == 'python worker.py'


def test_other_busy_processes_exclude_every_owned_descendant_and_pid_reuse():
    def rows(cpu):
        return {i: dict(pid=i, ppid=1 if i != 51 else 50, command='python',
                        started='same', cpu_s=cpu) for i in (*range(2, 14), 50, 51)}
    before, after = rows(10.), rows(11.)
    sample = reproduce.load_sample((0., before), (2., after), owned={50}, fraction=.2)
    assert sample['external_busy_count'] == 12
    assert sample['excluded_owned_pids'] == [50, 51]
    after[2]['started'] = 'new-process'
    assert reproduce.load_sample((0., before), (2., after), owned={50})['external_busy_count'] == 11


def test_repro_requires_same_frozen_artifact_actual_load_and_exact_order():
    rows = [dict(input=path, passed=True, lifts=79., ordered_actions_sha256='order',
                 full_stow_sha256='stow') for path in reproduce.MAPS]
    first = dict(artifact_id='frozen', rows=rows)
    loaded = dict(artifact_id='frozen', rows=[dict(row) for row in rows], loaded_threshold_met=True)
    assert reproduce.compare_arms(first, loaded)['status'] == 'reproduced'
    loaded['rows'][0]['ordered_actions_sha256'] = 'different-order'
    result = reproduce.compare_arms(first, loaded)
    assert result['status'] == 'diverged' and result['pairs'][0]['same_full_stow']
    loaded['loaded_threshold_met'] = False
    assert reproduce.compare_arms(first, loaded)['status'] == 'pending'
    loaded['loaded_threshold_met'] = True; loaded['artifact_id'] = 'changed'
    assert reproduce.compare_arms(first, loaded)['status'] == 'pending'


def test_freeze_excludes_results_and_detects_source_mutation(tmp_path, monkeypatch):
    root = tmp_path/'repo'; frontier = root/'experiments/mvp/frontier'; engine = frontier/'engine'
    (engine/'results').mkdir(parents=True)
    (engine/'planner.py').write_text('VERSION="test"\n')
    (engine/'results/old.py').write_text('stored recipe forbidden\n')
    (frontier/'planner.py').write_text('frozen=True\n')
    (root/'src/stowage').mkdir(parents=True); (root/'src/stowage/native.py').write_text('native=True\n')
    (root/'params').mkdir(); (root/'params/fleet.toml').write_text('value=1\n')
    (root/'map.json').write_text('{}\n')
    monkeypatch.setattr(reproduce, 'ROOT', root); monkeypatch.setattr(reproduce, 'FRONTIER', frontier)
    monkeypatch.setattr(reproduce, 'ENGINE', engine); monkeypatch.setattr(reproduce, 'MAPS', ('map.json',))
    artifact, manifest = reproduce.freeze(engine/'results/fresh')
    assert not (artifact/'experiments/mvp/frontier/engine/results').exists()
    reproduce.verify_artifact(artifact, manifest)
    (artifact/'experiments/mvp/frontier/engine/planner.py').write_text('changed=True\n')
    with pytest.raises(ValueError, match='changed'):
        reproduce.verify_artifact(artifact, manifest)


def test_marker_blocks_launch_and_poll_never_exceeds_sixty_seconds(tmp_path, monkeypatch):
    marker = tmp_path/'BENCH-RUNNING'; marker.touch()
    monkeypatch.setattr(reproduce, 'MARKER', marker)
    with pytest.raises(SystemExit):
        reproduce.main([])
    marker.unlink()
    with pytest.raises(SystemExit):
        reproduce.main(['--poll-s', '61'])


def test_missing_external_load_is_pending_without_a_second_arm(tmp_path, monkeypatch):
    engine = tmp_path/'engine'; engine.mkdir()
    monkeypatch.setattr(reproduce, 'ENGINE', engine)
    monkeypatch.setattr(reproduce, 'MARKER', tmp_path/'absent-marker')
    monkeypatch.setattr(reproduce, 'freeze', lambda out: (out/'artifact', {'artifact_id': 'frozen'}))
    calls = []
    def arm(name, *args):
        calls.append(name)
        return dict(artifact_id='frozen', rows=[])
    monkeypatch.setattr(reproduce, 'arm', arm)
    snapshots = iter([(0., {}), (1., {})])
    monkeypatch.setattr(reproduce, 'snapshot', lambda: next(snapshots))
    monkeypatch.setattr(reproduce, 'sleep', lambda seconds: None)
    out = engine/'results/repro'
    assert reproduce.main(['--out', str(out)]) == 2
    assert calls == ['two-worker']
    assert json.loads((out/'comparison.json').read_text())['status'] == 'pending'


def test_blocked_process_snapshot_stays_unknown_even_with_high_load_average(monkeypatch):
    def blocked(*args, **kwargs):
        raise PermissionError(1, 'Operation not permitted', 'ps')
    monkeypatch.setattr(reproduce.subprocess, 'check_output', blocked)
    monkeypatch.setattr(reproduce.os, 'getloadavg', lambda: (99., 99., 99.))
    snapshot = reproduce.snapshot()
    assert snapshot[1] is None
    assert snapshot[2]['status'] == 'unavailable'
    assert snapshot[2]['load_average_proxy'] == [99., 99., 99.]
    assert 'NOT evidence' in snapshot[2]['qualification']
    before = (0., None, snapshot[2]); after = (1., None, snapshot[2])
    sample = reproduce.load_sample(before, after, owned={123})
    assert sample['external_busy_count'] is None and not sample['available']


def test_unverified_second_arm_compares_exact_repeat_but_cannot_close_f2(tmp_path, monkeypatch):
    engine = tmp_path/'engine'; engine.mkdir()
    monkeypatch.setattr(reproduce, 'ENGINE', engine)
    monkeypatch.setattr(reproduce, 'MARKER', tmp_path/'absent-marker')
    monkeypatch.setattr(reproduce, 'freeze', lambda out: (out/'artifact', {'artifact_id': 'frozen'}))
    calls = []
    rows = [dict(input=path, passed=True, lifts=79., ordered_actions_sha256='same-order',
                 full_stow_sha256='same-stow') for path in reproduce.MAPS]
    def arm(name, *args, unverified_load=False):
        calls.append((name, unverified_load))
        return dict(artifact_id='frozen', rows=rows, unverified_load=unverified_load,
                    loaded_threshold_met=False)
    monkeypatch.setattr(reproduce, 'arm', arm)
    error = dict(status='unavailable', reason='PermissionError: ps blocked')
    snapshots = iter([(0., None, error), (1., None, error)])
    monkeypatch.setattr(reproduce, 'snapshot', lambda: next(snapshots))
    monkeypatch.setattr(reproduce, 'sleep', lambda seconds: None)
    out = engine/'results/repro'
    assert reproduce.main(['--out', str(out), '--allow-unverified-load']) == 2
    assert calls == [('two-worker', False), ('loaded', True)]
    comparison = json.loads((out/'comparison.json').read_text())
    assert comparison['status'] == 'pending' and not comparison['load_requirement_met']
    assert comparison['exact_repeat_observed'] is True
    assert all(row['same_ordered_actions'] and row['same_full_stow'] for row in comparison['pairs'])
    # Even contradictory metadata cannot qualify an explicitly unverified arm.
    first = dict(artifact_id='frozen', rows=rows)
    unverified = dict(first, unverified_load=True, loaded_threshold_met=True)
    assert reproduce.compare_arms(first, unverified)['status'] == 'pending'
