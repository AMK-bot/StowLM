"""Analytic source distances and certified bounds for sampled tank geometry."""

from types import SimpleNamespace

import pytest

from stowage.rules.imdg.heat_geometry import heat_source_clearance, heat_source_distance


def box(x0, x1, y0=0, y1=1, z0=0, z1=1):
    return SimpleNamespace(x_min_m=x0, x_max_m=x1, y_min_m=y0, y_max_m=y1, z_min_m=z0, z_max_m=z1)


def rectangular():
    return dict(shape='rectangular_prism', **vars(box(0, 2)))


def sections(polygons=None):
    polygons = polygons or [[(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]]
    return dict(shape='hull_sections', **vars(box(0, 2, 0, 4, 0, 4)),
                x_m=[.5, 1.5], integration_weights_m=[1, 1], sections_yz_m=[polygons, polygons])


def test_rectangular_exact_distance_and_clearance_boundary():
    geometry = rectangular()
    assert heat_source_distance(box(4.4, 5), geometry) == pytest.approx(2.4)
    assert heat_source_clearance(box(4.4, 5), geometry) is True
    assert heat_source_clearance(box(4.399, 5), geometry) is False
    assert heat_source_distance(box(1, 3), geometry) == 0
    assert heat_source_clearance(box(1, 3), geometry) is False
    assert heat_source_distance(box(3, 4, 2, 3, 3, 4), geometry) == pytest.approx(6 ** .5)


def test_unknown_or_invalid_shape_never_uses_source_bounding_box():
    assert heat_source_distance(box(10, 11), None) is None
    assert heat_source_clearance(box(10, 11), None) is None
    assert heat_source_clearance(box(10, 11), dict(shape='unknown', **vars(box(0, 2)))) is None
    assert heat_source_clearance(box(10, 11), {**rectangular(), 'x_max_m': -1}) is None
    assert heat_source_clearance(box(10, 11), rectangular(), minimum_clearance_m=float('nan')) is None


def test_hull_sections_have_no_continuous_distance_even_when_bbox_far_proves_clear():
    geometry = sections()
    assert heat_source_distance(box(4.4, 5), geometry) is None
    assert heat_source_clearance(box(4.4, 5), geometry) is True


def test_nonconvex_section_notch_stays_unknown_without_interpolating_a_solid():
    geometry = sections()
    envelope = box(.4, .6, 2, 2.5, 2, 2.5)
    assert heat_source_distance(envelope, geometry) is None
    assert heat_source_clearance(envelope, geometry, minimum_clearance_m=.5) is None
    assert heat_source_clearance(envelope, geometry, minimum_clearance_m=1.1) is False


def test_section_interior_is_a_close_witness_and_not_a_convex_hull():
    geometry = sections()
    assert heat_source_clearance(box(.4, .6, .2, .8, 2, 3), geometry) is False
    # The notch is outside the L; replacing its polygon by its convex hull would
    # manufacture a close witness and incorrectly reject this clearance query.
    assert heat_source_clearance(box(.4, .6, 2, 2.5, 2, 2.5), geometry,
                                 minimum_clearance_m=.5) is None


def test_disjoint_section_components_do_not_fill_the_gap():
    geometry = sections([[(0, 0), (1, 0), (1, 1), (0, 1)],
                         [(3, 0), (4, 0), (4, 1), (3, 1)]])
    envelope = box(.4, .6, 1.8, 2.2, 0, 1)
    assert heat_source_clearance(envelope, geometry, minimum_clearance_m=.5) is None
    assert heat_source_clearance(envelope, geometry, minimum_clearance_m=1) is False


def test_sampled_sections_cannot_prove_clearance_between_stations():
    geometry = sections([[(0, 0), (1, 0), (1, 1), (0, 1)]])
    geometry.update(y_max_m=1, z_max_m=1, x_m=[.2, 1.8])
    envelope = box(.9, 1.1, 2, 3)
    assert heat_source_clearance(envelope, geometry, minimum_clearance_m=1.1) is None


def test_geometry_must_match_actual_source_bounds():
    geometry = rectangular()
    assert heat_source_distance(box(4.4, 5), geometry, source_bounds=box(0, 2)) == pytest.approx(2.4)
    assert heat_source_distance(box(4.4, 5), geometry, source_bounds=box(0, 3)) is None
    assert heat_source_clearance(box(4.4, 5), geometry, source_bounds=box(0, 3)) is None
    assert heat_source_clearance(box(4.4, 5), sections(), source_bounds=box(0, 2)) is None


def test_malformed_sample_geometry_is_unknown():
    geometry = sections()
    geometry['sections_yz_m'][0] = [[(0, 0), (1, 0)]]
    assert heat_source_clearance(box(10, 11), geometry) is None
