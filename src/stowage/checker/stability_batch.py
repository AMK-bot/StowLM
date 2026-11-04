"""Ordered independent in-port checks with one rule-freeze activation snapshot.

The evaluator is an opt-in API for the future replay/finisher/reward callers;
it does not wire any production caller or alter D64 criteria. Reuse an evaluator
to keep workers warm. Each state gets the scalar checker's own time budget.
There is no shared replay deadline and no equilibrium warm start.

At density <= 1025 kg/m3, P5 implies the existing seawater maximum-displacement
row. A necessary mask must derive that row at the actual density or omit it at
denser ports; this API adds no separate displacement criterion.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import multiprocessing
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stowage.model import Instance, PartReport, ShipState
    from stowage.model.ship import WorldState


@dataclass(frozen=True)
class InPortBatchResult:
    enabled: bool
    reports: tuple[PartReport, ...]
    total_states: int
    failure_index: int | None


def _failed(report):
    return not report.ran or report.error is not None or any(
        violation.severity == 'hard' for violation in report.violations)


def _worker_init(instance, call_index):
    global _worker_instance, _worker_call_index
    # Workers use independent cold solves and one numerical thread. No process
    # reads the activation switch: the parent already captured it once.
    for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    _worker_instance, _worker_call_index = instance, call_index


def _worker_check(ship):
    from stowage.checker.stability import check_stability
    return check_stability(_worker_instance, ship, call_index=_worker_call_index,
                           in_port=True)


class InPortStateEvaluator:
    """Snapshot activation once, then evaluate ordered ship/world-state lists.

    ``enabled`` is the single L1 ``rule_freeze_v1_enabled()`` read, shared with
    callers such as 4.1 and the mask. Off returns no reports and starts no
    workers. Explicit scalar ``in_port=True`` remains available for isolated
    physics probes. The instance is copied once for consistent serial/worker
    inputs; create a new evaluator if its vessel, port or cargo data changes.

    Parallel results preserve input order and the scalar numerical algorithm.
    Completed numerical reports are bit-identical to serial cold checks with
    stable sources and single-thread numerical libraries. Resource-dependent
    timeouts retain the scalar checker's elapsed-time diagnostics.

    First-failure mode returns the prefix ending at the first unavailable or
    hard-failing state. At most ``workers-1`` later checks can be running; no
    unbounded map or full-series pre-submission defeats reward early exit.
    Returned report count is not a claim about speculative worker executions.
    Reusing the evaluator can overlap those bounded outstanding checks; closing
    waits for them, including their individual scalar timeout. For predictable
    reward latency, keep the evaluator alive across calls or use workers=1.
    Parallel callers must launch with OPENBLAS_NUM_THREADS=1 and
    OMP_NUM_THREADS=1 so spawn imports are single-threaded before initialization.
    """

    def __init__(self, instance: Instance, *, call_index: int, workers: int = 1):
        if isinstance(workers, bool) or not isinstance(workers, int):
            raise TypeError('workers must be a positive integer')
        if workers < 1:
            raise ValueError('workers must be a positive integer')
        from stowage.rules import catalogue
        self._enabled = catalogue.rule_freeze_v1_enabled()
        self.workers = workers
        self.call_index = call_index
        self._instance = instance.model_copy(deep=True)
        self._pool = None
        self._closed = False

    @property
    def enabled(self):
        """The immutable activation snapshot shared by all states/workers."""
        return self._enabled

    def __enter__(self):
        if self._closed:
            raise RuntimeError('in-port evaluator is closed')
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        """Drain only this evaluator's own workers; safe to call repeatedly."""
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
        self._closed = True

    def evaluate(self, states, *, stop_at_first_failure: bool = False) -> InPortBatchResult:
        if self._closed:
            raise RuntimeError('in-port evaluator is closed')
        ships = [getattr(state, 'ship', state) for state in states]
        if not self.enabled or not ships:
            return InPortBatchResult(self.enabled, (), len(ships), None)
        reports, failure = [], None
        if self.workers == 1:
            from stowage.checker.stability import check_stability
            for index, ship in enumerate(ships):
                report = check_stability(self._instance, ship,
                    call_index=self.call_index, in_port=True)
                reports.append(report)
                if _failed(report):
                    if failure is None:
                        failure = index
                    if stop_at_first_failure:
                        break
        else:
            if self._pool is None:
                if any(os.environ.get(name) != '1' for name in
                       ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')):
                    raise RuntimeError('parallel in-port workers require launch environment '
                                       'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1')
                self._pool = ProcessPoolExecutor(max_workers=self.workers,
                    mp_context=multiprocessing.get_context('spawn'),
                    initializer=_worker_init,
                    initargs=(self._instance, self.call_index))
            pending = deque()
            next_index = 0
            try:
                for _ in range(min(self.workers, len(ships))):
                    pending.append((next_index, self._pool.submit(_worker_check, ships[next_index])))
                    next_index += 1
                while pending:
                    index, future = pending.popleft()
                    report = future.result()
                    reports.append(report)
                    if _failed(report):
                        if failure is None:
                            failure = index
                        if stop_at_first_failure:
                            break
                    if next_index < len(ships):
                        pending.append((next_index, self._pool.submit(_worker_check, ships[next_index])))
                        next_index += 1
            finally:
                for _, future in pending:
                    future.cancel()
        return InPortBatchResult(True, tuple(reports), len(ships), failure)


def check_in_port_states(instance: Instance, states, *, call_index: int,
                         workers: int = 1, stop_at_first_failure: bool = False):
    """Convenience batch; reuse ``InPortStateEvaluator`` for warm workers.

    The existing maximum-displacement row is implied only at density <=1025
    kg/m3. A necessary mask must derive a density-consistent row or omit it at
    denser ports. This function does not impose a new displacement limit.
    """
    with InPortStateEvaluator(instance, call_index=call_index, workers=workers) as evaluator:
        return evaluator.evaluate(states, stop_at_first_failure=stop_at_first_failure)
