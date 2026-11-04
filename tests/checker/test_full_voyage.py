"""D57 voyage-state orchestration; liquid-sensitive probes isolate full-gate wiring.

These synthetic part functions test orchestration, never numerical compliance.
Actual physics and the convergence replay retain their own validation coverage.
"""
from __future__ import annotations

import pytest

import stowage.checker.full as full
from stowage.model import (
    BallastCondition, CheckReport, Instance, Load, MovePlan,
    PartReport, PlannerResult, Provenance, Violation, VoyageBallastOperation,
    VoyageConditionIdentity, VoyageConsumption, VoyageSpec, VoyageTankPolicy,
)
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet_voyage import build_voyage_policy


DEPARTURE = "voyage-test.departure"
MID_VOYAGE = f"{DEPARTURE}:mid_voyage"
ARRIVAL = "voyage-test.arrival"
PHYSICS = ("stability", "strength", "visibility", "lashing")
CONDITIONS = (("departure", DEPARTURE, 1.0),
              ("mid_voyage", MID_VOYAGE, .55), ("arrival", ARRIVAL, .1))


def voyage_problem(*, arrival_density=1025.0):
    instance = build_instance()
    instance.load_list = []
    instance.vessel.voyage_policy = build_voyage_policy(
        instance.vessel.tanks, parameter_source="synthetic model fixture",
        vessel_id=instance.vessel.id)
    instance.voyage = VoyageSpec(
        departure=VoyageConditionIdentity(id=DEPARTURE, phase="departure", call_index=0,
            locode=instance.port.locode, environment_id="synthetic.departure.environment",
            density_id="synthetic.departure.density", water_density_kg_m3=1025.0),
        arrival=VoyageConditionIdentity(id=ARRIVAL, phase="arrival", call_index=1,
            locode=instance.rotation.calls[1].locode, environment_id="synthetic.arrival.environment",
            density_id="synthetic.arrival.density", water_density_kg_m3=arrival_density))
    instance = Instance.model_validate(instance.model_dump())
    return instance, MovePlan(id="voyage-test.plan", instance_id=instance.id, call_index=0)


def install_parts(monkeypatch, *, fail_part=None, seen=None, broken_arrival=None):
    for name, attribute in (("rules", "check_static_rules"), ("stability", "check_stability"),
                            ("strength", "check_strength"), ("visibility", "check_visibility"),
                            ("lashing", "check_lashing")):
        def implementation(instance, ship, *, call_index, name=name):
            if seen is not None:
                seen.append((name, instance.model_copy(deep=True), ship.model_copy(deep=True), call_index))
            fuel = ship.tank_masses_kg.get("FO-1", 0.0)
            if broken_arrival == name and fuel < 10000:
                raise RuntimeError("arrival physics unavailable")
            violations = []
            if fail_part == name and fuel < 10000:
                violations = [Violation(rule_id="checker.missing_vessel_data", severity="hard",
                    call_index=call_index, message="Synthetic low-consumable endpoint fails")]
            return PartReport(name=name, violations=violations,
                metrics={"fuel_mass_kg": fuel, "note: synthetic evidence retained": 1.0},
                margins={"synthetic_margin": -1.0 if violations else 1.0})
        monkeypatch.setattr(full, attribute, implementation)


def condition_part(report, phase, name):
    identity = {"departure": DEPARTURE, "mid_voyage": MID_VOYAGE, "arrival": ARRIVAL}[phase]
    return report.parts[f"condition:{identity}:{name}"]


def test_d57_three_states_pass_and_keep_seven_aggregate_parts(monkeypatch):
    instance, plan = voyage_problem()
    seen = []
    replays = []
    replay = full.apply_plan
    def tracked_replay(*args, **kwargs):
        replays.append(True)
        return replay(*args, **kwargs)
    monkeypatch.setattr(full, "apply_plan", tracked_replay)
    install_parts(monkeypatch, seen=seen)
    before = instance.model_dump_json()
    report = full.check(instance, plan)
    assert report.passed
    assert {"moves", "rules", "stability", "strength", "visibility", "lashing", "constraints"} <= set(report.parts)
    for name in PHYSICS:
        for phase, _, fraction in CONDITIONS:
            part = condition_part(report, phase, name)
            assert part.ran
            assert part.metrics["fuel_mass_kg"] == pytest.approx(47500.0 * fraction)
            assert part.metrics["condition.remaining_fraction"] == pytest.approx(fraction)
        assert report.parts[name].metrics["fuel_mass_kg"] == 47500.0
        assert len([entry for entry in seen if entry[0] == name]) == 3
    assert len([entry for entry in seen if entry[0] == "rules"]) == 1
    assert len(replays) == 1
    assert instance.model_dump_json() == before
    assert full.report_matches_inputs(report, instance, plan)


