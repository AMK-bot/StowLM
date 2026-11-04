"""Experiment only: guarded slot helpers and continuous strength-cut reuse.

No production modules are patched. One numerical core; <50 MB working data.
"""
from __future__ import annotations
import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

from collections import OrderedDict
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import pstats
from statistics import median
from time import perf_counter
from types import SimpleNamespace

import numpy as np
from stowage import conventions as c
from stowage.checker import strength_assessment as a
from stowage.checker.perf import current_cache, evaluation_cache, reuse_enabled, without_reuse


def dependencies():
    return (c.parse_slot, c.cells_of_slot, c.format_slot, c.odd_bays_of,
            c._SLOT_RE, c.SlotAddress, c.SlotAddress.__post_init__)


ORIGINALS = dependencies()


def parse_slot(slot):
    cache = current_cache()
    if cache is None or type(slot) is not str or dependencies() != ORIGINALS:
        return c.parse_slot(slot)
    key = ('prototype.slot.parse', ORIGINALS, slot)
    if key not in cache:
        cache[key] = c.parse_slot(slot)
    return cache[key]


def cells_of_slot(slot):
    cache = current_cache()
    if cache is None or type(slot) is not str or dependencies() != ORIGINALS:
        return c.cells_of_slot(slot)
    key = ('prototype.slot.cells', ORIGINALS, slot)
    if key not in cache:
        cache[key] = c.cells_of_slot(slot)
    return cache[key]


ASSESSMENT = getattr(a, 'assessment_cuts_reference', a.assessment_cuts)
ASSESSMENT_DEPENDENCIES = (a._ratio_extrema, a._interior_roots)
_CUTS = OrderedDict()


def assessment_cuts(segments, points, stations):
    if (not reuse_enabled() or (a._ratio_extrema, a._interior_roots) != ASSESSMENT_DEPENDENCIES):
        return ASSESSMENT(segments, points, stations)
    # Prototype inputs are guaranteed finite float arrays. A production version
    # must bypass unsupported numeric types and preserve original exceptions.
    arrays = (np.asarray(segments, dtype=np.float64), np.asarray(points, dtype=np.float64),
        np.asarray([(s.x_m, s.min_shear_n, s.max_shear_n, s.min_bending_nm,
                     s.max_bending_nm) for s in stations], dtype=np.float64))
    key = (ASSESSMENT, ASSESSMENT_DEPENDENCIES,
           tuple((value.shape, value.tobytes()) for value in arrays))
    if key in _CUTS:
        _CUTS.move_to_end(key)
        return list(_CUTS[key])
    result = ASSESSMENT(segments, points, stations)
    _CUTS[key] = tuple(result)
    if len(_CUTS) > 64:
        _CUTS.popitem(last=False)
    return result


def timed(function):
    started = perf_counter()
    value = function()
    return perf_counter()-started, value


