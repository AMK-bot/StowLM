"""Cheap concurrency tests. Native engine/checker calls are never started."""
import json
import threading
import time

import pytest

from experiments.mvp.pipeline.copilot.jobs import JobManager, native_guard


def finished(manager, job_id):
    deadline = time.monotonic() + 2.
    while time.monotonic() < deadline:
        state = manager.wait(job_id, timeout_s=.1)
        if state['status'] != 'running':
            return state
    pytest.fail('cheap background operation did not complete')


def test_start_and_poll_are_responsive_while_operation_waits():
    manager = JobManager()
    started, release = threading.Event(), threading.Event()

    def operation():
        started.set()
        release.wait(2.)
        return {'synthetic': True}

    began = time.monotonic()
    initial = manager.start(operation, identity={'reading': 'current'})
    assert initial['status'] == 'running'
    assert time.monotonic() - began < .25
    try:
        assert started.wait(1.)
        began = time.monotonic()
        running = manager.poll(initial['job_id'])
        assert time.monotonic() - began < .1
        assert running['status'] == 'running'
        assert running['native_running'] is True
        assert running['result_available'] is False
        assert running['passed'] is False
        assert running['elapsed_label'] == 'measured'
        assert manager.busy()
        later = manager.wait(initial['job_id'], timeout_s=.02)
        assert later['elapsed_s'] >= running['elapsed_s']
    finally:
        release.set()
    assert finished(manager, initial['job_id'])['status'] == 'completed'
    assert manager.result(initial['job_id'], identity={'reading': 'current'}) == {'synthetic': True}


def test_snapshot_identity_and_context_are_detached_before_submission():
    manager = JobManager()
    started, release = threading.Event(), threading.Event()
    snapshot = {'cargo': ['original']}
    identity = {'reading': ['original']}
    context = {'label': ['synthetic']}

    def operation(detached):
        started.set()
        release.wait(2.)
        detached['cargo'].append('worker_only')
        return detached

    initial = manager.start(operation, snapshot=snapshot, identity=identity, context=context)
    try:
        assert started.wait(1.)
        snapshot['cargo'].append('host_only')
        identity['reading'].append('host_only')
        context['label'].append('host_only')
        state = manager.poll(initial['job_id'])
        assert state['identity'] == {'reading': ['original']}
        assert state['context'] == {'label': ['synthetic']}
    finally:
        release.set()
    finished(manager, initial['job_id'])
    assert manager.result(initial['job_id']) == {'cargo': ['original', 'worker_only']}
    assert snapshot == {'cargo': ['original', 'host_only']}


def test_all_managers_share_one_serial_daemon_dispatcher():
    first, second = JobManager(), JobManager()
    first_started, second_started, release = threading.Event(), threading.Event(), threading.Event()
    worker_metadata = []

    def one():
        worker_metadata.append((threading.get_ident(), threading.current_thread().daemon))
        first_started.set()
        release.wait(2.)
        return 'first'

    def two():
        worker_metadata.append((threading.get_ident(), threading.current_thread().daemon))
        second_started.set()
        return 'second'

    a = first.start(one)
    b = second.start(two)
    try:
        assert first_started.wait(1.)
        assert not second_started.wait(.02)
        assert second.poll(b['job_id'])['phase'] == 'queued'
    finally:
        release.set()
    assert finished(first, a['job_id'])['status'] == 'completed'
    assert finished(second, b['job_id'])['status'] == 'completed'
    assert len({row[0] for row in worker_metadata}) == 1
    assert all(row[1] for row in worker_metadata)


def test_cancelling_running_work_discards_publication_without_interrupting_it():
    manager = JobManager()
    started, release, operation_finished = threading.Event(), threading.Event(), threading.Event()

    def operation():
        started.set()
        release.wait(2.)
        operation_finished.set()
        return object()

    initial = manager.start(operation)
    try:
        assert started.wait(1.)
        cancelled = manager.cancel(initial['job_id'])
        assert cancelled['status'] == 'stale'
        assert cancelled['native_running'] is True
        assert not operation_finished.is_set()
        with pytest.raises(RuntimeError, match='fresh'):
            manager.result(initial['job_id'])
    finally:
        release.set()
    assert operation_finished.wait(1.)
    barrier = manager.start(lambda: None)
    finished(manager, barrier['job_id'])
    final = manager.poll(initial['job_id'])
    assert final['status'] == 'stale'
    assert final['phase'] == 'discarded'
    assert final['result_available'] is False


