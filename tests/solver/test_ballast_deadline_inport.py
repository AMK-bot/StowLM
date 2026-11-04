"""The finisher owns the acceptance clock and only its evaluator's workers."""
from concurrent.futures import Future, TimeoutError
import multiprocessing
import time

import pytest

from stowage.solver import ballast_deadline as deadline


class _Process:
    def __init__(self, events, label, *, exits=True):
        self.events, self.label = events, label
        self.alive, self.exits = True, exits

    def is_alive(self):
        return self.alive

    def kill(self):
        self.events.append(('kill', self.label))
        self.alive = False

    def join(self, *, timeout):
        self.events.append(('join', self.label, timeout))
        if self.exits:
            self.alive = False


class _Executor:
    def __init__(self, events, processes, *, late_process=None):
        self.events, self._processes = events, processes
        self.late_process = late_process
        self.futures = []

    def submit(self, function, *args):
        future = Future()
        self.futures.append(future)
        return future

    def shutdown(self, *, wait, cancel_futures):
        self.events.append(('shutdown', wait, cancel_futures))
        # A synchronized shutdown observes any final process registered by a
        # concurrent submit before it clears the executor's registration field.
        if self.late_process is not None:
            self._processes[2] = self.late_process
        self._processes = None


def test_result_receives_only_remaining_acceptance_time(monkeypatch):
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: 10.)
    calls = []

    class Pending:
        def result(self, *, timeout):
            calls.append(timeout)
            raise TimeoutError

    with pytest.raises(deadline.BallastDeadline, match='acceptance'):
        deadline._DeadlineFuture(Pending(), 10.75).result()
    assert calls == [.75]
    with pytest.raises(deadline.BallastDeadline):
        deadline._DeadlineFuture(Pending(), 9.).result()
    assert calls == [.75]


def test_early_failure_draining_aborts_only_synchronized_owned_handles(monkeypatch):
    import concurrent.futures
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: 10.)
    events = []
    owned = _Process(events, 'owned', exits=False)
    late_owned = _Process(events, 'late-owned', exits=False)
    unrelated = _Process(events, 'unrelated', exits=False)
    executor = _Executor(events, {1: owned}, late_process=late_owned)
    pool = deadline._DeadlinePool(executor, 10.25)
    first = pool.submit(abs, -1)
    pool.submit(abs, -2)
    executor.futures[0].set_result('first-state-failed')
    assert first.result() == 'first-state-failed'
    waits = []

    def wait(futures, *, timeout):
        waits.append(timeout)
        assert futures == executor.futures
        return {executor.futures[0]}, {executor.futures[1]}

    monkeypatch.setattr(concurrent.futures, 'wait', wait)
    with pytest.raises(deadline.BallastDeadline, match='draining'):
        pool.shutdown()
    assert waits == [.25]
    assert events[:3] == [('shutdown', False, True), ('kill', 'owned'), ('kill', 'late-owned')]
    assert not owned.alive and not late_owned.alive
    assert unrelated.alive
    assert pool.closed
    before = list(events)
    pool.shutdown()
    assert events == before


def test_completed_work_closes_registered_workers_with_remaining_time(monkeypatch):
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: 10.)
    events = []
    process = _Process(events, 'owned')
    executor = _Executor(events, {1: process})
    pool = deadline._DeadlinePool(executor, 10.5)
    pool.submit(abs, -1)
    executor.futures[0].set_result(1)
    pool.shutdown()
    assert events == [('shutdown', False, True), ('join', 'owned', .5)]
    assert pool.closed


def test_completed_work_cannot_leave_a_worker_blocked_on_exit(monkeypatch):
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: 10.)
    events = []
    process = _Process(events, 'owned', exits=False)
    executor = _Executor(events, {1: process})
    pool = deadline._DeadlinePool(executor, 10.5)
    pool.submit(abs, -1)
    executor.futures[0].set_result(1)
    with pytest.raises(deadline.BallastDeadline, match='closing'):
        pool.shutdown()
    assert events == [('shutdown', False, True), ('join', 'owned', .5),
                      ('kill', 'owned'), ('join', 'owned', .05)]
    assert pool.closed and not process.alive


