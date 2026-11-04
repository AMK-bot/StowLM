"""Read checksum-pinned public offsets and build affine closed-hull sections.

Input ``hull_form.family='stowlm_offsets_v1'`` uses ``offsets_path`` (relative
to the repository root), mandatory ``offsets_sha256``, and ``offsets_format``:
``kcs_fix`` (the published KRISO Fortran body-plan layout),
``station_waterline_csv_v1`` (station_index,point_index,x_m,y_half_m,z_m), or
``stowlm_offsets_json_v1`` (``{'schema':'stowlm_offsets_json_v1','stations':
[{'x_m':..., 'half_section_yz_m':[[y,z],...]}]}``). Half contours run from the
centreline/bottom, out round the bilge and up to the deck; horizontal bottom
segments and nonmonotone z are retained. Points must describe one continuous
half-contour; the top is closed across the centreline by the weather deck.

Required source dimensions in hull_form are ``reference_lpp_m``,
``reference_beam_m``, ``reference_draught_m``, ``reference_depth_m``. x is
scaled by L/Lref about AP=0, y by B/Bref, and z by T/Tref below Tref and
(D-T)/(Dref-Tref) above it. Thus source deck sheer/flare are preserved under the
same affine vertical scaling. No coefficient-based replacement or synthetic
fallback exists. The KCS .fix coordinates are full-scale metres (readhull.txt),
Lref=230, Bref=32.2, Tref=10.8. Its upper cutoff is z=16, below the actual ship's
moulded depth of 19 m: choosing Dref=16 declares a sealed synthetic deck at this
cutoff. APPROXIMATION: above-water reserve buoyancy is this truncated source
contour stretched to the target deck, not a reconstruction of KCS's full deck.

``build_offset_geometry(params,nx=48,nz=64)`` returns the attributes dictionary
used by HullGeometry. It keeps every source station as a longitudinal panel
boundary, adds coaming boundaries, and uses composite two-point Gauss in x.
Each SOURCE half-contour is parameterised by cumulative arc length before
any target scaling; neighbouring
stations are evaluated at the union of their original node parameters before
linear interpolation in x. The actual interpolated sections are then mapped
vertically, inserting design-waterline intersections to preserve the scaling
kink exactly. nz only adds contour nodes; it never deletes source
bilge/bottom nodes. This supports bulbs and rise of floor without imposing a
single-valued breadth-versus-height function. All source x, including AP/FP
overhangs, remain buoyant. ``buoyant_x_min_m/max_m`` expose the actual extent.

APPROXIMATION: interpolation between supplied station contours is piecewise
linear in normalised contour arc length. Published offset accuracy, station
spacing and a sealed deck at the declared source cutoff limit realism. The
first/last published stations are sealed by transverse end caps; no missing
bow-tip shape is invented. Watertight coaming boxes at/above the deck count as
buoyant; containers/deckhouses do not. Overlapping coamings or submerged-deck
box overlap require pre-unioned source sections and fail explicitly. Source:
KRISO, KCS public offset archive, readhull.txt and kcs.fix (Gothenburg 2000).
"""
from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from stowage.params import repo_root


@dataclass(frozen=True)
class OffsetStation:
    x_m: float
    half_section_yz_m: np.ndarray


@dataclass(frozen=True)
class OffsetsTable:
    stations: tuple[OffsetStation, ...]
    source_sha256: str
    source_path: str


def _plain(value):
    if isinstance(value, Mapping):
        if 'value' in value and 'unit' in value:
            return _plain(value['value'])
        return {k:_plain(v) for k,v in value.items()}
    if isinstance(value,list):
        return [_plain(v) for v in value]
    return value


def verified_offset_bytes(path, *, expected_sha256: str) -> bytes:
    """Read the pinned source, including when geometry is served from a cache."""
    path=Path(path)
    if not path.is_absolute():
        path=repo_root()/path
    raw=path.read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if not isinstance(expected_sha256,str) or len(expected_sha256)!=64 or digest!=expected_sha256.lower():
        raise ValueError(f'offset source checksum mismatch: {path}')
    return raw


