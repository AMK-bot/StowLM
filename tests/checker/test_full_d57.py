"""D57 three-sample gate regressions, plus an actual D56 lashing counterexample."""
from __future__ import annotations

import pytest

import stowage.checker.full as full
from stowage.model import (CheckReport, Instance, MovePlan, PartReport, Violation,
    VoyageConditionIdentity, VoyageConsumption, VoyageSpec)
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet_voyage import build_voyage_policy


DEPARTURE = "d57.departure"
MIDPOINT = DEPARTURE + ":mid_voyage"
ARRIVAL = "d57.arrival"
PARTS = {"stability": "check_stability",
         "strength": "check_strength", "visibility": "check_visibility",
         "lashing": "check_lashing", "constraints": "check_plan_constraints"}
STATES = ((DEPARTURE, "departure", 1.0), (MIDPOINT, "mid_voyage", .55),
          (ARRIVAL, "arrival", .1))


def problem():
    instance = build_instance()
    instance.load_list = []
    instance.vessel.voyage_policy = build_voyage_policy(instance.vessel.tanks,
        parameter_source="synthetic D57 fixture", vessel_id=instance.vessel.id)
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(id=condition_id, phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode,
            environment_id=f"d57.{phase}.environment", density_id=f"d57.{phase}.density",
            water_density_kg_m3=density)
        for condition_id, phase, index, density in ((DEPARTURE, "departure", 0, 1025.),
                                                   (ARRIVAL, "arrival", 1, 1000.))})
    instance = Instance.model_validate(instance.model_dump())
    return instance, MovePlan(id="d57.plan", instance_id=instance.id, call_index=0)


def probes(monkeypatch, *, seen=None, fail_part=None, fail_fraction=None):
    for name, attribute in {"rules": "check_static_rules", **PARTS}.items():
        def probe(instance, ship, *, call_index, name=name):
            r = ship.tank_masses_kg["FO-1"] / 47500.
            if seen is not None:
                seen.append((name, instance.model_copy(deep=True), ship.model_copy(deep=True), call_index, r))
            failed = name == fail_part and r == pytest.approx(fail_fraction)
            return PartReport(name=name, metrics={"remaining_fraction_probe": r,
                "note: synthetic per-state evidence": 1.},
                margins={"synthetic_margin": -1. if failed else 1.},
                violations=[Violation(rule_id="checker.missing_vessel_data", severity="hard",
                    call_index=call_index, message="Synthetic sampled-state hard failure")]
                    if failed else [])
        monkeypatch.setattr(full, attribute, probe)


def test_d57_every_part_runs_at_three_distinct_reported_states(monkeypatch):
    instance, plan = problem()
    seen = []
    probes(monkeypatch, seen=seen)
    before = instance.model_dump_json(), plan.model_dump_json()
    report = full.check(instance, plan)
    assert report.passed, report.violations
    restored = CheckReport.model_validate_json(report.model_dump_json())
    assert len([entry for entry in seen if entry[0] == "rules"]) == 1
    for part in PARTS:
        assert [entry[4] for entry in seen if entry[0] == part] == pytest.approx([1., .55, .1])
        for condition_id, phase, r in STATES:
            evidence = restored.parts[f"condition:{condition_id}:{part}"]
            assert evidence.ran
            assert evidence.metrics[f"condition.id: {condition_id}"] == 1.
            assert evidence.metrics[f"condition.phase: {phase}"] == 1.
            assert evidence.metrics["condition.remaining_fraction"] == pytest.approx(r)
            assert evidence.metrics["remaining_fraction_probe"] == pytest.approx(r)
            assert evidence.metrics["note: synthetic per-state evidence"] == 1.
    assert (instance.model_dump_json(), plan.model_dump_json()) == before
    assert full.report_matches_inputs(restored, instance, plan)
    notes = " ".join(key for part in restored.parts.values() for key in part.metrics if "note:" in key)
    assert "APPROXIMATION" in notes and "continuous" in notes.lower()


def test_d57_lashing_design_notes_survive_full_report_json_at_every_state(monkeypatch):
    from stowage.checker.lashing import LashingReport

    instance, plan = problem()
    probes(monkeypatch)
    def lashing_with_notes(instance, ship, *, call_index):
        return LashingReport(name="lashing", notes=["D56: synthetic design evidence"])
    monkeypatch.setattr(full, "check_lashing", lashing_with_notes)
    report = CheckReport.model_validate_json(full.check(instance, plan).model_dump_json())
    for condition_id, _, _ in STATES:
        assert report.parts[f"condition:{condition_id}:lashing"].metrics[
            "note: D56: synthetic design evidence"] == 1.


