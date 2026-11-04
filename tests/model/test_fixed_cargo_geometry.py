"""D42 background cargo uses explicit, vessel-checked physical box supports."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from stowage.frozen import StubTaintError
from stowage.io import load_instance, save_instance
from stowage.model import Instance
from stowage.model.fixtures import build_instance


def _payload():
    payload = build_instance().model_dump(mode="json")
    vessel = payload["vessel"]
    # Keep background support geometry separate from the decision-cell catalogue.
    for field in ("cells", "stacks"):
        vessel[f"fixed_load_{field}"] = [x for x in vessel[field] if x["id"][3:5] == "01"]
        vessel[field] = [x for x in vessel[field] if x["id"][3:5] == "02"]
    vessel["fixed_load_hatch_covers"] = [c for c in vessel["hatch_covers"] if c["id"].endswith("-1")]
    vessel["hatch_covers"] = [c for c in vessel["hatch_covers"] if c["id"].endswith("-2")]
    vessel["fixed_loads"] = []
    return payload


def _box(length_ft=20, *, tier=2, bay=1, height_m=2.591, floor_m=2.0):
    cells = [f"{bay:03d}01{tier:02d}"]
    if length_ft != 20:
        cells.append(f"{bay + 2:03d}01{tier:02d}")
    return dict(cell_ids=cells, length_ft=length_ft, height_m=height_m, gross_mass_kg=10000.0,
                x_m=46.9 if length_ft == 20 and bay == 1 else 53.1 if length_ft == 20 else 50.0,
                y_m=-1.3, z_m=floor_m + height_m / 2, stack_id=f"{bay:03d}01H", tier=tier,
                provenance=dict(created_by="test.fixed-cargo", parents=["synthetic support evidence"]))


def _load(*boxes):
    mass = sum(box["gross_mass_kg"] for box in boxes)
    return dict(kind="cargo", cargo_boxes=list(boxes), mass_kg=mass,
                **{axis: sum(b[axis] * b["gross_mass_kg"] for b in boxes) / mass
                   for axis in ("x_m", "y_m", "z_m")})


def _instance(*boxes):
    payload = _payload()
    payload["vessel"]["fixed_loads"] = [_load(*boxes)]
    return payload


@pytest.mark.parametrize("length_ft", [20, 40, 45])
def test_fixed_cargo_valid_supports_and_typed_records(length_ft):
    from stowage.model import FixedCargoBox

    instance = Instance.model_validate(_instance(_box(length_ft)))
    load = instance.vessel.fixed_loads[0]
    assert isinstance(load.cargo_boxes[0], FixedCargoBox)
    assert load.cargo_boxes[0].length_ft == length_ft
    assert len(load.cargo_boxes[0].cell_ids) == (1 if length_ft == 20 else 2)
    assert all(cell.id[3:5] == "02" for cell in instance.vessel.cells)
    assert all(cell.id[3:5] == "01" for cell in instance.vessel.fixed_load_cells)


def test_fixed_cargo_stacks_use_actual_heights_and_cell_sequence():
    payload = _instance(_box(height_m=2.896), _box(tier=6, floor_m=4.896))
    cell = next(c for c in payload["vessel"]["fixed_load_cells"] if c["id"] == "0010104")
    cell["id"] = "0010106"
    stack = next(s for s in payload["vessel"]["fixed_load_stacks"] if s["id"] == "00101H")
    stack["cell_ids"][-1] = "0010106"
    assert len(Instance.model_validate(payload).vessel.fixed_loads[0].cargo_boxes) == 2


@pytest.mark.parametrize("length_ft,bay", [(20, 1), (40, 1), (40, 3), (45, 1), (45, 3)])
def test_fixed_cargo_each_support_requires_length_capability(length_ft, bay):
    payload = _instance(_box(length_ft))
    cell = next(c for c in payload["vessel"]["fixed_load_cells"] if c["id"] == f"{bay:03d}0102")
    cell[f"accepts_{length_ft}"] = False
    with pytest.raises(ValidationError, match="fixed_load.*accepts_" + str(length_ft)):
        Instance.model_validate(payload)


@pytest.mark.parametrize("length_ft,half", [(20, 0), (40, 0), (40, 1), (45, 0), (45, 1)])
def test_fixed_cargo_unknown_support_is_rejected(length_ft, half):
    box = _box(length_ft)
    box["cell_ids"][half] = "0050102"
    with pytest.raises(ValidationError, match="cell_ids|fixed_load.*cell"):
        Instance.model_validate(_instance(box))


@pytest.mark.parametrize("length_ft", [20, 40, 45])
def test_fixed_cargo_active_decision_cell_cannot_be_background_support(length_ft):
    box = _box(length_ft)
    box["cell_ids"] = [cid[:3] + "02" + cid[5:] for cid in box["cell_ids"]]
    box["stack_id"] = "00102H"
    box["y_m"] = 1.3
    with pytest.raises(ValidationError, match="fixed_load.*cell|cell_ids"):
        Instance.model_validate(_instance(box))


@pytest.mark.parametrize("length_ft", [20, 40, 45])
def test_fixed_cargo_rejects_gap_below_box(length_ft):
    with pytest.raises(ValidationError, match="fixed_load.*unsupported|fixed_load.*gap|fixed_load.*support"):
        Instance.model_validate(_instance(_box(length_ft, tier=4, floor_m=4.591)))


@pytest.mark.parametrize("length_ft,missing_bay", [(40, 1), (40, 3), (45, 1), (45, 3)])
def test_fixed_cargo_long_box_needs_both_columns_supported(length_ft, missing_bay):
    lower = _box(bay=3 if missing_bay == 1 else 1)
    upper = _box(length_ft, tier=4, floor_m=4.591)
    with pytest.raises(ValidationError, match="fixed_load.*unsupported|fixed_load.*gap|fixed_load.*support"):
        Instance.model_validate(_instance(lower, upper))


def test_fixed_cargo_occupancy_is_unique_across_fixed_loads():
    payload = _instance(_box())
    payload["vessel"]["fixed_loads"].append(deepcopy(payload["vessel"]["fixed_loads"][0]))
    with pytest.raises(ValidationError, match="fixed_load.*occup|fixed_load.*duplicate|fixed_load.*overlap"):
        Instance.model_validate(payload)


def test_fixed_cargo_support_can_cross_fixed_load_records():
    payload = _payload()
    payload["vessel"]["fixed_loads"] = [_load(_box(tier=4, floor_m=4.591)), _load(_box())]
    assert len(Instance.model_validate(payload).vessel.fixed_loads) == 2


@pytest.mark.parametrize("axis,value", [("x_m", 60.0), ("y_m", 0.0), ("z_m", 1.9), ("z_m", 4.7)])
@pytest.mark.parametrize("length_ft", [20, 40, 45])
def test_fixed_cargo_cg_must_lie_within_its_physical_box(length_ft, axis, value):
    box = _box(length_ft)
    box[axis] = value
    with pytest.raises(ValidationError, match="fixed_load.*CG|fixed_load.*cg|fixed_load.*envelope"):
        Instance.model_validate(_instance(box))


def test_fixed_cargo_cg_need_not_be_box_midpoint():
    box = _box()
    box.update(x_m=47.4, y_m=-1.0, z_m=2.5)
    assert Instance.model_validate(_instance(box)).vessel.fixed_loads[0].cargo_boxes[0].z_m == 2.5


@pytest.mark.parametrize("length_ft", [40, 45])
def test_fixed_cargo_long_box_rejects_unequal_actual_support_heights(length_ft):
    with pytest.raises(ValidationError, match="fixed_load.*level|fixed_load.*height|fixed_load.*support"):
        Instance.model_validate(_instance(_box(), _box(bay=3, height_m=2.896),
                                         _box(length_ft, tier=4, floor_m=4.591)))


@pytest.mark.parametrize("field", ["fixed_load_cells", "fixed_load_stacks"])
def test_background_geometry_must_be_disjoint_from_decision_catalogues(field):
    payload = _instance(_box())
    active = field.removeprefix("fixed_load_")
    payload["vessel"][field].append(deepcopy(payload["vessel"][active][0]))
    with pytest.raises(ValidationError, match="fixed_load|disjoint|overlap"):
        Instance.model_validate(payload)


@pytest.mark.parametrize("damage", ["cell_stack", "stack_cell", "stack_cover", "cover_stack"])
def test_background_support_catalogues_require_reciprocal_geometry(damage):
    payload = _instance(_box())
    vessel = payload["vessel"]
    if damage == "cell_stack":
        vessel["fixed_load_stacks"] = [s for s in vessel["fixed_load_stacks"] if s["id"] != "00301D"]
        vessel["fixed_load_hatch_covers"][0]["deck_stack_ids"].remove("00301D")
    elif damage == "stack_cell":
        vessel["fixed_load_cells"] = [c for c in vessel["fixed_load_cells"] if c["id"] != "0030184"]
    elif damage == "stack_cover":
        vessel["fixed_load_hatch_covers"] = []
    else:
        vessel["fixed_load_hatch_covers"][0]["hold_stack_ids"].remove("00301H")
    with pytest.raises(ValidationError, match="fixed_load"):
        Instance.model_validate(payload)


@pytest.mark.parametrize("length_ft", [20, 40, 45])
def test_fixed_cargo_json_io_roundtrip(tmp_path, length_ft):
    instance = Instance.model_validate(_instance(_box(length_ft)))
    first = save_instance(instance, tmp_path / "first.json")
    restored = load_instance(first)
    assert restored == instance
    assert first.read_bytes() == save_instance(restored, tmp_path / "second.json").read_bytes()
    assert restored.vessel.fixed_loads[0].cargo_boxes[0].cell_ids == _box(length_ft)["cell_ids"]


def test_fixed_cargo_nested_provenance_reaches_instance_and_writer(tmp_path):
    box = _box()
    box["provenance"]["stubs"] = {"fixed.cargo": "synthetic evidence stub"}
    instance = Instance.model_validate(_instance(box))
    assert instance.provenance.stubs == {"fixed.cargo": "synthetic evidence stub"}
    with pytest.raises(StubTaintError, match="fixed.cargo"):
        save_instance(instance, tmp_path / "must_not_write.json")
    assert load_instance(save_instance(instance, tmp_path / "allowed.json", allow_stubs=True)) == instance


def test_legacy_fixed_load_geometry_remains_unknown():
    payload = build_instance().model_dump(mode="json")
    payload["vessel"]["fixed_loads"] = [[80, 0, 4, 200000]]
    instance = Instance.model_validate(payload)
    assert instance.vessel.fixed_loads[0].kind == "unknown"
    assert instance.vessel.fixed_loads[0].cargo_boxes is None
    assert instance.vessel.fixed_load_cells == []
    assert instance.vessel.fixed_load_stacks == []
    assert instance.vessel.fixed_load_hatch_covers == []


def test_background_and_decision_catalogues_can_split_one_physical_cover():
    payload = _instance(_box())
    vessel = payload["vessel"]
    for field in ("hatch_covers", "fixed_load_hatch_covers"):
        vessel[field][0]["id"] = "HC-SHARED"
    for field in ("stacks", "fixed_load_stacks"):
        for stack in vessel[field]:
            stack["hatch_cover_id"] = "HC-SHARED"
    instance = Instance.model_validate(payload)
    assert instance.vessel.hatch_covers[0].id == instance.vessel.fixed_load_hatch_covers[0].id
    assert not (set(instance.vessel.hatch_covers[0].hold_stack_ids)
                & set(instance.vessel.fixed_load_hatch_covers[0].hold_stack_ids))


@pytest.mark.parametrize("length_ft", [40, 45])
def test_fixed_cargo_long_box_requires_level_stack_bases(length_ft):
    payload = _instance(_box(length_ft))
    stack = next(s for s in payload["vessel"]["fixed_load_stacks"] if s["id"] == "00301H")
    stack["base_z_m"] += 0.1
    with pytest.raises(ValidationError, match="fixed_load.*level|fixed_load.*height|fixed_load.*support"):
        Instance.model_validate(payload)