def read_offsets(path, *, expected_sha256: str, format: str) -> OffsetsTable:
    """Parse a pinned offset file; reject checksum mismatch before interpreting it."""
    raw=verified_offset_bytes(path, expected_sha256=expected_sha256)
    path=Path(path)
    if not path.is_absolute():
        path=repo_root()/path
    digest=hashlib.sha256(raw).hexdigest()
    text=raw.decode('utf-8-sig')
    records=[]
    if format=='kcs_fix':
        tokens=iter(' '.join(text.splitlines()[1:]).split())
        try:
            count=int(next(tokens))
            if not 2<=count<=100000:
                raise ValueError('invalid offset station count')
            for _ in range(count):
                x=float(next(tokens)); n=int(next(tokens))
                if not 2<=n<=100000:
                    raise ValueError('invalid section point count')
                y=[float(next(tokens)) for _ in range(n)]
                z=[float(next(tokens)) for _ in range(n)]
                records.append((x,np.column_stack((y,z))))
            if next(tokens,None) is not None:
                raise ValueError('unexpected trailing offset data')
        except (StopIteration,OverflowError) as exc:
            raise ValueError('truncated or invalid KCS offsets') from exc
    elif format=='station_waterline_csv_v1':
        groups={}
        for row in csv.DictReader(io.StringIO(text)):
            key=int(row['station_index'])
            groups.setdefault(key,[]).append((int(row['point_index']),float(row['x_m']),
                                             float(row['y_half_m']),float(row['z_m'])))
        for rows in groups.values():
            rows.sort()
            if len({r[0] for r in rows})!=len(rows) or len({r[1] for r in rows})!=1:
                raise ValueError('duplicate point index or inconsistent station x')
            records.append((rows[0][1],np.array([[r[2],r[3]] for r in rows])))
    elif format=='stowlm_offsets_json_v1':
        obj=json.loads(text)
        if obj.get('schema')!='stowlm_offsets_json_v1':
            raise ValueError('unsupported offsets JSON schema')
        records=[(float(s['x_m']),np.asarray(s['half_section_yz_m'],dtype=float)) for s in obj['stations']]
    else:
        raise ValueError(f'unsupported offset format: {format}')
    records.sort(key=lambda s:s[0])
    if len(records)<2 or any(b[0]<=a[0] for a,b in zip(records,records[1:])):
        raise ValueError('at least two distinct ascending stations required')
    result=[]
    for x,points in records:
        if points.ndim!=2 or points.shape[1]!=2 or len(points)<2 or not np.isfinite(points).all() or not math.isfinite(x):
            raise ValueError('invalid or nonfinite section coordinates')
        if np.any(points[:,0]<0):
            raise ValueError('half breadth must be nonnegative')
        points=points[np.r_[True,np.any(np.diff(points,axis=0)!=0,axis=1)]]
        if len(points)<2 or points[-1,1]<=points[0,1]:
            raise ValueError('section must run from bottom to upper deck')
        if points[0,0]>1e-9:
            # A flat bottom starts at centreline, then reaches the outer chine.
            points=np.vstack(([0.,points[0,1]],points))
        points.setflags(write=False)
        result.append(OffsetStation(x,points))
    return OffsetsTable(tuple(result),digest,str(path))


def _parameter(points):
    distance=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))]
    if distance[-1]<=0:
        raise ValueError('zero-length source half-contour')
    return distance/distance[-1]


def _refined_parameters(pa,pb,nz):
    # Union retains each source corner; additional intervals resolve long chords.
    return np.unique(np.r_[pa,pb,np.linspace(0.,1.,nz+1)])


