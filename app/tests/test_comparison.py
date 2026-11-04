"""Comparisons describe executed lifts without inventing safety or causal claims."""

from copy import deepcopy

import pytest

from app.backend import build_replay


def test_comparison_uses_counts_and_fresh_departure_verdict(replay_case):
    from app.comparison import build_comparison

    instance, plan = replay_case
    replay = build_replay(instance, plan)
    compared = build_comparison(replay, replay)
    assert compared["instance_id"] == instance.id
    assert compared["comparable"] is False  # fixture leaves cargo ashore
    assert compared["saved_objective"] == 0
    assert compared["avoided_by_left"] == []
    assert compared["avoided_by_right"] == []
    assert all(row["saved"] == 0 for row in compared["rows"])
    assert all(compared["left"]["counts"][key] == replay["frames"][-1]["counts"][key]
               for key in ("loads", "relocations", "hatch_cover_lifts", "weighted_nonproductive", "total_lifts_now"))


def test_comparison_rejects_same_id_but_different_input(replay_case):
    from app.comparison import build_comparison

    instance, plan = replay_case
    left = build_replay(instance, plan)
    right = deepcopy(left)
    right["instance"]["seed"] = (right["instance"].get("seed") or 0) + 1
    assert right["instance"]["id"] == left["instance"]["id"]
    with pytest.raises(ValueError, match="(?i)(same|identical|instance)"):
        build_comparison(left, right)


def test_destination_change_alone_is_not_avoided_lift(replay_case):
    from app.comparison import build_comparison

    instance, plan = replay_case
    left = build_replay(instance, plan)
    right = deepcopy(left)
    # Matching an actor/kind still requires one lift even with another yard bay.
    right["plan"]["moves"][0]["dst"] = "B-003-01-01"
    right["frames"][1]["move"]["dst"] = "B-003-01-01"
    compared = build_comparison(left, right)
    assert compared["avoided_by_left"] == []
    assert compared["avoided_by_right"] == []


def test_hatch_and_future_port_projection_are_distinct_counts(replay_case):
    from app.comparison import build_comparison

    instance, plan = replay_case
    left = build_replay(instance, plan)
    compared = build_comparison(left, left)
    assert compared["left"]["counts"]["hatch_cover_lifts"] == 2
    assert compared["left"]["counts"]["weighted_nonproductive"] > 3
    keys = {row["key"] for row in compared["rows"]}
    assert {"hatch_cover_lifts", "weighted_nonproductive"} <= keys


def test_extra_rehandles_have_executed_frame_evidence_without_approval_claim(replay_case):
    from app.comparison import build_comparison
    from stowage.model.moves import Relocate

    instance, plan = replay_case
    first = plan.moves[0]
    extra = [
        Relocate(call_index=0, container_id=first.container_id, src=first.dst, dst=first.src),
        Relocate(call_index=0, container_id=first.container_id, src=first.src, dst=first.dst),
    ]
    more = plan.model_copy(update={"id": "ui.extra.rehandles", "moves": [first, *extra, *plan.moves[1:]]})
    left, right = build_replay(instance, plan), build_replay(instance, more)
    assert right["metadata"]["physical_replay_legal"]
    compared = build_comparison(left, right)
    assert compared["comparable"] is False
    row = next(row for row in compared["rows"] if row["key"] == "relocations")
    assert row["saved"] == 2
    evidence = compared["avoided_by_left"]
    assert len(evidence) == 2
    for item in evidence:
        index = item["move_index"]
        assert item["frame_index"] == index
        assert right["frames"][index + 1]["move"]["kind"] == "relocate"
        assert item["container_id"] == first.container_id
        assert item["message"]


def test_physical_checker_pass_without_shippable_evidence_cannot_claim_saving(replay_case):
    from app.comparison import build_comparison
    instance, plan = replay_case
    replay = build_replay(instance, plan)
    # Report pass alone is insufficient under the owner-0.9 contract.
    replay["checker"]["report"]["passed"] = True
    replay["checker"]["report"]["shut_out"] = []
    replay["metadata"]["shippable"] = False
    compared = build_comparison(replay, replay)
    assert compared["comparable"] is False
    assert compared["left"]["shippable"] is False


