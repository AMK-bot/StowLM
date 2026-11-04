"""Pin the SW1/SW2 physical thresholds in the catalogue identity."""

import pytest

from stowage.rules.catalogue import load_catalogue


@pytest.mark.parametrize(
    ("name", "value", "unit"),
    [
        ("sw1_minimum_distance_m", 2.4, "m"),
        ("sw2_minimum_distance_m", 3.0, "m"),
        ("sw1_surface_temperature_threshold_k", 328.15, "K"),
    ],
)
def test_imdg_placement_thresholds_have_primary_source_and_si_units(name, value, unit):
    parameter = load_catalogue()["imdg.stowage.3_2"].parameters[name]
    assert parameter.value == value
    assert parameter.unit == unit
    assert "MSC.556(108)" in parameter.provenance.document
    assert parameter.provenance.clause == "7.1.2"
    assert "477" in str(parameter.provenance.page)