def test_d57_derived_midpoint_identity_cannot_collide_with_arrival(monkeypatch):
    instance, plan = problem()
    instance.voyage.arrival.id = MIDPOINT
    probes(monkeypatch)
    report = full.check(instance, plan)
    assert not report.passed
    assert not report.parts["voyage"].ran
    assert any("collides" in violation.message for violation in report.violations)


@pytest.mark.parametrize("failed_part", list(PARTS))
def test_d57_midpoint_only_hard_failure_cannot_be_hidden_by_passing_endpoints(monkeypatch, failed_part):
    instance, plan = problem()
    probes(monkeypatch, fail_part=failed_part, fail_fraction=.55)
    report = full.check(instance, plan)
    assert not report.passed
    for identity in (DEPARTURE, ARRIVAL):
        assert not report.parts[f"condition:{identity}:{failed_part}"].violations
    failed = report.parts[f"condition:{MIDPOINT}:{failed_part}"]
    assert failed.violations
    assert all(f"[{MIDPOINT} mid_voyage]" in v.message for v in failed.violations)
    assert report.parts[failed_part].violations
    assert any(f"[{MIDPOINT} mid_voyage]" in v.message for v in report.violations)


@pytest.mark.parametrize("failed_part", ["visibility", "lashing"])
def test_d57_arrival_visibility_and_lashing_failures_are_hard_full_gate_failures(monkeypatch, failed_part):
    instance, plan = problem()
    probes(monkeypatch, fail_part=failed_part, fail_fraction=.1)
    report = full.check(instance, plan)
    assert not report.passed
    assert not report.parts[f"condition:{DEPARTURE}:{failed_part}"].violations
    assert report.parts[f"condition:{ARRIVAL}:{failed_part}"].violations
    assert any(f"[{ARRIVAL} arrival]" in v.message for v in report.violations)


def test_d57_declared_consumption_sets_the_midpoint_between_one_and_actual_arrival(monkeypatch):
    instance, plan = problem()
    instance.voyage.consumption = [VoyageConsumption(group_id="fuel_oil", mass_kg=19000.)]
    seen = []
    probes(monkeypatch, seen=seen)
    report = full.check(instance, plan)
    assert report.passed
    for name in PARTS:
        assert [entry[4] for entry in seen if entry[0] == name] == pytest.approx([1., .8, .6])
    assert report.parts[f"condition:{MIDPOINT}:stability"].metrics["condition.remaining_fraction"] == .8


def test_d57_midpoint_uses_declared_departure_environment_and_seagoing_limits(monkeypatch):
    instance, plan = problem()
    instance.port.max_draught_m = 1.
    seen = []
    probes(monkeypatch, seen=seen)
    report = full.check(instance, plan)
    assert report.passed
    for name in PARTS:
        departure, midpoint, arrival = [entry for entry in seen if entry[0] == name]
        assert departure[1].port.max_draught_m == 1.
        for entry, call_index, locode, density in (
                (midpoint, 0, instance.port.locode, 1025.),
                (arrival, 1, instance.rotation.calls[1].locode, 1000.)):
            assert entry[3] == call_index
            assert entry[1].port.locode == locode
            assert entry[1].port.water_density_kg_m3 == density
            assert entry[1].port.max_draught_m is None
            assert entry[1].port.port_rules == []
        for tank_id in ("WB-P", "WB-S"):
            assert departure[2].tank_masses_kg[tank_id] == midpoint[2].tank_masses_kg[tank_id] == arrival[2].tank_masses_kg[tank_id]
    midpoint_metrics = report.parts[f"condition:{MIDPOINT}:stability"].metrics
    assert midpoint_metrics["condition.call_index"] == 0.
    assert midpoint_metrics["condition.water_density_kg_m3"] == 1025.
    assert midpoint_metrics["condition.environment_id: d57.departure.environment"] == 1.
    notes = " ".join(key for part in report.parts.values() for key in part.metrics if "note:" in key)
    assert "constant" in notes.lower() and "context" in notes.lower()


def _manual_1700_c2():
    """Current production C2 recipe, without experiment imports or saved JSON."""
    from stowage.conventions import make_container_id
    from stowage.vessel.fleet import load_ship_params, load_vessel
    from stowage.vessel.fleet_manual import standard_manual_conditions

    instance, plan = problem()
    vessel = load_vessel("feeder_1700")
    recipe = next(row for row in standard_manual_conditions(vessel, load_ship_params("feeder_1700"))
                  if row["condition"] == "C2")
    payload = instance.model_dump(mode="python")
    payload["vessel"] = vessel
    payload["containers"] = []
    placements = []
    for serial, (slot, mass) in enumerate(recipe["cargo_cells"], 1):
        container_id = make_container_id("QZXU", serial)
        payload["containers"].append(dict(id=container_id, iso_type="22G1", length_ft=20,
            height_m=2.591, tare_kg=2200., gross_mass_kg=mass, max_gross_mass_kg=30480.,
            pol="THLCH", pod="THBKK"))
        placements.append(dict(slot=slot, container_id=container_id))
    payload["onboard"] = dict(vessel_id=vessel.id, placements=placements,
        tank_masses_kg=recipe["departure_tank_masses_kg"])
    payload["yard"]["stacks"] = []
    # Equal explicitly declared seawater density isolates the consumption effect.
    payload["voyage"]["arrival"]["water_density_kg_m3"] = 1025.
    return Instance.model_validate(payload), plan


