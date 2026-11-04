"""Strength consumes authoritative hull-tank quadrature without inventing panels."""
import numpy as np
import pytest

from stowage.checker.strength import (
    G,
    _section_tank_point_loads,
    _tank_liquid,
    integrate_strength,
)
from stowage.vessel.hull_tanks import section_geometry_from_params


@pytest.fixture
def section_tank():
    # Three unmatched stations with gaps deliberately cannot define Gauss panels.
    # Different section widths make a uniform spread over the box incorrect.
    sections = [
        [[[-width / 2, 0.], [width / 2, 0.],
          [width / 2, 2.], [-width / 2, 2.]]]
        for width in (2., 4., 1.)
    ]
    return section_geometry_from_params({
        'shape': 'hull_sections',
        'x_min_m': 0., 'x_max_m': 20.,
        'y_min_m': -2., 'y_max_m': 2.,
        'z_min_m': 0., 'z_max_m': 2.,
        'x_m': [2., 9., 16.],
        'integration_weights_m': [.7, 1.2, .5],
        'sections_yz_m': sections,
    })


@pytest.mark.parametrize('fill,heel,trim', [
    (0., .1, -.03), (.13, -.16, .05), (.5, .09, -.025),
    (.91, -.04, .04), (1., .13, -.05),
])
def test_section_loads_keep_authoritative_mass_and_first_moment(
        section_tank, fill, heel, trim):
    volume = fill * section_tank.capacity_m3
    density = 1025.
    liquid = section_tank.liquid(volume, heel, trim)
    xs, weights, areas = section_tank.section_liquid_areas(volume, heel, trim)
    loads = np.asarray(_section_tank_point_loads(
        section_tank, volume, heel, trim, density)).reshape(-1, 2)
    # Do not interpolate across gaps, pair unmatched stations, or replace the
    # nonuniform transverse sections with the bounding rectangular prism.
    wet = areas > 0.
    np.testing.assert_array_equal(loads[:, 0], xs[wet])
    assert loads[:, 1] == pytest.approx(density * weights[wet] * areas[wet], abs=1e-10)
    assert loads[:, 1].sum() == pytest.approx(density * volume, abs=1e-8)
    assert loads[:, 0] @ loads[:, 1] == pytest.approx(
        density * volume * liquid.centroid_m[0], abs=1e-7)
    actual = _tank_liquid(section_tank, volume, heel, trim)
    assert actual == liquid


def test_nonuniform_section_tank_beam_matches_discrete_force_reference(section_tank):
    density = 1000.
    volume = section_tank.capacity_m3 / 2
    loads = _section_tank_point_loads(section_tank, volume, 0., 0., density)
    # Each rectangle is half full at z=1 m: its wet area equals its width.
    expected_x = np.array([2., 9., 16.])
    expected_mass = density * np.array([.7 * 2., 1.2 * 4., .5 * 1.])
    assert np.asarray(loads) == pytest.approx(
        np.column_stack((expected_x, expected_mass)), abs=1e-9)
    stations = np.array([0., 1., 2., 5., 9., 12., 16., 19., 20.])
    # A zero-buoyancy box isolates liquid weight; closure is disabled so the
    # reference is the direct cumulative force and lever-arm moment.
    gauss_x = 10. + np.array([-1., 1.]) * 10. / np.sqrt(3)
    curves = integrate_strength(
        gauss_x, [10., 10.], [0., 0.], [], loads,
        stations, lpp_m=20., close=False)
    expected_sf = np.array([
        -G * expected_mass[expected_x <= x].sum() for x in stations])
    expected_bm = np.array([
        G * (expected_mass * np.maximum(x - expected_x, 0.)).sum()
        for x in stations])
    assert curves.shear_n == pytest.approx(expected_sf, abs=1e-7)
    assert curves.bending_nm == pytest.approx(expected_bm, abs=1e-7)
    # Uniform spreading would place weight before the first recorded station.
    assert curves.shear_n[1] == 0.
    assert curves.bending_nm[1] == 0.
