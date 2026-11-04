"""Analytic heat-source clearances without inventing hull-section surfaces.

``BoxSourceGeometry`` declares a continuous rectangular solid, so its Euclidean
distance to a CTU envelope is exact. ``HullSectionsSourceGeometry`` instead
retains longitudinal Gauss samples, with possible gaps and missing mates. That
contract explicitly supplies no surface between stations (see model.vessel and
vessel.hull_tanks). It cannot determine a continuous solid's shortest distance.

For those samples we can still certify two statements: an enclosing source box
gives a lower bound on distance; an actual sampled cross-section point gives a
close witness. A lower bound at least the requirement proves clearance, and a
closer witness proves violation. Otherwise the result is unknown. Neither the
box nor sampled polygons are substituted for the whole physical source.
There is no panel interpolation, convex-hull filling or new approximation.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import hypot, isclose, isfinite, sqrt

from pydantic import BaseModel

from stowage.model.vessel import BoxSourceGeometry, HullSectionsSourceGeometry


_BOUND_KEYS = tuple(f'{axis}_{side}_m' for axis in 'xyz' for side in ('min', 'max'))


def _bounds(value):
    values = tuple(value[key] if isinstance(value, Mapping) else getattr(value, key) for key in _BOUND_KEYS)
    if any(type(number) not in (int, float) or not isfinite(number) for number in values):
        raise ValueError('source/CTU coordinates must be finite numbers')
    if any(values[index] > values[index + 1] for index in (0, 2, 4)):
        raise ValueError('source/CTU bounds must be ordered')
    return values


def _validated(envelope, actual_geometry, source_bounds):
    box = _bounds(envelope)
    raw = actual_geometry.model_dump(mode='python') if isinstance(actual_geometry, BaseModel) else actual_geometry
    if not isinstance(raw, Mapping):
        raise ValueError('actual source geometry is unknown')
    shape = raw.get('shape')
    if shape == 'rectangular_prism':
        geometry = BoxSourceGeometry.model_validate(raw)
    elif shape == 'hull_sections':
        geometry = HullSectionsSourceGeometry.model_validate(raw)
    else:
        raise ValueError('source has no supported explicit shape')
    bounds = _bounds(geometry)
    if source_bounds is not None and _bounds(source_bounds) != bounds:
        raise ValueError('actual geometry is detached from its physical source bounds')
    return box, geometry, bounds


def _box_distance(a, b):
    return hypot(*(max(a[index] - b[index + 1], b[index] - a[index + 1], 0.0)
                   for index in (0, 2, 4)))


def heat_source_distance(envelope, actual_geometry, *, source_bounds=None) -> float | None:
    """Return an exact solid distance only when the declared shape supplies it.

    ``source_bounds`` binds the geometry to the actual source envelope; any
    mismatch or malformed geometry is unknown. Hull-section samples always
    return None, including when a separate clearance bound can prove safety.
    """
    try:
        box, geometry, bounds = _validated(envelope, actual_geometry, source_bounds)
        return _box_distance(box, bounds) if geometry.shape == 'rectangular_prism' else None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def _point_rectangle_squared(y, z, rectangle):
    ymin, ymax, zmin, zmax = rectangle
    return max(ymin - y, y - ymax, 0.0) ** 2 + max(zmin - z, z - zmax, 0.0) ** 2


def _segment_rectangle_squared(a, b, rectangle):
    """Analytic minimum of a segment's piecewise-quadratic distance to a box."""
    direction = (b[0] - a[0], b[1] - a[1])
    breaks = {0.0, 1.0}
    for axis, (low, high) in enumerate((rectangle[:2], rectangle[2:])):
        if direction[axis] != 0:
            for boundary in (low, high):
                t = (boundary - a[axis]) / direction[axis]
                if 0 < t < 1:
                    breaks.add(t)
    ordered = sorted(breaks)
    candidates = list(ordered)
    for left, right in zip(ordered, ordered[1:]):
        midpoint = (left + right) / 2
        quadratic = linear_half = 0.0
        for axis, (low, high) in enumerate((rectangle[:2], rectangle[2:])):
            value = a[axis] + midpoint * direction[axis]
            boundary = low if value < low else high if value > high else None
            if boundary is not None:
                quadratic += direction[axis] ** 2
                linear_half += direction[axis] * (a[axis] - boundary)
        if quadratic > 0:
            minimum = -linear_half / quadratic
            if left < minimum < right:
                candidates.append(minimum)
    return min(_point_rectangle_squared(a[0] + t * direction[0], a[1] + t * direction[1], rectangle)
               for t in candidates)


def _point_in_polygon(point, polygon):
    """Ray crossing for the filled, possibly nonconvex section polygon."""
    y, z = point
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > z) != (b[1] > z):
            crossing = a[0] + (z - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if y < crossing:
                inside = not inside
    return inside


def _polygon_rectangle_squared(polygon, rectangle):
    # If the rectangle lies inside the polygon, no polygon edge need touch it.
    # Other containment/intersection cases have zero segment-box distance.
    if _point_in_polygon((rectangle[0], rectangle[2]), polygon):
        return 0.0
    return min(_segment_rectangle_squared(a, b, rectangle)
               for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def _below(distance, required):
    # Match the placement checker's nanometre arithmetic guard at an exact
    # regulatory boundary; this is not a physical clearance allowance.
    return distance < required and not isclose(distance, required, rel_tol=0.0, abs_tol=1e-9)


def heat_source_clearance(envelope, actual_geometry, *, minimum_clearance_m=2.4,
                          source_bounds=None) -> bool | None:
    """Certify clearance, violation, or unknown against an actual source shape.

    Rectangular sources are decided by their exact solid distance. Sampled
    hull-section geometry gives only enclosing-box lower bounds and actual
    section witnesses. In particular, absence of a close sampled section can
    never prove the unsampled space clear.
    """
    try:
        if (type(minimum_clearance_m) not in (int, float) or not isfinite(minimum_clearance_m)
                or minimum_clearance_m < 0):
            return None
        box, geometry, bounds = _validated(envelope, actual_geometry, source_bounds)
        lower_bound = _box_distance(box, bounds)
        if not _below(lower_bound, minimum_clearance_m):
            return True
        if geometry.shape == 'rectangular_prism':
            return False
        rectangle = box[2:]
        for x, polygons in zip(geometry.x_m, geometry.sections_yz_m):
            dx = max(box[0] - x, x - box[1], 0.0)
            # This station alone cannot witness a closer point if its x gap
            # already meets the full clearance requirement.
            if not _below(dx, minimum_clearance_m):
                continue
            for polygon in polygons:
                transverse_squared = _polygon_rectangle_squared(polygon, rectangle)
                if _below(hypot(dx, sqrt(transverse_squared)), minimum_clearance_m):
                    return False
        return None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None