def test_comparison_retains_source_result_provenance_and_stub_reason(replay_case):
    from app.comparison import build_comparison
    from stowage.model import PlannerResult, Provenance

    instance, plan = replay_case
    candidate = PlannerResult(status="ok", plan=plan, planner="source", planner_version="1",
        provenance=Provenance(stubs={"solver.foo": "review-only stub"},
                              versions={"adapter": "historical-v1"}, parents=["source-artifact"]))
    replay = build_replay(instance, plan, planner_result=candidate)
    compared = build_comparison(replay, replay)
    assert compared["comparable"] is False
    for side in (compared["left"], compared["right"]):
        assert side["stub_tainted"] is True
        assert side["provenance"]["stubs"]["solver.foo"] == "review-only stub"
        assert side["provenance"]["versions"]["adapter"] == "historical-v1"
        assert "source-artifact" in side["provenance"]["parents"]
        assert any("solver.foo" in reason for reason in side["shipping_blockers"])


def test_comparison_explanation_is_explicitly_offline(replay_case, monkeypatch):
    from app.comparison import build_comparison
    import llm.spark.explain as explain_module

    instance, plan = replay_case
    replay = build_replay(instance, plan)
    calls = []

    def offline(instance, plan, report):
        calls.append((instance.id, plan.id, report.passed))
        return "Deterministic offline explanation"

    def forbidden(*args, **kwargs):
        pytest.fail("The offline demo must not invoke a paid Spark explanation")

    monkeypatch.setattr(explain_module, "template_explanation", offline)
    monkeypatch.setattr(explain_module, "explain", forbidden)
    compared = build_comparison(replay, replay)
    assert len(calls) == 2
    assert compared["left"]["explanation"] == "Deterministic offline explanation"


def test_unbound_comparison_does_not_display_or_explain_report(replay_case):
    from app.comparison import build_comparison

    instance, plan = replay_case
    replay = build_replay(instance, plan)
    replay["checker"]["report"]["provenance"]["parents"] = []
    replay["checker"]["report"]["passed"] = True
    replay["checker"]["report"]["shut_out"] = []
    replay["metadata"]["shippable"] = True
    compared = build_comparison(replay, replay)
    assert compared["left"]["checker_passed"] is False
    assert compared["left"]["shippable"] is False
    assert compared["left"]["explanation"] is None
    assert compared["comparable"] is False


def test_comparison_illegal_first_lift_reports_executed_prefix_not_planned_tail(replay_case):
    from app.comparison import build_comparison
    from stowage.model.moves import Load

    instance, plan = replay_case
    illegal = Load(call_index=0, container_id=instance.load_list[1],
                   src="B-001-01-01", dst="0010184")
    rejected = plan.model_copy(update={"moves": [illegal, *plan.moves, *plan.moves]})
    replay = build_replay(instance, rejected)
    assert replay["metadata"]["physical_replay_legal"] is False
    assert replay["checker"]["report"]["count"]["loads"] == 0
    compared = build_comparison(replay, replay)
    for side in (compared["left"], compared["right"]):
        assert side["executed_moves"] == 0
        assert side["planned_moves"] == 11
        assert side["count_scope"] == "executed_prefix"
        assert side["counts"]["loads"] == 0
        assert side["counts"]["discharges"] == 0
        assert side["counts"]["total_lifts_now"] == 0
        assert side["shippable"] is False
    assert compared["avoided_by_left"] == compared["avoided_by_right"] == []


def test_comparison_complete_sequence_reports_all_executed_moves(replay_case):
    from app.comparison import build_comparison

    instance, plan = replay_case
    replay = build_replay(instance, plan)
    side = build_comparison(replay, replay)["left"]
    assert side["executed_moves"] == side["planned_moves"] == len(plan.moves)
    assert side["count_scope"] == "complete_plan"
    assert side["counts"]["loads"] == 2
