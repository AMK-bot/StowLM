"""Optional source-to-tank construction, distinct from dependency-free runtime.

Run with the fleet preprocessing environment (uv --with shapely). Serialized
hull_sections tank evaluation itself does not require Shapely.
"""
import hashlib
import json

import numpy as np
import pytest

@pytest.fixture(autouse=True)
def _preprocessing_dependency():
    pytest.importorskip('shapely',reason='optional source tank preprocessing dependency')

from stowage.params import load_param_file
from stowage.vessel.hull_tank_sections import build_hull_tank_geometry
from stowage.vessel.hydrostatics import HullGeometry, _clip, _moments


def _integrals(g):
    result=np.zeros(4)
    for x,w,polys in zip(g['x_m'],g['integration_weights_m'],g['sections_yz_m'],strict=True):
        m=sum((_moments(np.asarray(p)) for p in polys),np.zeros(3))
        result+=w*np.array([m[0],x*m[0],m[1],m[2]])
    return result


def _box_ship(tmp_path):
    path=tmp_path/'box.json'
    path.write_text(json.dumps(dict(schema='stowlm_offsets_json_v1',stations=[
        dict(x_m=x,half_section_yz_m=[[0,0],[5,0],[5,2],[5,8]]) for x in (0,5,10)])))
    return dict(hull=dict(lpp_m=10,beam_m=10,design_draught_m=2,depth_m=8,water_density_kg_m3=1025),
        hull_form=dict(family='stowlm_offsets_v1',offsets_path=str(path),
            offsets_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),offsets_format='stowlm_offsets_json_v1',
            reference_lpp_m=10,reference_beam_m=10,reference_draught_m=2,reference_depth_m=8),
        envelope=dict(deck_z_m=8,hatch_coamings=[],openings=[]))


def test_box_compartment_inside_box_exact_capacity_and_centroid(tmp_path):
    ship=_box_ship(tmp_path)
    bounds=dict(x_min_m=2,x_max_m=7,y_min_m=-2,y_max_m=3,z_min_m=1,z_max_m=4)
    solid=build_hull_tank_geometry(ship,bounds,nx=8,nz=8)
    total=_integrals(solid)
    assert solid['shape']=='hull_sections'
    assert total[0]==pytest.approx(5*5*3,abs=1e-10)
    assert total[1:]/total[0]==pytest.approx((4.5,.5,2.5),abs=1e-12)
    assert sum(solid['integration_weights_m'])==pytest.approx(5)


def test_inner_skin_offsets_actual_box_shell_before_compartment_clip(tmp_path):
    ship=_box_ship(tmp_path)
    bounds=dict(x_min_m=1,x_max_m=9,y_min_m=-9,y_max_m=9,z_min_m=-2,z_max_m=9)
    solid=build_hull_tank_geometry(ship,bounds,nx=8,nz=8,inner_skin_m=.5)
    total=_integrals(solid)
    # A transverse inset changes beam and depth; x bulkheads are explicit.
    assert total[0]==pytest.approx(8*9*7)
    assert total[1:]/total[0]==pytest.approx((5,0,4))
    assert solid['y_min_m']==pytest.approx(-4.5)
    assert solid['z_min_m']==pytest.approx(.5)


def test_empty_or_invalid_inner_skin_fails(tmp_path):
    ship=_box_ship(tmp_path)
    bounds=dict(x_min_m=1,x_max_m=9,y_min_m=-9,y_max_m=9,z_min_m=-2,z_max_m=9)
    with pytest.raises(ValueError,match='nonnegative'):
        build_hull_tank_geometry(ship,bounds,inner_skin_m=-.1)
    with pytest.raises(ValueError,match='positive'):
        build_hull_tank_geometry(ship,bounds,inner_skin_m=10)