@pytest.mark.parametrize("failed_part", PHYSICS)
def test_d50_departure_passes_but_low_consumable_arrival_fails(monkeypatch, failed_part):
    instance, plan = voyage_problem()
    install_parts(monkeypatch, fail_part=failed_part)
    report = full.check(instance, plan)
    assert not report.passed
    assert not condition_part(report, "departure", failed_part).violations
    failures = condition_part(report, "arrival", failed_part).violations
    assert failures and all(v.severity == "hard" for v in failures)
    assert any(f"[{ARRIVAL} arrival]" in v.message for v in failures)
    assert all(v.call_index == 1 for v in failures)
    assert report.parts[failed_part].violations
    assert report.parts[failed_part].margins["synthetic_margin"] == -1.0
    assert any(f"[{ARRIVAL} arrival]" in v.message for v in report.violations)


def test_d50_declared_consumption_replaces_ten_percent_default(monkeypatch):
    instance, plan = voyage_problem()
    instance.voyage.consumption = [VoyageConsumption(group_id="fuel_oil", mass_kg=7500.0)]
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    assert report.passed
    for name in PHYSICS:
        assert condition_part(report, "mid_voyage", name).metrics["fuel_mass_kg"] == pytest.approx(43750.0)
        assert condition_part(report, "arrival", name).metrics["fuel_mass_kg"] == pytest.approx(40000.0)


@pytest.mark.parametrize("missing", ["voyage", "policy", "group_membership"])
def test_d50_missing_declarations_fail_closed(monkeypatch, missing):
    instance, plan = voyage_problem()
    if missing == "voyage":
        instance.voyage = None
    elif missing == "policy":
        instance.vessel.voyage_policy = None
    else:
        # A model_copy/mutable object bypass must not hide the actual fuel tank.
        instance.vessel.voyage_policy = VoyageTankPolicy(groups=[], drawdown_order=[])
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    assert not report.passed
    assert any(v.severity == "hard" and ("voyage" in v.message.lower() or "consumable" in v.message.lower())
               for v in report.violations)


@pytest.mark.parametrize("mass_change", [0.0, 100.0, -100.0])
def test_d50_declared_voyage_ballast_operation_is_rejected(monkeypatch, mass_change):
    instance, plan = voyage_problem()
    instance.voyage.ballast_operations = [VoyageBallastOperation(tank_id="WB-P", mass_change_kg=mass_change)]
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    assert not report.passed
    assert any(v.severity == "hard" and "ballast" in v.message.lower() for v in report.violations)


def test_d50_arrival_derivation_error_is_a_hard_diagnostic(monkeypatch):
    instance, plan = voyage_problem()
    instance.voyage.consumption = [VoyageConsumption(group_id="fuel_oil", mass_kg=47501.0)]
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    assert not report.passed
    assert any(v.severity == "hard" and "exceeds departure contents" in v.message for v in report.violations)
    for phase in ("mid_voyage", "arrival"):
        assert all(not condition_part(report, phase, name).ran for name in PHYSICS)


def test_d50_arrival_part_error_cannot_pass_or_suppress_other_endpoint_part(monkeypatch):
    instance, plan = voyage_problem()
    install_parts(monkeypatch, broken_arrival="stability")
    report = full.check(instance, plan)
    assert not report.passed
    assert condition_part(report, "departure", "stability").ran
    assert not condition_part(report, "arrival", "stability").ran
    assert all(condition_part(report, "mid_voyage", name).ran for name in PHYSICS)
    assert all(condition_part(report, "arrival", name).ran for name in PHYSICS if name != "stability")
    assert any("arrival physics unavailable" in v.message for v in report.violations)


