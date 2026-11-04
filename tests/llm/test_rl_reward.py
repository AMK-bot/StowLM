"""CPU-only reward gate and per-prompt GRPO advantage tests."""

import math

import pytest

from llm.rl import reward
from stowage.model import BallastCondition, CheckReport, FutureMoves, MoveCount, MovePlan, PlannerResult
from stowage.model.fixtures import build_instance


@pytest.fixture
def candidate():
    instance = build_instance()
    plan = MovePlan(id="reward.test", instance_id=instance.id, call_index=instance.call_index)
    result = PlannerResult(status="ok", plan=plan, planner="fake", planner_version="1")
    return instance, result


def finished(result):
    return result.model_copy(update={"plan": result.plan.model_copy(update={
        "ballast": BallastCondition(tank_masses_kg={}, source="finisher"),
    })})


def test_finalize_precedes_full_checker_and_uses_d11_count(monkeypatch, candidate):
    instance, result = candidate
    calls = []
    finalized = finished(result)

    def finalize(actual_instance, actual_result, *, time_limit_s):
        assert actual_instance is instance and actual_result is result
        assert time_limit_s == 2
        calls.append("finalize")
        return finalized

    def check(actual_instance, plan):
        assert actual_instance is instance and plan.ballast is not None
        calls.append("check")
        report = complete_report(actual_instance, plan)
        report.count = MoveCount(
            relocations=2, hatch_cover_lifts=2,
            future=[FutureMoves(call_index=1, restowed_boxes=3, restow_lifts=6, hatch_cover_lifts=2)],
            weighted_nonproductive=12.0,
        )
        return report

    monkeypatch.setattr(reward, "finalize", finalize)
    monkeypatch.setattr(reward, "check", check)
    outcome = reward.evaluate_reward(instance, result, config=reward.RewardConfig(
        move_weight=0.5, ballast_time_limit_s=2,
    ))
    assert calls == ["finalize", "check"]
    assert outcome.value == pytest.approx(-10_000 * 6 / (10_000 + 6))
    assert outcome.weighted_nonproductive == 12
    assert outcome.passed and outcome.result is finalized and outcome.report.passed
    assert result.plan.ballast is None  # shared inputs stay untouched


@pytest.mark.parametrize("moves", [0, 1_000_000])
def test_failed_checker_always_gets_fixed_penalty(monkeypatch, candidate, moves):
    instance, result = candidate
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, "check", lambda *a: CheckReport(
        passed=False, count=MoveCount(weighted_nonproductive=moves),
    ))
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.value == -10_000
    assert not outcome.passed and outcome.reason == "checker_failed"
    assert outcome.weighted_nonproductive is None


@pytest.mark.parametrize("status", ["dead_end", "timeout", "infeasible", "error"])
def test_incomplete_rollouts_do_not_run_finalizer_or_checker(monkeypatch, candidate, status):
    instance, result = candidate
    result = result.model_copy(update={"status": status})

    def forbidden(*args, **kwargs):
        pytest.fail("incomplete rollout must not be finalized or checked")

    monkeypatch.setattr(reward, "finalize", forbidden)
    monkeypatch.setattr(reward, "check", forbidden)
    outcome = reward.evaluate_reward(instance, result, config=reward.RewardConfig(dead_end_penalty=-123))
    assert outcome.value == (-123 if status == "dead_end" else -10_000)
    assert not outcome.passed


def test_explicit_mask_dead_end_and_missing_plan(candidate):
    instance, result = candidate
    assert reward.evaluate_reward(instance, result.plan, dead_end=True).reason == "dead_end"
    assert reward.evaluate_reward(instance, result.model_copy(update={"plan": None})).reason == "incomplete"


def test_finisher_failure_cannot_be_rewarded(monkeypatch, candidate):
    instance, result = candidate
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: result.model_copy(update={"status": "error"}))
    monkeypatch.setattr(reward, "check", lambda *a: pytest.fail("failed ballast must not pass"))
    assert reward.evaluate_reward(instance, result).reason == "finalization_failed"


@pytest.mark.parametrize("count,reason", [
    (None, "missing_move_count"),
    (MoveCount(weighted_nonproductive=-1), "invalid_move_count"),
    (MoveCount(weighted_nonproductive=math.inf), "invalid_move_count"),
])
def test_invalid_checker_objective_fails_closed(monkeypatch, candidate, count, reason):
    instance, result = candidate
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, "check", lambda *a: CheckReport(passed=True, count=count))
    outcome = reward.evaluate_reward(instance, result)
    assert not outcome.passed and outcome.value == -10_000 and outcome.reason == reason


