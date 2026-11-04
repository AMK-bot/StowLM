"""D50 additive voyage identity, policy references, serialization and evidence."""

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

import stowage.model as model
from stowage.frozen import StubTaintError
from stowage.io import load_instance, save_instance
from stowage.model.fixtures import build_instance


def _provenance(name):
    return {"created_by": name, "parents": ["synthetic-voyage-declaration"]}


def _policy():
    return {
        "groups": [{"id": "fuel", "kind": "fuel_oil", "tank_ids": ["FO-1"],
                    "provenance": _provenance("test.group")}],
        "drawdown_order": ["FO-1"],
        "default_arrival_remaining_fraction": 0.1,
        "drawdown_order_provenance": _provenance("test.order"),
        "arrival_rule_provenance": _provenance("test.rule"),
        "provenance": _provenance("test.policy"),
    }


def _identity(*, phase="departure", **changes):
    departure = phase == "departure"
    return {
        "id": "leg-0-departure" if departure else "leg-0-arrival",
        "phase": phase,
        "call_index": 0 if departure else 1,
        "locode": "THLCH" if departure else "THBKK",
        "environment_id": "berth-departure" if departure else "berth-arrival",
        "density_id": "declared-dock-water",
        "water_density_kg_m3": 1025.0,
        "provenance": _provenance("test.condition"),
    } | changes


def _spec():
    return {
        "departure": _identity(),
        "arrival": _identity(phase="arrival"),
        "consumption": None,
        "ballast_operations": [],
        "provenance": _provenance("test.voyage"),
    }


def _payload():
    payload = build_instance().model_dump(mode="json")
    payload["vessel"]["voyage_policy"] = _policy()
    payload["voyage"] = _spec()
    return payload


def test_legacy_json_has_unknown_voyage_contract(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload.pop("voyage", None)
    payload["vessel"].pop("voyage_policy", None)
    path = tmp_path / "legacy-voyage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_instance(path)
    assert loaded.voyage is None
    assert loaded.vessel.voyage_policy is None


def test_voyage_policy_and_spec_json_round_trip_preserves_evidence(tmp_path):
    instance = model.Instance.model_validate(_payload())
    path = save_instance(instance, tmp_path / "voyage.json")
    loaded = load_instance(path)
    assert loaded == instance
    assert isinstance(loaded.vessel.voyage_policy, model.VoyageTankPolicy)
    assert isinstance(loaded.voyage, model.VoyageSpec)
    assert loaded.voyage.departure.environment_id == "berth-departure"
    assert loaded.voyage.arrival.phase == "arrival"
    assert loaded.voyage.consumption is None
    assert loaded.vessel.voyage_policy.default_arrival_remaining_fraction == 0.1
    assert loaded.vessel.voyage_policy.groups[0].provenance.created_by == "test.group"
    assert loaded.vessel.voyage_policy.drawdown_order_provenance.created_by == "test.order"
    assert loaded.vessel.voyage_policy.arrival_rule_provenance.created_by == "test.rule"
    assert loaded.voyage.arrival.provenance.created_by == "test.condition"


@pytest.mark.parametrize("evidence_path", [
    ("vessel", "voyage_policy", "groups", 0, "provenance"),
    ("vessel", "voyage_policy", "drawdown_order_provenance"),
    ("vessel", "voyage_policy", "arrival_rule_provenance"),
    ("voyage", "arrival", "provenance"),
    ("voyage", "provenance"),
])
def test_voyage_nested_evidence_taints_instance_and_json_boundary(tmp_path, evidence_path):
    payload = _payload()
    evidence = payload
    for key in evidence_path:
        evidence = evidence[key]
    evidence["stubs"] = {"voyage.synthetic": "test-only unverified evidence"}
    instance = model.Instance.model_validate(payload)
    assert instance.provenance.tainted
    with pytest.raises(StubTaintError):
        save_instance(instance, tmp_path / "tainted.json")
    path = save_instance(instance, tmp_path / "tainted.json", allow_stubs=True)
    assert load_instance(path).provenance.tainted


@pytest.mark.parametrize("field", ["id", "environment_id", "density_id"])
@pytest.mark.parametrize("value", ["", " "])
def test_condition_identity_requires_stable_nonblank_identifiers(field, value):
    with pytest.raises(ValidationError, match=field):
        model.VoyageConditionIdentity.model_validate(_identity(**{field: value}))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, True])
def test_condition_density_is_finite_positive_and_not_boolean(value):
    with pytest.raises(ValidationError, match="water_density_kg_m3"):
        model.VoyageConditionIdentity.model_validate(_identity(water_density_kg_m3=value))


