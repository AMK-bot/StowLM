"""Planner boundary: finishing preserves plans, failures and provenance correctly."""

import pytest

from stowage.model import BallastCondition, BallastOutcome, MovePlan, PlannerResult, Provenance
from stowage.model.fixtures import build_instance
from stowage.planner import finalize
from stowage.solver import ballast


def inputs():
    instance = build_instance()
    plan = MovePlan(id="finalize-test", instance_id=instance.id, call_index=instance.call_index)
    result = PlannerResult(status="ok", plan=plan, planner="test", planner_version="1", seed=41,
                           metrics={"original": 3})
    return instance, result


@pytest.mark.parametrize("status", ["dead_end", "infeasible", "timeout", "error"])
def test_unsuccessful_result_unchanged(monkeypatch, status):
    instance, result = inputs()
    result = result.model_copy(update={"status": status})
    monkeypatch.setattr(ballast, "finish_ballast", lambda *a, **kw: pytest.fail("must not finish"))
    assert finalize(instance, result) is result


@pytest.mark.parametrize("with_plan", [False, True])
def test_absent_plan_or_existing_ballast_unchanged(monkeypatch, with_plan):
    instance, result = inputs()
    if with_plan:
        plan = result.plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")})
        result = result.model_copy(update={"plan": plan})
    else:
        result = result.model_copy(update={"plan": None})
    monkeypatch.setattr(ballast, "finish_ballast", lambda *a, **kw: pytest.fail("must not finish"))
    assert finalize(instance, result) is result


def test_success_forwards_budget_seed_and_preserves_input(monkeypatch):
    instance, result = inputs()
    snapshot = result.model_dump()
    finished = result.plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="finisher")})

    def finish(inst, plan, *, time_limit_s, seed):
        assert inst is instance and plan is result.plan
        assert time_limit_s == 1.25 and seed == 41
        return BallastOutcome(status="ok", plan=finished)

    monkeypatch.setattr(ballast, "finish_ballast", finish)
    actual = finalize(instance, result, time_limit_s=1.25)
    assert actual.status == "ok"
    assert actual.plan.ballast == finished.ballast
    assert actual.metrics == {"original": 3, "ballast_status": "ok"}
    assert result.model_dump() == snapshot


@pytest.mark.parametrize("outcome_status,status,proof", [
    ("infeasible_proven", "infeasible", "Even maximum achievable GM is below the required minimum."),
    ("not_found", "error", None),
])
def test_failure_retains_plan_and_distinguishes_proof(monkeypatch, outcome_status, status, proof):
    instance, result = inputs()
    monkeypatch.setattr(ballast, "finish_ballast", lambda *a, **kw: BallastOutcome(status=outcome_status, proof=proof))
    actual = finalize(instance, result)
    assert actual.status == status
    assert actual.plan is result.plan
    assert actual.metrics == {"original": 3, "ballast_status": outcome_status}
    assert actual.message == proof if proof else "not been proved" in actual.message


def test_merge_all_provenance_including_finished_plan(monkeypatch):
    instance, result = inputs()
    instance = instance.model_copy(update={"provenance": Provenance(stubs={"instance": "test"})})
    plan = result.plan.model_copy(update={"provenance": Provenance(stubs={"input_plan": "test"})})
    result = result.model_copy(update={"plan": plan, "provenance": Provenance(stubs={"result": "test"})})
    finished = plan.model_copy(update={
        "ballast": BallastCondition(tank_masses_kg={}, source="finisher"),
        "provenance": Provenance(stubs={"finished_plan": "test"}),
    })
    monkeypatch.setattr(ballast, "finish_ballast", lambda *a, **kw: BallastOutcome(
        status="ok", plan=finished, provenance=Provenance(stubs={"outcome": "test"})))
    actual = finalize(instance, result)
    expected = {name: "test" for name in ("instance", "input_plan", "result", "finished_plan", "outcome")}
    assert actual.provenance.stubs == expected
    assert actual.plan.provenance.stubs == expected


def test_seed_defaults_to_zero(monkeypatch):
    instance, result = inputs()
    result = result.model_copy(update={"seed": None})

    def finish(*args, **kwargs):
        assert kwargs["seed"] == 0
        return BallastOutcome(status="not_found")

    monkeypatch.setattr(ballast, "finish_ballast", finish)
    assert finalize(instance, result).status == "error"


@pytest.mark.parametrize('cause', ['rate_infeasible:R2', 'unknown_witness',
                                 'timeout:prepass', 'timeout:search', 'timeout:acceptance'])
def test_not_found_cause_survives_finalization_and_serialization(monkeypatch, cause):
    instance, result = inputs()
    # Resequencer evidence is already attached upstream and must survive.
    result.metrics['resequence'] = {'applied': True, 'lifts_moved': 2}
    outcome = ballast._active_metadata(BallastOutcome(status='not_found'), cause=cause)
    monkeypatch.setattr(ballast, 'finish_ballast', lambda *a, **kw: outcome)
    actual = finalize(instance, result)
    assert actual.status == 'error'
    assert actual.metrics['not_found_cause'] == cause
    assert actual.metrics['resequence'] == result.metrics['resequence']
    assert cause in actual.message
    assert PlannerResult.model_validate_json(actual.model_dump_json()).metrics['not_found_cause'] == cause
    assert 'not_found_cause' not in result.metrics
