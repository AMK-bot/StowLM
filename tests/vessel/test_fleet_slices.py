"""Whole-ship context and valid decision regions for the instance size ladder."""

import pytest

from stowage.model import FixedLoad, Vessel
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_slices import SLICE_PRESETS, load_fleet_preset, slice_preset, slice_vessel


def _structural_fixture():
    # APPROXIMATION: independently declared stores for geometry retention tests.
    # The model fixture's unsupported cargo point is not classified as stores.
    vessel = build_instance().vessel
    stores = FixedLoad(x_m=10, y_m=0, z_m=2, mass_kg=1000, kind="non_cargo")
    return vessel.model_copy(update={
        "slice_of": None, "fixed_loads": [stores],
        "sources": [*vessel.sources, "APPROXIMATION: synthetic test stores, 1000 kg at (10,0,2) m"],
    })


@pytest.mark.parametrize("fleet,expected_counts", [
    ("feeder_500", [14, 68, 240, 516]),
    ("feeder_800", [16, 92, 452, 816]),
    ("feeder_1700", [20, 116, 844, 1604]),
])
def test_presets_are_valid_nested_size_ladder(fleet, expected_counts):
    vessel = load_vessel(fleet, with_hull=False)
    original = vessel.model_dump_json()
    presets = [slice_preset(vessel, size) for size in SLICE_PRESETS]
    counts = [len(part.cells) for part in presets]
    assert counts == sorted(set(counts))
    assert counts == expected_counts
    assert counts[-1] == len(vessel.cells)
    assert len(presets[0].hatch_covers) == 1
    # A medium decision region spans the ship so the omitted cargo retains
    # longitudinal control authority, rather than leaving only its aft half.
    medium_bays = {stack.bay for stack in presets[2].stacks}
    full_bays = {stack.bay for stack in vessel.stacks}
    assert min(medium_bays) == min(full_bays)
    assert max(medium_bays) == max(full_bays)
    # Tierwise KCS fitting retains short hold stacks in the first hatch.
    # Tiny must exercise cover access as well as hold and deck placement.
    tiny = presets[0]
    assert len(tiny.stacks) == 4
    assert sum(stack.on_deck for stack in tiny.stacks) == 2
    assert tiny.hatch_covers[0].hold_stack_ids == ["00102H", "00302H"]
    assert tiny.hatch_covers[0].deck_stack_ids == ["00102D", "00302D"]
    for size, part in zip(SLICE_PRESETS, presets):
        assert Vessel.model_validate_json(part.model_dump_json()) == part
        assert part.bridge == vessel.bridge
        assert part.heat_sources == vessel.heat_sources
        assert part.hull == vessel.hull
        assert part.tanks == vessel.tanks
        assert part.hatch_covers
        parent_covers = {cover.id: cover for cover in vessel.hatch_covers}
        for cover in part.hatch_covers:
            parent_cover = parent_covers[cover.id]
            assert (cover.lcg_m, cover.tcg_m, cover.vcg_m, cover.mass_kg) == (
                parent_cover.lcg_m, parent_cover.tcg_m, parent_cover.vcg_m, parent_cover.mass_kg)
        assert any(stack.on_deck for stack in part.stacks)
        assert any(not stack.on_deck for stack in part.stacks)
        assert len(part.cells) == part.nominal_teu if part.slice_of else True
        stack_ids = {stack.id for stack in part.stacks}
        assert {sid for cover in part.hatch_covers
                for sid in cover.hold_stack_ids + cover.deck_stack_ids} == stack_ids
        expected_areas = {
            area.id: area.model_copy(update={"stack_ids": sorted(set(area.stack_ids) & stack_ids)})
            for area in vessel.dg_doc_areas if set(area.stack_ids) & stack_ids
        }
        if size == "full":
            # The full preset preserves document areas for KCS-cleared holds,
            # even where they have no cargo stacks in the parent geometry.
            expected_areas = {area.id: area for area in vessel.dg_doc_areas}
        assert {area.id: area for area in part.dg_doc_areas} == expected_areas
        membership = {sid: area.id for area in part.dg_doc_areas for sid in area.stack_ids}
        assert membership == {stack.id: stack.dg_doc_area_id for stack in part.stacks}
    for first, second in zip(presets, presets[1:]):
        assert {cell.id for cell in first.cells} <= {cell.id for cell in second.cells}
    assert vessel.model_dump_json() == original


def test_slice_retains_whole_physics_and_existing_loads():
    # Treat this fixture geometry as an explicit synthetic root for this test;
    # its default slice_of points at an unavailable foreign parent.
    parent = _structural_fixture()
    original = parent.model_dump_json()
    part = slice_vessel(parent, [1], name="test_slice")
    assert parent.model_dump_json() == original
    assert part.hull == parent.hull
    assert part.tanks == parent.tanks
    assert part.bridge == parent.bridge
    assert part.fixed_loads[:len(parent.fixed_loads)] == parent.fixed_loads
    # Omitted hold cargo may remain below an active cover; its omitted deck
    # stacks stay empty so fixed cargo cannot obstruct the cover lift.
    assert part.fixed_loads[:len(parent.fixed_loads)] == parent.fixed_loads
    assert part.slice_of == parent.id
    assert part.hull.lightship_mass_kg == parent.hull.lightship_mass_kg
    assert any("APPROXIMATION: slice background" in source for source in part.sources)
    assert {sid for cover in part.hatch_covers for sid in cover.hold_stack_ids + cover.deck_stack_ids} == {
        stack.id for stack in part.stacks}


