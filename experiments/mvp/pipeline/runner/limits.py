"""Bound external stages without leaking worker errors or killing shared jobs."""
from __future__ import annotations

from contextlib import contextmanager
import math
import os
import signal
import threading
from time import monotonic


class StageTimeout(TimeoutError):
    def __init__(self, stage, timeout_s):
        self.stage, self.timeout_s = stage, timeout_s
        super().__init__(f'{stage.capitalize()} exceeded its {timeout_s:g} second budget. Retry or choose a larger budget.')


class StageProcessError(RuntimeError):
    def __init__(self, stage, exitcode=None):
        self.stage, self.exitcode = stage, exitcode
        super().__init__(f'{stage.capitalize()} worker stopped before returning a result. Retry the run.')


class StageCallError(RuntimeError):
    def __init__(self, stage, error_type):
        self.stage, self.error_type = stage, error_type
        super().__init__(f'{stage.capitalize()} could not complete ({error_type}). Check the service and retry.')


class _StageStopped(BaseException):
    pass


def _budget(timeout_s):
    value = float(timeout_s)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Stage budget must be a finite positive number of seconds')
    return value


@contextmanager
def deadline(timeout_s, *, stage='reader'):
    """Interrupt an in-process parser without forking a loaded GPU runtime.

    POSIX main-thread only. Previous alarm handlers and remaining timers survive
    this scope; nested runner deadlines retain the earlier deadline.
    """
    timeout_s = _budget(timeout_s)
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, 'setitimer'):
        raise StageCallError(stage, 'MainThreadRequired')
    started = monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    end = started + timeout_s
    previous_end = started + previous_delay if previous_delay else math.inf

    def expired(signum, frame):
        if previous_end <= end and callable(previous_handler):
            previous_handler(signum, frame)
            remaining = end - monotonic()
            if remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining)
                return
        raise StageTimeout(stage, timeout_s)

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(timeout_s, previous_delay or timeout_s))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay:
            remaining = previous_delay - (monotonic() - started)
            if remaining <= 0 and previous_interval:
                remaining = previous_interval - ((-remaining) % previous_interval)
            signal.setitimer(signal.ITIMER_REAL, max(remaining, 0.000001), previous_interval)


def run_in_process_with_limit(function, *args, timeout_s, stage='reader', **kwargs):
    with deadline(timeout_s, stage=stage):
        return function(*args, **kwargs)


def _worker(read_fd, write_fd, function, args, kwargs, event_kw):
    import multiprocessing
    import pickle
    import struct
    os.close(read_fd)
    # A provider must not print a traceback or a secret into the planner's CLI.
    with open(os.devnull, 'w') as quiet:
        os.dup2(quiet.fileno(), 1)
        os.dup2(quiet.fileno(), 2)

    def send(kind, value):
        body = pickle.dumps((kind, value), protocol=pickle.HIGHEST_PROTOCOL)
        data = struct.pack('!Q', len(body)) + body
        while data:
            sent = os.write(write_fd, data)
            data = data[sent:]

    if event_kw:
        kwargs[event_kw] = lambda event: send('event', event)

    def stop_children():
        children = multiprocessing.active_children()
        for child in children:
            if child.is_alive():
                child.terminate()
        for child in children:
            child.join(timeout=0.05)
            if child.is_alive():
                child.kill()
                child.join(timeout=0.05)

    def stopped(signum, frame):
        # Unwind the engine's executor while it still owns its exact children.
        stop_children()
        raise _StageStopped()

    signal.signal(signal.SIGTERM, stopped)
    try:
        result = function(*args, **kwargs)
        send('result', result)
    except BaseException as error:
        try:
            send('error', type(error).__name__)
        except BaseException:
            pass
    finally:
        stop_children()
        os.close(write_fd)


def call_with_limit(function, *args, timeout_s, stage='engine', on_event=None,
                    event_kw=None, **kwargs):
    """Fork one owned process, forward events, and enforce its whole wall budget.

    Functions and arguments need not be pickleable; returned values and events
    must be. Callbacks execute in the caller. Only the exact Process created
    here is signalled, never a PID discovered from the machine's process list.
    SIGTERM unwinds and cleans up the engine's own multiprocessing children.
    SIGKILL cannot run that cleanup; no process-list or process-group kill is
    attempted, because this machine has other owners' jobs on it.
    """
    import multiprocessing
    import pickle
    import selectors
    import struct
    timeout_s = _budget(timeout_s)
    if 'fork' not in multiprocessing.get_all_start_methods():
        raise StageProcessError(stage)
    started = monotonic()
    read_fd, write_fd = os.pipe()
    process = multiprocessing.get_context('fork').Process(
        target=_worker, args=(read_fd, write_fd, function, args, kwargs, event_kw))
    selector = selectors.DefaultSelector()
    buffer = bytearray()
    try:
        process.start()
        os.close(write_fd)
        write_fd = None
        os.set_blocking(read_fd, False)
        selector.register(read_fd, selectors.EVENT_READ)
        while True:
            remaining = timeout_s - (monotonic() - started)
            if remaining <= 0:
                raise StageTimeout(stage, timeout_s)
            ready = selector.select(min(remaining, 0.05))
            eof = False
            if ready:
                chunk = os.read(read_fd, 65536)
                eof = not chunk
                buffer.extend(chunk)
            while len(buffer) >= 8:
                # A checker callback may have consumed the remaining time while
                # a later result was already buffered on this pipe.
                if monotonic() - started >= timeout_s:
                    raise StageTimeout(stage, timeout_s)
                size = struct.unpack('!Q', buffer[:8])[0]
                if len(buffer) < size + 8:
                    break
                body = bytes(buffer[8:size + 8])
                del buffer[:size + 8]
                kind, value = pickle.loads(body)
                if kind == 'result':
                    return value
                if kind == 'error':
                    raise StageCallError(stage, value)
                if kind == 'event' and on_event is not None:
                    on_event(value)
            if eof or (not ready and not process.is_alive()):
                process.join(timeout=0)
                raise StageProcessError(stage, process.exitcode)
    finally:
        selector.close()
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if process.pid is not None:
            process.join(timeout=0.05)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.2)
            if process.is_alive():
                process.kill()
            process.join(timeout=1)
            process.close()
