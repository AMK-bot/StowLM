"""Exact numeric strength-kernel keys, bounded reuse and reference behavior."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import strength_assessment as a
from stowage.checker.perf import evaluation_cache, without_reuse


@pytest.fixture(autouse=True)
def clean_cache():
    a.clear_assessment_cache()
    yield
    a.clear_assessment_cache()


def beam():
    segments = [(0., 100., 100., -2., .01), (20., 80., -50., 0., 0.)]
    points = [(23.25, -1700.), (60.75, 1300.)]
    stations = [SimpleNamespace(x_m=x, min_shear_n=-1e5, max_shear_n=1e5,
        min_bending_nm=-1e7, max_bending_nm=1e7) for x in (0., 17., 44., 69., 100.)]
    return segments, points, stations


def exact_cuts(left, right):
    assert len(left) == len(right)
    for first, second in zip(left, right):
        assert first[-1] == second[-1]
        assert all(type(x) is type(y) and float(x).hex() == float(y).hex()
                   for x, y in zip(first[:-1], second[:-1]))


def test_global_numeric_reuse_matches_reference_and_copies_list():
    data = beam()
    with without_reuse():
        expected = a.assessment_cuts(*data)
    with evaluation_cache():
        first = a.assessment_cuts(*data)
    second = a.assessment_cuts(*deepcopy(data))
    exact_cuts(first, expected)
    exact_cuts(second, expected)
    assert first is not second
    second.pop()
    exact_cuts(a.assessment_cuts(*data), expected)
    assert a.assessment_cache_info() == dict(hits=2, misses=1, size=1, maxsize=64)
    with without_reuse():
        exact_cuts(a.assessment_cuts(*data), expected)
    assert a.assessment_cache_info()['hits'] == 2


@pytest.mark.parametrize('dependency', ['coefficient', 'edge', 'force', 'point_position',
    'x_m', 'min_shear_n', 'max_shear_n', 'min_bending_nm', 'max_bending_nm',
    'segment_order', 'point_order', 'station_order', 'signed_zero', 'scalar_type', 'container_type'])
def test_all_numeric_content_and_types_invalidate(dependency):
    data = beam()
    a.assessment_cuts(*data)
    segments, points, stations = deepcopy(data)
    if dependency in ('coefficient', 'edge', 'signed_zero', 'scalar_type'):
        row = list(segments[1])
        if dependency == 'coefficient':
            row[2] += .1
        elif dependency == 'edge':
            row[0] += 1e-8
        elif dependency == 'signed_zero':
            row[3] = -0.
        else:
            row[3] = 0
        segments[1] = tuple(row)
    elif dependency == 'force':
        points[0] = (points[0][0], points[0][1]+.1)
    elif dependency == 'point_position':
        points[0] = (points[0][0]+1e-8, points[0][1])
    elif dependency.endswith('_order'):
        {'segment_order': segments, 'point_order': points, 'station_order': stations}[dependency].reverse()
    elif dependency == 'container_type':
        segments = tuple(segments)
    else:
        setattr(stations[1], dependency, getattr(stations[1], dependency)+.1)
    exact_cuts(a.assessment_cuts(segments, points, stations),
               a.assessment_cuts_reference(segments, points, stations))
    assert a.assessment_cache_info()['misses'] == 2
    assert a.assessment_cache_info()['size'] == 2


@pytest.mark.parametrize('damage', ['nan', 'infinite', 'wrong_sign', 'duplicate', 'uncovered', 'malformed'])
def test_invalid_inputs_preserve_errors_and_never_cache(damage):
    segments, points, stations = beam()
    if damage == 'nan':
        stations[0].max_shear_n = np.nan
    elif damage == 'infinite':
        stations[0].max_shear_n = np.inf
    elif damage == 'wrong_sign':
        stations[0].min_shear_n = 0.
    elif damage == 'duplicate':
        stations[1].x_m = stations[0].x_m
    elif damage == 'uncovered':
        stations = stations[:-1]
    else:
        segments[0] = segments[0][:-1]
    with pytest.raises(Exception) as expected:
        a.assessment_cuts_reference(segments, points, stations)
    for _ in range(2):
        with pytest.raises(type(expected.value)) as actual:
            a.assessment_cuts(segments, points, stations)
        assert str(actual.value) == str(expected.value)
        assert a.assessment_cache_info()['size'] == 0


def test_unsupported_iterator_and_scalar_inputs_bypass_cache():
    segments, points, stations = beam()
    expected = a.assessment_cuts_reference(segments, points, stations)
    exact_cuts(a.assessment_cuts(iter(segments), points, stations), expected)
    segments = [tuple(np.float32(v) for v in row) for row in segments]
    exact_cuts(a.assessment_cuts(segments, points, stations),
               a.assessment_cuts_reference(segments, points, stations))
    assert a.assessment_cache_info()['size'] == 0


def test_unknown_station_descriptors_are_not_read_for_key_extraction():
    segments, points, stations = beam()

    class StatefulStation:
        def __init__(self, station):
            self.station = station
            self.reads = []

        def __getattr__(self, name):
            self.reads.append(name)
            return getattr(self.station, name)

    first = [StatefulStation(station) for station in stations]
    second = [StatefulStation(station) for station in stations]
    exact_cuts(a.assessment_cuts(segments, points, first),
               a.assessment_cuts_reference(segments, points, second))
    assert [station.reads for station in first] == [station.reads for station in second]
    assert a.assessment_cache_info()['size'] == 0


def test_changed_numeric_helper_bypasses_populated_cache(monkeypatch):
    data = beam()
    a.assessment_cuts(*data)
    original = a._ratio_extrema
    calls = []
    def replaced(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(a, '_ratio_extrema', replaced)
    a.assessment_cuts(*data)
    assert calls
    assert a.assessment_cache_info()['hits'] == 0


def test_numeric_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(a, '_ASSESSMENT_MAXSIZE', 3)
    for change in range(5):
        segments, points, stations = beam()
        points[0] = (points[0][0], points[0][1]+change)
        a.assessment_cuts(segments, points, stations)
    assert a.assessment_cache_info()['size'] == 3
