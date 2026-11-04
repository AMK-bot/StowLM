"""Exact provider liquid centroid/level without unused free-surface integration.

The stability GZ calculation needs liquid first moments, whereas the initial GM
calculation also needs the free-surface area inertia. For ``hull_sections`` this
helper uses the provider's identical volume validation, plane normal, safeguarded
Brent root, and polygon/quadrature moment integrator. It omits only the final
waterplane inertia loop. This is an acceleration, not a geometry approximation.
Other tank types retain their public ``liquid`` implementation. Empty/full
conditions also delegate to the public implementation.

The duplicated root settings and private provider primitives deliberately match
``SectionTankGeometry.liquid``; ``PROVIDER_SOURCE_SHA256`` identifies that entire
provider source for cache provenance. Tests compare against its public API.
Source: stowage.vessel.hull_tanks, finite-angle liquid polygon integration.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

from stowage.vessel import hull_tanks

PROVIDER_SOURCE_SHA256 = hashlib.sha256(Path(hull_tanks.__file__).read_bytes()).hexdigest()
_CACHE = OrderedDict()
_CACHE_SIZE = 8192


def _centroid(values):
    # A bytes-backed array cannot be made writable by a cache caller.
    array = np.asarray(values, dtype=np.float64)
    return np.frombuffer(array.tobytes(), dtype=np.float64).reshape(3)


def centroid_level(solid, volume_m3, heel_rad=0., trim_rad=0.):
    """Return immutable centroid (m) and liquid-plane level (m), at exact inputs.

    Raises the same volume/root errors as the hull-section liquid provider. A
    bounded cache retains the geometry object, preventing reused object IDs from
    aliasing a different tank. The provider's declared numerical/loft errors
    remain unchanged; no new integration approximation is introduced.
    """
    volume, heel, trim = map(float, (volume_m3, heel_rad, trim_rad))
    if getattr(solid, 'shape', None) != 'hull_sections':
        result = solid.liquid(volume, heel, trim)
        return _centroid(result.centroid_m), float(result.plane_level_m)
    volume = solid._volume(volume)
    normal = solid._normal(heel, trim)
    key = (id(solid), volume, heel, trim)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key][1]
    if volume in (0., solid.capacity_m3):
        result = solid.liquid(volume, heel, trim)
        answer = (_centroid(result.centroid_m), float(result.plane_level_m))
    else:
        corners = np.array([[x, y, z]
                            for x in (solid.x_min_m, solid.x_max_m)
                            for y in (solid.y_min_m, solid.y_max_m)
                            for z in (solid.z_min_m, solid.z_max_m)])
        projections = corners @ normal
        low, high = float(min(projections)), float(max(projections))

        def residual(level):
            if level <= low:
                return -volume
            if level >= high:
                return solid.capacity_m3-volume
            return solid._integrate(level, normal)[0]-volume

        level = brentq(residual, low, high, xtol=5e-14,
                       rtol=4*np.finfo(float).eps, maxiter=150)
        totals = solid._integrate(level, normal, surface=False)
        if abs(totals[0]-volume) > max(1e-10, 2e-10*volume):
            raise ArithmeticError('section tank liquid root failed volume tolerance')
        answer = (_centroid(totals[1:]/totals[0]), float(level))
    _CACHE[key] = (solid, answer)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return answer
