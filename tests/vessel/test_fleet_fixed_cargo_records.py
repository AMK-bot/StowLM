"""D42: equivalent background cells remain inspectable after point aggregation."""

from math import fsum

import pytest

from stowage.conventions import CONTAINER_HEIGHT_STD_M, parse_slot
from stowage.model import FixedCargoBox, FixedLoad, FixedLoadSilhouette
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import (
    CONDITION_PREFIX, _deck_background_loads, background_condition,
)
from stowage.vessel.fleet_slices import slice_preset


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("preset", ["tiny", "small", "medium"])
def test_each_allocated_background_cell_emits_exact_box_mass_cg_and_provenance(name, preset):
    import json

    parent = load_vessel(name, with_hull=False)
    # The public selector also validates the separate physical support catalogs.
    selected = slice_preset(parent, preset)
    loads, source = background_condition(parent, {stack.id for stack in selected.stacks}, preset=preset)
    audit = json.loads(source[len(CONDITION_PREFIX):])
    cells = {cell.id: cell for cell in parent.cells}
    records = []
    for load in loads:
        assert load.kind == "cargo"
        assert load.cargo_boxes
        assert fsum(box.gross_mass_kg for box in load.cargo_boxes) == pytest.approx(load.mass_kg)
        for axis in ("x_m", "y_m", "z_m"):
            centroid = fsum(box.gross_mass_kg*getattr(box, axis) for box in load.cargo_boxes)/load.mass_kg
            assert centroid == pytest.approx(getattr(load, axis), abs=1e-10)
        records.extend(load.cargo_boxes)
    actual = {}
    for box in records:
        assert isinstance(box, FixedCargoBox)
        assert box.length_ft == 20
        assert box.height_m == CONTAINER_HEIGHT_STD_M
        assert len(box.cell_ids) == 1
        cid = box.cell_ids[0]
        cell = cells[cid]
        assert cid not in actual
        actual[cid] = box.gross_mass_kg
        assert box.stack_id == cell.stack_id
        assert box.tier == parse_slot(cid).tier
        assert (box.x_m, box.y_m, box.z_m) == (
            cell.lcg_m, cell.tcg_m, cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2)
        assert box.provenance.created_by == "vessel.fleet_conditions"
        assert parent.id in box.provenance.parents
        assert any("feeder_export.toml" in source for source in box.provenance.parents)
        assert any(source.startswith("APPROXIMATION:") for source in box.provenance.parents)
        assert box.provenance.seed == audit["seed"]
    assert actual == {row["cell_id"]: row["mass_kg"] for row in audit["background_cells"]}
    assert fsum(actual.values()) == pytest.approx(audit["fixed_cargo_mass_kg"])
    # Unbooked presets use the earlier fleet_gm deck-transfer path, before
    # D58's booked-background LP. Its stored D42 records obey the same exact
    # gross-kg order: equality is allowed, an upward inversion of any size is
    # not. Compare physical support columns after the actual JSON round trip.
    stored = [FixedLoad.model_validate_json(load.model_dump_json()) for load in loads]
    stored_mass = {cid: box.gross_mass_kg for load in stored
                   for box in load.cargo_boxes or [] for cid in box.cell_ids}
    assert stored_mass == actual
    comparisons = 0
    for stack in parent.stacks:
        occupied = [(cid, stored_mass[cid]) for cid in stack.cell_ids if cid in stored_mass]
        for (lower_cid, lower), (upper_cid, upper) in zip(occupied, occupied[1:]):
            assert upper <= lower, (stack.id, lower_cid, upper_cid, lower, upper, upper-lower)
            comparisons += 1
    assert comparisons > 0


def test_deck_aggregation_preserves_constituent_records_and_all_first_moments():
    points = []
    for index, mass in enumerate((9000., 5500., 2200.)):
        floor = 10.+index*CONTAINER_HEIGHT_STD_M
        box = FixedCargoBox(cell_ids=[f"00101{82+index*2}"], length_ft=20,
            height_m=CONTAINER_HEIGHT_STD_M, gross_mass_kg=mass,
            x_m=20., y_m=-2., z_m=floor+CONTAINER_HEIGHT_STD_M/2,
            stack_id="00101D", tier=82+index*2)
        points.append(FixedLoad(kind="cargo", cargo_boxes=[box],
            x_m=box.x_m, y_m=box.y_m, z_m=box.z_m, mass_kg=mass,
            silhouette=FixedLoadSilhouette(x_min_m=17., x_max_m=23., y_min_m=-3.3,
                y_max_m=-.7, z_min_m=floor, z_max_m=floor+CONTAINER_HEIGHT_STD_M)))
    aggregate = _deck_background_loads(points)
    assert aggregate.cargo_boxes == [point.cargo_boxes[0] for point in points]
    assert aggregate.mass_kg == sum(point.mass_kg for point in points)
    for axis in ("x_m", "y_m", "z_m"):
        assert getattr(aggregate, axis)*aggregate.mass_kg == pytest.approx(
            sum(getattr(point, axis)*point.mass_kg for point in points))


