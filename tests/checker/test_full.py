"""Full-gate wiring tests; stubbed parts deliberately carry provenance taint.

These isolate orchestration from numerical validation in test_full_cases/properties.
"""
from contextlib import ExitStack, contextmanager

import pytest

from stowage.checker.full import check, departure_condition
from stowage.frozen import stub_override
from stowage.model import (BallastCondition, Load, MovePlan, PartReport,
                          PlanConstraint, Provenance, Violation,
                          VoyageConditionIdentity, VoyageSpec)
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet_voyage import build_voyage_policy


PARTS = {"rules": "static_rules", "stability": "stability", "strength": "strength",
         "visibility": "visibility", "lashing": "lashing"}


@contextmanager
def isolated_parts(*, replacement=None, seen=None):
    with ExitStack() as stack:
        for name, interface in PARTS.items():
            def implementation(instance, ship, *, call_index, name=name):
                if seen is not None:
                    seen.append((name, ship.model_copy(deep=True), call_index))
                if replacement and name in replacement:
                    return replacement[name](instance, ship, call_index=call_index)
                return PartReport(name=name)
            stack.enter_context(stub_override(f"checker.check_{interface}", implementation,
                                              reason="full-gate wiring test"))
        yield


def empty_problem():
    instance = build_instance()
    instance.vessel.voyage_policy = build_voyage_policy(
        instance.vessel.tanks, parameter_source="synthetic model fixture",
        vessel_id=instance.vessel.id)
    evidence = Provenance(created_by="test_full.synthetic_voyage",
                          parents=["DECLARATION: synthetic next-call environment and seawater density"])
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(
            id=f"full-test.{phase}", phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode,
            environment_id=f"synthetic-full.{phase}", density_id="synthetic-seawater-1025",
            water_density_kg_m3=instance.port.water_density_kg_m3,
            provenance=evidence)
        for index, phase in enumerate(("departure", "arrival"))}, provenance=evidence)
    instance.load_list = []
    return instance, MovePlan(id="full-test", instance_id=instance.id, call_index=0)


def test_public_exports_and_clean_gate():
    import stowage.checker as public
    instance, plan = empty_problem()
    with isolated_parts():
        report = public.check(instance, plan)
    assert report.passed
    assert set(report.parts) >= {
        "moves", "rules", "stability", "strength", "visibility", "lashing", "constraints",
        "voyage"}
    assert {f"condition:{condition_id}:{name}"
            for condition_id in ("full-test.departure", "full-test.departure:mid_voyage", "full-test.arrival")
            for name in ("stability", "strength", "visibility", "lashing")} <= set(report.parts)
    assert report.provenance.tainted
    assert {instance.id, plan.id} <= set(report.provenance.parents)
    assert public.report_matches_inputs(report, instance, plan)
    assert report.count.total_lifts_now == 0
    assert public.departure_condition is departure_condition


def test_shutout_is_a_hard_gate_and_parts_still_run():
    instance, _ = empty_problem()
    instance.load_list = [box.id for box in instance.containers]
    plan = MovePlan(id="missing", instance_id=instance.id, call_index=0)
    seen = []
    with isolated_parts(seen=seen):
        report = check(instance, plan)
    assert not report.passed
    assert report.shut_out == instance.load_list
    assert any(v.rule_id == "objective.all_loaded" and v.severity == "hard" for v in report.violations)
    assert len(seen) == 13
    assert len([item for item in seen if item[0] == "rules"]) == 1


def test_illegal_move_keeps_reached_state_and_runs_all_parts():
    instance, plan = empty_problem()
    box = instance.containers[0]
    instance.load_list = [box.id]
    move = Load(call_index=0, container_id=box.id, src="A-001-01-01", dst="0020182")
    plan.moves = [move, move]  # second lift tries to load an already loaded box
    seen = []
    with isolated_parts(seen=seen):
        report = check(instance, plan)
    assert not report.passed
    assert not report.shut_out
    assert report.count.loads == 1
    assert any(v.move_index == 1 and v.severity == "hard" for v in report.violations)
    assert len(seen) == 13
    assert all(s.placements[0].container_id == box.id for _, s, _ in seen)
    with pytest.raises(ValueError):
        departure_condition(instance, plan)


def test_part_exception_does_not_suppress_other_parts():
    instance, plan = empty_problem()
    seen = []
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    with isolated_parts(replacement={"strength": broken}, seen=seen):
        report = check(instance, plan)
    assert not report.passed
    assert not report.parts["strength"].ran
    assert "synthetic failure" in report.parts["strength"].error
    assert any(v.rule_id == "checker.internal_error" for v in report.violations)
    assert len(seen) == 13


