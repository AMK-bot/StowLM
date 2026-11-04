# Copied from experiments/mvp/frontier/lab/c1/fx-finish/shaped_liquid.py; engine integration changes are local.
"""Optional exact section-tank search kernel; never used in full checking.

This file is deliberately separate from fast_finish.py for independently pinned
benchmarks. context() must be entered only during visibility/lashing public
search callbacks, then exited before any complete checker call.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from functools import wraps
import math
import time

import numpy as np


def batched_surface(solid, level, normal):
    """Same exact polygon-waterline segments and quadrature, batched in NumPy.

    Each polygon's boundary intersections are sorted along the waterline and
    paired using the original even/odd rule. We then integrate length and its
    first/second transverse moments with the original inclined-plane Jacobian.
    No station interpolation, contour replacement, or angle approximation.
    """
    _, _, _, batch = solid._quadrature(level, normal)
    pp, qq, px, pw, _ = batch
    # Match _water_segments' scalar expression, including its association.
    plane = level-normal[0]*px
    distance = pp[..., 0]*normal[1]+pp[..., 1]*normal[2]-plane[:, None]
    next_distance = np.roll(distance, -1, axis=1)
    crossing = (distance <= 0) != (next_distance <= 0)
    counts = crossing.sum(axis=1)
    if np.any(counts % 2):
        raise ArithmeticError('section waterline has unpaired boundary crossings')
    fraction = np.divide(distance, distance-next_distance,
                         out=np.zeros_like(distance), where=crossing)
    points = pp+fraction[..., None]*(qq-pp)
    coordinate = points[..., 0]*normal[2]-points[..., 1]*normal[1]
    order = np.argsort(np.where(crossing, coordinate, np.inf), axis=1, kind='stable')
    ordered = points[np.arange(len(points))[:, None], order]
    size = 2*(ordered.shape[1]//2)
    starts, ends = ordered[:, :size:2], ordered[:, 1:size:2]
    valid = 2*np.arange(size//2)[None, :] < counts[:, None]
    along = np.array([normal[2], 0., -normal[0]])
    along /= np.linalg.norm(along)
    across = np.cross(normal, along)
    jacobian = 1/np.hypot(normal[1], normal[2])
    lengths = np.linalg.norm(ends-starts, axis=2)*pw[:, None]*jacobian
    lengths = np.where(valid, lengths, 0.)
    origin = np.array([(getattr(solid, f'{axis}_min_m')+
                        getattr(solid, f'{axis}_max_m'))/2 for axis in 'xyz'])
    longitudinal = (px-origin[0])[:, None]*across[0]
    u = (longitudinal+(starts[..., 0]-origin[1])*across[1]+
         (starts[..., 1]-origin[2])*across[2])
    v = (longitudinal+(ends[..., 0]-origin[1])*across[1]+
         (ends[..., 1]-origin[2])*across[2])
    area = float(lengths.sum())
    first = float((lengths*(u+v)/2).sum())
    second = float((lengths*(u*u+u*v+v*v)/3).sum())
    inertia = max(0., second-first*first/area) if area > 0. else 0.
    if not np.isfinite([area, inertia]).all():
        raise ArithmeticError('section surface produced nonfinite moments')
    return area, inertia


def polynomial_liquid(solid, volume_m3, heel_rad=0., trim_rad=0.):
    """Return accelerated exact LiquidCondition, or None for native fallback.

    Reuse the production checker's exact section-height polynomial root for
    nonprismatic tanks. The polynomial builder preserves each supplied section
    and quadrature weight; this is an algebraic identity, not interpolation.
    The final level is checked by the original polygon integration with the
    original volume tolerance. Its exact polygon moments supply the centroid.
    """
    from stowage.checker.stability_tank_transfer import _polynomial_liquid
    from stowage.vessel.hull_tanks import SectionTankGeometry, _TINY_LIQUID_VOLUME_M3
    from stowage.vessel.tanks import LiquidCondition
    if type(solid) is not SectionTankGeometry:
        return None
    volume = solid._volume(volume_m3)
    normal = solid._normal(heel_rad, trim_rad)
    if volume <= _TINY_LIQUID_VOLUME_M3 or volume == solid.capacity_m3:
        return None
    # A prismatic extrusion's tilted quadrature changes at each liquid level.
    # Its production polynomial helper deliberately leaves this regime direct.
    if solid._prismatic and abs(trim_rad) > 1e-14:
        return None
    try:
        _, level = _polynomial_liquid(solid, volume, float(heel_rad), float(trim_rad))
        totals = solid._integrate(level, normal)
        if (not np.isfinite(totals).all() or not np.isfinite(level) or totals[0] <= 0. or
                abs(totals[0]-volume) > max(1e-10, 2e-10*volume)):
            return None
        area, inertia = batched_surface(solid, level, normal)
        return LiquidCondition(volume, tuple(map(float, totals[1:]/totals[0])),
                               area, inertia, level)
    except (ValueError, ArithmeticError, FloatingPointError):
        return None


@contextmanager
def context(diagnostics=None):
    """Caller must limit this context to public search part probes only."""
    from stowage.vessel.hull_tanks import SectionTankGeometry
    original = SectionTankGeometry.liquid
    counters = diagnostics if diagnostics is not None else {}
    bank = OrderedDict()

    @wraps(original)
    def liquid(solid, volume_m3, heel_rad=0., trim_rad=0.):
        if type(solid) is not SectionTankGeometry:
            return original(solid, volume_m3, heel_rad, trim_rad)
        # The class freezes its fields and owns read-only arrays. Retaining the
        # actual object in a key prevents object-ID recycling or geometry alias.
        key = solid, float(volume_m3), float(heel_rad), float(trim_rad)
        if key in bank:
            bank.move_to_end(key)
            counters['section_hits'] = counters.get('section_hits', 0)+1
            return bank[key]
        before = time.monotonic()
        result = polynomial_liquid(solid, volume_m3, heel_rad, trim_rad)
        name = 'section_polynomial' if result is not None else 'section_fallback'
        counters[name] = counters.get(name, 0)+1
        if result is None:
            result = original(solid, volume_m3, heel_rad, trim_rad)
        counters['section_s'] = counters.get('section_s', 0.)+time.monotonic()-before
        bank[key] = result
        if len(bank) > 8192:
            bank.popitem(last=False)
        return result

    SectionTankGeometry.liquid = liquid
    try:
        yield
    finally:
        SectionTankGeometry.liquid = original


def selftest(seed=18437, count=24):
    """Independent synthetic regression; no map or certificate is reused."""
    from stowage.vessel.hull_tanks import SectionTankGeometry
    rng = np.random.default_rng(seed)
    sections = []
    for width in (2., 2.2, 2.5, 2.8, 2.4, 2.1):
        sections.append((np.array([[-width, 0.], [width, 0.],
                                   [width*.65, 3.], [-width*.7, 3.]]),))
    solid = SectionTankGeometry(0., 12., -3., 3., 0., 3.,
        np.array([1., 3., 5., 7., 9., 11.]), np.repeat(2., 6), tuple(sections))
    original_s = accelerated_s = 0.
    errors = np.zeros(6)
    passed, fallback = 0, 0
    for index in range(count):
        fraction = float(rng.choice([1e-6, .01, .1, .3, .5, .8, .99, 1.-1e-6]))
        heel, trim = rng.uniform(-.4, .4, 2)
        volume = fraction*solid.capacity_m3
        before = time.perf_counter()
        trial = polynomial_liquid(solid, volume, heel, trim)
        accelerated = time.perf_counter()-before
        if trial is None:
            fallback += 1
            continue
        before = time.perf_counter()
        reference = solid.liquid(volume, heel, trim)
        original_s += time.perf_counter()-before
        accelerated_s += accelerated
        a = np.r_[trial.centroid_m, trial.plane_level_m, trial.free_surface_area_m2, trial.fsm_m4]
        b = np.r_[reference.centroid_m, reference.plane_level_m,
                  reference.free_surface_area_m2, reference.fsm_m4]
        errors = np.maximum(errors, np.abs(a-b))
        np.testing.assert_allclose(a, b, atol=2e-8, rtol=2e-8,
                                   err_msg=f'section liquid case {index}')
        passed += 1
    assert passed > 0
    original = SectionTankGeometry.liquid
    try:
        with context({}):
            assert SectionTankGeometry.liquid is not original
            raise RuntimeError('expected restoration test')
    except RuntimeError:
        pass
    assert SectionTankGeometry.liquid is original
    return dict(seed=seed, paired_cases=passed, fallback_cases=fallback,
                maximum_absolute_errors=errors.tolist(), original_s=original_s,
                accelerated_s=accelerated_s, speedup=original_s/max(accelerated_s, 1e-12))




# Copies of the production integration kernels. Only the two axis-one array
# gathers change; direct advanced indexing selects exactly the same elements.
from stowage.vessel.hull_tanks import _immutable
from stowage.vessel.hydrostatics import _water_segments, Submerged


def gather_integrate(self,level,normal,*,surface=False,distribution=False,origin=None):
    xs,weights,sections,batch=self._quadrature(level if origin is None else level+origin @ normal,normal)
    pp,qq,px,pw,station_ids=batch
    if origin is not None:
        pp=pp-origin[1:];qq=qq-origin[1:]
    # D114: avoid allocation-dependent errors in batched matrix products.
    d=pp[...,0]*normal[1]+pp[...,1]*normal[2]+normal[0]*(px[:,None] if origin is None else px[:,None]-origin[0])-level
    dq=np.roll(d,-1,axis=1)
    inside=d<=0
    crossing=inside != np.roll(inside,-1,axis=1)
    fraction=np.divide(d,d-dq,out=np.zeros_like(d),where=crossing)
    intersections=pp+fraction[...,None]*(qq-pp)
    if origin is not None:
        # Evaluate from the nearer endpoint: subtracting an almost complete
        # edge from its upper endpoint otherwise loses a sub-ULP wet depth.
        reverse=np.divide(dq,dq-d,out=np.zeros_like(d),where=crossing)
        intersections=np.where((np.abs(d)<=np.abs(dq))[...,None],intersections,
                                qq+reverse[...,None]*(pp-qq))
    points=np.stack((pp,intersections),axis=2).reshape(len(pp),-1,2)
    valid=np.stack((inside,crossing),axis=2).reshape(len(pp),-1)
    count=valid.sum(axis=1)
    indices=np.broadcast_to(np.arange(valid.shape[1]),valid.shape)
    sentinel=valid.shape[1]
    following=np.minimum.accumulate(np.where(valid,indices,sentinel)[:,::-1],axis=1)[:,::-1]
    first=following[:,:1]
    following=np.concatenate((following[:,1:],first),axis=1)
    following=np.minimum(np.where(following==sentinel,first,following),sentinel-1)
    end=points[np.arange(len(points))[:,None],following]
    if origin is not None:
        local=points[np.arange(len(points))[:,None],np.minimum(first,sentinel-1)]
        points=points-local;end=end-local
    cross=np.where(valid & (count[:,None]>=3),points[...,0]*end[...,1]-end[...,0]*points[...,1],0.)
    area=cross.sum(axis=1)/2
    my=((points[...,0]+end[...,0])*cross).sum(axis=1)/6
    mz=((points[...,1]+end[...,1])*cross).sum(axis=1)/6
    if origin is not None:
        my+=area*(local[:,0,0]+origin[1]);mz+=area*(local[:,0,1]+origin[2])
    totals=np.array([area @ pw,(area*px) @ pw,my @ pw,mz @ pw])
    if distribution:
        areas=np.bincount(station_ids,weights=area,minlength=len(xs))
        return tuple(_immutable(a) for a in (xs,weights,areas))
    if not surface:
        return totals
    # The longitudinal axis in the plane has zero transverse coordinate.
    # Construct it without requiring the original heel/trim angle inputs.
    along=np.array([normal[2],0.,-normal[0]])
    along/=np.linalg.norm(along)
    across=np.cross(normal,along)
    jac=1/np.hypot(normal[1],normal[2])
    wp=np.zeros(3)
    surface_origin=np.array([(self.x_min_m+self.x_max_m)/2,(self.y_min_m+self.y_max_m)/2,(self.z_min_m+self.z_max_m)/2])
    for x,w,section in zip(xs,weights,sections):
        for polygon in section:
            plane=level-normal[0]*x if origin is None else level-normal[0]*(x-origin[0])
            contour=polygon if origin is None else polygon-origin[1:]
            for a,b in _water_segments(contour,normal[1],normal[2],plane):
                if origin is not None:
                    a=a+origin[1:];b=b+origin[1:]
                length=np.linalg.norm(b-a)*w*jac
                u=float((np.r_[x,a]-surface_origin) @ across)
                v=float((np.r_[x,b]-surface_origin) @ across)
                wp+=length*np.array([1.,(u+v)/2,(u*u+u*v+v*v)/3])
    inertia=max(0.,float(wp[2]-wp[1]*wp[1]/wp[0])) if wp[0]>0 else 0.
    return totals,float(wp[0]),inertia


def gather_submerged(self,ta,tf,heel=0.,density=None):
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
    ends=points[np.arange(len(points))[:,None],following]
    cross=np.where(valid & (counts[:,None]>=3),points[...,0]*ends[...,1]-ends[...,0]*points[...,1],0.)
    area=cross.sum(axis=1)/2
    my=((points[...,0]+ends[...,0])*cross).sum(axis=1)/6
    mz=((points[...,1]+ends[...,1])*cross).sum(axis=1)/6
    volume=float(area @ ww)
    cg=np.array([(area*xx) @ ww,my @ ww,mz @ ww])/volume if volume>1e-12 else np.zeros(3)
    rho=self.density_kg_m3 if density is None else density
    return Submerged(volume,rho*volume,*map(float,cg),0.,self.lpp_m/2,0.,0.,self.x_m,np.array([]),self.integration_weights_m)


@contextmanager
def gather_context():
    """Exact gather-only copies; scope to search parts, never full checking."""
    from stowage.vessel.hull_tanks import SectionTankGeometry
    from stowage.vessel.hydrostatics import HullGeometry
    original_integrate = SectionTankGeometry._integrate
    original_submerged = HullGeometry._solver_submerged
    SectionTankGeometry._integrate = gather_integrate
    HullGeometry._solver_submerged = gather_submerged
    try:
        yield
    finally:
        HullGeometry._solver_submerged = original_submerged
        SectionTankGeometry._integrate = original_integrate


def gather_selftest(seed=8917, count=20):
    """Assert gather copies return bit-identical tank and hull integration."""
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.hydrostatics import HullGeometry
    from stowage.vessel.hull_tanks import SectionTankGeometry
    rng = np.random.default_rng(seed)
    sections = tuple((np.array([[-w, 0.], [w, 0.],
                                [w*.6, 3.], [-w*.8, 3.]]),)
                     for w in (2., 2.2, 2.5, 2.8, 2.4, 2.1))
    tank = SectionTankGeometry(0., 12., -3., 3., 0., 3.,
        np.array([1., 3., 5., 7., 9., 11.]), np.repeat(2., 6), sections)
    hull = HullGeometry.from_params(load_ship_params('feeder_500'))
    original_s = accelerated_s = 0.
    for _ in range(count):
        normal = tank._normal(*rng.uniform(-.3, .3, 2))
        level = float(rng.uniform(.2, 2.8))
        before = time.perf_counter()
        expected = tank._integrate(level, normal, surface=True)
        original_s += time.perf_counter()-before
        before = time.perf_counter()
        actual = gather_integrate(tank, level, normal, surface=True)
        accelerated_s += time.perf_counter()-before
        assert np.array_equal(expected[0], actual[0])
        assert expected[1:] == actual[1:]
        ta, tf = rng.uniform(2., 4., 2)
        heel = float(rng.uniform(-.04, .04))
        before = time.perf_counter()
        expected = hull._solver_submerged(ta, tf, heel)
        original_s += time.perf_counter()-before
        before = time.perf_counter()
        actual = gather_submerged(hull, ta, tf, heel)
        accelerated_s += time.perf_counter()-before
        for field in expected.__dataclass_fields__:
            assert np.array_equal(getattr(expected, field), getattr(actual, field)), field
    return dict(seed=seed, paired_cases=2*count, bit_identical=True,
                original_s=original_s, accelerated_s=accelerated_s,
                speedup=original_s/max(accelerated_s, 1e-12))


if __name__ == '__main__':
    import json
    print(json.dumps(selftest(), sort_keys=True))
