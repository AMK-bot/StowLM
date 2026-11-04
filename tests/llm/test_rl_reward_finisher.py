"""Ballast-finisher outcomes in the GRPO reward: proof vs spent search budget."""

from dataclasses import asdict
import inspect
from pathlib import Path
import re

import pytest

from llm.rl import reward
from stowage.model import BallastCondition, MovePlan, PlannerResult
from stowage.model.fixtures import build_instance

ROOT = Path(__file__).resolve().parents[2]


def _complete_report(*args, **kwargs):
    """Reuse the content-bound synthetic report from test_rl_reward (importlib mode)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rl_reward_tests", Path(__file__).with_name("test_rl_reward.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.complete_report(*args, **kwargs)


@pytest.fixture
def candidate():
    instance = build_instance()
    plan = MovePlan(id="reward.finisher", instance_id=instance.id, call_index=instance.call_index)
    return instance, PlannerResult(status="ok", plan=plan, planner="fake", planner_version="1")


def finished(result):
    return result.model_copy(update={
        "metrics": {**result.metrics, "ballast_status": "ok"},
        "plan": result.plan.model_copy(update={
            "ballast": BallastCondition(tank_masses_kg={}, source="finisher")})})


def unfinished(result, status, cause=None):
    return result.model_copy(update={
        "status": "infeasible" if status == "infeasible_proven" else "error",
        "metrics": {**result.metrics, "ballast_status": status,
                    **({"not_found_cause": cause} if cause is not None else {})},
        "message": "proof: GM bound" if status == "infeasible_proven" else "not found in time"})


def scripted_finalize(monkeypatch, result, statuses, *, fraction=1.0, cause=None):
    """Fake finisher; ``not_found`` spends ``fraction`` of its budget on a fake clock."""
    calls = []
    now = [0.0]
    monkeypatch.setattr(reward, "_clock", lambda: now[0])

    def finalize(instance, actual, *, time_limit_s):
        assert actual is result  # every attempt restarts from the unballasted rollout
        calls.append(time_limit_s)
        if statuses[len(calls) - 1] == "not_found":
            now[0] += fraction * time_limit_s
        status = statuses[len(calls) - 1]
        return finished(actual) if status == "ok" else unfinished(actual, status, cause)

    monkeypatch.setattr(reward, "finalize", finalize)
    return calls


def test_proven_infeasible_keeps_fixed_penalty_without_retry(monkeypatch, candidate):
    instance, result = candidate
    calls = scripted_finalize(monkeypatch, result, ["infeasible_proven"])
    monkeypatch.setattr(reward, "check", lambda *a: pytest.fail("no ballast, no check"))
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.reason == "finalization_infeasible"
    assert outcome.value == reward.RewardConfig().failure_penalty
    assert not outcome.passed and not outcome.dropped
    assert calls == [reward.FINISHER_TIME_LIMIT_S] and outcome.finisher_retries == 0


def test_not_found_then_ok_on_retry_reaches_checker(monkeypatch, candidate):
    instance, result = candidate
    calls = scripted_finalize(monkeypatch, result, ["not_found", "ok"])
    checked = []

    def check(actual_instance, plan):
        checked.append(plan.ballast)
        return _complete_report(actual_instance, plan, moves=4.0)

    monkeypatch.setattr(reward, "check", check)
    config = reward.RewardConfig(ballast_time_limit_s=5)
    outcome = reward.evaluate_reward(instance, result, config=config)
    assert calls == [5, 10] and len(checked) == 1 and checked[0] is not None
    assert outcome.passed and outcome.reason == "passed"
    assert outcome.finisher_retries == 1 and outcome.finisher_budgets_s == (5, 10)


def test_not_found_twice_is_dropped_as_finisher_timeout(monkeypatch, candidate):
    instance, result = candidate
    calls = scripted_finalize(monkeypatch, result, ["not_found", "not_found"])
    monkeypatch.setattr(reward, "check", lambda *a: pytest.fail("no ballast, no check"))
    config = reward.RewardConfig(ballast_time_limit_s=3, ballast_retry_time_limit_s=9)
    outcome = reward.evaluate_reward(instance, result, config=config)
    assert calls == [3, 9]  # the retry used the higher budget
    assert outcome.reason == "finisher_timeout"
    assert outcome.value is None and outcome.dropped and not outcome.passed
    assert not outcome.result.shippable
    assert outcome.finisher_retries == 1 and outcome.finisher_wall_times_s == (3, 9)


def test_early_not_found_is_not_a_timeout_and_keeps_penalty(monkeypatch, candidate):
    # Stopped by itself well inside the budget: no proof, but no budget issue either.
    instance, result = candidate
    calls = scripted_finalize(monkeypatch, result, ["not_found"], fraction=0.2)
    monkeypatch.setattr(reward, "check", lambda *a: pytest.fail("no ballast, no check"))
    outcome = reward.evaluate_reward(instance, result)
    assert calls == [reward.FINISHER_TIME_LIMIT_S]
    assert outcome.reason == "finalization_failed" and outcome.value == -10_000
    assert not outcome.dropped and not outcome.passed


def test_unlabelled_finisher_error_still_fails_closed(monkeypatch, candidate):
    instance, result = candidate
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: result.model_copy(update={"status": "error"}))
    monkeypatch.setattr(reward, "check", lambda *a: pytest.fail("failed ballast must not pass"))
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.reason == "finalization_failed" and outcome.value == -10_000


def test_finisher_timeouts_flow_through_drop_accounting_and_admission():
    rewards = [-2.0, None, None, -6.0]
    reasons = ["passed", "finisher_timeout", "checker_timeout", "checker_failed"]
    advantages = reward.normalize_group_rewards(rewards, reasons=reasons)
    assert advantages == pytest.approx(
        [reward.normalize_group_rewards([-2, -6])[0], 0, 0, reward.normalize_group_rewards([-2, -6])[1]])
    stats = advantages.stats
    assert stats["reward_count"] == 2
    assert stats["checker_timeout_drop_count"] == 2 and stats["checker_timeout_drop_rate"] == 0.5
    assert stats["finisher_timeout_drop_count"] == 1 and stats["finisher_timeout_drop_rate"] == 0.25
    assert stats["timeout_drop_count"] == 2 and stats["timeout_drop_rate"] == 0.5
    assert stats["timeout_drop_count_by_reason"] == {"checker_timeout": 1, "finisher_timeout": 1}
    # Unlabelled callers (modal_grpo/dry_run pass scores only) still count it.
    assert reward.normalize_group_rewards(rewards).stats["checker_timeout_drop_count"] == 2
    policy = reward.advance_timeout_policy([stats])
    assert policy["any_group_over_threshold"]
    with pytest.raises(ValueError, match="no scalar reward"):
        reward.normalize_group_rewards([1.0], reasons=["finisher_timeout"])
    with pytest.raises(ValueError, match="align"):
        reward.normalize_group_rewards([1.0], reasons=[])


def test_finisher_budget_matches_evaluation_and_is_pinnable():
    source = (ROOT / "src/llm/stowlm/planner.py").read_text()
    literals = re.findall(r"remaining = ([0-9.]+) if deadline is None", source)
    assert literals and all(float(value) == reward.FINISHER_TIME_LIMIT_S for value in literals)
    from stowage.planner import finalize
    assert inspect.signature(finalize).parameters["time_limit_s"].default == reward.FINISHER_TIME_LIMIT_S
    config = asdict(reward.RewardConfig())
    assert config["ballast_time_limit_s"] == reward.FINISHER_TIME_LIMIT_S
    assert config["ballast_retry_time_limit_s"] == 2 * reward.FINISHER_TIME_LIMIT_S
    for bad in (30.0, float("inf"), 1.0):
        with pytest.raises(ValueError, match="retry"):
            reward.RewardConfig(ballast_retry_time_limit_s=bad)


@pytest.mark.parametrize("cause,expected", [
    ("timeout:prepass", "finisher_timeout"),       # early, but a time-out on its own clock: retried, then dropped
    ("timeout:acceptance", "finisher_timeout"),
    ("search_miss", "finisher_timeout"),           # inconclusive search: not a proof
    ("unknown_witness", "finisher_timeout"),
    ("rate_infeasible:R1", "finisher_timeout"),    # D74: R1 is a reported delay, never infeasibility
    ("rate_infeasible:R2", "finalization_infeasible"),  # R2 states infeasibility: proven failure
])
def test_forwarded_not_found_cause_decides_early_not_found(monkeypatch, candidate, cause, expected):
    """Once finalize forwards not_found_cause (w2-l3-finisher), an early not_found is a proven failure only if the
    cause says infeasible; wall time no longer decides."""
    instance, result = candidate
    calls = scripted_finalize(monkeypatch, result, ["not_found", "not_found"], fraction=.1, cause=cause)
    monkeypatch.setattr(reward, "check", lambda *a: pytest.fail("no ballast, no check"))
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.reason == expected and not outcome.passed
    if expected == "finisher_timeout":
        assert outcome.value is None and outcome.dropped
        assert calls == [reward.FINISHER_TIME_LIMIT_S, reward.RewardConfig().ballast_retry_time_limit_s]
    else:
        assert outcome.value == reward.RewardConfig().failure_penalty and not outcome.dropped
        assert calls == [reward.FINISHER_TIME_LIMIT_S]


def test_early_not_found_without_cause_keeps_wall_time_heuristic(monkeypatch, candidate):
    instance, result = candidate
    scripted_finalize(monkeypatch, result, ["not_found"], fraction=.1)
    monkeypatch.setattr(reward, "check", lambda *a: pytest.fail("no ballast, no check"))
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.reason == "finalization_failed" and outcome.value == reward.RewardConfig().failure_penalty


def test_ballast_v4_finalize_scopes_the_env_to_the_finisher_only(monkeypatch, candidate):
    """D88: STOWAGE_BALLAST_V4=1 only while finalize runs; never left set for decoding or the checker."""
    import os
    instance, result = candidate
    monkeypatch.delenv("STOWAGE_BALLAST_V4", raising=False)
    seen = []

    def finalize(instance, actual, *, time_limit_s):
        seen.append(os.environ.get("STOWAGE_BALLAST_V4"))
        return unfinished(actual, "infeasible_proven")
    monkeypatch.setattr(reward, "finalize", finalize)
    outcome = reward.evaluate_reward(instance, result, config=reward.RewardConfig(ballast_v4_finalize=True))
    assert seen == ["1"] and "STOWAGE_BALLAST_V4" not in os.environ
    assert outcome.reason == "finalization_infeasible"
    monkeypatch.setenv("STOWAGE_BALLAST_V4", "1")  # a globally set flag fails closed (penalty), never a pass
    bad = reward.evaluate_reward(instance, result, config=reward.RewardConfig(ballast_v4_finalize=True))
    assert bad.reason == "evaluation_error" and not bad.passed and "unset globally" in (bad.diagnostic or "")
