"""SOLAS sector radial-range regressions, independent of conning setback."""
import math
from itertools import combinations, product
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import visibility
from stowage.model.fixtures import build_instance
from stowage.model.vessel import FixedLoad, FixedLoadSilhouette
from stowage.checker.visibility_range import range_blind_intervals


class _Plane:
    def waterplane_normal(self, *_):
        return np.array([0., 0., 1.])


def test_near_beam_below_eye_obstruction_uses_radial_range(monkeypatch):
    instance = build_instance()
    instance.onboard.placements = []
    instance.vessel.bridge.x_m = 0.
    instance.vessel.bridge.eye_z_m = 25.
    instance.vessel.fixed_loads = [FixedLoad(
        kind='non_cargo', mass_kg=0., x_m=5.5, y_m=17.5, z_m=23.,
        silhouette=FixedLoadSilhouette(x_min_m=3., x_max_m=8.,
            y_min_m=15., y_max_m=20., z_min_m=22., z_max_m=24.3))]
    condition = SimpleNamespace(t_aft_m=5., t_fwd_m=5., heel_rad=0., geometry=_Plane())
    monkeypatch.setattr(visibility, '_loading_condition', lambda *_: condition)
    report = visibility.check_visibility(instance, instance.onboard, call_index=0)
    # H=20 m, top h=19.3 m: the near-beam tangent sea range is at
    # least 20*hypot(3,15)/0.7 = 437 m > bow+limit = 384 m.
    # Its forward tangent distance stays <= 20*8/0.7 = 229 m.
    # Both inequalities hold with the separately declared 2 m aft setback.
    assert report.ran, report.error
    assert report.metrics['blind_distance_m'] == 0.
    assert report.metrics['max_blind_sector_rad'] > math.radians(10.)
    assert any('individual cargo blind sector' in v.message for v in report.violations)


def test_radial_cone_can_split_one_box_into_two_sectors():
    # Roof h=9 below H=10, so R=50 gives a radial cutoff of 5 m.
    # The rectangular footprint x in[1,2], y in[-10,10] is blind only
    # at the two outer azimuth bands, not through its central bearings.
    vertices = list(product((1., 2.), (-10., 10.), (8., 9.)))
    actual = range_blind_intervals(vertices, 10., 50.)
    start, end = math.acos(2. / 5.), math.atan2(10., 1.)
    np.testing.assert_allclose(actual, [(-end, -start), (start, end)], atol=1e-12)


def test_radial_cone_uses_face_edges_not_only_qualifying_corners():
    # The transition is inside the roof's front edge; a hull of only
    # qualifying corner azimuths would falsely cover the central clear band.
    vertices = list(product((1., 2.), (0., 10.), (8., 9.)))
    actual = range_blind_intervals(vertices, 10., 50.)
    np.testing.assert_allclose(actual, [(math.acos(.4), math.atan2(10., 1.))], atol=1e-12)


@pytest.mark.parametrize('angle', [-.05, .13])
def test_tilted_faces_preserve_analytic_azimuth_intersections(angle):
    # Tilt a zero-thickness rectangular face in z about its x axis. Then
    # y'=y*cos(a), z'=9+y*sin(a). The radial threshold on x=2 is
    # x²+(y cos(a))² = (5-5*y*sin(a))². Solve its positive root
    # independently and compare the exact interval endpoint.
    ca, sa = math.cos(angle), math.sin(angle)
    vertices = [np.array([x, y*ca, z+y*sa])
                for x, y, z in product((1., 2.), (0., 10.), (9., 9.))]
    coeff = (ca*ca-25*sa*sa, 50*sa, -21.)
    roots = np.roots(coeff)
    y = min(float(root) for root in roots if np.isreal(root) and 0. < root < 10.)
    expected_start = math.atan2(y*ca, 2.)
    expected_end = math.atan2(10.*ca, 1.)
    np.testing.assert_allclose(range_blind_intervals(vertices, 10., 50.),
                               [(expected_start, expected_end)], atol=1e-12)


def test_range_clips_aft_and_submerged_faces():
    assert range_blind_intervals(list(product((-4., -1.), (-3., 3.), (12., 15.))), 10., 50.) == []
    assert range_blind_intervals(list(product((1., 4.), (-3., 3.), (-5., -1.))), 10., 50.) == []


def test_eye_enclosing_projection_blocks_forward_beam():
    actual = range_blind_intervals(list(product((-4., 4.), (-3., 3.), (9., 12.))), 10., 50.)
    np.testing.assert_allclose(actual, [(-math.pi/2, math.pi/2)], atol=1e-8)


@pytest.mark.parametrize('list_angle,trim_angle', [(.13, -.09), (-.07, .11)])
def test_rigid_list_and_trim_matches_independent_vertical_section(list_angle, trim_angle):
    # Independent oracle: intersect the original box's six half-spaces with
    # the vertical plane at each bearing after inverse rigid rotation. In
    # this 2-D section, z+H*r/R-H is linear, so its maximum is at a polygon
    # vertex. This reference uses no projected edges or cone quadratics.
    ca, sa = math.cos(list_angle), math.sin(list_angle)
    cb, sb = math.cos(trim_angle), math.sin(trim_angle)
    rotation = np.array([[cb, sb*sa, sb*ca], [0., ca, -sa], [-sb, cb*sa, cb*ca]])
    pivot = np.array([0., 0., 9.])
    low, high = np.array([1., 0., 8.]), np.array([2., 10., 9.])
    corners = [pivot + rotation @ (np.array(p)-pivot)
               for p in product(*zip(low, high))]
    intervals = range_blind_intervals(corners, 10., 50.)
    for degrees in range(3, 87, 3):
        angle = math.radians(degrees)
        radial = rotation.T @ np.array([math.cos(angle), math.sin(angle), 0.])
        vertical = rotation.T @ np.array([0., 0., 1.])
        offset = pivot - rotation.T @ pivot
        constraints = []
        for axis in range(3):
            row = np.array([radial[axis], vertical[axis]])
            constraints.extend(((row, high[axis]-offset[axis]),
                                (-row, offset[axis]-low[axis])))
        constraints.extend(((np.array([-1., 0.]), 0.), (np.array([0., -1.]), 0.)))
        section = []
        for (a, bound_a), (b, bound_b) in combinations(constraints, 2):
            matrix = np.array([a, b])
            if abs(np.linalg.det(matrix)) < 1e-12:
                continue
            vertex = np.linalg.solve(matrix, [bound_a, bound_b])
            if all(row@vertex <= bound+1e-10 for row, bound in constraints):
                section.append(vertex)
        blocked_by_section = any(z + .2*r - 10. > 1e-10 for r, z in section)
        blocked_by_intervals = any(lo < angle < hi for lo, hi in intervals)
        assert blocked_by_intervals == blocked_by_section, (degrees, intervals)


def test_exact_tangent_box_has_no_positive_width_blind_sector():
    # All points lie at or inside the cone r=5 at roof z=9. Only the
    # outermost corner (3,4,9) touches it, which has zero angular width.
    vertices = list(product((1., 3.), (1., 4.), (8., 9.)))
    assert range_blind_intervals(vertices, 10., 50.) == []