@pytest.mark.slow
def test_d57_real_1700_lashing_departure_below_design_cap_but_arrival_above():
    """Real GM/lashing functions; cap and securing geometry are never relaxed.

    APPROXIMATION: a synthetic C2 derivative retains only each stack's bottom
    tier, then adjusts actual lightship KG to place departure GM five millimetres
    below the production D56 cap. This avoids unrelated securing overloads in
    the current full C2 recipe. Retained box and liquid masses, securing geometry
    and every rule limit remain the production declarations. Consumption then
    raises arrival GM over the unchanged cap. This is a gate regression, not an
    operating qualification.
    """
    from stowage.checker.lashing import check_lashing
    from stowage.checker.visibility import _loading_condition
    from stowage.model import derive_arrival_liquid_state

    instance, plan = _manual_1700_c2()
    # APPROXIMATION: synthetic one-tier C2 derivative isolates the GM boundary;
    # the production recipe's upper tiers can exceed securing loads below it.
    bottom_slots = {stack.cell_ids[0] for stack in instance.vessel.stacks if stack.cell_ids}
    instance.onboard.placements = [p for p in instance.onboard.placements if p.slot in bottom_slots]
    retained = {p.container_id for p in instance.onboard.placements}
    instance.containers = [box for box in instance.containers if box.id in retained]
    instance = Instance.model_validate(instance.model_dump())
    hull = instance.vessel.hull
    cap = hull.max_gm_lashing_m
    cap_evidence = hull.max_gm_lashing_m_provenance.model_dump()
    departure = full.departure_condition(instance, plan)
    target = cap - .005
    for _ in range(3):
        current = _loading_condition(instance, departure)
        hull.lightship_vcg_m += (current.gm_m-target)*current.mass_kg/hull.lightship_mass_kg
    instance.vessel.sources.append(
        "APPROXIMATION: synthetic D57 one-tier C2 derivative lightship KG adjusted to place departure GM 0.005m below unchanged D56 cap")
    liquids = derive_arrival_liquid_state(departure, instance.voyage, vessel=instance.vessel)
    arrival = departure.model_copy(deep=True, update={"tank_masses_kg": liquids.tank_masses_kg})
    arrival_context = instance.model_copy(update={"call_index": 1, "port": instance.port.model_copy(
        update={"locode": instance.voyage.arrival.locode, "max_draught_m": None, "port_rules": []})})
    dep_part = check_lashing(instance, departure, call_index=0)
    arr_part = check_lashing(arrival_context, arrival, call_index=1)
    assert dep_part.ran and not dep_part.violations
    assert dep_part.metrics["gm_m"] == pytest.approx(target, abs=1e-7)
    assert arr_part.metrics["gm_m"] > cap
    assert not arr_part.ran
    assert any("GM above lashing design GM" in violation.message for violation in arr_part.violations)
    report = full.check(instance, plan)
    assert not report.passed
    assert any("GM above lashing design GM" in violation.message for violation in report.parts["lashing"].violations)
    assert not report.parts[f"condition:{DEPARTURE}:lashing"].violations
    assert any("GM above lashing design GM" in violation.message
               for violation in report.parts[f"condition:{ARRIVAL}:lashing"].violations)
    assert hull.max_gm_lashing_m == cap
    assert hull.max_gm_lashing_m_provenance.model_dump() == cap_evidence


def test_d57_zero_declared_consumption_keeps_three_distinct_evidence_identities(monkeypatch):
    instance, plan = problem()
    instance.voyage.consumption = [VoyageConsumption(group_id="fuel_oil", mass_kg=0.)]
    seen = []
    probes(monkeypatch, seen=seen)
    report = full.check(instance, plan)
    assert report.passed
    for name in PARTS:
        assert [entry[4] for entry in seen if entry[0] == name] == [1., 1., 1.]
        for condition_id, phase, _ in STATES:
            part = report.parts[f"condition:{condition_id}:{name}"]
            assert part.metrics[f"condition.phase: {phase}"] == 1.
            assert part.metrics["condition.remaining_fraction"] == 1.
