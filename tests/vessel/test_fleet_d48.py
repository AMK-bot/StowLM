"""D48 reference bookings bound construction moments; they never add cargo."""

from math import fsum, isfinite

import numpy as np
import pytest

from stowage.conventions import CONTAINER_HEIGHT_STD_M
from stowage.model import Vessel
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_conditions import departure_tank_masses, slice_condition
from stowage.vessel.fleet_slices import recondition_slice, slice_preset
from stowage.vessel.hydrostatics import HullGeometry


@pytest.mark.parametrize("name,booking_mass", [
    ("feeder_500", 1917238.3810685035),
    ("feeder_800", 3734513.4862812967),
    ("feeder_1700", 6914017.2082863245),
])
def test_d48_real_hull_medium_booking_reference_balances_without_adding_cargo(name, booking_mass):
    parent = load_vessel(name)
    selected = slice_preset(parent, "medium")
    vessel = recondition_slice(selected, expected_load_mass_kg=booking_mass, seed=1)
    assert vessel.hull is not None
    condition = slice_condition(vessel)
    reference = condition["booking_reference"]
    assert reference["method"] == "bounded_c3_heavy_low_iso20_v1"
    assert reference["reference_seed"] == 29
    assert condition["seed"] == 1
    assert reference["mass_kg"] == pytest.approx(booking_mass, rel=0, abs=1e-6)
    assert reference["capacity_kg"] >= booking_mass-1e-6
    assert reference["cells"]
    cells = {cell.id: cell for cell in selected.cells}
    stacks = {stack.id: stack for stack in selected.stacks}
    allocated = {row["cell_id"]: row["mass_kg"] for row in reference["cells"]}
    assert len(allocated) == len(reference["cells"])
    assert set(allocated) <= cells.keys()
    assert all(isfinite(mass) and mass > 0 for mass in allocated.values())
    assert fsum(allocated.values()) == pytest.approx(booking_mass, rel=0, abs=1e-5)
    positions = {}
    maximum_capacity = 0.0
    for stack in stacks.values():
        mass_limit = 24999.0 if stack.on_deck else 30480.0
        prefix = [cid for cid in stack.cell_ids if cid in allocated]
        assert prefix == stack.cell_ids[:len(prefix)]
        assert len(prefix)*CONTAINER_HEIGHT_STD_M <= stack.max_height_m+1e-9
        ordered = [allocated.get(cid, 0.0) for cid in stack.cell_ids]
        assert all(upper <= lower+1e-6 for lower, upper in zip(ordered, ordered[1:]))
        assert all(mass <= mass_limit+1e-6 for mass in ordered)
        cap = stack.max_mass_20_kg or stack.max_mass_kg
        assert fsum(ordered) <= cap+1e-6
        eligible_count = min(len(stack.cell_ids), int((stack.max_height_m+1e-9)/CONTAINER_HEIGHT_STD_M))
        maximum_capacity += min(cap, eligible_count*mass_limit)
        for index, cid in enumerate(prefix):
            assert cells[cid].accepts_20
            positions[cid] = (cells[cid].lcg_m, cells[cid].tcg_m,
                              stack.base_z_m+(index+.5)*CONTAINER_HEIGHT_STD_M)
    assert reference["capacity_kg"] <= maximum_capacity+1e-6
    reference_cg = np.array([fsum(allocated[cid]*positions[cid][axis] for cid in allocated)/booking_mass
                             for axis in range(3)])
    assert reference["cg_m"] == pytest.approx(reference_cg, rel=0, abs=1e-6)
    geometric_z = fsum(cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2 for cell in cells.values())/len(cells)
    assert reference_cg[2] < geometric_z

    # The reference describes a prospective booking inside decision cells. Only
    # the real outside D42 background belongs to fixed cargo/support catalogs.
    fixed_cells = {cell.id for cell in vessel.fixed_load_cells}
    boxes = [box for load in vessel.fixed_loads for box in load.cargo_boxes or []]
    assert not (set(allocated) & fixed_cells)
    assert not (set(allocated) & {cid for box in boxes for cid in box.cell_ids})
    assert fsum(box.gross_mass_kg for box in boxes) == pytest.approx(condition["fixed_cargo_mass_kg"], abs=1e-5)
    assert fsum(load.mass_kg for load in vessel.fixed_loads) == pytest.approx(condition["fixed_load_mass_kg"], abs=1e-5)
    assert vessel.cells == selected.cells and vessel.stacks == selected.stacks
    assert vessel.hull == parent.hull and vessel.tanks == parent.tanks
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel

    # Recompute mass and all moments from original parameters, live liquid
    # soundings and actual background aggregates. The prospective booking is
    # absent from the D58 screen; separately confirm its deadweight reserve.
    params = load_ship_params(name)
    h = params["hull"]
    points = [(h["lightship_lcg_m"], h.get("lightship_tcg_m", 0.0),
               h["lightship_vcg_m"], h["lightship_mass_kg"])]
    points.extend(tuple(load) for load in vessel.fixed_loads)
    tank_masses = departure_tank_masses(vessel)
    for tank in vessel.tanks:
        mass = tank_masses[tank.id]
        if mass == 0:
            continue
        volume = mass/tank.content_density_kg_m3
        centre = [float(np.interp(volume, [row.volume_m3 for row in tank.table],
                                 [getattr(row, key) for row in tank.table]))
                  for key in ("lcg_m", "tcg_m", "vcg_m")]
        points.append((*centre, mass))
    departure_mass = fsum(point[3] for point in points)
    assert departure_mass+booking_mass == pytest.approx(condition["expected_displacement_kg"], rel=1e-10)
    assert condition["expected_load_mass_kg"] == booking_mass
    # D58's empty-plan witness may use more water than this recipe. The full
    # D36/D48 booking still retains a real, capacity-bounded ballast sizing
    # below D40; its reserved deadweight cannot be spent on that witness.
    assert departure_mass+booking_mass <= vessel.hull.max_displacement_kg+1e-5
    assert departure_mass+booking_mass <= condition["design_displacement_kg"]+1e-5
    assert fsum(tank_masses[tank.id] for tank in vessel.tanks if tank.adjustable) == pytest.approx(
        condition["stability_ballast_mass_kg"])
    for tank in vessel.tanks:
        assert 0 <= tank_masses[tank.id] <= tank.capacity_m3*tank.content_density_kg_m3+1e-5
    cg = np.array([fsum(point[axis]*point[3] for point in points)/departure_mass for axis in range(3)])
    screen = condition["gm_screen"]
    assert screen["target_reached"] is True
    assert screen["expected_background_mass_kg"] == pytest.approx(departure_mass)
    draught = screen["screen_mean_draught_m"]
    submerged = HullGeometry.from_params(params).submerged(
        draught, draught, density_kg_m3=h.get("water_density_kg_m3", 1025.0))
    assert submerged.displacement_kg == pytest.approx(departure_mass, rel=1e-9)
    assert cg[:2] == pytest.approx([submerged.lcb_m, submerged.tcb_m], rel=0, abs=1e-6)
    solid_gm = submerged.vcb_m+submerged.bm_t_m-cg[2]
    low, high = screen.get('allowed_solid_gm_band_m',
        [screen['requested_solid_gm_m']-.12, screen['requested_solid_gm_m']+.12])
    assert low-1e-7 <= solid_gm <= high+1e-7
    assert solid_gm <= vessel.hull.max_gm_lashing_m
    assert sum(condition['departure_ballast_masses_kg'].values()) > 0
    assert screen["estimated_solid_gm_after_m"] == pytest.approx(solid_gm, abs=1e-7)

    # Independently close the D48 equivalent booking's mass and longitudinal
    # balance with the separate capacity-bounded sizing recipe. This is a
    # design-reference check, not a certificate for any real cargo stow.
    sizing = condition["booking_reference_ballast_sizing"]
    assert sizing["status"] == "sized", sizing
    assert sizing["actual_plan_certificate"] is False
    assert sizing["empty_witness_mass_included"] is False
    booked_points = [(h["lightship_lcg_m"], h.get("lightship_tcg_m", 0.),
                      h["lightship_vcg_m"], h["lightship_mass_kg"])]
    booked_points.extend(tuple(load) for load in vessel.fixed_loads)
    booked_points.append((*reference_cg, booking_mass))
    for tank in vessel.tanks:
        amount = sizing["tank_masses_kg"].get(tank.id, 0.) if tank.adjustable else tank_masses[tank.id]
        assert 0 <= amount <= tank.capacity_m3*tank.content_density_kg_m3+1e-5
        volume = amount/tank.content_density_kg_m3
        centre = [float(np.interp(volume, [row.volume_m3 for row in tank.table],
                                 [getattr(row, key) for row in tank.table]))
                  for key in ("lcg_m", "tcg_m", "vcg_m")]
        booked_points.append((*centre, amount))
    booked_mass = fsum(point[3] for point in booked_points)
    assert booked_mass == pytest.approx(sizing["target_departure_mass_kg"], abs=.05)
    assert booked_mass <= vessel.hull.max_displacement_kg+1e-5
    assert vessel.hull.max_displacement_kg-booked_mass == pytest.approx(
        sizing["retained_displacement_headroom_kg"], abs=.05)
    balanced_hull = HullGeometry.from_params(params).submerged(
        sizing["target_departure_draught_m"], sizing["target_departure_draught_m"],
        density_kg_m3=h.get("water_density_kg_m3", 1025.))
    assert balanced_hull.displacement_kg == pytest.approx(booked_mass, abs=.05)
    assert fsum(point[0]*point[3] for point in booked_points)/booked_mass == pytest.approx(
        balanced_hull.lcb_m, abs=1e-6)
    assert fsum(point[1]*point[3] for point in booked_points)/booked_mass == pytest.approx(
        balanced_hull.tcb_m, abs=1e-6)


