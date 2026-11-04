"""CPU-only tests of staged D124; live gen0 modules remain immutable."""
import ctypes
from concurrent.futures import ProcessPoolExecutor
import importlib.util
import os
import multiprocessing
from pathlib import Path
import signal
import sys
import time

import pytest

_path = Path(__file__).resolve().parents[3] / 'experiments/mvp/exit/patches/d124/checker_watchdog.py'
_spec = importlib.util.spec_from_file_location('_exit_staged_watchdog', _path)
watchdog = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = watchdog
_spec.loader.exec_module(watchdog)


def passing(instance, plan):
    from stowage.model import CheckReport, PartReport
    return CheckReport(passed=True, parts={name: PartReport(name=name) for name in
        ('moves', 'rules', 'voyage', 'stability', 'strength', 'visibility', 'lashing', 'constraints')})


def physics_fail(instance, plan):
    from stowage.model import Violation
    report = passing(instance, plan)
    report.passed = False
    report.violations = [Violation(rule_id='moves.top_box_only',
        severity='hard', call_index=0, message='Box blocked')]
    return report


def internal_error(instance, plan):
    from stowage.model import Violation
    report = passing(instance, plan)
    report.passed = False
    report.violations = [Violation(rule_id='checker.internal_error',
        severity='hard', call_index=0, message='Numerical failure')]
    return report


def large_report(instance, plan):
    report = passing(instance, plan)
    report.parts['moves'].metrics = {'x' * (4 << 20): 1.}
    return report


def no_report(instance, plan):
    return None


def crash(instance, plan):
    os._exit(17)


def exit_without_report(instance, plan):
    os._exit(0)


def _pool_worker():
    return watchdog.run_full_check(None, None, checker=passing, timeout_s=8).available


def remove_alarm_and_block(instance, plan):
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    ctypes.CDLL(None).sleep(10)
    return passing(instance, plan)


def catches_exception(instance, plan):
    try:
        time.sleep(10)
    except Exception:
        return passing(instance, plan)


def catches_baseexception(instance, plan):
    try:
        time.sleep(10)
    except BaseException:
        return passing(instance, plan)


def test_alarm_is_baseexception_and_exception_handler_cannot_swallow_it():
    assert not issubclass(watchdog.CheckExpired, Exception)
    with pytest.raises(watchdog.CheckExpired):
        with watchdog.check_deadline(.03):
            try:
                time.sleep(.2)
            except Exception:
                pytest.fail('watchdog was caught as Exception')


def test_swallowed_baseexception_is_latched_and_cannot_return_pass():
    with pytest.raises(watchdog.CheckExpired, match='latched'):
        with watchdog.check_deadline(.03):
            try:
                time.sleep(.2)
            except BaseException:
                pass


@pytest.mark.parametrize('checker,available,passed,reason', [
    (passing, True, True, None),
    (physics_fail, True, False, None),
    (internal_error, False, None, 'checker_internal_error'),
    (no_report, False, None, 'missing_or_invalid_report'),
    (crash, False, None, 'subprocess_crash'),
    (exit_without_report, False, None, 'report_missing'),
])
def test_completed_physics_vs_unavailable(checker, available, passed, reason):
    result = watchdog.run_full_check(None, None, checker=checker, timeout_s=8)
    assert result.available is available
    assert (result.report.passed if result.report is not None else None) is passed
    assert result.reason == reason
    assert result.pid != os.getpid()
    assert result.exitcode == (17 if checker is crash else 0)
    assert result.wall_s > 0
    assert result.details()['expired'] is False


def test_parent_kills_and_reaps_only_its_owned_native_blocker():
    started = time.monotonic()
    result = watchdog.run_full_check(None, None, checker=remove_alarm_and_block, timeout_s=.5)
    assert not result.available and result.report is None
    assert result.reason == 'watchdog_expired' and result.killed
    assert result.details()['expired'] is True
    assert result.exitcode == -signal.SIGKILL
    assert time.monotonic() - started < 3
    with pytest.raises(ProcessLookupError):
        os.kill(result.pid, 0)


def test_large_report_does_not_deadlock_waiting_for_ipc_drain():
    result = watchdog.run_full_check(None, None, checker=large_report, timeout_s=8)
    assert result.available and result.report.passed
    assert len(next(iter(result.report.parts['moves'].metrics))) == 4 << 20


@pytest.mark.parametrize('change,reason', [
    ('missing_part', 'checker_incomplete'),
    ('not_ran', 'checker_incomplete'),
    ('part_error', 'checker_incomplete'),
    ('nested_internal', 'checker_internal_error'),
    ('contradictory_aggregate', 'checker_contradictory_report'),
    ('contradictory_nested', 'checker_contradictory_report'),
])
def test_missing_incomplete_or_contradictory_reports_are_unavailable(change, reason):
    report = passing(None, None)
    if change == 'missing_part':
        del report.parts['voyage']
    elif change == 'not_ran':
        report.parts['stability'].ran = False
    elif change == 'part_error':
        report.parts['stability'].error = 'computation unavailable'
    elif change == 'nested_internal':
        report.parts['stability'].violations = internal_error(None, None).violations
    elif change == 'contradictory_aggregate':
        report.violations = physics_fail(None, None).violations
    else:
        report.parts['moves'].violations = physics_fail(None, None).violations
    assert watchdog._unavailable_reason(report) == reason


def test_owned_subprocess_runs_inside_spawn_process_pool():
    # pytest's importlib module name is not importable in a fresh interpreter.
    # Give the spawned worker an ordinary importable module-level entry point.
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        module = importlib.import_module('test_checker_watchdog')
        with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn')) as pool:
            assert pool.submit(module._pool_worker).result(timeout=12)
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize('seconds', [0, -1, float('inf'), float('nan')])
def test_invalid_watchdog_rejected_before_spawn(seconds):
    with pytest.raises(ValueError):
        watchdog.run_full_check(None, None, timeout_s=seconds)