@pytest.mark.parametrize("changes", [{"phase": "at_sea"}, {"call_index": -1}, {"locode": "invalid"}])
def test_condition_identity_is_typed(changes):
    with pytest.raises(ValidationError):
        model.VoyageConditionIdentity.model_validate(_identity(**changes))


@pytest.mark.parametrize("part,field,value", [
    ("departure", "call_index", 1),
    ("departure", "locode", "THBKK"),
    ("departure", "water_density_kg_m3", 1000.0),
    ("arrival", "call_index", 2),
    ("arrival", "locode", "THSGZ"),
    ("departure", "phase", "arrival"),
    ("arrival", "phase", "departure"),
])
def test_voyage_conditions_match_call_phase_and_current_density(part, field, value):
    payload = _payload()
    payload["voyage"][part][field] = value
    with pytest.raises(ValidationError, match="voyage|departure|arrival|density"):
        model.Instance.model_validate(payload)


def test_voyage_condition_ids_are_distinct():
    spec = _spec()
    spec["arrival"]["id"] = spec["departure"]["id"]
    with pytest.raises(ValidationError, match="id|identity|condition"):
        model.VoyageSpec.model_validate(spec)


def test_voyage_spec_cannot_supply_free_form_arrival_tank_state():
    with pytest.raises(ValidationError, match="arrival_tank_masses_kg"):
        model.VoyageSpec.model_validate(_spec() | {"arrival_tank_masses_kg": {"FO-1": 123.0}})


def test_declared_voyage_requires_vessel_policy():
    payload = _payload()
    payload["vessel"]["voyage_policy"] = None
    with pytest.raises(ValidationError, match="voyage_policy"):
        model.Instance.model_validate(payload)


@pytest.mark.parametrize("mutation", ["unknown", "ballast", "kind_mismatch", "overlap", "uncovered", "missing_order", "duplicate_order", "ballast_order"])
def test_vessel_voyage_policy_resolves_groups_and_drawdown_order(mutation):
    payload = _payload()["vessel"]
    policy = payload["voyage_policy"]
    if mutation == "unknown":
        policy["groups"][0]["tank_ids"] = ["absent-tank"]
        policy["drawdown_order"] = ["absent-tank"]
    elif mutation == "ballast":
        policy["groups"][0]["tank_ids"] = ["WB-P", "FO-1"]
        policy["drawdown_order"] = ["WB-P", "FO-1"]
    elif mutation == "kind_mismatch":
        policy["groups"][0]["kind"] = "fresh_water"
    elif mutation == "overlap":
        policy["groups"].append(policy["groups"][0] | {"id": "second-fuel-group"})
    elif mutation == "uncovered":
        extra = deepcopy(next(tank for tank in payload["tanks"] if tank["id"] == "FO-1"))
        extra["id"] = "FO-2"
        payload["tanks"].append(extra)
    elif mutation == "missing_order":
        policy["drawdown_order"] = []
    elif mutation == "duplicate_order":
        policy["drawdown_order"] = ["FO-1", "FO-1"]
    elif mutation == "ballast_order":
        policy["drawdown_order"] = ["FO-1", "WB-P"]
    with pytest.raises(ValidationError, match="voyage_policy|tank|drawdown|group"):
        model.Vessel.model_validate(payload)


@pytest.mark.parametrize("kind", ["fuel_oil", "diesel_oil", "lube_oil", "fresh_water", "stores"])
def test_all_declared_consumable_kinds_can_be_grouped(kind):
    payload = _payload()["vessel"]
    fuel = next(tank for tank in payload["tanks"] if tank["id"] == "FO-1")
    fuel["kind"] = kind
    payload["voyage_policy"]["groups"][0]["kind"] = kind
    vessel = model.Vessel.model_validate(payload)
    assert vessel.voyage_policy.groups[0].kind == kind


def test_consumption_requires_exact_group_coverage_but_accepts_zero():
    payload = _payload()
    payload["voyage"]["consumption"] = [{"group_id": "fuel", "mass_kg": 0.0,
                                           "provenance": _provenance("test.consumption")}]
    instance = model.Instance.model_validate(payload)
    assert instance.voyage.consumption[0].mass_kg == 0.0
    assert model.Instance.model_validate_json(instance.model_dump_json()) == instance
    for invalid in ([], [{"group_id": "missing", "mass_kg": 0.0}],
                    [{"group_id": "fuel", "mass_kg": 0.0}, {"group_id": "fuel", "mass_kg": 1.0}]):
        bad = deepcopy(payload)
        bad["voyage"]["consumption"] = invalid
        with pytest.raises(ValidationError, match="consumption|group"):
            model.Instance.model_validate(bad)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1.0, True])
