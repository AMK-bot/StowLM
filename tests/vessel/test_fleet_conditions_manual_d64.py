"""D64 operating ballast replaces other ballast, never manual cargo or fuel."""
from math import isclose

import numpy as np
import pytest

from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.fleet_manual_witness import CURRENT_WITNESSES

CASES = (("feeder_500", "C1"), ("feeder_1700", "C3"))
_FROZEN_SHA256 = {
    ("feeder_500", "C1"): "08432f8acba16e3a07e94ef5416b6b2649bf57da42cd28744e0d9dc87cd26e5a",
    ("feeder_1700", "C3"): "17a674c43a91541676082d4e4e0a33511e13edc73651ab0828e2eac3f94413d1",
}


def _declare_current_crane_service(port):
    """Supply newly required D74 rates without changing any frozen port limit.

    APPROXIMATION: use the current synthetic archetype's declared crane service
    rates. These are operating-time inputs, not extra cargo or liquid, and no
    historical berth, height, outreach or cargo capability is replaced.
    """
    from stowage.ports import load_port
    current = load_port(port.archetype).quay_crane
    updates = {field: getattr(current, field) for field in (
        "gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h")}
    return port.model_copy(update={"quay_crane": port.quay_crane.model_copy(update=updates)})


def _moments(params, masses):
    values = np.zeros(4)
    for tank in params["tanks"]:
        mass = masses.get(tank["id"], 0.)
        table = tank["table"]
        volume = mass / tank["content_density_kg_m3"]
        coordinates = [np.interp(volume, [row["volume_m3"] for row in table],
                                 [row[key] for row in table])
                       for key in ("lcg_m", "tcg_m", "vcg_m")]
        values += np.array([1., *coordinates]) * mass
    return values


@pytest.mark.parametrize("name,condition", CASES)
def test_on_manual_half_pair_is_paid_from_other_existing_ballast(name, condition):
    params = load_ship_params(name)
    key = f"{name}.{condition}"
    with rule_freeze_v1(enabled=False):
        historical = CURRENT_WITNESSES.get(key)
    with rule_freeze_v1(enabled=True):
        current = CURRENT_WITNESSES.get(key)
    pair = next(pair for pair in params["tank_pairs"] if pair["role"] == "anti_heeling")
    pair_ids = {pair["port_tank"], pair["starboard_tank"]}
    for tank in params["tanks"]:
        if not tank["adjustable"]:
            continue
        mass = current["tank_masses_kg"][tank["id"]]
        assert 0. <= mass <= tank["capacity_m3"] * tank["content_density_kg_m3"]
        if tank["id"] in pair_ids:
            assert mass == .5 * tank["capacity_m3"] * tank["content_density_kg_m3"]
        else:
            assert mass <= historical["tank_masses_kg"][tank["id"]] + 1e-6
    old = _moments(params, historical["tank_masses_kg"])
    new = _moments(params, current["tank_masses_kg"])
    assert isclose(old[0], new[0], abs_tol=.05)
    assert isclose(old[1], new[1], abs_tol=5.)
    assert abs(new[2]) <= 5.
    assert current["refit_audit"]["cargo_and_consumables_unchanged"] is True
    from stowage.versions import component_versions
    with rule_freeze_v1(enabled=True):
        assert current["provenance"]["versions"] == component_versions()
    assert current["refit_audit"]["historical_finisher_versions"] == historical["provenance"]["versions"]


@pytest.mark.parametrize("name,condition", CASES)
def test_off_keeps_the_exact_historical_manual_record(name, condition):
    key = f"{name}.{condition}"
    with rule_freeze_v1(enabled=False):
        historical = CURRENT_WITNESSES.get(key)
        assert CURRENT_WITNESSES.get(key) is historical


@pytest.mark.parametrize("enabled", [False, True])
def test_all_mapping_lookup_surfaces_select_the_same_records(enabled):
    with rule_freeze_v1(enabled=enabled):
        for name, condition in CASES:
            key = f"{name}.{condition}"
            assert CURRENT_WITNESSES.get(key) == CURRENT_WITNESSES[key]
            assert CURRENT_WITNESSES.get(key) == dict(CURRENT_WITNESSES.items())[key]


@pytest.mark.parametrize("name,condition", CASES)
def test_static_on_witness_rejects_changed_parameter_bytes_but_off_is_unchanged(name, condition, monkeypatch):
    from stowage.vessel import fleet
    key = f"{name}.{condition}"
    with rule_freeze_v1(enabled=False):
        historical = CURRENT_WITNESSES[key]
    original = fleet._parameter_bytes
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda root: original(root) + b"\n")
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match="requires requalification"):
        CURRENT_WITNESSES[key]
    with rule_freeze_v1(enabled=False):
        assert CURRENT_WITNESSES[key] is historical


