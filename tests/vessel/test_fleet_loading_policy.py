"""D40 policy is vessel data and survives whole-ship planning regions."""

import pytest

from stowage.model import Vessel
from stowage.vessel.fleet import list_fleet, load_ship_params, load_vessel
from stowage.vessel.fleet_slices import load_fleet_preset, slice_preset, slice_vessel
from stowage.vessel.hydrostatics import HullGeometry


POLICY_FIELDS = ("min_trim_m", "max_trim_m", "min_displacement_kg",
                 "max_displacement_kg", "bilge_keel_area_m2")


@pytest.mark.parametrize("name", list_fleet())
def test_d40_full_and_sliced_fleet_preserve_declared_loading_manual(name):
    params = load_ship_params(name)
    full = load_vessel(name)
    hull = full.hull
    limits = params["operating_limits"]
    line = params["load_line"]
    # This is the declared operating freeboard, not the calibrated preliminary
    # minimum-freeboard screen. D40 does not grant a deeper operating draught.
    summer_draught = (line["inputs"]["depth_m"]+line["deck_stringer_thickness_m"]
                      -line["available_freeboard_m"])
    assert summer_draught == pytest.approx(line["inputs"]["draught_m"], abs=1e-12)
    geometry = HullGeometry.from_params(params)
    summer_mass = geometry.submerged(summer_draught, summer_draught,
                                    density_kg_m3=hull.water_density_kg_m3).displacement_kg
    assert hull.min_trim_m == limits["min_trim_m"]
    assert hull.max_trim_m == limits["max_trim_m"]
    assert hull.min_trim_m < hull.max_trim_m
    assert hull.min_displacement_kg == params["hull"]["lightship_mass_kg"]
    assert hull.min_displacement_kg < hull.max_displacement_kg
    assert hull.max_displacement_kg == pytest.approx(summer_mass, rel=1e-12)
    assert hull.hydrostatics[0].displacement_kg < hull.min_displacement_kg
    assert hull.bilge_keel_area_m2 == limits["bilge_keel_area_m2"] == 0.0
    for field in POLICY_FIELDS:
        record = getattr(hull, field+"_provenance")
        assert record is not None and record.source
        assert record.assumption

    regions = [full]
    for preset in ("tiny", "small", "medium", "full"):
        regions.extend((slice_preset(full, preset), load_fleet_preset(name, preset)))
    first_bays = sorted({stack.bay for stack in full.stacks})[:2]
    regions.append(slice_vessel(full, first_bays, name=f"{name}.d40-custom"))
    for region in regions:
        for field in POLICY_FIELDS:
            assert getattr(region.hull, field) == getattr(hull, field)
            assert getattr(region.hull, field+"_provenance") == getattr(hull, field+"_provenance")
        assert Vessel.model_validate_json(region.model_dump_json()) == region
        assert {cell.id for cell in region.cells}.isdisjoint(
            cell.id for cell in region.fixed_load_cells)
        assert {stack.id for stack in region.stacks}.isdisjoint(
            stack.id for stack in region.fixed_load_stacks)
        for load in region.fixed_loads:
            if load.kind == "cargo" and load.mass_kg > 0:
                assert load.cargo_boxes
                assert all(box.provenance.parents for box in load.cargo_boxes)
