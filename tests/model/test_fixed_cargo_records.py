"""D42 per-box background cargo and its aggregate accounting contract."""

import pytest
from pydantic import ValidationError

from stowage.model.vessel import FixedCargoBox, FixedLoad


def box_data(**changes):
    return dict(cell_ids=["0010182"], length_ft=20, height_m=2.591,
                gross_mass_kg=10000.0, x_m=80.0, y_m=-1.0, z_m=8.0,
                stack_id="00101D", tier=82,
                provenance={"created_by": "synthetic-test"}) | changes


def load_data(**changes):
    return dict(x_m=80.0, y_m=-1.0, z_m=8.0, mass_kg=10000.0,
                kind="cargo", cargo_boxes=[box_data()]) | changes


def test_positive_cargo_cannot_use_zero_aggregate_mass_within_float_tolerance():
    with pytest.raises(ValidationError, match="mass_kg"):
        FixedLoad(**load_data(mass_kg=0.0, cargo_boxes=[box_data(gross_mass_kg=1e-7)]))


@pytest.mark.parametrize("length", [20, 40, 45])
def test_fixed_cargo_record_round_trip(length):
    cells = ["0010182"] if length == 20 else ["0030182", "0010182"]
    record = FixedCargoBox(**box_data(length_ft=length, cell_ids=cells))
    assert record.cell_ids == sorted(cells)
    assert record.provenance.created_by == "synthetic-test"
    assert FixedCargoBox.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize("change,field", [
    ({"cell_ids": []}, "cell_ids"),
    ({"cell_ids": ["0010182", "0010182"]}, "cell_ids"),
    ({"cell_ids": ["0020182"]}, "cell_ids"),
    ({"length_ft": 40}, "cell_ids"),
    ({"length_ft": 40, "cell_ids": ["0010182", "0050182"]}, "cell_ids"),
    ({"length_ft": 40, "cell_ids": ["0010182", "0030282"]}, "cell_ids"),
    ({"length_ft": 40, "cell_ids": ["0010182", "0030184"]}, "cell_ids"),
    ({"length_ft": 21}, "length_ft"),
    ({"stack_id": "00301D"}, "stack_id"),
    ({"tier": 84}, "tier"),
    ({"tier": 0}, "tier"),
    ({"tier": 83}, "tier"),
    ({"tier": 100}, "tier"),
    ({"tier": "82"}, "tier"),
    ({"tier": True}, "tier"),
])
def test_fixed_cargo_record_rejects_invalid_address(change, field):
    with pytest.raises(ValidationError, match=field):
        FixedCargoBox(**box_data(**change))


@pytest.mark.parametrize("field", ["height_m", "gross_mass_kg", "x_m", "y_m", "z_m"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "1", True])
def test_fixed_cargo_record_requires_finite_typed_values(field, value):
    with pytest.raises(ValidationError, match=field):
        FixedCargoBox(**box_data(**{field: value}))


@pytest.mark.parametrize("field", ["height_m", "gross_mass_kg"])
@pytest.mark.parametrize("value", [0.0, -1.0])
def test_fixed_cargo_record_requires_positive_dimensions_and_mass(field, value):
    with pytest.raises(ValidationError, match=field):
        FixedCargoBox(**box_data(**{field: value}))


def test_fixed_cargo_record_signed_coordinates_and_default_provenance():
    data = box_data(x_m=-80.0, y_m=0.0, z_m=-8.0)
    del data["provenance"]
    assert FixedCargoBox(**data).provenance.stubs == {}


def test_fixed_load_round_trips_background_records():
    load = FixedLoad(**load_data())
    assert FixedLoad.model_validate_json(load.model_dump_json()) == load
    assert tuple(load) == (80.0, -1.0, 8.0, 10000.0)


def test_fixed_load_legacy_missing_kind_is_unknown():
    assert FixedLoad.model_validate([80, 0, 8, 10000]).kind == "unknown"
    assert FixedLoad(x_m=80, y_m=0, z_m=8, mass_kg=10000).kind == "unknown"
    assert FixedLoad(x_m=80, y_m=0, z_m=8, mass_kg=10000, kind="other").kind == "other"
    assert FixedLoad(x_m=80, y_m=0, z_m=8, mass_kg=10000, kind="cargo").cargo_boxes is None


@pytest.mark.parametrize("kind", ["non_cargo", "unknown", "other"])
def test_fixed_load_rejects_cargo_boxes_on_other_kinds(kind):
    with pytest.raises(ValidationError, match="cargo_boxes"):
        FixedLoad(**load_data(kind=kind))


def test_fixed_load_accepts_declared_non_cargo():
    assert FixedLoad(x_m=80, y_m=0, z_m=8, mass_kg=10000, kind="non_cargo").cargo_boxes is None


@pytest.mark.parametrize("change,field", [
    ({"mass_kg": 10001.0}, "mass_kg"),
    ({"cargo_boxes": []}, "mass_kg"),
    ({"x_m": 81.0}, "x_m"),
    ({"y_m": 0.0}, "y_m"),
    ({"z_m": 9.0}, "z_m"),
])
def test_fixed_load_rejects_inconsistent_aggregate(change, field):
    with pytest.raises(ValidationError, match=field):
        FixedLoad(**load_data(**change))


def test_fixed_load_mass_weighted_cg_and_tolerances():
    boxes = [box_data(), box_data(cell_ids=["0030182"], stack_id="00301D",
                                 gross_mass_kg=30000.0, x_m=120.0, y_m=3.0, z_m=12.0)]
    load = FixedLoad(**load_data(cargo_boxes=boxes, mass_kg=40000.0 + 1e-5,
                                x_m=110.0 + 1e-7, y_m=2.0, z_m=11.0))
    assert len(load.cargo_boxes) == 2


def test_fixed_load_handles_large_finite_moments_without_product_overflow():
    load = FixedLoad(**load_data(cargo_boxes=[box_data(gross_mass_kg=1e200, x_m=1e200)],
                                mass_kg=1e200, x_m=1e200))
    assert load.x_m == 1e200


def test_fixed_load_rejects_overflowed_aggregate_mass():
    boxes = [box_data(gross_mass_kg=1e308), box_data(gross_mass_kg=1e308,
             cell_ids=["0030182"], stack_id="00301D")]
    with pytest.raises(ValidationError, match="mass_kg"):
        FixedLoad(**load_data(cargo_boxes=boxes, mass_kg=1e308))