def build_offset_geometry(ship_params: Mapping[str,Any], *, nx=48,nz=64) -> dict[str,Any]:
    """Construct complete section/quadrature attributes for HullGeometry dispatch."""
    p=_plain(ship_params);h=p['hull'];f=p['hull_form'];env=p['envelope']
    if f['family']!='stowlm_offsets_v1':
        raise ValueError('unsupported offset hull family')
    if nx<4 or nz<8:
        raise ValueError('nx >= 4 and nz >= 8 required')
    table=read_offsets(f['offsets_path'],expected_sha256=f['offsets_sha256'],format=f['offsets_format'])
    L,B,T,D=[float(h[k]) for k in ('lpp_m','beam_m','design_draught_m','depth_m')]
    lr,br,tr,dr=[float(f[k]) for k in ('reference_lpp_m','reference_beam_m','reference_draught_m','reference_depth_m')]
    rho=float(h['water_density_kg_m3'])
    if not np.isfinite([L,B,T,D,lr,br,tr,dr,rho]).all() or min(L,B,T,lr,br,tr,rho)<=0 or D<=T or dr<=tr:
        raise ValueError('invalid target/source principal dimensions')
    if env.get('count_deckhouse',False):
        raise ValueError('deckhouse buoyancy requires explicit source offsets')
    if abs(float(env['deck_z_m'])-D)>1e-8:
        raise ValueError('envelope deck_z_m must equal target depth_m')
    stations=[]
    for station in table.stations:
        original=station.half_section_yz_m
        # The piecewise vertical map has a slope break at the design plane.
        # Insert every crossing before scaling so no straight chord spans it.
        contour=[]
        for i,point in enumerate(original):
            contour.append(point)
            if i+1<len(original):
                other=original[i+1]
                if (point[1]-tr)*(other[1]-tr)<0:
                    contour.append(point+(tr-point[1])/(other[1]-point[1])*(other-point))
        contour=np.array(contour)
        # Correspondence belongs to the SOURCE surface. Reparameterising after
        # anisotropic scaling would subtly change the hull coefficients.
        parameters=_parameter(contour)
        stations.append((station.x_m*L/lr,contour,parameters))
    boxes=env.get('hatch_coamings',[])
    for i,b in enumerate(boxes):
        if b['z_min_m']<D-1e-9:
            raise ValueError('coaming below nominal deck requires pre-unioned offsets')
        if any(b[k+'_max_m']<=b[k+'_min_m'] for k in 'xyz'):
            raise ValueError('coaming box extents must be positive')
        for other in boxes[:i]:
            if all(min(b[k+'_max_m'],other[k+'_max_m'])>max(b[k+'_min_m'],other[k+'_min_m'])+1e-10 for k in 'xyz'):
                raise ValueError('overlapping coaming boxes require pre-unioned offsets')
    xmin,xmax=stations[0][0],stations[-1][0]
    if any(b['x_min_m']<xmin or b['x_max_m']>xmax for b in boxes):
        raise ValueError('coamings outside source longitudinal envelope')
    breaks=sorted(set([s[0] for s in stations]+[b[k] for b in boxes for k in ('x_min_m','x_max_m')]))
    positions=[];weights=[];sections=[]
    source_x=np.array([s[0] for s in stations])
    for left,right in zip(breaks,breaks[1:]):
        index=min(len(stations)-2,max(0,int(np.searchsorted(source_x,(left+right)/2))-1))
        xa,ca,pa=stations[index];xb,cb,pb=stations[index+1]
        parameters=_refined_parameters(pa,pb,nz)
        ac=np.column_stack([np.interp(parameters,pa,ca[:,k]) for k in (0,1)])
        bc=np.column_stack([np.interp(parameters,pb,cb[:,k]) for k in (0,1)])
        edges=np.linspace(left,right,max(1,math.ceil((right-left)*nx/L))+1)
        for lo,hi in zip(edges,edges[1:]):
            for xi in ((lo+hi)/2-(hi-lo)/(2*math.sqrt(3)),(lo+hi)/2+(hi-lo)/(2*math.sqrt(3))):
                source_half=ac+(xi-xa)/(xb-xa)*(bc-ac)
                # Interpolate the SOURCE solid first, then map every actual
                # section. Mapping station vertices first would let chords
                # cross the vertical-scale kink between longitudinal stations.
                nodes=[]
                for j,point in enumerate(source_half):
                    nodes.append(point)
                    if j+1<len(source_half):
                        other=source_half[j+1]
                        if (point[1]-tr)*(other[1]-tr)<0:
                            nodes.append(point+(tr-point[1])/(other[1]-point[1])*(other-point))
                half=np.array(nodes)
                half[:,0]*=B/br
                z=half[:,1].copy()
                half[:,1]=np.where(z<=tr,z*T/tr,T+(z-tr)*(D-T)/(dr-tr))
                reflected=half[::-1]*np.array([-1.,1.])
                polygon=np.vstack((half,reflected))
                polys=[polygon]
                for box in boxes:
                    if box['x_min_m']<xi<box['x_max_m']:
                        if np.max(half[:,1])>box['z_min_m']+1e-9:
                            raise ValueError('coaming overlaps source sheer; pre-unioned offsets required')
                        yl,yh,zl,zh=[float(box[k]) for k in ('y_min_m','y_max_m','z_min_m','z_max_m')]
                        polys.append(np.array([[yl,zl],[yh,zl],[yh,zh],[yl,zh]]))
                positions.append(xi);weights.append((hi-lo)/2);sections.append(polys)
    maximum=max(max(np.max(poly[:,1]) for poly in polys) for polys in sections)
    return dict(lpp_m=L,beam_m=B,depth_m=D,design_draught_m=T,density_kg_m3=rho,
                max_z_m=float(maximum),openings=env.get('openings',[]),x_m=np.array(positions),
                integration_weights_m=np.array(weights),sections=sections,
                buoyant_x_min_m=xmin,buoyant_x_max_m=xmax,source_sha256=table.source_sha256)


