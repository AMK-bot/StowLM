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
