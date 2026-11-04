"""Offline admission and timeout configuration regressions for public clones."""
from dataclasses import asdict
import importlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from . import admission
from .admission import AdmissionUnavailable, call_admitted, wait_for_capacity
from .profiles import budget_profile, engine_kwargs, get_profile


def test_missing_state_waits_until_real_capacity(tmp_path):
    path = tmp_path / 'core-state.json'
    waits = []

    def update(seconds):
        waits.append(seconds)
        path.write_text(json.dumps({'mask': 255, 'load1': 12., 't': 1000}))

    result = wait_for_capacity(path, sleep=update, now=lambda: 1001.)
    assert waits == [2.]
    assert result['enabled_cores'] == 8 and result['load1'] == 12.
    assert result['reading_age_s'] == 1.


def test_unreadable_state_waits_until_real_capacity(monkeypatch, tmp_path):
    path = tmp_path / 'core-state.json'
    waits = []
    read_text = Path.read_text
    def denied(_):
        raise PermissionError('synthetic denied')

    monkeypatch.setattr(Path, 'read_text', denied)

    def update(seconds):
        waits.append(seconds)
        path.write_text(json.dumps({'mask': 255, 'load1': 12., 't': 1000}))
        monkeypatch.setattr(Path, 'read_text', read_text)

    result = wait_for_capacity(path, sleep=update, now=lambda: 1001.)
    assert waits == [2.]
    assert result['enabled_cores'] == 8 and result['load1'] == 12.
    assert result['reading_age_s'] == 1.


def test_busy_stale_invalid_and_future_readings_wait_until_real_capacity(tmp_path):
    path = tmp_path / 'core-state.json'
    updates = iter([
        {'mask': 255, 'load1': 46., 't': 1000},
        {'mask': 127, 'load1': 4., 't': 1000},
        {'mask': 255, 'load1': 4., 't': 1100},
        {'mask': 255, 'load1': float('nan'), 't': 1000},
        {'mask': 255, 'load1': -1., 't': 1000},
        {'mask': 255.5, 'load1': 4., 't': 1000},
        {'mask': '0xff', 'load1': 4., 't': 1000},
    ])
    waits = []
    path.write_text(json.dumps({'mask': 255, 'load1': 2., 't': 0}))

    def sleep(seconds):
        waits.append(seconds)
        path.write_text(json.dumps(next(updates)))

    result = wait_for_capacity(path, sleep=sleep, now=lambda: 1001.)
    assert len(waits) == 7
    assert result['enabled_cores'] == 8 and result['load1'] == 4.
    assert result['reading_age_s'] == 1.


@pytest.mark.parametrize('malformed', [b'{', b'\xff'])
def test_temporarily_malformed_state_waits(tmp_path, malformed):
    path = tmp_path / 'core-state.json'
    path.write_bytes(malformed)
    waits = []

    def update(seconds):
        waits.append(seconds)
        path.write_text(json.dumps({'mask': 255, 'load1': 45., 't': 1000}))

    assert wait_for_capacity(path, sleep=update, now=lambda: 1001.)['load1'] == 45.
    assert len(waits) == 1


@pytest.mark.parametrize('raises', [False, True])
def test_admitted_binding_reuses_real_reading_and_restores_hooks(monkeypatch, raises):
    original = lambda: pytest.fail('Binding must not queue inside engine timeout')
    adapter = SimpleNamespace(wait_for_capacity=original)
    allocation = SimpleNamespace(wait_for_capacity=original)
    modules = {'adapter': adapter, 'allocation': allocation,
               'runtime': SimpleNamespace(HERE=Path('binding'), ROOT=Path('binding/runtime-cache')),
               'hooks': SimpleNamespace(tempfile=tempfile)}
    monkeypatch.setattr(admission.importlib, 'import_module',
                        lambda name: modules[name.rsplit('.', 1)[-1]])
    reading = dict(mask=255, enabled_cores=8, load1=20., reading_age_s=1., waited_s=300.)

    def engine(*, on_incumbent):
        assert adapter.wait_for_capacity() == dict(reading, runner_pre_admitted=True)
        assert allocation.wait_for_capacity() == adapter.wait_for_capacity()
        on_incumbent({'synthetic': True})
        if raises:
            raise ValueError('synthetic engine failure')
        return None, {'admission': adapter.wait_for_capacity()}

    events = []
    if raises:
        with pytest.raises(ValueError, match='synthetic engine failure'):
            call_admitted(engine, admission=reading, on_incumbent=events.append)
    else:
        _, evidence = call_admitted(engine, admission=reading, on_incumbent=events.append)
        assert evidence['admission']['waited_s'] == 300.
        assert evidence['admission']['runner_pre_admitted'] is True
    assert events == [{'synthetic': True}]
    assert adapter.wait_for_capacity is original and allocation.wait_for_capacity is original


