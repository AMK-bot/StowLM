"""Frozen checker integration and failure semantics."""
import math
import pytest
from stowage.checker.stability import check_stability
from tests.checker.test_stability_policy_fixtures import build_instance


def test_missing_hull_is_hard_failure():
    i=build_instance()
    i.vessel.hull=None
    r=check_stability(i,i.onboard,call_index=0)
    assert not r.ran
    assert r.violations[0].rule_id=='checker.missing_vessel_data'


def test_fixture_without_offsets_does_not_pass():
    i=build_instance(layover=True)
    r=check_stability(i,i.onboard,call_index=0)
    assert not r.ran
    assert r.violations


def test_open_hatch_invalidates_envelope():
    i=build_instance()
    i.onboard.covers_off={i.vessel.hatch_covers[0].id:'quay'}
    r=check_stability(i,i.onboard,call_index=0)
    assert not r.ran
    assert 'envelope' in r.error


@pytest.fixture
def feeder():
    from stowage.vessel.fleet import load_ship_params,load_vessel
    from stowage.checker.stability_geometry import StabilityGeometry
    p=load_ship_params('feeder_500'); g=StabilityGeometry(p)
    i=build_instance()
    # Retain the canonical builder's full loading manual and source pin.
    v=load_vessel('feeder_500')
    i.vessel=v; i.onboard=i.onboard.model_copy(update=dict(vessel_id=v.id,tank_masses_kg={}))
    # A balanced design displacement, modelled by the schema's fixed-load contract.
    s=g.full_submerged(g.design_draught_m,g.design_draught_m)
    m=s.displacement_kg-v.hull.lightship_mass_kg
    v.fixed_loads=[((s.displacement_kg*s.lcb_m-v.hull.lightship_mass_kg*v.hull.lightship_lcg_m)/m,
                    0.,3.,m)]
    return i


def test_real_feeder_full_report_and_loading_limits(feeder):
    r=check_stability(feeder,feeder.onboard,call_index=0)
    assert r.ran,r.error
    assert r.metrics['equilibrium_residual']<2e-6
    assert r.metrics['gm_m'] == pytest.approx(r.metrics['gm_solid_m']-r.metrics['fsc_m'])
    feeder.port.max_draught_m=.5
    feeder.rule_overrides['iscode.loading.trim']={'maximum':0.}
    feeder.rule_overrides['iscode.loading.gm']={'minimum_gm':100.}
    r=check_stability(feeder,feeder.onboard,call_index=0)
    ids={v.rule_id for v in r.violations}
    assert 'port.call.draft' in ids
    assert 'iscode.loading.gm' in ids


def test_fresh_water_requires_more_draught(feeder):
    sea=check_stability(feeder,feeder.onboard,call_index=0)
    feeder.port.water_density_kg_m3=1000
    fresh=check_stability(feeder,feeder.onboard,call_index=0)
    assert sea.ran and fresh.ran,(sea.error,fresh.error)
    assert fresh.metrics['draught_mean_m']>sea.metrics['draught_mean_m']


def test_draft_trim_and_list_limits(feeder):
    feeder.vessel.hull.max_draught_m=.5
    feeder.vessel.hull.min_draught_fwd_m=20
    feeder.rule_overrides['iscode.loading.trim']={'minimum':0.,'maximum':0.}
    feeder.vessel.hull.max_list_rad=0
    x,y,z,m=feeder.vessel.fixed_loads[0]
    feeder.vessel.fixed_loads[0]=(x+1,.01,z,m)
    report=check_stability(feeder,feeder.onboard,call_index=0)
    assert report.ran,report.error
    ids={v.rule_id for v in report.violations}
    assert {'iscode.loading.draft','iscode.loading.trim','iscode.loading.list'} <= ids


@pytest.mark.slow
def test_curve_angular_quadrature_converges_within_l5(feeder):
    import numpy as np
    from stowage.checker.stability import _context
    from stowage.checker.stability_loading import loading_condition
    geometry,params=_context(feeder.vessel)
    c=loading_condition(feeder,feeder.onboard,ship_params=params)
    curves=[]
    for step in (2.,1.):
        x=np.radians(np.r_[np.arange(0.,90.,step),89.99])
        y,_=geometry.free_trim_curve(x,c.mass_kg,c.lcg_m,c.kg_m,1025.)
        curves.append((x,y))
    for end in (30.,40.,89.99):
        areas=[]
        for x,y in curves:
            select=x<=math.radians(end)+1e-12
            areas.append(float(np.sum(np.diff(x[select])*(y[select][1:]+y[select][:-1])/2)))
        assert abs(areas[0]-areas[1])<=max(.0012,.05*abs(areas[1]))


@pytest.mark.slow
def test_fully_occupied_feeder_with_slack_liquids_converges(feeder):
    from stowage.model import Container, Placement
    from stowage.conventions import make_container_id
    feeder.vessel.fixed_loads=[]
    feeder.containers=[]
    feeder.onboard.placements=[]
    for serial,cell in enumerate(feeder.vessel.cells,1):
        cid=make_container_id('QZXU',serial)
        feeder.containers.append(Container(id=cid,iso_type='22G1',length_ft=20,height_m=2.591,
            tare_kg=2200,gross_mass_kg=8000,pol='THLCH',pod='THBKK'))
        feeder.onboard.placements.append(Placement(slot=cell.id,container_id=cid))
    feeder.onboard.tank_masses_kg={t.id:t.capacity_m3*t.content_density_kg_m3*.5 for t in feeder.vessel.tanks}
    report=check_stability(feeder,feeder.onboard,call_index=0)
    assert report.ran,report.error
    assert report.metrics['fsc_m']>0
    assert report.metrics['equilibrium_residual']<2e-6


def test_explicit_minimum_and_displacement_policies(feeder):
    feeder.rule_overrides.update({
        'iscode.loading.gm':{'minimum_gm':100.},
        'iscode.loading.displacement':{
            'minimum':feeder.vessel.hull.min_displacement_kg,
            'maximum':feeder.vessel.hull.min_displacement_kg},
        'iscode.a2_2.initial_gm':{'minimum':100.}})
    report=check_stability(feeder,feeder.onboard,call_index=0)
    assert report.ran,report.error
    ids={v.rule_id for v in report.violations}
    assert {'iscode.loading.gm','iscode.loading.displacement','iscode.a2_2.initial_gm'} <= ids


@pytest.mark.parametrize('true_peak', [24.6,25.4])
def test_direct_peak_refinement_evaluates_between_angular_knots(true_peak):
    import numpy as np
    from stowage.checker.stability import _refine_peak
    angles=np.radians([22.,24.,25.,26.,28.])
    target=math.radians(true_peak)
    calls=[]
    def exact_gz(phi):
        calls.append(phi)
        return .6-10*(phi-target)**2
    angle,lever=_refine_peak(angles,np.array([exact_gz(phi) for phi in angles]),exact_gz)
    assert math.degrees(angle)==pytest.approx(true_peak,abs=1e-5)
    assert lever==pytest.approx(.6,abs=1e-10)
    assert any(not np.any(np.isclose(phi,angles,atol=1e-8)) for phi in calls)