@pytest.mark.parametrize('first_rating,expected', [(40000., [10000., 10000.]),
                                                  (5000., [5000., 15000.])])
def test_equal_height_booking_shares_mass_before_saturating_one_column(first_rating, expected):
    """D48-b: a cell label cannot decide the booking's fore/aft concentration."""
    from stowage.model import Cell, HatchCover, Stack
    from stowage.vessel.fleet_booking import booking_reference

    cells = [Cell(id=cid, stack_id=sid, lcg_m=x, tcg_m=0., vcg_base_m=2.)
             for cid, sid, x in [('0010102', '00101H', 20.), ('0030102', '00301H', 80.)]]
    stacks = [Stack(id=cell.stack_id, bay=bay, row=1, on_deck=False,
                    cell_ids=[cell.id], max_mass_kg=rating, max_mass_20_kg=rating,
                    max_height_m=CONTAINER_HEIGHT_STD_M, base_z_m=2., hatch_cover_id='HC-TIE')
              for cell, bay, rating in zip(cells, [1, 3], [first_rating, 40000.])]
    cover = HatchCover(id='HC-TIE', bays=[1, 3], hold_stack_ids=[s.id for s in stacks],
                       deck_stack_ids=[], mass_kg=1000., lcg_m=50., tcg_m=0., vcg_m=6.)
    vessel = Vessel(id='booking-tie', name='Booking tie', nominal_teu=2,
                    cells=cells, stacks=stacks, hatch_covers=[cover])
    audit = booking_reference(vessel, [], expected_load_mass_kg=20000., reference_seed=7)
    assert [row['mass_kg'] for row in audit['cells']] == pytest.approx(expected)
    assert audit['cg_m'][0] == pytest.approx(sum(x*m for x, m in zip([20., 80.], expected))/20000.)
    assert audit['allocation_policy_version'] == 2
    reordered = vessel.model_copy(update={'cells': list(reversed(cells)),
                                          'stacks': list(reversed(stacks))})
    assert booking_reference(reordered, [], expected_load_mass_kg=20000., reference_seed=7) == audit


def test_booking_sizing_rejects_mass_above_d40_before_hydrostatic_fit(monkeypatch):
    """An oversized reference has no admissible sizing, even before trim fitting."""
    from types import SimpleNamespace
    from stowage.vessel import fleet_balance

    def forbidden_fit(*args, **kwargs):
        raise AssertionError("oversized booking must not enter hydrostatic sizing")
    monkeypatch.setattr(fleet_balance, "_fit_symmetric_ballast", forbidden_fit)
    params = {"hull": {"lightship_mass_kg": 101., "lightship_lcg_m": 0.,
                       "lightship_vcg_m": 0.}, "tanks": []}
    full = SimpleNamespace(hull=SimpleNamespace(max_displacement_kg=100.))
    result = fleet_balance.booking_reference_ballast_sizing(
        full, params, [], booking_mass_kg=0., booking_cg_m=(0., 0., 0.), booking_ballast={})
    assert result["status"] == "above_maximum_displacement"
    assert result["actual_plan_certificate"] is False
    assert result["empty_witness_mass_included"] is False
    assert "tank_masses_kg" not in result
