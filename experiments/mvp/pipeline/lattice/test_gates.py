from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.lattice import gates
from stowage.model.fixtures import build_instance
from stowage.model import MovePlan


def frame(step, **changes):
    return dict(step=step, stability_passed=True, strength_passed=True, error=None, **changes)


def test_gauges_require_every_frame_not_selected_keyframes():
    assert gates.gauge_verdict({'frames': [frame(0), frame(1), frame(2)]}, 2)['passed']
    assert not gates.gauge_verdict({'frames': [frame(0), frame(2)]}, 2)['passed']


@pytest.mark.parametrize('bad', [dict(strength_passed=False), dict(stability_passed=False),
                               dict(stability_passed=None), dict(error='not evaluated')])
def test_outside_or_missing_gauge_never_passes(bad):
    value = frame(1) | bad
    assert not gates.gauge_verdict({'frames': [frame(0), value]}, 1)['passed']


def test_frames_requests_all_lifts_even_above_app_keyframe_cap(monkeypatch):
    from app import stage_physics
    captured = {}
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    monkeypatch.setattr(stage_physics, 'compute_physics', lambda i, p, **kw: captured.update(kw) or {})
    gates._frames(None, NS(moves=[None] * 100))
    assert captured['frames'][:3] == [0, 99, 100] and captured['workers'] == 1
    assert sorted(captured['frames']) == list(range(101))
    assert callable(captured['on_frame'])


@pytest.mark.parametrize('n_lifts', [0, 1, 2, 10, 122])
def test_gauge_frame_order_covers_each_prefix_once(n_lifts):
    order = gates.gauge_frame_order(n_lifts)
    assert sorted(order) == list(range(n_lifts + 1))
    assert len(order) == len(set(order))
    if n_lifts >= 2:
        assert order[:3] == [0, n_lifts - 1, n_lifts]


@pytest.mark.parametrize('bad', [dict(strength_passed=False), dict(stability_passed=False),
                               dict(stability_passed=None), dict(error='not evaluated')])
def test_failed_frame_aborts_remaining_gauges_and_cannot_pass(monkeypatch, bad):
    from app import stage_physics
    measured = []
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    monkeypatch.setattr(stage_physics, 'physics_header', lambda *args: {'keyframes': 'all'})
    def compute(i, p, *, frames, workers, on_frame):
        assert frames == gates.gauge_frame_order(122) and workers == 1
        for k in frames:
            value = frame(k) | (bad if k == 10 else {})
            measured.append(k)
            on_frame(k, value)
        pytest.fail('gauge sweep continued after a rejected frame')
    monkeypatch.setattr(stage_physics, 'compute_physics', compute)
    physics = gates._frames(None, NS(moves=[None] * 122))
    assert measured == [0, 121, 122, *range(1, 11)]
    assert physics['aborted'] is True and physics['abort_step'] == 10
    verdict = gates.gauge_verdict(physics, 122)
    assert not verdict['passed'] and not verdict['all_frames']


def test_successful_gauges_still_measure_every_frame(monkeypatch):
    from app import stage_physics
    measured = []
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    def compute(i, p, *, frames, workers, on_frame):
        for k in frames:
            value = frame(k)
            measured.append(value)
            on_frame(k, value)
        return dict(frames=sorted(measured, key=lambda f: f['step']), n_lifts=len(p.moves))
    monkeypatch.setattr(stage_physics, 'compute_physics', compute)
    physics = gates._frames(None, NS(moves=[None] * 122))
    assert [f['step'] for f in measured] == gates.gauge_frame_order(122)
    assert [f['step'] for f in physics['frames']] == list(range(123))
    assert gates.gauge_verdict(physics, 122)['passed'] is True
    assert physics['plot_optimization'] == 'withheld hatch-off plot skipped; native parts unchanged'


def test_bad_predeparture_frame_rejects_before_remaining_prefixes(monkeypatch):
    from app import stage_physics
    measured = []
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    monkeypatch.setattr(stage_physics, 'physics_header', lambda *args: {})
    def compute(i, p, *, frames, workers, on_frame):
        for k in frames:
            measured.append(k)
            on_frame(k, frame(k) | ({'stability_passed': False} if k == 121 else {}))
        pytest.fail('bad predeparture frame did not abort')
    monkeypatch.setattr(stage_physics, 'compute_physics', compute)
    physics = gates._frames(None, NS(moves=[None] * 122))
    assert measured == [0, 121]
    assert physics['abort_step'] == 121
    assert not gates.gauge_verdict(physics, 122)['passed']


