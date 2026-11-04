"""Persisted actual-fluid-transfer cross curves (2008 IS Code B 3.1.9.1).

Each grid contains *liquid first mass moments per unit density*, indexed by
fill fraction, heel and trim. Local four-point tensor Lagrange interpolation
preserves the zero-volume endpoint; interpolating centroids there would invent
an endpoint singularity. Whole-fill transfer uses all stationary points of a
piecewise cubic moment interpolant, rather than selecting a nominal half fill.

Preprocessing rotates the actual section polygons into the liquid-plane frame.
Between consecutive vertex heights their wet volume and first moments are low
order polynomials. Inverting those polynomials samples every requested fill
without repeatedly clipping geometry. This transformation retains the provider's
longitudinal section weights. Constant-section solids with adaptive longitudinal
quadrature are deliberately left to the direct provider when trim is nonzero.

APPROXIMATION: finite fill/heel/trim interpolation is a numerical approximation;
production lookup requires an independent SHA-bound error certificate. Outside
its certified grid the caller must use direct method .1, never extrapolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from zipfile import BadZipFile
import json
import os
import tempfile
from datetime import datetime, timezone

import numpy as np
from scipy.interpolate import CubicSpline

CACHE_DIRECTORY = Path(__file__).resolve().parents[3] / 'experiments/cache/stability'
STABILITY_LIQUID_TABLES_VERSION = 2
LEGACY_LIQUID_BUILDER_SHA256 = 'e3491fadc6e1a46d0238ff64fc615323f833bb526bfb8565ada145ed36410b2e'
LEGACY_SOUNDING_BUILDER_SHA256 = 'e42d5b87b659718d38a1503d1c497d08becfcd3202127d52994ec3193f232940'
DEFAULT_FILLS = (1-np.cos(np.linspace(0, np.pi, 257)))/2
# Ten degrees beyond the former cross-curve limit covers listed departures.
# Every tank, including peaks, retains the entire closed fill interval [0, 1].
# 257 Chebyshev fills x 57 heels x 13 trims resolve the widened envelope.
# Grid density is empirical: a certified coarser grid covering the same physical
# domain remains valid; each new grid requires its own independent certificate.
DEFAULT_HEELS = np.deg2rad(np.arange(-70., 70.01, 2.5))
DEFAULT_TRIMS = np.linspace(-.06, .06, 13)


def _provenance(solid):
    """Physical identity is independent of unrelated component version bumps."""
    geometry = sha256()
    for name in ('x_m', 'integration_weights_m'):
        array = np.asarray(getattr(solid, name), dtype='<f8')
        geometry.update(np.asarray(array.shape, dtype='<i8').tobytes())
        geometry.update(array.tobytes())
    for section in solid.sections_yz_m:
        geometry.update(len(section).to_bytes(8, 'little'))
        for polygon in section:
            array = np.asarray(polygon, dtype='<f8')
            geometry.update(np.asarray(array.shape, dtype='<i8').tobytes())
            geometry.update(array.tobytes())
    geometry.update(np.array([getattr(solid, f'{a}_{b}_m') for a in 'xyz' for b in ('min', 'max')], dtype='<f8').tobytes())
    legacy_geometry_hash=geometry.hexdigest()
    geometry.update(np.asarray([solid.capacity_m3, float(getattr(solid,'_prismatic',False))],dtype='<f8').tobytes())
    return {'physical_identity': geometry.hexdigest(), 'geometry_hash': geometry.hexdigest(), 'legacy_geometry_hash':legacy_geometry_hash,
            'builder_version': STABILITY_LIQUID_TABLES_VERSION}


def _grid_spec(fills, heels, trims):
    return {name:np.asarray(axis,dtype=float).tolist() for name,axis in
            (('fills',fills),('heels',heels),('trims',trims))}


def _base_identity(solid):
    return sha256(json.dumps(_provenance(solid),sort_keys=True).encode()).hexdigest()


def _identity(solid, grid=None):
    base=_base_identity(solid)
    if grid is None:
        matches=sorted((p for p in CACHE_DIRECTORY.glob('liquid-'+base+'-*.npz')
                        if '.partial.' not in p.name and '.tmp.' not in p.name),
                       key=lambda p:p.stat().st_mtime_ns,reverse=True)
        if matches: return matches[0].stem.removeprefix('liquid-')
        grid=_grid_spec(DEFAULT_FILLS,DEFAULT_HEELS,DEFAULT_TRIMS)
    return base+'-'+sha256(json.dumps(grid,sort_keys=True).encode()).hexdigest()[:16]


def _raw_path(solid):
    return CACHE_DIRECTORY / ('liquid-raw-'+_base_identity(solid)+'.npz')


def _path(solid, grid=None):
    return CACHE_DIRECTORY / ('liquid-'+_identity(solid,grid)+'.npz')


def _stencil_reference(axis, value):
    if not np.isfinite(value) or value < axis[0]-1e-13 or value > axis[-1]+1e-13:
        raise ValueError('liquid-table query outside certified grid')
    start = min(max(0, int(np.searchsorted(axis, value))-2), len(axis)-4)
    indexes = np.arange(start, start+4)
    nodes = axis[indexes]
    weights = np.ones(4)
    for i in range(4):
        for j in range(4):
            if i != j:
                weights[i] *= (value-nodes[j])/(nodes[i]-nodes[j])
    return indexes, weights


def _stencil(axis, value):
    if _stencil_dependencies() != _ORIGINAL_STENCIL_DEPENDENCIES:
        return _stencil_reference(axis, value)
    from stowage.checker.stability_reuse import axis_query
    return axis_query(_stencil_reference, axis, value)


def _stencil_dependencies():
    functions = (_stencil_reference, np.isfinite, np.searchsorted, np.arange, np.ones, min, max, int, len, range)
    return (*functions, *(getattr(function, '__code__', None) for function in functions))


_ORIGINAL_STENCIL_DEPENDENCIES = _stencil_dependencies()


@dataclass(eq=False)
class LiquidTable:
    fills: np.ndarray
    heels: np.ndarray
    trims: np.ndarray
    moments: np.ndarray  # heel, trim, fill, xyz; volume*centroid
    capacity: float
    empty_centroid: np.ndarray
    prismatic: bool = False
    _angular_cache: dict = field(default_factory=dict, repr=False)
    _fill_cache: dict = field(default_factory=dict, repr=False)

    def _at_angles(self, heel, trim):
        heel, trim = float(heel), float(trim)
        if self.prismatic and abs(trim) > 1e-14:
            raise ValueError('prismatic adaptive longitudinal quadrature requires direct method')
        key = (heel, trim)
        if key in self._angular_cache:
            return self._angular_cache[key]
        hi, hw = _stencil(self.heels, heel)
        ti, tw = _stencil(self.trims, trim)
        result = np.einsum('i,j,ijkd->kd', hw, tw, self.moments[hi[:, None], ti[None, :]], optimize=False)
        if len(self._angular_cache) >= 256:
            self._angular_cache.clear()
        self._angular_cache[key] = result
        return result

    def centroid(self, volume_m3, heel_rad=0., trim_rad=0.):
        volume = float(volume_m3)
        if not np.isfinite(volume) or not 0 <= volume <= self.capacity:
            raise ValueError('liquid-table volume outside capacity')
        heel, trim = float(heel_rad), float(trim_rad)
        if self.prismatic and abs(trim) > 1e-14:
            raise ValueError('prismatic adaptive longitudinal quadrature requires direct method')
        hi, hw = _stencil(self.heels, heel)
        ti, tw = _stencil(self.trims, trim)
        if volume == 0:
            return tuple(self.empty_centroid)
        if volume == self.capacity:
            return tuple(self.moments[0, 0, -1]/volume)
        if volume not in self._fill_cache:
            if len(self._fill_cache) >= 128:
                self._fill_cache.clear()
            self._fill_cache[volume] = _stencil(self.fills, volume/self.capacity)
        fi, fw = self._fill_cache[volume]
        block = self.moments[hi[:, None, None], ti[None, :, None], fi[None, None, :]]
        return tuple(np.einsum('i,j,k,ijkd->d', hw, tw, fw, block, optimize=False)/volume)

    def max_transfer(self, heel_rad, trim_rad=0., *, tolerance_m4):
        if not np.isfinite(tolerance_m4) or tolerance_m4 <= 0:
            raise ValueError('positive finite transfer tolerance required')
        heel, trim = float(heel_rad), float(trim_rad)
        tilted = self._at_angles(heel, trim)
        upright = self._at_angles(0., 0.)
        normal = np.array([np.tan(trim)*np.cos(heel), -np.sin(heel), np.cos(heel)])
        normal /= np.linalg.norm(normal)
        along = np.array([1., 0., -np.tan(trim)])
        along /= np.linalg.norm(along)
        direction = np.cross(normal, along)*(-1 if heel < 0 else 1)
        values = (tilted-upright) @ direction
        spline = CubicSpline(self.fills, values)
        roots = spline.derivative().roots(extrapolate=False)
        candidates = np.r_[self.fills, roots[np.isfinite(roots)]]
        # The certificate covers interpolation error. tolerance_m4 controls the
        # reference envelope, not a fictitious analytic bound on this table.
        return float(max(0., np.max(spline(candidates))))


def _angle_moments(solid, heel, trim, fills):
    """Exact section-edge polynomial integration; no interpolation at knots.

    Version 2 uses the vectorised edge-prefix kernel instead of rebuilding a
    sounding polynomial by clipping every vertex-height slab. The represented
    geometry and Gauss weights are unchanged; fresh certificates measure any
    floating-point difference independently against the direct reference.
    """
    from stowage.checker.stability_tank_transfer import angle_moments
    return angle_moments(solid, heel, trim, fills)


def _validate(table):
    for axis in (table.fills, table.heels, table.trims):
        if axis.ndim != 1 or len(axis) < 4 or not np.isfinite(axis).all() or np.any(np.diff(axis) <= 0):
            raise ValueError('invalid liquid table axis')
    if table.fills[0] != 0 or table.fills[-1] != 1 or not np.isfinite(table.capacity) or table.capacity <= 0:
        raise ValueError('invalid liquid table fill/capacity')
    if table.moments.shape != (len(table.heels), len(table.trims), len(table.fills), 3) or not np.isfinite(table.moments).all():
        raise ValueError('invalid liquid table moment array')
    if not np.allclose(table.moments[:, :, 0], 0., atol=1e-10):
        raise ValueError('liquid table empty moments must vanish')


def build(solid, *, fills=None, heels=None, trims=None):
    """Explicit offline build. Runtime lookup never initiates a grid build."""
    if getattr(solid, 'shape', None) != 'hull_sections':
        raise ValueError('liquid cross curves require explicit section geometry')
    fills = np.asarray(DEFAULT_FILLS if fills is None else fills, dtype=float)
    heels = np.asarray(DEFAULT_HEELS if heels is None else heels, dtype=float)
    trims = np.asarray(DEFAULT_TRIMS if trims is None else trims, dtype=float)
    # Reuse exact existing nodes when widening only the angular domain. Grid
    # keys change and require fresh certificates; the integration itself does
    # not change, so no builder-version bump or migration waiver is involved.
    raw_path = _raw_path(solid)
    grid = _grid_spec(fills, heels, trims)
    partial_path = _path(solid, grid).with_suffix('.partial.npz')
    moments = np.full((len(heels), len(trims), len(fills), 3), np.nan)
    candidates = [raw_path]
    candidates.extend(sorted(CACHE_DIRECTORY.glob('liquid-'+_base_identity(solid)+'-*.npz')))
    candidates.append(partial_path)
    expected = _provenance(solid)
    for candidate in candidates:
        try:
            with np.load(candidate, allow_pickle=False) as saved:
                previous = json.loads(str(saved['provenance_json']))
                if previous != expected or not np.array_equal(saved['fills'], fills):
                    continue
                old = saved['moments']
                old_heels, old_trims = saved['heels'], saved['trims']
                if old.shape != (len(old_heels),len(old_trims),len(fills),3):
                    continue
                for i, heel in enumerate(heels):
                    ih = np.flatnonzero(np.isclose(old_heels,heel,rtol=0.,atol=1e-13))
                    if not len(ih): continue
                    for j, trim in enumerate(trims):
                        it = np.flatnonzero(np.isclose(old_trims,trim,rtol=0.,atol=1e-13))
                        if len(it) and np.isfinite(old[ih[0],it[0]]).all():
                            moments[i,j] = old[ih[0],it[0]]
        except (OSError, ValueError, TypeError, KeyError, IndexError, EOFError, BadZipFile):
            continue
    empty = solid.liquid(0.).centroid_m
    table = LiquidTable(fills, heels, trims, moments, float(solid.capacity_m3),
                        np.array(empty), bool(getattr(solid, '_prismatic', False)))
    for i, heel in enumerate(heels):
        if np.isfinite(moments[i]).all(): continue
        for j, trim in enumerate(trims):
            if not np.isfinite(moments[i,j]).all():
                moments[i,j] = _angle_moments(solid, heel, trim, fills)
        # A reboot loses at most the current heel plane; partial arrays can
        # never enter lookup(), which requires a final SHA-bound certificate.
        _write(partial_path, table, solid)
    _validate(table)
    _write(raw_path, table, solid)
    _write(_path(solid,_grid_spec(fills,heels,trims)), table, solid)
    partial_path.unlink(missing_ok=True)
    _lookup.cache_clear()
    return table


def _write(path, table, solid):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.npz', delete=False) as file:
        temporary = file.name
    try:
        np.savez_compressed(temporary, fills=table.fills, heels=table.heels, trims=table.trims,
            moments=table.moments, capacity=table.capacity, empty_centroid=table.empty_centroid,
            prismatic=table.prismatic, provenance_json=json.dumps(_provenance(solid), sort_keys=True))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path, solid, *, version_strict):
    with np.load(path, allow_pickle=False) as saved:
        provenance = json.loads(str(saved['provenance_json']))
        expected = _provenance(solid)
        if provenance['physical_identity'] != expected['physical_identity']:
            raise ValueError('liquid table physical provenance mismatch')
        if version_strict and provenance['builder_version'] != expected['builder_version']:
            raise ValueError('liquid table builder version mismatch')
        table = LiquidTable(*(saved[k] for k in ('fills', 'heels', 'trims', 'moments')),
            float(saved['capacity']), saved['empty_centroid'], bool(saved['prismatic']))
    _validate(table)
    if not np.isclose(table.capacity, solid.capacity_m3, rtol=1e-12):
        raise ValueError('liquid table capacity mismatch')
    return table


def qualify(solid, statistics):
    """Attach independent error evidence; GZ/area/GM fractions must be <= .2."""
    for key in ('gz', 'area', 'gm'):
        fraction = statistics.get(key)
        if fraction is None or not np.isfinite(fraction) or not 0 <= fraction <= .2:
            raise ValueError('qualification requires gz/area/gm L5 fractions <= .2')
    if not np.isfinite(statistics.get('samples', 0)) or statistics.get('samples', 0) < 4:
        raise ValueError('qualification requires at least four independent samples')
    path = _path(solid)
    payload = dict(statistics, inputs_hash=_identity(solid), builder_version=STABILITY_LIQUID_TABLES_VERSION,
                   date=datetime.now(timezone.utc).isoformat(), sha256=sha256(path.read_bytes()).hexdigest())
    certificate = path.with_suffix('.qualified.json')
    temporary = certificate.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2)+'\n')
    os.replace(temporary, certificate)
    _lookup.cache_clear()


def lookup(solid, *, require_qualified=True):
    """Return a certified persisted grid, or None for direct-reference fallback."""
    if getattr(solid, 'shape', None) != 'hull_sections':
        return None
    return _lookup(solid, require_qualified)


@lru_cache(maxsize=128)
def _lookup(solid, require_qualified):
    path = _path(solid)
    try:
        if require_qualified:
            evidence = json.loads(path.with_suffix('.qualified.json').read_text())
            if evidence.get('inputs_hash') != _identity(solid) or evidence.get('builder_version') != STABILITY_LIQUID_TABLES_VERSION: return None
            if not np.isfinite(evidence.get('samples', 0)) or evidence.get('samples', 0) < 4 or evidence['sha256'] != sha256(path.read_bytes()).hexdigest():
                return None
            if any(not np.isfinite(evidence[k]) or not 0 <= evidence[k] <= .2 for k in ('gz', 'area', 'gm')):
                return None
        return _read(path, solid, version_strict=True)
    except (OSError, ValueError, TypeError, KeyError, IndexError, EOFError, BadZipFile):
        return None


def migrate_legacy(solid):
    """Offline D30 migration: certify only byte-verified, unchanged old grids.

    Numerical builders/interpolators were unchanged by the D30 metadata patch.
    Legacy source hashes must match that exact predecessor, and all provider
    source hashes must still match. The original measured evidence is retained.
    """
    if lookup(solid) is not None:
        return 'reused'
    from stowage.checker import stability_sounding
    if STABILITY_LIQUID_TABLES_VERSION != 1 or stability_sounding.STABILITY_SOUNDING_VERSION != 1:
        return 'missing'
    expected=_provenance(solid)
    from stowage.checker import stability_tank_envelope
    from stowage.vessel import tanks, hull_tanks, hydrostatics
    sources={Path(m.__file__).name:sha256(Path(m.__file__).read_bytes()).hexdigest()
             for m in (stability_tank_envelope,tanks,hull_tanks,hydrostatics)}
    sources.update({'stability_liquid_tables.py':LEGACY_LIQUID_BUILDER_SHA256,
                    'stability_sounding.py':LEGACY_SOUNDING_BUILDER_SHA256})
    candidates=sorted(CACHE_DIRECTORY.glob('liquid-*.npz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    for oldpath in candidates:
        if oldpath.name.startswith('liquid-raw-'): continue
        try:
            certificate=json.loads(oldpath.with_suffix('.qualified.json').read_text())
            if certificate['sha256'] != sha256(oldpath.read_bytes()).hexdigest(): continue
            with np.load(oldpath,allow_pickle=False) as saved:
                old=json.loads(str(saved['provenance_json']))
                if old.get('geometry_hash') != expected['legacy_geometry_hash'] or old.get('source_hashes') != sources: continue
                table=LiquidTable(*(saved[k] for k in ('fills','heels','trims','moments')),
                                  float(saved['capacity']),saved['empty_centroid'],bool(saved['prismatic']))
            _validate(table)
            if table.capacity != solid.capacity_m3: continue
            path=_path(solid,_grid_spec(table.fills,table.heels,table.trims))
            _write(path,table,solid);_write(_raw_path(solid),table,solid)
            qualify(solid,dict(certificate,legacy_evidence={'path':str(oldpath),
                    'sha256':certificate['sha256'],'reason':'D30 metadata-only migration; identical numerical arrays and provider sources'}))
            return 'migrated'
        except (OSError,ValueError,TypeError,KeyError,IndexError,EOFError,BadZipFile):
            continue
    return 'missing'
