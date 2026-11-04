"""D126 queued gold boundary: fake signals/clocks, no production imports."""
import ast
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


class FakeSignal:
    SIGALRM, ITIMER_REAL = 14, 0

    def __init__(self, clock):
        self.clock, self.handler, self.due, self.interval = clock, object(), None, 0.

    def getsignal(self, signum):
        return self.handler

    def signal(self, signum, handler):
        previous, self.handler = self.handler, handler
        return previous

    def getitimer(self, kind):
        return (max(0., self.due-self.clock[0]) if self.due is not None else 0., self.interval)

    def setitimer(self, kind, delay, interval=0.):
        previous = self.getitimer(kind)
        self.due, self.interval = self.clock[0]+delay if delay else None, interval
        return previous


@pytest.fixture
def boundary():
    clock = [0.]
    signals = FakeSignal(clock)
    namespace = dict(contextmanager=contextmanager, signal=signals,
        time=SimpleNamespace(monotonic=lambda: clock[0], perf_counter=lambda: clock[0], process_time=lambda: 0.),
        child_cpu_s=lambda: 0., atomic_json=lambda *args: None, target='synthetic-not-written')
    tree = ast.parse((HERE/'gold-driver-d126-candidate.py').read_text())
    expiry = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == '_WatchdogExpired')
    deadline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'deadline')
    stage = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'stage')
    factory = ast.parse("def factory():\n    current_stage = None\n    row = {'stages': {}}\n    return None\n").body[0]
    factory.body[-1:] = [deepcopy(stage), ast.parse('return stage, row').body[0]]
    module = ast.fix_missing_locations(ast.Module(body=[expiry, deadline, factory], type_ignores=[]))
    exec(compile(module, '<queued-D126-gold>', 'exec'), namespace)
    return SimpleNamespace(clock=clock, signals=signals, call=namespace['deadline'],
                           expiry=namespace['_WatchdogExpired'], factory=namespace['factory'])


def test_expiry_derives_from_baseexception_and_bypasses_ordinary_cache_catch(boundary):
    assert issubclass(boundary.expiry, BaseException)
    assert not issubclass(boundary.expiry, Exception)
    swallowed = []
    with pytest.raises(boundary.expiry):
        with boundary.call(10):
            try:
                boundary.signals.handler(None, None)
            except Exception:
                swallowed.append(True)
    assert not swallowed


def test_swallowed_baseexception_stays_latched(boundary):
    with pytest.raises(boundary.expiry):
        with boundary.call(10):
            try:
                boundary.signals.handler(None, None)
            except BaseException:
                pass


def test_late_return_without_signal_is_unavailable(boundary):
    with pytest.raises(boundary.expiry):
        with boundary.call(10):
            boundary.clock[0] = 11.


def test_expired_body_failure_cannot_be_reported_as_physical_failure(boundary):
    with pytest.raises(boundary.expiry):
        with boundary.call(10):
            boundary.clock[0] = 11.
            raise ValueError('synthetic physical failure after expiry')


def test_nested_inner_limit_does_not_renew_outer_limit(boundary):
    with boundary.call(100):
        with pytest.raises(boundary.expiry):
            with boundary.call(10):
                boundary.clock[0] = 11.
        assert boundary.signals.getitimer(0)[0] == pytest.approx(89.)


def test_nested_outer_expiry_survives_broad_inner_catches(boundary):
    with pytest.raises(boundary.expiry):
        with boundary.call(10):
            try:
                with boundary.call(100):
                    assert boundary.signals.getitimer(0)[0] == 10.
                    boundary.clock[0] = 11.
                    try:
                        boundary.signals.handler(None, None)
                    except BaseException:
                        pass
            except BaseException:
                pass


def test_legacy_outer_exception_handler_is_converted_to_baseexception(boundary):
    def legacy(*args):
        raise TimeoutError('legacy outer alarm')
    boundary.signals.handler = legacy
    boundary.signals.setitimer(0, 5.)
    with pytest.raises(boundary.expiry) as raised:
        with boundary.call(10):
            boundary.clock[0] = 6.
            boundary.signals.handler(None, None)
    assert isinstance(raised.value.__cause__, TimeoutError)
    assert boundary.signals.handler is legacy


def test_on_time_success_preserves_report_and_caught_oserror_metrics(boundary):
    stage, row = boundary.factory()
    report = {'passed': True, 'margins': {'gm': 2.}}
    with stage('checker', 10.) as details:
        try:
            raise OSError('damaged optional cache')
        except OSError:
            details.update(passed=True, report=report)
        boundary.clock[0] = 2.
    assert row['stages']['checker']['passed'] is True
    assert row['stages']['checker']['report'] is report
    assert report == {'passed': True, 'margins': {'gm': 2.}}
    assert 'unavailable' not in row


def test_stage_explicitly_converts_expiry_only_after_rejecting_admission(boundary):
    stage, row = boundary.factory()
    report = {'passed': True, 'margins': {'gm': 2.}}
    with pytest.raises(TimeoutError) as raised:
        with stage('checker', 10.) as details:
            details.update(passed=True, report=report)
            try:
                boundary.signals.handler(None, None)
            except BaseException:
                pass
    assert isinstance(raised.value.__cause__, boundary.expiry)
    assert row['operational_status'] == 'unavailable'
    assert row['watchdog_timeout'] and row['unavailable']
    assert not row['stages']['checker']['passed']
    assert report == {'passed': True, 'margins': {'gm': 2.}}


def test_unexpired_exceptions_and_operator_cancellation_restore_handler(boundary):
    old = boundary.signals.handler
    stage, row = boundary.factory()
    with pytest.raises(ValueError, match='ordinary'):
        with stage('checker', 10.):
            raise ValueError('ordinary')
    with pytest.raises(KeyboardInterrupt):
        with stage('checker', 10.):
            raise KeyboardInterrupt()
    assert boundary.signals.handler is old
    assert boundary.signals.getitimer(0)[0] == 0.
    assert 'unavailable' not in row


@pytest.mark.parametrize('seconds', [0., -1., float('nan'), float('inf'), True])
def test_invalid_configuration_does_not_install_handler(boundary, seconds):
    old = boundary.signals.handler
    with pytest.raises(ValueError):
        with boundary.call(seconds):
            pytest.fail('invalid deadline entered')
    assert boundary.signals.handler is old


def test_previous_timer_restored_without_renewal(boundary):
    old = boundary.signals.handler
    boundary.signals.setitimer(0, 50.)
    with boundary.call(10):
        boundary.clock[0] = 3.
    assert boundary.signals.handler is old
    assert boundary.signals.getitimer(0)[0] == pytest.approx(47.)


def test_candidate_and_historical_d121_bytes_match_pinned_proposal():
    pins = json.loads((HERE/'PROPOSAL.json').read_text())
    assert not pins['applied']
    assert sha256((HERE/'gold-driver-d126-candidate.py').read_bytes()).hexdigest() == pins['candidate_sha256']
    assert sha256((HERE.parent/'gold-driver-candidate.py').read_bytes()).hexdigest() == pins['historical_d121_candidate_sha256']
