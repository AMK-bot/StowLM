"""D124 owned CPU subprocess checks; availability is separate from physics.

Staged for the next fresh-process boundary. No MLX/model modules are imported.
File IPC avoids a full pipe blocking a large report before the parent can reap.
Only this invocation's recorded subprocess PID is ever killed.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import importlib.util
import math
import os
from pathlib import Path
import pickle
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any


class CheckExpired(BaseException):
    """Must bypass checker's broad ``except Exception`` handlers."""


@contextmanager
def check_deadline(seconds):
    """Latch expiry even if a badly behaved checker catches BaseException."""
    previous = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    expired = [False]

    def alarm(*_):
        expired[0] = True
        raise CheckExpired(f'full check exceeded {seconds}s')

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
        if expired[0] or time.monotonic() - started >= seconds:
            raise CheckExpired(f'full check exceeded {seconds}s (latched)')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@dataclass
class CheckOutcome:
    available: bool
    report: Any = None
    reason: str | None = None
    wall_s: float = 0.
    checker_s: float | None = None
    pid: int | None = None
    exitcode: int | None = None
    killed: bool = False

    def details(self):
        details = {key: value for key, value in vars(self).items() if key != 'report'}
        details['expired'] = bool(self.reason and self.reason.startswith('watchdog_'))
        return details


def _unavailable_reason(report):
    from stowage.model import CheckReport
    if not isinstance(report, CheckReport):
        return 'missing_or_invalid_report'
    expected = {'moves', 'rules', 'voyage', 'stability', 'strength', 'visibility',
                'lashing', 'constraints'}
    if not expected.issubset(report.parts):
        return 'checker_incomplete'
    if any(not part.ran or part.error is not None for part in report.parts.values()):
        return 'checker_incomplete'
    violations = [*report.violations,
                  *(v for part in report.parts.values() for v in part.violations)]
    if any(str(v.rule_id).startswith('checker.') for v in violations):
        return 'checker_internal_error'
    hard_violation = any(v.severity == 'hard' for v in violations)
    if report.passed and hard_violation:
        return 'checker_contradictory_report'
    # A false report without a completed hard violation is not negative evidence.
    if not report.passed and not hard_violation:
        return 'checker_no_completed_violation'
    return None


def _callable_descriptor(checker):
    if checker is None:
        return None
    if '<locals>' in checker.__qualname__:
        raise ValueError('checker must be an importable module-level callable')
    module = sys.modules[checker.__module__]
    return (checker.__module__, getattr(module, '__file__', None), checker.__qualname__)


def _load_checker(descriptor):
    if descriptor is None:
        from stowage.checker import check
        return check
    name, filename, qualname = descriptor
    try:
        module = importlib.import_module(name)
    except ImportError:
        if not filename:
            raise
        spec = importlib.util.spec_from_file_location(name, filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    result = module
    for attr in qualname.split('.'):
        result = getattr(result, attr)
    return result


def _child_main(request, response):
    with open(request, 'rb') as stream:
        payload = pickle.load(stream)
    sys.path[:] = payload['sys_path']
    started = time.monotonic()
    try:
        with check_deadline(payload['timeout_s']):
            report = _load_checker(payload['checker'])(payload['instance'], payload['plan'])
        reason = _unavailable_reason(report)
        result = {'available': reason is None, 'report': report if reason is None else None,
                  'reason': reason}
    except CheckExpired:
        result = {'available': False, 'reason': 'watchdog_expired'}
    except BaseException as error:
        result = {'available': False, 'reason': 'checker_exception',
                  'error': f'{type(error).__name__}: {error}'}
    result['checker_s'] = time.monotonic() - started
    temporary = str(response) + '.tmp'
    with open(temporary, 'wb') as stream:
        pickle.dump(result, stream, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, response)


def run_full_check(instance, plan, *, timeout_s=300., checker=None):
    """Return a completed CheckReport or an explicit unavailable outcome.

    The parent hard deadline includes interpreter startup and IPC. Child alarm
    is defense in depth; expiry never becomes a regulatory/checker FAIL report.
    ``checker`` is a trusted module-level callable injection for CPU tests only.
    This uses exec rather than multiprocessing, so works inside pool workers and
    never forks a model-owning process or re-runs the training entry point.
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('timeout_s must be finite and positive')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='exit-check-') as directory:
        request, response = Path(directory)/'request.pkl', Path(directory)/'response.pkl'
        payload = dict(instance=instance, plan=plan, timeout_s=timeout_s,
                       checker=_callable_descriptor(checker), sys_path=list(sys.path))
        with request.open('wb') as stream:
            pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
        env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   VECLIB_MAXIMUM_THREADS='1', MKL_NUM_THREADS='1',
                   NUMEXPR_NUM_THREADS='1', CUDA_VISIBLE_DEVICES='')
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                  str(request), str(response)], env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        killed = False
        try:
            child.wait(timeout=max(.001, timeout_s - (time.monotonic()-started)))
        except subprocess.TimeoutExpired:
            killed = True
            child.kill()
            child.wait()
        except BaseException:
            if child.poll() is None:
                child.kill()
            child.wait()
            raise
        common = dict(wall_s=time.monotonic()-started, pid=child.pid,
                      exitcode=child.returncode, killed=killed)
        if killed or common['wall_s'] >= timeout_s:
            return CheckOutcome(False, reason='watchdog_expired', **common)
        if child.returncode != 0:
            return CheckOutcome(False, reason='subprocess_crash', **common)
        if not response.is_file():
            return CheckOutcome(False, reason='report_missing', **common)
        try:
            with response.open('rb') as stream:
                result = pickle.load(stream)
            if not result.get('available'):
                return CheckOutcome(False, reason=result.get('reason', 'checker_unavailable'),
                                    checker_s=result.get('checker_s'), **common)
            report = result['report']
            reason = _unavailable_reason(report)
            return CheckOutcome(reason is None, report=report if reason is None else None,
                                reason=reason, checker_s=result.get('checker_s'), **common)
        except Exception:
            return CheckOutcome(False, reason='report_invalid', **common)


if __name__ == '__main__':
    _child_main(sys.argv[1], sys.argv[2])
