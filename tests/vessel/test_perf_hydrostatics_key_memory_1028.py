"""Bound retained geometry-key memory without changing wet integrations."""
from dataclasses import fields
import sys

import numpy as np
import pytest

from stowage.checker.perf import without_reuse
from stowage.vessel import hydrostatics as hydro


def _geometry():
    solid = object.__new__(hydro.HullGeometry)
    solid.lpp_m = 10.
    solid.density_kg_m3 = 1025.
    solid.x_m = np.array([2.5, 7.5])
    solid.integration_weights_m = np.array([5., 5.])
    rectangle = np.array([[-2., 0.], [2., 0.], [2., 4.], [-2., 4.]])
    solid.sections = [[rectangle.copy()], [rectangle.copy()]]
    return solid


def _retained_size(value):
    # Count each key independently, including repeated/shared tuple members:
    # this deliberately overestimates its actual retained memory.
    return sys.getsizeof(value) + (sum(_retained_size(item) for item in value)
                                   if isinstance(value, tuple) else 0)


def _identical(first, second):
    for field in fields(first):
        a = np.asarray(getattr(first, field.name))
        b = np.asarray(getattr(second, field.name))
        assert a.dtype == b.dtype, field.name
        assert a.shape == b.shape, field.name
        assert a.tobytes() == b.tobytes(), field.name


@pytest.fixture(autouse=True)
def _clear_caches():
    hydro.clear_hydrostatic_caches()
    yield
    hydro.clear_hydrostatic_caches()


def test_256_distinct_draughts_retain_less_than_1kb_per_key(record_property):
    solid = _geometry()
    for index in range(256):
        solid.submerged(1. + index / 512., 1.1 + index / 512., .13)
    assert len(hydro._SUBMERGED_CACHE) == 256
    sizes = [_retained_size(key) for key in hydro._SUBMERGED_CACHE]
    record_property('max_retained_key_bytes', max(sizes))
    record_property('total_retained_key_bytes', sum(sizes))
    assert max(sizes) < 1024
    assert sum(sizes) < 256 * 1024


def test_fleet_sized_polygon_payload_is_not_retained_in_key(record_property):
    solid = _geometry()
    solid.sections = [[np.tile(section[0], (12500, 1))] for section in solid.sections]
    payload_bytes = sum(polygon.nbytes for section in solid.sections for polygon in section)
    assert payload_bytes == 1_600_000
    key = hydro._submerged_content_key(solid, 2., 2.1, .13, 1025.)
    record_property('polygon_payload_bytes', payload_bytes)
    record_property('retained_key_bytes', _retained_size(key))
    assert _retained_size(key) < 1024
    assert len(key[1]) == 32
    solid.sections[0][0][1, 0] += .1
    assert hydro._submerged_content_key(solid, 2., 2.1, .13, 1025.) != key


def test_inplace_polygon_edit_matches_without_reuse_bitwise():
    solid = _geometry()
    before = solid.submerged(2., 2.1, .13)
    solid.sections[0][0][1, 0] += .25
    with without_reuse():
        expected = solid.submerged(2., 2.1, .13)
    assert expected.volume_m3 != before.volume_m3
    _identical(solid.submerged(2., 2.1, .13), expected)
    _identical(solid.submerged(2., 2.1, .13), expected)
    assert len(hydro._SUBMERGED_CACHE) == 2


def test_digest_frames_station_polygon_boundaries():
    solid = _geometry()
    polygon = solid.sections[0][0].copy()
    solid.sections = [[polygon, polygon], []]
    first = hydro._submerged_content_key(solid, 2., 2.1, .13, 1025.)
    solid.sections = [[polygon], [polygon]]
    assert hydro._submerged_content_key(solid, 2., 2.1, .13, 1025.) != first


def test_replaced_digest_provider_bypasses_populated_cache(monkeypatch):
    solid = _geometry()
    original = solid.submerged(2., 2.1, .13)
    def fail(*args, **kwargs):
        raise AssertionError('unreviewed hash provider must not run')
    monkeypatch.setattr(hydro.hashlib, 'sha256', fail)
    _identical(solid.submerged(2., 2.1, .13), original)
    assert len(hydro._SUBMERGED_CACHE) == 1