@pytest.mark.parametrize('aborted', [False, True])
def test_gauge_sweep_rejects_midrun_physics_source_drift(monkeypatch, aborted):
    from app import stage_physics
    drift = iter([None, 'physics source changed during sweep'])
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: next(drift))
    monkeypatch.setattr(stage_physics, 'physics_header', lambda *args: {})
    def compute(i, p, *, frames, workers, on_frame):
        value = frame(0) | ({'strength_passed': False} if aborted else {})
        on_frame(0, value)
        return dict(frames=[value], n_lifts=0)
    monkeypatch.setattr(stage_physics, 'compute_physics', compute)
    with pytest.raises(ValueError, match='physics source changed during sweep'):
        gates._frames(None, NS(moves=[]))


def test_gauge_sweep_uses_and_restores_discarded_plot_scope(monkeypatch):
    from contextlib import contextmanager
    from app import stage_physics
    from experiments.mvp.pipeline.lattice import runtime
    active = []
    @contextmanager
    def scope():
        active.append(True)
        try:
            yield
        finally:
            active.pop()
    monkeypatch.setattr(runtime, 'discarded_plot_scope', scope)
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    def compute(*args, **kwargs):
        assert active == [True]
        return {'frames': []}
    monkeypatch.setattr(stage_physics, 'compute_physics', compute)
    gates._frames(None, NS(moves=[]))
    assert active == []


def test_gate_rechecks_original_and_each_rule_with_full_checker_overruling_cache(monkeypatch):
    instance = build_instance()
    plan = MovePlan(id='lattice-test', instance_id=instance.id, call_index=instance.call_index, moves=[])
    report = NS(passed=True, count=NS(weighted_nonproductive=7., total_lifts_now=3),
                model_dump=lambda **_: {'passed': True})
    seen = []
    monkeypatch.setattr(gates, 'runtime_cache_scope', nullcontext)
    monkeypatch.setattr(gates, 'engine_identity', lambda: {'passed': True})
    monkeypatch.setattr(gates, '_check', lambda i, p: seen.append((i, p)) or report)
    monkeypatch.setattr(gates, '_clean', lambda *args: None)
    monkeypatch.setattr(gates, '_frames', lambda *args: {'frames': [frame(0)]})
    rules_seen = []
    monkeypatch.setattr(gates, '_violations', lambda i, a, p: rules_seen.append(a.type) or ['rule violated'])
    audit = gates.gate_plan(instance, plan, [{'type': 'close_bay', 'bay': 1}], admit=False)
    assert seen == [(instance, plan)]
    assert rules_seen == ['close_bay']
    assert audit['lifts'] == 7. and audit['total_lifts_now'] == 3
    assert not audit['passed'] and not audit['rules'][0]['passed']


@pytest.mark.parametrize('field,value', [('weighted_nonproductive', float('nan')),
                                       ('total_lifts_now', float('inf'))])
def test_gate_rejects_nonfinite_checker_counts(monkeypatch, field, value):
    instance = build_instance()
    plan = MovePlan(id='lattice-test', instance_id=instance.id, call_index=instance.call_index, moves=[])
    count = NS(weighted_nonproductive=1., total_lifts_now=1)
    setattr(count, field, value)
    monkeypatch.setattr(gates, 'runtime_cache_scope', nullcontext)
    monkeypatch.setattr(gates, 'engine_identity', lambda: {'passed': True})
    monkeypatch.setattr(gates, '_check', lambda *args: NS(count=count))
    monkeypatch.setattr(gates, '_clean', lambda *args: None)
    monkeypatch.setattr(gates, '_frames', lambda *args: pytest.fail('invalid count reached expensive gauges'))
    with pytest.raises(ValueError, match='nonfinite'):
        gates.gate_plan(instance, plan, [], admit=False)