@pytest.mark.parametrize("name,condition", CASES)
def test_requalified_manual_passes_full_checker_without_cargo_or_fuel_changes(name, condition, tmp_path):
    import json
    from stowage.checker.full import check
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.vessel.fleet import load_vessel
    from stowage.vessel.fleet_manual import standard_manual_conditions
    from tests.vessel.test_fleet_manual_restore import _manual_instance, _physical_recipe

    with rule_freeze_v1(enabled=True):
        vessel = load_vessel(name)
        params = load_ship_params(name)
        current = next(row for row in standard_manual_conditions(vessel, params)
                       if row["condition"] == condition)
        with rule_freeze_v1(enabled=False):
            previous = next(row for row in standard_manual_conditions(vessel, params)
                            if row["condition"] == condition)
        assert current["cargo_cells"] == previous["cargo_cells"]
        assert current["cargo_budget_kg"] == previous["cargo_budget_kg"]
        _physical_recipe(vessel, current)
        instance = _manual_instance(vessel, current)
        instance = instance.model_copy(update={"port": _declare_current_crane_service(instance.port)})
        plan = MovePlan(id=f"{instance.id}.D64", instance_id=instance.id,
            call_index=instance.call_index, ballast=BallastCondition(source="planner",
                tank_masses_kg={tank.id: current["departure_tank_masses_kg"][tank.id]
                                for tank in vessel.tanks if tank.adjustable}))
        report = check(instance, plan)
    evidence = tmp_path / f"{name}-{condition}-qualification.json"
    evidence.write_text(json.dumps(dict(instance=instance.model_dump(mode="json"),
        plan=plan.model_dump(mode="json"), report=report.model_dump(mode="json")),
        allow_nan=False))
    print(f"D64 manual qualification evidence: {evidence}")
    assert report.passed, [(violation.rule_id, violation.message, violation.measured, violation.limit)
                           for violation in report.violations]
    assert all(part.ran for part in report.parts.values()), {
        key: part.error for key, part in report.parts.items() if not part.ran}


@pytest.mark.parametrize("name,condition", CASES)
def test_refit_passes_full_checker_with_exact_frozen_manual_cargo_and_fuel(name, condition, tmp_path):
    """A current regenerated recipe cannot stand in for the frozen user case.

    In particular, enlarging the full-HEEL cargo reserve can reduce regenerated
    C3 cargo. Read the original raw payload first, replace only current vessel
    geometry and adjustable tank masses, then validate against current models.
    """
    import json
    from copy import deepcopy
    from hashlib import sha256
    from pathlib import Path

    from stowage.checker.full import check
    from stowage.model import Instance, MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.vessel.fleet import load_vessel

    source = (Path(__file__).resolve().parents[2] / "experiments/background-1028/d71-round2"
              / f"{name}-{condition}-frozen.json")
    frozen_bytes = source.read_bytes()
    assert sha256(frozen_bytes).hexdigest() == _FROZEN_SHA256[name, condition]
    frozen = json.loads(frozen_bytes)
    payload = deepcopy(frozen)
    with rule_freeze_v1(enabled=True):
        vessel = load_vessel(name)
        witness = CURRENT_WITNESSES[f"{name}.{condition}"]
        adjustable = {tank.id for tank in vessel.tanks if tank.adjustable}
        assert set(witness["tank_masses_kg"]) == adjustable
        payload["vessel"] = vessel.model_dump(mode="json")
        payload["onboard"]["tank_masses_kg"].update(witness["tank_masses_kg"])
        instance = Instance.model_validate(payload)
        instance = instance.model_copy(update={"port": _declare_current_crane_service(instance.port)})
        validated = instance.model_dump(mode="json")
        for key, value in frozen.items():
            if key not in {"vessel", "onboard", "provenance", "port"}:
                assert validated[key] == value, f"frozen {key} changed"
        # All original port facts remain exact. Only service rates introduced
        # after this archived case are declared from its existing archetype.
        rates = {"gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h"}
        for key, value in frozen["port"].items():
            if key == "quay_crane":
                for field, fact in value.items():
                    if field not in rates or fact is not None:
                        assert validated["port"][key][field] == fact
            else:
                assert validated["port"][key] == value
        # Current geometry/witness lineage is merged into provenance by the
        # model; original evidence stays present while all physical facts below
        # and all other top-level fields remain byte-value identical.
        assert set(frozen["provenance"]["parents"]) <= set(validated["provenance"]["parents"])
        for pair in vessel.tank_pairs:
            if pair.role == "anti_heeling":
                tanks = {tank.id: tank for tank in vessel.tanks}
                for tid in (pair.port_tank, pair.starboard_tank):
                    assert witness["tank_masses_kg"][tid] == .5*tanks[tid].capacity_m3*tanks[tid].content_density_kg_m3
        for key, value in frozen["onboard"].items():
            if key != "tank_masses_kg":
                assert validated["onboard"][key] == value, f"frozen onboard.{key} changed"
        for tank_id, mass in frozen["onboard"]["tank_masses_kg"].items():
            if tank_id not in adjustable:
                assert instance.onboard.tank_masses_kg[tank_id] == mass
        plan = MovePlan(id=f"{instance.id}.D64.frozen", instance_id=instance.id,
            call_index=instance.call_index, ballast=BallastCondition(source="planner",
                tank_masses_kg=dict(witness["tank_masses_kg"])))
        report = check(instance, plan)
    evidence = tmp_path / f"{name}-{condition}-frozen-qualification.json"
    evidence.write_text(json.dumps(dict(frozen_sha256=_FROZEN_SHA256[name, condition],
        instance=validated, plan=plan.model_dump(mode="json"),
        report=report.model_dump(mode="json")), allow_nan=False))
    print(f"D64 frozen manual qualification evidence: {evidence}")
    assert report.passed, [(violation.rule_id, violation.message, violation.measured, violation.limit)
                           for violation in report.violations]
    assert all(part.ran for part in report.parts.values()), {
        key: part.error for key, part in report.parts.items() if not part.ran}
