"""Container mass ratings are facts from the individual container, not its size."""

import pytest
from pydantic import ValidationError

from stowage.conventions import make_container_id
from stowage.model.cargo import Container


def cargo(**updates):
    payload = dict(
        id=make_container_id("QZXU", 1), iso_type="22G1", length_ft=20,
        height_m=2.591, tare_kg=2000, gross_mass_kg=24000, pol="THLCH", pod="THBKK",
    )
    payload.update(updates)
    return Container.model_validate(payload)


@pytest.mark.parametrize("gross_mass_kg", [30480.001, 50000, 1000000])
def test_container_rejects_gross_above_certified_maximum(gross_mass_kg):
    with pytest.raises(ValidationError, match="gross_mass_kg must not exceed max_gross_mass_kg"):
        cargo(gross_mass_kg=gross_mass_kg, max_gross_mass_kg=30480)


@pytest.mark.parametrize("gross_mass_kg", [2000, 24000, 30480])
def test_container_accepts_gross_at_or_below_certified_maximum(gross_mass_kg):
    box = cargo(gross_mass_kg=gross_mass_kg, max_gross_mass_kg=30480)
    assert box.max_gross_mass_kg == 30480
    assert Container.model_validate_json(box.model_dump_json()) == box


@pytest.mark.parametrize("max_gross_mass_kg", [0, -1, float("inf"), float("nan")])
def test_container_certified_maximum_is_positive_and_finite(max_gross_mass_kg):
    with pytest.raises(ValidationError, match="max_gross_mass_kg"):
        cargo(max_gross_mass_kg=max_gross_mass_kg)


def test_container_legacy_missing_rating_is_unknown_without_size_inference():
    box = cargo(gross_mass_kg=1000000)
    assert box.max_gross_mass_kg is None
    assert Container.model_validate_json(box.model_dump_json()) == box


def test_container_rating_is_individual_even_for_equal_iso_size():
    assert cargo(gross_mass_kg=50000, max_gross_mass_kg=50000).gross_mass_kg == 50000


def test_container_gross_mass_is_finite():
    with pytest.raises(ValidationError, match="gross_mass_kg"):
        cargo(gross_mass_kg=float("inf"))
