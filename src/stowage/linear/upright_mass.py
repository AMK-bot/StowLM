"""Exact upright displacement without repeatedly clipping every hull polygon.

Between polygon vertex heights, section breadth is affine in height and its
integral (immersed area) is quadratic. Evaluating those local polynomials and
using HullGeometry's original Gauss weights gives the same upright volume as
polygon clipping, with no interpolated hydrostatics or uniform-grid assumption.
Source: Biran & López-Pulido, Ship Hydrostatics and Stability (2014), ch. 2.

Only section coordinates and quadrature weights determine these coefficients;
background loads, water density and other components' versions do not. D30
caches their exact content hash plus this builder's own version. Density is a
cheap multiplication at evaluation time. Local coordinates and long-double
accumulation avoid cancellation around almost-horizontal polygon edges.
"""
from __future__ import annotations

import hashlib
import numpy as np
from scipy.optimize import brentq

UPRIGHT_MASS_VERSION = '1.0'
_MEMORY = {}


def _key(geometry):
    digest = hashlib.sha256(('upright-volume:' + UPRIGHT_MASS_VERSION).encode())
    for array in (geometry.integration_weights_m,
                  *(p for section in geometry.sections for p in section)):
        array = np.asarray(array, dtype='<f8')
        digest.update(str(array.shape).encode())
        digest.update(array.tobytes())
    # Section boundaries assign the correct quadrature weight to each polygon.
    digest.update(str(tuple(len(section) for section in geometry.sections)).encode())
    return digest.hexdigest()


def _build(geometry):
    from stowage.linear.physics import _polygon_upright_polynomials
    rows = []
    for section, weight in zip(geometry.sections, geometry.integration_weights_m, strict=True):
        for polygon in section:
            knots, volume, *_, full_volume, full_moment = _polygon_upright_polynomials(polygon)
            rows.append((knots, volume[:, :3] * weight, full_volume * weight))
    width = max(len(row[0]) for row in rows)
    # Repeated final knots are harmless padding: no infinite cache values.
    knots = np.array([np.pad(z, (0, width-len(z)), mode='edge') for z, _, _ in rows], dtype=np.longdouble)
    coefficients = np.zeros((len(rows), width, 3), dtype=np.longdouble)
    counts = np.array([len(z)-1 for z, _, _ in rows], dtype=np.int64)
    for i, (z, volume, full) in enumerate(rows):
        coefficients[i, :len(volume)] = volume
        coefficients[i, len(volume):, 0] = full
    return dict(knots=knots, coefficients=coefficients, counts=counts)


def _valid(payload):
    if not isinstance(payload, dict) or set(payload) != {'knots', 'coefficients', 'counts'}:
        return False
    z, c, n = (payload[k] for k in ('knots', 'coefficients', 'counts'))
    return (isinstance(z, np.ndarray) and z.ndim == 2 and len(z) > 0
            and isinstance(c, np.ndarray) and c.shape == (*z.shape, 3)
            and isinstance(n, np.ndarray) and n.shape == (len(z),)
            and n.dtype.kind in 'iu'
            and np.all((n > 0) & (n < z.shape[1]))
            and np.all(np.isfinite(z)) and np.all(np.isfinite(c))
            and np.all(np.diff(z, axis=1) >= 0))


class UprightMass:
    """Density-independent, exact piecewise-polynomial upright volume."""
    def __init__(self, payload):
        self.knots = payload['knots']
        self.coefficients = payload['coefficients']
        self.counts = payload['counts']
        self.rows = np.arange(len(self.knots))
        self._caps = {}

    def capped_mass(self, geometry, draught_m, density_kg_m3):
        """Intersect equivalent polynomial/clipped endpoints, once per cap.

        Their floating-point sums can differ by an ulp. A validity interval
        must remain inside the coefficient family's canonical clipped upper
        bound; intersecting them avoids allowing even that tiny extrapolation.
        The geometry content was checked before selecting this polynomial.
        """
        key = (float(draught_m), float(density_kg_m3))
        if key not in self._caps:
            clipped = geometry.submerged(draught_m, draught_m, 0., density_kg_m3).displacement_kg
            self._caps[key] = min(self.mass(draught_m, density_kg_m3), float(clipped))
        return self._caps[key]

    def mass(self, draught_m, density_kg_m3):
        if not np.isfinite([draught_m, density_kg_m3]).all() or density_kg_m3 <= 0:
            raise ValueError('finite draught and positive finite density required')
        d = np.longdouble(draught_m)
        indices = np.sum(self.knots <= d, axis=1)-1
        wet = indices >= 0
        indices = np.clip(indices, 0, self.counts)
        delta = d-self.knots[self.rows, indices]
        c = self.coefficients[self.rows, indices]
        volumes = c[:, 0]+delta*(c[:, 1]+delta*c[:, 2])
        return float(np.sum(np.where(wet, volumes, 0), dtype=np.longdouble)*density_kg_m3)

    def draught(self, mass_kg, density_kg_m3, maximum_m):
        return brentq(lambda d: self.mass(d, density_kg_m3)-mass_kg, .001, maximum_m-.001)


def upright_mass(geometry):
    """Reuse content-addressed coefficients; foreign geometry keeps its API."""
    # Preserve custom HullGeometry subclass/fixture semantics rather than
    # silently replacing a caller's deliberately overridden submerged method.
    from stowage.vessel.hydrostatics import HullGeometry
    if type(geometry) is not HullGeometry:
        return None
    key = _key(geometry)
    if key not in _MEMORY:
        from stowage.linear.cache import load_payload, store_payload
        payload = load_payload(key)
        if not _valid(payload):
            payload = _build(geometry)
            store_payload(key, payload)
        for value in payload.values():
            value.flags.writeable = False
        if len(_MEMORY) >= 8:
            _MEMORY.pop(next(iter(_MEMORY)))
        _MEMORY[key] = UprightMass(payload)
    return _MEMORY[key]
