"""Fixed synthetic cargo keeps mass and occupied geometry as separate contracts.

ISO 668 dimensions in ``stowage.conventions`` bound occupied dry20 stack blocks.
These tests establish the background supplied to windage/visibility consumers,
not checker approval of a departure or a synthetic loading manual.
"""

import pytest

from stowage.conventions import (
    CONTAINER_HEIGHT_STD_M,
    CONTAINER_LENGTH_M,
    CONTAINER_WIDTH_M,
)
from stowage.model import FixedLoad, FixedLoadSilhouette, Vessel
from stowage.vessel import fleet_cache
from stowage.vessel.fleet import list_fleet, load_vessel
from stowage.vessel.fleet_conditions import slice_condition
from stowage.vessel.fleet_slices import (
    load_fleet_preset,
    recondition_slice,
    slice_preset,
    slice_vessel,
)


def _mass_and_moments(vessel):
    loads = vessel.fixed_loads
    return (sum(load.mass_kg for load in loads),
            *(sum(load.mass_kg * getattr(load, axis) for load in loads)
              for axis in ("x_m", "y_m", "z_m")))


@pytest.mark.parametrize("name", list_fleet())
@pytest.mark.parametrize("preset", ("tiny", "small", "medium"))
def test_background_cargo_has_authentic_deck_stack_envelopes(name, preset, fleet_vessel):
    full = fleet_vessel(name, with_hull=False)
    vessel = slice_preset(full, preset)
    condition = slice_condition(vessel)
    cells = {(cell.lcg_m, cell.tcg_m, cell.vcg_base_m + CONTAINER_HEIGHT_STD_M / 2): cell
             for cell in full.cells}
    cells_by_id = {cell.id: cell for cell in full.cells}
    stacks = {stack.id: stack for stack in full.stacks}
    deck_stacks = {(cells_by_id[stack.cell_ids[0]].lcg_m,
                    cells_by_id[stack.cell_ids[0]].tcg_m): stack
                   for stack in full.stacks if stack.on_deck and stack.cell_ids}
    selected = {stack.id for stack in vessel.stacks}
    active_covers = {stack.hatch_cover_id for stack in vessel.stacks}
    deck_count = hold_count = 0
    assert vessel.fixed_loads
    assert not full.fixed_loads
    assert all(isinstance(load, FixedLoad) for load in vessel.fixed_loads)
    assert sum(load.mass_kg for load in vessel.fixed_loads) == pytest.approx(
        condition["fixed_cargo_mass_kg"])
    for load in vessel.fixed_loads:
        assert load.kind == "cargo"
        silhouette = load.silhouette
        if silhouette is None:
            cell = cells[(load.x_m, load.y_m, load.z_m)]
            stack = stacks[cell.stack_id]
            assert not stack.on_deck
            assert stack.id not in selected
            hold_count += 1
            continue
        deck_count += 1
        matches = [candidate for (x, y), candidate in deck_stacks.items()
                   if (load.x_m, load.y_m) == pytest.approx((x, y))]
        assert len(matches) == 1
        stack = matches[0]
        assert stack.id not in selected
        assert stack.hatch_cover_id not in active_covers
        cell = cells_by_id[stack.cell_ids[0]]
        assert isinstance(silhouette, FixedLoadSilhouette)
        assert silhouette.x_min_m == pytest.approx(cell.lcg_m - CONTAINER_LENGTH_M[20] / 2)
        assert silhouette.x_max_m == pytest.approx(cell.lcg_m + CONTAINER_LENGTH_M[20] / 2)
        assert silhouette.y_min_m == pytest.approx(cell.tcg_m - CONTAINER_WIDTH_M / 2)
        assert silhouette.y_max_m == pytest.approx(cell.tcg_m + CONTAINER_WIDTH_M / 2)
        assert silhouette.z_min_m == pytest.approx(stack.base_z_m)
        possible_tops = [cells_by_id[cid].vcg_base_m + CONTAINER_HEIGHT_STD_M
                         for cid in stack.cell_ids]
        assert any(silhouette.z_max_m == pytest.approx(top) for top in possible_tops)
        assert silhouette.z_min_m < load.z_m < silhouette.z_max_m
    assert deck_count > 0
    assert hold_count > 0
    restored = Vessel.model_validate_json(vessel.model_dump_json())
    assert restored == vessel
    assert _mass_and_moments(restored) == _mass_and_moments(vessel)


