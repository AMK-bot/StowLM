"""Bound checker work without converting incomplete physics into a pass.

A monotonic deadline is shared by nested direct rechecks. Cooperative polls
bound numerical loops; a final poll rejects results completed after the budget.
The budget excludes no physics and changes no regulatory tolerance.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import math
from time import monotonic

_deadline = ContextVar('stability_deadline', default=None)


class StabilityTimeout(Exception):
    """Numerical work exhausted the caller's stability time budget."""


def check_budget(label=''):
    deadline = _deadline.get()
    if deadline is not None and monotonic() >= deadline:
        raise StabilityTimeout(f'stability time budget exceeded: {label}')


@contextmanager
def budget(seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('stability time budget must be finite and positive')
    existing = _deadline.get()
    deadline = monotonic()+seconds
    token = _deadline.set(min(existing,deadline) if existing is not None else deadline)
    try:
        check_budget('entry')
        yield
        check_budget('completion')
    finally:
        _deadline.reset(token)
