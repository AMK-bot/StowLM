"""D57's single fraction drains all consumable tanks without order excursions."""

import copy
import math

import pytest
from pydantic import ValidationError

import stowage.model as model
from stowage.model import voyage
from stowage.model.provenance import Provenance
from stowage.model.ship import ShipState
from stowage.model.vessel import Vessel


def _tank(tank_id, kind, tcg=0.0, lcg=25.0):
    return dict(id=tank_id, kind=kind, content_density_kg_m3=1.0, capacity_m3=1000.0,
                table=[dict(volume_m3=v, lcg_m=lcg, tcg_m=tcg, vcg_m=0.0, fsm_m4=0.0)
                       for v in (0.0, 1000.0)], x_aft_m=lcg - 1.0, x_fore_m=lcg + 1.0)


def _vessel():
    return Vessel.model_validate(dict(
        id="proportional", name="Synthetic proportional test", nominal_teu=0,
        cells=[], stacks=[], hatch_covers=[],
        tanks=[_tank("FP", "fuel_oil", -2.0), _tank("FS", "fuel_oil", 2.0),
               _tank("W", "fresh_water", lcg=40.0), _tank("B", "ballast"),
               _tank("S", "sludge")],
        voyage_policy=dict(groups=[dict(id="fuel", kind="fuel_oil", tank_ids=["FP", "FS"]),
                                   dict(id="water", kind="fresh_water", tank_ids=["W"])],
                           drawdown_order=["FP", "W", "FS"]),
    ))


def _spec(consumption=None, **changes):
    def identity(phase):
        return dict(id=f"test-{phase}", phase=phase, call_index=0 if phase == "departure" else 1,
                    locode="THLCH" if phase == "departure" else "SGSIN",
                    environment_id="synthetic-calm", density_id="saltwater", water_density_kg_m3=1025.0)
    return voyage.VoyageSpec.model_validate(dict(departure=identity("departure"),
                                                 arrival=identity("arrival"), consumption=consumption) | changes)


def _departure(masses=None):
    return ShipState(vessel_id="proportional", tank_masses_kg=(
        dict(FP=200.0, FS=200.0, W=100.0, B=80.0, S=30.0) if masses is None else masses))


def _sample(state, spec, r, ship=None):
    return voyage.derive_voyage_liquid_state(state, spec, r, vessel=ship or _vessel())


def _arrival_fraction(state, spec, ship=None):
    return voyage.arrival_remaining_fraction(state, spec, vessel=ship or _vessel())


def test_legacy_arrival_wrapper_preserves_symmetric_pair_instead_of_draining_one_side():
    result = voyage.derive_arrival_liquid_state(_departure(), _spec(), vessel=_vessel())
    assert result.tank_masses_kg == dict(FP=20.0, FS=20.0, W=10.0, B=80.0, S=30.0)


def test_new_functions_and_sample_type_are_public_model_exports():
    for name in ("derive_voyage_liquid_state", "arrival_remaining_fraction", "VoyageLiquidSample"):
        assert getattr(model, name) is getattr(voyage, name)


@pytest.mark.parametrize("r", [1, 0.55, 0.1])
def test_symmetric_tanks_stay_balanced_in_mass_and_transverse_moment(r):
    ship = _vessel()
    result = _sample(_departure(), _spec(), r, ship)
    masses = result.tank_masses_kg
    assert masses["FP"] == masses["FS"] == 200.0 * r
    assert sum(masses[t.id] * t.table[0].tcg_m for t in ship.tanks) == 0.0
    assert result.remaining_fraction == r
    assert not hasattr(result, "condition")  # No false arrival identity for intermediate samples.


def test_r_one_is_exact_departure_and_missing_empty_keys_stay_missing():
    state = _departure(dict(FP=0.3, FS=1000.0, B=80.0))
    assert _sample(state, _spec(), 1).tank_masses_kg == state.tank_masses_kg
    assert set(_sample(state, _spec(), 0).tank_masses_kg) == {"FP", "FS", "B"}


def test_default_arrival_fraction_and_wrapper_preserve_old_group_totals():
    state, spec, ship = _departure(), _spec(), _vessel()
    r = _arrival_fraction(state, spec, ship)
    assert r == 0.1
    sample = _sample(state, spec, r, ship)
    arrival = voyage.derive_arrival_liquid_state(state, spec, vessel=ship)
    assert arrival.condition == spec.arrival
    assert sample.tank_masses_kg == arrival.tank_masses_kg
    assert sum(sample.tank_masses_kg[t] for t in ("FP", "FS")) == 40.0
    assert sample.tank_masses_kg["W"] == 10.0