@pytest.mark.slow
def test_spawned_native_sleep_is_stopped_and_unrelated_owned_process_survives(monkeypatch):
    """Explicit reservation required: two pool workers plus one sleeping probe."""
    from concurrent.futures import ProcessPoolExecutor
    monkeypatch.setenv('OPENBLAS_NUM_THREADS', '1')
    monkeypatch.setenv('OMP_NUM_THREADS', '1')
    context = multiprocessing.get_context('spawn')
    unrelated = context.Process(target=time.sleep, args=(30,))
    unrelated.start()
    executor = ProcessPoolExecutor(max_workers=2, mp_context=context)

    class Evaluator:
        workers = 2

        def __init__(self):
            self._pool = executor

        def evaluate(self, states, *, stop_at_first_failure):
            assert stop_at_first_failure
            futures = [self._pool.submit(time.sleep, seconds) for seconds in states]
            return futures[0].result()

        def close(self):
            self._pool.shutdown(wait=True, cancel_futures=True)

    try:
        # Warm spawn/import separately from the acceptance interval.
        assert list(executor.map(abs, [-1, -2])) == [1, 2]
        owned = list(executor._processes.values())
        started = time.monotonic()
        with pytest.raises((deadline.BallastDeadline, deadline.ExactDeadline)):
            deadline.evaluate_in_port_with_deadline(Evaluator(), [30., 30.],
                                                    deadline=started+.15)
        assert time.monotonic()-started < 1.
        assert all(not process.is_alive() for process in owned)
        assert unrelated.is_alive()
    finally:
        remaining_owned = list((executor._processes or {}).values())
        executor.shutdown(wait=False, cancel_futures=True)
        # Stop only the explicitly started probe and recorded executor handles.
        for process in remaining_owned:
            if process.is_alive():
                process.kill()
            process.join(timeout=.1)
        if unrelated.is_alive():
            unrelated.kill()
        unrelated.join(timeout=1.)


def test_normal_close_joins_executor_manager_without_racing_its_waitpid(monkeypatch):
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: 10.)
    events = []
    process = _Process(events, 'owned', exits=False)
    executor = _Executor(events, {1: process})

    class Manager:
        def join(self, *, timeout):
            events.append(('manager_join', timeout))
        def is_alive(self):
            return False
    executor._executor_manager_thread = Manager()
    pool = deadline._DeadlinePool(executor, 10.5)
    pool.submit(abs, -1)
    executor.futures[0].set_result(1)
    pool.shutdown()
    assert events == [('shutdown', False, True), ('manager_join', .5)]
    assert pool.closed


def test_prepass_reuses_registered_pool_without_closing_between_chunks(monkeypatch):
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: 10.)
    events = []
    executor = _Executor(events, {})
    pool = deadline._DeadlinePool(executor, 11.)
    class Evaluator:
        workers = 2
        def __init__(self):
            self._pool = pool
        def evaluate(self, states, *, stop_at_first_failure):
            assert stop_at_first_failure
            return tuple(states)
        def close(self):
            self._pool.shutdown()
    evaluator = Evaluator()
    assert deadline.evaluate_in_port_with_deadline(evaluator, [1], deadline=12., close=False) == (1,)
    assert evaluator._pool is pool and not pool.closed and events == []
    assert deadline.evaluate_in_port_with_deadline(evaluator, [2], deadline=13., close=False) == (2,)
    assert pool.deadline == 13. and events == []
    evaluator.close()
    assert pool.closed and events == [('shutdown', False, True)]


def test_parallel_initializer_receives_heel_interval_capability(monkeypatch):
    import concurrent.futures
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: 10.)
    events, created = [], []
    def executor(**kw):
        created.append(kw)
        return _Executor(events, {})
    monkeypatch.setattr(concurrent.futures, 'ProcessPoolExecutor', executor)
    class Evaluator:
        workers = 2
        instance, context, warm_start, heel_intervals = 'instance', 'context', False, True
        _pool = None
        def evaluate(self, states, *, stop_at_first_failure):
            return 'complete'
        def close(self):
            self._pool.shutdown()
    assert deadline.evaluate_in_port_with_deadline(Evaluator(), [], deadline=11.) == 'complete'
    assert created[0]['initargs'] == ('instance', 'context', False, True)
