"""D50 derives immutable arrival liquids from declared consumable policy."""

import copy
import math

import pytest
from pydantic import ValidationError

from stowage.model.provenance import Provenance
from stowage.model.ship import ShipState
from stowage.model.vessel import Vessel
from stowage.model.voyage import (
    ConsumableTankGroup, VoyageBallastOperation, VoyageConditionIdentity,
    VoyageConsumption, VoyageLiquidState, VoyageSpec, VoyageTankPolicy,
    derive_arrival_liquid_state, validate_voyage_policy, validate_voyage_spec,
)


def identity(phase="departure", **changes):
    return VoyageConditionIdentity.model_validate(dict(
        id=f"leg-0-{phase}", phase=phase, call_index=0 if phase == "departure" else 1,
        locode="THLCH" if phase == "departure" else "SGSIN", environment_id="calm",
        density_id="saltwater", water_density_kg_m3=1025.0,
    ) | changes)


def spec(**changes):
    return VoyageSpec(departure=identity(), arrival=identity("arrival"), **changes)


def tank(tank_id, kind="fuel_oil", **changes):
    return dict(id=tank_id, kind=kind, content_density_kg_m3=1.0, capacity_m3=1000.0,
                table=[dict(volume_m3=v, lcg_m=0.0, tcg_m=0.0, vcg_m=0.0, fsm_m4=0.0)
                       for v in (0.0, 1000.0)], x_aft_m=0.0, x_fore_m=1.0) | changes


def policy(**changes):
    return VoyageTankPolicy.model_validate(dict(
        groups=[dict(id="fuel", kind="fuel_oil", tank_ids=["F1", "F2"]),
                dict(id="water", kind="fresh_water", tank_ids=["W1"])],
        drawdown_order=["F2", "W1", "F1"],
    ) | changes)


def vessel(**changes):
    return Vessel.model_validate(dict(id="test", name="Test", nominal_teu=0, cells=[], stacks=[],
        hatch_covers=[], tanks=[tank("F1"), tank("F2"), tank("W1", "fresh_water"),
                                tank("B1", "ballast")], voyage_policy=policy()) | changes)


def departure(**masses):
    return ShipState(vessel_id="test", tank_masses_kg=masses or dict(F1=100.0, F2=300.0, W1=50.0, B1=80.0))


def test_default_arrival_draws_every_tank_proportionally_to_ten_percent():
    original = departure()
    before = original.model_dump()
    result = derive_arrival_liquid_state(original, spec(), vessel=vessel())
    assert result.tank_masses_kg == {"F1": 10.0, "F2": 30.0, "W1": 5.0, "B1": 80.0}
    assert result.condition == identity("arrival")
    assert original.model_dump() == before


def test_explicit_consumption_with_different_group_fractions_is_rejected():
    voyage = spec(consumption=[VoyageConsumption(group_id="fuel", mass_kg=125.0),
                               VoyageConsumption(group_id="water", mass_kg=0.0)])
    with pytest.raises(ValueError, match="consumption.*common remaining fraction"):
        derive_arrival_liquid_state(departure(), voyage, vessel=vessel())


def test_missing_tanks_mean_empty_and_stay_missing():
    result = derive_arrival_liquid_state(departure(F1=100.0), spec(), vessel=vessel())
    assert result.tank_masses_kg == {"F1": 10.0}


def test_empty_departure_has_empty_arrival():
    result = derive_arrival_liquid_state(ShipState(vessel_id="test"), spec(), vessel=vessel())
    assert result.tank_masses_kg == {}


def test_fully_consumed_group_arrives_empty():
    result = derive_arrival_liquid_state(departure(), spec(consumption=[
        VoyageConsumption(group_id="fuel", mass_kg=400.0),
        VoyageConsumption(group_id="water", mass_kg=50.0),
    ]), vessel=vessel())
    assert result.tank_masses_kg == {"F1": 0.0, "F2": 0.0, "W1": 0.0, "B1": 80.0}


@pytest.mark.parametrize("consumption", [[], [dict(group_id="fuel", mass_kg=1.0)],
    [dict(group_id="fuel", mass_kg=1.0), dict(group_id="water", mass_kg=1.0),
     dict(group_id="unknown", mass_kg=1.0)]])
def test_declared_consumption_requires_every_group_exactly_once(consumption):
    with pytest.raises(ValueError, match="consumption"):
        derive_arrival_liquid_state(departure(), spec(consumption=consumption), vessel=vessel())


def test_overconsumption_is_rejected_even_below_float_tolerance():
    voyage = spec(consumption=[dict(group_id="fuel", mass_kg=400.0 + 1e-9),
                               dict(group_id="water", mass_kg=0.0)])
    with pytest.raises(ValueError, match="consumption"):
        derive_arrival_liquid_state(departure(), voyage, vessel=vessel())


@pytest.mark.parametrize("mass", [-1.0, math.inf, -math.inf, math.nan, True, "20", 1000.000000001])
def test_derivation_revalidates_mutated_departure_mass(mass):
    state = departure()
    state.tank_masses_kg["F1"] = mass
    with pytest.raises(ValueError, match="tank_masses_kg"):
        derive_arrival_liquid_state(state, spec(), vessel=vessel())