def test_legacy_base_classification_remains_unknown_when_background_is_added():
    parent = load_vessel("feeder_500", with_hull=False)
    legacy = FixedLoad.model_validate([0., 0., 0., 12.])
    assert legacy.kind == "unknown"
    parent = parent.model_copy(update={"fixed_loads": [legacy]})
    selected = {stack.id for stack in parent.stacks if stack.bay in (1, 3)}
    loads, _ = background_condition(parent, selected, preset="small")
    assert loads[0] == legacy
    assert loads[0].kind == "unknown"
    assert loads[0].cargo_boxes is None


def test_positive_legacy_base_cargo_without_box_evidence_fails_closed():
    parent = load_vessel("feeder_500", with_hull=False)
    missing = FixedLoad(x_m=0., y_m=0., z_m=0., mass_kg=12., kind="cargo")
    parent = parent.model_copy(update={"fixed_loads": [missing]})
    selected = {stack.id for stack in parent.stacks if stack.bay in (1, 3)}
    with pytest.raises(ValueError, match="base fixed cargo lacks per-box D42 support evidence"):
        background_condition(parent, selected, preset="small")


def test_background_box_provenance_preserves_nested_source_taint():
    parent = load_vessel("feeder_500", with_hull=False).model_copy(deep=True)
    parent.dg_doc_areas[0].provenance.stubs["test.synthetic-source"] = "tainted support evidence"
    selected = {stack.id for stack in parent.stacks if stack.bay in (1, 3)}
    loads, _ = background_condition(parent, selected, preset="small")
    boxes = [box for load in loads for box in (load.cargo_boxes or [])]
    assert boxes
    assert all(box.provenance.stubs == {"test.synthetic-source": "tainted support evidence"}
               for box in boxes)


@pytest.mark.parametrize("length_ft", [20, 40])
def test_named_original_column_already_carrying_base_cargo_is_not_allocated_again(length_ft):
    """A named selector may move real columns into non-decision catalogs."""
    from stowage.model import Vessel
    from stowage.vessel.fleet_conditions import slice_condition
    from stowage.vessel.fleet_slices import recondition_slice, slice_vessel

    parent = load_vessel("feeder_500", with_hull=False)
    sid = "00501H"
    stack = next(stack for stack in parent.stacks if stack.id == sid)
    cell = next(cell for cell in parent.cells if cell.id == stack.cell_ids[0])
    reserved = {sid} if length_ft == 20 else {sid, "00701H"}
    base_cells = [next(item for item in parent.cells if item.stack_id == support and item.id.endswith("02"))
                  for support in sorted(reserved)]
    x = sum(item.lcg_m for item in base_cells)/len(base_cells)
    payload = parent.model_dump(mode="json")
    payload["fixed_load_cells"] = [item for item in payload["cells"] if item["stack_id"] in reserved]
    payload["cells"] = [item for item in payload["cells"] if item["stack_id"] not in reserved]
    payload["fixed_load_stacks"] = [item for item in payload["stacks"] if item["id"] in reserved]
    payload["stacks"] = [item for item in payload["stacks"] if item["id"] not in reserved]
    payload["fixed_load_hatch_covers"] = []
    for cover in payload["hatch_covers"]:
        fixed_hold = [item for item in cover["hold_stack_ids"] if item in reserved]
        if fixed_hold:
            payload["fixed_load_hatch_covers"].append(dict(cover, hold_stack_ids=fixed_hold, deck_stack_ids=[]))
            cover["hold_stack_ids"] = [item for item in cover["hold_stack_ids"] if item not in reserved]
    for area in payload["dg_doc_areas"]:
        area["stack_ids"] = [item for item in area["stack_ids"] if item not in reserved]
    z = cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2
    payload["fixed_loads"] = [dict(kind="cargo", mass_kg=8000., x_m=x,
        y_m=cell.tcg_m, z_m=z, cargo_boxes=[dict(cell_ids=[item.id for item in base_cells], length_ft=length_ft,
            height_m=CONTAINER_HEIGHT_STD_M, gross_mass_kg=8000., x_m=x,
            y_m=cell.tcg_m, z_m=z, stack_id=sid, tier=parse_slot(cell.id).tier)])]
    parent = Vessel.model_validate(payload)
    original = parent.model_dump_json()
    direct = slice_vessel(parent, [1, 3], name="converted-base-column")
    intermediate = slice_vessel(parent, [1, 3, 7, 9], name="intermediate")
    nested = slice_vessel(Vessel.model_validate_json(intermediate.model_dump_json()),
                          [1, 3], name="converted-base-column")
    rebuilt = recondition_slice(direct, expected_load_mass_kg=slice_condition(direct)["expected_load_mass_kg"])
    reserved_cell_ids = {item.id for item in parent.fixed_load_cells}
    for part in (direct, nested, rebuilt):
        assert part.fixed_loads[0] == parent.fixed_loads[0]
        boxes = [box for load in part.fixed_loads for box in load.cargo_boxes or []]
        assert sum(cell.id in box.cell_ids for box in boxes) == 1
        assert [box for box in boxes if reserved_cell_ids.intersection(box.cell_ids)] == parent.fixed_loads[0].cargo_boxes
        assert next(item for item in part.fixed_load_stacks if item.id == sid) == stack
        assert Vessel.model_validate_json(part.model_dump_json()) == part
    assert direct.fixed_loads == nested.fixed_loads
    assert parent.model_dump_json() == original