def test_unexpected_evaluation_error_fails_closed(monkeypatch, candidate):
    instance, result = candidate

    def broken(*args, **kwargs):
        raise RuntimeError("checker offline")

    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, "check", broken)
    outcome = reward.evaluate_reward(instance, result)
    assert not outcome.passed and outcome.reason == "evaluation_error"
    assert outcome.diagnostic == "RuntimeError: checker offline"


def test_real_fixture_real_checker_rejects_unloaded_ship(candidate):
    instance, result = candidate
    # Ballast is explicitly set, so finalize keeps the plan. The actual full
    # checker must reject all four shut-out boxes; no hull approximation/mocking.
    outcome = reward.evaluate_reward(instance, finished(result))
    assert not outcome.passed and outcome.value == -10_000
    assert outcome.report is not None and not outcome.report.passed
    assert set(outcome.report.shut_out) == set(instance.load_list)
    assert any(v.rule_id == "objective.all_loaded" for v in outcome.report.violations)


def test_multicall_result_cannot_silently_reward_only_first_call(candidate):
    instance, result = candidate
    result = result.model_copy(update={"later_plans": [result.plan]})
    assert reward.evaluate_reward(instance, result).reason == "unsupported_multicall"


def test_group_normalization_is_per_prompt_and_handles_equal_rewards():
    assert reward.normalize_group_rewards([-2, -4, -6]) == pytest.approx([math.sqrt(1.5), 0, -math.sqrt(1.5)])
    assert reward.normalize_group_rewards([-10_000, -10_000]) == [0, 0]
    assert reward.normalize_group_rewards([3]) == [0]
    assert reward.normalize_group_rewards([]) == []


@pytest.mark.parametrize("values", [[math.nan], [math.inf], [-math.inf]])
def test_group_normalization_rejects_nonfinite_rewards(values):
    with pytest.raises(ValueError, match="finite"):
        reward.normalize_group_rewards(values)


@pytest.mark.parametrize("kwargs", [
    {"failure_penalty": 0}, {"dead_end_penalty": math.nan},
    {"move_weight": 0}, {"ballast_time_limit_s": -1},
])
def test_invalid_reward_config_rejected(kwargs):
    with pytest.raises(ValueError):
        reward.RewardConfig(**kwargs)


def complete_report(instance, plan, *, moves=1, passed=True):
    """Synthetic oracle output with actual public content-binding contract."""
    import hashlib
    import json

    from stowage.model import PartReport, Provenance

    parents = [prefix + hashlib.sha256(json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()
        for prefix, value in zip(
            ("checker.instance.sha256:", "checker.plan.sha256:"), (instance, plan))]
    return CheckReport(passed=passed, count=MoveCount(weighted_nonproductive=moves),
                       parts={name: PartReport(name=name) for name in (
                           "moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")},
                       provenance=Provenance(parents=parents))


@pytest.mark.parametrize("config", [
    reward.RewardConfig(move_weight=10001),
    reward.RewardConfig(move_weight=1e308),
    reward.RewardConfig(failure_penalty=-1e308, dead_end_penalty=-1e-300),
    reward.RewardConfig(failure_penalty=-5e-324, dead_end_penalty=-1),
    reward.RewardConfig(failure_penalty=-1, dead_end_penalty=-5e-324, move_weight=5e-324),
])
@pytest.mark.parametrize("moves", [0, 1, 1e308])
def test_any_passing_reward_strictly_outranks_both_failure_penalties(monkeypatch, candidate, config, moves):
    instance, result = candidate
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, "check", lambda instance, plan: complete_report(instance, plan, moves=moves))
    passing = reward.evaluate_reward(instance, result, config=config)
    failure = reward.evaluate_reward(instance, result.model_copy(update={"status": "error"}), config=config)
    dead_end = reward.evaluate_reward(instance, result, config=config, dead_end=True)
    assert passing.passed
    assert math.isfinite(passing.value)
    assert passing.value > failure.value
    assert passing.value > dead_end.value
    assert passing.weighted_nonproductive == moves


@pytest.mark.parametrize("defect", [
    "missing_parts", "unrun_part", "tainted", "detached", "stale_same_id", "shut_out",
])
def test_passing_flag_without_clean_bound_full_evidence_is_rejected(monkeypatch, candidate, defect):
    instance, result = candidate
    def checker(instance, plan):
        report = complete_report(instance, plan)
        if defect == "missing_parts":
            report.parts.pop("strength")
        elif defect == "unrun_part":
            report.parts["strength"].ran = False
        elif defect == "tainted":
            report.provenance.stubs["test.oracle"] = "synthetic tainted oracle"
        elif defect == "stale_same_id":
            old_plan = plan.model_copy(update={"actions": [{"synthetic": "previous content"}]})
            report = complete_report(instance, old_plan)
        elif defect == "shut_out":
            report.shut_out = [instance.load_list[0]]
        else:
            report.provenance.parents = []
        return report
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, "check", checker)
    outcome = reward.evaluate_reward(instance, result)
    assert not outcome.passed
    assert outcome.value == reward.RewardConfig().failure_penalty