def test_binding_runtime_isolation_restores_and_keeps_tempfile_local():
    import tempfile
    from pathlib import Path
    from experiments.mvp.pipeline.binding import hooks, runtime
    from experiments.mvp.pipeline.lattice.runtime import HERE, isolated_binding, worker_entry
    original = runtime.HERE, runtime.ROOT, hooks.tempfile, hooks._worker_entry
    with isolated_binding():
        assert runtime.HERE == HERE
        assert hooks.tempfile is not tempfile
        assert hooks._worker_entry is worker_entry
        descriptor, name = hooks.tempfile.mkstemp(dir=Path('/tmp'))
        import os
        os.close(descriptor)
        assert Path(name).parent == HERE / 'runtime-cache'
        Path(name).unlink()
    assert (runtime.HERE, runtime.ROOT, hooks.tempfile, hooks._worker_entry) == original


def _runtime_probe(queue):
    import os
    from experiments.mvp.pipeline.binding import runtime, hooks
    queue.put(dict(root=str(runtime.ROOT), vessel=os.environ.get('STOWAGE_VESSEL_CACHE_DIR'),
                   active=hooks.binding_active()))


def test_spawned_binding_worker_inherits_lattice_fence():
    import multiprocessing
    from experiments.mvp.pipeline.binding.hooks import binding_scope
    from experiments.mvp.pipeline.lattice.runtime import HERE, isolated_binding
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()
    instance = build_instance()
    with isolated_binding(), binding_scope(instance):
        process = context.Process(target=_runtime_probe, args=(queue,))
        process.start()
        try:
            result = queue.get(timeout=30)
            process.join(timeout=30)
            assert process.exitcode == 0
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            queue.close()
            queue.join_thread()
    assert result == dict(root=str(HERE / 'runtime-cache'),
                          vessel=str(HERE / 'runtime-cache/vessels'), active=True)


def test_real_app_epoch_survives_late_binding_import_and_pure_cache_scope():
    import subprocess
    import sys
    from pathlib import Path
    from textwrap import dedent
    script = dedent('''
        import importlib
        import os
        from app.stage_physics import physics_drift
        assert physics_drift() is None
        from experiments.mvp.pipeline.lattice import gates
        thread_keys = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS')
        def environment():
            return {key: value for key, value in os.environ.items()
                    if key.startswith('STOWAGE_') or key in thread_keys}
        before = environment()
        assert gates.engine_identity()['passed'] is True
        assert environment() == before
        assert physics_drift() is None
        from stowage.vessel import fleet_cache
        original_vessel_dir = fleet_cache._cache_dir
        targets = (
            ('stowage.checker.stability_tables', 'CACHE_DIR'),
            ('stowage.checker.stability_sounding', 'CACHE_DIRECTORY'),
            ('stowage.checker.stability_liquid', 'CACHE_DIRECTORY'),
            ('stowage.checker.stability_liquid_tables', 'CACHE_DIRECTORY'),
            ('stowage.linear.cache', '_DEFAULT_ROOT'),
        )
        originals = {(name, attr): getattr(importlib.import_module(name), attr) for name, attr in targets}
        with gates.runtime_cache_scope():
            assert environment() == before
            assert physics_drift() is None
            root = (gates.HERE / 'runtime-cache').resolve()
            assert fleet_cache._cache_dir().resolve().is_relative_to(root)
            for name, attr in targets:
                assert getattr(importlib.import_module(name), attr).resolve().is_relative_to(root)
            tables = importlib.import_module('stowage.checker.stability_tables')
            for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
                assert getattr(tables, name).__kwdefaults__['cache_dir'].resolve().is_relative_to(root)
        assert environment() == before
        assert physics_drift() is None
        assert fleet_cache._cache_dir is original_vessel_dir
        for (name, attr), value in originals.items():
            assert getattr(importlib.import_module(name), attr) == value
        print('epoch-stable; every cache target fenced')
    ''')
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[4],
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'epoch-stable; every cache target fenced' in result.stdout


