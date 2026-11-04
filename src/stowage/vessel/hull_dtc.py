"""Convert the pinned Duisburg Test Case mesh to actual station contours.

The OpenFOAM ASCII STL is inert input: triangles are intersected with vertical
station planes, joined by their shared edges, and the port half-contours are
written in ``stowlm_offsets_json_v1`` format for HullGeometry. No hull coefficient
is fitted. Source: el Moctar, Shigunov & Zorn (2012), Table 1 and Introduction,
scale 1:59.407; Piehl (2016), section 2.1 fixes AP/centreplane/baseline origin.
Downloaded evidence and licences are in data/raw/dtc/NOTES.md and MANIFEST.

APPROXIMATION: the production offset interface mirrors the published port half
of the nominally symmetric mesh. Mesh port/starboard numerical asymmetry is
therefore discarded, not averaged or calibrated. Slice intersections retain
all nonduplicate facet corners. Joining tolerance is 1e-7 full-scale m; actual
intersection coordinates are retained rather than rounded. Separated bulb/stem
components connect by a zero-breadth centreplane trace (zero added area).
Station loft interpolation remains hull_offsets.py's declared approximation.
The extreme slices are inset by 1e-8 of mesh length, then closed transversely;
this avoids degenerate point sections without inventing a missing hull tip.
The source upper cutoff is sealed at approximately 34 m, not a certified deck.
"""
from __future__ import annotations

from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from stowage.params import repo_root

DTC_SCALE = 59.407
DTC_SOURCE_SHA256 = '61a60ffca0aae2ffb7703cdaf1bab4de7dfeb719ccc3ea8ae9eadd1932c097a0'


def read_dtc_triangles(source_path, *, expected_sha256=DTC_SOURCE_SHA256):
    """Checksum-verify gzip ASCII STL, returning full-scale metre triangles."""
    path=Path(source_path)
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ValueError('DTC source checksum mismatch')
    text=gzip.decompress(raw).decode('ascii')
    if not text.lstrip().startswith('solid '):
        raise ValueError('DTC requires ASCII STL')
    vertices=[tuple(map(float,line.split()[1:])) for line in text.splitlines()
              if line.lstrip().startswith('vertex ')]
    if not vertices or len(vertices)%3:
        raise ValueError('DTC STL vertex count must be a nonempty multiple of three')
    triangles=np.asarray(vertices,dtype=float).reshape(-1,3,3)*DTC_SCALE
    if not np.isfinite(triangles).all():
        raise ValueError('nonfinite DTC vertex')
    return triangles