def test_rewarded_result_has_canonical_shipping_evidence(monkeypatch, candidate):
    instance, result = candidate
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, "check", complete_report)
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.passed and outcome.result.shippable


def test_bounded_passing_reward_preserves_move_order_and_mixed_group_priority(monkeypatch, candidate):
    instance, result = candidate
    config = reward.RewardConfig(move_weight=10001)
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    values = []
    for moves in (0, 1, 10, 1_000_000):
        monkeypatch.setattr(reward, "check", lambda instance, plan: complete_report(instance, plan, moves=moves))
        outcome = reward.evaluate_reward(instance, result, config=config)
        assert outcome.passed
        values.append(outcome.value)
    assert all(a > b for a, b in zip(values, values[1:]))
    advantages = reward.normalize_group_rewards([values[-1], config.failure_penalty])
    assert advantages[0] > 0 > advantages[1]


def test_tainted_planner_cannot_get_passing_training_reward(monkeypatch, candidate):
    instance, result = candidate
    result.provenance.stubs["test.policy"] = "synthetic policy"
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, "check", complete_report)
    outcome = reward.evaluate_reward(instance, result)
    assert not outcome.passed and not outcome.result.shippable


def timeout_report(*, budget=30, condition=False):
    from stowage.model import PartReport, Violation

    return CheckReport(passed=False, parts={
        "condition:arrival:stability" if condition else "stability": PartReport(
            name="stability", ran=False, error="stability time budget exceeded",
            metrics={"time_budget_exceeded": 1.0, "time_budget_s": budget},
            violations=[Violation(rule_id="checker.internal_error", severity="hard", call_index=0,
                                  message="stability time budget exceeded")],
        ),
    })


def test_checker_timeout_retries_once_with_double_budget_and_restores_env(monkeypatch, candidate):
    import os

    instance, result = candidate
    monkeypatch.setenv("STOWAGE_STABILITY_TIME_BUDGET_S", "7")
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    budgets = []

    def checker(actual_instance, plan):
        budgets.append(float(os.environ["STOWAGE_STABILITY_TIME_BUDGET_S"]))
        return timeout_report(budget=7, condition=True) if len(budgets) == 1 else complete_report(actual_instance, plan)

    monkeypatch.setattr(reward, "check", checker)
    outcome = reward.evaluate_reward(instance, result)
    assert budgets == [7, 14]
    assert os.environ["STOWAGE_STABILITY_TIME_BUDGET_S"] == "7"
    assert outcome.passed
    assert outcome.checker_retries == 1
    assert not outcome.dropped


def test_repeated_checker_timeout_drops_reward_without_penalty(monkeypatch, candidate):
    import os

    instance, result = candidate
    monkeypatch.delenv("STOWAGE_STABILITY_TIME_BUDGET_S", raising=False)
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    budgets = []

    def checker(*args):
        budgets.append(float(os.environ.get("STOWAGE_STABILITY_TIME_BUDGET_S", "30")))
        return timeout_report(budget=budgets[-1])

    monkeypatch.setattr(reward, "check", checker)
    outcome = reward.evaluate_reward(instance, result)
    assert budgets == [30, 60]
    assert "STOWAGE_STABILITY_TIME_BUDGET_S" not in os.environ
    assert outcome.reason == "checker_timeout"
    assert outcome.value is None and outcome.dropped and not outcome.passed
    assert not outcome.result.shippable
    assert outcome.checker_retries == 1


def test_dropped_group_samples_do_not_change_advantages_and_report_drop_rate():
    advantages = reward.normalize_group_rewards([-2, None, -6])
    expected = reward.normalize_group_rewards([-2, -6])
    assert advantages == pytest.approx([expected[0], 0, expected[1]])
    assert advantages.stats == {"sample_count": 3, "reward_count": 2,
                                "checker_timeout_drop_count": 1,
                                "checker_timeout_drop_rate": 1 / 3}
    empty = reward.normalize_group_rewards([None, None])
    assert empty == [0, 0]
    assert empty.stats["checker_timeout_drop_count"] == 2
    assert empty.stats["checker_timeout_drop_rate"] == 1
    assert reward.normalize_group_rewards([]).stats["checker_timeout_drop_rate"] == 0


def test_completed_recheck_can_identify_a_true_checker_failure(monkeypatch, candidate):
    instance, result = candidate
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    reports = iter([timeout_report(), CheckReport(passed=False)])
    monkeypatch.setattr(reward, "check", lambda *a: next(reports))
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.reason == "checker_failed" and not outcome.dropped
    assert outcome.value == reward.RewardConfig().failure_penalty
    assert outcome.checker_retries == 1


