"""D49's unfiltered LIST interior evidence needs independent valid seeds."""
from copy import deepcopy

import pytest

from stowage.linear import margin_policy as policy
from tests.linear.test_boundary_list_policy import boundary_context


def _necessary_boundary(context):
    _, _, _, bank = context
    boundary = deepcopy(bank["entries"]["boundary-response"]["boundary_list"])
    boundary["direction"] = "necessary"
    boundary["residual_definition"] = "exact outward capacity minus affine capacity"
    boundary["measured_optimistic_kgm"] = boundary.pop("measured_unsafe_kgm")
    boundary["interior_qualification"] = {
        "independent_holdout_passed": True,
        "affine_gm_filter_applied": False,
        "calibration_seed": 11,
        "holdout_seed": 12,
        "calibration_samples": 128,
        "holdout_samples": 64,
        "max_holdout_excess_kgm": {side: 0. for side in policy._SIDES},
    }
    boundary["builder_inputs_hash"] = policy._boundary_builder_inputs_hash(boundary)
    return boundary


def _validate(context, boundary):
    _, model, diagnostics, _ = context
    return policy._boundary_list(boundary, model, response="boundary-response",
        reference=diagnostics["reference_identity"], domain=diagnostics["family_domain"],
        direction="necessary")


@pytest.mark.parametrize("field", ["calibration_seed", "holdout_seed"])
@pytest.mark.parametrize("value", [None, -1, True, 1.5, "13"])
def test_necessary_interior_rejects_invalid_independence_seed(boundary_context, field, value):
    boundary = _necessary_boundary(boundary_context)
    boundary["interior_qualification"][field] = value
    with pytest.raises(ValueError, match="independent interior"):
        _validate(boundary_context, boundary)


def test_necessary_interior_rejects_missing_independence_seed(boundary_context):
    boundary = _necessary_boundary(boundary_context)
    del boundary["interior_qualification"]["calibration_seed"]
    with pytest.raises(ValueError, match="independent interior"):
        _validate(boundary_context, boundary)


def test_necessary_interior_rejects_reused_calibration_seed(boundary_context):
    boundary = _necessary_boundary(boundary_context)
    boundary["interior_qualification"]["holdout_seed"] = 11
    with pytest.raises(ValueError, match="independent interior"):
        _validate(boundary_context, boundary)


def test_necessary_interior_accepts_distinct_nonnegative_integer_seeds(boundary_context):
    boundary = _necessary_boundary(boundary_context)
    boundary["interior_qualification"]["calibration_seed"] = 0
    result = _validate(boundary_context, boundary)
    assert set(result) == set(policy._SIDES)
