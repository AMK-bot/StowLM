"""D42 outside support geometry accompanies fixed cargo without adding mass."""

from pathlib import Path

import pytest

from stowage.model import Vessel
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import background_condition, slice_condition
from stowage.vessel.fleet_slices import recondition_slice, slice_preset, slice_vessel
from stowage.vessel.larsen_pacino import load_larsen_pacino


def _assert_outside_catalog(parent, part):
    decision_stacks = {stack.id for stack in part.stacks}
    outside = {stack.id for stack in parent.stacks} - decision_stacks
    expected_cells = {cell.id for cell in parent.cells if cell.stack_id in outside}
    assert expected_cells <= {cell.id for cell in part.fixed_load_cells}
    assert outside <= {stack.id for stack in part.fixed_load_stacks}
    assert not ({cell.id for cell in part.cells} & {cell.id for cell in part.fixed_load_cells})
    assert not (decision_stacks & {stack.id for stack in part.fixed_load_stacks})
    for stack in part.fixed_load_stacks:
        assert stack.cell_ids == [cell.id for cell in part.fixed_load_cells if cell.stack_id == stack.id]
    covers = {cover.id: cover for cover in part.fixed_load_hatch_covers}
    for stack in part.fixed_load_stacks:
        if stack.hatch_cover_id is not None:
            cover = covers[stack.hatch_cover_id]
            assert stack.id in (cover.deck_stack_ids if stack.on_deck else cover.hold_stack_ids)
    assert Vessel.model_validate_json(part.model_dump_json()) == part


@pytest.mark.parametrize("preset", ["tiny", "small", "medium", "full"])
def test_every_preset_retains_complete_outside_supports(preset):
    parent = load_vessel("feeder_500", with_hull=False)
    part = slice_preset(parent, preset)
    _assert_outside_catalog(parent, part)
    if preset != "full":
        assert sum(load.mass_kg for load in part.fixed_loads) == pytest.approx(
            slice_condition(part)["fixed_load_mass_kg"])
        assert all(load.cargo_boxes is not None for load in part.fixed_loads if load.kind == "cargo")


def _paired_base_parent():
    # APPROXIMATION: synthetic fixture geometry demonstrates catalog transport;
    # its placeholder hydrostatics are deliberately removed.
    payload = build_instance().vessel.model_dump(mode="json")
    payload.update(id="foreign-support-root", slice_of=None, hull=None, tanks=[], fixed_loads=[])
    for field in ("cells", "stacks"):
        payload[f"fixed_load_{field}"] = [item for item in payload[field] if item["id"][3:5] == "01"]
        payload[field] = [item for item in payload[field] if item["id"][3:5] == "02"]
    payload["fixed_load_hatch_covers"] = [cover for cover in payload["hatch_covers"] if cover["id"].endswith("-1")]
    payload["hatch_covers"] = [cover for cover in payload["hatch_covers"] if cover["id"].endswith("-2")]
    # One physical cover can have independently cropped memberships in both
    # catalogs, then union its omitted decision stacks into the fixed catalog.
    for catalog in ("hatch_covers", "fixed_load_hatch_covers"):
        payload[catalog][0].update(id="HC-SHARED", tcg_m=0.0)
    for stack in [*payload["stacks"], *payload["fixed_load_stacks"]]:
        stack["hatch_cover_id"] = "HC-SHARED"
    payload["fixed_loads"] = [dict(
        kind="cargo", mass_kg=10_000.0, x_m=50.0, y_m=-1.3, z_m=3.2955,
        cargo_boxes=[dict(cell_ids=["0010102", "0030102"], length_ft=40,
                         height_m=2.591, gross_mass_kg=10_000.0, x_m=50.0, y_m=-1.3,
                         z_m=3.2955, stack_id="00101H", tier=2,
                         provenance=dict(created_by="synthetic.support-fixture"))])]
    return Vessel.model_validate(payload)