def test_checker_retry_error_restores_original_budget(monkeypatch, candidate):
    import os

    instance, result = candidate
    monkeypatch.setenv("STOWAGE_STABILITY_TIME_BUDGET_S", "12")
    monkeypatch.setattr(reward, "finalize", lambda *a, **kw: finished(result))
    reports = iter([timeout_report(budget=12)])

    def checker(*args):
        try:
            return next(reports)
        except StopIteration:
            raise RuntimeError("retry infrastructure failure") from None

    monkeypatch.setattr(reward, "check", checker)
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.reason == "evaluation_error"
    assert os.environ["STOWAGE_STABILITY_TIME_BUDGET_S"] == "12"


@pytest.mark.parametrize('verdict', ['timeout', 'failed', 'exception', 'incomplete', 'dead_end'])
def test_rechecking_revokes_old_shipping_authorization(monkeypatch, candidate, verdict):
    instance, result = candidate
    monkeypatch.setattr(reward, 'finalize', lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, 'check', complete_report)
    first = reward.evaluate_reward(instance, result)
    assert first.result.shippable
    monkeypatch.setattr(reward, 'finalize', lambda i, r, **kw: r)
    def checker(*args):
        if verdict == 'exception':
            raise RuntimeError('checker failed unexpectedly')
        return timeout_report() if verdict == 'timeout' else CheckReport(passed=False)
    monkeypatch.setattr(reward, 'check', checker)
    if verdict == 'incomplete':
        first.result.status = 'timeout'
    second = reward.evaluate_reward(instance, first.result, dead_end=verdict == 'dead_end')
    assert not second.passed and not second.result.shippable
    if verdict not in {'incomplete', 'dead_end'}:
        assert second.report is not None
        assert second.result._shipping_evidence[3] is second.report


@pytest.mark.parametrize('retry_verdict', ['timeout', 'pass', 'exception'])
def test_timeout_cannot_erase_an_evaluated_failure(monkeypatch, candidate, retry_verdict):
    from stowage.model import PartReport, Violation
    instance, result = candidate
    initial = timeout_report()
    initial.parts['moves'] = PartReport(name='moves', violations=[Violation(
        rule_id='objective.all_loaded', severity='hard', call_index=0,
        message='load list not complete')])
    initial.violations = list(initial.parts['moves'].violations)
    initial.shut_out = [instance.load_list[0]]
    calls = 0
    def checker(i, p):
        nonlocal calls
        calls += 1
        if calls == 1:
            return initial
        if retry_verdict == 'exception':
            raise RuntimeError('retry broke')
        return timeout_report() if retry_verdict == 'timeout' else complete_report(i, p)
    monkeypatch.setattr(reward, 'finalize', lambda *a, **kw: finished(result))
    monkeypatch.setattr(reward, 'check', checker)
    outcome = reward.evaluate_reward(instance, result)
    assert outcome.reason == 'checker_failed'
    assert outcome.value == reward.RewardConfig().failure_penalty
    assert not outcome.dropped and not outcome.result.shippable
    assert outcome.checker_retries == 1
    assert any(v.rule_id == 'objective.all_loaded' for v in outcome.report.violations)


def test_drop_admission_uses_worst_group_and_three_consecutive_bad_steps():
    good = {'checker_timeout_drop_rate': .05}
    bad = {'checker_timeout_drop_rate': .051}
    state = reward.advance_timeout_policy([good, bad])
    assert state['any_group_over_threshold'] and state['consecutive_bad_steps'] == 1
    state = reward.advance_timeout_policy([good, bad], previous=state)
    assert not state['abort'] and state['consecutive_bad_steps'] == 2
    reset = reward.advance_timeout_policy([good], previous=state)
    assert reset['consecutive_bad_steps'] == 0 and not reset['abort']
    state = reward.advance_timeout_policy([bad], previous=state)
    assert state['abort'] and state['consecutive_bad_steps'] == 3
    persisted = []
    with pytest.raises(RuntimeError, match='timeout.*drop'):
        reward.require_timeout_admission(state, lambda: persisted.append(dict(state)))
    assert persisted == [state]


def test_dropped_sample_logs_observed_stability_and_tank_state(candidate):
    from types import SimpleNamespace
    instance, result = candidate
    report = timeout_report()
    report.parts['stability'].metrics.update(trim_m=.4, list_rad=.1, gm_m=.7)
    diagnostic = reward.dropped_sample_diagnostics(instance, SimpleNamespace(
        report=report, result=finished(result)))
    assert diagnostic['trim_m'] == .4
    assert diagnostic['list_rad'] == .1
    assert diagnostic['gm_m'] == .7
    assert diagnostic['tank_masses_kg'] == instance.onboard.tank_masses_kg
