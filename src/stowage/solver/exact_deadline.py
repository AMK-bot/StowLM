"""A parent watchdog for exact search, including stalled native calculations.

Clock checks in A* cannot interrupt engine calibration, LAPACK, or a checker
inside compiled code. A dedicated forked process owns the entire calculation;
its parent waits only until an absolute monotonic deadline and stops only that
recorded process. Fork preserves the caller's frozen overrides and test doubles.
A receiver thread bounds even partial/large IPC frames. This uses POSIX fork
on the project's macOS/Linux platform, rather than Windows multiprocessing.
Checkpoints are accepted
only after a fully validated incumbent has been published; no partial numerical
calculation becomes a plan. Source: Python multiprocessing Process/Pipe and
threading.Event documentation. No physics or optimization approximation occurs.

The worker runs ballast's public finisher with its process-local deadline runner
inlined. The outer watchdog already isolates that work, so a second ballast
process would add no protection and could survive its parent's forced stop.
Only the child's module binding changes; caller state is untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
import multiprocessing as mp
import threading
import time
from typing import Any


@dataclass(frozen=True)
class DeadlineOutcome:
    value: Any = None
    checkpoint: Any = None
    timed_out: bool = False
    wall_time_s: float = 0.
    worker_pid: int | None = None


_connection = None


def in_worker():
    return _connection is not None


def publish_checkpoint(value):
    """Publish a completed validated witness; no-op outside isolated work."""
    if _connection is not None:
        _connection.send(('checkpoint', value))


def _inline(function, args=(), kwargs=None, *, deadline):
    # The authoritative parent clock covers this call, including native hangs.
    return function(*args, **({} if kwargs is None else kwargs))


def _worker(connection, function, args, kwargs):
    global _connection
    _connection = connection
    try:
        from stowage.solver import ballast
        ballast.run_with_deadline = _inline
        # Native BLAS pools may have been initialized before fork. Constrain
        # those inherited pools too; CP/HiGHS workers are separately explicit.
        try:
            from threadpoolctl import threadpool_limits
            limits = threadpool_limits(limits=1)
        except ImportError:
            limits = None
        try:
            value = function(*args, **kwargs)
            connection.send(('result', value))
        finally:
            if limits is not None:
                limits.restore_original_limits()
    except BaseException as error:
        try:
            connection.send(('error', error))
        except BaseException:
            try:
                connection.send(('error', RuntimeError(f'{type(error).__name__}: {error}')))
            except BaseException:
                pass
    finally:
        connection.close()
        _connection = None


def run_with_deadline(function, args=(), kwargs=None, *, deadline):
    """Run one callable before deadline; return its latest complete checkpoint.

    The caller performs type-specific timeout conversion. Errors propagate.
    All startup, serialization, calculation and result delivery time counts.
    The recorded worker is never reused, so each call inherits current overrides.
    """
    if in_worker():
        raise RuntimeError('nested exact watchdogs are not supported')
    started = time.monotonic()
    if started >= deadline:
        return DeadlineOutcome(timed_out=True, wall_time_s=0.)
    context = mp.get_context('fork')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(child, function, args,
                              {} if kwargs is None else kwargs), daemon=True,
                              name='stowage-exact')
    delivery = {}
    completed = threading.Event()
    process.start()
    child.close()
    pid = process.pid
    def receive():
        try:
            while True:
                kind, value = parent.recv()
                # Deserialization itself is bounded by the parent's Event wait.
                # A frame completing late cannot retroactively become a result.
                if time.monotonic() >= deadline:
                    return
                if kind == 'checkpoint':
                    delivery['checkpoint'] = value
                else:
                    delivery[kind] = value
                    completed.set()
                    return
        except (EOFError, OSError) as error:
            delivery['pipe_error'] = error
            completed.set()
    receiver = threading.Thread(target=receive, name='exact-result', daemon=True)
    receiver.start()
    timed_out = not completed.wait(timeout=max(0., deadline-time.monotonic()))
    timed_out = timed_out or time.monotonic() >= deadline
    try:
        if timed_out:
            return DeadlineOutcome(checkpoint=delivery.get('checkpoint'), timed_out=True,
                                   wall_time_s=time.monotonic()-started, worker_pid=pid)
        if 'error' in delivery:
            raise delivery['error']
        if 'result' not in delivery:
            raise RuntimeError(f'exact worker {pid} exited without a completed result') from delivery.get('pipe_error')
        return DeadlineOutcome(value=delivery['result'], checkpoint=delivery.get('checkpoint'),
                               wall_time_s=time.monotonic()-started, worker_pid=pid)
    finally:
        # This Process handle was created above. Never discover PIDs by name,
        # command pattern, process group or machine-wide process inspection.
        if process.is_alive():
            process.kill()
        process.join(timeout=.02)
        parent.close()
        if not process.is_alive():
            process.close()
