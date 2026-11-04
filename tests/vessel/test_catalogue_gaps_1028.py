"""D20 public KCS validation with explicit accepted/rejected comparisons.

These tests add independent displacement, draft and GM evidence. They do not
close the complete L5 row: the published KCS data has no GZ/area curve, tank
FSM reference or inclined equilibrium condition. Its model KG/GM columns are
scaled by geometric similarity, as documented in D20, without fitting offsets.
The existing validation experiment's comparator is exercised; this is not a
claim that the departure checker performs class approval or emits an L5 ID.
"""

import runpy

import pytest

from stowage.params import repo_root
from stowage.rules.catalogue import get_rule
from stowage.vessel.hydrostatics import HullGeometry


@pytest.fixture(scope="module")
def kcs_validation():
    return runpy.run_path(str(repo_root() / "experiments/hydrostatics/validate_kcs.py"))


@pytest.fixture(scope="module")
def kcs_geometries(kcs_validation):
    geometries = {}
    for model in ("published", "density_error", "beam_error"):
        params = kcs_validation["reference_params"]()
        if model == "density_error":
            params["hull"]["water_density_kg_m3"] *= 1.03
        if model == "beam_error":
            params["hull"]["beam_m"] *= .98
        geometries[model] = HullGeometry.from_params(params, nx=96, nz=128)
    return geometries


def _comparison(kcs_validation, actual, reference, relative, absolute=0.):
    return kcs_validation["compare"](
        actual, reference, max(relative * abs(reference), absolute))


@pytest.mark.parametrize("model", ["published", "density_error"])
def test_l5_kcs_published_displacement_accepts_and_density_error_rejects(
        model, kcs_geometries, kcs_validation):
    geometry = kcs_geometries[model]
    # Tokyo 2015 kcs_gc.html, full-scale displacement volume at T=10.8m.
    # ASSUMPTION: seawater density 1025kg/m3, the explicit D20 reference input.
    reference = 52_030. * 1025.
    actual = geometry.submerged(10.8, 10.8).displacement_kg
    tolerance = get_rule("iacs.l5.validation").parameters["displacement_relative"].value
    check = _comparison(kcs_validation, actual, reference, tolerance)
    assert check["passes"] is (model == "published"), check
    if model == "density_error":
        assert abs(actual - reference) > .02 * reference


@pytest.mark.parametrize("model", ["published", "beam_error"])
@pytest.mark.parametrize("kg_model,gm_model,length_model", [
    (.378, .0158, 6.0702), (.168, .007, 2.7),
])
def test_l5_kcs_published_gm_accepts_and_beam_error_rejects(
        model, kcs_geometries, kcs_validation, kg_model, gm_model, length_model):
    geometry = kcs_geometries[model]
    # Independent published KG and GM columns, not production-derived targets.
    scale = 230. / length_model
    kg, reference_gm = kg_model * scale, gm_model * scale
    actual_gm = geometry.hydrostatic_row(10.8, kg_m=kg).kmt_m - kg
    parameters = get_rule("iacs.l5.validation").parameters
    check = _comparison(kcs_validation, actual_gm, reference_gm,
                        parameters["kg_gmt_relative"].value,
                        parameters["kg_gmt_absolute"].value)
    assert check["passes"] is (model == "published"), check
    if model == "beam_error":
        assert abs(actual_gm - reference_gm) > .05


@pytest.mark.parametrize("model", ["published", "density_error"])
def test_l5_kcs_published_draft_accepts_and_density_error_rejects(
        model, kcs_geometries, kcs_validation):
    geometry = kcs_geometries[model]
    # Published displacement/LCB plus model-2 KG specify the same independently
    # given even-keel condition; no computed displacement is fed back as truth.
    equilibrium = geometry.equilibrium(
        52_030. * 1025., 230. * (.5 - .0148), 0., .378 * 230. / 6.0702)
    parameters = get_rule("iacs.l5.validation").parameters
    for actual in (equilibrium.t_aft_m, equilibrium.t_fwd_m):
        check = _comparison(kcs_validation, actual, 10.8,
                            parameters["draft_relative"].value,
                            parameters["draft_absolute"].value)
        assert check["passes"] is (model == "published"), check
