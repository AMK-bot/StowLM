"""Real fleet hull-section tanks: capacities, soundings and finite liquid solids.

These tests use the explicit solid and its quadrature, never a bounding-box
surrogate. The 30–45% ballast/DWT design range is reported separately: the
declared sourced 500-class target is an explicit exception, not silently resized.
"""
from math import cos, sin, tan

import numpy as np
import pytest

from stowage.params import load_param_file
from stowage.vessel.hydrostatics import _clip, _moments, _water_segments
from stowage.vessel.tanks import TankGeometry, build_tanks


@pytest.fixture(scope="module",params=["feeder_500","feeder_800","feeder_1700"])
def fleet(request):
    params=load_param_file("fleet",request.param)
    return request.param,params,{t.id:t for t in build_tanks(params)}


def integrate_record_liquid(record,level,heel,trim):
    """Independently integrate the stored solid at a returned liquid level."""
    g=record["geometry"]
    normal=np.array([tan(trim)*cos(heel),-sin(heel),cos(heel)])
    normal/=np.linalg.norm(normal)
    longitudinal=np.array([1.,0.,-tan(trim)])
    longitudinal/=np.linalg.norm(longitudinal)
    transverse=np.cross(normal,longitudinal)
    total=np.zeros(4)
    area=first=second=0.
    for x,w,section in zip(g["x_m"],g["integration_weights_m"],g["sections_yz_m"],strict=True):
        section_level=level-normal[0]*x
        for vertices in section:
            poly=np.asarray(vertices)
            a,my,mz=_moments(_clip(poly,normal[1],normal[2],section_level))
            total+=w*np.array([a,x*a,my,mz])
            for start,end in _water_segments(poly,normal[1],normal[2],section_level):
                length=float(np.linalg.norm(end-start))
                da=w*length/np.hypot(normal[1],normal[2])
                u=float(np.dot(np.r_[x,start],transverse))
                v=float(np.dot(np.r_[x,end],transverse))
                area+=da
                first+=da*(u+v)/2
                second+=da*(u*u+u*v+v*v)/3
    return total,area,max(0.,second-first*first/area) if area else 0.


def test_all_fleet_capacities_and_sounding_tables_use_real_solids(fleet):
    _,params,built=fleet
    assert len(built)==len(params["tanks"])
    hull_following=0
    non_box_volumes=0
    for record in params["tanks"]:
        tank=built[record["id"]]
        solid=TankGeometry.from_params(record)
        assert solid.capacity_m3==pytest.approx(record["capacity_m3"],rel=1e-9,abs=1e-7)
        assert tank.capacity_m3==record["capacity_m3"]
        assert tank.x_aft_m==record["x_aft_m"]
        assert tank.x_fore_m==record["x_fore_m"]
        assert tank.heated==record["heated"]
        assert tank.adjustable==record["adjustable"]
        if record["geometry"]["shape"]=="hull_sections":
            hull_following+=1
            assert solid.shape=="hull_sections"
            assert solid.is_rectangular is False
            g=record["geometry"]
            volume=sum(w*sum(_moments(np.asarray(p))[0] for p in polygons)
                       for w,polygons in zip(g["integration_weights_m"],g["sections_yz_m"],strict=True))
            assert volume==pytest.approx(solid.capacity_m3,rel=1e-10,abs=1e-7)
            box_volume=np.prod([g[f"{axis}_max_m"]-g[f"{axis}_min_m"] for axis in "xyz"])
            assert solid.capacity_m3<=box_volume*(1+1e-8)
            non_box_volumes+=solid.capacity_m3<box_volume*.99
        for actual,declared in zip(tank.table,record["table"],strict=True):
            assert actual.volume_m3==pytest.approx(declared["volume_m3"],rel=1e-9,abs=1e-6)
            for coordinate in ("lcg_m","tcg_m","vcg_m"):
                assert getattr(actual,coordinate)==pytest.approx(declared[coordinate],abs=1e-6)
            assert actual.fsm_m4==pytest.approx(declared["fsm_m4"],rel=1e-6,abs=1e-6)
        assert tank.table[0].fsm_m4==tank.table[-1].fsm_m4==0
        assert tank.table[-1].volume_m3==tank.capacity_m3
    assert hull_following>0
    assert non_box_volumes>0  # Bounds demonstrably cannot replace the solids.


def test_ballast_and_heated_fuel_capacity_match_declared_design_targets(fleet):
    _,params,built=fleet
    totals={kind:sum(t.capacity_m3 for t in built.values() if t.kind==kind)
            for kind in ("ballast","fuel_oil")}
    design=params["tank_design"]
    assert totals["ballast"]==pytest.approx(design["target_ballast_m3"],rel=1e-9,abs=1e-5)
    assert totals["ballast"]==pytest.approx(design["ballast_capacity_m3"],rel=1e-9,abs=1e-5)
    assert totals["fuel_oil"]==pytest.approx(design["target_hfo_m3"],rel=1e-9,abs=1e-5)
    assert all(t.heated for t in built.values() if t.kind=="fuel_oil")
    assert all(t.adjustable for t in built.values() if t.kind=="ballast")


@pytest.mark.parametrize("fraction,heel,trim",[(.37,.35,.008),(.62,-.2,-.006)])
def test_finite_angle_liquid_matches_explicit_section_solid(fleet,fraction,heel,trim):
    _,params,_=fleet
    records=[r for r in params["tanks"] if r["kind"]=="ballast" and r["geometry"]["shape"]=="hull_sections"]
    record=max(records,key=lambda r:r["capacity_m3"])
    solid=TankGeometry.from_params(record)
    target=fraction*solid.capacity_m3
    liquid=solid.liquid(target,heel_rad=heel,trim_rad=trim)
    total,area,inertia=integrate_record_liquid(record,liquid.plane_level_m,heel,trim)
    assert total[0]==pytest.approx(target,rel=1e-8,abs=1e-6)
    assert liquid.centroid_m==pytest.approx(total[1:]/total[0],abs=1e-7)
    assert liquid.free_surface_area_m2==pytest.approx(area,rel=1e-7,abs=1e-6)
    assert liquid.free_surface_inertia_m4==pytest.approx(inertia,rel=1e-7,abs=1e-6)
    x,weights,section_areas=solid.section_liquid_areas(target,heel_rad=heel,trim_rad=trim)
    assert np.dot(weights,section_areas)==pytest.approx(target,rel=1e-8,abs=1e-6)
    assert np.dot(weights*section_areas,x)/target==pytest.approx(liquid.centroid_m[0],abs=1e-7)


def test_mirrored_section_tanks_follow_mirrored_liquid_motion(fleet):
    _,params,_=fleet
    records={r["id"]:r for r in params["tanks"]}
    port=next(r for key,r in records.items() if key.endswith("-P") and r["kind"]=="ballast")
    starboard=records[port["id"][:-2]+"-S"]
    p,s=TankGeometry.from_params(port),TankGeometry.from_params(starboard)
    assert p.capacity_m3==pytest.approx(s.capacity_m3,rel=1e-10)
    lp=p.liquid(p.capacity_m3*.4,heel_rad=.25,trim_rad=.005)
    ls=s.liquid(s.capacity_m3*.4,heel_rad=-.25,trim_rad=.005)
    assert lp.centroid_m==pytest.approx(np.asarray(ls.centroid_m)*[1,-1,1],abs=1e-7)
    assert lp.fsm_m4==pytest.approx(ls.fsm_m4,rel=1e-7,abs=1e-6)
