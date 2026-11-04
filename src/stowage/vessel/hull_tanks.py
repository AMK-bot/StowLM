"""Finite-angle liquid integration for explicit hull-following section tanks.

``section_geometry_from_params`` reads shape='hull_sections': ascending x_m,
positive integration_weights_m, sections_yz_m[station][polygon][vertex][y,z],
and six bounding extents. Polygons are counterclockwise and disjoint within a
station. The solid is its recorded longitudinal Gauss quadrature, not its box.
``SectionTankGeometry`` retains TankGeometry's capacity/sounding/liquid API.
``section_liquid_areas(volume,heel,trim)`` returns (x, weights, wet areas) in SI;
sum(weights*areas) is liquid volume. Stored stations can have gaps and missing
Gauss mates: consumers must not invent continuous panels by pairing neighbours.

Liquid level is solved at fixed volume with a safeguarded scalar root. Each
polygon is clipped against the physical free plane; shoelace first moments and
waterline segment moments are exact at each station. Surface area includes the
inclined-plane Jacobian and transverse inertia is about its own surface centroid,
using HullGeometry's longitudinal/transverse axes (IMO 2008 IS Code Part B 3.1).
Empty/full tanks have zero free surface. Full-tank CG is the integrated solid CG.
Only tiny numerical volume overshoots (1e-12 relative or 1e-10 m³ absolute)
are clamped to physical endpoints; an interior slack fill remains slack.
Exact-input bounded geometry/liquid caches retain immutable arrays/results.

APPROXIMATION: longitudinal integration uses the supplied section Gauss weights,
including the upstream hull loft, exclusion and shell-allowance approximations.
This representation supplies no geometry between stations and is not silently
replaced by a rectangular prism. A provably continuous constant-section extrusion
has sufficient geometry to refine its x quadrature at every liquid-plane/corner
transition; two-point Gauss then integrates its tilted polygon moments exactly.
All other hull-following tanks keep the authoritative supplied quadrature.
No baffles, fluid connectivity barriers or permeability are invented. Separated
polygons in one tank share a common liquid level (declared interconnected tank).
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
import hashlib
from math import cos, sin, tan
from typing import ClassVar

import numpy as np
from scipy.optimize import brentq

from stowage.model import TankSoundingPoint
from stowage.vessel.hydrostatics import _moments, _water_segments
from stowage.vessel.tanks import LiquidCondition

_GEOMETRY_CACHE = OrderedDict()
_GEOMETRY_CACHE_SIZE = 96
_LIQUID_CACHE_SIZE = 64
# Arithmetic dispatch only: no positive fill is snapped at this threshold.
_TINY_LIQUID_VOLUME_M3 = 1e-8
# APPROXIMATION: D65 treats a nonpositive computed volume within this cap as
# exactly empty. Mass/moment errors are bounded, but dropping slack free-surface
# inertia is unbounded and leans to pass. Resolved positive volumes retain their
# free surface, even below this cap.
_UNDEFINED_LIQUID_VOLUME_M3 = 1e-14


def _immutable(values):
    array=np.asarray(values,dtype=np.float64)
    return np.frombuffer(array.tobytes(),dtype=np.float64).reshape(array.shape)


def _scalar(value):
    return float(value['value'] if isinstance(value,Mapping) else value)


def _polygons_overlap(first,second):
    """Test positive-area intersection, allowing shared edges and vertices.

    Between vertex/crossing y coordinates the ordering of all boundary edges is
    fixed. One transverse scan through each such open slab therefore detects
    every positive-area intersection, including concave and contained polygons.
    Rational predicates avoid cancellation at edge intersections. Boundary
    contact allows eight coordinate ULPs: separately clipped fleet components
    can encode their common edge one or two ULPs apart. This is only arithmetic
    roundoff, rather than the metre-scale bounds tolerance or a geometric union.
    """
    lower=np.maximum(first.min(axis=0),second.min(axis=0))
    upper=np.minimum(first.max(axis=0),second.max(axis=0))
    coordinate_ulp=np.maximum(np.spacing(np.abs(first)).max(axis=0),
                              np.spacing(np.abs(second)).max(axis=0))
    tolerance=8*coordinate_ulp
    if np.any(upper-lower<=tolerance):
        return False
    polygons=[tuple((Fraction(float(y)),Fraction(float(z))) for y,z in p)
              for p in (first,second)]
    edges=[tuple(zip(p,p[1:]+p[:1])) for p in polygons]
    breaks={point[0] for p in polygons for point in p}

    def cross(a,b):
        return a[0]*b[1]-a[1]*b[0]

    for a,b in edges[0]:
        direction=(b[0]-a[0],b[1]-a[1])
        for c,d in edges[1]:
            other=(d[0]-c[0],d[1]-c[1])
            denominator=cross(direction,other)
            if not denominator:
                continue
            offset=(c[0]-a[0],c[1]-a[1])
            t=cross(offset,other)/denominator
            u=cross(offset,direction)/denominator
            if 0<=t<=1 and 0<=u<=1:
                breaks.add(a[0]+t*direction[0])

    def intervals(boundary,y):
        crossings=sorted(a[1]+(y-a[0])*(b[1]-a[1])/(b[0]-a[0])
                         for a,b in boundary if min(a[0],b[0])<y<max(a[0],b[0]))
        return tuple(zip(crossings[::2],crossings[1::2]))

    ordered=sorted(breaks)
    for left,right in zip(ordered,ordered[1:]):
        y=(left+right)/2
        a_intervals=intervals(edges[0],y)
        b_intervals=intervals(edges[1],y)
        if any(min(b,d)-max(a,c)>tolerance[1] for a,b in a_intervals for c,d in b_intervals):
            return True
    return False


def section_geometry_from_params(record):
    """Return a shared immutable section solid, keyed by all exact geometry data."""
    g=record.get('geometry',record)
    bounds={f'{axis}_{end}_m':_scalar(g[f'{axis}_{end}_m']) for axis in 'xyz' for end in ('min','max')}
    x=_immutable(g['x_m']);weights=_immutable(g['integration_weights_m'])
    sections=tuple(tuple(_immutable(p) for p in section) for section in g['sections_yz_m'])
    digest=hashlib.sha256()
    digest.update(np.array(list(bounds.values()),dtype=np.float64).tobytes())
    for array in (x,weights):
        digest.update(np.array(array.shape,dtype=np.int64).tobytes());digest.update(array.tobytes())
    for section in sections:
        digest.update(len(section).to_bytes(8,'little'))
        for polygon in section:
            digest.update(np.array(polygon.shape,dtype=np.int64).tobytes());digest.update(polygon.tobytes())
    key=digest.digest()
    if key in _GEOMETRY_CACHE:
        _GEOMETRY_CACHE.move_to_end(key)
        return _GEOMETRY_CACHE[key]
    result=SectionTankGeometry(**bounds,x_m=x,integration_weights_m=weights,sections_yz_m=sections)
    _GEOMETRY_CACHE[key]=result
    while len(_GEOMETRY_CACHE)>_GEOMETRY_CACHE_SIZE:
        _GEOMETRY_CACHE.popitem(last=False)
    return result


@dataclass(frozen=True,eq=False)
class SectionTankGeometry:
    x_min_m: float
    x_max_m: float
    y_min_m: float
    y_max_m: float
    z_min_m: float
    z_max_m: float
    x_m: np.ndarray
    integration_weights_m: np.ndarray
    sections_yz_m: tuple
    shape: ClassVar[str] = 'hull_sections'
    is_rectangular: ClassVar[bool] = False
    capacity_m3: float = field(init=False)
    _full_centroid: tuple = field(init=False,repr=False)
    _batch: tuple = field(init=False,repr=False)
    _prismatic: bool = field(init=False,repr=False)
    _liquid_cache: OrderedDict = field(default_factory=OrderedDict,init=False,repr=False)

    def __post_init__(self):
        object.__setattr__(self,'x_m',_immutable(self.x_m))
        object.__setattr__(self,'integration_weights_m',_immutable(self.integration_weights_m))
        object.__setattr__(self,'sections_yz_m',tuple(tuple(_immutable(p) for p in section) for section in self.sections_yz_m))
        for axis in 'xyz':
            lo,hi=getattr(self,f'{axis}_min_m'),getattr(self,f'{axis}_max_m')
            if not np.isfinite([lo,hi]).all() or hi<=lo:
                raise ValueError('tank bounds must be finite with positive extents')
        if self.x_m.ndim!=1 or self.integration_weights_m.shape!=self.x_m.shape or len(self.sections_yz_m)!=len(self.x_m) or not len(self.x_m):
            raise ValueError('tank x, weights and sections must be nonempty with equal lengths')
        if not np.isfinite(self.x_m).all() or not np.isfinite(self.integration_weights_m).all() or np.any(np.diff(self.x_m)<=0) or np.any(self.integration_weights_m<=0):
            raise ValueError('tank stations must be finite ascending, with positive finite weights')
        if self.x_m[0]<self.x_min_m-1e-8 or self.x_m[-1]>self.x_max_m+1e-8:
            raise ValueError('tank station lies outside declared longitudinal bounds')
        total=np.zeros(4);volumes=[]
        for x,w,section in zip(self.x_m,self.integration_weights_m,self.sections_yz_m):
            if not section:
                raise ValueError('section tank cannot contain an empty station')
            combined=np.zeros(3)
            for polygon in section:
                if polygon.ndim!=2 or polygon.shape[1]!=2 or len(polygon)<3 or not np.isfinite(polygon).all():
                    raise ValueError('tank polygon requires finite [y,z] vertices')
                if np.any(polygon[:,0]<self.y_min_m-1e-8) or np.any(polygon[:,0]>self.y_max_m+1e-8) or np.any(polygon[:,1]<self.z_min_m-1e-8) or np.any(polygon[:,1]>self.z_max_m+1e-8):
                    raise ValueError('tank polygon lies outside declared section bounds')
                moments=_moments(polygon)
                if moments[0]<=0:
                    raise ValueError('tank polygons must have positive counterclockwise area')
                combined+=moments
            for i,polygon in enumerate(section):
                if any(_polygons_overlap(polygon,other) for other in section[i+1:]):
                    raise ValueError('tank section polygons must not overlap with positive area')
            volumes.append(w*sum(_moments(poly)[0] for poly in section))
            total+=w*np.array([combined[0],x*combined[0],combined[1],combined[2]])
        capacity=float(sum(volumes))
        if capacity<=0:
            raise ValueError('tank capacity must be positive')
        object.__setattr__(self,'capacity_m3',capacity)
        object.__setattr__(self,'_full_centroid',tuple(float(v) for v in total[1:]/total[0]))
        object.__setattr__(self,'_batch',self._make_batch(self.x_m,self.integration_weights_m,self.sections_yz_m))
        object.__setattr__(self,'_prismatic',self._is_prismatic())

    def _is_prismatic(self):
        reference=self.sections_yz_m[0]
        if any(len(section)!=len(reference) or any(not np.array_equal(a,b) for a,b in zip(section,reference)) for section in self.sections_yz_m):
            return False
        if len(self.x_m)%2:
            return False
        last=self.x_min_m
        for i in range(0,len(self.x_m),2):
            a,b=self.x_m[i:i+2];u,v=self.integration_weights_m[i:i+2]
            middle=(a+b)/2
            if not np.allclose([u,middle-u,a,b],[v,last,middle-u/np.sqrt(3),middle+u/np.sqrt(3)],rtol=1e-12,atol=1e-10):
                return False
            last=middle+u
        return bool(np.isclose(last,self.x_max_m,rtol=1e-12,atol=1e-10))

    @staticmethod
    def _make_batch(xs,weights,sections):
        records=[(x,w,i,p) for i,(x,w,section) in enumerate(zip(xs,weights,sections)) for p in section]
        size=max(len(p) for _,_,_,p in records)
        padded=np.array([np.concatenate((p,np.repeat(p[-1:],size-len(p),axis=0))) for _,_,_,p in records])
        return (padded,np.roll(padded,-1,axis=1),np.array([x for x,_,_,_ in records]),
                np.array([w for _,w,_,_ in records]),np.array([i for _,_,i,_ in records]))

    def _quadrature(self,level,normal):
        if not self._prismatic or abs(normal[0])<1e-14:
            return self.x_m,self.integration_weights_m,self.sections_yz_m,self._batch
        corners=np.vstack(self.sections_yz_m[0])
        crossings=(level-corners @ normal[1:])/normal[0]
        breaks=np.unique(np.r_[self.x_min_m,crossings[(crossings>self.x_min_m)&(crossings<self.x_max_m)],self.x_max_m])
        mid=(breaks[:-1]+breaks[1:])/2;half=np.diff(breaks)/2
        xs=np.column_stack((mid-half/np.sqrt(3),mid+half/np.sqrt(3))).ravel()
        weights=np.repeat(half,2)
        sections=(self.sections_yz_m[0],)*len(xs)
        return xs,weights,sections,self._make_batch(xs,weights,sections)

    def _integrate(self,level,normal,*,surface=False,distribution=False,origin=None):
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
        end=np.take_along_axis(points,following[...,None],axis=1)
        if origin is not None:
            local=np.take_along_axis(points,np.minimum(first,sentinel-1)[...,None],axis=1)
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

    def _tiny_liquid(self,volume,normal,*,surface=False,distribution=False):
        """Same polygon integration in a support-relative coordinate frame.

        At microscopic fills, absolute-coordinate shoelace subtraction and
        absolute liquid levels lose the wet thickness. Translation preserves
        polygon area/first moments while keeping that thickness representable.
        The ordinary-fill integration and root remain unchanged.
        """
        vertices=np.array([[x,y,z] for x,section in zip(self.x_m,self.sections_yz_m)
                           for polygon in section for y,z in polygon])
        if self._prismatic:
            vertices=np.array([[x,y,z] for x in (self.x_min_m,self.x_max_m)
                               for polygon in self.sections_yz_m[0] for y,z in polygon])
        origin=vertices[np.argmin(vertices @ normal)]
        heights=(vertices-origin) @ normal
        low,high=float(heights.min()),float(heights.max())
        def residual(level):
            return self._integrate(level,normal,origin=origin)[0]-volume
        level=brentq(residual,low,high,xtol=np.nextafter(0.,1.),
                     rtol=4*np.finfo(float).eps,maxiter=150)
        result=self._integrate(level,normal,origin=origin,surface=surface,distribution=distribution)
        return result,float(level+origin @ normal)

    def _volume(self,volume_m3):
        value=float(volume_m3)
        tolerance=max(8*np.spacing(self.capacity_m3),1e-12*self.capacity_m3,1e-10)
        if not np.isfinite(value) or value < -tolerance or value>self.capacity_m3+tolerance:
            raise ValueError('tank volume must be finite and between zero and capacity')
        return min(self.capacity_m3,max(0.,value))

    @staticmethod
    def _normal(heel_rad,trim_rad):
        if not np.isfinite([heel_rad,trim_rad]).all() or max(abs(heel_rad),abs(trim_rad))>=np.pi/2:
            raise ValueError('heel and trim must be finite and less than 90 degrees')
        normal=np.array([tan(trim_rad)*cos(heel_rad),-sin(heel_rad),cos(heel_rad)])
        return normal/np.linalg.norm(normal)

    def liquid(self,volume_m3,heel_rad=0.,trim_rad=0.):
        """Physical finite-angle centroid and central liquid-plane surface inertia."""
        volume=self._volume(volume_m3)
        normal=self._normal(heel_rad,trim_rad)
        key=(volume,float(heel_rad),float(trim_rad))
        if key in self._liquid_cache:
            self._liquid_cache.move_to_end(key)
            return self._liquid_cache[key]
        corners=np.array([[x,y,z] for x in (self.x_min_m,self.x_max_m) for y in (self.y_min_m,self.y_max_m) for z in (self.z_min_m,self.z_max_m)])
        projections=corners @ normal
        low,high=float(min(projections)),float(max(projections))
        if volume==0.:
            centroid=((self.x_min_m+self.x_max_m)/2,(self.y_min_m+self.y_max_m)/2,self.z_min_m)
            result=LiquidCondition(0.,centroid,0.,0.,low)
        elif volume==self.capacity_m3:
            result=LiquidCondition(volume,self._full_centroid,0.,0.,high)
        else:
            def residual(value):
                if value<=low:
                    return -volume
                if value>=high:
                    return self.capacity_m3-volume
                return self._integrate(value,normal)[0]-volume
            if volume<=_TINY_LIQUID_VOLUME_M3:
                (totals,area,inertia),level=self._tiny_liquid(volume,normal,surface=True)
            else:
                level=brentq(residual,low,high,xtol=5e-14,rtol=4*np.finfo(float).eps,maxiter=150)
                totals,area,inertia=self._integrate(level,normal,surface=True)
            if not np.isfinite(totals).all() or not np.isfinite([area,inertia,level]).all():
                raise ArithmeticError('section tank liquid produced nonfinite integration')
            tolerance=(2e-10*volume if volume<=_TINY_LIQUID_VOLUME_M3 and totals[0]>0.
                       else max(1e-10,2e-10*volume))
            if abs(totals[0]-volume)>tolerance:
                raise ArithmeticError('section tank liquid root failed volume tolerance')
            if totals[0]<=0. and volume<=_UNDEFINED_LIQUID_VOLUME_M3:
                # APPROXIMATION: D65 exactly-empty fallback; the dropped slack
                # free-surface inertia has no error bound and leans to pass.
                centroid=((self.x_min_m+self.x_max_m)/2,(self.y_min_m+self.y_max_m)/2,self.z_min_m)
                result=LiquidCondition(0.,centroid,0.,0.,low)
            else:
                if totals[0]<=0.:
                    raise ArithmeticError('section tank liquid has no resolved positive volume')
                result=LiquidCondition(volume,tuple(float(a) for a in totals[1:]/totals[0]),area,inertia,level)
        self._liquid_cache[key]=result
        while len(self._liquid_cache)>_LIQUID_CACHE_SIZE:
            self._liquid_cache.popitem(last=False)
        return result

    def sounding(self,volume_m3):
        """Upright true-solid sounding; return a fresh mutable model record."""
        condition=self.liquid(volume_m3)
        return TankSoundingPoint(volume_m3=condition.volume_m3,lcg_m=condition.centroid_m[0],
            tcg_m=condition.centroid_m[1],vcg_m=condition.centroid_m[2],fsm_m4=condition.fsm_m4)

    def section_liquid_areas(self,volume_m3,heel_rad=0.,trim_rad=0.):
        """Return immutable (x_m, dx_weights_m, wet_areas_m2) at the liquid plane.

        These are quadrature samples, not continuous uniform-density blocks.
        x/weights may refine for a proven continuous constant-section extrusion;
        arbitrary hull-section records retain their exact supplied quadrature.
        """
        condition=self.liquid(volume_m3,heel_rad,trim_rad)
        normal=self._normal(heel_rad,trim_rad)
        if condition.volume_m3==0.:
            return tuple(_immutable(a) for a in (self.x_m,self.integration_weights_m,np.zeros(len(self.x_m))))
        if condition.volume_m3<=_TINY_LIQUID_VOLUME_M3:
            return self._tiny_liquid(condition.volume_m3,normal,distribution=True)[0]
        return self._integrate(condition.plane_level_m,normal,distribution=True)