def _wet_triangle_areas(triangles, draught_m):
    triangles=np.asarray(triangles)
    areas=np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1)/2
    # Zero-halfbreadth centreline traces close degenerate sections below the
    # raised keel/bulb. Their mirrored sheets coincide inside the solid and
    # are not exterior shell. Exclude triangles wholly on that centreplane.
    areas[np.all(np.abs(triangles[:,:,1])<1e-12,axis=1)]=0.
    z=np.sort(triangles[:,:,2],axis=1)
    lo,mid,hi=z.T
    fraction=np.zeros(len(z))
    full=draught_m>=hi
    fraction[full]=1.
    low=(draught_m>lo)&(draught_m<mid)
    fraction[low]=(draught_m-lo[low])**2/((mid[low]-lo[low])*(hi[low]-lo[low]))
    high=(draught_m>=mid)&(draught_m<hi)&(draught_m>lo)
    fraction[high]=1-(hi[high]-draught_m)**2/((hi[high]-lo[high])*(hi[high]-mid[high]))
    return areas*fraction


def wetted_surface_area_m2(geometry, draught_m: float) -> float:
    """Upright bare-hull wetted area of the same ruled polygon loft, in m².

    Accept either a HullGeometry object or build_offset_geometry's dictionary.
    Each quadrature panel is recovered at its boundaries, corresponding contour
    edges are joined by two triangles, and each triangle is clipped exactly by
    the horizontal waterplane. Refining nx reduces the ruled-surface triangle
    diagonal approximation. Both transverse source end caps are included.
    This is for bare-hull reference checks below deck; coamings are dry there.
    """
    attrs=geometry if isinstance(geometry,Mapping) else vars(geometry)
    if not math.isfinite(draught_m) or draught_m>=attrs['depth_m']:
        raise ValueError('bare-hull wetted reference requires finite draught below deck')
    x=attrs['x_m'];w=attrs['integration_weights_m'];sections=attrs['sections']
    if len(x)%2:
        raise ValueError('two Gauss stations per panel required')
    triangles=[];first=None;last=None
    for j in range(0,len(x),2):
        p,q=sections[j][0],sections[j+1][0]
        if p.shape!=q.shape:
            raise ValueError('corresponding contour vertices required within each Gauss panel')
        mid=(x[j]+x[j+1])/2
        left,right=mid-w[j],mid+w[j]
        c0=p+(left-x[j])/(x[j+1]-x[j])*(q-p)
        c1=p+(right-x[j])/(x[j+1]-x[j])*(q-p)
        a=np.column_stack((np.full(len(p),left),c0))
        b=np.column_stack((np.full(len(p),right),c1))
        an,bn=np.roll(a,-1,axis=0),np.roll(b,-1,axis=0)
        triangles.extend(np.stack((a,b,bn),axis=1))
        triangles.extend(np.stack((a,bn,an),axis=1))
        if first is None: first=c0
        last=c1
    result=float(_wet_triangle_areas(triangles,draught_m).sum())
    # Polygon clipping is reused only for planar source end caps; the lateral
    # surface above is computed independently of hydrostatic first moments.
    from stowage.vessel.hydrostatics import _clip, _moments
    for contour in (first,last):
        result+=abs(float(_moments(_clip(contour,0.,1.,draught_m))[0]))
    return result
