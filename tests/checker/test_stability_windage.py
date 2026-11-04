"""Background cargo shares the IS A2.3 lateral windage projection."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability
from tests.checker.test_stability_policy_fixtures import build_instance
from stowage.model.ship import Placement
from stowage.model.vessel import FixedLoad


def background(*, x0=0., x1=10., z0=4., z1=10., y=0., kind='cargo', mass=100.):
    return FixedLoad(x_m=(x0+x1)/2, y_m=y, z_m=(z0+z1)/2,
                     mass_kg=mass, kind=kind,
                     silhouette=dict(x_min_m=x0,x_max_m=x1,y_min_m=-1.,
                                     y_max_m=1.,z_min_m=z0,z_max_m=z1))


def setup_windage(loads, rectangles=()):
    instance = build_instance()
    instance.vessel.hull.depth_m = 4.  # Rectangle cases use a four-metre deck.
    instance.vessel.fixed_loads = loads
    ship = instance.onboard.model_copy(update={'placements': []})
    params = {'windage': {'projected_rectangles': list(rectangles)}}
    return instance, ship, params


@pytest.mark.parametrize('kind', ['cargo', 'other'])
def test_background_envelope_is_clipped_by_waterline_and_sets_lever(kind):
    instance, ship, params = setup_windage([background(kind=kind)])
    area, lever = stability._windage(instance,ship,params,6.,6.)
    assert area == pytest.approx(40.)
    # Above-water centroid z=8; underwater lateral centre z=T/2=3.
    assert lever == pytest.approx(5.)


def test_background_and_vessel_rectangles_use_union_including_overlaps():
    load = background()
    instance, ship, params = setup_windage([load, load.model_copy(deep=True)], [
        dict(x_min_m=5.,x_max_m=15.,z_min_m=8.,z_max_m=12.)])
    area, lever = stability._windage(instance,ship,params,6.,6.)
    # First area40 at z8; second area40 at z10; overlap10 at z9.
    assert area == pytest.approx(70.)
    assert lever == pytest.approx((40*8+40*10-10*9)/70-3)


def test_background_matches_planned_box_silhouette():
    instance = build_instance()
    instance.vessel.fixed_loads = []
    box = instance.containers[1]
    stack = next(s for s in instance.vessel.stacks if s.on_deck)
    cell = next(c for c in instance.vessel.cells if c.id == stack.cell_ids[0])
    placement = Placement(container_id=box.id,slot=cell.id)
    ship = instance.onboard.model_copy(update={'placements':[placement]})
    params = {'windage':{'projected_rectangles':[]}}
    expected = stability._windage(instance,ship,params,6.,7.)
    instance.vessel.fixed_loads = [background(
        x0=cell.lcg_m-box.length_ft*.3048/2,
        x1=cell.lcg_m+box.length_ft*.3048/2,
        z0=stack.base_z_m,z1=stack.base_z_m+box.height_m,y=cell.tcg_m)]
    ship.placements = []
    assert stability._windage(instance,ship,params,6.,7.) == pytest.approx(expected)


def test_background_waterline_crossing_is_integrated_exactly():
    instance, ship, params = setup_windage([background(z0=6.,z1=10.)])
    # Waterline rises from5 to7 over the ten-metre block, crossing z=6 at x=5.
    tf = 5.+.2*instance.vessel.hull.lpp_m
    area, lever = stability._windage(instance,ship,params,5.,tf)
    assert area == pytest.approx(37.5)
    # Integral z moment: full first half160; clipped second half181.666... .
    moment = 160.+(100*5-(7**3-6**3)/(.6))/2
    assert lever == pytest.approx(moment/area-(5.+tf)/4)


@pytest.mark.parametrize('z', [4.,20.])
def test_missing_on_deck_slice_cargo_silhouette_fails_closed(z):
    load = background().model_copy(update={'silhouette':None,'z_m':z})
    instance, ship, params = setup_windage([load])
    with pytest.raises(ValueError,match='background cargo.*silhouette'):
        stability._windage(instance,ship,params,5.,5.)


@pytest.mark.parametrize('envelope', [True,False])
def test_hold_background_has_no_windage_or_missing_geometry_failure(envelope):
    load = background(z0=1.,z1=3.)
    if not envelope:
        load.silhouette = None
    instance, ship, params = setup_windage([load], [
        dict(x_min_m=0.,x_max_m=10.,z_min_m=6.,z_max_m=10.)])
    assert not stability._missing_background_silhouette(instance.vessel, load)
    assert stability._windage(instance,ship,params,1.,1.) == pytest.approx((40.,7.5))


def test_envelope_crossing_weather_deck_exposes_only_its_upper_part():
    instance, ship, params = setup_windage([background(z0=2.,z1=8.)])
    assert stability._windage(instance,ship,params,1.,1.) == pytest.approx((40.,5.5))


def test_local_hatch_stack_top_overrides_moulded_hull_depth():
    instance = build_instance()
    stack = next(s for s in instance.vessel.stacks if s.on_deck)
    cell = next(c for c in instance.vessel.cells if c.id == stack.cell_ids[0])
    load = background(x0=cell.lcg_m-3.,x1=cell.lcg_m+3.,
                      z0=stack.base_z_m,z1=stack.base_z_m+1.,y=cell.tcg_m)
    load.silhouette = None
    assert stack.base_z_m < instance.vessel.hull.depth_m
    assert stability._background_deck_z(instance.vessel,load) == stack.base_z_m
    assert stability._missing_background_silhouette(instance.vessel,load)
    load.z_m = stack.base_z_m-.1
    assert not stability._missing_background_silhouette(instance.vessel,load)


def test_below_water_envelope_has_no_windage_without_being_missing_data():
    instance, ship, params = setup_windage([background(z0=1.,z1=3.)], [
        dict(x_min_m=0.,x_max_m=10.,z_min_m=6.,z_max_m=10.)])
    assert stability._windage(instance,ship,params,5.,5.) == pytest.approx((40.,5.5))


def test_missing_silhouette_tags_complete_check_as_missing_vessel_data(monkeypatch):
    instance, ship, params = setup_windage([
        background().model_copy(update={'silhouette':None})])
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD','2')
    floating = SimpleNamespace(t_aft_m=5.,t_fwd_m=5.,trim_m=0.,heel_rad=0.,
                               mean_draught_m=5.,residual_norm=0.)
    geometry = SimpleNamespace(
        equilibrium=lambda *a:floating,
        full_submerged=lambda *a:SimpleNamespace(centroid_m=(60.,0.,2.5),bm_t_m=6.),
        waterplane_normal=lambda *a:np.array([0.,0.,1.]))
    condition = SimpleNamespace(errors=(),mass_kg=1e7,lcg_m=60.,tcg_m=0.,
                                kg_m=5.,fsc_m=0.)
    monkeypatch.setattr(stability,'_context',lambda v:(geometry,params))
    monkeypatch.setattr(stability,'loading_condition',lambda *a,**k:condition)
    monkeypatch.setattr(stability,'_deck_edge_angle',lambda *a,**k:.3)
    monkeypatch.setattr(stability,'_refine_peak',lambda *a:(None,None))
    from stowage.checker import stability_curve
    curve = SimpleNamespace(angles=np.radians([0.,30.,40.]),gz=np.array([0.,.4,.3]),
        negative_gz=np.array([0.,.4,.3]),drafts=np.full((3,2),5.),
        flooding_angle=math.radians(40.),at_angle=lambda a:(.3,(5.,5.)))
    monkeypatch.setattr(stability_curve,'build_stability_curve',lambda *a,**k:curve)
    report = stability._check_stability(instance,ship,call_index=0,_force_direct=True)
    assert not report.ran
    assert 'background cargo' in report.error
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
