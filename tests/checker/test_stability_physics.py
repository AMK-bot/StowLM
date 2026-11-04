"""Independent buoyancy checks underlying the exact intact-stability checker.

A rectangular prism has KB=T/2, BM=B²/(12T), and (until deck-edge
immersion or bottom emergence) GZ=sin(phi)[GM+BM*tan²(phi)/2]. These
identities are independent of the numerical polygon clipping/solver. Source:
Biran & López-Pulido, Ship Hydrostatics and Stability (2014), wall-sided
formula. IACS UR L5 Rev.4 §5.2 Table 1 gives the comparison tolerances;
its two-part tolerances use the larger term. Our known-solid checks are tighter.
These are numerical verification tests, not independent real-ship approval.
"""

import math

import numpy as np
import pytest
from scipy.optimize import brentq

from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry


def box_geometry():
    """Exact 80×16×12 m prism, supplied as two identical Gauss sections.

    HullGeometry.from_params describes the fleet's tapered family only. Build
    this independent test solid directly from its documented section quadrature;
    no taper, bilge or flare is approximated to imitate a box.
    """
    g = object.__new__(HullGeometry)
    g.lpp_m, g.beam_m, g.depth_m = 80.0, 16.0, 12.0
    g.design_draught_m = 5.0
    g.density_kg_m3 = 1025.0
    g.max_z_m, g.openings = 12.0, []
    g.x_m = np.array([40 - 40 / math.sqrt(3), 40 + 40 / math.sqrt(3)])
    g.integration_weights_m = np.array([40.0, 40.0])
    section = np.array([[8., 0.], [8., 12.], [-8., 12.], [-8., 0.]])
    g.sections = [[section.copy()], [section.copy()]]
    # Manual test solids also need the geometry fingerprint used by task 2.1's
    # exact-input cache; it fingerprints these box offsets, not fleet params.
    g._finish_geometry()
    return g


@pytest.mark.parametrize("draught", [2., 5., 9.])
def test_box_kb_bm_and_displacement_closed_form(draught):
    g = box_geometry()
    s = g.submerged(draught, draught)
    expected_volume = 80 * 16 * draught
    expected_kb = draught / 2
    expected_bm = 16**2 / (12 * draught)
    assert s.volume_m3 == pytest.approx(expected_volume, rel=1e-12)
    assert s.displacement_kg == pytest.approx(expected_volume * 1025, rel=1e-12)
    assert s.vcb_m == pytest.approx(expected_kb, abs=1e-12)
    assert s.bm_t_m == pytest.approx(expected_bm, rel=1e-12)
    assert s.vcb_m + s.bm_t_m == pytest.approx(expected_kb + expected_bm)
    assert abs(s.vcb_m - expected_kb) < max(.01 * expected_kb, .05)  # L5


@pytest.mark.parametrize("degrees", [.1, 2., 8., 15.])
def test_box_free_trim_gz_against_wall_sided_formula(degrees):
    g = box_geometry()
    draught, kg = 5., 4.
    phi = math.radians(degrees)
    bm = 16**2 / (12 * draught)
    gm = draught / 2 + bm - kg
    expected = (gm + .5 * bm * math.tan(phi)**2) * math.sin(phi)
    actual = g.kn(phi, 80 * 16 * draught * 1025, lcg_m=40) - kg * math.sin(phi)
    assert actual == pytest.approx(expected, abs=2e-8)
    assert abs(actual - expected) < max(.05 * abs(expected), .05)  # L5


def test_box_equilibrium_recovers_trim_heel_and_fresh_water_draught():
    g = box_geometry()
    aft, forward, phi, rho = 5.3, 4.7, math.radians(3), 1000.
    s = g.submerged(aft, forward, phi, rho)
    # Put G on the upward normal through B: exact force and moment equilibrium.
    centre = np.asarray(s.centroid_m) + 1.5 * g.waterplane_normal(aft, forward, phi)
    q = g.equilibrium(s.displacement_kg, *centre, density_kg_m3=rho)
    assert q.t_aft_m == pytest.approx(aft, abs=1e-5)
    assert q.t_fwd_m == pytest.approx(forward, abs=1e-5)
    assert q.heel_rad == pytest.approx(phi, abs=1e-6)
    assert q.submerged.displacement_kg == pytest.approx(s.displacement_kg, rel=1e-8)
    assert abs(q.heel_rad - phi) < math.radians(1)  # IACS L5 equilibrium angle


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("loading", ["light", "design", "scantling"])
def test_all_feeders_equilibrate_across_operating_draughts(name, loading):
    p = load_ship_params(name)
    h = p["hull"]
    g = HullGeometry.from_params(p)
    if loading == "light":
        draught = brentq(lambda t: g.submerged(t, t).displacement_kg - h["lightship_mass_kg"],
                         .01, h["design_draught_m"])
    else:
        draught = h[f"{loading}_draught_m"]
    s = g.submerged(draught, draught)
    # Known even-keel loading: G directly above B. This tests solver range
    # independently of whether a particular cargo manifest is operationally safe.
    q = g.equilibrium(s.displacement_kg, s.lcb_m, 0., s.vcb_m + .2)
    assert q.t_aft_m == pytest.approx(draught, abs=2e-5)
    assert q.t_fwd_m == pytest.approx(draught, abs=2e-5)
    assert q.heel_rad == pytest.approx(0., abs=2e-6)
    assert q.residual_norm < 2e-6
    assert q.submerged.displacement_kg == pytest.approx(s.displacement_kg, rel=2e-7)


def test_batched_checker_geometry_matches_independent_section_clipping():
    from stowage.checker.stability_geometry import StabilityGeometry

    p = load_ship_params("feeder_500")
    reference = HullGeometry.from_params(p)
    fast = StabilityGeometry.from_params(p)
    for degrees in (0., 5., 30., 60.):
        phi = math.radians(degrees)
        a = reference.submerged(5.3, 4.7, phi, 1000.)
        b = fast.submerged(5.3, 4.7, phi, 1000.)
        assert b.volume_m3 == pytest.approx(a.volume_m3, rel=1e-11)
        assert b.centroid_m == pytest.approx(a.centroid_m, abs=2e-10)
        full = fast.full_submerged(5.3, 4.7, phi, 1000.)
        assert full.bm_t_m == pytest.approx(a.bm_t_m, rel=1e-11)


def test_batched_free_trim_curve_matches_box_wall_sided_formula():
    from stowage.checker.stability_geometry import StabilityGeometry

    reference = box_geometry()
    fast = object.__new__(StabilityGeometry)
    fast.__dict__.update(reference.__dict__)
    fast._p = np.array([sections[0] for sections in reference.sections])
    fast._q = np.roll(fast._p, -1, axis=1)
    fast._px = fast.x_m
    fast._pw = fast.integration_weights_m
    fast._prepare_edges()
    angles = np.radians([0., .1, 2., 8., 15.])
    rho, draught, kg = 1000., 5., 4.
    mass = 80 * 16 * draught * rho
    actual, attitudes = fast.free_trim_curve(angles, mass, 40., kg, rho)
    bm = 16**2 / (12 * draught)
    gm = draught / 2 + bm - kg
    expected = (gm + .5 * bm * np.tan(angles)**2) * np.sin(angles)
    assert actual == pytest.approx(expected, abs=2e-8)
    assert attitudes == pytest.approx(np.full((len(angles), 2), draught), abs=1e-7)
