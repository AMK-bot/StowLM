"""D199 V2 search-only rejection order and durable owned-worker progress.

Native loading probes still run first. Public physics parts are then attempted
visibility, lashing, stability, strength in each voyage phase. All parts must
succeed before V4 invokes its unchanged complete checker. No acceptance entry
point is wrapped or replaced. Exact helper reuse comes from frozen finisher.py.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import time

ORDER = ('visibility', 'lashing', 'stability', 'strength')

# Deliberately omit reports, margin vectors, seed lists, recipes and input state.
_COUNTERS = (
    'evaluations', 'candidate_count', 'candidates_evaluated',
    'selective_margin_evaluations', 'cache_hits', 'cheap_probe_calls',
    'cheap_probe_s', 'cheap_rejections', 'physics_part_calls', 'physics_part_s',
    'physics_rejections', 'full_checker_calls', 'full_checker_s',
    'full_checker_passed', 'context_setup_s', 'cheap_probe_errors',
    'scale_exact_reuse',
)


def _bounded(value, depth=0):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        # Nonfinite telemetry is represented explicitly without invalid JSON.
        return value if value == value and abs(value) != float('inf') else str(value)
    if isinstance(value, str):
        return value[:320]
    if isinstance(value, dict) and depth < 2:
        return {str(key)[:80]: _bounded(item, depth+1)
                for key, item in list(value.items())[:40]}
    return str(value)[:160]


class _Progress:
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.started = time.monotonic()
        self.last = float('-inf')
        self.serial = 0

    def write(self, evaluator, event, *, phase=None, name=None, identity=None,
              error=None, force=True):
        if self.path is None:
            return
        now = time.monotonic()
        if not force and now-self.last < 10.:
            return
        diagnostics = evaluator.diagnostics
        self.serial += 1
        payload = dict(version='scale-finisher-v2-progress-v1', pid=os.getpid(),
                       event=event, serial=self.serial, elapsed_s=now-self.started,
                       unix_time_s=time.time(), stage=str(diagnostics.get('stage', ''))[:160],
                       search_part_order=list(ORDER),
                       diagnostics={key: _bounded(diagnostics[key]) for key in _COUNTERS
                                    if key in diagnostics})
        if phase is not None:
            payload['phase'] = str(phase)[:80]
        if name is not None:
            payload['part'] = str(name)[:80]
        if identity is not None:
            payload['candidate_digest'] = hashlib.sha256(identity).hexdigest()[:20]
        if error is not None:
            payload['error_type'] = type(error).__name__[:80]
            payload['error'] = str(error)[:320]
        temporary = self.path.with_name(self.path.name+f'.tmp-{os.getpid()}')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, sort_keys=True, allow_nan=False)+'\n')
            temporary.replace(self.path)
            self.last = now
        except (OSError, ValueError, TypeError) as failure:
            # A monitoring path cannot alter a physics/search decision.
            diagnostics['scale_progress_write_error'] = str(failure)[:320]
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def search_hooks(progress_path=None):
    """Restore every search-only hook even on watchdog/deadline exceptions."""
    from stowage.solver import ballast_v4_evaluator as module
    original_checkers = module.checkers
    original_part = module.Evaluator._part
    original_checkpoint = module.Evaluator.checkpoint
    progress = _Progress(progress_path)

    @wraps(original_checkers)
    def checkers():
        functions = original_checkers()
        # Preserve any future additional callbacks, too; ordering removes none.
        return {name: functions[name] for name in ORDER if name in functions} | {
            name: function for name, function in functions.items() if name not in ORDER}

    @wraps(original_part)
    def part(evaluator, identity, phase, instance, ship, name):
        evaluator.diagnostics['scale_search_part_order'] = list(ORDER)
        evaluator.diagnostics['stage'] = f'physics:{phase}/{name}'
        progress.write(evaluator, 'part_start', phase=phase, name=name, identity=identity)
        try:
            result = original_part(evaluator, identity, phase, instance, ship, name)
        except BaseException as error:
            progress.write(evaluator, 'part_error', phase=phase, name=name,
                           identity=identity, error=error)
            raise
        progress.write(evaluator, 'part_done', phase=phase, name=name, identity=identity)
        return result

    @wraps(original_checkpoint)
    def checkpoint(evaluator):
        progress.write(evaluator, 'checkpoint', force=False)
        return original_checkpoint(evaluator)

    module.checkers = checkers
    module.Evaluator._part = part
    module.Evaluator.checkpoint = checkpoint
    try:
        yield
    finally:
        module.Evaluator.checkpoint = original_checkpoint
        module.Evaluator._part = original_part
        module.checkers = original_checkers


def worker(instance, plan, *, time_limit_s, seed, deadline):
    """Spawn-safe entry point called by the original owned-worker watchdog."""
    from finisher import exact_part_reuse
    from stowage.solver.ballast_v4 import finish
    with search_hooks(os.environ.get('SCALE_FINISHER_PROGRESS')):
        with exact_part_reuse():
            return finish(instance, plan, time_limit_s=time_limit_s, seed=seed,
                          _deadline=deadline)


def install():
    """Install after frozen runtime load; return an idempotent restore hook."""
    from stowage.solver import ballast_v4_dispatch
    original = ballast_v4_dispatch._worker
    ballast_v4_dispatch._worker = worker

    def restore():
        if ballast_v4_dispatch._worker is worker:
            ballast_v4_dispatch._worker = original
    return restore
