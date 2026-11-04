"""Continuous assessment of the existing piecewise-polynomial beam model.

Station limits define piecewise-linear permissible envelopes. Fleet envelopes
include every wave/scantling knot, so interpolation preserves their design
formulae. No extra physical load approximation is introduced here.
"""
from __future__ import annotations

from collections import defaultdict, OrderedDict
import math
from threading import RLock
from types import SimpleNamespace

import numpy as np

from stowage.model.vessel import Station


def _root_dependencies():
    functions = (np.polynomial.polynomial.polyroots,
                 np.polynomial.polynomial.polycompanion, np.linalg.eigvals,
                 np.real, np.imag)
    return (*functions, *(getattr(fn, '__code__', None) for fn in functions))


_ORIGINAL_ROOT_DEPENDENCIES = _root_dependencies()
_ROOT_DEPENDENCY_IDENTITIES = tuple(map(id, _ORIGINAL_ROOT_DEPENDENCIES))


def _interior_roots(coefficients):
    """Real roots in the open unit interval, with no spatial sampling grid."""
    coefficients = np.trim_zeros(np.asarray(coefficients, dtype=float), 'b')
    if len(coefficients) <= 1:
        return []
    if len(coefficients) == 2:
        roots = [-coefficients[0] / coefficients[1]]
    else:
        normalized = coefficients / np.max(np.abs(coefficients))
        from stowage.checker.perf import current_cache
        cache = current_cache()
        if cache is not None and _root_dependencies() != _ORIGINAL_ROOT_DEPENDENCIES:
            cache = None
        # polyroots builds its companion matrix from these exact divisions.
        # Signed permissible envelopes often give the same matrix. Reuse
        # only an identical eigenproblem, without rescaling its computation
        # or changing which stationary cuts enter the strength assessment.
        key = (('strength.companion_roots', _ROOT_DEPENDENCY_IDENTITIES,
                (normalized[:-1]/normalized[-1]).tobytes())
               if cache is not None else None)
        if cache is not None and key in cache:
            return list(cache[key])
        roots = np.polynomial.polynomial.polyroots(normalized)
        result = [float(np.real(v)) for v in roots
                  if abs(np.imag(v)) <= 1e-9 and 0 < np.real(v) < 1]
        if cache is not None:
            cache[key] = tuple(result)
        return result
    return [float(np.real(v)) for v in roots
            if abs(np.imag(v)) <= 1e-9 and 0 < np.real(v) < 1]


def _ratio_extrema(poly, start, change):
    # d(P/E)/du = (P' E - P E')/E^2. E is strictly positive
    # (or strictly negative) throughout each permissible-envelope interval.
    derivative = np.arange(1, len(poly)) * poly[1:]
    numerator = np.zeros(len(poly))
    numerator[:-1] = start * derivative
    numerator[1:] += change * derivative
    numerator -= change * poly
    return _interior_roots(numerator)


_ASSESSMENT_MAXSIZE = 64
_ASSESSMENT_CACHE = OrderedDict()
_ASSESSMENT_LOCK = RLock()
_ASSESSMENT_HITS = _ASSESSMENT_MISSES = 0


def clear_assessment_cache():
    """Clear the disposable numerical cache for cold measurements."""
    global _ASSESSMENT_HITS, _ASSESSMENT_MISSES
    with _ASSESSMENT_LOCK:
        _ASSESSMENT_CACHE.clear()
        _ASSESSMENT_HITS = _ASSESSMENT_MISSES = 0


def assessment_cache_info():
    with _ASSESSMENT_LOCK:
        return dict(hits=_ASSESSMENT_HITS, misses=_ASSESSMENT_MISSES,
                    size=len(_ASSESSMENT_CACHE), maxsize=_ASSESSMENT_MAXSIZE)


def _assessment_dependencies():
    functions = (assessment_cuts_reference, _ratio_extrema, _interior_roots,
        np.asarray, np.isfinite, np.diff, np.any, np.column_stack, np.interp,
        np.array, np.arange, np.zeros, np.trim_zeros, np.max, np.abs, np.real,
        np.imag, np.polynomial.polynomial.polyval, np.polynomial.polynomial.polyroots,
        np.polynomial.polynomial.polycompanion, np.linalg.eigvals, math.fsum)
    return (*functions, *(getattr(fn, '__code__', None) for fn in functions))


def _numeric_rows_key(rows, width):
    # Preserve each scalar's type and exact bits: Python equality alone merges
    # signed zeros and equal-valued integers/floats. Unusual numeric/iterator
    # inputs retain the original computation and its validation/errors.
    if type(rows) not in (list, tuple):
        return None
    scalar_types, row_types = [], []
    for row in rows:
        if type(row) not in (list, tuple) or len(row) != width:
            return None
        row_types.append(type(row))
        for value in row:
            kind = type(value)
            if kind not in (float, np.float64, int):
                return None
            if kind is int and abs(value) > 2**53:
                return None
            scalar_types.append(kind)
    array = np.asarray(rows, dtype=np.float64)
    if not np.isfinite(array).all():
        return None
    return type(rows), tuple(row_types), tuple(scalar_types), array.shape, array.tobytes()


