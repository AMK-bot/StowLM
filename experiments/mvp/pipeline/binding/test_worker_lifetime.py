"""Real native reusable transport, with table probes instead of numerical work."""
from contextlib import contextmanager
import importlib
import os
from pathlib import Path
from time import monotonic

import pytest

from experiments.mvp.pipeline.binding.admission import wait_for_capacity
from experiments.mvp.pipeline.binding import hooks
from experiments.mvp.pipeline.binding.test_hooks import fixture_instance
from experiments.mvp.pipeline.binding.test_process_wait import subprocess_guard
from experiments.mvp.reader.occupancy import apply_reader_constraint
from llm.instructions.schema import CloseBay
from stowage.solver import ballast_deadline as native


def _mask_probe(instance):
    """Runs inside the unchanged native receive/reuse loop; no solver/checker."""
    from experiments.mvp.pipeline.binding.hooks import binding_active
    from experiments.mvp.pipeline.binding.runtime import runtime_cache_scope
    import stowage.feasibility._engine as engine
    with runtime_cache_scope():
        table = engine.build_tables(instance)
        box = instance.load_list[0]
        return dict(pid=os.getpid(), active=binding_active(),
                    bay1=bool(table.static[table.box_index[box], table.slot_index['0010102']]),
                    bay3=bool(table.static[table.box_index[box], table.slot_index['0030102']]))


def _call(instance):
    # pytest may collect this file as binding.test_worker_lifetime. Send the
    # canonical module spelling so an ordinary unbound worker can import it.
    function = importlib.import_module(
        'experiments.mvp.pipeline.binding.test_worker_lifetime')._mask_probe
    return native.run_with_deadline(function, args=(instance,), deadline=monotonic()+45.)


@contextmanager
def _test_owned_slot():
    """Preserve pre-existing pytest/native handles; retire only our own handles."""
    with native._lock:
        previous = native._process, native._connection
        native._process = native._connection = None
    owned = []
    try:
        yield owned
    finally:
        try:
            if native._process is not None:
                # The slot began empty and belongs exclusively to this test.
                # Record even a process whose probe raised before _call could
                # return, so a failed regression does not strand its own child.
                if not any(native._process is process for process in owned):
                    owned.append(native._process)
                native.shutdown_worker()  # This exact slot was created by this test.
        finally:
            with native._lock:
                native._process, native._connection = previous


def _instance():
    instance = fixture_instance()
    return instance.model_copy(update={'containers': [box.model_copy(update={'dg': None})
                                                     for box in instance.containers]})


@subprocess_guard(timeout_s=60)
def test_consecutive_bound_scopes_retire_their_workers_and_preserve_external_unbound_worker(monkeypatch):
    wait_for_capacity()
    instance = _instance()
    rule_a = apply_reader_constraint(instance, CloseBay(type='close_bay', bay=1))
    rule_b = apply_reader_constraint(instance, CloseBay(type='close_bay', bay=3))
    paths = []
    original_temp = hooks.tempfile.mkstemp
    def record_temp(*args, **kwargs):
        fd, name = original_temp(*args, **kwargs)
        if kwargs.get('prefix') == '.binding-worker-':
            paths.append(Path(name))
        return fd, name
    monkeypatch.setattr(hooks.tempfile, 'mkstemp', record_temp)
    with _test_owned_slot() as owned:
        unbound_before = _call(instance)
        external, external_connection = native._process, native._connection
        owned.append(external)
        assert unbound_before['bay1'] and unbound_before['bay3'] and not unbound_before['active']
        with hooks.binding_scope(instance, rule_a) as audit_a:
            first_a = _call(instance)
            second_a = _call(instance)
            assert first_a == second_a  # The native interface really reuses it.
            assert first_a['active'] and not first_a['bay1'] and first_a['bay3']
            assert first_a['pid'] != unbound_before['pid']
        assert native._process is external and native._connection is external_connection
        assert external.is_alive()
        with hooks.binding_scope(instance, rule_b) as audit_b:
            first_b = _call(instance)
            assert first_b['active'] and first_b['bay1'] and not first_b['bay3']
            assert first_b['pid'] not in (first_a['pid'], unbound_before['pid'])
        assert native._process is external and native._connection is external_connection
        unbound_after = _call(instance)
        assert unbound_after == unbound_before
        for audit, message in ((audit_a, first_a), (audit_b, first_b)):
            assert any(row['pid'] == message['pid'] and row['exited']
                       for row in audit['reusable_workers_retired'])
            assert any(row['pid'] == message['pid'] and row['event'] == 'worker_table_bound'
                       for row in audit['worker_events'])
            assert audit['cleanup_errors'] == []
    assert len(paths) == 2 and all(not path.exists() for path in paths)
    assert not hooks.binding_active()


@subprocess_guard(timeout_s=60)
def test_exception_retires_owned_reusable_worker_before_deleting_audit_path(monkeypatch):
    wait_for_capacity()
    instance = _instance()
    rule = apply_reader_constraint(instance, CloseBay(type='close_bay', bay=1))
    paths = []
    original_temp = hooks.tempfile.mkstemp
    def record_temp(*args, **kwargs):
        fd, name = original_temp(*args, **kwargs)
        if kwargs.get('prefix') == '.binding-worker-':
            paths.append(Path(name))
        return fd, name
    monkeypatch.setattr(hooks.tempfile, 'mkstemp', record_temp)
    with _test_owned_slot() as owned:
        with pytest.raises(RuntimeError, match='intentional scope failure'):
            with hooks.binding_scope(instance, rule) as audit:
                message = _call(instance)
                raise RuntimeError('intentional scope failure')
        assert native._process is None and native._connection is None
        assert any(row['pid'] == message['pid'] and row['exited']
                   for row in audit['reusable_workers_retired'])
        unbound = _call(instance)
        owned.append(native._process)
        assert not unbound['active'] and unbound['bay1'] and unbound['bay3']
    assert paths and all(not path.exists() for path in paths)
    assert not hooks.binding_active()


def test_busy_external_worker_slot_is_not_detached_or_stopped():
    instance = _instance()
    previous = native._process, native._connection
    assert native._lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match='native ballast worker is busy'):
            with hooks.binding_scope(instance):
                pytest.fail('busy native slot was admitted')
        assert (native._process, native._connection) == previous
    finally:
        native._lock.release()
    assert not hooks.binding_active()
