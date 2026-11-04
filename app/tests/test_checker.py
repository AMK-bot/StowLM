"""Departure panels present checker evidence without making a second verdict."""

import pytest

from app.backend import build_replay
from app.checker import build_checker_payload
from stowage.checker.full import check
from stowage.model import CheckReport, PartReport, Violation
from stowage.versions import CHECKER_VERSION
from app.tests.report_helpers import bind_report


def test_checker_payload_matches_authoritative_report(replay_case):
    instance, plan = replay_case
    expected = check(instance, plan)
    replay = build_replay(instance, plan)
    panels = replay["checker"]
    actual = panels["report"]
    for key in ("passed", "parts", "violations", "count", "shut_out"):
        assert actual[key] == expected.model_dump(mode="json")[key]
    assert panels["violations"] == actual["violations"]
    assert panels["version"] == CHECKER_VERSION
    assert panels["scope"] == "departure; no declared arrival evidence"
    assert panels["bound"] is True
    assert panels["binding_status"] == "bound"
    assert panels["evaluated_frame"] == len(plan.moves)
    assert replay["metadata"]["departure_approved"] is expected.passed
    assert any(not part.ran or part.error for part in expected.parts.values())
    assert replay["metadata"]["checker_status"] == "NOT VALIDATED"
    assert panels["validation_status"] == "NOT VALIDATED"
    assert all(v["rule_id"] and v["message"] for v in panels["violations"])
    for name in ("stability", "strength"):
        part = actual["parts"][name]
        assert panels[name]["ran"] is part["ran"]
        assert panels[name]["metrics"] == part["metrics"]
        assert panels[name]["margins"] == part["margins"]


@pytest.mark.parametrize("missing", [False, True])
def test_unavailable_part_never_appears_as_approval(replay_case, monkeypatch, missing):
    instance, plan = replay_case
    violation = Violation(rule_id="checker.missing_vessel_data", severity="hard", call_index=0,
                          message="Synthetic test: no loading manual data.")
    parts = {} if missing else {
        "stability": PartReport(name="stability", ran=False, error="No hull geometry",
                                violations=[violation]),
        "strength": PartReport(name="strength", ran=False, error="No station envelope",
                               violations=[violation]),
    }
    report = CheckReport(passed=False, violations=[violation], parts=parts)
    bind_report(report, instance, plan)
    calls = []

    def full_check(actual_instance, actual_plan):
        calls.append((actual_instance, actual_plan))
        return report

    monkeypatch.setattr("app.checker.full.check", full_check)
    replay = build_replay(instance, plan)
    assert len(calls) == 1
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["checker_status"] == "NOT VALIDATED"
    assert replay["checker"]["stability"]["ran"] is False
    assert replay["checker"]["strength"]["ran"] is False
    assert replay["checker"]["stability"]["gz_curve"] == []
    assert replay["checker"]["strength"]["stations"] == []
    assert replay["checker"]["violations"][0]["message"] == violation.message


def test_b23_criterion_flags_and_limits_come_from_checker(replay_case, monkeypatch):
    instance, plan = replay_case
    metrics = {"criteria_part_b_2_3_active": 1., "criteria_part_a_2_2_1_to_3_active": 0.,
               "gm_m": 0.8, "area_0_30_mrad": 0.11}
    part = PartReport(name="stability", metrics=metrics,
                      margins={"containership_area_0_30_mrad": 0.02})
    monkeypatch.setattr("app.checker.full.check", lambda *_: bind_report(
        CheckReport(passed=True, parts={"stability": part}), instance, plan))
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert "B 2.3" in payload["stability"]["criteria_set"]
    assert "A 2.2.4" in payload["stability"]["criteria_set"]
    criterion = next(c for c in payload["stability"]["criteria"]
                     if c["key"] == "containership_area_0_30_mrad")
    assert criterion["measured"] == 0.11
    assert criterion["margin"] == 0.02
    assert criterion["limit"] == pytest.approx(0.09)
    assert criterion["rule_id"].startswith("iscode.b2_3.")


def test_strength_stations_preserve_signed_permissible_envelope(replay_case, monkeypatch):
    instance, plan = replay_case
    stations = instance.vessel.hull.stations
    metrics = {f"{kind}@{station.x_m:.12g}": value
               for station in stations for kind, value in (("sf_n", -1_000.), ("bm_nm", 2_000.))}
    part = PartReport(name="strength", metrics=metrics)
    monkeypatch.setattr("app.checker.full.check", lambda *_: bind_report(
        CheckReport(passed=True, parts={"strength": part}), instance, plan))
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert payload["strength"]["stations"] == [
        {"x_m": s.x_m, "sf_n": -1_000., "bm_nm": 2_000.,
         "min_shear_n": s.min_shear_n, "max_shear_n": s.max_shear_n,
         "min_bending_nm": s.min_bending_nm, "max_bending_nm": s.max_bending_nm}
        for s in stations
    ]


