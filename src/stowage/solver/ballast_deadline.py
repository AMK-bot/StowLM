"""Bound ballast work even when a native numerical routine stops responding.

Cooperative clock checks cannot stop an in-progress LAPACK/HiGHS or exact hull
calculation. Run that work in a dedicated, reusable Python process instead. The
parent waits only until its monotonic deadline, then kills its own worker. A
successful worker stays warm, retaining its linear-model and hull caches. No
signals, global tracing hooks, or unrelated processes are involved.

This is process isolation, not an approximation to the physical checks. Only a
completed calculation is returned. Source: Python multiprocessing documentation,
Processes and exceptions, and threading.Event.wait.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import multiprocessing as mp
import sys
import threading
import time
from typing import Any, Callable


class BallastDeadline(TimeoutError):
    """The parent wall-clock budget expired; no completed result is available."""


class ExactDeadline(BaseException):
    """Clock expiry that broad numerical ``except Exception`` cannot suppress."""


@contextmanager
def exact_budget(deadline: float):
    """Check the clock inside Python physics calls without per-line tracing.

    Profiling hooks are local to this thread, and the previous hook is restored
    and forwarded. Compiled routines without Python events remain bounded by
    the parent process deadline. This context is intended for the worker only.
    """
    previous = sys.getprofile()
    events = 0

    def checkpoint(frame, event, arg):
        nonlocal events
        events += 1
        if events % 64 == 0 and time.monotonic() >= deadline:
            raise ExactDeadline
        if previous is not None:
            previous(frame, event, arg)

    if time.monotonic() >= deadline:
        raise ExactDeadline
    # cProfile can install a non-Python hook which cannot be forwarded by
    # calling it. Preserve that hook; parent process isolation still bounds it.
    install = previous is None or callable(previous)
    if install:
        sys.setprofile(checkpoint)
    try:
        yield
        if time.monotonic() >= deadline:
            raise ExactDeadline
    finally:
        if install:
            sys.setprofile(previous)


_lock = threading.Lock()
_process: Any = None
_connection: Any = None


def _worker(connection):
    try:
        while True:
            function, args, kwargs = connection.recv()
            try:
                result = function(*args, **kwargs)
            except BaseException as error:
                try:
                    connection.send((False, error))
                except Exception:
                    connection.send((False, RuntimeError(
                        f'Ballast worker raised {type(error).__name__}: {error}')))
            else:
                connection.send((True, result))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


def _discard_worker():
    global _process, _connection
    process, connection = _process, _connection
    _process = _connection = None
    if connection is not None:
        connection.close()
    if process is not None:
        # This handle belongs exclusively to this helper. Never discover or
        # terminate workers by process name, command pattern, or global PID scan.
        if process.is_alive():
            process.kill()
        process.join(timeout=.02)
        if not process.is_alive():
            process.close()


def shutdown_worker():
    """Release this module's idle worker (also done at interpreter exit)."""
    with _lock:
        _discard_worker()


atexit.register(shutdown_worker)


def run_with_deadline(function: Callable, args=(), kwargs=None, *, deadline: float):
    """Execute a pickleable top-level callable before an absolute monotonic time.

    Exceptions, including illegal-move ``ValueError``, propagate to the caller.
    A timeout discards the calculation and its worker; the next call starts a
    fresh worker. Concurrent callers share one worker and their time spent
    waiting for it counts against their own deadlines. Arguments and results
    must be pickleable. Child mutation of arguments is intentionally isolated.
    """
    global _process, _connection
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not _lock.acquire(timeout=max(0., remaining)):
        raise BallastDeadline('Ballast deadline expired')
    try:
        if time.monotonic() >= deadline:
            raise BallastDeadline('Ballast deadline expired')
        if _process is None or not _process.is_alive():
            _discard_worker()
            context = mp.get_context('spawn')
            parent, child = context.Pipe()
            process = context.Process(target=_worker, args=(child,),
                                      name='stowage-ballast', daemon=True)
            process.start()
            child.close()
            _process, _connection = process, parent
        connection = _connection
        completed = threading.Event()
        delivery = {}
        # Sending a large instance can block until the child finishes importing.
        # A daemon sender keeps even that wait inside the parent's clock budget.
        def send():
            try:
                connection.send((function, args, {} if kwargs is None else kwargs))
            except BaseException as error:
                delivery['error'] = error
                completed.set()
        def receive():
            try:
                delivery['result'] = connection.recv()
            except BaseException as error:
                delivery['error'] = error
            finally:
                completed.set()
        sender = threading.Thread(target=send, name='ballast-input', daemon=True)
        receiver = threading.Thread(target=receive, name='ballast-output', daemon=True)
        sender.start()
        receiver.start()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not completed.wait(timeout=remaining):
            _discard_worker()
            raise BallastDeadline('Ballast deadline expired')
        if 'error' in delivery:
            _discard_worker()
            raise delivery['error']
        succeeded, value = delivery['result']
        sender.join(timeout=0)
        if time.monotonic() >= deadline:
            _discard_worker()
            raise BallastDeadline('Ballast deadline expired')
        if succeeded:
            return value
        raise value
    except (EOFError, BrokenPipeError, OSError):
        _discard_worker()
        raise
    finally:
        _lock.release()


