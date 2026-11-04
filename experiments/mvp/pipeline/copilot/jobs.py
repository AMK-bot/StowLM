"""Daemon background jobs, serialised around binding's process-wide hooks.

The worker returns native objects only to the host through ``result``. Polling
exposes JSON progress, never a candidate or an unchecked plan digest. Native
planning, full checking and instruction audit belong inside the operation;
publication on poll must only compare identity and install already checked state.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from queue import Queue
import sys
import threading
import time
import weakref
from types import ModuleType
from typing import Any, Callable
from uuid import uuid4


_MISSING = object()
# Like binding/hooks.py, survive both package and pytest import spellings.
_STATE_NAME = '_stowlm_copilot_native_job_state'
_STATE = sys.modules.setdefault(_STATE_NAME, ModuleType(_STATE_NAME))
_STATE.__dict__.setdefault('lock', threading.RLock())
_STATE.__dict__.setdefault('execution_lock', threading.RLock())
_STATE.__dict__.setdefault('queue', Queue())
_STATE.__dict__.setdefault('worker', None)
_STATE.__dict__.setdefault('pending', 0)


def _json_copy(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


@contextmanager
def native_guard(*, blocking: bool = False):
    """Protect synchronous native calls too; default never blocks conversation.

    A ``busy()`` check alone has a race. A synchronous native consumer must use
    this guard and return a running/unavailable result when acquisition fails.
    Background operations hold the same process-wide guard throughout their run.
    This does not coordinate separate processes or unrelated native callers.
    """
    acquired = _STATE.execution_lock.acquire(blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            _STATE.execution_lock.release()


@dataclass
class _Job:
    job_id: str
    operation: Callable | None
    identity: Any
    context: dict
    snapshot: Any
    has_snapshot: bool
    execution_context: Any
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    status: str = 'running'
    phase: str = 'queued'
    value: Any = None
    has_result: bool = False
    error_type: str | None = None
    stale_reason: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    lock: Any = field(default_factory=threading.RLock)
    owner: Any = None


def _clear_payload(job):
    job.operation = job.snapshot = job.execution_context = job.value = None
    job.has_result = False


def _terminal(job):
    owner = job.owner() if job.owner is not None else None
    if owner is not None:
        owner._prune_history()
    job.done.set()


def _run(job: _Job):
    with native_guard(blocking=True):
        with job.lock:
            if job.status == 'stale':
                _clear_payload(job)
                job.phase = 'discarded'
                job.finished_at = job.finished_at or time.monotonic()
                discarded = True
            else:
                discarded = False
                job.phase = 'executing'
                job.started_at = time.monotonic()
                operation, snapshot, execution_context = job.operation, job.snapshot, job.execution_context
                has_snapshot = job.has_snapshot
                job.operation = job.snapshot = job.execution_context = None
        if discarded:
            _terminal(job)
            return
        try:
            value = execution_context.run(operation, snapshot) if has_snapshot else execution_context.run(operation)
            error_type = None
        except BaseException as error:
            # A user callback must not kill the only dispatcher. Never surface
            # arbitrary native/provider exception bodies in conversation text.
            value, error_type = None, type(error).__name__
        with job.lock:
            job.finished_at = time.monotonic()
            if job.status == 'stale':
                job.phase = 'discarded'
                job.value = None
                job.has_result = False
            elif error_type is not None:
                job.status = 'error'
                job.phase = 'completed'
                job.error_type = error_type
            else:
                job.status = 'completed'
                job.phase = 'completed'
                job.value = value
                job.has_result = True
        # Do not pin a native graph in this frame after notifying a host that
        # can immediately consume and forget it.
        operation = snapshot = execution_context = value = None
        _terminal(job)


def _dispatch():
    while True:
        job = _STATE.queue.get()
        try:
            _run(job)
        finally:
            with _STATE.lock:
                _STATE.pending -= 1
            _STATE.queue.task_done()
            job = None


def _submit(job):
    with _STATE.lock:
        worker = _STATE.worker
        if worker is None or not worker.is_alive():
            worker = threading.Thread(target=_dispatch, name='copilot-native-jobs', daemon=True)
            _STATE.worker = worker
            worker.start()
        _STATE.pending += 1
        _STATE.queue.put(job)


class JobManager:
    """Memory-only jobs. The native worker cannot safely be force-cancelled.

    ``start(operation, snapshot=x)`` deepcopies x on submission, then invokes
    operation(detached_x). Without snapshot, operation is zero-argument and its
    closure must already own detached state; the manager cannot inspect closures.
    ``context`` and ``identity`` are JSON snapshots, never live references.
    Identity checks invalidate results if the host's instance/reading/generation
    changed. The host must supply its current identity again before publication.
    """

    def __init__(self, *, history_limit: int = 16):
        if type(history_limit) is not int or history_limit < 1:
            raise ValueError('history_limit must be a positive integer')
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        self._history_limit = history_limit

    def _prune_history(self):
        with self._lock:
            terminal = []
            for job in self._jobs.values():
                with job.lock:
                    if job.status != 'running' and job.phase != 'executing':
                        terminal.append(job)
            terminal.sort(key=lambda job: job.finished_at or job.created_at)
            for job in terminal[:max(0, len(terminal) - self._history_limit)]:
                with job.lock:
                    _clear_payload(job)
                    del self._jobs[job.job_id]

    def start(self, operation: Callable, *, identity=None, context=None, snapshot=_MISSING) -> dict:
        if not callable(operation):
            raise TypeError('operation must be callable')
        if context is not None and not isinstance(context, dict):
            raise TypeError('context must be a JSON object')
        has_snapshot = snapshot is not _MISSING
        # Copy before the worker can run; edits by the host cannot race it.
        detached = deepcopy(snapshot) if has_snapshot else None
        job = _Job(uuid4().hex, operation, _json_copy(identity), _json_copy(context or {}),
                   detached, has_snapshot, copy_context(), owner=weakref.ref(self))
        with self._lock:
            self._jobs[job.job_id] = job
        initial = self._describe(job)
        _submit(job)
        return initial

    def _get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    @staticmethod
    def _invalidate(job, reason):
        if job.status == 'stale':
            return
        job.status = 'stale'
        job.stale_reason = reason
        job.value = None
        job.has_result = False
        if job.phase != 'executing':
            _clear_payload(job)
            job.finished_at = time.monotonic()
            job.phase = 'discarded'
        # Waiting hosts may return now; a running native call continues safely.
        job.done.set()

    @staticmethod
    def _describe(job):
        with job.lock:
            end = job.finished_at if job.finished_at is not None else time.monotonic()
            elapsed = round(max(0., end - job.created_at), 3)
            text = {
                'running': 'Background native job is running. No new plan has been accepted.',
                'completed': 'Background native job completed. The host must validate freshness before publishing its checked result.',
                'error': 'Background native job failed. No new plan was accepted.',
                'stale': 'Background native job is stale; its result will not be published. Native work may still be finishing.',
            }[job.status]
            value = {'job_id': job.job_id, 'status': job.status, 'phase': job.phase,
                     'elapsed_s': elapsed, 'elapsed_label': 'measured',
                     'text': text + f' Elapsed {elapsed:.3f} seconds.',
                     'result_available': job.status == 'completed' and job.has_result,
                     'native_running': job.phase == 'executing',
                     'identity': _json_copy(job.identity), 'context': _json_copy(job.context),
                     'passed': False}
            if job.error_type is not None:
                value['error_type'] = job.error_type
            if job.stale_reason is not None:
                value['stale_reason'] = job.stale_reason
            return value

    def poll(self, job_id: str, *, identity=_MISSING) -> dict:
        """Return immediately; no future waiting, checker call or serialisation of native results."""
        job = self._get(job_id)
        if job is None:
            return {'job_id': job_id, 'status': 'not_found', 'elapsed_s': 0.,
                    'text': 'The background native job is not available.',
                    'result_available': False, 'passed': False}
        with job.lock:
            if identity is not _MISSING and _json_copy(identity) != job.identity:
                self._invalidate(job, 'identity_changed')
            value = self._describe(job)
        self._prune_history()
        return value

    def result(self, job_id: str, *, identity=_MISSING):
        """Host-only native payload; never a model tool response by itself."""
        progress = self.poll(job_id, identity=identity)
        job = self._get(job_id)
        if job is None:
            raise KeyError(job_id)
        with job.lock:
            if progress['status'] != 'completed' or not job.has_result:
                raise RuntimeError('No completed fresh job result is available')
            return job.value

    def cancel(self, job_id: str) -> dict:
        """Discard publication; a running native call is never interrupted."""
        job = self._get(job_id)
        if job is None:
            return self.poll(job_id)
        with job.lock:
            self._invalidate(job, 'cancelled_by_host')
            value = self._describe(job)
        self._prune_history()
        return value

    def wait(self, job_id: str, *, timeout_s: float = .05, identity=_MISSING) -> dict:
        """Optional host convenience, bounded to a quarter second per call."""
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (float, int))
                or not math.isfinite(timeout_s) or not 0 <= timeout_s <= .25):
            raise ValueError('timeout_s must be between zero and a quarter second')
        initial = self.poll(job_id, identity=identity)
        job = self._get(job_id)
        if job is not None and initial['status'] == 'running':
            job.done.wait(timeout_s)
        return self.poll(job_id, identity=identity)

    def busy(self) -> bool:
        """Advisory global backlog flag; use native_guard for race-free exclusion."""
        with _STATE.lock:
            return _STATE.pending > 0

    def forget(self, job_id: str) -> bool:
        """Release a finished/stale result after the host has consumed it."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            with job.lock:
                if job.status == 'running' or job.phase == 'executing':
                    return False
                _clear_payload(job)
                del self._jobs[job_id]
                return True