def test_kcs_double_bottom_matches_independent_hull_clipping_and_refines():
    ship=load_param_file('fleet','feeder_500')
    L=ship['hull']['lpp_m']
    bounds=dict(x_min_m=.1*L,x_max_m=.8*L,y_min_m=0,y_max_m=ship['hull']['beam_m'],
                z_min_m=.15,z_max_m=1.2)
    coarse=build_hull_tank_geometry(ship,bounds,nx=48,nz=64)
    fine=build_hull_tank_geometry(ship,bounds,nx=96,nz=128)
    # Independent production hull integration, retaining actual curved shell.
    # .1L/.8L are KCS source stations, so no cut cell is silently dropped.
    ship['envelope']['hatch_coamings']=[]
    reference=HullGeometry.from_params(ship,nx=192,nz=256)
    target=np.zeros(4)
    for x,w,polys in zip(reference.x_m,reference.integration_weights_m,reference.sections,strict=True):
        if not bounds['x_min_m']<x<bounds['x_max_m']:
            continue
        poly=polys[0]
        for ny,nz,h in ((-1,0,0),(0,-1,-.15),(0,1,1.2)):
            poly=_clip(poly,ny,nz,h)
        m=_moments(poly)
        target+=w*np.array([m[0],x*m[0],m[1],m[2]])
    a,b=_integrals(coarse),_integrals(fine)
    assert b[0]==pytest.approx(target[0],rel=1e-3)
    assert b[1:]/b[0]==pytest.approx(target[1:]/target[0],rel=1e-3,abs=1e-4)
    assert abs(a[0]-b[0])/b[0]<1e-3
    box_volume=(bounds['x_max_m']-bounds['x_min_m'])*ship['hull']['beam_m']/2*1.05
    assert b[0]<.95*box_volume  # Hull curvature materially changes this capacity.


def test_nonconvex_skin_preserves_disjoint_polygon_components(tmp_path):
    ship=_box_ship(tmp_path)
    # Narrow the source at mid-height. A port-side compartment away from
    # centreline intersects separate upper and lower lobes of this real polygon.
    path=ship['hull_form']['offsets_path']
    from pathlib import Path
    obj=json.loads(Path(path).read_text())
    for station in obj['stations']:
        station['half_section_yz_m']=[[0,0],[5,0],[5,2],[1,3],[1,5],[5,6],[5,8]]
    Path(path).write_text(json.dumps(obj))
    ship['hull_form']['offsets_sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    bounds=dict(x_min_m=1,x_max_m=9,y_min_m=2,y_max_m=6,z_min_m=2,z_max_m=6)
    solid=build_hull_tank_geometry(ship,bounds,nx=8,nz=8,inner_skin_m=.2)
    assert _integrals(solid)[0]>0
    assert all(len(pp)==2 for pp in solid['sections_yz_m'])
    assert all(np.asarray(p)[:,0].min()>=2-1e-12 for pp in solid['sections_yz_m'] for p in pp)
    assert all(not any(3<float(z)<5 for _,z in p) for pp in solid['sections_yz_m'] for p in pp)


def test_actual_kcs_inner_skin_has_euclidean_shell_clearance():
    from shapely.geometry import Polygon,Point
    ship=load_param_file('fleet','feeder_500')
    ship['envelope']['hatch_coamings']=[]
    L,B=ship['hull']['lpp_m'],ship['hull']['beam_m']
    bounds=dict(x_min_m=.1*L,x_max_m=.8*L,y_min_m=0,y_max_m=B,z_min_m=.15,z_max_m=1.2)
    solid=build_hull_tank_geometry(ship,bounds,nx=48,nz=64,inner_skin_m=.02)
    hull=HullGeometry.from_params(ship,nx=48,nz=64)
    for x,polys in zip(solid['x_m'],solid['sections_yz_m'],strict=True):
        index=int(np.argmin(abs(hull.x_m-x)))
        assert abs(hull.x_m[index]-x)<1e-8
        shell=Polygon(hull.sections[index][0]).buffer(0)
        for poly in polys:
            for yz in poly:
                point=Point(yz)
                assert shell.covers(point)
                assert shell.boundary.distance(point)>=.02-1e-8