class _DeadlineFuture:
    """A future whose blocking result cannot outlive this acceptance phase."""

    def __init__(self, future, deadline):
        self.future, self.deadline = future, deadline

    def result(self):
        from concurrent.futures import TimeoutError
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise BallastDeadline('in-port acceptance deadline expired')
        try:
            return self.future.result(timeout=remaining)
        except TimeoutError as error:
            raise BallastDeadline('in-port acceptance deadline expired') from error

    def cancel(self):
        return self.future.cancel()


class _DeadlinePool:
    """4.2's deadline adapter for the exact pool created for 4.1's evaluator.

    4.1 owns submissions, physics and normal process creation. 4.2 owns the
    aggregate deadline, including draining speculative early-exit work. On
    expiry only this executor's registered Process handles are stopped. This
    intentionally depends on CPython ProcessPoolExecutor._processes; it never
    discovers processes by PID scans, command lines or names.
    """

    def __init__(self, executor, deadline):
        self.executor, self.deadline = executor, deadline
        self.futures = []
        self.closed = False

    def submit(self, function, *args):
        if time.monotonic() >= self.deadline:
            raise BallastDeadline('in-port acceptance deadline expired')
        future = self.executor.submit(function, *args)
        self.futures.append(future)
        return _DeadlineFuture(future, self.deadline)

    def _abort(self):
        # Retain this exact registration table before shutdown clears the
        # executor field. Shutdown synchronizes with submit and prevents new
        # work before we snapshot and stop the registered owned processes.
        registered = self.executor._processes
        for future in self.futures:
            future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        processes = list(registered.values()) if registered is not None else []
        for process in processes:
            if process.is_alive():
                process.kill()
        for process in processes:
            process.join(timeout=.05)
        self.closed = True

    def shutdown(self, wait=True, *, cancel_futures=True):
        if self.closed:
            return
        from concurrent.futures import wait as wait_futures
        remaining = max(0., self.deadline-time.monotonic())
        _, pending = wait_futures(self.futures, timeout=remaining)
        if pending or time.monotonic() >= self.deadline:
            self._abort()
            raise BallastDeadline('in-port acceptance deadline expired while draining owned workers')
        registered = self.executor._processes
        processes = list(registered.values()) if registered is not None else []
        manager = getattr(self.executor, '_executor_manager_thread', None)
        self.executor.shutdown(wait=False, cancel_futures=cancel_futures)
        # The executor manager normally joins its own children. Concurrent
        # Process.join calls race waitpid on macOS and can return while a
        # child still appears alive. Join that exact manager thread instead.
        if manager is not None:
            manager.join(timeout=max(0., self.deadline-time.monotonic()))
            blocked = manager.is_alive()
        else:
            for process in processes:
                process.join(timeout=max(0., self.deadline-time.monotonic()))
            blocked = any(process.is_alive() for process in processes)
        if blocked:
            for process in processes:
                if process.is_alive():
                    process.kill()
            for process in processes:
                process.join(timeout=.05)
            self.closed = True
            raise BallastDeadline('in-port acceptance deadline expired while closing owned workers')
        self.closed = True


def evaluate_in_port_with_deadline(evaluator, states, *, deadline,
                                  stop_at_first_failure=True, close=True):
    """Evaluate within 4.2's allowance, closing by default.

    W>1 runs directly in the calling parent, never in the legacy daemon search
    worker. The exact 4.1 initializer and per-state call are retained. W=1 uses
    cooperative checks and must be inside run_with_deadline for native-call
    isolation at the public finisher boundary. Closing/draining is charged to
    acceptance, including up to W-1 speculative checks after early failure.
    A parallel pass1 may retain its exact pool with close=False between
    chronological chunks; the phase owner must close it under the final
    calibrated deadline before returning or transporting the policy.
    """
    if evaluator.workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        from stowage.checker.in_port_evaluator import _worker_init
        if isinstance(evaluator._pool, _DeadlinePool):
            evaluator._pool.deadline = deadline
            executor = None
        elif evaluator._pool is None:
            executor = ProcessPoolExecutor(max_workers=evaluator.workers,
                mp_context=mp.get_context('spawn'), initializer=_worker_init,
                initargs=(evaluator.instance, evaluator.context, evaluator.warm_start,
                          getattr(evaluator, 'heel_intervals', False)))
        else:
            executor = evaluator._pool
        if executor is not None:
            evaluator._pool = _DeadlinePool(executor, deadline)
    try:
        with exact_budget(deadline):
            return evaluator.evaluate(states, stop_at_first_failure=stop_at_first_failure)
    finally:
        if close:
            evaluator.close()
