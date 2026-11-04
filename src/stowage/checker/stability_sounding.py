"""Precomputed exact upright tank sounding polynomials (IS Code B 3.1.8).

A horizontal slice through straight polygon edges has area linear in its height,
first transverse moment quadratic, and second transverse moment cubic. Integrate
these polynomials once with the tank's recorded longitudinal Gauss weights, and
store the coefficients. A sounding then inverts the monotone volume polynomial
and evaluates mass moments and central free-surface inertia, without clipping or
integrating geometry. This is analytic interpolation of the source section solid;
only the upstream longitudinal hull quadrature approximation remains. Empty and
full tanks have zero FSM; the adjacent slack-fill limit is retained separately.

Tables and their whole-fill inertia maxima are cached under
experiments/cache/stability, keyed by all section geometry, its own builder version. These geometric quantities are independent of water density. Cache
misses preprocess the solid; caches are disposable and do not change the answer.
"""
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import os
import tempfile
from zipfile import BadZipFile

import numpy as np
from numpy.polynomial import polynomial as poly
from scipy.optimize import brentq

from stowage.model import TankSoundingPoint
from stowage.checker.stability_tank_envelope import max_fsm as _reference_maximum

STABILITY_SOUNDING_VERSION = 1

CACHE_DIRECTORY = Path(__file__).resolve().parents[3] / 'experiments/cache/stability'


@dataclass(frozen=True)
class SoundingTable:
    levels: np.ndarray
    cumulative: np.ndarray
    integrals: np.ndarray
    surface: np.ndarray
    capacity: float
    empty_centroid: tuple
    maximum: tuple

    def sounding(self, volume_m3):
        volume = float(volume_m3)
        if not np.isfinite(volume) or not 0 <= volume <= self.capacity:
            raise ValueError('tank volume must be finite and between zero and capacity')
        if volume == 0:
            xyz, inertia = self.empty_centroid, 0.
        elif volume == self.capacity:
            xyz, inertia = self.cumulative[-1, 1:]/self.cumulative[-1, 0], 0.
        else:
            # Right interval at a horizontal ledge, matching the provider's
            # inclusive liquid plane convention. No interpolation across a ledge.
            i = min(len(self.levels)-2, int(np.searchsorted(self.cumulative[:, 0], volume, side='right')-1))
            target = volume-self.cumulative[i, 0]
            span = self.levels[i+1]-self.levels[i]
            coefficients = self.integrals[i]
            total = poly.polyval(1., coefficients[0])*span
            target = min(max(0., target), total)
            u = (0. if target == 0 else 1. if target == total else
                 brentq(lambda q: span*poly.polyval(q, coefficients[0])-target, 0., 1., xtol=5e-15))
            moments = self.cumulative[i]+span*np.array([poly.polyval(u, c) for c in coefficients])
            xyz = moments[1:]/moments[0]
            area, first, second = [poly.polyval(u, c) for c in self.surface[i]]
            inertia = max(0., second-first*first/area) if area > 0 else 0.
        return TankSoundingPoint(volume_m3=volume, lcg_m=float(xyz[0]), tcg_m=float(xyz[1]),
                                 vcg_m=float(xyz[2]), fsm_m4=float(inertia))


def _key(solid):
    digest = sha256(str(STABILITY_SOUNDING_VERSION).encode())
    for name in ('x_m', 'integration_weights_m'):
        digest.update(np.asarray(getattr(solid, name), dtype='<f8').tobytes())
    for section in solid.sections_yz_m:
        digest.update(len(section).to_bytes(8, 'little'))
        for polygon in section:
            array = np.asarray(polygon, dtype='<f8')
            digest.update(np.asarray(array.shape, dtype='<i8').tobytes())
            digest.update(array.tobytes())
    digest.update(np.array([getattr(solid, f'{a}_{b}_m') for a in 'xyz' for b in ('min', 'max')], dtype='<f8').tobytes())
    return digest.hexdigest()


