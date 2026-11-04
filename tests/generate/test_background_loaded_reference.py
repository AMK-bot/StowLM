"""D64 rev3.1 Correction: medium/full seagoing witnesses carry their booking.

The empty 1700 medium is a light ship outside the IS Code A 2.3.5 weather
table (B/d > 3.5); no feeder sails with its decision region empty. The witness
therefore loads the slice's D48 booking reference as fixed ISO20 cargo.
"""
import pytest

from stowage.generate import background_acceptance as gate
from stowage.rules.catalogue import rule_freeze_v1


def _instance(ship, rung, seed=7):
    from stowage.generate.random_voyage import build_voyage_spec
    from stowage.model import Instance
    from stowage.model.port import PortCall, Rotation
    from stowage.model.ship import ShipState
    from stowage.model.yard import YardState
    from stowage.ports import load_port
    from stowage.vessel.fleet import load_vessel
    from stowage.vessel.fleet_conditions import departure_tank_masses
    from stowage.vessel.fleet_slices import recondition_slice, slice_preset

    parent = load_vessel(ship)
    # The D71 driver's medium booking mass (experiments/background-1028/d71-round2).
    vessel = recondition_slice(slice_preset(parent, rung), seed=seed,
        expected_load_mass_kg={"feeder_1700": 6_914_017.2082863245}[ship])
    port = load_port("laem_chabang_like")
    rotation = Rotation(service="loaded-reference", calls=[
        PortCall(locode=port.locode, archetype=port.archetype, index=i) for i in (0, 1)])
    return Instance(id=f"loaded.{ship}.{rung}.{seed}", stage="A", size_class=rung, seed=seed,
        vessel=vessel, port=port, rotation=rotation, call_index=0, containers=[], load_list=[],
        yard=YardState(yard_id=port.yard.id), onboard=ShipState(vessel_id=vessel.id,
            tank_masses_kg=departure_tank_masses(vessel)),
        voyage=build_voyage_spec(rotation, 0, port, seed=seed))


@pytest.fixture(scope="module")
def medium_1700():
    with rule_freeze_v1(enabled=True):
        return _instance("feeder_1700", "medium")


def test_loaded_reference_vessel_attributes_every_reference_cell(medium_1700):
    from stowage.vessel.fleet_conditions import loaded_reference_witness_vessel, slice_condition

    with rule_freeze_v1(enabled=True):
        base = gate.background_instance(medium_1700).vessel
        loaded = loaded_reference_witness_vessel(base)
    reference = slice_condition(base)["booking_reference"]
    rows = [row for row in reference["cells"] if row["mass_kg"] > 0]
    added = sum(load.mass_kg for load in loaded.fixed_loads) - sum(load.mass_kg for load in base.fixed_loads)
    assert added == pytest.approx(reference["mass_kg"], rel=1e-12)
    # D42: decision region becomes fixed support; the condition record names
    # every reference cell so the checker can rebuild the fixed loads exactly.
    assert not loaded.cells and {c.id for c in base.cells} <= {c.id for c in loaded.fixed_load_cells}
    attributed = {row["cell_id"] for row in slice_condition(loaded)["background_cells"]}
    assert {row["cell_id"] for row in rows} <= attributed
    # The emitted instance never carries it.
    assert medium_1700.vessel.fixed_loads == base.fixed_loads


def test_1700_medium_seagoing_witness_is_the_loaded_reference_and_passes(medium_1700):
    """Fail-first: the empty-plan witness failed weather (A 2.3.5) in all D57 states."""
    from stowage.vessel.fleet_berth import prepare_arrival

    with rule_freeze_v1(enabled=True):
        arrival = prepare_arrival(medium_1700)
        result = gate.evaluate_background(medium_1700, arrival_instance=arrival, use_cache=False)
    assert result.in_port["witness_kind"] == "loaded_booking_reference"
    assert result.status == "accepted", result.reasons
    assert set(result.states) == {"departure", "mid_voyage", "arrival"}
    assert result.states["arrival"]["b23_ratio"] >= 1.05
    # Weather criterion applicable (not failed closed) and passing in each state.
    assert not any("weather" in reason for reason in result.reasons)