def test_unknown_tank_and_wrong_vessel_fail_closed():
    with pytest.raises(ValueError, match="tank_masses_kg"):
        derive_arrival_liquid_state(departure(unknown=1.0), spec(), vessel=vessel())
    state = departure()
    state.vessel_id = "different"
    with pytest.raises(ValueError, match="vessel_id"):
        derive_arrival_liquid_state(state, spec(), vessel=vessel())


def test_voyage_ballast_is_representable_but_derivation_rejects_even_zero():
    voyage = spec(ballast_operations=[VoyageBallastOperation(tank_id="B1", mass_change_kg=0.0)])
    assert VoyageSpec.model_validate_json(voyage.model_dump_json()) == voyage
    with pytest.raises(ValueError, match="ballast_operations"):
        derive_arrival_liquid_state(departure(), voyage, vessel=vessel())


def test_derivation_revalidates_mutated_spec_and_policy():
    voyage = spec()
    voyage.arrival.phase = "departure"
    with pytest.raises(ValueError, match="arrival"):
        derive_arrival_liquid_state(departure(), voyage, vessel=vessel())
    ship = vessel()
    ship.voyage_policy.drawdown_order = ["F1"]
    with pytest.raises(ValueError, match="drawdown_order"):
        derive_arrival_liquid_state(departure(), spec(), vessel=ship)


def test_output_retains_all_nested_evidence_taint_and_does_not_mutate_inputs():
    ship = vessel()
    ship.voyage_policy.groups[0].provenance = Provenance(stubs={"fuel_group": "synthetic"})
    ship.voyage_policy.drawdown_order_provenance = Provenance(stubs={"order": "unverified"})
    ship.voyage_policy.arrival_rule_provenance = Provenance(stubs={"rule": "test"})
    voyage = spec()
    voyage.arrival.provenance = Provenance(stubs={"environment": "synthetic"})
    before = copy.deepcopy((ship.model_dump(), voyage.model_dump()))
    result = derive_arrival_liquid_state(departure(), voyage, vessel=ship)
    assert set(result.provenance.stubs) == {"fuel_group", "order", "rule", "environment"}
    assert (ship.model_dump(), voyage.model_dump()) == before
    assert VoyageLiquidState.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("value", [math.inf, math.nan, -1.0, True, "10"])
def test_output_liquid_state_mass_is_strict_finite_nonnegative(value):
    with pytest.raises(ValidationError, match="tank_masses_kg"):
        VoyageLiquidState(condition=identity("arrival"), tank_masses_kg={"F1": value})


def test_vessel_policy_must_exist_for_derivation():
    ship = vessel(voyage_policy=None)
    validate_voyage_policy(ship)
    with pytest.raises(ValueError, match="voyage_policy"):
        validate_voyage_spec(ship, spec())


@pytest.mark.parametrize("call_index", [True, 0.0, "0"])
def test_condition_call_index_is_a_strict_integer(call_index):
    with pytest.raises(ValidationError, match="call_index"):
        identity(call_index=call_index)


@pytest.mark.parametrize("field,value", [
    ("capacity_m3", math.inf), ("capacity_m3", -1.0),
    ("content_density_kg_m3", math.nan), ("content_density_kg_m3", True),
])
def test_mutated_consumable_capacity_or_density_fails_closed(field, value):
    ship = vessel()
    setattr(next(t for t in ship.tanks if t.id == "F1"), field, value)
    with pytest.raises(ValueError, match=field):
        derive_arrival_liquid_state(departure(), spec(), vessel=ship)


def test_finite_capacity_density_product_overflow_fails_closed():
    ship = vessel()
    fuel = next(t for t in ship.tanks if t.id == "F1")
    fuel.capacity_m3 = 1e308
    fuel.content_density_kg_m3 = 1e308
    with pytest.raises(ValueError, match="capacity"):
        derive_arrival_liquid_state(departure(), spec(), vessel=ship)


def test_group_mass_overflow_fails_closed():
    ship = vessel()
    for fuel in ship.tanks:
        if fuel.kind == "fuel_oil":
            fuel.capacity_m3 = 1e308
    with pytest.raises(ValueError, match="tank_masses_kg"):
        derive_arrival_liquid_state(departure(F1=1e308, F2=1e308), spec(), vessel=ship)


def test_policy_and_spec_json_preserve_declared_order_and_explicit_zero():
    policy_value = policy()
    assert VoyageTankPolicy.model_validate_json(policy_value.model_dump_json()) == policy_value
    assert policy_value.drawdown_order == ["F2", "W1", "F1"]
    voyage = spec(consumption=[dict(group_id="fuel", mass_kg=0.0),
                               dict(group_id="water", mass_kg=0.0)])
    restored = VoyageSpec.model_validate_json(voyage.model_dump_json())
    assert restored == voyage
    assert all(item.mass_kg == 0.0 for item in restored.consumption)
