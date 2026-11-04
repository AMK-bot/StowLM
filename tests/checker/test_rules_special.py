"""Synthetic fixture variants for E8/E9 and static port restrictions."""
import pytest

from stowage.checker.rules_special import special_violations
from stowage.model import ShipState
from stowage.model.cargo import OutOfGauge, SpecialStowage
from tests.checker.test_rules_fixtures import build_instance
from stowage.model.port import PortRuleRef
from stowage.model.ship import Placement
from stowage.model.vessel import HeatSource


def fixture(requests=(), oog=None):
    instance = build_instance()
    # Plain 20-foot test box; all variants originate from the shared fixture builder.
    box = instance.containers[1]
    box.special_stowage = [SpecialStowage(r) for r in requests]
    box.oog = oog
    instance.rule_overrides = {
        "expert.e8.away_from_heat": {"minimum_clearance": 1.0},
        "expert.e9.over_width_length": {"oog_clearance": 0.0},
    }
    return instance, box


def state(instance, box, slot, other=None):
    placements = [Placement(slot=slot, container_id=box.id)]
    if other:
        placements.append(Placement(slot=other, container_id=instance.containers[2].id))
    return ShipState(vessel_id=instance.vessel.id, placements=placements)


def ids(instance, ship, call_index=0):
    return {v.rule_id for v in special_violations(instance, ship, call_index=call_index)}


@pytest.mark.parametrize("booking,passing,failing", [
    ("under_deck", "0010102", "0010182"),
    ("on_deck", "0010182", "0010102"),
])
def test_e8_location_pass_fail(booking, passing, failing):
    instance, box = fixture([booking])
    rule = f"expert.e8.{booking}"
    assert rule not in ids(instance, state(instance, box, passing))
    assert rule in ids(instance, state(instance, box, failing))


def test_e8_top_stowage_pass_fail():
    instance, box = fixture(["top_stowage"])
    rule = "expert.e8.top_stowage"
    assert rule not in ids(instance, state(instance, box, "0010102"))
    assert rule in ids(instance, state(instance, box, "0010102", "0010104"))


def test_e8_away_from_heat_pass_fail():
    instance, box = fixture(["away_from_heat"])
    ship = state(instance, box, "0010102")
    rule = "expert.e8.away_from_heat"
    assert rule not in ids(instance, ship)
    instance.vessel.heat_sources.append(HeatSource(id="near", kind="engine_room", x_min_m=46,
        x_max_m=47, y_min_m=-2, y_max_m=0, z_min_m=2, z_max_m=3))
    assert rule in ids(instance, ship)


def test_e8_catalogue_heat_policy_default():
    instance, box = fixture(["away_from_heat"])
    instance.rule_overrides.clear()
    assert "checker.missing_vessel_data" not in ids(instance, state(instance, box, "0010102"))


def test_e8_missing_heated_tank_geometry_fails_closed():
    instance, box = fixture(["away_from_heat"])
    instance.vessel.heat_sources.clear()
    assert "checker.missing_vessel_data" in ids(instance, state(instance, box, "0010102"))


def test_e9_over_height_pass_fail():
    instance, box = fixture(oog=OutOfGauge(over_height_m=0.4))
    rule = "expert.e9.over_height"
    assert rule not in ids(instance, state(instance, box, "0010102"))
    assert rule in ids(instance, state(instance, box, "0010102", "0010104"))


def test_e9_over_height_vertical_clearance():
    instance, box = fixture(oog=OutOfGauge(over_height_m=0.4))
    instance.rule_overrides["expert.e9.over_height"] = {"vertical_clearance": 2.8}
    assert "expert.e9.over_height" in ids(instance, state(instance, box, "0010102"))
    instance.rule_overrides["expert.e9.over_height"] = {"vertical_clearance": 3.0}
    assert "expert.e9.over_height" not in ids(instance, state(instance, box, "0010102"))


@pytest.mark.parametrize("oog,neighbour", [
    (OutOfGauge(over_width_port_m=0.3), "0010202"),
    (OutOfGauge(over_length_fore_m=0.3), "0030102"),
])
def test_e9_neighbour_envelope_pass_fail(oog, neighbour):
    instance, box = fixture(oog=oog)
    rule = "expert.e9.over_width_length"
    assert rule not in ids(instance, state(instance, box, "0010102"))
    assert rule in ids(instance, state(instance, box, "0010102", neighbour))


def test_e9_catalogue_neighbour_clearance_default():
    instance, box = fixture(oog=OutOfGauge(over_width_port_m=0.3))
    instance.rule_overrides.clear()
    assert "checker.missing_vessel_data" not in ids(instance, state(instance, box, "0010102"))


def test_e9_asymmetric_overhang_does_not_block_opposite_side():
    instance, box = fixture(oog=OutOfGauge(over_width_starboard_m=1.0))
    assert "expert.e9.over_width_length" not in ids(instance, state(instance, box, "0010102", "0010202"))


def test_port_quay_height_pass_fail():
    instance, box = fixture()
    instance.port.quay_crane.max_on_deck_tiers = 1
    rule = "port.e3.quay_height"
    assert rule not in ids(instance, state(instance, box, "0010182"))
    assert rule in ids(instance, state(instance, box, "0010184"))


def test_port_quay_current_profile_and_later_call():
    instance, box = fixture()
    instance.port.quay_crane.max_on_deck_tiers = 2
    instance.rotation.calls[0].max_on_deck_tiers = 1
    instance.rotation.calls[1].max_on_deck_tiers = 1
    ship = state(instance, box, "0010184")
    assert "port.e3.quay_height" not in ids(instance, ship)
    assert "port.e3.quay_height" in ids(instance, ship, 1)


def test_port_length_pass_fail():
    instance, box = fixture()
    ship = state(instance, box, "0010102")
    rule = "port.call.length"
    instance.port.port_rules = [PortRuleRef(rule_id=rule, params={"max_loa": 128.0})]
    assert rule not in ids(instance, ship)
    instance.rule_overrides[rule] = {"max_loa": 127.0}
    assert rule in ids(instance, ship)


def test_port_length_missing_hull_fails_closed():
    instance, box = fixture()
    instance.rule_overrides["port.call.length"] = {"max_loa": 128.0}
    instance.vessel.hull = None
    assert "checker.missing_vessel_data" in ids(instance, state(instance, box, "0010102"))


def test_focus_detects_booking_blocked_in_candidate_stack():
    instance, box = fixture(["top_stowage"])
    ship = state(instance, box, "0010102", "0010104")
    candidate = instance.containers[2]
    violations = special_violations(instance, ship, call_index=0, focus_container_id=candidate.id)
    assert "expert.e8.top_stowage" in {v.rule_id for v in violations}


def test_focus_detects_existing_oog_blocking_candidate():
    instance, box = fixture(oog=OutOfGauge(over_width_port_m=0.3))
    ship = state(instance, box, "0010102", "0010202")
    violations = special_violations(instance, ship, call_index=0, focus_container_id=instance.containers[2].id)
    assert "expert.e9.over_width_length" in {v.rule_id for v in violations}


def test_focus_excludes_unrelated_booking_failure():
    instance, box = fixture(["on_deck"])
    ship = state(instance, box, "0010102", "0010202")
    violations = special_violations(instance, ship, call_index=0, focus_container_id=instance.containers[2].id)
    assert "expert.e8.on_deck" not in {v.rule_id for v in violations}
