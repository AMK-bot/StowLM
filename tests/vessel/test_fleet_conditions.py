"""Synthetic loading-manual backgrounds preserve whole-ship mass accounting.

These are departure-condition tests, not intact-stability certificates. Deck
loads carry occupied geometry; these tests do not certify securing or stability.
The C3 convention comes from experiments/physics-review/design/harness.py.
"""

import pytest

from stowage.model import FixedLoad, Vessel
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import list_fleet, load_ship_params, load_vessel
from stowage.vessel.fleet_conditions import departure_tank_masses, slice_condition
from stowage.vessel.fleet_slices import recondition_slice, slice_preset


@pytest.mark.parametrize("name", list_fleet())
@pytest.mark.parametrize("preset", ["tiny", "small", "medium"])
def test_geometry_slices_have_design_band_departure_condition(name, preset):
    parent = load_vessel(name, with_hull=False)
    original = parent.model_dump_json()
    vessel = slice_preset(parent, preset)
    condition = slice_condition(vessel)
    assert condition is not None
    assert condition["version"]
    assert isinstance(condition["seed"], int)
    assert condition["preset"] == preset
    assert condition["achieved_design_band"] is True
    design = condition["design_displacement_kg"]
    assert condition["target_displacement_kg"] == pytest.approx(.97 * design)
    assert .90 * design <= condition["expected_displacement_kg"] <= design
    # A larger region reserves its share of the full C3 condition; assigning
    # half-capacity demand would force excessive mass into the omitted bays.
    expected_load = (6 * 2350 if preset == "tiny" else
                     condition["unscaled_decision_c3_mass_kg"])
    assert condition["expected_load_mass_kg"] == pytest.approx(expected_load)
    assert isinstance(condition["expected_load_policy"], str)
    assert condition["expected_load_policy"]
    assert condition["expected_load_t_per_teu"] == pytest.approx(15.768)
    assert condition["cargo_t_per_teu"] > 0
    assert condition["fixed_cargo_mass_kg"] > 0
    assert condition["fixed_load_mass_kg"] == pytest.approx(sum(m for *_, m in vessel.fixed_loads))
    params = load_ship_params(name)
    assert condition["expected_displacement_kg"] == pytest.approx(.97 * design)
    # The production upright free-trim solve independently checks that the
    # mass target actually gives a near-design mean draught on this hull.
    # Trim, list and liquid free surfaces still need the finisher/checker.
    screen = condition["gm_screen"]
    assert .94 * params["hull"]["design_draught_m"] <= screen["screen_mean_draught_m"]
    assert screen["screen_mean_draught_m"] <= params["hull"]["design_draught_m"]
    departure = (params["hull"]["lightship_mass_kg"]
                 + condition["fixed_load_mass_kg"]
                 + condition["consumables_mass_kg"] + expected_load)
    assert condition["expected_displacement_kg"] == pytest.approx(departure)
    assert any("APPROXIMATION:" in source for source in vessel.sources)
    assert any("C3" in source for source in vessel.sources)
    assert parent.model_dump_json() == original
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel
    assert slice_preset(parent, preset).model_dump_json() == vessel.model_dump_json()


def test_departure_consumables_use_review_fraction_and_leave_ballast_adjustable():
    vessel = build_instance().vessel
    original = vessel.model_dump_json()
    masses = departure_tank_masses(vessel)
    assert set(masses) == {tank.id for tank in vessel.tanks}
    assert any(not tank.adjustable for tank in vessel.tanks)
    assert any(tank.adjustable for tank in vessel.tanks)
    for tank in vessel.tanks:
        expected = 0 if tank.adjustable else .97 * tank.capacity_m3 * tank.content_density_kg_m3
        assert masses[tank.id] == pytest.approx(expected)
    assert vessel.model_dump_json() == original


@pytest.mark.parametrize("name", list_fleet())
def test_physics_slice_departure_mass_fits_hull_and_consumables_are_counted_once(name):
    parent = load_vessel(name)
    original = parent.model_dump_json()
    for preset in ("tiny", "small", "medium"):
        vessel = slice_preset(parent, preset)
        condition = slice_condition(vessel)
        masses = departure_tank_masses(vessel)
        assert vessel.hull == parent.hull
        assert vessel.tanks == parent.tanks
        assert condition["consumables_mass_kg"] == pytest.approx(sum(masses.values()))
        base_mass = sum(FixedLoad.model_validate(load).mass_kg for load in condition["base_fixed_loads"])
        assert sum(load[3] for load in vessel.fixed_loads) == pytest.approx(
            base_mass + condition["fixed_cargo_mass_kg"])
        departure = (vessel.hull.lightship_mass_kg
                     + sum(load[3] for load in vessel.fixed_loads)
                     + sum(masses.values()) + condition["expected_load_mass_kg"])
        assert departure == pytest.approx(condition["expected_displacement_kg"])
        assert .90 * condition["design_displacement_kg"] <= departure <= condition["design_displacement_kg"]
        rows = vessel.hull.hydrostatics
        assert rows[0].displacement_kg <= departure <= rows[-1].displacement_kg
        # Fixed point masses are deliberately outside the box/stack rule system.
        assert all(len(load) == 4 and load[3] >= 0 for load in vessel.fixed_loads)
        assert len(vessel.cells) < len(parent.cells)
    assert parent.model_dump_json() == original


