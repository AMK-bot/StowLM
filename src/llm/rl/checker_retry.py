"""Shared full-check retry; resource exhaustion never erases evaluated failures."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import os
from threading import RLock

from stowage.model import CheckReport, Instance, MovePlan, PartReport

CHECKER_BUDGET_ENV = "STOWAGE_STABILITY_TIME_BUDGET_S"
CHECKER_BUDGET_LOCK = RLock()


def report_timed_out(report: CheckReport) -> bool:
    return any(part.metrics.get("time_budget_exceeded", 0) > 0
               for part in report.parts.values())


def report_has_evaluated_hard_failure(report: CheckReport) -> bool:
    """Only completed, error-free parts establish a definite invalid plan."""
    moves = report.parts.get("moves")
    if moves is not None and moves.ran and not moves.error and report.shut_out:
        return True
    return any(part.ran and not part.error and any(
        violation.severity == "hard" for violation in part.violations)
        for part in report.parts.values())


@dataclass(frozen=True)
class CheckerAttempts:
    initial_report: CheckReport
    retry_report: CheckReport | None = None
    retry_error: str | None = None
    retry_budget_s: float | None = None

    @property
    def retries(self) -> int:
        return int(self.retry_budget_s is not None)

    @property
    def latest_report(self) -> CheckReport:
        return self.retry_report if self.retry_report is not None else self.initial_report

    @property
    def definitive_failure(self) -> bool:
        return any(report_has_evaluated_hard_failure(report) for report in (
            self.initial_report, self.latest_report))

    @property
    def timed_out(self) -> bool:
        return report_timed_out(self.latest_report)

    @property
    def report(self) -> CheckReport:
        """Latest evidence, retaining earlier completed hard failures fail-closed."""
        latest = self.latest_report
        if not self.retry_error and not (
                self.retry_report is not None and report_has_evaluated_hard_failure(self.initial_report)):
            return latest
        merged = latest.model_copy(deep=True)
        if self.retry_error:
            merged.parts["checker_retry"] = PartReport(
                name="checker_retry", ran=False, error=self.retry_error)
            merged.passed = False
        if self.retry_report is not None:
            for name, part in self.initial_report.parts.items():
                if not part.ran or part.error:
                    continue
                hard = [v for v in part.violations if v.severity == "hard"]
                if hard:
                    merged.parts[f"initial_check:{name}"] = part.model_copy(deep=True)
                    for violation in hard:
                        if violation not in merged.violations:
                            merged.violations.append(violation.model_copy(deep=True))
                    merged.passed = False
            if report_has_evaluated_hard_failure(self.initial_report):
                merged.passed = False
                merged.shut_out = sorted(set(merged.shut_out) | set(self.initial_report.shut_out))
        return merged


def check_with_retry(instance: Instance, plan: MovePlan, *,
                     checker: Callable[[Instance, MovePlan], CheckReport] | None = None,
                     initial_report: CheckReport | None = None) -> CheckerAttempts:
    """Recheck timeout once at twice its recorded budget; expose both verdicts.

    The checker currently accepts its budget only through the process environment.
    Serialize participating calls and restore it even when a retry raises. Direct
    checker threads must share CHECKER_BUDGET_LOCK or use process isolation.
    """
    if checker is None:
        from stowage.checker.full import check
        checker = check
    with CHECKER_BUDGET_LOCK:
        initial = initial_report if initial_report is not None else checker(instance, plan)
        if not report_timed_out(initial):
            return CheckerAttempts(initial)
        budgets = [part.metrics.get("time_budget_s", 30.0)
                   for part in initial.parts.values()
                   if part.metrics.get("time_budget_exceeded", 0) > 0]
        seconds = max((value for value in budgets if math.isfinite(value) and value > 0), default=30.0)
        retry_budget = seconds * 2
        previous = os.environ.get(CHECKER_BUDGET_ENV)
        try:
            os.environ[CHECKER_BUDGET_ENV] = str(retry_budget)
            try:
                retry = checker(instance, plan)
            except Exception as error:
                return CheckerAttempts(initial, retry_error=f"{type(error).__name__}: {error}",
                                       retry_budget_s=retry_budget)
            return CheckerAttempts(initial, retry_report=retry, retry_budget_s=retry_budget)
        finally:
            if previous is None:
                os.environ.pop(CHECKER_BUDGET_ENV, None)
            else:
                os.environ[CHECKER_BUDGET_ENV] = previous