def test_all_tanks_are_monotone_and_capacities_hold_including_empty_endpoint():
    ship = _vessel()
    state = _departure(dict(FP=1000.0, FS=1000.0, W=1000.0, B=1000.0, S=1000.0))
    samples = [_sample(state, _spec(), r, ship).tank_masses_kg for r in (0, 0.1, 0.55, 1)]
    for tank in ship.tanks:
        values = [sample[tank.id] for sample in samples]
        assert values == sorted(values)
        assert all(0 <= mass <= tank.capacity_m3 * tank.content_density_kg_m3 for mass in values)
        if tank.kind in ("ballast", "sludge"):
            assert values == [1000.0] * 4
        else:
            assert values[0] == 0.0


@pytest.mark.parametrize("fuel,water,expected_r", [(100.0, 25.0, 0.75), (0.0, 0.0, 1.0),
                                                  (400.0, 100.0, 0.0)])
def test_explicit_consistent_consumption_produces_one_fraction(fuel, water, expected_r):
    spec = _spec([dict(group_id="fuel", mass_kg=fuel), dict(group_id="water", mass_kg=water)])
    r = _arrival_fraction(_departure(), spec)
    assert r == expected_r
    arrival = voyage.derive_arrival_liquid_state(_departure(), spec, vessel=_vessel())
    assert arrival.tank_masses_kg == _sample(_departure(), spec, r).tank_masses_kg
    assert arrival.tank_masses_kg["FP"] + arrival.tank_masses_kg["FS"] == 400.0 - fuel
    assert arrival.tank_masses_kg["W"] == 100.0 - water


@pytest.mark.parametrize("api", ["fraction", "sample", "wrapper"])
def test_inconsistent_group_consumption_fails_closed(api):
    spec = _spec([dict(group_id="fuel", mass_kg=100.0), dict(group_id="water", mass_kg=0.0)])
    with pytest.raises(ValueError, match="consumption.*(common|fraction)|common.*fraction"):
        _invoke(api, _departure(), spec, _vessel())


@pytest.mark.parametrize("api", ["fraction", "sample", "wrapper"])
def test_close_fractions_cannot_hide_inconsistent_retained_group_mass(api):
    ship = _vessel()
    water = next(tank for tank in ship.tanks if tank.id == "W")
    water.capacity_m3 = 1e12
    water.table[-1].volume_m3 = 1e12
    ship = Vessel.model_validate(ship.model_dump())
    state = _departure(dict(FP=200.0, FS=200.0, W=1e12, B=80.0, S=12.0))
    spec = _spec([dict(group_id="fuel", mass_kg=400.0 - 4e-11),
                  dict(group_id="water", mass_kg=1e12 - 0.01)])
    # Fractions differ by <1e-12, but a shared fraction would retain about
    # 0.1 kg of water instead of the explicitly declared 0.01 kg.
    with pytest.raises(ValueError, match="consumption.*common remaining fraction"):
        _invoke(api, state, spec, ship)


@pytest.mark.parametrize("masses,consumption,expected_r", [
    ({}, [dict(group_id="fuel", mass_kg=0.0), dict(group_id="water", mass_kg=0.0)], 1.0),
    (dict(FP=0.0, FS=0.0, W=0.0), [dict(group_id="fuel", mass_kg=0.0),
                                         dict(group_id="water", mass_kg=0.0)], 1.0),
    (dict(FP=200.0), [dict(group_id="fuel", mass_kg=100.0), dict(group_id="water", mass_kg=0.0)], 0.5),
])
def test_empty_groups_do_not_constrain_explicit_common_fraction(masses, consumption, expected_r):
    state, spec = _departure(masses), _spec(consumption)
    assert _arrival_fraction(state, spec) == expected_r
    assert set(_sample(state, spec, expected_r).tank_masses_kg) == set(masses)


def test_changing_valid_drawdown_order_does_not_change_fills():
    ship = _vessel()
    before = _sample(_departure(), _spec(), 0.55, ship).tank_masses_kg
    ship.voyage_policy.drawdown_order.reverse()
    assert _sample(_departure(), _spec(), 0.55, ship).tank_masses_kg == before


