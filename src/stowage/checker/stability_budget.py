"""Cache-neutral production work policy and opt-in diagnostic poll limits.

Physical numerical kernels retain their own fixed convergence/evaluation caps.
Wall time and cache-sensitive poll totals never decide a production verdict.
The legacy ``budget(seconds)`` API validates its argument for compatibility but
does not impose elapsed-time exhaustion. An external watchdog can mark a run
unavailable; it cannot turn incomplete checking into a pass.

``diagnostic_budget`` is intentionally outside production paths. Poll counts
depend on cache hits and therefore are not a physical acceptance policy.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import math

_diagnostic_remaining = ContextVar('stability_diagnostic_polls_remaining', default=())


class StabilityTimeout(Exception):
    """Compatibility exception for explicitly requested diagnostic exhaustion."""


StabilityWorkLimit = StabilityTimeout


def check_budget(label=''):
    """No production cutoff; charge polls only inside diagnostic_budget."""
    remaining = _diagnostic_remaining.get()
    if not remaining:
        return
    if any(value <= 0 for value in remaining):
        raise StabilityTimeout(f'diagnostic stability work limit exceeded: {label}')
    _diagnostic_remaining.set(tuple(value-1 for value in remaining))


@contextmanager
def budget(seconds):
    """Retain legacy callers/config validation without a clock-dependent limit.

    ``seconds`` is the legacy argument name, not a current duration guarantee.
    Nested production scopes remain valid; an enclosing explicit diagnostic
    scope still observes their entry/completion polls and is never reset here.
    """
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('stability time budget must be finite and positive')
    check_budget('entry')
    yield
    check_budget('completion')


@contextmanager
def diagnostic_budget(polls):
    """Opt-in debug-only poll cap, retaining outer consumption across nesting.

    Immutable ContextVar tuples avoid mutable budget sharing between copied
    contexts. No production caller selects this cache-sensitive diagnostic.
    """
    if isinstance(polls, bool) or not isinstance(polls, int) or polls <= 0:
        raise ValueError('diagnostic stability work limit must be a positive integer')
    previous = _diagnostic_remaining.get()
    token = _diagnostic_remaining.set((*previous, polls))
    try:
        yield
    finally:
        consumed_parent = _diagnostic_remaining.get()[:-1]
        _diagnostic_remaining.reset(token)
        if previous:
            _diagnostic_remaining.set(consumed_parent)
