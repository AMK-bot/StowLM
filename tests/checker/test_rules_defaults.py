"""D32 heat and OOG defaults, exercised through both public checker entry points.

Heat protection uses the shortest distance between occupied cargo and heated
structures (IMDG 42-24 7.1.2). ASSUMPTION: OOG uses its declared rectangular
occupied envelope with no additional margin, pending expert Q13.
"""

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M
from stowage.model import Placement, ShipState
from stowage.model.cargo import OutOfGauge, SpecialStowage
from tests.checker.test_rules_fixtures import build_instance
from stowage.model.port import PortRuleRef
from stowage.model.vessel import HeatSource

HEAT = "expert.e8.away_from_heat"
OOG = "expert.e9.over_width_length"


def _fixture():
    instance = build_instance()
    instance.containers = [box.model_copy(update={
        "length_ft": 20, "iso_type": "22G1", "gross_mass_kg": 6000,
        "height_m": 2.591, "reefer": False, "reefer_setpoint_c": None,
        "dg": None, "special_stowage": [], "oog": None,
    }) for box in instance.containers[:2]]
    instance.rule_overrides = {}
    return instance, instance.containers[0]


def _results(instance, box, *, neighbour=False, candidate_is_neighbour=False):
    placement = Placement(slot="0010102", container_id=box.id)
    other = Placement(slot="0010202", container_id=instance.containers[1].id)
    complete = ShipState(vessel_id=instance.vessel.id,
                         placements=[placement, other] if neighbour else [placement])
    full = check_static_rules(instance, complete, call_index=0).violations
    if candidate_is_neighbour:
        partial = complete.model_copy(update={"placements": [placement]})
        incremental = placement_violations(instance, partial, instance.containers[1], other.slot, call_index=0)
    else:
        partial = complete.model_copy(update={"placements": [other] if neighbour else []})
        incremental = placement_violations(instance, partial, box, placement.slot, call_index=0)
    for violations in (full, incremental):
        assert not [v for v in violations if v.rule_id == "checker.missing_vessel_data"], violations
    return full, incremental


def _heat_at_distance(instance, box, distance):
    cell = next(c for c in instance.vessel.cells if c.id == "0010102")
    # The box occupies x +/- half its length. A source beyond its forward face
    # overlaps the other two axes so this is an exact face-to-face distance.
    edge = cell.lcg_m + CONTAINER_LENGTH_M[20] / 2
    instance.vessel.heat_sources.append(HeatSource(
        id="synthetic-near-structure", kind="engine_room",
        x_min_m=edge + distance, x_max_m=edge + distance + 1,
        y_min_m=cell.tcg_m - 0.1, y_max_m=cell.tcg_m + 0.1,
        z_min_m=2.1, z_max_m=2.2,
    ))
    box.special_stowage = [SpecialStowage("away_from_heat")]


@pytest.mark.parametrize("distance,failing", [(2.399, True), (2.4, False), (2.401, False)])
def test_heat_catalogue_default_full_incremental(distance, failing):
    instance, box = _fixture()
    _heat_at_distance(instance, box, distance)
    for violations in _results(instance, box):
        matches = [v for v in violations if v.rule_id == HEAT]
        assert bool(matches) is failing
        if matches:
            assert matches[0].limit == 2.4
            assert matches[0].measured == pytest.approx(distance)


@pytest.mark.parametrize("clearance,distance,failing", [(3.0, 2.5, True), (1.0, 1.5, False), (0.0, 0.0, False)])
@pytest.mark.parametrize("parameter", ["minimum_clearance", "heat_clearance_m"])
def test_heat_instance_override_precedes_catalogue(clearance, distance, failing, parameter):
    instance, box = _fixture()
    _heat_at_distance(instance, box, distance)
    instance.rule_overrides[HEAT] = {parameter: {"value": clearance}}
    for violations in _results(instance, box):
        assert any(v.rule_id == HEAT for v in violations) is failing


def _oog_neighbour(instance, box, gap):
    box.oog = OutOfGauge(over_width_port_m=0.3)
    # Move the second row to the desired signed gap from the declared overhang.
    for cell in instance.vessel.cells:
        if cell.id[3:5] == "01":
            cell.tcg_m = 0.0
        elif cell.id[3:5] == "02":
            cell.tcg_m = CONTAINER_WIDTH_M + box.oog.over_width_port_m + gap


@pytest.mark.parametrize("gap,failing", [(-0.001, True), (0.0, False), (0.001, False)])
@pytest.mark.parametrize("candidate_is_neighbour", [False, True])
def test_oog_catalogue_zero_margin_full_incremental(gap, failing, candidate_is_neighbour):
    instance, box = _fixture()
    _oog_neighbour(instance, box, gap)
    for violations in _results(instance, box, neighbour=True, candidate_is_neighbour=candidate_is_neighbour):
        assert any(v.rule_id == OOG for v in violations) is failing


@pytest.mark.parametrize("clearance,gap,failing", [(0.5, 0.25, True), (0.1, 0.25, False), (0.0, 0.001, False)])
@pytest.mark.parametrize("parameter", ["oog_clearance", "minimum_clearance_m"])
def test_oog_instance_override_precedes_catalogue(clearance, gap, failing, parameter):
    instance, box = _fixture()
    _oog_neighbour(instance, box, gap)
    instance.rule_overrides[OOG] = {parameter: clearance}
    for violations in _results(instance, box, neighbour=True):
        assert any(v.rule_id == OOG for v in violations) is failing


@pytest.mark.parametrize("rule,parameter", [(HEAT, "minimum_clearance"), (OOG, "oog_clearance")])
@pytest.mark.parametrize("value", [None, -0.1, True, float("inf")])
def test_invalid_explicit_override_does_not_fall_back(rule, parameter, value):
    instance, box = _fixture()
    if rule == HEAT:
        _heat_at_distance(instance, box, 4)
    else:
        box.oog = OutOfGauge(over_width_port_m=0.3)
    instance.rule_overrides[rule] = {parameter: value}
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(slot="0010102", container_id=box.id)])
    empty = ship.model_copy(update={"placements": []})
    for violations in (check_static_rules(instance, ship, call_index=0).violations,
                       placement_violations(instance, empty, box, "0010102", call_index=0)):
        assert any(v.rule_id == "checker.missing_vessel_data" and rule in v.message for v in violations)


def test_instance_override_precedes_port_heat_policy():
    instance, box = _fixture()
    _heat_at_distance(instance, box, 1.5)
    instance.port.port_rules = [PortRuleRef(rule_id=HEAT, params={"minimum_clearance": 4.0})]
    instance.rule_overrides[HEAT] = {"minimum_clearance": 1.0}
    for violations in _results(instance, box):
        assert not any(v.rule_id == HEAT for v in violations)