def test_voyage_consumption_is_finite_nonnegative(value):
    with pytest.raises(ValidationError, match="mass_kg"):
        model.VoyageConsumption(group_id="fuel", mass_kg=value, provenance=_provenance("test.consumption"))


@pytest.mark.parametrize("mass_change_kg", [-100.0, 0.0, 100.0])
def test_ballast_operation_is_serializable_declaration_and_rejected_on_derivation(tmp_path, mass_change_kg):
    payload = _payload()
    payload["voyage"]["ballast_operations"] = [{"tank_id": "WB-P", "mass_change_kg": mass_change_kg,
                                                "provenance": _provenance("test.prohibited-operation")}]
    instance = model.Instance.model_validate(payload)
    path = save_instance(instance, tmp_path / "ballast-declaration.json")
    loaded = load_instance(path)
    assert loaded.voyage.ballast_operations[0].mass_change_kg == mass_change_kg
    with pytest.raises(ValueError, match="ballast"):
        model.derive_arrival_liquid_state(loaded.onboard, loaded.voyage, vessel=loaded.vessel)


def test_vessel_policy_can_precede_instance_voyage_declaration():
    payload = _payload()
    payload["voyage"] = None
    instance = model.Instance.model_validate(payload)
    assert instance.voyage is None
    assert instance.vessel.voyage_policy.drawdown_order == ["FO-1"]


@pytest.mark.parametrize("field", ["consumption", "ballast_operations"])
def test_consumption_and_operation_evidence_taints_instance(field):
    payload = _payload()
    record = ({"group_id": "fuel", "mass_kg": 100.0} if field == "consumption"
              else {"tank_id": "WB-P", "mass_change_kg": 0.0})
    record["provenance"] = {"stubs": {"voyage.synthetic": "unverified declaration"}}
    payload["voyage"][field] = [record]
    assert model.Instance.model_validate(payload).provenance.tainted


@pytest.mark.parametrize("field", ["content_density_kg_m3", "capacity_m3"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_consumable_tank_capacity_inputs_are_finite_when_policy_declared(field, value):
    payload = _payload()["vessel"]
    fuel = next(tank for tank in payload["tanks"] if tank["id"] == "FO-1")
    fuel[field] = value
    if field == "capacity_m3":
        fuel["table"][-1]["volume_m3"] = value
    with pytest.raises(ValidationError, match="capacity|density|tank"):
        model.Vessel.model_validate(payload)


def test_policy_covers_multiple_groups_without_losing_declared_order():
    payload = _payload()["vessel"]
    extra = deepcopy(next(tank for tank in payload["tanks"] if tank["id"] == "FO-1"))
    extra.update(id="FW-1", kind="fresh_water", content_density_kg_m3=1000.0)
    payload["tanks"].append(extra)
    policy = payload["voyage_policy"]
    policy["groups"].append({"id": "water", "kind": "fresh_water", "tank_ids": ["FW-1"],
                             "provenance": _provenance("test.water-group")})
    policy["drawdown_order"] = ["FW-1", "FO-1"]
    vessel = model.Vessel.model_validate(payload)
    assert vessel.voyage_policy.drawdown_order == ["FW-1", "FO-1"]


def test_repeated_locode_does_not_replace_call_identity():
    payload = _payload()
    calls = payload["rotation"]["calls"]
    calls.insert(1, calls[0] | {"index": 1})
    for index, call in enumerate(calls):
        call["index"] = index
    payload["voyage"]["arrival"]["locode"] = "THLCH"
    instance = model.Instance.model_validate(payload)
    assert instance.voyage.departure.locode == instance.voyage.arrival.locode
    assert instance.voyage.arrival.call_index == 1
    payload["voyage"]["arrival"]["call_index"] = 0
    with pytest.raises(ValidationError, match="arrival|call_index|voyage"):
        model.Instance.model_validate(payload)


@pytest.mark.parametrize("fraction", [0.0, 0.2, 1.0, True])
def test_arrival_default_fraction_is_the_declared_d50_rule(fraction):
    payload = _policy()
    payload["default_arrival_remaining_fraction"] = fraction
    with pytest.raises(ValidationError, match="default_arrival_remaining_fraction"):
        model.VoyageTankPolicy.model_validate(payload)
