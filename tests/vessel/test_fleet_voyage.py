"""D57 proportional voyage liquids preserve real fleet/slice mass and geometry."""

from math import fsum

import pytest

from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model import (
    ShipState, Vessel, VoyageConditionIdentity, VoyageConsumption,
    VoyageLiquidState, VoyageLiquidSample, VoyageSpec, arrival_remaining_fraction,
    derive_arrival_liquid_state, derive_voyage_liquid_state,
)
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import departure_tank_masses
from stowage.vessel.fleet_slices import recondition_slice, slice_preset, slice_vessel


def _leg(vessel, consumption=None):
    def identity(phase, call_index, locode):
        return VoyageConditionIdentity(
            id=f"{vessel.id}.leg0.{phase}", phase=phase, call_index=call_index,
            locode=locode, environment_id=f"synthetic-leg0-{phase}-environment",
            density_id=f"synthetic-leg0-{phase}-seawater-1025",
            water_density_kg_m3=1025.0,
        )
    return VoyageSpec(departure=identity("departure", 0, "THLCH"),
                      arrival=identity("arrival", 1, "SGSIN"), consumption=consumption)


def _departure(vessel, *, varied=False):
    masses = departure_tank_masses(vessel)
    for index, tank in enumerate(vessel.tanks):
        capacity = tank.capacity_m3*tank.content_density_kg_m3
        if tank.adjustable:
            masses[tank.id] = (0.20+0.05*(index % 7))*capacity
        elif varied:
            masses[tank.id] = (0.15+0.10*(index % 6))*capacity
    return ShipState(vessel_id=vessel.id, tank_masses_kg=masses, placements=[dict(
        slot=vessel.stacks[0].cell_ids[0],
        container_id=make_container_id(SYNTHETIC_OWNER_CODES[0], 1),
    )])


def _assert_arrival(vessel, departure, spec, *, expected_fraction=.1):
    vessel_before, departure_before, spec_before = (
        vessel.model_dump_json(), departure.model_dump_json(), spec.model_dump_json())
    arrival = derive_arrival_liquid_state(departure, spec, vessel=vessel)
    assert arrival_remaining_fraction(departure, spec, vessel=vessel) == pytest.approx(expected_fraction)
    assert vessel.model_dump_json() == vessel_before
    assert departure.model_dump_json() == departure_before
    assert spec.model_dump_json() == spec_before
    assert arrival.condition == spec.arrival
    assert VoyageLiquidState.model_validate_json(arrival.model_dump_json()) == arrival
    assert set(arrival.tank_masses_kg) == set(departure.tank_masses_kg)
    policy = vessel.voyage_policy
    declared = None if spec.consumption is None else {item.group_id: item.mass_kg for item in spec.consumption}
    grouped = set()
    consumed_total = 0.0
    for group in policy.groups:
        grouped.update(group.tank_ids)
        ordered = [tank_id for tank_id in policy.drawdown_order if tank_id in group.tank_ids]
        total = fsum(departure.tank_masses_kg[tank_id] for tank_id in ordered)
        consumed = .9*total if declared is None else declared[group.id]
        consumed_total += consumed
        assert fsum(arrival.tank_masses_kg[tank_id] for tank_id in ordered) == pytest.approx(
            total-consumed, rel=1e-12, abs=1e-6)
        # D57 uses one common fraction for every tank, irrespective of retained
        # legacy drawdown_order metadata. Group totals alone cannot detect the
        # old one-tank-at-a-time draining of port/starboard pairs.
        for tank_id in ordered:
            before = departure.tank_masses_kg[tank_id]
            assert arrival.tank_masses_kg[tank_id] == pytest.approx(before*expected_fraction, abs=1e-6)
            assert 0 <= arrival.tank_masses_kg[tank_id] <= before
    for tank in vessel.tanks:
        assert 0 <= arrival.tank_masses_kg[tank.id] <= tank.capacity_m3*tank.content_density_kg_m3
        if tank.id not in grouped:
            assert arrival.tank_masses_kg[tank.id] == departure.tank_masses_kg[tank.id]
    assert fsum(departure.tank_masses_kg.values())-fsum(arrival.tank_masses_kg.values()) == pytest.approx(
        consumed_total, abs=1e-6)
    if declared is None:
        assert consumed_total == pytest.approx(.9*fsum(departure.tank_masses_kg[tank_id] for tank_id in grouped))
        assert all(arrival.tank_masses_kg[tank_id] > 0
                   for tank_id in grouped if departure.tank_masses_kg[tank_id] > 0)
    # Exercise the actual D57 departure/midpoint/arrival fractions, including
    # r=1/.55/.1 for the default leg, without relabelling a midpoint as arrival.
    for fraction in (1., (1.+expected_fraction)/2, expected_fraction):
        sample = derive_voyage_liquid_state(departure, spec, fraction, vessel=vessel)
        assert sample.remaining_fraction == fraction
        assert VoyageLiquidSample.model_validate_json(sample.model_dump_json()) == sample
        assert set(sample.tank_masses_kg) == set(departure.tank_masses_kg)
        for tank_id, before in departure.tank_masses_kg.items():
            expected = before*fraction if tank_id in grouped else before
            assert sample.tank_masses_kg[tank_id] == expected
            assert 0 <= sample.tank_masses_kg[tank_id] <= before
        if fraction == 1.:
            assert sample.tank_masses_kg == departure.tank_masses_kg
        if fraction == expected_fraction:
            assert sample.tank_masses_kg == arrival.tank_masses_kg
        endpoint = departure.model_copy(update={"tank_masses_kg": sample.tank_masses_kg})
        assert endpoint.placements == departure.placements
        assert endpoint.covers_off == departure.covers_off
    assert vessel.model_dump_json() == vessel_before
    assert departure.model_dump_json() == departure_before
    assert spec.model_dump_json() == spec_before
    # Applying this liquids-only endpoint to a departure copy preserves its
    # actual cargo and the vessel's fixed cargo, tanks and capacities.
    endpoint = departure.model_copy(update={"tank_masses_kg": arrival.tank_masses_kg})
    assert endpoint.placements == departure.placements
    assert endpoint.covers_off == departure.covers_off
    assert ShipState.model_validate_json(endpoint.model_dump_json()) == endpoint


