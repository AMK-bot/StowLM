"""A stalled exact/native call cannot consume the caller's wall-clock budget."""
import math
import sys
import time

import pytest

from stowage.solver.ballast_deadline import (
    BallastDeadline, ExactDeadline, exact_budget, run_with_deadline, shutdown_worker,
)


@pytest.fixture(autouse=True)
def clean_worker():
    shutdown_worker()
    yield
    shutdown_worker()


def test_returns_results_and_keeps_warm_worker():
    assert run_with_deadline(abs, (-3,), deadline=time.monotonic()+10) == 3
    started = time.monotonic()
    assert run_with_deadline(abs, (-4,), deadline=started+1) == 4
    assert time.monotonic()-started < 1


def test_deadline_interrupts_native_sleep_and_worker_recovers():
    # Warm first, so this measures interruption rather than import speed.
    run_with_deadline(abs, (-1,), deadline=time.monotonic()+10)
    started = time.monotonic()
    with pytest.raises(BallastDeadline):
        run_with_deadline(time.sleep, (30,), deadline=started+.1)
    assert time.monotonic()-started < .5
    assert run_with_deadline(abs, (-5,), deadline=time.monotonic()+10) == 5


def test_expired_deadline_does_not_start_work():
    started = time.monotonic()
    with pytest.raises(BallastDeadline):
        run_with_deadline(time.sleep, (30,), deadline=started-1)
    assert time.monotonic()-started < .1


def test_illegal_value_error_propagates():
    with pytest.raises(ValueError, match='math domain'):
        run_with_deadline(math.sqrt, (-1,), deadline=time.monotonic()+10)


def test_exact_budget_interrupts_inside_calls_and_restores_hook():
    previous = sys.getprofile()
    started = time.monotonic()
    with pytest.raises(ExactDeadline):
        with exact_budget(started+.01):
            while True:
                # Exact checker handlers that catch Exception must not swallow
                # clock expiry and turn it into an ordinary failed condition.
                try:
                    math.sqrt(2.)
                except Exception:
                    pass
    assert time.monotonic()-started < .5
    assert sys.getprofile() is previous