def test_cancelling_queued_work_skips_its_operation():
    manager = JobManager()
    first_started, second_started, release = threading.Event(), threading.Event(), threading.Event()

    def first():
        first_started.set()
        release.wait(2.)

    a = manager.start(first)
    try:
        assert first_started.wait(1.)
        b = manager.start(lambda: second_started.set())
        assert manager.cancel(b['job_id'])['status'] == 'stale'
    finally:
        release.set()
    finished(manager, a['job_id'])
    barrier = manager.start(lambda: None)
    finished(manager, barrier['job_id'])
    assert not second_started.is_set()


@pytest.mark.parametrize('finished_before_change', [False, True])
def test_identity_change_invalidates_queued_running_or_completed_results(finished_before_change):
    manager = JobManager()
    release = threading.Event()
    initial = manager.start(lambda: release.wait(1.), identity={'generation': 1})
    if finished_before_change:
        release.set()
        assert finished(manager, initial['job_id'])['status'] == 'completed'
    try:
        state = manager.poll(initial['job_id'], identity={'generation': 2})
        assert state['status'] == 'stale'
        assert state['stale_reason'] == 'identity_changed'
        with pytest.raises(RuntimeError, match='fresh'):
            manager.result(initial['job_id'], identity={'generation': 1})
    finally:
        release.set()


def test_poll_does_not_serialize_or_expose_native_payloads():
    class NativePayload:
        def __repr__(self):
            raise AssertionError('native payload must not be formatted')

    payload = NativePayload()
    manager = JobManager()
    initial = manager.start(lambda: payload)
    state = finished(manager, initial['job_id'])
    json.dumps(state, allow_nan=False)
    assert state['result_available'] is True
    assert 'result' not in state
    assert 'plan_sha256' not in state
    assert manager.result(initial['job_id']) is payload


def test_worker_errors_are_sanitized_and_dispatcher_survives_baseexception():
    manager = JobManager()

    def bad():
        raise KeyboardInterrupt('sensitive provider body')

    initial = manager.start(bad)
    state = finished(manager, initial['job_id'])
    assert state['status'] == 'error'
    assert state['error_type'] == 'KeyboardInterrupt'
    assert 'sensitive' not in json.dumps(state)
    with pytest.raises(RuntimeError):
        manager.result(initial['job_id'])
    next_job = manager.start(lambda: 'survived')
    finished(manager, next_job['job_id'])
    assert manager.result(next_job['job_id']) == 'survived'


def test_native_guard_is_nonblocking_across_threads_and_reentrant_in_worker():
    manager = JobManager()
    started, release = threading.Event(), threading.Event()
    nested = []

    def operation():
        with native_guard() as acquired:
            nested.append(acquired)
            started.set()
            release.wait(2.)

    initial = manager.start(operation)
    try:
        assert started.wait(1.)
        began = time.monotonic()
        with native_guard() as acquired:
            assert acquired is False
        assert time.monotonic() - began < .1
        assert nested == [True]
    finally:
        release.set()
    finished(manager, initial['job_id'])


def test_guard_can_hold_queued_native_work_without_blocking_start_or_poll():
    manager = JobManager()
    executed = threading.Event()
    with native_guard() as acquired:
        assert acquired
        initial = manager.start(lambda: executed.set())
        assert manager.poll(initial['job_id'])['phase'] == 'queued'
        assert not executed.wait(.02)
    finished(manager, initial['job_id'])
    assert executed.is_set()


@pytest.mark.parametrize('timeout', [-1, .251, float('inf'), float('nan'), True, 'slow'])
def test_host_wait_is_strictly_bounded(timeout):
    with pytest.raises(ValueError, match='quarter'):
        JobManager().wait('missing', timeout_s=timeout)


def test_unknown_jobs_and_explicit_release():
    manager = JobManager()
    assert manager.poll('missing')['status'] == 'not_found'
    assert manager.cancel('missing')['status'] == 'not_found'
    assert manager.wait('missing', timeout_s=0)['status'] == 'not_found'
    with pytest.raises(KeyError):
        manager.result('missing')
    initial = manager.start(lambda: None)
    finished(manager, initial['job_id'])
    assert manager.forget(initial['job_id'])
    assert manager.poll(initial['job_id'])['status'] == 'not_found'


def test_submission_rejects_invalid_metadata_without_starting_work():
    manager = JobManager()
    with pytest.raises(TypeError):
        manager.start(None)
    with pytest.raises(TypeError):
        manager.start(lambda: None, context=[])
    with pytest.raises(ValueError):
        manager.start(lambda: None, identity={'invalid': float('nan')})