@pytest.mark.parametrize("severity,passed", [("hard", False), ("soft", True), ("tolerated", True)])
def test_gate_respects_applied_severity(severity, passed):
    instance, plan = empty_problem()
    def violated(instance, ship, *, call_index):
        return PartReport(name="rules", violations=[Violation(
            rule_id="expert.physical.stack_weight", severity=severity,
            call_index=call_index, message="Synthetic gate test")])
    with isolated_parts(replacement={"rules": violated}):
        report = check(instance, plan)
    assert report.passed is passed


def test_part_that_did_not_run_fails_even_without_its_own_violation():
    instance, plan = empty_problem()
    with isolated_parts(replacement={"visibility": lambda *a, **k: PartReport(name="visibility", ran=False)}):
        report = check(instance, plan)
    assert not report.passed
    assert any(v.rule_id == "checker.internal_error" for v in report.violations)


def test_ballast_is_pure_replaces_adjustable_and_preserves_fixed():
    instance, plan = empty_problem()
    before = instance.model_dump_json()
    plan.ballast = BallastCondition(tank_masses_kg={"WB-P": 1000}, source="planner")
    ship = departure_condition(instance, plan)
    assert ship.tank_masses_kg["WB-P"] == 1000
    assert ship.tank_masses_kg.get("WB-S", 0) == 0
    assert ship.tank_masses_kg["FO-1"] == 47500
    assert instance.model_dump_json() == before
    plan.ballast = None
    assert departure_condition(instance, plan).tank_masses_kg == instance.onboard.tank_masses_kg


@pytest.mark.parametrize("masses", [{"unknown": 1}, {"FO-1": 1}, {"WB-P": 1e9}])
def test_invalid_ballast_fails_closed_but_runs_physics(masses):
    instance, plan = empty_problem()
    plan.ballast = BallastCondition(tank_masses_kg=masses, source="planner")
    seen = []
    with isolated_parts(seen=seen):
        report = check(instance, plan)
    assert not report.passed
    assert len(seen) == 13
    with pytest.raises(ValueError):
        departure_condition(instance, plan)


def test_plan_instance_mismatch_is_a_report():
    instance, plan = empty_problem()
    plan.instance_id = "other"
    with isolated_parts():
        report = check(instance, plan)
    assert not report.passed
    assert report.count is None
    with pytest.raises(ValueError):
        departure_condition(instance, plan)


def test_unknown_instruction_cannot_silently_pass():
    instance, plan = empty_problem()
    instance.plan_constraints = [PlanConstraint(kind="unimplemented_test_instruction")]
    with isolated_parts():
        report = check(instance, plan)
    assert not report.passed
    assert not report.parts["constraints"].ran


def test_intermediate_stability_after_successful_load_only():
    instance, plan = empty_problem()
    box = instance.containers[0]
    instance.load_list = [box.id]
    move = Load(call_index=0, container_id=box.id, src="A-001-01-01", dst="0020182")
    plan.moves = [move, move]
    seen = []
    with isolated_parts(seen=seen):
        report = check(instance, plan, intermediate_stability=True)
    assert not report.passed
    assert len([item for item in seen if item[0] == "stability"]) == 4


def test_d11_count_includes_weighted_later_port_restows():
    instance, plan = empty_problem()
    early, late = instance.containers[2], instance.containers[1]
    instance.load_list = [early.id, late.id]
    instance.objective.future_restow = 3
    plan.moves = [
        Load(call_index=0, container_id=early.id, src="A-003-01-01", dst="0010182"),
        Load(call_index=0, container_id=late.id, src="B-001-01-01", dst="0010184"),
    ]
    with isolated_parts():
        report = check(instance, plan)
    assert report.passed
    assert report.count.loads == 2
    assert report.count.future[0].restowed_boxes == 1
    assert report.count.future[0].restow_lifts == 2
    assert report.count.weighted_nonproductive == 6


def test_ballast_cache_cannot_reuse_stale_same_id_capacity():
    instance, plan = empty_problem()
    plan.ballast = BallastCondition(tank_masses_kg={"WB-P": 100000}, source="planner")
    assert departure_condition(instance, plan).tank_masses_kg["WB-P"] == 100000
    next(t for t in instance.vessel.tanks if t.id == "WB-P").capacity_m3 = 1
    with pytest.raises(ValueError):
        departure_condition(instance, plan)