@pytest.mark.parametrize("r", [math.nan, math.inf, -math.inf, -0.01, 1.01, True, False, "0.5", None,
                              pytest.param(10**500, id="huge-integer")])
def test_fraction_is_strict_numeric_finite_and_within_unit_interval(r):
    with pytest.raises(ValueError, match="remaining_fraction|fraction|\\br\\b"):
        _sample(_departure(), _spec(), r)


def _invoke(api, state, spec, ship):
    if api == "fraction":
        return _arrival_fraction(state, spec, ship)
    if api == "sample":
        return _sample(state, spec, 1, ship)  # r=1 must still revalidate voyage inputs.
    return voyage.derive_arrival_liquid_state(state, spec, vessel=ship)


@pytest.mark.parametrize("api", ["fraction", "sample", "wrapper"])
@pytest.mark.parametrize("invalid", ["overcapacity", "negative", "nonfinite", "unknown_tank",
                                     "vessel_id", "order", "missing_policy", "overconsumption",
                                     "empty_group_consumption", "ballast", "identity", "tank_kind",
                                     "capacity", "density", "consumption_groups", "missing_tank"])
def test_every_entry_point_revalidates_even_at_departure_fraction(api, invalid):
    state, spec, ship = _departure(), _spec(), _vessel()
    if invalid in ("overcapacity", "negative", "nonfinite"):
        state.tank_masses_kg["FP"] = {"overcapacity": 1000.000001, "negative": -1.0,
                                       "nonfinite": math.nan}[invalid]
    elif invalid == "unknown_tank":
        state.tank_masses_kg["unknown"] = 0.0
    elif invalid == "vessel_id":
        state.vessel_id = "other"
    elif invalid == "order":
        ship.voyage_policy.drawdown_order = ["FP"]
    elif invalid == "missing_policy":
        ship.voyage_policy = None
    elif invalid == "overconsumption":
        spec = _spec([dict(group_id="fuel", mass_kg=400.000000001),
                      dict(group_id="water", mass_kg=100.0)])
    elif invalid == "empty_group_consumption":
        state = _departure({})
        spec = _spec([dict(group_id="fuel", mass_kg=0.0), dict(group_id="water", mass_kg=1.0)])
    elif invalid == "ballast":
        spec = _spec(ballast_operations=[dict(tank_id="B", mass_change_kg=0.0)])
    elif invalid == "identity":
        spec.arrival.phase = "departure"
    elif invalid == "tank_kind":
        next(tank for tank in ship.tanks if tank.id == "FP").kind = "ballast"
    elif invalid == "capacity":
        ship.tanks[0].capacity_m3 = math.inf
    elif invalid == "density":
        ship.tanks[0].content_density_kg_m3 = -1.0
    elif invalid == "consumption_groups":
        spec = _spec(consumption=[])
    elif invalid == "missing_tank":
        ship.tanks = ship.tanks[1:]
    with pytest.raises(ValueError):
        _invoke(api, state, spec, ship)


def test_samples_keep_nested_evidence_json_and_do_not_mutate_inputs():
    state, spec, ship = _departure(), _spec(), _vessel()
    ship.voyage_policy.groups[0].provenance = Provenance(stubs={"group": "synthetic"})
    ship.voyage_policy.drawdown_order_provenance = Provenance(stubs={"order": "synthetic"})
    ship.voyage_policy.arrival_rule_provenance = Provenance(stubs={"rule": "synthetic"})
    spec.arrival.provenance = Provenance(stubs={"arrival": "synthetic"})
    before = copy.deepcopy((state.model_dump(), spec.model_dump(), ship.model_dump()))
    result = _sample(state, spec, 0.55, ship)
    assert set(result.provenance.stubs) == {"group", "order", "rule", "arrival"}
    assert result.provenance.parents == sorted([spec.departure.id, spec.arrival.id])
    assert voyage.VoyageLiquidSample.model_validate_json(result.model_dump_json()) == result
    assert (state.model_dump(), spec.model_dump(), ship.model_dump()) == before
    result.tank_masses_kg["FP"] = 0.0
    assert state.tank_masses_kg["FP"] == 200.0


@pytest.mark.parametrize("value", [math.nan, math.inf, -0.1, 1.1, True, "0.5"])
def test_sample_fraction_contract_is_strict(value):
    with pytest.raises(ValidationError, match="remaining_fraction"):
        voyage.VoyageLiquidSample(remaining_fraction=value, tank_masses_kg={})