def test_concurrent_gate_scopes_keep_cache_paths_fenced_until_each_check_finishes():
    import importlib
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from stowage.vessel import fleet_cache
    targets = (
        ('stowage.checker.stability_tables', 'CACHE_DIR'),
        ('stowage.checker.stability_sounding', 'CACHE_DIRECTORY'),
        ('stowage.checker.stability_liquid', 'CACHE_DIRECTORY'),
        ('stowage.checker.stability_liquid_tables', 'CACHE_DIRECTORY'),
        ('stowage.linear.cache', '_DEFAULT_ROOT'),
    )
    originals = {(name, attr): getattr(importlib.import_module(name), attr) for name, attr in targets}
    vessel_dir = fleet_cache._cache_dir
    entered_a, attempted_b, entered_b, release_a = [threading.Event() for _ in range(4)]
    root = (gates.HERE / 'runtime-cache').resolve()

    def fenced():
        assert fleet_cache._cache_dir().resolve().is_relative_to(root)
        for name, attr in targets:
            assert getattr(importlib.import_module(name), attr).resolve().is_relative_to(root)

    def first_check():
        with gates.runtime_cache_scope():
            with gates.runtime_cache_scope():
                fenced()
            entered_a.set()
            assert release_a.wait(10)
            fenced()

    def second_check():
        attempted_b.set()
        with pytest.raises(RuntimeError, match='second checker failed'):
            with gates.runtime_cache_scope():
                entered_b.set()
                fenced()
                raise RuntimeError('second checker failed')

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_check)
        try:
            assert entered_a.wait(10)
            second = pool.submit(second_check)
            assert attempted_b.wait(10)
            assert not entered_b.wait(.1), 'a concurrent checker entered before the active scope finished'
        finally:
            release_a.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert entered_b.is_set()
    assert fleet_cache._cache_dir is vessel_dir
    for (name, attr), value in originals.items():
        assert getattr(importlib.import_module(name), attr) == value


@pytest.mark.parametrize('failed', [False, True])
def test_native_frame_checkpoint_binds_inputs_epoch_and_includes_failed_frame(tmp_path, monkeypatch, failed):
    import json
    from app import stage_physics
    from stowage.model.moves import Load
    instance = build_instance()
    move = Load(call_index=instance.call_index, container_id=instance.load_list[0], src='A-001-01-01', dst='0010102')
    plan = MovePlan(id='checkpoint-test', instance_id=instance.id, call_index=instance.call_index, moves=[move] * 4)
    checkpoint = tmp_path / 'plan.frames.json'
    snapshots = []
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    monkeypatch.setattr(stage_physics, 'physics_epoch', lambda: 'test-epoch')
    monkeypatch.setattr(stage_physics, 'physics_header', lambda *args: {})
    def compute(i, p, *, frames, workers, on_frame):
        measured = []
        for k in frames:
            value = frame(k) | ({'stability_passed': False} if failed and k == 1 else {})
            measured.append(value)
            on_frame(k, value)
            snapshots.append(json.loads(checkpoint.read_text()))
        return dict(frames=sorted(measured, key=lambda f: f['step']), n_lifts=len(p.moves))
    monkeypatch.setattr(stage_physics, 'compute_physics', compute)
    physics = gates._frames(instance, plan, frame_checkpoint_path=checkpoint)
    saved = json.loads(checkpoint.read_text())
    for key, value in stage_physics.input_digests(instance, plan).items():
        assert saved[key] == value
    assert saved['physics_epoch'] == 'test-epoch' and saved['n_lifts'] == 4
    assert saved['schema_version'] == 'stowlm.lattice-frames/1'
    assert not list(tmp_path.glob('*.tmp'))
    if failed:
        assert saved['aborted'] is True and saved['last_step'] == 1
        assert not saved['complete'] and not saved['passed']
        assert next(f for f in saved['frames'] if f['step'] == 1)['stability_passed'] is False
        assert len(saved['frames']) == 4 and physics['aborted'] is True
    else:
        assert [len(row['frames']) for row in snapshots] == [1, 2, 3, 4, 5]
        assert [f['step'] for f in saved['frames']] == list(range(5))
        assert saved['complete'] is True and saved['passed'] is True
        assert saved['aborted'] is False and saved['last_step'] == 2
        assert gates.gauge_verdict(physics, len(plan.moves))['passed'] is True