def test_custom_nested_and_recondition_keep_original_paired_cargo_supports_and_mass():
    parent = _paired_base_parent()
    selected = {stack.id for stack in parent.stacks if stack.bay == 1}
    expected_loads, _ = background_condition(parent, selected)
    direct = slice_vessel(parent, [1], name="slice")
    intermediate = slice_vessel(parent, [1, 3], name="intermediate")
    nested = slice_vessel(Vessel.model_validate_json(intermediate.model_dump_json()), [1], name="slice")
    assert direct.fixed_loads == nested.fixed_loads == expected_loads
    assert direct.fixed_loads[0] == parent.fixed_loads[0]
    _assert_outside_catalog(parent, direct)
    active_cover = direct.hatch_covers[0]
    fixed_cover = direct.fixed_load_hatch_covers[0]
    assert active_cover.id == fixed_cover.id == "HC-SHARED"
    assert not (set(active_cover.hold_stack_ids + active_cover.deck_stack_ids)
                & set(fixed_cover.hold_stack_ids + fixed_cover.deck_stack_ids))
    assert {cell.id for cell in parent.fixed_load_cells} <= {cell.id for cell in direct.fixed_load_cells}
    # Both unoccupied upper cells survive, so later prefix validation cannot
    # mistake missing declaration for a shorter or discontinuous support.
    assert {"0010104", "0030104"} <= {cell.id for cell in direct.fixed_load_cells}
    assert nested.fixed_load_cells == direct.fixed_load_cells
    assert nested.fixed_load_stacks == direct.fixed_load_stacks
    assert nested.fixed_load_hatch_covers == direct.fixed_load_hatch_covers
    rebuilt = recondition_slice(direct, expected_load_mass_kg=20_000.0)
    _assert_outside_catalog(parent, rebuilt)
    assert rebuilt.fixed_loads[0] == parent.fixed_loads[0]
    assert rebuilt.fixed_load_cells == direct.fixed_load_cells
    assert sum(load.mass_kg for load in rebuilt.fixed_loads) == pytest.approx(
        slice_condition(rebuilt)["fixed_load_mass_kg"])
    invalid = rebuilt.model_dump(mode="json")
    next(cell for cell in invalid["fixed_load_cells"] if cell["id"] == "0030102")["accepts_40"] = False
    with pytest.raises(ValueError, match="accepts_40"):
        Vessel.model_validate(invalid)
    invalid = rebuilt.model_dump(mode="json")
    box = invalid["fixed_loads"][0]["cargo_boxes"][0]
    box.update(cell_ids=["0010104", "0030104"], tier=4, z_m=5.8865)
    invalid["fixed_loads"][0]["z_m"] = 5.8865
    with pytest.raises(ValueError, match="support|gap"):
        Vessel.model_validate(invalid)


def test_named_parent_original_fixed_catalog_survives_parameter_parent_recovery():
    supports = _paired_base_parent().model_dump(mode="json")

    def address(value):
        return {"001": "995", "003": "997"}[value[:3]] + value[3:]

    for cell in supports["fixed_load_cells"]:
        cell.update(id=address(cell["id"]), stack_id=address(cell["stack_id"]))
    for stack in supports["fixed_load_stacks"]:
        stack.update(id=address(stack["id"]), bay={1: 995, 3: 997}[stack["bay"]],
                     cell_ids=[address(cid) for cid in stack["cell_ids"]])
    for cover in supports["fixed_load_hatch_covers"]:
        cover.update(bays=[995, 996, 997],
                     hold_stack_ids=[address(sid) for sid in cover["hold_stack_ids"]],
                     deck_stack_ids=[address(sid) for sid in cover["deck_stack_ids"]])
    for box in supports["fixed_loads"][0]["cargo_boxes"]:
        box.update(cell_ids=[address(cid) for cid in box["cell_ids"]], stack_id=address(box["stack_id"]))
    parent_payload = load_vessel("feeder_500", with_hull=False).model_dump(mode="json")
    parent_payload.update({field: supports[field] for field in (
        "fixed_loads", "fixed_load_cells", "fixed_load_stacks", "fixed_load_hatch_covers")})
    parent = Vessel.model_validate(parent_payload)
    direct = slice_vessel(parent, [1], name="slice")
    intermediate = slice_vessel(parent, [1, 3], name="intermediate")
    nested = slice_vessel(Vessel.model_validate_json(intermediate.model_dump_json()), [1], name="slice")
    assert nested.fixed_loads == direct.fixed_loads
    assert nested.fixed_load_cells == direct.fixed_load_cells
    assert nested.fixed_load_stacks == direct.fixed_load_stacks
    assert nested.fixed_load_hatch_covers == direct.fixed_load_hatch_covers
    assert {cell.id for cell in parent.fixed_load_cells} <= {cell.id for cell in nested.fixed_load_cells}
    assert nested.fixed_loads[0] == parent.fixed_loads[0]


@pytest.mark.parametrize("field,change", [
    ("cells", {"lcg_m": 47.0}),
    ("stacks", {"max_height_m": 6.1}),
    ("hatch_covers", {"mass_kg": 12_001.0}),
])
def test_conflicting_original_fixed_support_geometry_rejects(field, change):
    part = slice_vessel(_paired_base_parent(), [1, 3], name="intermediate")
    catalog = getattr(part, f"fixed_load_{field}")
    catalog[0] = catalog[0].model_copy(update=change)
    with pytest.raises(ValueError, match="conflicting fixed cargo.*geometry"):
        slice_vessel(part, [1], name="slice")


def test_nested_imported_support_catalog_matches_direct():
    raw = Path(__file__).resolve().parents[2] / "data/raw/larsen-pacino/vessel_data/vessel_S.txt"
    parent = load_larsen_pacino(raw)
    direct = slice_vessel(parent, [7], name="slice")
    intermediate = slice_vessel(parent, [7, 9], name="intermediate")
    nested = slice_vessel(Vessel.model_validate_json(intermediate.model_dump_json()), [7], name="slice")
    _assert_outside_catalog(parent, nested)
    assert nested.fixed_loads == direct.fixed_loads
    assert nested.fixed_load_cells == direct.fixed_load_cells
    assert nested.fixed_load_stacks == direct.fixed_load_stacks
    assert nested.fixed_load_hatch_covers == direct.fixed_load_hatch_covers
