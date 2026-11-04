"""Queued patch helper tests: fake clocks/signals only, no project imports."""
import ast
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


class FakeSignal:
    SIGALRM, SIGPROF, ITIMER_REAL, ITIMER_PROF = 14, 27, 0, 2

    def __init__(self, clock):
        self.clock = clock
        self.handlers = {self.SIGALRM: object(), self.SIGPROF: object()}
        self.timers = {}

    def getsignal(self, signum):
        return self.handlers[signum]

    def signal(self, signum, handler):
        previous = self.handlers[signum]
        self.handlers[signum] = handler
        return previous

    def getitimer(self, kind):
        due, interval = self.timers.get(kind, (None, 0.))
        return (max(0., due-self.clock[0]) if due is not None else 0., interval)

    def setitimer(self, kind, delay, interval=0.):
        old = self.getitimer(kind)
        self.timers[kind] = (self.clock[0]+delay if delay else None, interval)
        return old


@pytest.fixture(params=[('gold-driver', 'deadline', False), ('smoke', 'deadline', False), ('lookahead', 'cpu_cap', True)])
def helper(request):
    label, name, cpu = request.param
    clock = [0.]
    signals = FakeSignal(clock)
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], process_time=lambda: clock[0], perf_counter=lambda: clock[0])
    namespace = dict(contextmanager=contextmanager, time=fake_time, signal=signals)
    tree = ast.parse((HERE/f'{label}-candidate.py').read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), f'<queued-{label}>', 'exec'), namespace)
    return SimpleNamespace(call=namespace[name], clock=clock, signals=signals, namespace=namespace,
                           kind=signals.ITIMER_PROF if cpu else signals.ITIMER_REAL,
                           alarm=signals.SIGPROF if cpu else signals.SIGALRM, exclusive=label == 'smoke')


def test_normal_return_preserves_result_and_restores_handler(helper):
    old = helper.signals.getsignal(helper.alarm)
    marker = object()
    with helper.call(10):
        result = marker
        helper.clock[0] = 2.
    assert result is marker
    assert helper.signals.getsignal(helper.alarm) is old
    assert helper.signals.getitimer(helper.kind)[0] == 0.


def test_swallowed_alarm_is_latched_even_before_post_return_clock_limit(helper):
    with pytest.raises(TimeoutError):
        with helper.call(10):
            helper.clock[0] = 9.
            try:
                helper.signals.getsignal(helper.alarm)(None, None)
            except TimeoutError:
                pass


def test_post_return_clock_rejects_delayed_signal_delivery(helper):
    with pytest.raises(TimeoutError):
        with helper.call(10):
            helper.clock[0] = 11.


def test_late_body_error_is_unavailable_not_a_physical_failure(helper):
    with pytest.raises(TimeoutError):
        with helper.call(10):
            helper.clock[0] = 11.
            raise ValueError('a returned physical failure cannot erase expiry')


def test_on_time_body_error_and_operator_cancel_keep_original_meaning(helper):
    old = helper.signals.getsignal(helper.alarm)
    with pytest.raises(ValueError, match='ordinary'):
        with helper.call(10):
            raise ValueError('ordinary')
    with pytest.raises(KeyboardInterrupt):
        with helper.call(10):
            helper.clock[0] = 11.
            raise KeyboardInterrupt()
    assert helper.signals.getsignal(helper.alarm) is old
    assert helper.signals.getitimer(helper.kind)[0] == 0.


@pytest.mark.parametrize('seconds', [0., -1., float('nan'), float('inf'), True])
def test_invalid_limits_leave_previous_state_untouched(helper, seconds):
    old = helper.signals.getsignal(helper.alarm)
    with pytest.raises(ValueError):
        with helper.call(seconds):
            pytest.fail('invalid context entered')
    assert helper.signals.getsignal(helper.alarm) is old
    assert helper.signals.getitimer(helper.kind)[0] == 0.


