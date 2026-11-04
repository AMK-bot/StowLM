"""D74 rates are explicit data; an absent legacy system has no numeric default."""

import json

import pytest
from pydantic import ValidationError

import stowage.model as model
from stowage.model.fixtures import build_instance


RATES = {"ballast_pump_m3_h": 300., "heel_transfer_m3_h": 150., "max_cranes": 2}


def test_ballast_system_explicit_model_and_json_round_trip():
    rates = model.BallastSystem(**RATES, assumption="APPROXIMATION: synthetic; Q23")
    vessel = build_instance().vessel.model_copy(update={"ballast_system": rates})
    restored = model.Vessel.model_validate_json(vessel.model_dump_json())
    assert restored.ballast_system == rates
    assert restored.ballast_system.max_cranes == 2


@pytest.mark.parametrize("field", RATES)
def test_supplied_ballast_system_requires_every_rate(field):
    values = {key: value for key, value in RATES.items() if key != field}
    with pytest.raises(ValidationError):
        model.BallastSystem(**values)


@pytest.mark.parametrize("field", RATES)
@pytest.mark.parametrize("value", [0, -1, None, True, float("nan"), float("inf")])
def test_ballast_system_rejects_nonpositive_missing_boolean_and_nonfinite_values(field, value):
    with pytest.raises(ValidationError):
        model.BallastSystem(**{**RATES, field: value})


def test_ballast_system_rejects_fractional_crane_count():
    with pytest.raises(ValidationError):
        model.BallastSystem(**{**RATES, "max_cranes": 2.5})


def test_legacy_vessel_omits_absent_ballast_system_in_both_serializers():
    vessel = build_instance().vessel
    assert vessel.ballast_system is None
    assert "ballast_system" not in vessel.model_dump()
    assert "ballast_system" not in json.loads(vessel.model_dump_json())
    explicit_null = model.Vessel.model_validate({**vessel.model_dump(), "ballast_system": None})
    assert explicit_null.model_dump_json() == vessel.model_dump_json()