def _runtime_worker_report(output):
    """Probe spawned binding scope paths without invoking an engine/checker."""
    from experiments.mvp.pipeline.binding import hooks, runtime
    from stowage.linear import cache
    from stowage.checker import stability_tables
    fd, path = hooks.tempfile.mkstemp(prefix='.runner-fence-test-', dir=Path(hooks.__file__).parent)
    os.close(fd)
    Path(path).unlink()
    output.put(dict(root=str(runtime.ROOT), here=str(runtime.HERE),
                    audit=path, linear=str(cache._DEFAULT_ROOT),
                    stability=str(stability_tables.CACHE_DIR),
                    vessel=os.environ['STOWAGE_VESSEL_CACHE_DIR'],
                    active=hooks.binding_active()))


def test_binding_cache_and_audit_overrides_restore_after_admitted_call(monkeypatch, tmp_path):
    from multiprocessing.process import BaseProcess
    from experiments.mvp.pipeline.binding import hooks, runtime
    from stowage.linear import cache
    from stowage.checker import stability_tables
    root = tmp_path / 'runner-cache'
    monkeypatch.setattr(admission, 'CACHE_ROOT', root)
    original = (runtime.HERE, runtime.ROOT, hooks.tempfile, BaseProcess.start,
                cache._DEFAULT_ROOT, stability_tables.CACHE_DIR)
    reading = dict(mask=255, enabled_cores=8, load1=20., reading_age_s=1., waited_s=0.)
    paths = []

    def engine():
        with runtime.runtime_cache_scope():
            assert runtime.ROOT == root
            assert Path(cache._DEFAULT_ROOT).is_relative_to(root)
            assert Path(stability_tables.CACHE_DIR).is_relative_to(root)
            assert Path(os.environ['STOWAGE_VESSEL_CACHE_DIR']).is_relative_to(root)
            fd, path = hooks.tempfile.mkstemp(prefix='.runner-fence-test-', dir=Path(hooks.__file__).parent)
            os.close(fd)
            paths.append(Path(path))
            Path(path).unlink()
        raise ValueError('synthetic engine failure')

    with pytest.raises(ValueError, match='synthetic engine failure'):
        call_admitted(engine, admission=reading)
    assert paths and all(path.is_relative_to(root / 'audits') for path in paths)
    assert (runtime.HERE, runtime.ROOT, hooks.tempfile, BaseProcess.start,
            cache._DEFAULT_ROOT, stability_tables.CACHE_DIR) == original


@pytest.mark.parametrize('method', ['fork', 'spawn'])
def test_binding_descendant_cache_and_audit_paths_stay_in_runner(monkeypatch, tmp_path, method):
    import multiprocessing
    from experiments.mvp.pipeline.binding import hooks, runtime
    root = tmp_path / 'runner-cache'
    monkeypatch.setattr(admission, 'CACHE_ROOT', root)
    context = multiprocessing.get_context(method)
    output = context.Queue()
    target = importlib.import_module(
        'experiments.mvp.pipeline.runner.test_admission')._runtime_worker_report
    original_root = runtime.ROOT
    reading = dict(mask=255, enabled_cores=8, load1=20., reading_age_s=1., waited_s=0.)

    def engine():
        with hooks.binding_scope(SimpleNamespace(id='synthetic')) as audit:
            worker = context.Process(target=target, args=(output,))
            worker.start()
            try:
                result = output.get(timeout=30)
                worker.join(timeout=10)
                assert worker.exitcode == 0
                return result
            finally:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=10)

    try:
        result = call_admitted(engine, admission=reading)
    finally:
        output.close()
        output.join_thread()
    assert result['active'] is True
    for key in ('root', 'linear', 'stability', 'vessel', 'audit'):
        assert Path(result[key]).is_relative_to(root), (method, key, result)
    assert runtime.ROOT == original_root
    assert not list((root / 'audits').iterdir())


def test_call_admitted_rejects_missing_real_reading():
    with pytest.raises(AdmissionUnavailable, match='real fleet admission'):
        call_admitted(lambda: pytest.fail('must not execute'), admission={})


def test_provisional_live_cap_preserves_recorded_demo_and_budgets(monkeypatch, tmp_path):
    from . import profiles
    monkeypatch.setattr(profiles, 'CALIBRATION', tmp_path / 'no-measurements-yet.json')
    source = json.loads((Path(__file__).parent / 'fixtures/cli-measurement.json').read_text())
    profile = get_profile('live')
    assert profile['engine_timeout_s'] == 720.
    assert profile['engine_timeout_s'] > source['wall_s'] > source['first_incumbent_s']
    assert profile['measured_demo']['whole_run_s'] == source['wall_s']
    assert profile['measured_demo']['first_incumbent_s'] == source['first_incumbent_s']
    assert profile['target_wall_s'] == 180. and profile['pending_measurement'] is True
    requested = engine_kwargs(budget_profile(180.))
    actual = engine_kwargs(profile)
    assert {key: value for key, value in actual.items() if key != 'config'} == {
        key: value for key, value in requested.items() if key != 'config'}
    assert asdict(actual['config']) == dict(asdict(requested['config']), peel_proposal_s=70.)
    assert profile['peel_budget_choice']['measurement'] == 'projection'
    assert get_profile('standard')['engine_timeout_s'] == 900.
