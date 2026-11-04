"""Layer L1 metadata only; enforcement intentionally belongs to later layers."""

import pytest
from pydantic import ValidationError

from stowage.rules.catalogue import Catalogue, load_catalogue, rule_freeze_v1
from stowage.versions import component_versions


NEW_PORT_IDS = {
    "port.dg.refused_class", "port.dg.direct_delivery", "port.e3.quay_crossing",
    "port.e3.yard_crossing", "port.stability.cargo_operations",
}


def test_new_port_contracts_have_traceable_sources_and_planned_owners():
    catalogue = load_catalogue()
    for rule_id in NEW_PORT_IDS:
        rule = catalogue[rule_id]
        assert rule.scope == "port" and rule.hardness == "hard"
        assert rule.status == "provisional"
        assert set(rule.enforced_by) == {"checker", "feasibility_engine", "solver"}
        assert any("28 Oct 2026" in reference for reference in rule.references)
        assert rule.source == ("user specification (D62)" if rule_id.endswith("_crossing") else "expert")


@pytest.mark.parametrize("rule_id", ["port.dg.refused_class", "port.dg.direct_delivery"])
def test_dg_policies_are_port_scoped_and_use_d66_primary_class_only(rule_id):
    rule = load_catalogue()[rule_id]
    assert rule.parameters["classes"].profile_field == "port.port_rules"
    assert "PortRuleRef.params.classes" in rule.parameters["classes"].provenance.assumption
    assert rule.parameters["include_subsidiary_risks"].value is False
    assert "D66" in rule.parameters["include_subsidiary_risks"].provenance.assumption
    assert rule.parameters["include_subsidiary_risks"].provenance.clause == "D66"
    assert "primary class only" in rule.description.lower()
    assert "recursively through DangerousGoods.additional" not in rule.description
    if rule_id == "port.dg.direct_delivery":
        assert "all class-1 divisions and compatibility groups" in rule.description
        assert "1.4S" in rule.description and "1.1D" in rule.description
        assert "Preserve original hazard class strings" in rule.description
        assert rule.parameters["exempt_divisions"].value == []


@pytest.mark.parametrize("suffix", ["under_deck", "on_deck", "top_stowage", "away_from_heat"])
def test_special_requests_remain_binding_without_killed_slots(suffix):
    rule = load_catalogue()[f"expert.e8.{suffix}"]
    assert rule.hardness == "hard"
    assert "create no killed slots" in rule.description
    assert "unplaceable request makes the instance infeasible" in rule.description
    assert any("Q12, 28 Oct 2026" in reference for reference in rule.references)
    if suffix == "away_from_heat":
        assert rule.status == "provisional"
        assert rule.parameters["minimum_clearance"].value == 2.4
        assert "numeric heat clearance remains provisional" in rule.description


def test_crane_height_and_crossing_use_h_and_explicit_geometry():
    with rule_freeze_v1(enabled=True):
        catalogue = load_catalogue()
    for rule_id in ("port.e3.quay_height", "port.e3.quay_crossing"):
        height = catalogue[rule_id].parameters["max_crane_height_tiers"]
        assert height.unit == "tiers"
        assert height.profile_field == "port.max_crane_height_tiers"
    crossing = catalogue["port.e3.quay_crossing"]
    assert crossing.parameters["berthing_side"].profile_field == "port.berthing_side"
    yard_crossing = catalogue["port.e3.yard_crossing"]
    assert yard_crossing.parameters["truck_lane_side"].profile_field == "port.yard.blocks.truck_lane_side"
    assert "max_crane_height_tiers" not in yard_crossing.parameters
    assert "truck_lane_side" not in crossing.parameters
    assert ">= H" in crossing.description
    assert "APPROXIMATION:" in crossing.description
    assert "hatch-cover" in crossing.description
    assert "40-foot footprint" in crossing.description


@pytest.mark.parametrize("rule_id", ["expert.e9.over_height", "expert.e9.over_width_length"])
def test_oog_retains_hard_killed_slot_safety_and_separate_objective_metadata(rule_id):
    rule = load_catalogue()[rule_id]
    assert rule.hardness == "hard" and rule.penalty is None
    pending = rule.pending_contract
    assert pending.parameters["under_deck_allowed"].value is True
    assert pending.parameters["killed_slots_must_be_empty"].value is True
    assert pending.parameters["tie_break_metric"].value == "killed_slots"
    assert pending.parameters["objective_priority"].value == 4
    if rule_id == "expert.e9.over_height":
        assert rule.parameters["top_only"].value is True
        assert rule.parameters["top_only"].superseded_by


def test_cargo_operations_stability_has_no_unconfirmed_numeric_limits():
    rule = load_catalogue()["port.stability.cargo_operations"]
    assert rule.parameters["intermediate_stability"].value is True
    assert rule.parameters["intermediate_stability"].provenance.clause == "Q6"
    assert "final pre-departure" in rule.description
    assert "APPROXIMATION" in rule.description
    assert "must not reject a checker-acceptable state" in rule.description


def test_catalogue_json_round_trip_and_content_version():
    catalogue = load_catalogue()
    assert Catalogue.model_validate_json(catalogue.model_dump_json()) == catalogue
    assert component_versions()["rule_catalogue"].startswith("sha256:")


def test_arbitrary_expert_safety_rule_cannot_become_soft():
    catalogue = load_catalogue()
    rule = catalogue["expert.physical.stack_weight"].model_dump()
    rule.update(hardness="soft", penalty={"kind": "tie_break", "priority": 4,
                                         "description": "Unsafe waiver"})
    with pytest.raises(ValidationError, match="D11"):
        type(catalogue.rules[0]).model_validate(rule)


def test_e6_soft_preference_requires_its_count_metric():
    catalogue = load_catalogue()
    rule = catalogue["expert.e6.heavy_under_deck"].model_dump()
    rule["pending_contract"]["penalty"]["metric"] = None
    with pytest.raises(ValidationError, match="D11"):
        type(catalogue.rules[0]).model_validate(rule)


@pytest.mark.parametrize("change", [
    {"penalty": None},
    {"enforced_by": ["checker"]},
    {"enforced_by": ["solver", "solver"]},
])
def test_pending_e6_contract_rejects_missing_penalty_or_wrong_owners(change):
    catalogue = load_catalogue()
    rule = catalogue["expert.e6.heavy_under_deck"].model_dump()
    rule["pending_contract"].update(change)
    with pytest.raises(ValidationError):
        type(catalogue.rules[0]).model_validate(rule)


@pytest.mark.parametrize("hardness", ["soft", "tolerated_if_inherited"])
def test_pending_contract_cannot_waive_arbitrary_safety_rules(hardness):
    catalogue = load_catalogue()
    rule = catalogue["expert.physical.stack_weight"].model_dump()
    rule["pending_contract"] = {
        "hardness": hardness, "enforced_by": ["solver"],
        "activation": "Must not waive a safety limit",
        "penalty": ({"kind": "tie_break", "priority": 4,
                     "metric": "heavy_on_deck_count", "description": "Unsafe waiver"}
                    if hardness == "soft" else None),
    }
    with pytest.raises(ValidationError):
        type(catalogue.rules[0]).model_validate(rule)