def _build(solid, *, include_maximum=True):
    origin = np.longdouble((solid.y_min_m+solid.y_max_m)/2)
    records, heights = [], set()
    for x, weight, section in zip(solid.x_m, solid.integration_weights_m, solid.sections_yz_m, strict=True):
        for polygon in section:
            heights.update(float(z) for z in polygon[:, 1])
            for start, end in zip(polygon, np.roll(polygon, -1, axis=0), strict=True):
                dz = np.longdouble(end[1])-np.longdouble(start[1])
                if dz:
                    records.append((start[0]-origin, start[1], np.longdouble(end[0])-np.longdouble(start[0]), dz, weight*np.sign(dz), x, end[1]))
    edges = np.asarray(records, dtype=np.longdouble)
    # Compare original endpoints. Reconstructing end=start+(end-start) can move
    # an almost horizontal edge past its next height event by one rounding bit;
    # its huge slope then corrupts the entire following interval.
    low = np.minimum(edges[:, 1], edges[:, 6])
    high = np.maximum(edges[:, 1], edges[:, 6])
    levels = np.array(sorted(heights))
    cumulative = [np.zeros(4, dtype=np.longdouble)]
    integral_rows, surfaces = [], []
    for left, right in zip(levels[:-1], levels[1:], strict=True):
        span = np.longdouble(right-left)
        active = edges[(low < right) & (high > left)]
        intercept = active[:, 0]+(np.longdouble(left)-active[:, 1])*active[:, 2]/active[:, 3]
        slope = span*active[:, 2]/active[:, 3]
        weight = active[:, 4]
        area = np.array([np.sum(weight*intercept), np.sum(weight*slope)])
        q = np.array([np.sum(weight*intercept**2)/2, np.sum(weight*intercept*slope), np.sum(weight*slope**2)/2])
        second = np.array([np.sum(weight*intercept**3)/3, np.sum(weight*intercept**2*slope),
                           np.sum(weight*intercept*slope**2), np.sum(weight*slope**3)/3])
        xmoment = np.array([np.sum(weight*active[:, 5]*intercept), np.sum(weight*active[:, 5]*slope)])
        ymoment = poly.polyadd(q, origin*area)
        zmoment = poly.polymul(area, [left, span])
        integrals = np.zeros((4, 4), dtype=np.longdouble)
        for j, moment in enumerate((area, xmoment, ymoment, zmoment)):
            integrated = poly.polyint(moment)
            integrals[j, :len(integrated)] = integrated
        surface = np.zeros((3, 4), dtype=np.longdouble)
        for j, coefficients in enumerate((area, q, second)):
            surface[j, :len(coefficients)] = coefficients
        integral_rows.append(integrals)
        surfaces.append(surface)
        cumulative.append(cumulative[-1]+span*np.array([poly.polyval(1., row) for row in integrals]))
    cumulative = np.array(cumulative, dtype=np.float64)
    if not np.isclose(cumulative[-1, 0], solid.capacity_m3, rtol=2e-9, atol=1e-9):
        raise ArithmeticError(f'precomputed sounding volume {cumulative[-1, 0]} disagrees with solid {solid.capacity_m3}')
    # Empty centroid carries zero mass; retain the provider convention.
    empty = solid.sounding(0.)
    best, best_volume = 0., 0.
    for i, (area, q, second) in enumerate(surfaces if include_maximum else ()):
        aa = poly.polymul(area, area)
        derivative = poly.polysub(poly.polymul(poly.polyder(second), aa),
            2*poly.polymul(poly.polymul(q, poly.polyder(q)), area))
        derivative = poly.polyadd(derivative, poly.polymul(poly.polymul(q, q), poly.polyder(area)))
        magnitude = np.max(np.abs(derivative))
        candidates = [0., 1.]
        if magnitude > 0:
            coefficients = np.trim_zeros(np.asarray(derivative/magnitude, dtype=float), 'b')
            if len(coefficients) > 1:
                candidates.extend(float(r.real) for r in poly.polyroots(coefficients)
                                  if abs(r.imag) < 1e-8 and 0 < r.real < 1)
        for u in candidates:
            a = poly.polyval(u, area)
            if a > 0:
                inertia = float(max(0., poly.polyval(u, second)-poly.polyval(u, q)**2/a))
                if inertia > best:
                    best = inertia
                    best_volume = float(cumulative[i, 0]+(levels[i+1]-levels[i])*poly.polyval(u, integral_rows[i][0]))
    return SoundingTable(levels, cumulative, np.array(integral_rows, dtype=np.float64),
                         np.array(surfaces, dtype=np.float64), float(solid.capacity_m3),
                         (empty.lcg_m, empty.tcg_m, empty.vcg_m),
                         (best, min(float(solid.capacity_m3), max(0., best_volume))))


