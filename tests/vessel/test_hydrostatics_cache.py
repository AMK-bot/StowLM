"""Exact cache reuse, caller isolation and the declared MCT reference KG."""
from dataclasses import fields
import math

import numpy as np
import pytest

from stowage.params import load_param_file
import stowage.vessel.hydrostatics as hydro


@pytest.fixture
def params():
    return load_param_file('fleet','feeder_500')


@pytest.fixture(autouse=True)
def clear_cache():
    hydro.clear_hydrostatic_caches()
    yield
    hydro.clear_hydrostatic_caches()


def geometry(params, **kwargs):
    return hydro.HullGeometry(params,nx=12,nz=16,**kwargs)


def assert_identical(a,b):
    assert a.t_aft_m==b.t_aft_m
    assert a.t_fwd_m==b.t_fwd_m
    assert a.heel_rad==b.heel_rad
    assert a.residual_norm==b.residual_norm
    for field in fields(a.submerged):
        actual,expected=getattr(a.submerged,field.name),getattr(b.submerged,field.name)
        if isinstance(actual,np.ndarray):
            np.testing.assert_array_equal(actual,expected)
        else:
            assert actual==expected


def solve_args(g):
    return (g.submerged(5.,5.).displacement_kg,g.lpp_m/2,0.,4.,1025.,None)


def test_solve_and_equilibrium_share_exact_cache_without_array_leaks(params):
    g=geometry(params)
    args=solve_args(g)
    uncached=g._solve_uncached(*args)
    first=g._solve(*args)
    assert_identical(first,uncached)
    first.submerged.sectional_areas_m2[:]=-123.
    first.submerged.x_m[:]=-456.
    hit=g.equilibrium(*args[:5])
    assert_identical(hit,uncached)
    assert hydro.hydrostatic_cache_info()['solve_hits']==1
    assert hydro.hydrostatic_cache_info()['solve_misses']==1
    assert g.x_m[0] == uncached.submerged.x_m[0]


def test_identical_instances_reuse_cache_but_distinct_classes_and_grids_do_not(params):
    g=geometry(params)
    args=solve_args(g)
    a=g._solve(*args)
    b=geometry(params)._solve(*args)
    assert_identical(a,b)
    assert hydro.hydrostatic_cache_info()['solve_hits']==1
    class Other(hydro.HullGeometry):
        pass
    Other(params,nx=12,nz=16)._solve(*args)
    hydro.HullGeometry(params,nx=16,nz=24)._solve(*args)
    assert hydro.hydrostatic_cache_info()['solve_misses']==3


def test_neighbouring_exact_inputs_never_alias_rounded_index(params):
    g=geometry(params)
    args=solve_args(g)
    g._solve(*args)
    varied=(*args[:2],1e-10,*args[3:])
    g._solve(*varied)
    assert hydro.hydrostatic_cache_info()['solve_misses']==2
    assert hydro.hydrostatic_cache_info()['solve_hits']==0


def test_cache_is_bounded_and_evicted_entries_recompute(params,monkeypatch):
    g=geometry(params)
    args=solve_args(g)
    expected=g._solve_uncached(*args)
    calls=[]
    def solve(*values):
        calls.append(values)
        return expected
    monkeypatch.setattr(g,'_solve_uncached',solve)
    monkeypatch.setattr(hydro,'_CACHE_LIMIT',2)
    for i in range(3):
        g._solve(args[0]+i,*args[1:])
    assert hydro.hydrostatic_cache_info()['solve_size']==2
    g._solve(*args)
    assert len(calls)==4


def test_kn_density_lcg_and_cross_instance_cache(params):
    g=geometry(params)
    mass=g.submerged(5.,5.).displacement_kg
    heel=math.radians(20)
    first=g.kn(heel,mass,g.lpp_m/2,density_kg_m3=1025.)
    same=geometry(params).kn(heel,mass,g.lpp_m/2,density_kg_m3=1025.)
    assert first==same
    assert hydro.hydrostatic_cache_info()['kn_hits']==1
    hydro.clear_hydrostatic_caches()
    assert g.kn(heel,mass,g.lpp_m/2,density_kg_m3=1025.)==first
    fresh=g.kn(heel,mass,g.lpp_m/2,density_kg_m3=1000.)
    assert fresh!=first


def test_hydrostatic_mct_includes_reference_kg_and_trimmed_station_weights(params):
    g=geometry(params)
    s=g.submerged(5.,5.)
    baseline=g.hydrostatic_row(5.)
    loaded=g.hydrostatic_row(5.,kg_m=4.)
    expected=s.displacement_kg*9.80665*(s.vcb_m+s.bm_l_m-4)/(100*g.lpp_m)
    assert loaded.mct_nm_per_cm==pytest.approx(expected,rel=1e-14)
    assert baseline.mct_nm_per_cm-loaded.mct_nm_per_cm==pytest.approx(s.displacement_kg*9.80665*4/(100*g.lpp_m))
    assert g.hydrostatic_row(5.,vcg_m=4.)==loaded
    trimmed=g.trimmed_sectional_areas(5.4,4.6,.1,1000.)
    assert np.dot(trimmed.sectional_areas_m2,trimmed.integration_weights_m)==pytest.approx(trimmed.volume_m3)
    assert trimmed.displacement_kg==pytest.approx(trimmed.volume_m3*1000.)


@pytest.mark.parametrize('ta,tf,heel',[(5.,5.,0.),(5.4,4.6,.2),(1.2,1.8,.7),(-1.,2.,1.)])
def test_batched_solver_geometry_matches_full_polygon_integrals(params,ta,tf,heel):
    g=geometry(params)
    exact=g.submerged(ta,tf,heel,1025.)
    fast=g._solver_submerged(ta,tf,heel,1025.)
    assert fast.volume_m3==pytest.approx(exact.volume_m3,rel=2e-14,abs=1e-10)
    assert fast.centroid_m==pytest.approx(exact.centroid_m,rel=2e-14,abs=1e-12)


def test_batched_duplicate_contour_corners_retain_the_real_corner(params):
    g=geometry(params)
    # Offset source interpolation can contain consecutive duplicate vertices.
    g.sections=[[np.repeat(poly,2,axis=0) for poly in section] for section in g.sections]
    g._finish_geometry()
    exact=g.submerged(4.8,4.6,.5)
    fast=g._solver_submerged(4.8,4.6,.5)
    assert fast.volume_m3==pytest.approx(exact.volume_m3,rel=2e-14)
    assert fast.centroid_m==pytest.approx(exact.centroid_m,rel=2e-14,abs=1e-12)


def test_kn_continuation_matches_individual_roots_and_isolates_cached_arrays(params):
    g=geometry(params)
    row=g.hydrostatic_row(5.)
    angles=np.radians([0.,2.,10.,30.,60.])
    first=g.kn_curve(angles,row.displacement_kg,row.lcb_m,1000.)
    scalar=np.array([g.kn(float(a),row.displacement_kg,row.lcb_m,1000.) for a in angles])
    np.testing.assert_allclose(first,scalar,rtol=0,atol=1e-7)
    expected=first.copy()
    first[:]=-999.
    hit=g.kn_curve(angles,row.displacement_kg,row.lcb_m,1000.)
    np.testing.assert_array_equal(hit,expected)
    hydro.clear_hydrostatic_caches()
    np.testing.assert_array_equal(g.kn_curve(angles,row.displacement_kg,row.lcb_m,1000.),expected)