@pytest.mark.parametrize("name,booking_mass", [
    ("feeder_500", 1917238.3810685035),
    ("feeder_800", 3734513.4862812967),
    ("feeder_1700", 6914017.2082863245),
])
def test_real_fleet_voyage_policy_survives_regions_and_derives_group_arrival(name, booking_mass):
    parent = load_vessel(name)
    assert parent.hull is not None
    policy = parent.voyage_policy
    assert policy is not None, "D50 production vessel must declare its consumable groups and draw-down order"
    actual = {tank.id: tank for tank in parent.tanks
              if tank.kind in {"fuel_oil", "diesel_oil", "lube_oil", "fresh_water", "stores"}}
    assert {group.kind for group in policy.groups} == {"fuel_oil", "diesel_oil", "fresh_water"}
    assert {tank_id for group in policy.groups for tank_id in group.tank_ids} == actual.keys()
    assert policy.default_arrival_remaining_fraction == .1
    assert policy.drawdown_order == sorted(actual, key=lambda tank_id: (-actual[tank_id].table[-1].lcg_m, tank_id))
    assert policy.provenance.parents
    assert policy.drawdown_order_provenance.parents
    assert policy.arrival_rule_provenance.parents
    for group in policy.groups:
        assert group.provenance.parents
        assert set(group.tank_ids) == {tank_id for tank_id, tank in actual.items() if tank.kind == group.kind}
        assert all(actual[tank_id].kind != "ballast" for tank_id in group.tank_ids)

    presets = [slice_preset(parent, size) for size in ("tiny", "small", "medium", "full")]
    custom = slice_vessel(parent, [1, 3], name=f"{name}.voyage-custom")
    nested = slice_vessel(Vessel.model_validate_json(custom.model_dump_json()), [1], name=f"{name}.voyage-nested")
    conditioned = recondition_slice(presets[2], expected_load_mass_kg=booking_mass, seed=1)
    for vessel in [parent, *presets, custom, nested, conditioned]:
        assert vessel.voyage_policy == policy
        assert vessel.tanks == parent.tanks
        assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel
        departure = _departure(vessel)
        assert any(tank.adjustable and 0 < departure.tank_masses_kg[tank.id]
                   <= .5*tank.capacity_m3*tank.content_density_kg_m3 for tank in vessel.tanks)
        _assert_arrival(vessel, departure, _leg(vessel))

    varied = _departure(conditioned, varied=True)
    for fraction in (1., .65, 0.):
        consumption = [VoyageConsumption(group_id=group.id, mass_kg=(1.-fraction)
                       *fsum(varied.tank_masses_kg[tank_id] for tank_id in group.tank_ids))
                       for group in policy.groups]
        _assert_arrival(conditioned, varied, _leg(conditioned, consumption), expected_fraction=fraction)

    # The former accepted mixed group depletion 0/.35/1 has no common r.
    consumption = [VoyageConsumption(group_id=group.id, mass_kg=(0., .35, 1.)[index % 3]
                   *fsum(varied.tank_masses_kg[tank_id] for tank_id in group.tank_ids))
                   for index, group in enumerate(policy.groups)]
    inconsistent = _leg(conditioned, consumption)
    before = conditioned.model_dump_json(), varied.model_dump_json(), inconsistent.model_dump_json()
    for derive in (
        lambda: arrival_remaining_fraction(varied, inconsistent, vessel=conditioned),
        lambda: derive_arrival_liquid_state(varied, inconsistent, vessel=conditioned),
        lambda: derive_voyage_liquid_state(varied, inconsistent, 1., vessel=conditioned),
    ):
        with pytest.raises(ValueError, match='consumption.*common remaining fraction'):
            derive()
    assert (conditioned.model_dump_json(), varied.model_dump_json(), inconsistent.model_dump_json()) == before
