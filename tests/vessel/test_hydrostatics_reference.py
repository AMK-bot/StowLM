"""Independent analytic checks of the declared solid, not real-ship approval.

The closed-form integrals here come from the piecewise geometry printed in
params/fleet/README.md; production code must integrate sections instead.
Bardu provides one waterplane-area anchor only (NOTES.md, PDF page 2).
IACS UR L5 tolerances are a comparison bar, not evidence of a known real hull.
"""

import math
import runpy

import pytest

from stowage.params import repo_root
from stowage.vessel.hydrostatics import HullGeometry


def historical_params(name="feeder_500"):
    module = runpy.run_path(str(repo_root() / "experiments/hydrostatics/validate.py"))
    return module["historical_family_params"](name)


@pytest.mark.parametrize("ship", ["feeder_500", "feeder_800", "feeder_1700"])
def test_upright_integrals_against_declared_family(ship):
    p = historical_params(ship)
    hull, shape = p["hull"], p["hull_form"]
    length, beam = hull["lpp_m"], hull["beam_m"]
    draught = 0.8 * hull["design_draught_m"]
    radius = shape["bilge_radius_m"]
    aft, fore = shape["aft_run_fraction"], shape["fore_run_fraction"]
    longitudinal_factor = 1 - (aft + fore) / 2
    # Missing circular bilge area relative to a full rectangular section.
    missing_area = (2 - math.pi / 2) * radius**2
    section_area = beam * draught - missing_area
    # Integral z*b(z) for two rounded bilge corners, integrated analytically.
    missing_first_moment = radius**3 * (5 / 3 - math.pi / 2)
    expected_kb = (beam * draught**2 / 2 - missing_first_moment) / section_area
    expected_volume = length * longitudinal_factor * section_area
    expected_area = length * beam * longitudinal_factor
    expected_it = beam**3 / 12 * length * (1 - 3 * (aft + fore) / 4)
    geom = HullGeometry.from_params(p, nx=48, nz=64)
    result = geom.submerged(draught, draught, 0)
    # These numerical tolerances are substantially tighter than the L5 bar.
    assert result.volume_m3 == pytest.approx(expected_volume, rel=3e-4)
    assert result.vcb_m == pytest.approx(expected_kb, abs=0.002)
    assert result.waterplane_area_m2 == pytest.approx(expected_area, rel=3e-4)
    assert result.transverse_inertia_m4 == pytest.approx(expected_it, rel=5e-4)
    assert result.displacement_kg == pytest.approx(
        result.volume_m3 * hull["water_density_kg_m3"], rel=1e-12)
    assert result.tcb_m == pytest.approx(0, abs=1e-9)
    if aft == fore:
        assert result.lcb_m == pytest.approx(length / 2, abs=1e-8)
        assert result.lcf_m == pytest.approx(length / 2, abs=1e-8)


def test_hydrostatic_derivative_bm_and_tpc_units():
    p = historical_params()
    geom = HullGeometry.from_params(p)
    t = 0.8 * p["hull"]["design_draught_m"]
    dt = 0.001
    r = geom.submerged(t, t, 0)
    plus = geom.submerged(t + dt, t + dt, 0)
    minus = geom.submerged(t - dt, t - dt, 0)
    row = geom.hydrostatic_row(t, kg_m=r.vcb_m)
    derivative = (plus.displacement_kg - minus.displacement_kg) / (2 * dt)
    expected_tpc = p["hull"]["water_density_kg_m3"] * r.waterplane_area_m2 / 100
    assert derivative / 100 == pytest.approx(expected_tpc, rel=3e-4)
    assert row.tpc_kg_per_cm == pytest.approx(expected_tpc, rel=1e-10)
    assert row.kmt_m - row.kb_m == pytest.approx(
        r.transverse_inertia_m4 / r.volume_m3, rel=1e-10)
    assert row.kmt_m == pytest.approx(
        r.vcb_m + r.transverse_inertia_m4 / r.volume_m3, rel=1e-10)
    # Stated waterplane approximation neglects the (KG-KB) contribution to GM_L.
    expected_mct = (p["hull"]["water_density_kg_m3"] * 9.80665 *
                    r.longitudinal_inertia_m4 / (100 * p["hull"]["lpp_m"]))
    assert row.mct_nm_per_cm == pytest.approx(expected_mct, rel=1e-10)

    # Independent finite-trim buoyancy moment at fixed mean draught, with
    # KG=the upright KB so the omitted (KG-KB) term vanishes at first order.
    # This checks the integrated LCB response, rather than repeating I_long.
    trim_step = 0.01
    aft_down = geom.submerged(t + trim_step / 2, t - trim_step / 2, 0)
    fwd_down = geom.submerged(t - trim_step / 2, t + trim_step / 2, 0)
    aft_moment = aft_down.displacement_kg * 9.80665 * (aft_down.lcb_m - r.lcb_m)
    fwd_moment = fwd_down.displacement_kg * 9.80665 * (fwd_down.lcb_m - r.lcb_m)
    measured_mct = abs(aft_moment - fwd_moment) / (2 * trim_step * 100)
    assert row.mct_nm_per_cm == pytest.approx(measured_mct, rel=0.002)