def main():
    root = Path(__file__).resolve().parents[3]
    profile = root/'experiments/checker/perf-1028/optimized-profile-feeder_1700.prof'
    stats = pstats.Stats(str(profile)).stats
    counts = {}
    for key, value in stats.items():
        if key[0].endswith('conventions.py') and key[2] in ('cells_of_slot', 'parse_slot'):
            counts[key[2]] = sum(v[0] for caller, v in value[4].items() if '/checker/' in caller[0])
    slots = [f'{bay:03d}{row:02d}{tier:02d}' for bay in range(1, 40)
             for row in range(8) for tier in (2, 4, 6, 82, 84)]
    assert len(slots) == 1560
    with evaluation_cache():
        for slot in slots:
            assert parse_slot(slot) == c.parse_slot(slot)
            assert cells_of_slot(slot) == c.cells_of_slot(slot)
        try:
            parse_slot(slots[0]).tier = 84
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError('SlotAddress must be immutable')
        for slot in ('invalid', '0000002', '0010003', None, 123):
            for original, cached in ((c.parse_slot, parse_slot), (c.cells_of_slot, cells_of_slot)):
                failures = []
                for fn in (original, cached):
                    try:
                        fn(slot)
                    except Exception as exc:
                        failures.append((type(exc), str(exc)))
                assert len(failures) == 2 and failures[0] == failures[1]
        original_parser = c.parse_slot
        replacement_calls = []

        def replacement(slot):
            replacement_calls.append(slot)
            return c.SlotAddress(1, 0, 82)

        c.parse_slot = replacement
        try:
            assert parse_slot(slots[0]) == c.SlotAddress(1, 0, 82)
            assert parse_slot(slots[0]) == c.SlotAddress(1, 0, 82)
            assert len(replacement_calls) == 2  # Unknown overrides always bypass.
        finally:
            c.parse_slot = original_parser

    def slot_trace(cached):
        fs = (cells_of_slot, parse_slot) if cached else (c.cells_of_slot, c.parse_slot)
        result = 0
        with evaluation_cache():
            for fn, count in zip(fs, (counts['cells_of_slot'], counts['parse_slot'])):
                for i in range(count):
                    value = fn(slots[i % len(slots)])
                    result += len(value) if isinstance(value, tuple) else value.tier
        return result

    slot_samples = {'original': [], 'memo': []}
    for repeat in range(10):
        for label in (('original', 'memo') if repeat % 2 == 0 else ('memo', 'original')):
            seconds, value = timed(lambda: slot_trace(label == 'memo'))
            slot_samples[label].append(seconds)

    rng = np.random.default_rng(1028)
    segments = [(0., 120., 0., 0., 0.)]
    for _ in range(240):
        lo, hi = sorted(map(float, rng.uniform(0., 120., size=2)))
        segments.append((lo, hi, *map(float, rng.uniform(-30., 30., size=3))))
    points = [(float(x), float(force)) for x, force in zip(rng.uniform(0., 120., size=916),
                                                       rng.uniform(-1e5, 1e5, size=916))]
    stations = [SimpleNamespace(x_m=float(x), min_shear_n=-1e9, max_shear_n=1e9,
        min_bending_nm=-1e11, max_bending_nm=1e11) for x in np.linspace(0., 120., 120)]
    with evaluation_cache():
        reference = ASSESSMENT(segments, points, stations)
        assert assessment_cuts(segments, points, stations) == reference
        actual = assessment_cuts(segments, points, stations)
        assert actual == reference
        actual.pop()
        assert assessment_cuts(segments, points, stations) == reference
        for dependency in ('segment_coefficient', 'segment_edge', 'point_force',
                           'point_position', 'station_limit', 'station_position', 'signed_zero'):
            ss, pp, rr = deepcopy((segments, points, stations))
            if dependency == 'segment_coefficient':
                row = list(ss[1]); row[2] += .1; ss[1] = tuple(row)
            elif dependency == 'segment_edge':
                row = list(ss[1]); row[0] += 1e-8; ss[1] = tuple(row)
            elif dependency == 'point_force':
                pp[0] = (pp[0][0], pp[0][1]+.1)
            elif dependency == 'point_position':
                pp[0] = (pp[0][0]+1e-8, pp[0][1])
            elif dependency == 'station_limit':
                rr[1].max_shear_n *= .9
            elif dependency == 'station_position':
                rr[1].x_m += 1e-8
            else:
                ss[0] = (0., 120., -0., 0., 0.)
            previous_size = len(_CUTS)
            assert assessment_cuts(ss, pp, rr) == ASSESSMENT(ss, pp, rr)
            assert len(_CUTS) == previous_size+1
    assessment_samples = {'original': [], 'memo': []}
    for repeat in range(10):
        for label in (('original', 'memo') if repeat % 2 == 0 else ('memo', 'original')):
            with evaluation_cache():
                seconds, value = timed(lambda: (assessment_cuts if label == 'memo' else ASSESSMENT)(
                    segments, points, stations))
            assert value == reference
            assessment_samples[label].append(seconds)
    print(json.dumps({'slot_trace_calls': counts, 'slot_unique_addresses': len(slots),
        'slot_samples': slot_samples, 'slot_medians': {key: median(v) for key,v in slot_samples.items()},
        'assessment_segments': len(segments), 'assessment_points': len(points),
        'assessment_cuts': len(reference), 'assessment_samples': assessment_samples,
        'assessment_medians': {key: median(v) for key,v in assessment_samples.items()}}, indent=2))


if __name__ == '__main__':
    main()