def test_existing_silhouette_survives_nested_slicing_and_reconditioning(fleet_vessel):
    full = fleet_vessel("feeder_500", with_hull=False)
    base = FixedLoad(x_m=-1, y_m=0, z_m=10, mass_kg=1000, kind="non_cargo",
                     silhouette=FixedLoadSilhouette(x_min_m=-2, x_max_m=0,
                                                   y_min_m=-1, y_max_m=1,
                                                   z_min_m=9, z_max_m=11))
    payload = full.model_dump()
    payload["fixed_loads"] = [base.model_dump()]
    payload["sources"].append("APPROXIMATION: declared synthetic test stores with occupied envelope")
    full = Vessel.model_validate(payload)
    small = slice_preset(full, "small")
    nested = slice_vessel(small, sorted({stack.bay for stack in small.stacks}), name="nested")
    restored = Vessel.model_validate_json(nested.model_dump_json())
    changed = recondition_slice(restored, expected_load_mass_kg=14100)
    for vessel in (small, nested, restored, changed):
        matching = [load for load in vessel.fixed_loads if load.kind == "non_cargo"]
        assert matching == [base]
        assert matching[0].model_dump() == base.model_dump()
        condition = slice_condition(vessel)
        assert len(condition["base_fixed_loads"]) == 1
        assert condition["fixed_load_mass_kg"] == pytest.approx(
            base.mass_kg + condition["fixed_cargo_mass_kg"])
    assert recondition_slice(changed, expected_load_mass_kg=14100) == changed


def test_legacy_fixed_point_loads_still_parse_and_roundtrip(fleet_vessel):
    payload = fleet_vessel("feeder_500", with_hull=False).model_dump()
    payload["fixed_loads"] = [(3.0, -2.0, 1.0, 1234.0)]
    vessel = Vessel.model_validate(payload)
    load = vessel.fixed_loads[0]
    assert isinstance(load, FixedLoad)
    assert load.as_point_mass() == (3.0, -2.0, 1.0, 1234.0)
    assert tuple(load) == load.as_point_mass()
    assert load.kind == "unknown"
    assert load.silhouette is None
    restored = Vessel.model_validate_json(vessel.model_dump_json())
    assert restored == vessel
    assert _mass_and_moments(restored) == _mass_and_moments(vessel)


def test_deck_stack_combination_preserves_first_moments_and_full_envelope():
    from stowage.vessel.fleet_conditions import _deck_background_loads

    # A heavy bottom box and a light top box have their CG below half height.
    points = [FixedLoad(x_m=20, y_m=-3, z_m=11, mass_kg=30000, kind="cargo",
                        silhouette=FixedLoadSilhouette(x_min_m=17, x_max_m=23,
                                                      y_min_m=-4, y_max_m=-2,
                                                      z_min_m=10, z_max_m=12)),
              FixedLoad(x_m=20, y_m=-3, z_m=13, mass_kg=10000, kind="cargo",
                        silhouette=FixedLoadSilhouette(x_min_m=17, x_max_m=23,
                                                      y_min_m=-4, y_max_m=-2,
                                                      z_min_m=12, z_max_m=14))]
    combined = _deck_background_loads(points)
    assert combined.mass_kg == 40000
    assert (combined.x_m, combined.y_m, combined.z_m) == (20, -3, 11.5)
    assert combined.kind == "cargo"
    for axis in ("x_m", "y_m", "z_m"):
        assert combined.mass_kg * getattr(combined, axis) == pytest.approx(
            sum(point.mass_kg * getattr(point, axis) for point in points))
    assert combined.silhouette == FixedLoadSilhouette(
        x_min_m=17, x_max_m=23, y_min_m=-4, y_max_m=-2, z_min_m=10, z_max_m=14)


def test_disk_and_memo_cache_preserve_silhouettes_and_scalar_moments(monkeypatch, tmp_path):
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")
    fleet_cache.clear_vessel_memo()
    try:
        uncached = load_fleet_preset("feeder_500", "tiny", with_hull=False)
        expected = uncached.model_dump_json()
        assert any(load.silhouette is not None for load in uncached.fixed_loads)
        monkeypatch.delenv("STOWAGE_VESSEL_CACHE")
        cold = load_fleet_preset("feeder_500", "tiny", with_hull=False)
        memo = load_fleet_preset("feeder_500", "tiny", with_hull=False)
        fleet_cache.clear_vessel_memo()
        disk = load_fleet_preset("feeder_500", "tiny", with_hull=False)
        for vessel in (cold, memo, disk):
            assert vessel.model_dump_json() == expected
            assert _mass_and_moments(vessel) == _mass_and_moments(uncached)
        slice_json = list(tmp_path.glob("*.tiny.geometry.*.json"))
        assert len(slice_json) == 1
        assert Vessel.model_validate_json(slice_json[0].read_text()) == uncached
    finally:
        fleet_cache.clear_vessel_memo()
