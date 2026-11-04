"""The browser consumes engine states; crane legality and counting stay in Python."""

import pytest

from app.backend import build_replay, make_demo_plan
from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Discharge, HatchCoverOff, HatchCoverOn, Load, RestowOff, RestowOn
from stowage.moves import apply_move, apply_plan, initial_state
from app.tests.report_helpers import bind_report


def test_every_frame_matches_authoritative_engine_and_counts(replay_case):
    instance, plan = replay_case
    before = instance.model_dump_json(), plan.model_dump_json()
    replay = build_replay(instance, plan)
    assert len(replay["frames"]) == len(plan.moves) + 1
    state = initial_state(instance)
    for index, frame in enumerate(replay["frames"]):
        if index:
            state = apply_move(instance, state, plan.moves[index - 1])
        assert frame["state"] == state.model_dump(mode="json")
        assert frame["move"] == (plan.moves[index - 1].model_dump(mode="json") if index else None)
        prefix = plan.model_copy(update={"moves": plan.moves[:index]})
        expected = apply_plan(instance, prefix).count
        assert frame["counts"] == {
            **expected.model_dump(mode="json"), "total_lifts_now": expected.total_lifts_now,
        }
    final = replay["frames"][-1]["counts"]
    assert (final["loads"], final["relocations"], final["hatch_cover_lifts"]) == (2, 1, 2)
    assert final["weighted_nonproductive"] > 3  # Includes future-port projection.
    assert replay["metadata"]["physical_replay_legal"] is True
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["checker_status"] == "NOT VALIDATED"
    assert (instance.model_dump_json(), plan.model_dump_json()) == before


def test_replay_exposes_illegal_lift_and_incomplete_departure(replay_case):
    instance, plan = replay_case
    illegal = Load(call_index=0, container_id=instance.load_list[1],
                   src="B-001-01-01", dst="0010184")
    replay = build_replay(instance, plan.model_copy(update={"moves": [illegal]}))
    assert replay["metadata"]["physical_replay_legal"] is False
    assert len(replay["frames"]) == 1  # Rejected lift never appears as an applied state.
    assert "expert.moves.bottom_up" in {v["rule_id"] for v in replay["checker"]["violations"]}
    replay = build_replay(instance, plan.model_copy(update={"moves": plan.moves[:2]}))
    assert replay["metadata"]["departure_approved"] is False
    assert "expert.moves.hatch_clear" in {v["rule_id"] for v in replay["checker"]["violations"]}


@pytest.mark.parametrize("updates", [{"instance_id": "another-instance"}, {"call_index": 1}])
def test_replay_rejects_plan_identity_mismatch(replay_case, updates):
    instance, plan = replay_case
    with pytest.raises(ValueError, match="match"):
        build_replay(instance, plan.model_copy(update=updates))


def test_placeholder_is_deterministic_legal_and_explicitly_unapproved():
    instance = build_instance()
    before = instance.model_dump_json()
    first = make_demo_plan(instance)
    assert first.model_dump_json() == make_demo_plan(instance).model_dump_json()
    assert instance.model_dump_json() == before
    assert apply_plan(instance, first).legal
    assert any(move.kind == "load" for move in first.moves)
    replay = build_replay(instance, first)
    assert replay["metadata"]["planner"] == "ui-placeholder"
    assert "Placeholder" in replay["metadata"]["label"]
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["shut_out"] == sorted(
        set(instance.load_list) - {p["container_id"] for p in replay["frames"][-1]["state"]["ship"]["placements"]}
    )


def test_placeholder_dig_out_highlights_buried_target_and_blockers():
    instance = build_instance()
    _, lower, blocker, _ = instance.load_list
    stacks = [stack for stack in instance.yard.stacks if blocker not in stack.boxes]
    next(stack for stack in stacks if lower in stack.boxes).boxes.append(blocker)
    instance.yard.stacks = stacks
    instance = Instance.model_validate(instance.model_dump())
    plan = make_demo_plan(instance)
    assert any(move.kind == "relocate" and move.container_id == blocker for move in plan.moves)
    replay = build_replay(instance, plan)
    assert any(frame["highlight"]["target_id"] == lower
               and blocker in frame["highlight"]["blockers"] for frame in replay["frames"])
    assert all(frame["highlight"]["blockers"] == []
               for frame in replay["frames"] if frame["highlight"]["target_id"] is None)