def _assessment_key(segments, points, stations):
    if type(stations) not in (list, tuple):
        return None
    if any(type(station) not in (Station, SimpleNamespace) for station in stations):
        return None
    try:
        station_rows = [(s.x_m, s.min_shear_n, s.max_shear_n,
                         s.min_bending_nm, s.max_bending_nm) for s in stations]
        keys = (_numeric_rows_key(segments, 5), _numeric_rows_key(points, 2),
                _numeric_rows_key(station_rows, 5))
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None
    if any(key is None for key in keys):
        return None
    return _ORIGINAL_ASSESSMENT_DEPENDENCIES, type(stations), keys


def assessment_cuts(segments, points, stations):
    """Reuse only identical numerical beam/extrema inputs, never a verdict."""
    from stowage.checker.perf import reuse_enabled
    global _ASSESSMENT_HITS, _ASSESSMENT_MISSES
    if (not reuse_enabled()
            or _assessment_dependencies() != _ORIGINAL_ASSESSMENT_DEPENDENCIES):
        return assessment_cuts_reference(segments, points, stations)
    key = _assessment_key(segments, points, stations)
    if key is None:
        return assessment_cuts_reference(segments, points, stations)
    with _ASSESSMENT_LOCK:
        cached = _ASSESSMENT_CACHE.get(key)
        if cached is not None:
            _ASSESSMENT_CACHE.move_to_end(key)
            _ASSESSMENT_HITS += 1
            return list(cached)
        _ASSESSMENT_MISSES += 1
    result = assessment_cuts_reference(segments, points, stations)
    # Every cut contains immutable numeric scalars and its side string. The
    # caller gets a fresh list, including on the first calculation.
    with _ASSESSMENT_LOCK:
        _ASSESSMENT_CACHE[key] = tuple(result)
        _ASSESSMENT_CACHE.move_to_end(key)
        while len(_ASSESSMENT_CACHE) > _ASSESSMENT_MAXSIZE:
            _ASSESSMENT_CACHE.popitem(last=False)
    return result


def assessment_cuts_reference(segments, points, stations):
    """Return (x, SF, BM, side) at every possible envelope-utilization peak.

    IACS UR S11A §2.2.1, local ur-s11a.pdf PDF p.8: still-water loads
    are "calculated at each section along the ship length". In this beam
    representation a concentrated force has two one-sided shear values;
    both must be assessed. On continuous intervals, all stationary ratios
    against both signed local envelopes and all boundaries suffice, since
    SF is cubic at most, BM quartic and each envelope is affine.
    """
    ordered = sorted(stations, key=lambda station: station.x_m)
    xs = np.asarray([s.x_m for s in ordered], dtype=float)
    limits = np.asarray([[s.min_shear_n, s.max_shear_n,
                          s.min_bending_nm, s.max_bending_nm] for s in ordered])
    if (len(xs) < 2 or not np.isfinite(xs).all() or np.any(np.diff(xs) <= 0)
            or not np.isfinite(limits).all()
            or np.any(limits[:, (0, 2)] >= 0) or np.any(limits[:, (1, 3)] <= 0)):
        raise ValueError('Strength requires distinct stations and finite permissible limits straddling zero.')

    events = defaultdict(list)
    jumps = defaultdict(list)
    for left, right, a, b, c in segments:
        events[left].append((a, b, c))
        d = right-left
        events[right].append((-(a+b*d+c*d*d), -(b+2*c*d), -c))
    for position, force in points:
        jumps[position].append(force)
    edges = sorted(set(events) | set(jumps) | set(xs))
    if xs[0] > edges[0]+1e-8 or xs[-1] < edges[-1]-1e-8:
        raise ValueError('Permissible envelope must cover the complete buoyant beam and all loads.')
    edge_limits = np.column_stack([np.interp(edges, xs, limits[:, k]) for k in range(4)])
    sf = bm = q0 = q1 = q2 = 0.
    cuts = []
    for i, left in enumerate(edges):
        jump = math.fsum(jumps[left])
        if jump:
            cuts.append((left, sf, bm, 'aft'))
        sf += jump
        cuts.append((left, sf, bm, 'fore' if jump else 'continuous'))
        if events[left]:
            q0 += math.fsum(e[0] for e in events[left])
            q1 += math.fsum(e[1] for e in events[left])
            q2 += math.fsum(e[2] for e in events[left])
        if i == len(edges)-1:
            break
        d = edges[i+1]-left
        # Normalize to u=(x-left)/d so root conditioning does not depend on
        # vessel length or narrow gaps between adjacent load boundaries.
        shear = np.array([sf, q0*d, q1*d*d/2, q2*d**3/3])
        bending = np.array([bm, -sf*d, -q0*d*d/2, -q1*d**3/6, -q2*d**4/12])
        candidates = set()
        for poly, columns in ((shear, (0, 1)), (bending, (2, 3))):
            for column in columns:
                start = edge_limits[i, column]
                change = edge_limits[i+1, column]-start
                candidates.update(_ratio_extrema(poly, start, change))
        for u in sorted(candidates):
            cuts.append((left+d*u, float(np.polynomial.polynomial.polyval(u, shear)),
                         float(np.polynomial.polynomial.polyval(u, bending)), 'continuous'))
        sf = float(sum(shear))
        bm = float(sum(bending))
        q0, q1 = q0+q1*d+q2*d*d, q1+2*q2*d
    return cuts


_ORIGINAL_ASSESSMENT_DEPENDENCIES = _assessment_dependencies()
