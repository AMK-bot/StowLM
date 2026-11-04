"""E8 tank/source namespaces; synthetic static rules only, no vessel solver."""
import pytest

from stowage.checker import rules_special
from stowage.model import ShipState
from stowage.model.cargo import SpecialStowage
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement
from stowage.model.vessel import HeatSource

HEAT = "expert.e8.away_from_heat"
MISSING = "checker.missing_vessel_data"


@pytest.fixture
def heat_case(monkeypatch):
    # This regression belongs to the MVP OFF path, independent of ON features.
    monkeypatch.setattr(rules_special, "rule_freeze_v1_enabled", lambda: False)
    instance = build_instance()
    instance.vessel.fixed_loads = []
    for box in instance.containers:
        box.max_gross_mass_kg = 100000  # Synthetic plate; not an ISO size limit.
    box = instance.containers[1]
    box.special_stowage = [SpecialStowage.AWAY_FROM_HEAT]
    instance.rule_overrides[HEAT] = {"minimum_clearance": 1.0}
    ship = ShipState(vessel_id=instance.vessel.id, placements=[
        Placement(slot="0010102", container_id=box.id)])
    tank = next(tank for tank in instance.vessel.tanks if tank.heated)
    source = instance.vessel.heat_sources[0]
    assert source.id == tank.id and source.kind == "heated_tank"
    return instance, ship, tank, source


def heat_violations(instance, ship):
    return [violation for violation in rules_special.special_violations(
        instance, ship, call_index=0)
        if violation.rule_id == HEAT or (
            violation.rule_id == MISSING and HEAT in violation.message)]


def test_explicit_tank_link_accepts_a_different_source_id(heat_case):
    instance, ship, tank, source = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={
        "id": f"heat-{tank.id}", "tank_id": tank.id})]
    assert heat_violations(instance, ship) == []


def test_legacy_same_id_source_remains_supported(heat_case):
    instance, ship, _, source = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"tank_id": None})]
    assert heat_violations(instance, ship) == []


@pytest.mark.parametrize("tank_id", ["UNKNOWN-TANK", "", "WB-P"])
def test_explicit_wrong_link_cannot_fall_back_to_source_id(heat_case, tank_id):
    instance, ship, _, source = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"tank_id": tank_id})]
    assert any(violation.rule_id == MISSING for violation in heat_violations(instance, ship))


@pytest.mark.parametrize("kind", ["engine_room", "funnel", "other"])
def test_non_tank_source_id_collision_cannot_cover_a_heated_tank(heat_case, kind):
    instance, ship, _, source = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"kind": kind, "tank_id": None})]
    assert any(violation.rule_id == MISSING for violation in heat_violations(instance, ship))


def test_missing_heat_geometry_still_fails_closed(heat_case):
    instance, ship, _, _ = heat_case
    instance.vessel.heat_sources = []
    assert any(violation.rule_id == MISSING for violation in heat_violations(instance, ship))


def test_an_unrepresented_heated_tank_is_never_dropped(heat_case):
    instance, ship, tank, source = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={
        "id": f"heat-{tank.id}", "tank_id": tank.id})]
    instance.vessel.tanks.append(tank.model_copy(update={"id": "UNREPRESENTED-FUEL"}))
    assert any(violation.rule_id == MISSING for violation in heat_violations(instance, ship))


def test_a_heat_prefix_alone_is_not_a_tank_link(heat_case):
    instance, ship, tank, source = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={
        "id": f"heat-{tank.id}", "tank_id": None})]
    assert any(violation.rule_id == MISSING for violation in heat_violations(instance, ship))


def test_link_normalization_keeps_nearby_engine_heat_rejection(heat_case):
    instance, ship, tank, source = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={
        "id": f"heat-{tank.id}", "tank_id": tank.id}), HeatSource(
        id="near-engine", kind="engine_room", x_min_m=46, x_max_m=47,
        y_min_m=-2, y_max_m=0, z_min_m=2, z_max_m=3)]
    violations = heat_violations(instance, ship)
    assert not any(violation.rule_id == MISSING for violation in violations)
    assert any(violation.rule_id == HEAT and "near-engine" in violation.message
               for violation in violations)
