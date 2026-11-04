"""Exact numerical geometry reuse and complete dependency invalidation."""
from itertools import product

import numpy as np
import pytest

from stowage.checker import visibility_range as module
from stowage.checker.perf import without_reuse


def corners():
    return np.array(list(product((30., 42.), (-4., 4.), (5., 19.))))


def test_intersections_are_bitwise_equal_for_diverse_transformed_boxes():
    rng = np.random.default_rng(1028)
    for _ in range(200):
        points = corners() + rng.uniform((-50., -30., -15.), (100., 30., 15.))
        height, radius = rng.uniform(15., 40.), rng.uniform(50., 500.)
        expected = module._range_blind_intervals_reference(points, height, radius)
        assert module.range_blind_intervals(points, height, radius) == expected
        assert module.range_blind_intervals(points.copy(), height, radius) == expected


@pytest.mark.parametrize('dependency', ['corner', 'height', 'radius'])
def test_each_numerical_input_invalidates_cache(dependency):
    module._range_intersections.cache_clear()
    points, height, radius = corners(), 22., 150.
    module.range_blind_intervals(points, height, radius)
    if dependency == 'corner':
        points[0, 0] = np.nextafter(points[0, 0], np.inf)
    elif dependency == 'height':
        height = np.nextafter(height, np.inf)
    else:
        radius = np.nextafter(radius, np.inf)
    actual = module.range_blind_intervals(points, height, radius)
    assert module._range_intersections.cache_info().misses == 2
    assert actual == module._range_blind_intervals_reference(points, height, radius)


def test_results_cannot_taint_cache_and_reference_bypasses_it():
    module._range_intersections.cache_clear()
    points = corners()
    expected = module.range_blind_intervals(points, 22., 150.)
    changed = module.range_blind_intervals(points, 22., 150.)
    changed.append((-999., 999.))
    assert module.range_blind_intervals(points, 22., 150.) == expected
    before = module._range_intersections.cache_info()
    with without_reuse():
        assert module.range_blind_intervals(points, 22., 150.) == expected
    assert module._range_intersections.cache_info() == before


def test_changed_numerical_helper_bypasses_populated_cache(monkeypatch):
    module._range_intersections.cache_clear()
    points = corners()
    expected = module.range_blind_intervals(points, 22., 150.)
    before = module._range_intersections.cache_info()
    original, calls = module._roots, []

    def roots(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(module, '_roots', roots)
    assert module.range_blind_intervals(points, 22., 150.) == expected
    assert calls
    assert module._range_intersections.cache_info() == before


@pytest.mark.parametrize('provider', ['array', 'asarray', 'frombuffer', 'isfinite', 'all'])
def test_changed_array_provider_bypasses_populated_cache(monkeypatch, provider):
    module._range_intersections.cache_clear()
    points = corners()
    expected = module.range_blind_intervals(points, 22., 150.)
    before = module._range_intersections.cache_info()
    original, calls = getattr(np, provider), []

    def replacement(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(np, provider, replacement)
    assert module.range_blind_intervals(points, 22., 150.) == expected
    assert module._range_intersections.cache_info() == before
    # frombuffer belongs only to the cached reconstruction path; unknown
    # providers bypass that path entirely and therefore are not called.
    assert calls or provider == 'frombuffer'


def test_changed_array_arithmetic_does_not_reuse_old_cone_intersections(monkeypatch):
    module._range_intersections.cache_clear()
    points = np.array(list(product((37.13756137810432, 55.93696228256123),
        (9.981190866594748, 13.28475105295729), (3.336177796640556, 6.500811111324218))))
    height, radius = 20.0847011378874, 78.95019920948327
    before = module.range_blind_intervals(points, height, radius)
    original = np.array
    monkeypatch.setattr(np, 'array', lambda *args, **kwargs: original(*args, **kwargs)*0)
    actual = module.range_blind_intervals(points, height, radius)
    assert actual == module._range_blind_intervals_reference(points, height, radius)
    assert actual != before


def test_array_subclass_with_custom_iteration_retains_reference_behavior():
    class ChangedIteration(np.ndarray):
        def __iter__(self):
            for point in super().__iter__():
                yield np.asarray(point)+np.array([1000., 0., 0.])

    points = corners().view(ChangedIteration)
    expected = module._range_blind_intervals_reference(points, 22., 150.)
    assert module.range_blind_intervals(points, 22., 150.) == expected
    assert expected != module.range_blind_intervals(np.asarray(points), 22., 150.)


@pytest.mark.parametrize('points,height,radius', [
    (np.zeros((7, 3)), 20., 100.), (np.full((8, 3), np.nan), 20., 100.),
    (corners(), 0., 100.), (corners(), 20., -1.),
])
def test_invalid_geometry_retains_original_errors(points, height, radius):
    with pytest.raises(ValueError) as reference:
        module._range_blind_intervals_reference(points, height, radius)
    with pytest.raises(ValueError) as actual:
        module.range_blind_intervals(points, height, radius)
    assert str(actual.value) == str(reference.value)