def test_placeholder_requires_supplied_plan_for_discharge_calls():
    with pytest.raises(ValueError, match="discharge"):
        make_demo_plan(build_instance(layover=True))


def test_supplied_layover_plan_counts_each_restow_lift():
    instance = build_instance(layover=True)
    *_, discharge, through = instance.containers
    plan = MovePlan(id="ui.layover", instance_id=instance.id, call_index=0, moves=[
        HatchCoverOff(call_index=0, cover_id="HC-02-1"),
        RestowOff(call_index=0, container_id=through.id, src="0010104"),
        Discharge(call_index=0, container_id=discharge.id, src="0010102", dst="B-002-01-01"),
        RestowOn(call_index=0, container_id=through.id, dst="0010102"),
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
    ])
    replay = build_replay(instance, plan)
    assert [frame["counts"]["restow_lifts"] for frame in replay["frames"]] == [0, 0, 1, 1, 2, 2]
    assert replay["frames"][2]["state"]["yard"]["quay_buffer"] == [through.id]
    final = replay["frames"][-1]
    assert final["state"]["yard"]["quay_buffer"] == []
    assert final["counts"]["discharges"] == 1
    assert final["counts"]["total_lifts_now"] == 5
    assert replay["metadata"]["planner"] == "supplied-plan"


def _passing_ui_report(monkeypatch, instance, plan, *, report_taint=False):
    """Synthetic checker evidence isolates the UI gate, not physical certification."""
    from stowage.model import CheckReport, PartReport, Provenance
    report = CheckReport(passed=True, count=apply_plan(instance, plan).count,
        parts={name: PartReport(name=name) for name in
               ("moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")},
        provenance=Provenance(stubs={"checker.test": "stub"} if report_taint else {}))
    bind_report(report, instance, plan)
    monkeypatch.setattr("app.backend.build_checker_payload", lambda *args, **kwargs: {
        "passed": True, "report": report.model_dump(mode="json"), "violations": []})
    return report


@pytest.mark.parametrize("taint", ["plan", "instance", "report"])
def test_passed_but_tainted_departure_is_never_approved(replay_case, monkeypatch, taint):
    from stowage.model import Provenance
    instance, plan = replay_case
    if taint == "plan":
        plan = plan.model_copy(update={"provenance": Provenance(stubs={"planner.test": "stub"})})
    elif taint == "instance":
        instance = instance.model_copy(update={"provenance": Provenance(stubs={"input.test": "stub"})})
    _passing_ui_report(monkeypatch, instance, plan, report_taint=taint == "report")
    replay = build_replay(instance, plan)
    assert replay["checker"]["report"]["passed"] is True
    assert replay["checker"]["passed"] is False
    assert replay["metadata"]["checker_status"] == "NOT VALIDATED"
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["shippable"] is False
    assert replay["metadata"]["stub_tainted"] is True
    assert replay["metadata"]["provenance"]["stubs"]
    assert "taint" in replay["metadata"]["notice"].lower()


def test_clean_full_evidence_approval_uses_planner_result_gate(replay_case, monkeypatch):
    from stowage.model import PlannerResult
    instance, plan = replay_case
    _passing_ui_report(monkeypatch, instance, plan)
    calls = []
    monkeypatch.setattr(PlannerResult, "shippable", property(lambda self: calls.append(self) or False))
    replay = build_replay(instance, plan)
    assert calls
    assert replay["metadata"]["departure_approved"] is False


