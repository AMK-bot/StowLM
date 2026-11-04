"""Admit CPU work before starting its engine clock; no fabricated capacity."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import importlib
import json
import math
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace

STATE = Path(__file__).resolve().parents[3] / 'fleet' / 'core-state.json'
CACHE_ROOT = Path(__file__).resolve().parent / 'cache'
_RUNTIME = sys.modules.setdefault('_stowlm_runner_runtime_state', ModuleType('_stowlm_runner_runtime_state'))
_RUNTIME.__dict__.setdefault('root', None)


class AdmissionUnavailable(RuntimeError):
    pass


def _runner_worker(target, args, kwargs, root):
    """Enter runner paths before binding's spawned-worker trampoline runs."""
    with _binding_runtime_scope(root):
        return target(*args, **kwargs)


@contextmanager
def _binding_runtime_scope(root=None):
    """Route binding-derived caches and hook audits into this runner's fence.

    A fork inherits the installed scope. A spawn gets a pickleable runner
    trampoline outside binding's own trampoline, so its runtime scope starts
    with the same cache paths. No existing cache is moved or deleted.
    """
    from multiprocessing.process import BaseProcess
    from unittest.mock import patch
    root = Path(root or CACHE_ROOT).resolve()
    if _RUNTIME.root == str(root):
        yield
        return
    if _RUNTIME.root is not None:
        raise RuntimeError('A runner cache scope is already active in this process')
    runtime = importlib.import_module('experiments.mvp.pipeline.binding.runtime')
    hooks = importlib.import_module('experiments.mvp.pipeline.binding.hooks')
    audit_dir = root / 'audits'
    audit_dir.mkdir(parents=True, exist_ok=True)
    original_tempfile = hooks.tempfile.mkstemp
    original_start = BaseProcess.start

    def mkstemp(suffix=None, prefix=None, dir=None, text=False):
        return original_tempfile(suffix=suffix, prefix=prefix, dir=audit_dir, text=text)

    def start(process, *args, **kwargs):
        target = getattr(process, '_target', None)
        if target is None:
            return original_start(process, *args, **kwargs)
        target_args, target_kwargs = process._args, process._kwargs
        # Use a canonical import path even when pytest loaded an alias package.
        process._target = importlib.import_module(
            'experiments.mvp.pipeline.runner.admission')._runner_worker
        process._args = (target, target_args, target_kwargs, str(root))
        process._kwargs = {}
        try:
            return original_start(process, *args, **kwargs)
        except BaseException:
            process._target, process._args, process._kwargs = target, target_args, target_kwargs
            raise

    _RUNTIME.root = str(root)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, 'HERE', root.parent))
            stack.enter_context(patch.object(runtime, 'ROOT', root))
            stack.enter_context(patch.object(hooks, 'tempfile', SimpleNamespace(mkstemp=mkstemp)))
            stack.enter_context(patch.object(BaseProcess, 'start', start))
            yield
    finally:
        _RUNTIME.root = None


def wait_for_capacity(state_path=None, *, poll_s=2., sleep=None, now=None):
    """Wait for fresh telemetry, eight enabled cores, and load1 <= 45.

    Missing, unreadable, busy, stale, malformed, and future readings all wait.
    Queueing happens before the engine deadline starts.
    """
    state_path = Path(state_path) if state_path is not None else STATE
    sleep, now = sleep or time.sleep, now or time.time
    began = time.monotonic()
    while True:
        try:
            text = state_path.read_text()
        except (UnicodeError, OSError):
            text = ''
        try:
            row = json.loads(text)
            raw_mask = row['mask']
            if isinstance(raw_mask, str):
                mask = int(raw_mask, 0)
            elif isinstance(raw_mask, int) and not isinstance(raw_mask, bool):
                mask = raw_mask
            else:
                raise ValueError('invalid mask')
            load = float(row['load1'])
            age = now() - float(row['t'])
            if (mask >= 0 and mask.bit_count() >= 8 and math.isfinite(load)
                    and 0 <= load <= 45 and 0 <= age < 120):
                return dict(mask=mask, enabled_cores=mask.bit_count(), load1=load,
                            reading_age_s=age, waited_s=time.monotonic() - began)
        except (ValueError, KeyError, TypeError, OverflowError):
            pass
        sleep(poll_s)


def call_admitted(function, *args, admission, **kwargs):
    """Worker-local binding call using the parent's real accepted reading.

    Binding's two internal admission sites reuse that reading so queueing does
    not consume the engine clock. This belongs inside the isolated worker;
    callers must first obtain ``admission`` from ``wait_for_capacity``.
    """
    if not isinstance(admission, dict) or not all(
            key in admission for key in ('mask', 'enabled_cores', 'load1', 'reading_age_s', 'waited_s')):
        raise AdmissionUnavailable('A real fleet admission reading is required before starting the engine.')
    from unittest.mock import patch
    reading = dict(admission, runner_pre_admitted=True)
    with ExitStack() as stack:
        stack.enter_context(_binding_runtime_scope())
        for name in ('adapter', 'allocation'):
            module = importlib.import_module('experiments.mvp.pipeline.binding.' + name)
            stack.enter_context(patch.object(module, 'wait_for_capacity', lambda: dict(reading)))
        return function(*args, **kwargs)