def test_short_inner_scope_restores_outer_remaining_time_without_renewal(helper):
    if helper.exclusive:
        pytest.skip('smoke intentionally refuses an already owned alarm')
    with helper.call(100):
        with pytest.raises(TimeoutError):
            with helper.call(10):
                helper.clock[0] = 11.
        assert helper.signals.getitimer(helper.kind)[0] == pytest.approx(89.)


def test_inner_scope_cannot_hide_outer_expiry_even_if_both_errors_caught(helper):
    if helper.exclusive:
        pytest.skip('smoke intentionally refuses an already owned alarm')
    with pytest.raises(TimeoutError):
        with helper.call(10):
            try:
                with helper.call(100):
                    assert helper.signals.getitimer(helper.kind)[0] == 10.
                    helper.clock[0] = 11.
                    try:
                        helper.signals.getsignal(helper.alarm)(None, None)
                    except TimeoutError:
                        pass
            except TimeoutError:
                pass


def test_previous_timer_restored_with_elapsed_time_or_explicit_ownership_rejection(helper):
    old = helper.signals.getsignal(helper.alarm)
    helper.signals.setitimer(helper.kind, 50.)
    if helper.exclusive:
        with pytest.raises(RuntimeError, match='ownership'):
            with helper.call(10):
                pytest.fail('exclusive smoke may not replace an existing timer')
        assert helper.signals.getitimer(helper.kind)[0] == 50.
    else:
        with helper.call(10):
            helper.clock[0] = 3.
        assert helper.signals.getitimer(helper.kind)[0] == pytest.approx(47.)
    assert helper.signals.getsignal(helper.alarm) is old


def test_previous_periodic_timer_keeps_its_phase(helper):
    if helper.exclusive:
        pytest.skip('smoke intentionally refuses an already owned alarm')
    calls = []
    helper.signals.signal(helper.alarm, lambda *args: calls.append(True))
    helper.signals.setitimer(helper.kind, 5., 7.)
    with pytest.raises(TimeoutError):
        with helper.call(10):
            helper.clock[0] = 8.
    assert calls == [True]
    assert helper.signals.getitimer(helper.kind) == pytest.approx((4., 7.))


@pytest.mark.parametrize('label', ['gold-driver', 'smoke'])
def test_stage_expiry_marks_unavailable_and_never_retains_passed_flag(label):
    clock = [0.]
    signals = FakeSignal(clock)
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], perf_counter=lambda: clock[0], process_time=lambda: 0.)
    namespace = dict(contextmanager=contextmanager, signal=signals, time=fake_time,
                     child_cpu_s=lambda: 0., atomic_json=lambda *args: None, target='unused',
                     save=lambda *args: None, result_path='unused',
                     traceback=SimpleNamespace(format_exc=lambda: 'synthetic traceback'))
    tree = ast.parse((HERE/f'{label}-candidate.py').read_text())
    deadline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'deadline')
    stage = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'stage')
    factory = ast.parse("def factory():\n    current_stage = None\n    row = {'stages': {}}\n    return None\n").body[0]
    factory.body[-1:] = [deepcopy(stage), ast.parse('return stage, row').body[0]]
    module = ast.fix_missing_locations(ast.Module(body=[deadline, factory], type_ignores=[]))
    exec(compile(module, '<queued-gold-stage>', 'exec'), namespace)
    stage, row = namespace['factory']()
    with pytest.raises(TimeoutError):
        with stage('checker', 10.) as details:
            details['passed'] = True
            try:
                signals.getsignal(signals.SIGALRM)(None, None)
            except TimeoutError:
                pass
    assert row['operational_status'] == 'unavailable'
    assert row['unavailable'] and row['watchdog_timeout']
    assert row['stages']['checker']['passed'] is False
    assert not all(item['passed'] for item in row['stages'].values())