def test_clean_complete_fresh_evidence_can_approve_departure(replay_case, monkeypatch):
    instance, plan = replay_case
    _passing_ui_report(monkeypatch, instance, plan)
    replay = build_replay(instance, plan)
    assert replay["metadata"]["departure_approved"] is True
    assert replay["metadata"]["shippable"] is True
    assert replay["metadata"]["stub_tainted"] is False
    assert replay["metadata"]["shipping_blockers"] == []


def test_source_result_must_contain_exact_replayed_plan(replay_case):
    from stowage.model import PlannerResult
    instance, plan = replay_case
    other = plan.model_copy(update={"id": "different-plan"})
    candidate = PlannerResult(status="ok", plan=other, planner="source", planner_version="1")
    with pytest.raises(ValueError, match="must match"):
        build_replay(instance, plan, planner_result=candidate)


@pytest.mark.parametrize("mode", ["timeout", "later_plans", "required_calls", "result_taint"])
def test_source_result_shipping_conditions_survive_recheck(replay_case, monkeypatch, mode):
    from stowage.model import PlannerResult, Provenance
    instance, plan = replay_case
    candidate = PlannerResult(status="ok", plan=plan, planner="source", planner_version="1")
    if mode == "timeout":
        candidate.status = "timeout"
    elif mode == "later_plans":
        candidate.later_plans = [plan.model_copy(update={"id": "later", "call_index": 1, "moves": []})]
    elif mode == "required_calls":
        instance = instance.model_copy(update={"plan_calls": [1]})
    else:
        candidate.provenance = Provenance(stubs={"solver.foo": "stub"})
    _passing_ui_report(monkeypatch, instance, plan)
    replay = build_replay(instance, plan, planner_result=candidate)
    assert replay["checker"]["report"]["passed"] is True
    assert replay["checker"]["passed"] is (mode != "result_taint")
    assert replay["metadata"]["validation_status"] == ("NOT VALIDATED" if mode == "result_taint" else "PASS")
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["shipping_blockers"]
    assert replay["planner_result"]["status"] == candidate.status


@pytest.mark.parametrize("binding", ["missing", "other_plan", "suppressed"])
def test_unbound_report_cannot_authorize_or_display_shipping(replay_case, monkeypatch, binding):
    from stowage.checker.full import _input_bindings

    instance, plan = replay_case
    report = _passing_ui_report(monkeypatch, instance, plan)
    report.provenance.parents = []
    if binding == "other_plan":
        other = plan.model_copy(update={"id": "another-displayed-plan"})
        report.provenance.parents = list(_input_bindings(instance, other))
    monkeypatch.setattr("app.backend.build_checker_payload", lambda *args, **kwargs: {
        "passed": True, "bound": True,
        "report": None if binding == "suppressed" else report.model_dump(mode="json"),
        "violations": [], "stability": {"metrics": {"gm_m": 99}},
    })
    replay = build_replay(instance, plan)
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["checker_status"] == "unbound report"
    assert replay["checker"]["report"] is None
    assert replay["checker"]["stability"]["metrics"] == {}
    assert "unbound" in replay["metadata"]["notice"].lower()


@pytest.mark.parametrize("legacy", [False, True])
def test_middle_feeder_display_resolves_label_without_rewriting_instance(replay_case, legacy):
    from stowage.conventions import VESSEL_ID_ALIASES

    instance, plan = replay_case
    vessel_id = "feeder_800"
    if legacy:
        vessel_id = next(old for old, current in VESSEL_ID_ALIASES.items()
                         if current == vessel_id)
    instance.vessel.id = vessel_id
    instance.vessel.slice_of = None
    instance.vessel.name = "Historical synthetic feeder label"
    before = instance.model_dump_json()
    replay = build_replay(instance, plan)
    assert replay["metadata"]["vessel_label"] == "Synthetic feeder ~800 TEU"
    assert instance.model_dump_json() == before
    assert replay["instance"]["vessel"]["id"] == vessel_id
    assert replay["instance"]["vessel"]["name"] == "Historical synthetic feeder label"
