"""Analytic checks for the independent scanline method (no src imports)."""
import importlib.util
import math
from pathlib import Path
import sys

import numpy as np
import pytest


spec = importlib.util.spec_from_file_location("large_angle_independent_geometry", Path(__file__).with_name("independent_geometry.py"))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ScanlineHull = module.ScanlineHull


def box(length=100., beam=20., depth=15.):
    polygon = np.array([[-beam/2, 0.], [beam/2, 0.], [beam/2, depth], [-beam/2, depth]])
    nodes, weights = np.polynomial.legendre.leggauss(4)
    return ScanlineHull((nodes+1)*length/2, weights*length/2, [polygon]*4, lpp_m=length)


def test_box_upright_exact_volume_and_centroid():
    b = box().submerged(7., 7.)
    assert b.volume_m3 == pytest.approx(14000., abs=1e-9)
    assert (b.lcb_m, b.tcb_m, b.vcb_m) == pytest.approx((50., 0., 3.5), abs=1e-12)


def test_box_wall_sided_closed_form_at_thirty_degrees():
    # Exact wall-sided expression, valid before deck/keel corner immersion.
    phi, draft, kg = math.radians(30), 7., 5.
    bm = 20.**2/(12*draft)
    expected = (draft/2+bm-kg+bm*math.tan(phi)**2/2)*math.sin(phi)
    r = box().float_at_heel(30., 14000., 50., kg_m=kg)
    assert r["lever_m"] == pytest.approx(expected, abs=1e-10)
    assert r["trim_m"] == pytest.approx(0., abs=1e-9)
    assert abs(r["volume_residual_m3"]) < 1e-7


@pytest.mark.parametrize("degrees", [50., 75., -75.])
def test_box_large_angle_closed_form_through_centre(degrees):
    # Half-volume rectangle, high enough heel that both intersections are on
    # opposite vertical sides: integrate y from z*cot(phi) to half-beam.
    phi = math.radians(abs(degrees))
    a, b = 10., 7.5
    cot = 1/math.tan(phi)
    y = a/2-b*b*cot*cot/(6*a)
    z_from_centre = -b*b*cot/(3*a)
    expected = y*math.cos(phi)+z_from_centre*math.sin(phi)
    expected *= math.copysign(1., degrees)
    r = box().float_at_heel(degrees, 15000., 50., kg_m=7.5)
    assert r["lever_m"] == pytest.approx(expected, abs=2e-10)


def test_concave_section_scanline_multiple_wet_intervals():
    # An upside-down U: scanlines at z<2 intersect two disjoint legs.
    polygon = np.array([[-3., 0.], [-1., 0.], [-1., 2.], [1., 2.],
                        [1., 0.], [3., 0.], [3., 4.], [-3., 4.]])
    hull = ScanlineHull([5.], [10.], [polygon], lpp_m=10.)
    b = hull.submerged(3., 3.)
    assert b.volume_m3 == pytest.approx(140.)
    assert (b.tcb_m, b.vcb_m) == pytest.approx((0., 23./14.))


def test_pinned_raw_source_and_kcs_small_heel_symmetry():
    hull = ScanlineHull.from_kcs_fix(quadrature_order=2, max_panel_length_m=8.)
    upright = hull.submerged(10.8, 10.8)
    assert upright.volume_m3 == pytest.approx(52030., rel=.005)
    positive = hull.float_at_heel(10., upright.volume_m3, upright.lcb_m, kg_m=14.38)
    negative = hull.float_at_heel(-10., upright.volume_m3, upright.lcb_m, kg_m=14.38)
    assert positive["lever_m"] == pytest.approx(-negative["lever_m"], abs=1e-10)
    assert positive["trim_m"] == pytest.approx(negative["trim_m"], abs=1e-9)