def test_d50_json_retains_condition_identity_notes_lineage_and_input_binding(monkeypatch):
    instance, plan = voyage_problem()
    instance.voyage.arrival.provenance = Provenance(stubs={"voyage.arrival.evidence": "test-only"},
        parents=["synthetic.arrival.source"])
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    restored = CheckReport.model_validate_json(report.model_dump_json())
    for phase, condition_id, fraction in CONDITIONS:
        environment_phase = "departure" if phase == "mid_voyage" else phase
        for name in PHYSICS:
            part = condition_part(restored, phase, name)
            assert part.metrics[f"condition.id: {condition_id}"] == 1.0
            assert part.metrics[f"condition.phase: {phase}"] == 1.0
            assert part.metrics[f"condition.environment_id: synthetic.{environment_phase}.environment"] == 1.0
            assert part.metrics[f"condition.density_id: synthetic.{environment_phase}.density"] == 1.0
            assert part.metrics["note: synthetic evidence retained"] == 1.0
            assert part.metrics["condition.remaining_fraction"] == pytest.approx(fraction)
            assert any(key.startswith("note: APPROXIMATION: D57") for key in part.metrics)
    assert restored.provenance.stubs["voyage.arrival.evidence"] == "test-only"
    assert "synthetic.arrival.source" in restored.provenance.parents
    assert full.report_matches_inputs(restored, instance, plan)
    instance.voyage.arrival.environment_id = "same-id-mutated-arrival-environment"
    assert not full.report_matches_inputs(restored, instance, plan)


@pytest.mark.parametrize("changed", ["departure_call", "arrival_locode", "departure_density"])
def test_d50_revalidates_mutated_condition_context(monkeypatch, changed):
    instance, plan = voyage_problem()
    if changed == "departure_call":
        instance.voyage.departure.call_index = 1
        instance.voyage.arrival.call_index = 2
    elif changed == "arrival_locode":
        instance.voyage.arrival.locode = instance.port.locode
    else:
        instance.voyage.departure.water_density_kg_m3 = 1000.0
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    assert not report.passed
    assert not report.parts["voyage"].ran
    assert any(v.rule_id == "checker.missing_vessel_data" for v in report.violations)


def test_d50_derives_from_replayed_ballast_finished_departure_not_initial_onboard(monkeypatch):
    instance, plan = voyage_problem(arrival_density=1000.0)
    box = instance.containers[0]
    instance.load_list = [box.id]
    plan.moves = [Load(call_index=0, container_id=box.id, src="A-001-01-01", dst="0020182")]
    plan.ballast = BallastCondition(tank_masses_kg={"WB-P": 1000.0}, source="planner")
    seen = []
    install_parts(monkeypatch, seen=seen)
    report = full.check(instance, plan)
    assert report.passed
    for name in PHYSICS:
        calls = [entry for entry in seen if entry[0] == name]
        assert len(calls) == 3
        _, departure_instance, departure_ship, departure_call = calls[0]
        _, midpoint_instance, midpoint_ship, midpoint_call = calls[1]
        _, arrival_instance, arrival_ship, arrival_call = calls[2]
        assert departure_call == 0
        assert midpoint_call == 0
        assert arrival_call == 1
        assert departure_instance.port.water_density_kg_m3 == 1025.0
        assert midpoint_instance.port.water_density_kg_m3 == 1025.0
        assert midpoint_instance.port.locode == departure_instance.port.locode
        assert midpoint_instance.port.max_draught_m is None
        assert not midpoint_instance.port.port_rules
        assert arrival_instance.port.water_density_kg_m3 == 1000.0
        assert arrival_instance.port.locode == instance.rotation.calls[1].locode
        assert arrival_instance.port.max_draught_m is None
        assert not arrival_instance.port.port_rules
        assert midpoint_ship.placements == departure_ship.placements
        assert arrival_ship.placements == departure_ship.placements
        assert [p.container_id for p in arrival_ship.placements] == [box.id]
        assert arrival_ship.tank_masses_kg["WB-P"] == departure_ship.tank_masses_kg["WB-P"] == 1000.0
        assert arrival_ship.tank_masses_kg["WB-S"] == departure_ship.tank_masses_kg["WB-S"] == 0.0
        assert midpoint_ship.tank_masses_kg["WB-P"] == 1000.0
        assert midpoint_ship.tank_masses_kg["WB-S"] == 0.0
        assert midpoint_ship.tank_masses_kg["FO-1"] == pytest.approx(26125.0)
        assert arrival_ship.tank_masses_kg["FO-1"] == 4750.0
    assert instance.onboard.placements == []
    assert instance.onboard.tank_masses_kg["WB-P"] == 51250.0