def test_repeated_slice_does_not_duplicate_background():
    vessel = load_vessel("feeder_500", with_hull=False)
    direct = slice_vessel(vessel, [1], name="slice")
    intermediate = slice_vessel(vessel, [1, 3], name="intermediate")
    nested = slice_vessel(intermediate, [1], name="slice")
    assert sorted(nested.fixed_loads) == sorted(direct.fixed_loads)
    assert nested.slice_of == direct.slice_of == vessel.id
    assert slice_vessel(direct, [1], name="slice").fixed_loads == direct.fixed_loads
    assert direct.fixed_loads
    from stowage.vessel.fleet_conditions import slice_condition
    expected = slice_condition(direct)["fixed_load_mass_kg"]
    assert sum(load[3] for load in direct.fixed_loads) == pytest.approx(expected)


def test_nested_foreign_hull_slice_preserves_parent_background_after_round_trip():
    # APPROXIMATION: fixture hydrostatics exercise foreign-parent recovery only;
    # this is not a physically approved loading condition.
    fixture = _structural_fixture()
    from stowage.vessel.fleet_voyage import build_voyage_policy

    fixture_policy = build_voyage_policy(fixture.tanks, vessel_id="foreign_hull",
        parameter_source="tests/vessel/test_fleet_slices.py: independently declared synthetic fixture tanks")
    parent = load_vessel("feeder_500", with_hull=False).model_copy(update={
        "id": "foreign_hull", "hull": fixture.hull, "tanks": fixture.tanks,
        "voyage_policy": fixture_policy,
        "fixed_loads": fixture.fixed_loads,
        "sources": [*load_vessel("feeder_500", with_hull=False).sources, *fixture.sources],
    })
    original = parent.model_dump_json()
    direct = slice_vessel(parent, [1], name="slice")
    intermediate = slice_vessel(parent, [1, 3], name="intermediate")
    intermediate = Vessel.model_validate_json(intermediate.model_dump_json())
    nested = slice_vessel(intermediate, [1], name="slice")
    assert nested.fixed_loads == direct.fixed_loads
    assert nested.hull == direct.hull == parent.hull
    assert nested.tanks == direct.tanks == parent.tanks
    assert nested.fixed_loads[:len(parent.fixed_loads)] == parent.fixed_loads
    assert parent.model_dump_json() == original
    snapshots = [source for source in nested.sources if source.startswith("stowage.slice_parent:")]
    assert len(snapshots) == 1
    assert snapshots == [source for source in intermediate.sources
                         if source.startswith("stowage.slice_parent:")]


def test_legacy_foreign_slice_without_parent_snapshot_rejects_repeated_slicing():
    parent = load_vessel("feeder_500", with_hull=False).model_copy(update={"id": "foreign"})
    part = slice_vessel(parent, [1, 3], name="intermediate")
    legacy = part.model_copy(update={"sources": [source for source in part.sources
                                                if not source.startswith("stowage.slice_parent:")]})
    with pytest.raises(ValueError, match="original parent geometry unavailable.*reload the full vessel"):
        slice_vessel(legacy, [1], name="slice")


def test_foreign_slice_rejects_corrupt_parent_snapshot():
    from stowage.vessel.fleet_slices import recover_parent

    parent = build_instance().vessel
    parent.sources.append("stowage.slice_parent:v1:invalid-json-not-executable")
    with pytest.raises(ValueError, match="invalid slice parent snapshot"):
        recover_parent(parent)


@pytest.mark.parametrize("bays", [[], [2], [999], [True], [1.0]])
def test_bad_bay_selection_rejected(bays):
    with pytest.raises(ValueError):
        slice_vessel(build_instance().vessel, bays, name="bad")


def test_full_copy_and_preset_loader():
    parent = build_instance().vessel
    full = slice_preset(parent, "full")
    assert full == parent
    assert full is not parent
    full.cells.clear()
    assert parent.cells
    assert load_fleet_preset("feeder_500", "tiny", with_hull=False).id == "feeder_500.tiny"
    with pytest.raises(ValueError, match="unknown size"):
        slice_preset(parent, "enormous")


def test_tiny_skips_hatches_without_paired_hold_and_deck():
    from stowage.vessel.fleet_slices import _slice_stacks

    parent = _structural_fixture()
    first = parent.hatch_covers[0]
    retained = {stack.id for stack in parent.stacks} - set(first.hold_stack_ids)
    deck_first = _slice_stacks(parent, retained, name="deck_first")
    tiny = slice_preset(deck_first, "tiny")
    assert len(tiny.stacks) == 4
    assert sum(stack.on_deck for stack in tiny.stacks) == 2
    assert tiny.hatch_covers[0].id != first.id
    deck_only = _slice_stacks(parent, {stack.id for stack in parent.stacks if stack.on_deck}, name="deck_only")
    with pytest.raises(ValueError, match="paired hold and deck"):
        slice_preset(deck_only, "tiny")
