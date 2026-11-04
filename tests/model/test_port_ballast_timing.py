"""D74 timing and D75 berth depth remain optional, explicit physical inputs."""

import pytest
from pydantic import ValidationError

from stowage.model.fixtures import build_instance
from stowage.model.port import PortProfile, QuayCraneSpec


@pytest.mark.parametrize("field", ["gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h"])
def test_optional_crane_timing_is_positive_finite_and_round_trips(field):
    legacy = QuayCraneSpec(count=2, max_on_deck_tiers=8)
    assert getattr(legacy, field) is None
    assert field not in legacy.model_dump(mode="json")
    supplied = QuayCraneSpec(count=2, max_on_deck_tiers=8, **{field: 0.5})
    assert getattr(supplied, field) == 0.5
    assert QuayCraneSpec.model_validate_json(supplied.model_dump_json()) == supplied
    for value in (0.0, -1.0, float("nan"), float("inf"), True):
        with pytest.raises(ValidationError):
            QuayCraneSpec(count=2, max_on_deck_tiers=8, **{field: value})


def test_crane_count_per_berth_property_preserves_legacy_json():
    crane = QuayCraneSpec(count=2, max_on_deck_tiers=8)
    assert crane.count_per_berth == crane.count == 2
    assert "count_per_berth" not in crane.model_dump()
    assert crane.model_dump_json() == '{"count":2,"max_on_deck_tiers":8,"max_lift_height_m":null,"outreach_rows":null}'


def test_optional_berth_depth_is_positive_finite_and_omitted_when_unknown():
    raw = build_instance().port.model_dump()
    legacy_json = PortProfile.model_validate(raw).model_dump_json()
    assert "berth_depth_m" not in legacy_json
    profile = PortProfile.model_validate({**raw, "max_draught_m": 8.0})
    assert profile.berth_depth_m == 8.0
    assert PortProfile.model_validate_json(profile.model_dump_json()) == profile
    for value in (0.0, -1.0, float("nan"), float("inf"), True):
        with pytest.raises(ValidationError):
            PortProfile.model_validate({**raw, "max_draught_m": value})
