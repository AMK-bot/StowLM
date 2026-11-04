"""Addendum 1 contracts stay explicit and gated without changing legacy readers."""

import math

import pytest

from stowage.rules import catalogue as module


E1 = "expert.e1.heavy_bottom"
E2 = "expert.e2.weight_order"
E6 = "expert.e6.heavy_under_deck"
E9H = "expert.e9.over_height"
E9W = "expert.e9.over_width_length"


@pytest.mark.parametrize("rule_id", [E1, E2, E6, E9H, E9W])
def test_legacy_metadata_and_pending_contract_are_unambiguous(rule_id):
    rule = module.load_catalogue()[rule_id]
    assert rule.pending_contract is not None
    pending = rule.pending_contract
    assert pending.title and pending.description and pending.parameters
    assert "activation.rule_freeze_v1" in pending.activation
    assert module.rule_freeze_v1_enabled() is False
    if rule_id in {E1, E2}:
        assert "Gross mass" not in rule.title
        assert "weight_order_mode" not in rule.parameters
        assert pending.parameters["weight_order_mode"].value == "gross_mass_kg"
        assert pending.parameters["weight_order_tolerance_kg"].value == 0
        assert "order_mode" not in pending.parameters
    if rule_id == E1:
        assert "order_mode" not in rule.parameters
        assert rule.parameters["heavy_threshold"].value == 20_000
    if rule_id == E2:
        assert rule.parameters["order_mode"].value == "weight_class"
    if rule_id == E9H:
        assert rule.parameters["top_only"].value is True
        assert "top_only" not in pending.parameters


def test_shared_activation_projects_complete_pending_contract_in_snapshot_and_lookups():
    active = module.load_catalogue()[E6]
    assert active.hardness == "hard" and active.pending_contract is not None
    with module.rule_freeze_v1(enabled=True):
        catalogue = module.load_catalogue()
        rule = catalogue[E6]
        assert rule.hardness == "soft"
        assert rule.penalty.metric == "heavy_on_deck_count"
        assert rule.parameters["heavy_on_deck_threshold_kg"].value == 20_000
        assert "heavy_threshold" not in rule.parameters
        assert rule.pending_contract is None
        assert module.catalogue_snapshot().rules[E6] == rule
        assert module.get_rule(E6) == rule
        for rule_id in (E1, E2):
            assert catalogue[rule_id].parameters["weight_order_mode"].value == "gross_mass_kg"
            assert "order_mode" not in catalogue[rule_id].parameters
            assert catalogue[rule_id].pending_contract is None
        assert "top_only" not in catalogue[E9H].parameters
        assert catalogue[E9H].parameters["under_deck_allowed"].value is True
    assert module.load_catalogue()[E6] == active


def test_d66_primary_class_matching_has_no_subsidiary_or_compound_expansion():
    catalogue = module.load_catalogue()
    for rule_id in ("port.dg.refused_class", "port.dg.direct_delivery"):
        rule = catalogue[rule_id]
        assert rule.parameters["include_subsidiary_risks"].value is False
        assert "primary class only" in rule.description.lower()
        assert "recursively through" not in rule.description
        assert any("D66" in ref for ref in rule.references)
    direct = catalogue["port.dg.direct_delivery"]
    assert direct.parameters["exempt_divisions"].value == []
    assert "1.4S" in direct.description
    assert "APPROXIMATION:" in direct.description
    refusal = catalogue["port.dg.refused_class"]
    assert "2.0.3" in refusal.description
    assert "staying on board" in refusal.description


def test_d64_in_port_criteria_are_pinned_in_catalogue_parameters():
    rule = module.load_catalogue()["port.stability.cargo_operations"]
    p = rule.parameters
    assert p["equilibrium_frequency"].value == "every_lift"
    assert p["minimum_fluid_gm_m"].value == .15
    assert p["minimum_fluid_gm_m"].unit == "m"
    assert p["max_list_rad"].profile_field == "vessel.hull.max_list_rad"
    assert p["maximum_trim_angle_rad"].value == math.radians(1)
    assert p["maximum_trim_angle_rad"].unit == "rad"
    assert p["harbour_wave_bending_fraction"].value == .5
    assert p["harbour_bending_mode"].value == "per_sign"
    assert p["strength_top_surrogate_count"].value == 3
    assert "Lpp" in rule.description and "tan" in rule.description
    assert "single total order" in rule.description
    assert "changeover" in rule.description
    assert any("D64" in ref for ref in rule.references)
    for name in ("minimum_fluid_gm_m", "maximum_trim_angle_rad", "harbour_wave_bending_fraction"):
        assert "APPROXIMATION:" in p[name].provenance.assumption


def test_every_port_rule_states_its_activation_path():
    for rule in module.load_catalogue():
        if rule.scope == "port":
            path = rule.parameters["activation_path"]
            assert path.unit == "1" and path.value
            if rule.id in {"port.dg.refused_class", "port.dg.direct_delivery", "port.e3.quay_crossing",
                           "port.e3.yard_crossing", "port.stability.cargo_operations"}:
                assert "activation.rule_freeze_v1" in path.value


def test_crossing_paths_cover_addendum_actions_without_hold_blockers():
    catalogue = module.load_catalogue()
    description = catalogue["port.e3.quay_crossing"].description
    for phrase in ("on-deck stacks only", "same-bay shift", "another bay", "via the quay",
                   "quay-side-most row", "source and target stacks"):
        assert phrase in description
    assert "hold contents never block" in description
    yard_description = catalogue["port.e3.yard_crossing"].description
    for phrase in ("40'/45'", "union", "source and target stacks", "truck_lane_side",
                   "source bay", "destination row", "APPROXIMATION:"):
        assert phrase in yard_description


def test_shared_heavy_boundary_provenance_acknowledges_active_legacy_e6():
    p = module.load_catalogue().weight_classes.parameters["heavy_min_kg"]
    assert "legacy E6" in p.provenance.assumption
    assert "pending" in p.provenance.assumption
    assert "independent" in p.provenance.assumption
