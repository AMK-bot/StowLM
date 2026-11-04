"""Deadlines, abrupt process death, and parent-side event forwarding."""
import os
import signal
import threading
from time import monotonic, sleep

import pytest

from .limits import (
    StageCallError, StageProcessError, StageTimeout, call_with_limit,
    deadline, run_in_process_with_limit,
)


def test_process_returns_large_result_and_forwards_events_in_parent():
    parent_pid = os.getpid()
    events = []

    def task(*, on_incumbent):
        on_incumbent({'pid': os.getpid(), 'plan': 'synthetic'})
        return 'x' * 300_000

    def receive(event):
        events.append((os.getpid(), event))

    result = call_with_limit(task, timeout_s=2, event_kw='on_incumbent', on_event=receive)
    assert len(result) == 300_000
    assert events[0][0] == parent_pid
    assert events[0][1]['pid'] != parent_pid


def test_hung_process_is_stopped_at_whole_budget():
    pids = []

    def task(*, report):
        report(os.getpid())
        sleep(5)

    began = monotonic()
    with pytest.raises(StageTimeout, match='budget'):
        call_with_limit(task, timeout_s=0.1, event_kw='report', on_event=pids.append)
    assert monotonic() - began < 2
    assert pids
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_timeout_cleans_up_workers_started_by_the_engine():
    import multiprocessing
    pids = []

    def task(*, report):
        child = multiprocessing.get_context('fork').Process(target=sleep, args=(5,))
        child.start()
        report(child.pid)
        sleep(5)

    with pytest.raises(StageTimeout):
        call_with_limit(task, timeout_s=0.2, event_kw='report', on_event=pids.append)
    assert pids
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_sigkill_returns_an_actionable_process_failure():
    def task():
        os.kill(os.getpid(), signal.SIGKILL)

    with pytest.raises(StageProcessError, match='Retry') as caught:
        call_with_limit(task, timeout_s=1)
    assert caught.value.exitcode in (None, -signal.SIGKILL)


def test_worker_exception_is_typed_without_raw_message_or_traceback(capfd):
    def task():
        raise RuntimeError('secret provider response')

    with pytest.raises(StageCallError) as caught:
        call_with_limit(task, timeout_s=1, stage='reader')
    assert caught.value.error_type == 'RuntimeError'
    assert 'secret' not in str(caught.value)
    captured = capfd.readouterr()
    assert 'Traceback' not in captured.out + captured.err


def test_parent_callback_exception_still_cleans_owned_process():
    pids = []

    def task(*, report):
        report(os.getpid())
        sleep(5)

    def receive(pid):
        pids.append(pid)
        raise ValueError('callback failed')

    with pytest.raises(ValueError, match='callback failed'):
        call_with_limit(task, timeout_s=1, event_kw='report', on_event=receive)
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_in_process_limit_preserves_observable_parser_state():
    calls = []
    assert run_in_process_with_limit(lambda: calls.append('read') or 7, timeout_s=1) == 7
    assert calls == ['read']
    with pytest.raises(StageTimeout, match='Reader exceeded'):
        run_in_process_with_limit(sleep, 1, timeout_s=0.03)


def test_non_main_thread_reader_fails_closed_before_running_parser():
    errors, calls = [], []

    def run():
        try:
            run_in_process_with_limit(lambda: calls.append('read'), timeout_s=1)
        except StageCallError as error:
            errors.append(error.error_type)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=1)
    assert errors == ['MainThreadRequired'] and not calls


def test_deadline_restores_previous_signal_handler_and_timer():
    original_handler = signal.getsignal(signal.SIGALRM)
    original_timer = signal.getitimer(signal.ITIMER_REAL)
    handler = lambda signum, frame: None
    try:
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, 10, 20)
        with deadline(0.5):
            sleep(0.01)
        assert signal.getsignal(signal.SIGALRM) is handler
        delay, interval = signal.getitimer(signal.ITIMER_REAL)
        assert 9 < delay < 10 and interval == 20
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original_handler)
        signal.setitimer(signal.ITIMER_REAL, *original_timer)


def test_nested_deadline_retains_earlier_outer_limit():
    with pytest.raises(StageTimeout) as caught:
        with deadline(0.03, stage='outer'):
            with deadline(2, stage='inner'):
                sleep(1)
    assert caught.value.stage == 'outer'


@pytest.mark.parametrize('budget', [0, -1, float('nan'), float('inf')])
def test_invalid_budget_is_rejected_before_starting_a_process(budget):
    with pytest.raises(ValueError, match='positive'):
        call_with_limit(lambda: pytest.fail('must not run'), timeout_s=budget)