def slice_dtc_half_contour(triangles, station_x_m):
    """Intersect all facets at x, return ordered port (y,z) shell coordinates.

    Shared-edge joins are topological; points are never sorted by z, preserving
    bulbs and overhangs. Disjoint components must each end on the centreplane.
    A branched, closed off-centre or incomplete section fails explicitly.
    """
    triangles=np.asarray(triangles)
    starts=triangles
    ends=np.roll(triangles,-1,axis=1)
    crosses=(starts[:,:,0]<station_x_m)!=(ends[:,:,0]<station_x_m)
    ti,ei=np.nonzero(crosses)
    counts=np.bincount(ti,minlength=len(triangles))
    if not np.all((counts==0)|(counts==2)):
        raise ValueError('station intersects degenerate triangle; choose an adjacent plane')
    aa,bb=starts[ti,ei],ends[ti,ei]
    hit=aa+(bb-aa)*((station_x_m-aa[:,0])/(bb[:,0]-aa[:,0]))[:,None]
    segments=hit.reshape(-1,2,3)[:,:,1:]
    segments=segments[np.any(segments[:,:,0]>=0,axis=1)]
    graph=defaultdict(set)
    coordinates={}
    for a,b in segments:
        a,b=a.copy(),b.copy()
        if a[0]<0:
            a += (b-a)*(-a[0])/(b[0]-a[0]);a[0]=0.
        if b[0]<0:
            b += (a-b)*(-b[0])/(a[0]-b[0]);b[0]=0.
        ka,kb=(tuple(np.round(point,7)) for point in (a,b))
        if ka==kb:
            continue
        graph[ka].add(kb);graph[kb].add(ka)
        coordinates.setdefault(ka,a);coordinates.setdefault(kb,b)
    if not graph or any(len(neighbours)>2 for neighbours in graph.values()):
        raise ValueError('DTC station graph is empty or branched')
    components=[]
    remaining=set(graph)
    while remaining:
        seed=min(remaining)
        component=set();stack=[seed]
        while stack:
            node=stack.pop()
            if node not in component:
                component.add(node);stack.extend(graph[node]-component)
        endpoints=[node for node in component if len(graph[node])==1]
        if len(endpoints)!=2 or any(abs(coordinates[node][0])>1e-6 for node in endpoints):
            raise ValueError('DTC component must be open at two centreplane endpoints')
        start=min(endpoints,key=lambda node:coordinates[node][1])
        ordered=[];previous=None;node=start
        while True:
            ordered.append(coordinates[node])
            following=graph[node]-({previous} if previous is not None else set())
            if not following:
                break
            previous,node=node,next(iter(following))
        if len(ordered)!=len(component):
            raise ValueError('DTC station does not form a single shell path')
        components.append(np.asarray(ordered))
        remaining-=component
    components.sort(key=lambda contour:contour[0,1])
    for first,second in zip(components,components[1:]):
        if first[-1,1]>second[0,1]+1e-7:
            raise ValueError('DTC disconnected components overlap vertically')
    contour=np.vstack(components)
    contour[:,0]=np.maximum(0.,contour[:,0])
    return contour


def write_dtc_offsets(source_path, destination_path, *, station_count=121,
                      expected_sha256=DTC_SOURCE_SHA256):
    """Write reproducible full-scale source slices; return output SHA256.

    station_count controls equally spaced planes across the full mesh extent;
    AP and FP are included explicitly. More planes refine the loft, not the
    original facet geometry. Typical one-core conversion is a few seconds.
    """
    if station_count<9:
        raise ValueError('at least nine DTC stations required')
    triangles=read_dtc_triangles(source_path,expected_sha256=expected_sha256)
    minimum,maximum=float(triangles[:,:,0].min()),float(triangles[:,:,0].max())
    inset=(maximum-minimum)*1e-8
    stations=np.unique(np.r_[np.linspace(minimum+inset,maximum-inset,station_count),0.,355.])
    records=[{'x_m':float(x),'half_section_yz_m':slice_dtc_half_contour(triangles,x).tolist()} for x in stations]
    obj={'schema':'stowlm_offsets_json_v1','source_sha256':expected_sha256,
         'source_path':str(source_path),'source_scale':DTC_SCALE,'station_count_requested':station_count,
         'conversion':'Vertical triangle slices; port half retained and mirrored; no coefficient fitting.',
         'endpoint_inset_m':inset,'stations':records}
    destination=Path(destination_path)
    destination.parent.mkdir(parents=True,exist_ok=True)
    payload=(json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode()
    destination.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def dtc_reference_params(offsets_path):
    """Minimal geometry params in full-scale metres, pinned to converted offsets."""
    path=Path(offsets_path)
    if not path.is_absolute():
        path=repo_root()/path
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    return {'hull':{'lpp_m':355.,'beam_m':51.,'depth_m':34.,'design_draught_m':14.5,
                    'water_density_kg_m3':1025.},
            'hull_form':{'family':'stowlm_offsets_v1','offsets_path':str(path),
                         'offsets_sha256':digest,'offsets_format':'stowlm_offsets_json_v1',
                         'reference_lpp_m':355.,'reference_beam_m':51.,
                         'reference_draught_m':14.5,'reference_depth_m':34.},
            'envelope':{'deck_z_m':34.,'count_deckhouse':False,'openings':[],'hatch_coamings':[]}}
