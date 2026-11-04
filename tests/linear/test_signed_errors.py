"""One-sided residuals distinguish safety error from conservative bias."""
import numpy as np
import pytest

from stowage.linear.signed_errors import signed_statistics, solver_signed_statistics


def test_signed_scalar_and_station_envelopes():
    result = signed_statistics({"gm_m": [-3., -2., .1], "sf_n": [[1., -4.], [2., -2.]]})
    assert result["lower_unsafe"]["gm_m"] == .1
    assert result["lower_conservatism"]["gm_m"] == 3.
    assert result["upper_unsafe"]["gm_m"] == 3.
    assert result["upper_conservatism"]["gm_m"] == .1
    assert result["lower_unsafe"]["sf_n"] == [2., 0.]
    assert result["upper_unsafe"]["sf_n"] == [0., 4.]
    assert result["p99"]["lower_unsafe"]["gm_m"] == pytest.approx(.098)


@pytest.mark.parametrize("values", [[], 1., [np.nan], [np.inf]])
def test_bad_residuals_fail_closed(values):
    with pytest.raises(ValueError, match="finite nonempty"):
        signed_statistics({"gm_m": values})


def test_solver_list_statistics_exclude_opposite_branch_and_gm_rejections():
    residuals = {"list_rad": [.8, -.7, .03, -.04, 9., -8., .01],
                 "gm_m": [-.2, -.1, .01, .02, .03, .04, -.3]}
    result = solver_signed_statistics(residuals, exact_list_rad=[.1, -.1, -.2, .2, -.3, .3, 0.],
                                      affine_gm_m=[1., 1., 1., 1., .1, .1, 1.], gm_min_m=.15)
    assert result["lower_unsafe"]["list_rad"] == .03
    assert result["upper_unsafe"]["list_rad"] == .04
    assert result["lower_conservatism"]["list_rad"] == .7
    assert result["upper_conservatism"]["list_rad"] == .8
    assert result["p99"]["lower_unsafe"]["list_rad"] == pytest.approx(.0297)
    assert result["list_branch_counts"] == dict(total=7, gm_eligible=5, gm_rejected=2,
                                                 lower=2, upper=2, zero_exact_heel=1)
    assert result["lower_unsafe"]["gm_m"] == .04


def test_empty_solver_list_branches_are_zero_with_explicit_counts():
    result = solver_signed_statistics({"list_rad": [3., -2.]}, exact_list_rad=[-1., 1.],
                                      affine_gm_m=[.01, .02], gm_min_m=.15)
    for direction in ("lower_unsafe", "upper_unsafe", "lower_conservatism", "upper_conservatism"):
        assert result[direction]["list_rad"] == result["p99"][direction]["list_rad"] == 0.
    assert result["list_branch_counts"]["gm_eligible"] == 0


def test_gm_minimum_equality_remains_eligible():
    result = solver_signed_statistics({"list_rad": [.1]}, exact_list_rad=[-.2],
                                      affine_gm_m=[.15], gm_min_m=.15)
    assert result["lower_unsafe"]["list_rad"] == .1
    assert result["list_branch_counts"]["lower"] == 1


@pytest.mark.parametrize("change", [dict(exact_list_rad=[np.nan]), dict(affine_gm_m=[np.inf]),
                                     dict(exact_list_rad=[]), dict(gm_min_m=0), dict(gm_min_m=-1)])
def test_bad_solver_list_inputs_fail_closed(change):
    kwargs = dict(exact_list_rad=[.1], affine_gm_m=[1.], gm_min_m=.15)
    kwargs.update(change)
    with pytest.raises(ValueError, match="conditional list"):
        solver_signed_statistics({"list_rad": [.1]}, **kwargs)
