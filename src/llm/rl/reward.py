"""CPU rewards for masked Group Relative Policy Optimization (GRPO).

GRPO compares several completions of the same prompt: subtract their mean reward
and divide by their standard deviation to obtain advantages, without a learned
value model. See Shao et al. (2024), DeepSeekMath, section 4:
https://arxiv.org/abs/2402.03300 . Groups must never mix different instances.

The full checker is the gate, after departure ballast has been finalized. Only a
passing plan earns a bounded negative transform of the D11 ``weighted_nonproductive`` objective;
that count already includes projected later-port restows and hatch-cover lifts.
A failed check, incomplete rollout, or failed ballast finishing earns a fixed
penalty, regardless of its move count. A mask dead end earns the separately
configurable fixed dead-end penalty (the same penalty by default). We never
repair a rollout here: doing so would reward actions the policy did not take.
Checker timeouts receive one doubled-budget recheck; unresolved timeouts have
no scalar reward and are excluded from the group's advantage normalization.
The ballast finisher is treated the same way: ``infeasible_proven`` (a proof
for this fixed stow) earns the fixed penalty, but ``not_found`` is only a spent
search budget, so it gets one retry at a larger budget and, if still unresolved,
is dropped (``finisher_timeout``) rather than penalised or counted as a pass.
The success transform is monotone in weighted moves and strictly above both
fixed penalties, even for extreme finite configuration values. Raw D11 moves
remain in the outcome; the scalar training reward is not a move-count metric.
This module has no accelerator, training-framework, network, or cloud dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
import os
import time

from stowage.checker.full import check, report_matches_inputs
from stowage.model import CheckReport, Instance, MovePlan, PartReport, PlannerResult, Violation
from stowage.planner import finalize
from llm.rl.checker_retry import CheckerAttempts, check_with_retry


# Ballast-finisher budget per call. MUST equal the evaluation planner's budget
# (``remaining = 30.0`` before ``finalize`` in src/llm/stowlm/planner.py, also
# ``stowage.planner.finalize``'s default) so GRPO scores plans under the same
# finisher search as evaluation; tests/llm/test_rl_reward_finisher.py pins it.
FINISHER_TIME_LIMIT_S = 30.0
FINISHER_RETRY_FACTOR = 2.0
# A not_found returned after this fraction of its budget is taken as budget-
# limited; an earlier one is a search that stopped by itself (no proof either).
FINISHER_BUDGET_USED_FRACTION = 0.9
_clock = time.monotonic  # patched by tests to simulate elapsed finisher time

# Reasons whose value is None: no scalar reward, excluded from advantages and
# counted by the drop-rate admission policy. Never a pass.
DROP_REASONS = ("checker_timeout", "finisher_timeout")


@dataclass(frozen=True)
class RewardConfig:
    """Reward coefficients and CPU ballast budgets; penalties stay fixed per run.

    ``ballast_retry_time_limit_s`` defaults to ``FINISHER_RETRY_FACTOR`` times
    the base budget and is resolved at construction, so ``dataclasses.asdict``
    gives the exact base and retry budgets for the GRPO run identity.
    """

    failure_penalty: float = -10_000.0
    dead_end_penalty: float = -10_000.0
    move_weight: float = 1.0
    ballast_time_limit_s: float = FINISHER_TIME_LIMIT_S
    ballast_retry_time_limit_s: float | None = None
    # D88 (MVP v4): the evaluation finishes plans with the V4 ballast finisher, STOWAGE_BALLAST_V4=1 scoped to
    # finalize only (experiments/eval-c1/run_c1.py ballast_v4_finalize). GRPO must score with the same finisher.
    ballast_v4_finalize: bool = False

    def __post_init__(self) -> None:
        for name in ("failure_penalty", "dead_end_penalty"):
            value = getattr(self, name)
            if not math.isfinite(value) or value >= 0:
                raise ValueError(f"{name} must be finite and negative")
        if not math.isfinite(self.move_weight) or self.move_weight <= 0:
            raise ValueError("move_weight must be finite and positive")
        if not math.isfinite(self.ballast_time_limit_s) or self.ballast_time_limit_s <= 0:
            raise ValueError("ballast_time_limit_s must be finite and positive")
        if self.ballast_retry_time_limit_s is None:
            object.__setattr__(self, "ballast_retry_time_limit_s",
                               FINISHER_RETRY_FACTOR * self.ballast_time_limit_s)
        retry = self.ballast_retry_time_limit_s
        if not math.isfinite(retry) or retry <= self.ballast_time_limit_s:
            raise ValueError("ballast_retry_time_limit_s must be finite and exceed ballast_time_limit_s")


def _passing_reward(moves: float, config: RewardConfig) -> float:
    """Map cost c to -s*c/(s+c), where -s is the higher failure penalty.

    This leaves fixed failure rewards unchanged and separates their range from
    all successful rewards without an unproved instance objective bound.
    Branches avoid overflow in the rational expression. Cost multiplication
    may overflow to infinity; its limiting transformed reward is still finite.
    Float saturation can tie extreme successful costs, but nextafter ensures
    it can never tie either failure penalty, including subnormal penalties.
    """
    floor = max(config.failure_penalty, config.dead_end_penalty)
    span = -floor
    cost = config.move_weight * moves
    if cost <= span:
        value = -cost / (1.0 + cost / span)
    else:
        value = floor / (1.0 + span / cost)
    return max(value, math.nextafter(floor, 0.0))


@dataclass(frozen=True)
class RewardOutcome:
    """A scalar training reward plus the checker evidence; never a shipping verdict alone."""

    value: float | None
    passed: bool
    reason: str
    result: PlannerResult
    report: CheckReport | None = None
    weighted_nonproductive: float | None = None
    diagnostic: str | None = None
    checker_retries: int = 0
    checker_attempts: CheckerAttempts | None = None
    finisher_retries: int = 0
    finisher_budgets_s: tuple[float, ...] = ()
    finisher_wall_times_s: tuple[float, ...] = ()

    @property
    def dropped(self) -> bool:
        """An unresolved checker or finisher timeout contributes no reward or advantage."""
        return self.reason in DROP_REASONS


def evaluate_reward(
    instance: Instance,
    result: PlannerResult | MovePlan,
    *,
    config: RewardConfig | None = None,
    dead_end: bool = False,
) -> RewardOutcome:
    """Finalize then check a single-call rollout, returning a fail-closed reward.

    Non-``ok`` statuses (including truncation/timeouts) are incomplete rollouts.
    Explicit ``dead_end`` also applies to token decoders that only return a plan.
    Unexpected checker/finisher errors receive the fixed failure penalty and a
    diagnostic; they cannot accidentally produce positive training feedback.
    Multi-call results are rejected: each planned call needs its own instance
    and checker evaluation, rather than rewarding only the first call.
    """
    config = config or RewardConfig()
    if isinstance(result, MovePlan):
        result = PlannerResult(status="ok", plan=result, planner="grpo.rollout", planner_version="1")
    # Re-evaluation revokes any earlier shipping authorization, even on early exit.
    result._shipping_evidence = None
    if dead_end or result.status == "dead_end":
        return RewardOutcome(config.dead_end_penalty, False, "dead_end", result)
    if result.status != "ok" or result.plan is None:
        return RewardOutcome(config.failure_penalty, False, "incomplete", result)
    if result.later_plans:
        return RewardOutcome(config.failure_penalty, False, "unsupported_multicall", result)
    checker_retries = 0
    attempts = None
    finisher_budgets: list[float] = []
    finisher_times: list[float] = []

    def failed(reason: str, report: CheckReport, *, diagnostic: str | None = None) -> RewardOutcome:
        # A passing but incomplete/unbound report cannot authorize this result.
        if report.passed:
            report = report.model_copy(deep=True, update={"passed": False})
        result._record_full_check(instance, report)
        return RewardOutcome(config.failure_penalty, False, reason, result, report,
                             diagnostic=diagnostic, checker_retries=checker_retries,
                             checker_attempts=attempts, **finisher_evidence())

    def finisher_evidence() -> dict:
        return {"finisher_retries": max(0, len(finisher_budgets) - 1),
                "finisher_budgets_s": tuple(finisher_budgets),
                "finisher_wall_times_s": tuple(finisher_times)}

    def run_finisher(budget: float) -> tuple[PlannerResult, bool]:
        finisher_budgets.append(budget)
        started = _clock()
        if config.ballast_v4_finalize:
            if os.environ.get("STOWAGE_BALLAST_V4") is not None:
                raise RuntimeError("STOWAGE_BALLAST_V4 must be unset globally; ballast_v4_finalize scopes it")
            os.environ["STOWAGE_BALLAST_V4"] = "1"
        try:
            finalized = finalize(instance, rollout, time_limit_s=budget)
        finally:
            if config.ballast_v4_finalize:
                os.environ.pop("STOWAGE_BALLAST_V4", None)
        finisher_times.append(_clock() - started)
        finalized._shipping_evidence = None
        return finalized, _finisher_budget_limited(finalized, budget, finisher_times[-1])

    rollout = result
    try:
        result, limited = run_finisher(config.ballast_time_limit_s)
        if limited:
            # Mirror check_with_retry: one retry at a larger budget, then drop.
            result, limited = run_finisher(config.ballast_retry_time_limit_s)
            if limited:
                return RewardOutcome(None, False, "finisher_timeout", result,
                                     diagnostic=result.message, **finisher_evidence())
        if result.status != "ok" or result.plan is None or result.plan.ballast is None:
            reason = ("finalization_infeasible"
                      if result.metrics.get("ballast_status") == "infeasible_proven"
                      or finisher_cause_infeasible(result.metrics.get("not_found_cause"))
                      else "finalization_failed")
            return RewardOutcome(config.failure_penalty, False, reason, result,
                                 diagnostic=result.message, **finisher_evidence())
        attempts = check_with_retry(instance, result.plan, checker=check)
        report, checker_retries = attempts.report, attempts.retries
        result._record_full_check(instance, report)
        if attempts.definitive_failure:
            return failed("checker_failed", report)
        if attempts.retry_error:
            return failed("evaluation_error", report, diagnostic=attempts.retry_error)
        if attempts.timed_out:
            return RewardOutcome(None, False, "checker_timeout", result, report,
                                 checker_retries=checker_retries, checker_attempts=attempts,
                                 **finisher_evidence())
        if not report.passed:
            return failed("checker_failed", report)
        if report.count is None:
            return failed("missing_move_count", report)
        moves = report.count.weighted_nonproductive
        if not math.isfinite(moves) or moves < 0:
            return failed("invalid_move_count", report)
        if not report_matches_inputs(report, instance, result.plan):
            return failed("report_binding_failed", report)
        # Use the same complete, clean-evidence gate as the shipping pipeline.
        # Never modify the checked plan after the checker binds its contents.
        result._record_full_check(instance, report)
        if not result.shippable:
            return failed("unshippable", report)
        value = _passing_reward(moves, config)
        return RewardOutcome(value, True, "passed", result, report, moves,
                             checker_retries=checker_retries, checker_attempts=attempts,
                             **finisher_evidence())
    except Exception as error:
        diagnostic = f"{type(error).__name__}: {error}"
        report = CheckReport(passed=False, parts={"reward_evaluation": PartReport(
            name="reward_evaluation", ran=False, error=diagnostic,
            violations=[Violation(rule_id="checker.internal_error", severity="hard",
                                  call_index=instance.call_index, message=diagnostic)])})
        return failed("evaluation_error", report, diagnostic=diagnostic)


def _finisher_budget_limited(result: PlannerResult, budget_s: float, elapsed_s: float) -> bool:
    """True when the ballast finisher ran out of its search budget.

    ``finalize`` (src/stowage/planner.py:72,81) copies the BallastOutcome status
    into ``metrics["ballast_status"]`` and maps ``infeasible_proven`` to status
    ``infeasible`` and ``not_found`` to ``error``. The finisher's own
    ``not_found_cause`` (``timeout:*`` vs e.g. ``rate_infeasible:R1``,
    ``search_miss``; src/stowage/solver/ballast.py:177-186) is not propagated
    by ``finalize``, and no ``not_found`` path certifies an exhaustive search:
    only ``infeasible_proven`` carries a proof (src/stowage/model/results.py:182-187).
    A ``not_found`` is therefore never treated as infeasible. Without the cause,
    the observable time-limit signal is wall time: a search that hit its
    deadline returns at the budget (finish_ballast reserves at most 5 % for
    transport), so ``not_found`` after >= 90 % of the budget is budget-limited
    (retried, then dropped). One that returns earlier stopped by itself (e.g.
    exhausted proposals, missing hull data) and keeps the fixed failure penalty
    as ``finalization_failed``; this is not a proof, and is a known residual:
    D64 prepass/acceptance time-outs on their own clocks can land here too.
    """
    if result.status != "error" or result.metrics.get("ballast_status") != "not_found":
        return False
    cause = result.metrics.get("not_found_cause")
    if cause is not None:
        # Leader rule (29 Oct): once finalize forwards the finisher's cause (w2-l3-finisher), a not_found is a
        # proven failure only if the cause says infeasible; time-outs and inconclusive searches (timeout:*,
        # search_miss, unknown_witness) are retried, then dropped, however early they returned.
        return not finisher_cause_infeasible(cause)
    return elapsed_s >= FINISHER_BUDGET_USED_FRACTION * budget_s


# D74 (user decision): R1 is a reported ballast delay, never a reason a plan is infeasible, so an R1 cause is not a
# proof (retried, then dropped like other inconclusive searches). R2 and window failures still prove infeasibility.
NON_PROOF_INFEASIBLE_CAUSES = frozenset({"rate_infeasible:R1"})


def finisher_cause_infeasible(cause: str | None) -> bool:
    """A finisher ``not_found_cause`` that proves infeasibility (e.g. ``rate_infeasible:R2``; never R1, D74)."""
    return (cause is not None and not cause.startswith("timeout:") and "infeasible" in cause
            and cause not in NON_PROOF_INFEASIBLE_CAUSES)


class GroupAdvantages(list[float]):
    """Aligned advantages with reward-availability statistics for trainer logs.

    ``checker_timeout_drop_*`` keep their historic names because the admission
    policy and trainer logs read them; without ``reasons`` they count every
    ``None`` reward. With ``reasons`` they still hold the total drop rate (so
    finisher time-outs reach the same alert/abort policy); ``timeout_drop_*``
    repeat the total under an unambiguous name, and
    ``timeout_drop_count_by_reason`` / ``finisher_timeout_drop_*`` separate
    checker from finisher time-outs.
    """

    def __init__(self, values: Sequence[float], *, dropped: int,
                 reasons: Sequence[str | None] | None = None,
                 rewards: Sequence[float | None] | None = None):
        super().__init__(values)
        self.stats = {"sample_count": len(values), "reward_count": len(values) - dropped,
                      "checker_timeout_drop_count": dropped,
                      "checker_timeout_drop_rate": dropped / len(values) if values else 0.0}
        if reasons is not None:
            labels = [reason if reason in DROP_REASONS else "unlabelled"
                      for reason, reward in zip(reasons, rewards or ()) if reward is None]
            by_reason = {reason: labels.count(reason) for reason in (*DROP_REASONS, "unlabelled")
                         if labels.count(reason) or reason != "unlabelled"}
            self.stats["timeout_drop_count"] = dropped
            self.stats["timeout_drop_rate"] = self.stats["checker_timeout_drop_rate"]
            self.stats["timeout_drop_count_by_reason"] = by_reason
            self.stats["finisher_timeout_drop_count"] = by_reason["finisher_timeout"]
            self.stats["finisher_timeout_drop_rate"] = (
                by_reason["finisher_timeout"] / len(values) if values else 0.0)


def normalize_group_rewards(rewards: Sequence[float | None], *, epsilon: float = 1e-8,
                            reasons: Sequence[str | None] | None = None) -> GroupAdvantages:
    """Normalize one prompt's rewards using population standard deviation.

    Equal rewards (including a singleton group) yield zero advantages. Empty
    groups return an empty list. Nonfinite rewards are rejected before training.
    The explicit population convention makes CPU and trainer adapters agree.
    ``None`` rewards mark unresolved checker or finisher timeouts: exclude them
    from the mean/std, return aligned zero advantages, and report their
    count/drop rate. Optional ``reasons`` (aligned ``RewardOutcome.reason``)
    adds per-reason drop counts.
    """
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("epsilon must be finite and positive")
    if reasons is not None and len(reasons) != len(rewards):
        raise ValueError("reasons must align with rewards")
    for reward, reason in zip(rewards, reasons or ()):
        if reason in DROP_REASONS and reward is not None:
            raise ValueError(f"{reason} must have no scalar reward")
    extra = {"reasons": reasons, "rewards": rewards} if reasons is not None else {}
    values = [float(value) for value in rewards if value is not None]
    dropped = len(rewards) - len(values)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("rewards must be finite")
    if not values:
        return GroupAdvantages([0.0] * len(rewards), dropped=dropped, **extra)
    mean = math.fsum(value / len(values) for value in values)
    centered = [value - mean for value in values]
    scale = max(abs(value) for value in centered)
    if scale == 0:
        return GroupAdvantages([0.0] * len(rewards), dropped=dropped, **extra)
    # Scale before squaring to avoid overflow for large fixed penalties.
    std = scale * math.sqrt(math.fsum((value / scale) ** 2 for value in centered) / len(values))
    normalized = iter(value / (std + epsilon) for value in centered)
    return GroupAdvantages([next(normalized) if value is not None else 0.0 for value in rewards],
                           dropped=dropped, **extra)


TIMEOUT_DROP_THRESHOLD = 0.05
TIMEOUT_BAD_STEP_LIMIT = 3


def advance_timeout_policy(group_stats: Sequence[dict], *, previous: dict | None = None) -> dict:
    """Admission choice: >5% drops in any group, abort after three bad steps.

    ``checker_timeout_drop_rate`` is the total unresolved-timeout drop rate,
    including finisher time-outs (see ``GroupAdvantages``).

    Three consecutive steps distinguish a transient budget overrun from sustained
    missing feedback; this is an explicit training policy, not a physics limit.
    """
    rates = [float(stats["checker_timeout_drop_rate"]) for stats in group_stats]
    if any(not math.isfinite(rate) or not 0 <= rate <= 1 for rate in rates):
        raise ValueError("group timeout drop rates must lie in [0, 1]")
    bad = any(rate > TIMEOUT_DROP_THRESHOLD for rate in rates)
    streak = int((previous or {}).get("consecutive_bad_steps", 0)) + 1 if bad else 0
    return {"group_drop_rates": rates, "any_group_over_threshold": bad,
            "consecutive_bad_steps": streak, "abort": streak >= TIMEOUT_BAD_STEP_LIMIT,
            "drop_rate_threshold": TIMEOUT_DROP_THRESHOLD,
            "consecutive_bad_step_limit": TIMEOUT_BAD_STEP_LIMIT}


def require_timeout_admission(policy: dict, persist_abort) -> None:
    """Persist the aborted step's diagnostics before refusing a policy update."""
    if policy["abort"]:
        persist_abort()
        raise RuntimeError("timeout drop admission (checker/finisher) aborted after three consecutive bad steps")


def dropped_sample_diagnostics(instance: Instance, outcome) -> dict:
    """Log observed trim/list/GM and tanks; unavailable physics stays null."""
    result = getattr(outcome, "result", None)
    plan = getattr(result, "plan", None)
    report = getattr(outcome, "report", None)
    conditions = {}
    if report is not None:
        for name, part in report.parts.items():
            if part.name == "stability" or "stability" in name:
                conditions[name] = {key: part.metrics.get(key) for key in ("trim_m", "list_rad", "gm_m")}
    observed = next((metrics for name, metrics in conditions.items() if name == "stability"),
                    {key: None for key in ("trim_m", "list_rad", "gm_m")})
    tanks = dict(instance.onboard.tank_masses_kg)
    if plan is not None and plan.ballast is not None:
        tanks.update(plan.ballast.tank_masses_kg)
    return {"instance_id": instance.id, "plan_id": plan.id if plan is not None else None,
            **observed, "condition_metrics": conditions, "tank_masses_kg": tanks}