def test_dg_panel_exposes_real_doc_areas_and_cargo(replay_case):
    instance, plan = replay_case
    panels = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert panels["dg"]["areas"] == [a.model_dump(mode="json") for a in instance.vessel.dg_doc_areas]
    dg = next(box for box in instance.containers if box.dg is not None)
    cargo = next(box for box in panels["dg"]["cargo"] if box["container_id"] == dg.id)
    assert cargo["class_number"] == dg.dg.imdg_class
    assert cargo["un_number"] == dg.dg.un_number
    assert all(v in panels["violations"] for v in panels["dg"]["violations"])


def test_dg_pair_rows_keep_authoritative_segregation_violations(replay_case, monkeypatch):
    instance, plan = replay_case
    _, dg_id, special_id, _ = instance.load_list
    boxes = {box.id: box for box in instance.containers}
    boxes[special_id].dg = boxes[dg_id].dg.model_copy(deep=True)
    violation = Violation(rule_id="imdg.segregation.7_2_4", severity="hard", call_index=0,
                          containers=[dg_id, special_id],
                          message="Synthetic checker diagnostic: incompatible pair.")
    monkeypatch.setattr("app.checker.full.check", lambda *_: bind_report(CheckReport(
        passed=False, violations=[violation], parts={"rules": PartReport(name="rules", violations=[violation])}), instance, plan))
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    pair = next(p for p in payload["dg"]["pairs"] if set(p["containers"]) == {dg_id, special_id})
    assert pair["violations"] == [violation.model_dump(mode="json")]
    assert len(pair["slots"]) == 2
    assert pair["status"] == "failed"


@pytest.mark.parametrize("un_number, packing_group", [("1263", None), ("9999", "III")])
def test_dg_pair_with_unresolved_individual_dgl_is_unavailable(
        replay_case, monkeypatch, un_number, packing_group):
    from stowage.checker.rules_imdg import imdg_violations
    from stowage.moves import apply_plan

    instance, plan = replay_case
    _, dg_id, special_id, _ = instance.load_list
    boxes = {box.id: box for box in instance.containers}
    boxes[special_id].dg = boxes[dg_id].dg.model_copy(deep=True)
    boxes[dg_id].dg.un_number = un_number
    boxes[dg_id].dg.packing_group = packing_group
    diagnostics = imdg_violations(instance, apply_plan(instance, plan).final_state.ship, call_index=0)
    unresolved = next(v for v in diagnostics if v.containers == [dg_id]
                      and "no unambiguous sampled DGL entry" in v.message)
    assert not any({dg_id, special_id} <= set(v.containers) for v in diagnostics)
    rules = PartReport(name="rules", violations=diagnostics)
    monkeypatch.setattr("app.checker.full.check", lambda *_: bind_report(CheckReport(
        passed=False, violations=diagnostics, parts={"rules": rules}), instance, plan))

    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    pair = next(p for p in payload["dg"]["pairs"] if set(p["containers"]) == {dg_id, special_id})
    assert pair["status"] == "unavailable"
    assert unresolved.model_dump(mode="json") in pair["violations"]


@pytest.mark.parametrize("rule_id", ["checker.missing_vessel_data", "imdg.segregation.7_2_4"])
def test_dg_pair_retains_individual_missing_data_diagnostic(replay_case, monkeypatch, rule_id):
    instance, plan = replay_case
    _, dg_id, special_id, _ = instance.load_list
    boxes = {box.id: box for box in instance.containers}
    boxes[special_id].dg = boxes[dg_id].dg.model_copy(deep=True)
    violation = Violation(rule_id=rule_id, severity="hard", call_index=0, containers=[dg_id],
                          message="Synthetic checker diagnostic: required shipment verification missing.")
    monkeypatch.setattr("app.checker.full.check", lambda *_: bind_report(CheckReport(
        passed=False, violations=[violation], parts={"rules": PartReport(name="rules", violations=[violation])}), instance, plan))

    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    pair = next(p for p in payload["dg"]["pairs"] if set(p["containers"]) == {dg_id, special_id})
    assert pair["status"] == "unavailable"
    assert pair["violations"] == [violation.model_dump(mode="json")]


def test_dg_pair_without_missing_data_or_pair_violations_is_passed(replay_case, monkeypatch):
    instance, plan = replay_case
    _, dg_id, special_id, _ = instance.load_list
    boxes = {box.id: box for box in instance.containers}
    boxes[special_id].dg = boxes[dg_id].dg.model_copy(deep=True)
    monkeypatch.setattr("app.checker.full.check", lambda *_: bind_report(CheckReport(
        passed=True, parts={"rules": PartReport(name="rules")}), instance, plan))
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    pair = next(p for p in payload["dg"]["pairs"] if set(p["containers"]) == {dg_id, special_id})
    assert pair["status"] == "passed"
    assert pair["violations"] == []