def test_d50_shipping_gate_stays_private_and_rejects_arrival_failures(monkeypatch):
    instance, plan = voyage_problem()
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    result = PlannerResult(status="ok", plan=plan, planner="synthetic-test", planner_version="1")
    assert not result.shippable
    result._record_full_check(instance, report)
    assert result.shippable
    instance.voyage.arrival.density_id = "mutated"
    assert not result.shippable
    install_parts(monkeypatch, fail_part="stability")
    failed = full.check(instance, plan)
    result._record_full_check(instance, failed)
    assert not result.shippable


def test_d50_real_low_consumable_kg_rise_passes_departure_and_fails_arrival_gm():
    """Real production parts at three states; tighten GM between computed endpoints.

    The synthetic ship's unchanged hydrostatics, tank geometry and criteria
    decide the results. A stricter operating GM is an allowed instance policy;
    no regulatory limit is loosened and no geometry/part function is stubbed.
    """
    import runpy
    from pathlib import Path
    from stowage.checker.stability import check_stability
    from stowage.model import derive_arrival_liquid_state

    helper = runpy.run_path(str(Path(__file__).with_name("test_full_properties.py")))["_random_plan"]
    instance, plan = helper(0)
    for tank in instance.vessel.tanks:
        if tank.kind == "fuel_oil":
            instance.onboard.tank_masses_kg[tank.id] = .05 * tank.capacity_m3 * tank.content_density_kg_m3
    departure = full.departure_condition(instance, plan)
    liquids = derive_arrival_liquid_state(departure, instance.voyage, vessel=instance.vessel)
    arrival = departure.model_copy(deep=True, update={"tank_masses_kg": liquids.tank_masses_kg})
    dep = check_stability(instance, departure, call_index=0)
    arrival_context = instance.model_copy(deep=False, update={
        "call_index": 1, "port": instance.port.model_copy(update={
            "locode": instance.voyage.arrival.locode,
            "water_density_kg_m3": instance.voyage.arrival.water_density_kg_m3})})
    arr = check_stability(arrival_context, arrival, call_index=1)
    assert dep.ran and arr.ran and not dep.violations and not arr.violations
    assert arr.metrics["kg_m"] > dep.metrics["kg_m"]
    assert arr.metrics["gm_m"] < dep.metrics["gm_m"]
    both_pass = full.check(instance, plan)
    assert both_pass.passed, both_pass.violations
    instance.rule_overrides["iscode.loading.gm"] = {
        "minimum_gm": .5 * (dep.metrics["gm_m"] + arr.metrics["gm_m"])}
    report = full.check(instance, plan)
    assert not report.passed
    dep_id, arr_id = instance.voyage.departure.id, instance.voyage.arrival.id
    dep_part = report.parts[f"condition:{dep_id}:stability"]
    arr_part = report.parts[f"condition:{arr_id}:stability"]
    assert not dep_part.violations
    assert any(v.rule_id == "iscode.loading.gm" for v in arr_part.violations)
    assert all(f"[{arr_id} arrival]" in v.message for v in arr_part.violations)
    for condition_id in (dep_id, f"{dep_id}:mid_voyage", arr_id):
        for name in PHYSICS:
            assert report.parts[f"condition:{condition_id}:{name}"].ran


def test_d50_explicit_empty_policy_accepts_vessel_without_consumable_tanks(monkeypatch):
    instance, plan = voyage_problem()
    instance.vessel.tanks = [tank for tank in instance.vessel.tanks if tank.kind == "ballast"]
    instance.onboard.tank_masses_kg.pop("FO-1")
    instance.vessel.voyage_policy = VoyageTankPolicy(groups=[], drawdown_order=[])
    install_parts(monkeypatch)
    report = full.check(instance, plan)
    assert report.passed
    for phase, _, _ in CONDITIONS:
        assert all(condition_part(report, phase, name).ran for name in PHYSICS)
