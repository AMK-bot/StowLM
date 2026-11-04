"""Build physical tank solids by intersecting compartments with source hull sections.

``build_hull_tank_geometry(ship_params, tank_bounds, nx=48, nz=64,
inner_skin_m=None)`` accepts the complete parsed fleet parameters and mandatory
``x_min_m,x_max_m,y_min_m,y_max_m,z_min_m,z_max_m`` compartment bounds. The
optional ``inner_skin_m`` keyword (or same key in tank_bounds) is a nonnegative
uniform inward distance in each TRANSVERSE section. Returned geometry uses the
existing tank schema: ``shape='hull_sections'``, ``x_m``,
``integration_weights_m``, ``sections_yz_m`` (station/polygon/vertex/[y,z]), and
the six bounding keys. It can be passed directly to TankGeometry.from_params.
A compartment with no positive integrated volume raises ValueError.

Hull sections come from the checksum-pinned ``stowlm_offsets_v1`` source loft,
re-evaluated at this tank's Gauss stations. Source stations and compartment x
ends are integration-panel boundaries; two-point Gauss weights are retained.
No interpolation of unrelated hull Gauss polygons, rectangular replacement, or
coefficient approximation is used. Source contour corners and design-waterline
scale breaks are retained; nz adds points without deleting source nodes.

The optional inner skin is the mitred inward offset of the ACTUAL section
polygon before clipping to the compartment. This is a transverse Euclidean
clearance to the polygonal shell, not a reduction of its bounding box and not a
three-dimensional normal offset of a sloping longitudinal shell. Mitred reentrant
corners conservatively exclude extra liquid relative to a circular erosion arc.
Longitudinal bulkhead positions remain the explicit compartment x bounds. Use
Shapely/GEOS polygon topology to retain every disjoint component. Zero-area
centreline traces in a fine-ended source contour can be removed only if the
cleanup preserves finite area; finite-area invalid polygons raise an error.

APPROXIMATION: the source hull loft, transverse inner-skin allowance and Gauss
longitudinal quadrature describe an unbaffled synthetic tank, not scantlings,
frames, piping, permeability, or a surveyed tank. Uniform transverse clearance
is a declared design allowance. Consumers must clip this solid at actual liquid
heel/trim; its bounding box is never its liquid geometry. Sources: the public
KCS body-plan offsets/readhull.txt, production hull_offsets.py, and IMO 2008 IS
Code Part B 3.1 (free-surface effects require the actual liquid surface).

Shapely is imported only when this construction helper is called. Already
serialized hull_sections geometry does not require this construction dependency.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import numpy as np

from stowage.vessel.hull_offsets import _parameter, _plain, _refined_parameters, read_offsets
from stowage.vessel.hydrostatics import _moments


class _SourceLoft:
    """The same source-first, piecewise vertical affine map as hull_offsets."""

    def __init__(self, params, nz):
        h,f=params['hull'],params['hull_form']
        if f['family']!='stowlm_offsets_v1':
            raise ValueError('hull-derived tank builder requires explicit stowlm_offsets_v1 source sections')
        self.L,self.B,self.T,self.D=[float(h[k]) for k in ('lpp_m','beam_m','design_draught_m','depth_m')]
        self.lr,self.br,self.tr,self.dr=[float(f[k]) for k in ('reference_lpp_m','reference_beam_m','reference_draught_m','reference_depth_m')]
        if not np.isfinite([self.L,self.B,self.T,self.D,self.lr,self.br,self.tr,self.dr]).all() or min(self.L,self.B,self.T,self.lr,self.br,self.tr)<=0 or self.D<=self.T or self.dr<=self.tr:
            raise ValueError('invalid source/target hull dimensions')
        table=read_offsets(f['offsets_path'],expected_sha256=f['offsets_sha256'],format=f['offsets_format'])
        self.source_sha256=table.source_sha256
        self.stations=[]
        for station in table.stations:
            contour=self._insert_waterline(station.half_section_yz_m)
            self.stations.append((station.x_m*self.L/self.lr,contour,_parameter(contour)))
        self.x=np.array([s[0] for s in self.stations])
        self.nz=nz
        self._pairs={}

    def _insert_waterline(self, points):
        result=[]
        for i,p in enumerate(points):
            result.append(p)
            if i+1<len(points):
                q=points[i+1]
                if (p[1]-self.tr)*(q[1]-self.tr)<0:
                    result.append(p+(self.tr-p[1])/(q[1]-p[1])*(q-p))
        return np.array(result)

    def section(self,x):
        i=min(len(self.x)-2,max(0,int(np.searchsorted(self.x,x))-1))
        xa,a,pa=self.stations[i];xb,b,pb=self.stations[i+1]
        if i not in self._pairs:
            knots=_refined_parameters(pa,pb,self.nz)
            aa=np.column_stack([np.interp(knots,pa,a[:,k]) for k in (0,1)])
            bb=np.column_stack([np.interp(knots,pb,b[:,k]) for k in (0,1)])
            self._pairs[i]=(aa,bb)
        aa,bb=self._pairs[i]
        half=self._insert_waterline(aa+(x-xa)/(xb-xa)*(bb-aa))
        half[:,0]*=self.B/self.br
        z=half[:,1].copy()
        half[:,1]=np.where(z<=self.tr,z*self.T/self.tr,
                           self.T+(z-self.tr)*(self.D-self.T)/(self.dr-self.tr))
        return np.vstack((half,half[::-1]*[-1,1]))


def _polygon_components(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type=='Polygon':
        return [geometry]
    if geometry.geom_type in ('MultiPolygon','GeometryCollection'):
        return [p for g in geometry.geoms for p in _polygon_components(g)]
    return []  # Boundary-only line/point intersections have zero capacity.


def build_hull_tank_geometry(ship_params:Mapping[str,Any],tank_bounds:Mapping[str,Any],
                             *,nx=48,nz=64,inner_skin_m:float|None=None) -> dict[str,Any]:
    """Return a serialized, possibly multi-component hull-intersection tank solid."""
    try:
        from shapely.geometry import Polygon,box
    except ImportError as exc:
        raise ImportError('constructing hull-derived tank sections requires Shapely; serialized hull_sections runtime does not') from exc
    params,bounds=_plain(ship_params),_plain(tank_bounds)
    if nx<4 or nz<8:
        raise ValueError('nx >= 4 and nz >= 8 required')
    bounds={k:float(bounds[k]) for k in (f'{a}_{end}_m' for a in 'xyz' for end in ('min','max'))}
    for axis in 'xyz':
        lo,hi=bounds[axis+'_min_m'],bounds[axis+'_max_m']
        if not np.isfinite([lo,hi]).all() or hi<=lo:
            raise ValueError('tank compartment bounds must be finite and increasing')
    if inner_skin_m is None:
        inner_skin_m=float(_plain(tank_bounds).get('inner_skin_m',0.))
    inner_skin_m=float(inner_skin_m)
    if not math.isfinite(inner_skin_m) or inner_skin_m<0:
        raise ValueError('inner_skin_m must be finite and nonnegative')
    loft=_SourceLoft(params,nz)
    x0=max(bounds['x_min_m'],float(loft.x[0]))
    x1=min(bounds['x_max_m'],float(loft.x[-1]))
    if x1<=x0:
        raise ValueError('tank compartment does not intersect source hull')
    breaks=np.unique(np.r_[x0,loft.x[(loft.x>x0)&(loft.x<x1)],x1])
    compartment=box(bounds['y_min_m'],bounds['z_min_m'],bounds['y_max_m'],bounds['z_max_m'])
    xs=[];weights=[];sections=[]
    for left,right in zip(breaks,breaks[1:]):
        edges=np.linspace(left,right,max(1,math.ceil((right-left)*nx/loft.L))+1)
        for a,b in zip(edges,edges[1:]):
            for sign in (-1,1):
                x=(a+b)/2+sign*(b-a)/(2*math.sqrt(3))
                source=Polygon(loft.section(x))
                if not source.is_valid:
                    clean=source.buffer(0)
                    if abs(clean.area-source.area)>1e-10*max(1.,source.area):
                        raise ValueError(f'finite-area invalid source section at x={x}')
                    source=clean
                inner=source.buffer(-inner_skin_m,join_style=2) if inner_skin_m else source
                cut=inner.intersection(compartment)
                polygons=[]
                for part in _polygon_components(cut):
                    if part.area<=1e-12:
                        continue
                    if part.interiors:
                        # A single compartment intersection of a simple inset
                        # hull should not create holes. Refuse an undocumented
                        # hole representation instead of filling it with liquid.
                        raise ValueError('tank section contains holes; explicit disjoint decomposition required')
                    points=np.asarray(part.exterior.coords[:-1],dtype=float)
                    if _moments(points)[0]<0:
                        points=points[::-1]
                    polygons.append(points.tolist())
                if polygons:
                    xs.append(float(x));weights.append(float((b-a)/2));sections.append(polygons)
    capacity=sum(w*sum(_moments(np.asarray(p))[0] for p in pp) for w,pp in zip(weights,sections))
    if capacity<=1e-10:
        raise ValueError('tank compartment has no positive hull-intersection volume')
    allpoints=np.vstack([p for pp in sections for p in pp])
    actual=dict(x_min_m=x0,x_max_m=x1,y_min_m=float(allpoints[:,0].min()),
                y_max_m=float(allpoints[:,0].max()),z_min_m=float(allpoints[:,1].min()),
                z_max_m=float(allpoints[:,1].max()))
    return dict(shape='hull_sections',x_m=xs,integration_weights_m=weights,
                sections_yz_m=sections,**actual)
