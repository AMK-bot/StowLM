"""Closed-form tests of experimental IGES upper-shell extraction/plumbing."""
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result;spec.loader.exec_module(result)
    return result


iges=module("test_iges_upper_module","iges_upper19.py")
extend=module("test_extend19_module","extend19.py")


def plane_patch():
    return dict(label="synthetic",m1=1,m2=1,knots1=np.array([0.,0.,1.,1.]),
        knots2=np.array([0.,0.,1.,1.]),weights=np.ones((2,2)),
        controls=np.array([[[0.,8.,16.],[10.,8.,16.]],[[0.,10.,20.],[10.,10.,20.]]]),
        u0=0.,u1=1.,v0=0.,v1=1.)


def test_actual_bspline_evaluation_matches_analytic_plane():
    result=iges.evaluate_patch(plane_patch(),np.array([.25]),np.array([.5]))
    assert result[0,0].tolist()==pytest.approx([2.5,9.,18.])


def test_transverse_intersection_preserves_exact_flare_and_clips_at19():
    triangles=iges.upper_triangles([plane_patch()],subdivisions=4)
    points,diagnostic=iges.upper_section(triangles,4.,clip_top=19.)
    assert points[0].tolist()==pytest.approx([8.,16.])
    assert points[-1].tolist()==pytest.approx([9.5,19.])
    assert points[:,0]==pytest.approx(8.+(points[:,1]-16.)/2)
    assert diagnostic["gaps_z_m"]==[]
    assert diagnostic["max_branch_spread_m"]<1e-12


def test_upper_splice_preserves_lower_prism_and_exact_added_volume():
    from stowage.vessel.hydrostatics import HullGeometry
    half=np.array([[0.,0.],[8.,0.],[8.,16.]])
    polygon=np.vstack((half,half[::-1]*[-1.,1.]))
    native=HullGeometry.__new__(HullGeometry)
    native.__dict__.update(lpp_m=10.,beam_m=16.,depth_m=16.,max_z_m=16.,design_draught_m=10.,
        density_kg_m3=1025.,openings=[],x_m=np.array([5.]),integration_weights_m=np.array([10.]),
        sections=[[polygon]],buoyant_x_min_m=0.,buoyant_x_max_m=10.)
    native._finish_geometry()
    upper,_=iges.append_upper(polygon,iges.upper_triangles([plane_patch()],4),5.)
    transformed=extend.geometry_from_sections(native,[[upper]],depth_m=19.)
    assert transformed.submerged(10.,10.).volume_m3==pytest.approx(1600.,abs=1e-9)
    assert transformed.submerged(19.,19.).volume_m3==pytest.approx(3085.,abs=1e-9)
    assert native.submerged(19.,19.).volume_m3==pytest.approx(2560.,abs=1e-9)
    assert np.array_equal(native.sections[0][0],polygon)


def test_stability_geometry_clone_rebuilds_fast_arrays_and_empty_caches():
    import runpy
    from stowage.checker.stability_geometry import StabilityGeometry
    params=runpy.run_path(str(iges.ROOT/"experiments/hydrostatics/validate_kcs.py"))["reference_params"]()
    native=StabilityGeometry.from_params(params,nx=48,nz=64)
    expected=native.submerged(10.8,10.8,.5)
    cloned=extend.geometry_from_sections(native,native.sections)
    assert type(cloned) is StabilityGeometry and hasattr(cloned,"free_trim_curve")
    assert len(cloned._state_cache)==0 and len(cloned._full_state_cache)==0
    assert cloned._p is not native._p
    assert cloned._geometry_digest==native._geometry_digest
    actual=cloned.submerged(10.8,10.8,.5)
    assert [actual.volume_m3,*actual.centroid_m]==pytest.approx([expected.volume_m3,*expected.centroid_m],abs=1e-10)
