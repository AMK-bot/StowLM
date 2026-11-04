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
import json
import math
import os
from pathlib import Path
import pickle
import signal
import secrets
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
    epoch_ack: dict | None = None
    epoch_ack_error: str | None = None

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


def _child_main(request, response, bootstrap=None):
    # Read the plain envelope BEFORE unpickling Instance/MovePlan (which imports
    # stowage.model). Only standard-library imports have run at this point.
    envelope = {}
    if bootstrap is not None:
        with open(bootstrap) as stream:
            envelope = json.load(stream)
        sys.path[:] = envelope['sys_path']
    expected = envelope.get('expected_outcome_epoch')
    ack = None
    mismatch_type = ()
    started = time.monotonic()
    try:
        if expected is not None:
            from llm.exit.outcome_epoch import (process_snapshot, snapshot,
                guarded_outcome_reads, OutcomeEpochMismatch)
            mismatch_type = OutcomeEpochMismatch
            loaded = process_snapshot()
            ack = dict(protocol='exit-check-outcome-ack-v1', request_id=envelope['request_id'],
                       loaded_pre=loaded, loaded_post=None)
            if loaded != expected:
                raise OutcomeEpochMismatch('checker process loaded outcome epoch differs')
            # Source bytes actually imported and primary inputs actually read
            # must match the manifest, even during a change-and-revert window.
            with guarded_outcome_reads(expected) as consumed:
                with open(request, 'rb') as stream:
                    payload = pickle.load(stream)
                with check_deadline(payload['timeout_s']):
                    report = _load_checker(payload['checker'])(payload['instance'], payload['plan'])
                reason = _unavailable_reason(report)
                post = snapshot()
                if post != expected:
                    raise OutcomeEpochMismatch('checker outcome epoch changed during check')
                ack.update(loaded_post=post, **consumed)
        else:
            with open(request, 'rb') as stream:
                payload = pickle.load(stream)
            sys.path[:] = payload['sys_path']
            with check_deadline(payload['timeout_s']):
                report = _load_checker(payload['checker'])(payload['instance'], payload['plan'])
            reason = _unavailable_reason(report)
        result = {'available': reason is None, 'report': report if reason is None else None,
                  'reason': reason}
    except CheckExpired:
        result = {'available': False, 'reason': 'watchdog_expired'}
    except BaseException as error:
        result = {'available': False, 'reason': 'outcome_epoch_mismatch' if isinstance(error, mismatch_type)
                  else 'checker_exception', 'error': f'{type(error).__name__}: {error}'}
    result['checker_s'] = time.monotonic() - started
    result['epoch_ack'] = ack
    temporary = str(response) + '.tmp'
    with open(temporary, 'wb') as stream:
        pickle.dump(result, stream, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, response)


def _validate_epoch_ack(result, expected, request_id):
    if expected is None:
        return None
    ack = result.get('epoch_ack')
    if (not isinstance(ack, dict) or ack.get('protocol') != 'exit-check-outcome-ack-v1'
            or ack.get('request_id') != request_id or ack.get('loaded_pre') != expected
            or ack.get('loaded_post') != expected):
        return 'outcome_epoch_ack_mismatch'
    for name in ('imports', 'resources'):
        if not isinstance(ack.get(name), dict):
            return 'outcome_epoch_ack_mismatch'
        if any(expected.get(path) != value for path, value in ack[name].items()):
            return 'outcome_epoch_ack_mismatch'
    return None


def run_full_check(instance, plan, *, timeout_s=300., checker=None, expected_outcome_epoch=None):
    """Return a completed CheckReport or an explicit unavailable outcome.

    The parent hard deadline includes interpreter startup and IPC. Child alarm
    is defense in depth; expiry never becomes a regulatory/checker FAIL report.
    ``checker`` is a trusted module-level callable injection for CPU tests only.
    This uses exec rather than multiprocessing, so works inside pool workers and
    never forks a model-owning process or re-runs the training entry point.
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('timeout_s must be finite and positive')
    if expected_outcome_epoch is not None:
        from llm.exit.outcome_epoch import process_snapshot
        if process_snapshot() != expected_outcome_epoch:
            return CheckOutcome(False, reason='parent_loaded_outcome_epoch_mismatch')
    started = time.monotonic()
    request_id = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix='exit-check-') as directory:
        request, response = Path(directory)/'request.pkl', Path(directory)/'response.pkl'
        bootstrap = Path(directory)/'bootstrap.json'
        bootstrap.write_text(json.dumps(dict(protocol='exit-check-outcome-ack-v1',
            expected_outcome_epoch=expected_outcome_epoch, request_id=request_id, sys_path=list(sys.path))))
        payload = dict(instance=instance, plan=plan, timeout_s=timeout_s,
                       checker=_callable_descriptor(checker), sys_path=list(sys.path))
        with request.open('wb') as stream:
            pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
        env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   VECLIB_MAXIMUM_THREADS='1', MKL_NUM_THREADS='1',
                   NUMEXPR_NUM_THREADS='1', CUDA_VISIBLE_DEVICES='')
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                  str(request), str(response), str(bootstrap)], env=env,
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
            ack_error = _validate_epoch_ack(result, expected_outcome_epoch, request_id)
            if expected_outcome_epoch is not None and ack_error:
                # Never overwrite an actual expiry/exception with a secondary
                # missing-ACK label. Both are unavailable; only an otherwise
                # completed report needs promotion to an ACK-mismatch refusal.
                reason=ack_error if result.get('available') else result.get('reason') or ack_error
                return CheckOutcome(False, reason=reason, checker_s=result.get('checker_s'),
                    epoch_ack=result.get('epoch_ack'), epoch_ack_error=ack_error, **common)
            common['epoch_ack'] = result.get('epoch_ack')
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
    _child_main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv)>3 else None)