def test_reconditioning_known_booking_replaces_background_without_duplication():
    parent = load_vessel("feeder_500", with_hull=False)
    tiny = slice_preset(parent, "tiny")
    # Presets have a generic C3 reserve; actual bookings additionally reserve
    # ballast displacement and balance the centroid cargo's three moments.
    tiny = recondition_slice(tiny, expected_load_mass_kg=slice_condition(tiny)["expected_load_mass_kg"])
    original = tiny.model_dump_json()
    before = slice_condition(tiny)
    known_mass = before["expected_load_mass_kg"] + 15000
    changed = recondition_slice(tiny, expected_load_mass_kg=known_mass)
    after = slice_condition(changed)
    assert after["expected_load_mass_kg"] == pytest.approx(known_mass)
    # Physical capacity/GM bounds may select a different departure mass as the
    # booking changes. The mass accounting must still close without duplicates.
    change_in_departure = after["expected_displacement_kg"]-before["expected_displacement_kg"]
    assert after["fixed_load_mass_kg"] == pytest.approx(
        before["fixed_load_mass_kg"] + change_in_departure - 15000)
    assert changed.tanks == tiny.tanks
    assert changed.cells == tiny.cells
    assert changed.hatch_covers == tiny.hatch_covers
    assert tiny.model_dump_json() == original
    assert recondition_slice(changed, expected_load_mass_kg=known_mass) == changed
    assert recondition_slice(tiny, expected_load_mass_kg=known_mass) == changed
    assert Vessel.model_validate_json(changed.model_dump_json()) == changed
    # The canonical selector can start from a full parent and keep its id and
    # slice_of=None. Reconditioning must still recover all omitted ship cells.
    from stowage.generate.instances_v2_random import select_tiny_region
    selected = select_tiny_region(parent)
    assert selected.slice_of is None and len(selected.cells) == 6
    conditioned = recondition_slice(selected, expected_load_mass_kg=6*2350, seed=7)
    assert conditioned.cells == selected.cells
    assert slice_condition(conditioned)["achieved_design_band"]
    assert slice_condition(conditioned)["expected_load_mass_kg"] == 6*2350
    assert conditioned.fixed_loads


def test_full_vessel_does_not_get_a_synthetic_background():
    parent = load_vessel("feeder_500", with_hull=False)
    full = slice_preset(parent, "full")
    assert slice_condition(full) is None
    assert full.fixed_loads == parent.fixed_loads


@pytest.mark.parametrize("name", list_fleet())
@pytest.mark.parametrize("preset", ["tiny", "small", "medium"])
def test_default_background_respects_each_original_stack_capacity(name, preset):
    """Reconstruct original supports from real coordinates, not aggregate mass."""
    parent = load_vessel(name, with_hull=False)
    vessel = slice_preset(parent, preset)
    cells = {cell.id: cell for cell in parent.cells}
    reconstructed = {stack.id: 0. for stack in parent.stacks}
    for load in vessel.fixed_loads:
        if load.kind != "cargo":
            continue
        candidates = [stack for stack in parent.stacks if stack.cell_ids
                      and abs(cells[stack.cell_ids[0]].lcg_m-load.x_m) < 1e-7
                      and abs(cells[stack.cell_ids[0]].tcg_m-load.y_m) < 1e-7
                      and stack.on_deck == (load.silhouette is not None)]
        assert len(candidates) == 1
        reconstructed[candidates[0].id] += load.mass_kg
    for stack in parent.stacks:
        assert reconstructed[stack.id] <= (stack.max_mass_20_kg or stack.max_mass_kg)+1e-6, stack.id
    audit = slice_condition(vessel)
    assert sum(item["mass_kg"] for item in audit["background_cells"]) == pytest.approx(
        audit["fixed_cargo_mass_kg"])
    for item in audit["background_stacks"]:
        stack = next(stack for stack in parent.stacks if stack.id == item["stack_id"])
        assert item["mass_kg"] == pytest.approx(reconstructed[stack.id])
        assert item["max_mass_20_kg"] == (stack.max_mass_20_kg or stack.max_mass_kg)
        emitted = {cell["cell_id"]: cell["mass_kg"] for cell in audit["background_cells"]
                   if cell["stack_id"] == stack.id}
        ordered = [emitted.get(cid, 0.) for cid in stack.cell_ids]
        assert all(mass <= item["max_cell_mass_kg"]+1e-6 for mass in ordered)
        assert all(upper <= lower+1e-6 for lower, upper in zip(ordered, ordered[1:]))
        actual = [cid for cid in stack.cell_ids if cid in emitted]
        assert actual == stack.cell_ids[:len(actual)]
        from stowage.conventions import CONTAINER_HEIGHT_STD_M
        assert len(actual)*CONTAINER_HEIGHT_STD_M <= stack.max_height_m+1e-9
        if stack.on_deck:
            active_covers = {selected.hatch_cover_id for selected in vessel.stacks}
            assert stack.hatch_cover_id not in active_covers


def test_background_capacity_projection_caps_cells_and_fails_closed_if_impossible():
    from stowage.vessel.fleet_balance import bound_background_mass

    parent = load_vessel("feeder_500", with_hull=False)
    stack = next(stack for stack in parent.stacks if not stack.on_deck and len(stack.cell_ids) == 3)
    excluded = {item.id for item in parent.stacks if item.id != stack.id}
    output = bound_background_mass(parent, [(stack.cell_ids[0], 60000.)], stack_ids=excluded)
    masses = dict(output)
    ordered = [masses.get(cid, 0.) for cid in stack.cell_ids]
    assert sum(ordered) == pytest.approx(60000.)
    assert all(mass <= 30480.+1e-6 for mass in ordered)
    assert all(upper <= lower+1e-6 for lower, upper in zip(ordered, ordered[1:]))
    with pytest.raises(ValueError, match="exceeds physical outside stack capacities"):
        bound_background_mass(parent, [(stack.cell_ids[0], 1e12)], stack_ids=excluded)
