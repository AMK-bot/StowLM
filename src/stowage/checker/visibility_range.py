"""Exact azimuth intervals whose obstructed sea range exceeds a boundary.

For a point (x,y,z) in the equilibrium waterplane frame, the tangent sea
range is H*hypot(x,y)/(H-z). It exceeds R iff
H*hypot(x,y) + R*(z-H) > 0; points at/above the eye also qualify.

This cone's exterior is not convex, so clipping by a forward-distance
half-plane, or joining all qualifying corners, is insufficient. Instead,
intersect each clipped face edge analytically with the cone. At a fixed
azimuth the face's intersection with the radial vertical plane is a line
segment, and the expression above is affine on that segment. If any point
qualifies, an endpoint on a face edge qualifies. Projecting qualifying edge
segments therefore recovers the complete angular union, without sampling.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np


_FACES = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4),
          (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5))


def _clip(poly, coordinate, boundary):
    result = []
    for p, q in zip(poly, poly[1:] + poly[:1]):
        a, b = p[coordinate] - boundary, q[coordinate] - boundary
        if a >= 0:
            result.append(p)
        if (a >= 0) != (b >= 0):
            result.append(p + a / (a - b) * (q - p))
    return result


def _roots(a, b, c):
    """Real quadratic roots, with stable evaluation near cancellation."""
    if a == 0:
        return [] if b == 0 else [-c / b]
    discriminant = math.fsum((b * b, -4 * a * c))
    # The polynomial coefficients come from floating-point transformed corners.
    # Only absorb a discriminant's rounding error at a double root.
    error = 8 * math.ulp(max(abs(b * b), abs(4 * a * c)))
    if discriminant < -error:
        return []
    if abs(discriminant) <= error:
        return [-b / (2 * a)]
    q = -.5 * (b + math.copysign(math.sqrt(discriminant), b))
    return [q / a, c / q]


@lru_cache(maxsize=8192)
def _range_intersections(payload, height, radius):
    """Cache only exact cone/edge geometry, never a visibility verdict."""
    vertices = np.frombuffer(payload, dtype=np.float64).reshape(8, 3)
    return tuple(_range_blind_intervals_reference(vertices, height, radius))


def range_blind_intervals(vertices, eye_height_m, range_boundary_m):
    """Return numerical angular intersections for the exact supplied geometry.

    Every corner bit and both cone parameters identify the computation. Calls
    with new cargo, waterplane, eye or range therefore compute fresh geometry;
    callers still merge sectors and judge every visibility criterion afresh.
    """
    from stowage.checker.perf import reuse_enabled

    if not reuse_enabled() or _intersection_dependencies() != _ORIGINAL_INTERSECTION_DEPENDENCIES:
        return _range_blind_intervals_reference(vertices, eye_height_m, range_boundary_m)
    # Keep unusual/invalid input behavior on the original validation path.
    if type(vertices) not in (list, tuple, np.ndarray):
        return _range_blind_intervals_reference(vertices, eye_height_m, range_boundary_m)
    try:
        points = np.asarray(vertices, dtype=np.float64)
        height, radius = float(eye_height_m), float(range_boundary_m)
    except (TypeError, ValueError, OverflowError):
        return _range_blind_intervals_reference(vertices, eye_height_m, range_boundary_m)
    if (points.shape != (8, 3) or not np.isfinite(points).all()
            or not math.isfinite(height) or not math.isfinite(radius)
            or height <= 0 or radius <= 0):
        return _range_blind_intervals_reference(vertices, eye_height_m, range_boundary_m)
    return list(_range_intersections(points.tobytes(), height, radius))


def _range_blind_intervals_reference(vertices, eye_height_m, range_boundary_m):
    """Return forward-beam blind intervals for one transformed rectangular box.

    ``vertices`` has the existing itertools.product((xmin,xmax),
    (ymin,ymax),(zmin,zmax)) ordering after its rigid waterplane transform.
    Coordinates are forward, transverse, and height above sea; the eye is at
    (0,0,H). R is the declared horizontal radial sea-range boundary.
    """
    h, radius = float(eye_height_m), float(range_boundary_m)
    if not math.isfinite(h) or not math.isfinite(radius) or h <= 0 or radius <= 0:
        raise ValueError('visibility eye height and range boundary must be finite and positive')
    points = [np.asarray(p, dtype=float) for p in vertices]
    if len(points) != 8 or any(p.shape != (3,) or not np.all(np.isfinite(p)) for p in points):
        raise ValueError('visibility range geometry requires eight finite transformed box corners')
    intervals = []
    # SOLAS V/22.1.2, reproduced in IRS July 2026 Pt.5 Ch.31
    # §3.1.4.4, printed p.6 / PDF p.2439: "forward of the beam
    # obstructing the view of the sea surface as seen from the conning
    # position". Use horizontal range from that eye, not x projection.
    # APPROXIMATION: the finite sea-range threshold R retains D43's declared
    # interpretation; the clause itself does not prescribe that threshold.
    for indices in _FACES:
        face = _clip([points[i] for i in indices], 0, 1e-9)
        face = _clip(face, 2, 0.)
        for p, q in zip(face, face[1:] + face[:1]):
            delta = q - p
            # Dimensionless cone equation, avoiding unnecessarily large squares:
            # (x/R)^2 + (y/R)^2 - ((H-z)/H)^2 = 0.
            u = np.array([p[0] / radius, p[1] / radius, (h - p[2]) / h])
            d = np.array([delta[0] / radius, delta[1] / radius, -delta[2] / h])
            a = math.fsum((d[0]**2, d[1]**2, -d[2]**2))
            b = 2 * math.fsum((u[0]*d[0], u[1]*d[1], -u[2]*d[2]))
            c = math.fsum((u[0]**2, u[1]**2, -u[2]**2))
            cuts = [0., 1.] + [t for t in _roots(a, b, c) if 0. < t < 1.]
            # Squaring also finds the cone above the eye. Those extra cuts
            # are harmless: determine the original inequality on each piece.
            cuts = sorted(set(cuts))
            for lo, hi in zip(cuts, cuts[1:]):
                middle = p + ((lo + hi) / 2) * delta
                if h * math.hypot(middle[0], middle[1]) + radius * (middle[2] - h) <= 0:
                    continue
                ends = (p + lo * delta, p + hi * delta)
                angles = [math.atan2(end[1], end[0]) for end in ends]
                left, right = min(angles), max(angles)
                if left < right:
                    intervals.append((left, right))
    merged = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1e-12:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _intersection_dependencies():
    array_functions = (np.array, np.asarray, np.frombuffer, np.isfinite, np.all)
    return (_FACES, _range_blind_intervals_reference, _clip, _roots,
            getattr(_range_blind_intervals_reference, '__code__', None),
            getattr(_clip, '__code__', None), getattr(_roots, '__code__', None),
            math.hypot, math.fsum, math.sqrt, math.ulp, math.atan2, math.copysign,
            *array_functions, *(getattr(fn, '__code__', None) for fn in array_functions))


_ORIGINAL_INTERSECTION_DEPENDENCIES = _intersection_dependencies()