@pytest.mark.parametrize('label', ['gold-driver', 'smoke'])
def test_ordinary_caught_cache_oserror_preserves_success_and_metrics(label):
    clock = [0.]
    signals = FakeSignal(clock)
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], perf_counter=lambda: clock[0], process_time=lambda: 0.)
    namespace = dict(contextmanager=contextmanager, signal=signals, time=fake_time,
                     child_cpu_s=lambda: 0., atomic_json=lambda *args: None, target='unused',
                     save=lambda *args: None, result_path='unused',
                     traceback=SimpleNamespace(format_exc=lambda: 'synthetic traceback'))
    tree = ast.parse((HERE/f'{label}-candidate.py').read_text())
    deadline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'deadline')
    stage = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'stage')
    factory = ast.parse("def factory():\n    current_stage = None\n    row = {'stages': {}}\n    return None\n").body[0]
    factory.body[-1:] = [deepcopy(stage), ast.parse('return stage, row').body[0]]
    module = ast.fix_missing_locations(ast.Module(body=[deadline, factory], type_ignores=[]))
    exec(compile(module, '<queued-ordinary-fallback>', 'exec'), namespace)
    stage, row = namespace['factory']()
    with stage('checker', 10.) as details:
        try:
            raise OSError('ordinary damaged optional cache')
        except OSError:
            details.update(passed=True, metrics={'measured_margin': 2.})
    assert row['stages']['checker']['passed'] is True
    assert row['stages']['checker']['metrics'] == {'measured_margin': 2.}
    assert 'unavailable' not in row


@pytest.mark.parametrize('mode', ['swallowed_base_exception', 'late_return'])
def test_loop_smoke_cancellation_bookkeeping_without_model_or_training(mode):
    import builtins
    clock = [0.]
    signals = FakeSignal(clock)
    records = {}
    def generate(*args, **kwargs):
        if mode == 'swallowed_base_exception':
            try:
                signals.getsignal(signals.SIGALRM)(None, None)
            except BaseException:
                pass
        else:
            clock[0] = 601.
        return dict(candidates=[], timings={}, samples=[], securing={'evaluations': 1, 'fail_open': 0, 'unavailable': 0}, memory_config={})
    audit = lambda paths, *args, **kwargs: [dict(plan=None, status='incomplete',
        securing={'evaluations': 1, 'fail_open': 0, 'unavailable': 0}, memory_config={}) for path in paths]
    fake_modules = {
        'llm.exit.runtime': SimpleNamespace(clear_model_cache=lambda: None, generate=generate),
        'llm.exit.selection': SimpleNamespace(verify_candidate=lambda *args, **kwargs: pytest.fail('no candidate exists')),
        'stowage.io': SimpleNamespace(load_instance=lambda *args: pytest.fail('no candidate body needs loading')),
        'llm.exit.audit_runtime': SimpleNamespace(generate_audit_batch=audit),
    }
    def fake_import(name, *args, **kwargs):
        return fake_modules[name] if name in fake_modules else builtins.__import__(name, *args, **kwargs)
    root = Path('/synthetic-not-read')
    schedule = SimpleNamespace(root=root, state={'generations': {'0': [{'path': 'train-a'}, {'path': 'train-b'}]}})
    namespace = dict(__builtins__=dict(vars(builtins), __import__=fake_import), signal=signals,
                     time=SimpleNamespace(monotonic=lambda: clock[0]),
                     CorpusSchedule=lambda *args: schedule, select_job=lambda *args: {'passed': False},
                     write_json=lambda path, value: records.update({path.name: value}))
    tree = ast.parse((HERE/'exit-smoke-candidate.py').read_text())
    smoke = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'smoke')
    exec(compile(ast.Module(body=[smoke], type_ignores=[]), '<queued-loop-smoke>', 'exec'), namespace)
    args = SimpleNamespace(corpus=root, out=root, gold=root, parent='synthetic', max_tokens=1, ballast_s=1.)
    with pytest.raises(RuntimeError, match='incomplete'):
        namespace['smoke'](args)
    assert records['gpu-smoke.json']['timed_out'] is True
