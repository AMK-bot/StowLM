"""D74 B.6 shared chronological, bounded pass-1 evaluation.

The reusable evaluator owns its workers and per-state numerical budgets. This
helper owns no pool: it submits at most four times the actual worker count per
chronological chunk and retains only the prefix ending at the earliest failure.
Cold independent reports preserve the serial k; warm continuation is excluded.

An optional plan deadline is checked at chunk boundaries. It does not interrupt
an active scalar solve or kill workers; a chunk may finish after the deadline,
then raises a typed compute-undecided timeout with its immutable partial result.
All measured state durations come from inside the evaluator's worker calls,
never parent queue/startup/wait times. They remain outside PartReport payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
from time import monotonic

from stowage.checker.stability_batch import _failed
from stowage.model import PartReport

CHUNK_MULTIPLIER = 4


@dataclass(frozen=True)
class InPortPrepassResult:
    """Immutable evidence and runtime metadata for a chronological prefix.

    Reports are stored as immutable JSON snapshots. Each property access makes
    independent PartReports, so neither evaluator nor consumer mutation can
    alter retained evidence. total_states counts all requested states, including
    s0; k_arr equals that count if the entire series passes.
    """

    enabled: bool
    total_states: int
    failure_index: int | None
    state_times_s: tuple[float, ...] = ()
    deadline_s: float | None = None
    _report_json: tuple[str, ...] = field(default=(), repr=False)

    @property
    def reports(self) -> tuple[PartReport, ...]:
        return tuple(PartReport.model_validate_json(payload) for payload in self._report_json)

    @property
    def evaluated_states(self) -> int:
        return len(self._report_json)

    @property
    def n(self) -> int:
        """Requested state count (including s0), an alias of total_states."""
        return self.total_states

    @property
    def complete(self) -> bool:
        return self.failure_index is not None or self.evaluated_states == self.total_states

    @property
    def k_arr(self) -> int | None:
        if not self.complete:
            return None  # A timeout's partial result cannot certify an unseen tail.
        return self.failure_index if self.failure_index is not None else self.total_states

    @property
    def measured_state_count(self) -> int:
        return min(8, len(self.state_times_s))

    @property
    def measured_state_time_s(self) -> float:
        times = self.state_times_s[:8]
        return float(median(times)) if times else 0.


class InPortPrepassTimeout(TimeoutError):
    """The finisher maps this compute-undecided result to prepass_timeout."""

    cause = 'prepass_timeout'

    def __init__(self, *, partial: InPortPrepassResult, elapsed_s: float, deadline_s: float):
        self.partial = partial
        self.elapsed_s = elapsed_s
        self.deadline_s = deadline_s
        super().__init__(f'prepass_timeout: {elapsed_s:.6g}s elapsed, {deadline_s:.6g}s deadline')


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f'{label} must be a positive integer')
    return value


def _report_snapshot(report):
    if not isinstance(report, PartReport):
        raise TypeError('pass1 evaluator must return PartReport objects')
    numbers = [*report.metrics.values(), *report.margins.values(),
               *(value for violation in report.violations
                 for value in (violation.measured, violation.limit) if value is not None)]
    if not all(math.isfinite(value) for value in numbers):
        raise RuntimeError('nonfinite pass1 report cannot establish a passing prefix')
    return report.model_dump_json()


def arrival_prepass(evaluator, ships, *, workers=None, chunk_multiplier=CHUNK_MULTIPLIER,
                    deadline_s=None, calibrated_deadline=False) -> InPortPrepassResult:
    """Evaluate arrival states in chronological chunks, stopping at the first failure.

    evaluator.evaluate (or a callable evaluator) must return ordered .reports
    and support stop_at_first_failure=True. Its optional .state_times_s must
    align with those reports. Missing timings are retained as missing, never
    replaced by parent wall time; calibrated deadlines require worker timings.

    deadline_s is an optional duration from this call's start. With
    calibrated_deadline=True the plan limit is max(30, 2*t_hat*N/workers), using
    the first min(8, available) logical-state durations. A supplied explicit
    duration additionally caps that limit. Checkers may omit the plan deadline;
    the finisher opts into it and translates InPortPrepassTimeout to not_found.
    """
    actual_workers = getattr(evaluator, 'workers', None)
    if actual_workers is not None:
        _positive_integer(actual_workers, 'evaluator workers')
    workers = _positive_integer(actual_workers if workers is None and actual_workers is not None
                                else (1 if workers is None else workers), 'workers')
    if actual_workers is not None and workers != actual_workers:
        raise ValueError('workers must match the evaluator actual worker count')
    chunk_multiplier = _positive_integer(chunk_multiplier, 'chunk multiplier')
    if getattr(evaluator, 'warm_start', False):
        raise ValueError('pass1 requires cold independent state evaluation')
    if deadline_s is not None and (isinstance(deadline_s, bool)
            or not isinstance(deadline_s, (int, float))
            or not math.isfinite(deadline_s) or deadline_s <= 0):
        raise ValueError('deadline_s must be a positive finite duration')
    evaluate = getattr(evaluator, 'evaluate', evaluator if callable(evaluator) else None)
    if not callable(evaluate):
        raise TypeError('pass1 requires an evaluator or evaluation callback')
    states = tuple(ships)
    total = len(states)
    snapshots, times, failure = [], [], None
    timing_available = None
    started = monotonic()
    limit = min(30., deadline_s) if calibrated_deadline and deadline_s is not None else (
        30. if calibrated_deadline else deadline_s)

    def result():
        return InPortPrepassResult(True, total, failure, tuple(times), limit, tuple(snapshots))

    def check_deadline():
        if limit is not None:
            elapsed = monotonic()-started
            if elapsed >= limit:
                raise InPortPrepassTimeout(partial=result(), elapsed_s=elapsed, deadline_s=limit)

    size = chunk_multiplier*workers
    for start in range(0, total, size):
        check_deadline()
        chunk = states[start:start+size]
        batch = evaluate(chunk, stop_at_first_failure=True)
        if not getattr(batch, 'enabled', True):
            raise RuntimeError('pass1 evaluator is disabled; no prefix verdict is available')
        reports = tuple(batch.reports)
        if len(reports) > len(chunk):
            raise RuntimeError('pass1 evaluator returned too many reports')
        local_failure = next((i for i, report in enumerate(reports) if _failed(report)), None)
        if local_failure is None and len(reports) != len(chunk):
            raise RuntimeError('incomplete pass1 chunk without a failed state')
        kept = local_failure+1 if local_failure is not None else len(reports)
        durations = getattr(batch, 'state_times_s', None)
        available = durations is not None
        if timing_available is not None and available != timing_available:
            raise RuntimeError('inconsistent pass1 worker timing availability')
        timing_available = available
        if available:
            durations = tuple(durations)
            if len(durations) != len(reports) or any(isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                    for value in durations):
                raise RuntimeError('invalid pass1 worker state durations')
            times.extend(float(value) for value in durations[:kept])
        elif calibrated_deadline:
            raise ValueError('calibrated prepass deadline requires measured worker state durations')
        snapshots.extend(_report_snapshot(report) for report in reports[:kept])
        if local_failure is not None:
            failure = start+local_failure
        if calibrated_deadline and times:
            measured = float(median(times[:8]))
            limit = max(30., 2.*measured*total/workers)
            if deadline_s is not None:
                limit = min(limit, deadline_s)
        check_deadline()
        if failure is not None:
            break
    return result()


# Explicit descriptive alias for consumers using a pass-number API.
evaluate_pass1 = arrival_prepass