def test_kn_zero_and_small_angle_limit():
    p = historical_params()
    geom = HullGeometry.from_params(p, nx=48, nz=128)
    t = 0.8 * p["hull"]["design_draught_m"]
    row = geom.hydrostatic_row(t)
    assert geom.kn(0, row.displacement_kg) == pytest.approx(0, abs=1e-8)
    angle = math.radians(0.5)
    expected = row.kmt_m * math.sin(angle)
    assert geom.kn(angle, row.displacement_kg) == pytest.approx(expected, rel=0.01, abs=0.001)


def test_bardu_reference_fact_and_waterplane_conversion():
    text = (repo_root() / "data/raw/reference-ships/bardu.txt").read_text()
    assert "52.13 mt/cm (at 11.6 m)" in text
    target_kg_per_cm = 52.13 * 1000
    # Density is an explicit assumption; the owner sheet does not state it.
    implied_waterplane_m2 = target_kg_per_cm * 100 / 1025
    assert implied_waterplane_m2 == pytest.approx(5085.853658536586)
    assert implied_waterplane_m2 / (196.9 * 29.8) == pytest.approx(0.8667660241352687)


def test_bardu_illustrative_tpc_with_three_percent_sanity_tolerance():
    # APPROXIMATION: this reproduces an UNCALIBRATED feeder-family reconstruction,
    # not the unknown Bardu hull. The 3% dimensional sanity tolerance is distinct
    # from the requested 2% comparison bar, which this reconstruction misses.
    module = runpy.run_path(str(repo_root() / "experiments/hydrostatics/validate.py"))
    p = module["bardu_illustrative_params"]()
    geom = HullGeometry.from_params(p, nx=96, nz=128)
    row = geom.hydrostatic_row(11.6)
    assert row.tpc_kg_per_cm == pytest.approx(52_130, rel=0.03)
    assert abs(row.tpc_kg_per_cm / 52_130 - 1) > 0.02


@pytest.mark.parametrize("ship", ["feeder_500", "feeder_800", "feeder_1700"])
def test_generated_design_coefficients_match_parameter_targets(ship):
    module = runpy.run_path(str(repo_root() / "experiments/hydrostatics/validate.py"))
    p = historical_params(ship)
    geom = HullGeometry.from_params(p, nx=96, nz=128)
    comparison = module["coefficient_comparison"](p, geom)
    for name, actual in comparison["generated"].items():
        # Polygon chords produce a small, convergent deficit in bilge volume.
        assert actual == pytest.approx(comparison["parameter_target"][name],
                                       rel=1e-4, abs=1e-8)


def test_bardu_calibration_fits_only_taper_and_reports_independent_failure():
    module = runpy.run_path(str(repo_root() / "experiments/hydrostatics/validate.py"))
    original = module["bardu_illustrative_params"]()
    calibrated = module["bardu_calibrated_params"]()
    for taper in ("aft_run_fraction", "fore_run_fraction"):
        assert calibrated["hull_form"][taper] == pytest.approx(0.14141101382827148)
        original["hull_form"][taper] = calibrated["hull_form"][taper]
    assert calibrated == original  # All other choices unchanged.
    result = module["bardu_calibrated_comparison"]()
    assert result["fitted_tpc_kg_per_cm"] == pytest.approx(52_130, rel=1e-10)
    # This independent quantity was not used in the TPC calibration. A passing
    # regression asserts the honestly recorded failure, not successful validation.
    source = (repo_root() / "data/raw/reference-ships/bardu.txt").read_text()
    assert "34,000 mt at 11.6 m" in source
    assert "26,700 mt at 10.1 m" in source
    assert result["reference_displacement_increment_kg"] == 7_300_000
    assert result["integrated_displacement_increment_kg"] == pytest.approx(7_782_615.566, abs=1)
    assert result["increment_error_percent"] == pytest.approx(6.611172, abs=0.0001)
    assert result["increment_passes"] is False
    assert result["absolute_displacement_validated"] is False
    assert result["lightship_inferred"] is False


def test_grid_halving_changes_submerged_geometry_below_l5_bar():
    p = historical_params()
    coarse = HullGeometry.from_params(p, nx=48, nz=64)
    fine = HullGeometry.from_params(p, nx=96, nz=128)
    t = p["hull"]["design_draught_m"] * 0.8
    for angle in (0, 30, 60):
        a = coarse.submerged(t + 0.15, t - 0.15, math.radians(angle))
        b = fine.submerged(t + 0.15, t - 0.15, math.radians(angle))
        assert a.volume_m3 == pytest.approx(b.volume_m3, rel=0.002)
        assert a.vcb_m == pytest.approx(b.vcb_m, abs=0.01)
        assert a.tcb_m == pytest.approx(b.tcb_m, abs=0.01)