@lru_cache(maxsize=128)
def lookup(solid):
    """Return the persisted analytic sounding table for a section tank."""
    if getattr(solid, 'shape', None) != 'hull_sections':
        raise ValueError('precomputed sounding lookup requires hull_sections geometry')
    path = CACHE_DIRECTORY / ('sounding-'+_key(solid)+'.npz')
    try:
        with np.load(path, allow_pickle=False) as saved:
            if saved['capacity'].shape != ():
                raise ValueError('sounding capacity must be scalar')
            table = SoundingTable(*(saved[k] for k in ('levels', 'cumulative', 'integrals', 'surface')),
                                 float(saved['capacity']), tuple(saved['empty_centroid']), tuple(saved['maximum']))
            _validate_cache(table, solid)
            return table
    except (OSError, ValueError, TypeError, IndexError, KeyError, EOFError, BadZipFile):
        table = _build(solid)
    temporary = None
    try:
        CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix='sounding-', suffix='.npz', dir=CACHE_DIRECTORY)
        os.close(handle)
        np.savez_compressed(temporary, levels=table.levels, cumulative=table.cumulative,
                            integrals=table.integrals, surface=table.surface, capacity=table.capacity,
                            empty_centroid=table.empty_centroid, maximum=table.maximum)
        os.replace(temporary, path)
    except OSError:
        # Persistence is optional: an unwritable cache must not turn an exact
        # successfully precomputed sounding into a failed stability check.
        pass
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return table


def _validate_cache(table, solid):
    """Reject malformed arrays and physically invalid cached scalar metadata."""
    n = len(table.levels) if table.levels.ndim == 1 else 0
    if (n < 2 or table.cumulative.shape != (n, 4) or
            table.integrals.shape != (n-1, 4, 4) or table.surface.shape != (n-1, 3, 4) or
            np.shape(table.empty_centroid) != (3,) or np.shape(table.maximum) != (2,)):
        raise ValueError('invalid sounding cache dimensions')
    if not all(np.isfinite(a).all() for a in
               (table.levels, table.cumulative, table.integrals, table.surface,
                table.capacity, table.empty_centroid, table.maximum)):
        raise ValueError('nonfinite sounding cache')
    if (table.capacity <= 0 or not np.isclose(table.capacity, solid.capacity_m3, rtol=1e-12, atol=1e-12) or
            np.any(np.diff(table.levels) <= 0) or np.any(np.diff(table.cumulative[:, 0]) < 0) or
            np.any(table.cumulative[0] != 0) or
            not np.isclose(table.cumulative[-1, 0], table.capacity, rtol=2e-9, atol=1e-9) or
            table.maximum[0] < 0 or not 0 <= table.maximum[1] <= table.capacity):
        raise ValueError('invalid sounding cache physical metadata')


def get_sounding(solid, volume_m3):
    """Upright sounding: closed form for boxes, precomputed polynomials otherwise."""
    if getattr(solid, 'shape', None) == 'rectangular_prism':
        return solid.sounding(volume_m3)
    return lookup(solid).sounding(volume_m3)


def max_fsm(solid):
    """Whole-fill maximum, precomputed once and stored with the sounding table."""
    if getattr(solid, 'shape', None) == 'rectangular_prism':
        return _reference_maximum(solid)
    return lookup(solid).maximum

# Descriptive aliases used by the production loading assembler.
sounding = get_sounding
maximum_fsm = max_fsm
