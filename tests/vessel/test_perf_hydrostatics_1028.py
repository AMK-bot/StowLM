"""Exact-content public hydrostatic intermediates; no state verdict reuse."""
from dataclasses import fields

import numpy as np
import pytest

from stowage.checker.perf import without_reuse
from stowage.vessel import hydrostatics as hydro


def geometry():
    result = object.__new__(hydro.HullGeometry)
    result.lpp_m = 10.
    result.density_kg_m3 = 1025.
    result.x_m = np.array([2.5, 7.5])
    result.integration_weights_m = np.array([5., 5.])
    rectangle = np.array([[-2., 0.], [2., 0.], [2., 4.], [-2., 4.]])
    result.sections = [[rectangle.copy()], [rectangle.copy()]]
    return result


def identical(first, second):
    for field in fields(first):
        a, b = np.asarray(getattr(first, field.name)), np.asarray(getattr(second, field.name))
        assert a.shape == b.shape, field.name
        assert a.dtype == b.dtype, field.name
        assert a.tobytes() == b.tobytes(), field.name


def test_submerged_reuse_matches_original_bitwise_across_identical_solids():
    hydro.clear_hydrostatic_caches()
    first = geometry()
    with without_reuse():
        expected = first.submerged(2., 2.1, .13)
    assert not hydro._SUBMERGED_CACHE
    identical(first.submerged(2., 2.1, .13), expected)
    assert len(hydro._SUBMERGED_CACHE) == 1
    identical(geometry().submerged(2., 2.1, .13), expected)
    assert len(hydro._SUBMERGED_CACHE) == 1
    with without_reuse():
        identical(first.submerged(2., 2.1, .13), expected)
    assert len(hydro._SUBMERGED_CACHE) == 1


def test_submerged_cache_covers_every_current_integrated_input():
    hydro.clear_hydrostatic_caches()
    solid = geometry()
    solid.submerged(2., 2.1, .13)
    mutations = [lambda: setattr(solid, 'lpp_m', 11.),
                 lambda: solid.x_m.__setitem__(0, 2.6),
                 lambda: solid.integration_weights_m.__setitem__(0, 5.1),
                 lambda: solid.sections[0][0].__setitem__((1, 0), 2.1),
                 lambda: solid.sections[0].append(solid.sections[0][0].copy()),
                 lambda: setattr(solid, 'density_kg_m3', 1020.)]
    for mutate in mutations:
        mutate()
        with without_reuse():
            expected = solid.submerged(2., 2.1, .13)
        identical(solid.submerged(2., 2.1, .13), expected)
        identical(solid.submerged(2., 2.1, .13), expected)
    for args in [(2.01, 2.1, .13, 1020.), (2., 2.11, .13, 1020.),
                 (2., 2.1, .14, 1020.), (2., 2.1, .13, 1000.)]:
        with without_reuse():
            expected = solid.submerged(*args)
        identical(solid.submerged(*args), expected)


def test_cached_result_arrays_are_independent():
    hydro.clear_hydrostatic_caches()
    solid = geometry()
    solid.submerged(2., 2.1, .13)
    first = solid.submerged(2., 2.1, .13)
    first.x_m[:] = 100.
    first.sectional_areas_m2[:] = 100.
    first.integration_weights_m[:] = 100.
    with without_reuse():
        expected = solid.submerged(2., 2.1, .13)
    identical(solid.submerged(2., 2.1, .13), expected)


def test_subclasses_keep_their_uncached_axes_contract():
    hydro.clear_hydrostatic_caches()
    class Special(hydro.HullGeometry):
        calls = 0
        def _axes(self, *args):
            self.calls += 1
            return super()._axes(*args)
    solid = geometry()
    solid.__class__ = Special
    solid.submerged(2., 2.1, .13)
    solid.submerged(2., 2.1, .13)
    assert solid.calls == 2
    assert not hydro._SUBMERGED_CACHE


@pytest.mark.parametrize('dependency', ['moments', 'moments_code', 'dot', 'instance_axes'])
def test_runtime_dependencies_bypass_populated_cache(monkeypatch, dependency):
    hydro.clear_hydrostatic_caches()
    solid = geometry()
    old = solid.submerged(2., 2.1, .13)
    if dependency == 'moments':
        original = hydro._moments
        monkeypatch.setattr(hydro, '_moments', lambda polygon: original(polygon)*2)
    elif dependency == 'moments_code':
        def replacement(polygon):
            return np.zeros(3)
        monkeypatch.setattr(hydro._moments, '__code__', replacement.__code__)
    elif dependency == 'dot':
        original = np.dot
        monkeypatch.setattr(np, 'dot', lambda a, b: original(a, b)*1.125)
    else:
        original = solid._axes
        solid._axes = lambda ta, tf, heel: original(ta, tf, heel+.05)
    with without_reuse():
        expected = solid.submerged(2., 2.1, .13)
    identical(solid.submerged(2., 2.1, .13), expected)
    assert len(hydro._SUBMERGED_CACHE) == 1
    assert (old.volume_m3, old.transverse_inertia_m4) != (expected.volume_m3, expected.transverse_inertia_m4)


def test_unusual_geometry_container_and_scalar_types_keep_original_behavior():
    hydro.clear_hydrostatic_caches()
    solid = geometry()
    old = solid.submerged(2., 2.1, .13)
    # The numerical key must not turn an invalid list polygon into a hit on a
    # previously valid ndarray containing the same values.
    solid.sections[0][0] = solid.sections[0][0].tolist()
    with pytest.raises(TypeError):
        solid.submerged(2., 2.1, .13)
    class Density(float):
        def __mul__(self, other):
            return 1.5*super().__mul__(other)
    solid = geometry()
    with without_reuse():
        expected = solid.submerged(2., 2.1, .13, Density(1025.))
    identical(solid.submerged(2., 2.1, .13, Density(1025.)), expected)
    assert expected.displacement_kg != old.displacement_kg
    class Stations(np.ndarray):
        pass
    solid.x_m = solid.x_m.view(Stations)
    assert hydro._submerged_content_key(solid, 2., 2.1, .13, 1025.) is None
