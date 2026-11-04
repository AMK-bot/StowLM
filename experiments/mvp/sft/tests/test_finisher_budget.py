"""Finisher-aware best-of-k (StowLMPlanner(candidate_finalize_s=...)): a candidate whose ballast finisher runs out of
its short budget fails and selection moves on; the last resort retries the fewest-move capped candidate with the
remaining time. Reuses the planner selection fixture (fake decode/finalize/check, CPU)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "_planner_selection_fixture", Path(__file__).resolve().parents[4] / "tests/llm/test_stowlm_planner_selection.py")
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
setup_selection = _MOD.setup_selection  # pytest fixture


def _finalize_with_slow(state, slow, needs):
    import stowage.planner as protocol  # noqa: F401
    from stowage.model import BallastCondition

    calls = []

    def finalize(instance, result, time_limit_s=30.0, **kw):
        index = int(result.plan.id.rsplit("-", 1)[1])
        calls.append((index, time_limit_s))
        if index in slow:
            spent = min(time_limit_s, needs)
            state.clock += spent
            if spent < needs:
                return result.model_copy(update={"status": "timeout", "message": "ballast deadline"})
        state.finalized.append(index)
        plan = result.plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="finisher")})
        return result.model_copy(update={"plan": plan})
    return finalize, calls


def test_short_budget_moves_on_to_the_next_candidate(setup_selection, monkeypatch):
    import stowage.planner as protocol

    p, instance, state = setup_selection
    p.candidate_finalize_s = 90.0
    finalize, calls = _finalize_with_slow(state, slow={0}, needs=1000.0)
    monkeypatch.setattr(protocol, "finalize", finalize)
    result = p.plan(instance, time_limit_s=600)
    assert calls[0] == (0, 90.0)  # sample 0 (fewer moves) got only the short budget
    assert result.metrics["chosen_sample"] == 1 and result.metrics["finisher_budget_timeouts"] == [0]
    assert "last_resort" not in result.metrics  # a sibling passed: no last resort


def test_last_resort_gets_the_remaining_time(setup_selection, monkeypatch):
    import stowage.planner as protocol

    p, instance, state = setup_selection
    p.candidate_finalize_s = 90.0
    finalize, calls = _finalize_with_slow(state, slow={0, 1}, needs=200.0)
    monkeypatch.setattr(protocol, "finalize", finalize)
    result = p.plan(instance, time_limit_s=600)
    assert [c[0] for c in calls] == [0, 1, 0]
    assert calls[2][1] > 90.0  # last resort: the remaining budget, for the fewest-move capped candidate
    assert result.metrics["last_resort"]["sample"] == 0 and result.metrics["last_resort"]["passed"]
    assert result.metrics["chosen_sample"] == 0


def test_lr_schedule_is_sized_in_optimizer_updates():
    """trainer 2.1: with accumulation 4 the cosine reaches its floor at the last optimizer update (iters / 4)."""
    from llm.stowlm.train import TrainConfig, learning_rate_schedule

    cfg = TrainConfig(data_dir="x", adapter_dir="y", learning_rate=5e-5, lr_schedule="cosine", warmup_frac=0.05,
                      min_lr_frac=0.1, iters=1764, grad_accumulation_steps=4)
    lr = learning_rate_schedule(cfg)
    updates = 1764 // 4
    assert abs(float(lr(round(0.05 * updates))) - 5e-5) < 1e-9  # peak right after warmup
    assert abs(float(lr(updates)) - 5e-6) < 1e-8  # floor at the last update


def test_equal_budget_has_no_last_resort(setup_selection, monkeypatch):
    import stowage.planner as protocol

    p, instance, state = setup_selection
    p.candidate_finalize_s, p.last_resort = 90.0, False
    finalize, calls = _finalize_with_slow(state, slow={0, 1}, needs=200.0)
    monkeypatch.setattr(protocol, "finalize", finalize)
    result = p.plan(instance, time_limit_s=600)
    assert [c[1] for c in calls] == [90.0, 90.0] and "last_resort" not in result.metrics
    assert result.metrics["finisher_budget_timeouts"] == [0, 1]
