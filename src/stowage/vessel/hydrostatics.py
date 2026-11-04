"""Numerical buoyancy from public offsets and a declared closed envelope (2.1).

``HullGeometry.from_params(raw_params, nx=48, nz=64)`` creates actual polygon
sections of the family in params/fleet/README.md. ``submerged(ta, tf, heel,
density)`` clips those sections against the water plane and returns volume,
ship-frame buoyancy centroid, waterplane integrals and sectional immersed areas.
``hydrostatic_row(T)`` is upright/even keel. ``equilibrium(mass, lcg, tcg, vcg,
density)`` solves volume and the two buoyancy/weight moment balances; infeasible
or unconverged conditions raise ValueError. A mathematical equilibrium can be
unstable: the caller must separately require positive GM and pass GZ criteria.
``kn(heel, mass, lcg_m=None, density_kg_m3=None)`` holds
heel while solving volume and longitudinal balance at free trim. The default
reference LCG is midships; G is on the baseline for a KN curve. Supplying a
physical KG belongs in equilibrium, not KN. No external loads or tank corrections
are included here. ``kn_curve(angles, mass, lcg_m, density_kg_m3)`` continues
the same physical roots between adjacent angles for efficient dense tables.
``trimmed_sectional_areas`` returns a full Submerged record,
including station areas and quadrature weights. ``_solve`` and ``kn`` use bounded
process-local caches keyed by exact input values, actual integrated geometry,
and implementation class; returned condition arrays are independent copies.
Rounded lookup indices never change physical inputs or allow false hits.
Measured offsets may use ``stowlm_offsets_v1`` (see hull_offsets.py); their
source contours replace the synthetic bilge family.

Offsets parameter schema (in the parsed fleet TOML)::

    [hull_form]
    family = "stowlm_offsets_v1"
    parent = "kcs"  # provenance identifier; no hidden shape lookup
    offsets_format = "kcs_fix"
    offsets_path = "data/raw/kcs/kcs.fix"
    offsets_sha256 = "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"
    reference_lpp_m = 230.0
    reference_beam_m = 32.2
    reference_draught_m = 10.8
    reference_depth_m = 16.0

The source .fix upper cutoff is 16 m; published KCS moulded depth is 19 m.
``reference_depth_m`` denotes the declared source sealing cutoff, not an
assertion that the published ship's depth is 16 m. Coordinates map as
x'=x*L/Lref, y'=y*B/Bref, and z'=z*T/Tref below the source draught. Above it,
z'=T+(z-Tref)*(D-T)/(Dref-Tref). Source contour correspondences are fixed
before this mapping, preserving underwater form coefficients. ``[envelope]``
must explicitly specify deck_z_m=D, count_deckhouse=false, closed coaming boxes
and any openings. All supplied source overhangs outside AP/FP remain buoyant;
``buoyant_x_min_m`` and ``buoyant_x_max_m`` expose them. Source checksum changes
fail explicitly. CSV/JSON contour schemas are documented in hull_offsets.py;
hull_dtc.py converts the pinned public DTC mesh to the same section schema.
The former separable family is retained solely for analytic regression tests;
build_hull_model rejects it for production assembly.

Axes: x forward of AP, y port, z above baseline. Positive heel immerses port;
waterplane z=ta+(tf-ta)*x/L+y*tan(heel). Trim is ta-tf. Draughts are the
centreline intercepts, not the lowest shell immersion. For the companion tank API use
trim_rad=atan((ta-tf)/L), matching its draught-intercept convention. ``waterplane_normal`` returns the unit normal directly. Centroids remain in the
ship frame. Waterplane inertias are about its centroid in orthonormal axes
parallel to the longitudinal plane and transverse to it, respectively.

Integration: composite two-point Gauss in x with mandatory breakpoints at taper
and coaming ends; circular-bilge/flare offsets are straight polygon chords, using equal arc-angle
bilge nodes and mandatory radius/design/deck boundaries. Polygon area and
first moments and clipped waterplane line moments are exact for these chords.
Doubling nx/nz halves the nominal grids. ``x_m`` are ascending Gauss stations;
``sectional_areas_m2`` use precisely those stations (not uniform Simpson points).
``integration_weights_m`` must be used to sum their buoyancy. The closed hull,
secured hatch covers and watertight coaming boxes count as buoyant; containers
and deckhouse do not. Open hatches invalidate this envelope. Nonempty declared
openings require a downstream downflooding check; they do not magically seal.

APPROXIMATION: stations are linearly lofted using normalised source contour arc
length. A watertight weather deck seals the declared upper source cutoff;
the missing KCS 16-to-19 m detail is not reconstructed. Declared coamings count
as buoyant, while deckhouses/containers do not. This limits large-angle realism
even though upright published hydrostatics validate independently. The legacy
bespoke family forces Cp=Cwp and Cb about 0.88, and is not a fleet hull.
Curvature is resolved by polygon chords, and hydrostatic tables use
seawater density from params. MCT uses displacement*g*(KB+BMlong-KG)/(100*L), with a declared reference
KG (baseline zero by default); equilibrium retains exact
geometric moment arms. Sources: Biran & López-Pulido, Ship Hydrostatics and
Stability (2014), chapters 2–4; 2008 IS Code Part B 3.1 for free surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from collections import OrderedDict
import hashlib
from typing import Any
import math

import numpy as np
from scipy.optimize import least_squares, brentq

from stowage.model import HydrostaticRow


# Exact physical inputs disambiguate rounded lookup indices: no calculation is
# rounded, and neighbouring mass/CG inputs can never return a false cache hit.
_CACHE_LIMIT = 256
_SOLVE_CACHE = OrderedDict()
_KN_CACHE = OrderedDict()
_KN_CURVE_CACHE = OrderedDict()
_SUBMERGED_CACHE = OrderedDict()
_CACHE_COUNTS = {'solve_hits': 0, 'solve_misses': 0, 'kn_hits': 0, 'kn_misses': 0,
                 'curve_hits': 0, 'curve_misses': 0}
_CACHE_MISSING = object()
_SUBMERGED_SCALAR_TYPES = (bool, int, float, np.float16, np.float32, np.float64, np.longdouble,
                           np.int8, np.int16, np.int32, np.int64,
                           np.uint8, np.uint16, np.uint32, np.uint64)


def clear_hydrostatic_caches():
    """Clear bounded process-local result caches (also useful for verification)."""
    _SOLVE_CACHE.clear()
    _KN_CACHE.clear()
    _KN_CURVE_CACHE.clear()
    _SUBMERGED_CACHE.clear()
    for key in _CACHE_COUNTS:
        _CACHE_COUNTS[key] = 0


def hydrostatic_cache_info():
    """A fresh statistics dictionary; changing it cannot alter cache state."""
    return dict(_CACHE_COUNTS, solve_size=len(_SOLVE_CACHE), kn_size=len(_KN_CACHE),
                curve_size=len(_KN_CURVE_CACHE), maxsize=_CACHE_LIMIT)


def _cache_get(cache, key, name):
    value = cache.get(key, _CACHE_MISSING)
    _CACHE_COUNTS[name + ('_misses' if value is _CACHE_MISSING else '_hits')] += 1
    if value is not _CACHE_MISSING:
        cache.move_to_end(key)
    return value


def _cache_put(cache, key, value):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def _copy_condition(value):
    """Return independent arrays: frozen dataclasses alone do not freeze NumPy."""
    from dataclasses import replace
    submerged = replace(value.submerged, x_m=value.submerged.x_m.copy(),
        sectional_areas_m2=value.submerged.sectional_areas_m2.copy(),
        integration_weights_m=value.submerged.integration_weights_m.copy())
    return replace(value, submerged=submerged)


def _copy_submerged(value):
    from dataclasses import replace
    return replace(value, x_m=value.x_m.copy(),
        sectional_areas_m2=value.sectional_areas_m2.copy(),
        integration_weights_m=value.integration_weights_m.copy())


def _submerged_content_key(geometry, ta, tf, heel, rho):
    """Every current input to base polygon/waterplane integration, unrounded.

    Read the arrays on every query, rather than trusting a construction-time
    digest. Replacement of a public geometry array or scalar therefore cannot
    hide behind the cache. Only a SHA-256 digest of the geometry is retained,
    rather than retaining every polygon's bytes for each draught. Class
    overrides retain their original computation.
    """
    def content(value):
        if type(value) is not np.ndarray and type(value) not in _SUBMERGED_SCALAR_TYPES:
            raise TypeError('custom inputs retain uncached hydrostatics')
        array = np.asarray(value)
        if array.dtype.hasobject:
            raise TypeError('object inputs retain uncached hydrostatics')
        payload = array.tobytes()
        header = repr((array.dtype.str, array.shape, len(payload))).encode('ascii')
        return len(header).to_bytes(8, 'big'), header, payload
    try:
        if (type(geometry.x_m) is not np.ndarray or type(geometry.integration_weights_m) is not np.ndarray
                or type(geometry.sections) not in (list, tuple)
                or any(type(section) not in (list, tuple) for section in geometry.sections)):
            return None
        # Compact scalar records retain the same dtype/shape/exact-bit identity
        # without the nested Python tuples' per-entry memory overhead.
        scalars = tuple(b''.join(content(value)) for value in (geometry.lpp_m, ta, tf, heel, rho))
        digest = hashlib.sha256()
        for value in (geometry.x_m, geometry.integration_weights_m):
            for part in content(value):
                digest.update(part)
        # Frame both levels, so regrouping polygons cannot alias a different
        # station arrangement even when their concatenated coordinates match.
        digest.update(len(geometry.sections).to_bytes(8, 'big'))
        for section in geometry.sections:
            digest.update(len(section).to_bytes(8, 'big'))
            for polygon in section:
                for part in content(polygon):
                    digest.update(part)
        return scalars, digest.digest()
    except TypeError:
        return None


def _value(v):
    return v['value'] if isinstance(v, Mapping) and 'value' in v else v


def _number(d, k):
    return float(_value(d[k]))


@dataclass(frozen=True)
class Submerged:
    volume_m3: float
    displacement_kg: float
    lcb_m: float
    tcb_m: float
    vcb_m: float
    waterplane_area_m2: float
    lcf_m: float
    transverse_inertia_m4: float
    longitudinal_inertia_m4: float
    x_m: np.ndarray
    sectional_areas_m2: np.ndarray
    integration_weights_m: np.ndarray

    @property
    def centroid_m(self):
        return (self.lcb_m, self.tcb_m, self.vcb_m)

    @property
    def bm_t_m(self):
        return self.transverse_inertia_m4 / self.volume_m3

    @property
    def bm_l_m(self):
        return self.longitudinal_inertia_m4 / self.volume_m3


@dataclass(frozen=True)
class FloatingCondition:
    t_aft_m: float
    t_fwd_m: float
    heel_rad: float
    submerged: Submerged
    residual_norm: float

    @property
    def trim_m(self):
        return self.t_aft_m - self.t_fwd_m

    @property
    def mean_draught_m(self):
        return (self.t_aft_m + self.t_fwd_m)/2


def _clip(poly, ny, nz, h):
    """Sutherland–Hodgman clipping, retaining wet vertices in boundary order.

    Edge crossings are batched; each edge still emits its wet start followed by
    its exact waterline intersection, as in the scalar clipping algorithm.
    """
    d = poly[:, 0]*ny + poly[:, 1]*nz - h
    inside = d <= 0
    if inside.all():
        return poly
    if not inside.any():
        return np.empty((0, 2))
    nxt = np.roll(poly, -1, axis=0)
    dn = np.roll(d, -1)
    crossing = inside != np.roll(inside, -1)
    fraction = np.divide(d, d-dn, out=np.zeros_like(d), where=crossing)
    intersections = poly + fraction[:, None] * (nxt-poly)
    points = np.stack((poly, intersections), axis=1).reshape(-1, 2)
    emitted = np.stack((inside, crossing), axis=1).reshape(-1)
    return points[emitted]


def _moments(p):
    if len(p) < 3:
        return np.zeros(3)
    q = np.roll(p, -1, axis=0)
    c = p[:,0]*q[:,1]-q[:,0]*p[:,1]
    return np.array([c.sum()/2, ((p[:,0]+q[:,0])*c).sum()/6,
                     ((p[:,1]+q[:,1])*c).sum()/6])


def _water_segments(poly, ny, nz, h):
    # Polygon boundary intersections sorted along the waterline; even/odd fill.
    d = poly[:,0]*ny+poly[:,1]*nz-h
    dn = np.roll(d, -1)
    crossing = (d <= 0) != (dn <= 0)
    start = poly[crossing]
    end = np.roll(poly, -1, axis=0)[crossing]
    points = start + (d[crossing]/(d[crossing]-dn[crossing]))[:, None]*(end-start)
    if len(points) < 2:
        return []
    points = points[np.argsort(points[:, 0]*nz-points[:, 1]*ny, kind='stable')]
    return list(zip(points[::2], points[1::2]))


class HullGeometry:
    """Immutable-in-use section quadrature; construction requires the explicit family."""

    @classmethod
    def from_params(cls, ship_params: Mapping[str, Any], *, nx=48, nz=64):
        return cls(ship_params, nx=nx, nz=nz)

    def __init__(self, ship_params, *, nx=48, nz=64):
        h, f = ship_params['hull'], ship_params['hull_form']
        if f['family'] == 'stowlm_offsets_v1':
            from stowage.vessel.hull_offsets import build_offset_geometry
            self.__dict__.update(build_offset_geometry(ship_params, nx=nx, nz=nz))
            self._finish_geometry()
            return
        if f['family'] != 'stowlm_separable_rounded_bilge_v1':
            raise ValueError('unsupported hull family; actual offsets required')
        if nx < 4 or nz < 8:
            raise ValueError('nx >= 4 and nz >= 8 required')
        self.lpp_m = L = _number(h,'lpp_m')
        self.beam_m = B = _number(h,'beam_m')
        self.depth_m = D = _number(h,'depth_m')
        self.design_draught_m = T = _number(h,'design_draught_m')
        self.density_kg_m3 = _number(h,'water_density_kg_m3')
        a, fwd = _number(f,'aft_run_fraction'), _number(f,'fore_run_fraction')
        r, flare = _number(f,'bilge_radius_m'), _number(f,'deck_flare_fraction')
        if not (L > 0 and B > 0 and 0 <= r < min(B/2,D) and 0 < T < D
                and a > 0 and fwd > 0 and a+fwd < 1):
            raise ValueError('invalid hull geometry')
        envelope = ship_params['envelope']
        if envelope.get('count_deckhouse',False):
            raise ValueError('deckhouse buoyancy requested without deckhouse geometry')
        self.openings = envelope.get('openings', [])
        boxes = [{k:_number(b,k) for k in ('x_min_m','x_max_m','y_min_m','y_max_m','z_min_m','z_max_m')}
                 for b in envelope.get('hatch_coamings',[])]
        # Below deck the hull can cover only part of a box at an end. Supporting
        # arbitrary overlaps needs unioned offsets; do not double-count silently.
        for b in boxes:
            if b['z_min_m'] < D-1e-10:
                raise ValueError('coaming below deck requires pre-unioned section offsets')
        for i,b in enumerate(boxes):
            for c in boxes[:i]:
                if all(min(b[k+'_max_m'],c[k+'_max_m']) > max(b[k+'_min_m'],c[k+'_min_m'])+1e-10 for k in 'xyz'):
                    raise ValueError('overlapping coaming solids require pre-unioned offsets')
        self.max_z_m = max([D]+[b['z_max_m'] for b in boxes])
        breaks = sorted(set([0.,a*L,(1-fwd)*L,L]+[b[k] for b in boxes for k in ('x_min_m','x_max_m')]))
        x,w = [],[]
        for left,right in zip(breaks,breaks[1:]):
            edges = np.linspace(left,right,max(1,math.ceil((right-left)*nx/L))+1)
            for u,v in zip(edges,edges[1:]):
                x.extend([(u+v)/2-(v-u)/(2*math.sqrt(3)),(u+v)/2+(v-u)/(2*math.sqrt(3))])
                w.extend([(v-u)/2]*2)
        self.x_m, self.integration_weights_m = np.array(x),np.array(w)
        zb = sorted(set([0.,r,T,D]))
        # Equal arc-angle nodes resolve the bilge's vertical tangent at baseline.
        arc_z = r*(1-np.cos(np.linspace(0,math.pi/2,max(4,2*math.ceil(r*nz/D))+1))) if r else np.array([0.])
        arc_z[0], arc_z[-1] = 0., r
        z = np.unique(np.concatenate([arc_z]+[np.linspace(u,v,max(1,math.ceil((v-u)*nz/D))+1) for u,v in zip(zb,zb[1:]) if u >= r]))
        shape = np.ones_like(z)
        if r:
            low = z < r
            shape[low] = 1-2*r/B+2/B*np.sqrt(np.maximum(0,r*r-(z[low]-r)**2))
        shape *= 1+flare*np.maximum(0,z-T)/(D-T)
        self.sections = []
        for xx in x:
            q = min(1.,xx/(a*L),(L-xx)/(fwd*L))
            b = B/2*q*shape
            # Counterclockwise coordinates (y,z).
            polys = [np.column_stack((np.r_[b,-b[::-1]],np.r_[z,z[::-1]]))]
            for box in boxes:
                if box['x_min_m'] < xx < box['x_max_m']:
                    lo,hi = box['y_min_m'],box['y_max_m']
                    bottom,top = box['z_min_m'],box['z_max_m']
                    polys.append(np.array([[lo,bottom],[hi,bottom],[hi,top],[lo,top]]))
            self.sections.append(polys)

        self._finish_geometry()

    def _finish_geometry(self):
        """Fingerprint actual integration geometry, including numerical grid."""
        digest = hashlib.sha256()
        for value in (self.lpp_m,self.beam_m,self.depth_m,self.design_draught_m,self.max_z_m):
            digest.update(float(value).hex().encode())
        for array in [self.x_m,self.integration_weights_m]:
            digest.update(np.asarray(array.shape,dtype=np.int64).tobytes())
            digest.update(np.asarray(array,dtype=np.float64).tobytes())
            array.flags.writeable = False
        for section in self.sections:
            digest.update(len(section).to_bytes(8,'little'))
            for polygon in section:
                digest.update(np.asarray(polygon.shape,dtype=np.int64).tobytes())
                digest.update(np.asarray(polygon,dtype=np.float64).tobytes())
                polygon.flags.writeable = False
        self._geometry_digest = digest.digest()

    def _cache_key(self, values):
        exact = tuple(None if v is None else float(v) for v in values)
        rounded = tuple(None if v is None else round(v,8) for v in exact)
        # Classes with different integration overrides never share results.
        return type(self), self._geometry_digest, rounded, exact

    def _axes(self, ta, tf, heel):
        if not np.isfinite([ta,tf,heel]).all() or abs(heel) >= math.pi/2-1e-5:
            raise ValueError('finite draughts and heel strictly inside +/-90 degrees required')
        slope = (tf-ta)/self.lpp_m
        n = np.array([-slope*math.cos(heel),-math.sin(heel),math.cos(heel)])
        n /= np.linalg.norm(n)
        longitudinal = np.array([1.,0.,slope]); longitudinal /= np.linalg.norm(longitudinal)
        transverse = np.cross(n,longitudinal)
        return n,longitudinal,transverse

    def waterplane_normal(self, t_aft_m, t_fwd_m, heel_rad=0.):
        """Unit upward waterplane normal in ship coordinates (for tank clipping)."""
        return self._axes(t_aft_m, t_fwd_m, heel_rad)[0]

    def submerged(self,t_aft_m,t_fwd_m,heel_rad=0.,density_kg_m3=None):
        rho = self.density_kg_m3 if density_kg_m3 is None else density_kg_m3
        if not np.isfinite(rho) or rho <= 0:
            raise ValueError('positive finite water density required')
        n,along,across = self._axes(t_aft_m,t_fwd_m,heel_rad)
        key = None
        if (type(self) is HullGeometry and '_axes' not in self.__dict__
                and _submerged_dependencies() == _ORIGINAL_SUBMERGED_DEPENDENCIES):
            from stowage.checker.perf import reuse_enabled
            if reuse_enabled():
                key = _submerged_content_key(self,t_aft_m,t_fwd_m,heel_rad,rho)
                if key is not None and key in _SUBMERGED_CACHE:
                    _SUBMERGED_CACHE.move_to_end(key)
                    return _copy_submerged(_SUBMERGED_CACHE[key])
        offset = n[2]*t_aft_m
        totals = np.zeros(4); wp = np.zeros(6); areas=[]
        jac = 1/math.sqrt(1-n[0]**2)
        for x,w,polys in zip(self.x_m,self.integration_weights_m,self.sections):
            m = np.zeros(3)
            for poly in polys:
                m += _moments(_clip(poly,n[1],n[2],offset-n[0]*x))
                for p,q in _water_segments(poly,n[1],n[2],offset-n[0]*x):
                    length = np.linalg.norm(q-p)*jac*w
                    p3,q3 = np.r_[x,p],np.r_[x,q]
                    aa,bb = np.dot(p3,along),np.dot(q3,along)
                    cc,dd = np.dot(p3,across),np.dot(q3,across)
                    wp += length*np.array([1,x,(aa+bb)/2,(cc+dd)/2,
                                          (aa*aa+aa*bb+bb*bb)/3,(cc*cc+cc*dd+dd*dd)/3])
            areas.append(m[0]); totals += w*np.array([m[0],x*m[0],m[1],m[2]])
        vol=totals[0]
        centroid=totals[1:]/vol if vol>1e-12 else np.zeros(3)
        area=wp[0]
        it=max(0.,wp[5]-wp[3]**2/area) if area>0 else 0.
        il=max(0.,wp[4]-wp[2]**2/area) if area>0 else 0.
        result = Submerged(float(vol),float(rho*vol),*map(float,centroid),float(area),
                           float(wp[1]/area) if area else self.lpp_m/2,it,il,self.x_m,np.array(areas),self.integration_weights_m)
        if key is not None:
            _cache_put(_SUBMERGED_CACHE,key,_copy_submerged(result))
        return result

    def trimmed_sectional_areas(self,t_aft_m,t_fwd_m,heel_rad=0.,density_kg_m3=None):
        """Return full Submerged including station areas and quadrature weights.

        Areas are m² at x_m, and sum(area*integration_weights_m) is volume.
        This explicit trimmed interface does not interpolate upright Bonjeans.
        """
        return self.submerged(t_aft_m,t_fwd_m,heel_rad,density_kg_m3)

    def _solver_submerged(self,ta,tf,heel=0.,density=None):
        """Batched exact polygon volume/centroid; solver residuals need no WP.

        Uses the same wet boundary vertices and edge intersections as submerged;
        vectorisation changes summation order only. Final returned equilibrium
        always recomputes the full waterplane via the public submerged method.
        Subclasses retain their own submerged override in solver residuals.
        """
        if not hasattr(self,'_solver_arrays'):
            polygons=[]
            for x,w,section in zip(self.x_m,self.integration_weights_m,self.sections):
                for poly in section:
                    # Duplicate contour vertices must be removed before collinear
                    # filtering, or both copies of a real corner could disappear.
                    poly=poly[np.any(poly != np.roll(poly,1,axis=0),axis=1)]
                    before=poly-np.roll(poly,1,axis=0)
                    after=np.roll(poly,-1,axis=0)-poly
                    cross=before[:,0]*after[:,1]-before[:,1]*after[:,0]
                    corners=poly[cross != 0.]  # Remove only exactly collinear nodes, never curved offsets.
                    polygons.append((x,w,corners))
            count=max(len(poly) for _,_,poly in polygons)
            pp=np.array([np.concatenate((poly,np.repeat(poly[-1:],count-len(poly),axis=0))) for _,_,poly in polygons])
            self._solver_arrays=(pp,np.roll(pp,-1,axis=1),np.array([x for x,_,_ in polygons]),np.array([w for _,w,_ in polygons]))
        pp,qq,px,pw=self._solver_arrays
        normal,_,_=self._axes(ta,tf,heel)
        h=normal[2]*ta-normal[0]*px
        # Keep the two-term contraction explicit: the batched BLAS path was
        # observed to return allocation-dependent signed distances for identical
        # inputs. The polygon geometry, work budget and tolerances are unchanged.
        distances=pp[...,0]*normal[1]+pp[...,1]*normal[2]-h[:,None]
        wet=np.any(distances<=0,axis=1)
        poly,nxt=pp[wet],qq[wet]
        xx,ww=px[wet],pw[wet]
        d=distances[wet]
        if not len(d):
            return Submerged(0.,0.,0.,0.,0.,0.,self.lpp_m/2,0.,0.,self.x_m,np.array([]),self.integration_weights_m)
        dq=np.roll(d,-1,axis=1)
        inside=d<=0
        crossing=inside != np.roll(inside,-1,axis=1)
        fraction=np.divide(d,d-dq,out=np.zeros_like(d),where=crossing)
        intersections=poly+fraction[...,None]*(nxt-poly)
        points=np.stack((poly,intersections),axis=2).reshape(len(d),-1,2)
        valid=np.stack((inside,crossing),axis=2).reshape(len(d),-1)
        counts=valid.sum(axis=1)
        indices=np.broadcast_to(np.arange(valid.shape[1]),valid.shape)
        sentinel=valid.shape[1]
        following=np.minimum.accumulate(np.where(valid,indices,sentinel)[:,::-1],axis=1)[:,::-1]
        first=following[:,:1]
        following=np.concatenate((following[:,1:],first),axis=1)
        following=np.minimum(np.where(following==sentinel,first,following),sentinel-1)
        ends=np.take_along_axis(points,following[...,None],axis=1)
        cross=np.where(valid & (counts[:,None]>=3),points[...,0]*ends[...,1]-ends[...,0]*points[...,1],0.)
        area=cross.sum(axis=1)/2
        my=((points[...,0]+ends[...,0])*cross).sum(axis=1)/6
        mz=((points[...,1]+ends[...,1])*cross).sum(axis=1)/6
        volume=float(area @ ww)
        cg=np.array([(area*xx) @ ww,my @ ww,mz @ ww])/volume if volume>1e-12 else np.zeros(3)
        rho=self.density_kg_m3 if density is None else density
        return Submerged(volume,rho*volume,*map(float,cg),0.,self.lpp_m/2,0.,0.,self.x_m,np.array([]),self.integration_weights_m)

    def hydrostatic_row(self,draught_m,density_kg_m3=None,vcg_m=0.,*,kg_m=None):
        """Even-keel table row; MCT includes the declared reference KG (m).

        vcg_m defaults to baseline zero; kg_m is a keyword alias. MCT is
        displacement*g*(KB+BMlong-KG)/(100*L), not the waterplane-only shortcut.
        """
        if kg_m is not None:
            if vcg_m != 0. and vcg_m != kg_m:
                raise ValueError('conflicting vcg_m and kg_m')
            vcg_m = kg_m
        if not np.isfinite(vcg_m):
            raise ValueError('finite reference KG required')
        s=self.submerged(draught_m,draught_m,0,density_kg_m3)
        rho=self.density_kg_m3 if density_kg_m3 is None else density_kg_m3
        if s.volume_m3<=0 or s.waterplane_area_m2<=0:
            raise ValueError('hydrostatic table requires positive volume and waterplane')
        return HydrostaticRow(draught_m=draught_m,displacement_kg=s.displacement_kg,
            kb_m=s.vcb_m,kmt_m=s.vcb_m+s.bm_t_m,lcb_m=s.lcb_m,lcf_m=s.lcf_m,
            tpc_kg_per_cm=rho*s.waterplane_area_m2/100,
            mct_nm_per_cm=s.displacement_kg*9.80665*(s.vcb_m+s.bm_l_m-vcg_m)/(100*self.lpp_m))

    def _solve(self,mass,lcg,tcg,vcg,rho,heel):
        """Bounded exact-input memoisation, shared across identical hull instances."""
        key = self._cache_key((mass,lcg,tcg,vcg,rho,heel))
        answer = _cache_get(_SOLVE_CACHE,key,'solve')
        if answer is _CACHE_MISSING:
            answer = self._solve_uncached(mass,lcg,tcg,vcg,rho,heel)
            _cache_put(_SOLVE_CACHE,key,_copy_condition(answer))
        return _copy_condition(answer)

    def _solve_uncached(self,mass,lcg,tcg,vcg,rho,heel):
        if not np.isfinite([mass,lcg,tcg,vcg,rho]).all() or mass<=0 or rho<=0:
            raise ValueError('finite CG, positive mass and density required')
        submerged=self._solver_submerged if type(self) is HullGeometry else self.submerged
        target=mass/rho
        guess=min(self.depth_m*.95,max(.1,target/(self.lpp_m*self.beam_m*.85)))
        fixed=heel is not None
        def residual(v):
            ta,tf=v[:2]; phi=heel if fixed else v[2]
            s=submerged(ta,tf,phi,rho)
            _,a,b=self._axes(ta,tf,phi)
            diff=np.asarray(s.centroid_m)-[lcg,tcg,vcg]
            terms=[(s.volume_m3-target)/target,np.dot(diff,a)/self.lpp_m]
            if not fixed: terms.append(np.dot(diff,b)/self.beam_m)
            return terms
        bound=self.max_z_m+self.beam_m*2
        if fixed:
            # At large heel a lightly loaded hull can have a *negative* centreline
            # intercept. Bracketing volume first avoids the dry/full flat regions
            # in which a multidimensional Newton Jacobian loses rank.
            guess=brentq(lambda d:submerged(d,d,heel,rho).volume_m3-target,
                         -bound,bound,xtol=1e-9)
        initial=[guess,guess] if fixed else [guess,guess,0.]
        lower=[-bound,-bound] if fixed else [-bound,-bound,-math.radians(85)]
        upper=[bound,bound] if fixed else [bound,bound,math.radians(85)]
        opt=least_squares(residual,initial,bounds=(lower,upper),xtol=2e-10,ftol=2e-10,gtol=2e-10,
                          diff_step=1e-4,max_nfev=100)
        error=float(np.linalg.norm(residual(opt.x)))
        if fixed and (not opt.success or error>2e-6):
            # Robust nested volume/trim bracket fallback. For each prescribed
            # trim the volume is monotone in mean immersion; longitudinal moment
            # is then bracketed independently, rather than trusting a local fit.
            def condition_at_trim(trim):
                limit=bound+abs(trim)/2
                mean=brentq(lambda d:submerged(d+trim/2,d-trim/2,heel,rho).volume_m3-target,
                            -limit,limit,xtol=1e-8)
                return np.array([mean+trim/2,mean-trim/2])
            bracket=None
            for extent in (.05,.2,.5,1.,2.):
                lo,hi=-self.lpp_m*extent,self.lpp_m*extent
                flo=residual(condition_at_trim(lo))[1]
                fhi=residual(condition_at_trim(hi))[1]
                if flo*fhi<=0:
                    bracket=(lo,hi); break
            if bracket is not None:
                trim=brentq(lambda t:residual(condition_at_trim(t))[1],*bracket,xtol=1e-7)
                opt.x=condition_at_trim(trim)
                error=float(np.linalg.norm(residual(opt.x)))
                opt.success=error<=2e-6
        if not opt.success or error>2e-6:
            raise ValueError(f'no converged floating equilibrium (scaled residual {error:.3g})')
        phi=float(heel if fixed else opt.x[2]); ta,tf=map(float,opt.x[:2])
        return FloatingCondition(ta,tf,phi,self.submerged(ta,tf,phi,rho),error)

    def equilibrium(self,mass_kg,lcg_m,tcg_m,vcg_m,density_kg_m3=None):
        return self._solve(mass_kg,lcg_m,tcg_m,vcg_m,
                           self.density_kg_m3 if density_kg_m3 is None else density_kg_m3,None)

    def kn(self,heel_rad,displacement_kg,lcg_m=None,density_kg_m3=None):
        """Free-trim KN at given mass and optional density; third argument stays LCG."""
        lcg=self.lpp_m/2 if lcg_m is None else lcg_m
        rho=self.density_kg_m3 if density_kg_m3 is None else density_kg_m3
        key=self._cache_key((heel_rad,displacement_kg,lcg,rho))
        value=_cache_get(_KN_CACHE,key,'kn')
        if value is not _CACHE_MISSING:
            return value
        f=self._solve(displacement_kg,lcg,0.,0.,rho,heel_rad)
        _,_,transverse=self._axes(f.t_aft_m,f.t_fwd_m,heel_rad)
        value=float(np.dot(np.asarray(f.submerged.centroid_m)-[lcg,0.,0.],transverse))
        _cache_put(_KN_CACHE,key,value)
        return value

    def kn_curve(self,heel_rads,displacement_kg,lcg_m=None,density_kg_m3=None):
        """Free-trim KN at successive angles, with exact polygon continuation.

        The previous root is only a starting guess: each angle independently
        meets the same volume/longitudinal moment equations as ``kn``. Invalid
        local roots fall back to the bracketed solver. No waterplane shortcut or
        stability formula replaces integration. Cached arrays are copied.
        """
        from scipy.optimize import root
        angles=np.asarray(heel_rads,dtype=float)
        if angles.ndim!=1 or not np.isfinite(angles).all() or np.any(np.abs(angles)>=math.pi/2-1e-5):
            raise ValueError('KN curve requires finite angles strictly inside +/-90 degrees')
        lcg=self.lpp_m/2 if lcg_m is None else float(lcg_m)
        rho=self.density_kg_m3 if density_kg_m3 is None else float(density_kg_m3)
        if not np.isfinite([displacement_kg,lcg,rho]).all() or displacement_kg<=0 or rho<=0:
            raise ValueError('positive finite displacement/density and finite LCG required')
        key=self._cache_key((displacement_kg,lcg,rho,*angles))
        value=_cache_get(_KN_CURVE_CACHE,key,'curve')
        if value is not _CACHE_MISSING:
            return value.copy()
        submerged=self._solver_submerged if type(self) is HullGeometry else self.submerged
        target=displacement_kg/rho
        guess=None;values=[]
        for angle in angles:
            def residual(v):
                s=submerged(*v,float(angle),rho)
                _,along,_=self._axes(*v,float(angle))
                return [(s.volume_m3-target)/target,
                        np.dot(np.asarray(s.centroid_m)-[lcg,0.,0.],along)/self.lpp_m]
            result=None
            if guess is not None:
                try:
                    result=root(residual,guess,tol=2e-10,options={'eps':1e-8})
                except (ValueError,FloatingPointError):
                    # Invalid local trial points do not invalidate a physical
                    # condition: retry with the robust volume/trim solver.
                    result=None
            if result is None or not np.isfinite(result.x).all() or np.linalg.norm(residual(result.x))>2e-8:
                condition=self._solve(displacement_kg,lcg,0.,0.,rho,float(angle))
                guess=np.array([condition.t_aft_m,condition.t_fwd_m])
                s=condition.submerged
            else:
                guess=result.x
                s=submerged(*guess,float(angle),rho)
            _,_,across=self._axes(*guess,float(angle))
            values.append(float(np.dot(np.asarray(s.centroid_m)-[lcg,0.,0.],across)))
        value=np.asarray(values)
        _cache_put(_KN_CURVE_CACHE,key,value.copy())
        return value


def _submerged_dependencies():
    """Runtime providers used by the unchanged polygon/waterplane formulae."""
    functions = (HullGeometry._axes, _clip, _moments, _water_segments,
                 Submerged, getattr(Submerged, '__init__', None), float, map, max, zip, len, list,
                 np.asarray, np.isfinite, np.zeros, np.array, np.roll, np.empty,
                 np.divide, np.zeros_like, np.stack, np.argsort,
                 np.linalg.norm, np.dot, np.cross, np.r_, getattr(type(np.r_), '__getitem__', None),
                 math.sqrt, math.sin, math.cos, hashlib.sha256)
    return (*functions, *(getattr(function, '__code__', None) for function in functions))


_ORIGINAL_SUBMERGED_DEPENDENCIES = _submerged_dependencies()
