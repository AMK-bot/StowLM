"""D64 equilibrium-only primitives; replay/HEEL/changeover belong to full 4.1."""
from dataclasses import replace
import math
from statistics import median
from time import perf_counter
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability
from stowage.checker.stability_loading import LoadingCondition
from stowage.vessel.hydrostatics import FloatingCondition
from tests.checker.test_stability_policy_fixtures import build_instance

RULE = 'port.stability.cargo_operations'


def _nested_heave_immersion_angles(g,params,covers,mass,rho,trim):
    """Independent reference: solve heave at every angular clearance query."""
    from scipy.optimize import brentq
    deck,vertices = [],[]
    for x,section in zip(g.x_m,g.sections):
        vertices.extend((x,y,z) for polygon in section for y,z in polygon)
        polygon = section[0]
        for (y,z),(yn,zn) in zip(polygon,np.roll(polygon,-1,axis=0)):
            if z == g.depth_m:
                deck.append((x,y,z))
            elif min(z,zn) < g.depth_m < max(z,zn):
                deck.append((x,y+(yn-y)*(g.depth_m-z)/(zn-z),g.depth_m))
    coamings = {c['id']:c for c in params['envelope']['hatch_coamings']}
    lips = [(x,y,coamings[cid]['z_max_m']) for cid in covers
        for x in (coamings[cid]['x_min_m'],coamings[cid]['x_max_m'])
        for y in (coamings[cid]['y_min_m'],coamings[cid]['y_max_m'])]
    vertices,points = np.asarray(vertices),np.asarray([*deck,*lips])
    cache = {}
    def clearance(angle):
        if angle not in cache:
            tangent = math.tan(angle)
            intercepts = vertices[:,2]+trim*vertices[:,0]/g.lpp_m-trim/2-vertices[:,1]*tangent
            mean = brentq(lambda mean:g.submerged(mean+trim/2,mean-trim/2,angle,rho).displacement_kg-mass,
                float(intercepts.min())-1.,float(intercepts.max())+1.,xtol=1e-10)
            freeboard = points[:,2]-mean-trim/2+trim*points[:,0]/g.lpp_m-points[:,1]*tangent
            cache[angle] = (float(freeboard[:len(deck)].min()),float(freeboard.min()))
        return cache[angle]
    result = [math.inf,math.inf]
    for sign in (-1.,1.):
        previous = 0.
        found = [value <= 0 for value in clearance(0.)]
        for k,wet in enumerate(found):
            if wet:
                result[k] = 0.
        for angle in np.radians(np.r_[np.arange(2.,90.,2.),89.9]):
            values = clearance(sign*float(angle))
            for k in (0,1):
                if not found[k] and values[k] <= 0:
                    result[k] = min(result[k],brentq(lambda phi:clearance(sign*phi)[k],
                        previous,float(angle),xtol=1e-10))
                    found[k] = True
            if all(found):
                break
            previous = float(angle)
        assert all(found),'independent heave oracle did not bracket immersion'
    return tuple(result)


@pytest.fixture
def isolated(monkeypatch):
    """Declared synthetic equilibria isolate each hard boundary from solvers."""
    from stowage.checker import stability_tables
    i = build_instance()
    i.vessel.hull.max_list_rad = math.radians(2.)
    i.vessel.hull.max_draught_m = 5.
    i.port.max_draught_m = 4.
    c = LoadingCondition(1e6, 50., 0., 3., .1, 1e5)
    f = FloatingCondition(2., 2., 0., None, 0.)
    g = SimpleNamespace(floating=f, gm=.4,immersion_angles=(math.radians(10.),math.radians(10.)))
    g.equilibrium = lambda *args:g.floating
    g.free_trim_curve = lambda *args,**kwargs:(np.array([0.]),np.array([[g.floating.t_aft_m,g.floating.t_fwd_m]]))
    g.full_submerged = lambda *args:SimpleNamespace(centroid_m=[c.lcg_m,0.,c.kg_m],bm_t_m=g.gm+c.fsc_m)
    g.waterplane_normal = lambda *args:np.array([0.,0.,1.])
    cover = i.vessel.hatch_covers[0].id
    params = {'envelope':{'hatch_coamings':[dict(id=cover,x_min_m=0.,x_max_m=100.,y_min_m=-5.,y_max_m=5.,z_max_m=5.)]}}
    monkeypatch.setattr(stability,'_canonical_fleet_policy',lambda *args:None)
    monkeypatch.setattr(stability,'_context',lambda *args:(g,params))
    monkeypatch.setattr(stability,'loading_condition',lambda *args,**kwargs:c)
    monkeypatch.setattr(stability_tables,'cached_geometry',lambda *args:None)
    monkeypatch.setattr(stability,'_in_port_immersion_angles',lambda *args:g.immersion_angles)
    monkeypatch.setenv('STOWAGE_STABILITY_LIQUID_DIRECT','1')
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT',raising=False)
    return i,g,params


def port_report(i):
    return stability.check_stability(i,i.onboard,call_index=0,in_port=True)


def test_t1_open_hatch_is_evaluated_and_reports_p4(isolated):
    i,g,_ = isolated
    i.onboard.covers_off = {i.vessel.hatch_covers[0].id:'quay'}
    r = port_report(i)
    assert r.ran and not r.violations,r.error
    assert r.margins['open_hatch_immersion_angle_rad'] > 0
    assert r.metrics['angle_open_hatch_immersion_rad'] == pytest.approx(math.radians(10.))
    assert not stability.check_stability(i,i.onboard,call_index=0).ran


def test_t2_seagoing_criteria_and_lower_bounds_are_not_evaluated(isolated,monkeypatch):
    from stowage.checker import stability_curve
    i,g,_ = isolated
    i.vessel.hull.min_draught_fwd_m = 3.
    i.vessel.hull.min_gm_m = 1.
    i.rule_overrides['iscode.loading.gm'] = {'minimum_gm':10.}
    g.gm = .2
    g.floating = replace(g.floating,t_aft_m=2.75,t_fwd_m=1.25)
    def forbidden(*args,**kwargs):
        pytest.fail('in-port check evaluated a seagoing criterion')
    for name in ('_loading_policy','_minimum_loading_gm','_weather_keel_area','_windage'):
        monkeypatch.setattr(stability,name,forbidden)
    monkeypatch.setattr(stability_curve,'build_stability_curve',forbidden)
    r = port_report(i)
    assert r.ran and not r.violations,r.error
    assert r.metrics['gm_m'] == pytest.approx(.2)
    assert r.metrics['draught_fwd_m'] < i.vessel.hull.min_draught_fwd_m
    assert not any('weather' in key or 'area' in key or 'gz' in key for key in r.margins)
    assert 'draught_fwd_min_m' not in r.margins
    assert not any('displacement' in key for key in r.margins)
    assert 'open_hatch_immersion_angle_rad' not in r.margins


@pytest.mark.parametrize('gm,passes',[(.15,True),(.149999,False)])
def test_p2_fluid_gm_boundary(isolated,gm,passes):
    i,g,_ = isolated
    g.gm = gm
    r = port_report(i)
    assert r.ran
    assert (not r.violations) == passes
    assert r.margins['gm_m'] == pytest.approx(gm-.15)
    assert all(v.rule_id == RULE and v.severity == 'hard' for v in r.violations)


@pytest.mark.parametrize('sign',[-1.,1.])
def test_t3_midbay_list_fails_even_when_balanced_endpoint_passes(isolated,sign):
    # Primitive states of a one-sided prefix and a balanced endpoint. Every-lift
    # replay and an actual HEEL-P/S transfer are integration tests for full 4.1.
    i,g,_ = isolated
    g.floating = replace(g.floating,heel_rad=sign*math.radians(3.))
    mid = port_report(i)
    assert mid.ran and mid.margins['list_rad'] < 0
    assert {v.rule_id for v in mid.violations} == {RULE}
    g.floating = replace(g.floating,heel_rad=sign*i.vessel.hull.max_list_rad)
    assert not port_report(i).violations
    g.floating = replace(g.floating,heel_rad=0.)
    assert not port_report(i).violations


@pytest.mark.parametrize('sign',[-1.,1.])
def test_p6_port_trim_boundary(isolated,sign):
    i,g,_ = isolated
    limit = i.vessel.hull.lpp_m*math.tan(math.radians(1.))
    g.floating = replace(g.floating,t_aft_m=2.+sign*limit/2,t_fwd_m=2.-sign*limit/2)
    assert not port_report(i).violations
    g.floating = replace(g.floating,t_aft_m=g.floating.t_aft_m+sign*1e-5)
    assert port_report(i).margins['trim_m'] < 0


@pytest.mark.parametrize('endpoint',['aft','fwd'])
def test_p5_berth_and_loadline_upper_boundaries(isolated,endpoint):
    i,g,_ = isolated
    i.port.max_draught_m = 2.
    assert not port_report(i).violations
    g.floating = replace(g.floating,**{f't_{endpoint}_m':2.00001})
    assert port_report(i).margins['port_draught_m'] < 0
    i.port.max_draught_m = None
    i.vessel.hull.max_draught_m = 2.
    r = port_report(i)
    assert r.ran and r.margins[f'draught_{endpoint}_m'] < 0


@pytest.mark.parametrize('delta,passes',[(0.,False),(-1e-5,False),(1e-5,True)])
def test_p4_deck_edge_requires_strict_positive_margin(isolated,monkeypatch,delta,passes):
    i,g,_ = isolated
    i.onboard.covers_off = {i.vessel.hatch_covers[0].id:'quay'}
    angle = i.vessel.hull.max_list_rad+delta
    monkeypatch.setattr(stability,'_in_port_immersion_angles',lambda *a,**k:(angle,angle))
    r = port_report(i)
    assert r.ran
    assert (not r.violations) == passes
    assert r.margins['open_hatch_immersion_angle_rad'] == pytest.approx(delta)


@pytest.mark.parametrize('side',[-1.,1.])
def test_p4_open_coaming_corners_use_equilibrium_trim_and_both_sides(side):
    # An independent wall-sided prism has constant heave before deck immersion.
    # Its aft, outer open lip controls; mirroring it must preserve the angle.
    from tests.checker.test_stability_physics import box_geometry
    g = box_geometry()
    mass = g.submerged(5.5,4.5,0.,1025.).displacement_kg
    p = {'envelope':{'hatch_coamings':[
        dict(id='OPEN',x_min_m=0.,x_max_m=1.,y_min_m=min(side,3*side),y_max_m=max(side,3*side),z_max_m=5.6),
        dict(id='CLOSED',x_min_m=0.,x_max_m=1.,y_min_m=-8.,y_max_m=8.,z_max_m=5.5)]}}
    deck,opening = stability._in_port_immersion_angles(g,p,{'OPEN':'quay'},mass,1025.,1.)
    assert opening == pytest.approx(math.atan2(.1,3.),abs=1e-9)
    assert deck > opening
    assert (deck,opening) == pytest.approx(
        _nested_heave_immersion_angles(g,p,{'OPEN':'quay'},mass,1025.,1.),abs=1e-9)


def test_p4_missing_geometry_is_data_gap(isolated):
    i,g,p = isolated
    i.onboard.covers_off = {i.vessel.hatch_covers[0].id:'quay'}
    p['envelope']['hatch_coamings'] = []
    r = port_report(i)
    assert not r.ran
    assert {v.rule_id for v in r.violations} == {'checker.missing_vessel_data'}


@pytest.mark.parametrize('failure',['solver_value_error','nonfinite_state','free_trim_error','nonfinite_free_trim'])
def test_p1_numerical_equilibrium_error_is_internal_failure(isolated,failure):
    i,g,_ = isolated
    def unavailable(*args):
        raise ValueError('numerical equilibrium failure')
    if failure == 'solver_value_error':
        g.equilibrium = unavailable
    elif failure == 'nonfinite_state':
        g.floating = replace(g.floating,t_aft_m=float('nan'))
    elif failure == 'free_trim_error':
        g.free_trim_curve = lambda *a,**k:unavailable()
    else:
        g.free_trim_curve = lambda *a,**k:(np.array([0.]),np.array([[float('nan'),2.]]))
    r = port_report(i)
    assert not r.ran
    assert {v.rule_id for v in r.violations} == {'checker.internal_error'}
    assert all(v.severity == 'hard' for v in r.violations)


def test_p4_tumblehome_resolves_heave_at_fixed_trim_before_deck_immersion():
    """Physical review regression: fixed intercepts falsely pass at 2 degrees."""
    from scipy.optimize import brentq
    from stowage.checker.stability_geometry import StabilityGeometry
    p = {'hull':dict(lpp_m=80.,beam_m=10.,depth_m=5.,design_draught_m=2.,water_density_kg_m3=1025.),
        'hull_form':dict(family='stowlm_separable_rounded_bilge_v1',aft_run_fraction=.1,
                        fore_run_fraction=.1,bilge_radius_m=0.,deck_flare_fraction=-.5),
        'envelope':dict(hatch_coamings=[],openings=[])}
    g = StabilityGeometry(p,nx=8,nz=16)
    limit = math.radians(2.)
    draft = 5.-2.5*math.tan(limit)-.0002
    state = g.full_submerged(draft,draft,0.,1025.)
    c = LoadingCondition(state.displacement_kg,state.lcb_m,0.,1.,0.,0.)
    f = g.equilibrium(c.mass_kg,c.lcg_m,0.,c.kg_m,1025.)
    i = SimpleNamespace(vessel=SimpleNamespace(hull=SimpleNamespace(lpp_m=80.,max_list_rad=limit,
        max_draught_m=5.,lightship_mass_kg=1e6)),
        port=SimpleNamespace(water_density_kg_m3=1025.,max_draught_m=5.))
    p['envelope']['hatch_coamings'] = [dict(id='H',x_min_m=30.,x_max_m=50.,
        y_min_m=-1.,y_max_m=1.,z_max_m=6.)]
    # Independent volume-only oracle at fixed trim, with actual deck vertices.
    deck = np.array([(x,y) for x,section in zip(g.x_m,g.sections)
        for y,z in section[0] if abs(z-g.depth_m)<1e-12])
    def clearance(angle):
        mean = brentq(lambda mean:g.submerged(mean+f.trim_m/2,mean-f.trim_m/2,
            angle,1025.).displacement_kg-c.mass_kg,0.,10.)
        return float(np.min(g.depth_m-(mean+f.trim_m/2)+f.trim_m*deck[:,0]/g.lpp_m
            -deck[:,1]*math.tan(angle)))
    expected = min(abs(brentq(clearance,0.,sign*math.radians(3.))) for sign in (-1.,1.))
    assert math.degrees(expected) == pytest.approx(1.976957029,abs=1e-6)
    assert clearance(limit)<-.001
    metrics,margins,broken = stability._in_port_criteria(i,SimpleNamespace(covers_off={'H':'quay'}),g,p,c,f,1)
    assert broken
    assert margins['open_hatch_immersion_angle_rad'] < 0
    assert metrics['angle_open_hatch_immersion_rad'] == pytest.approx(expected,abs=1e-8)


@pytest.mark.parametrize('name',['feeder_500','feeder_800','feeder_1700'])
def test_p4_fleet_contact_roots_match_nested_heave_reference(name):
    from stowage.vessel.fleet import load_ship_params
    from stowage.checker.stability_geometry import StabilityGeometry
    params = load_ship_params(name)
    g = StabilityGeometry(params)
    rho,trim = 1025.,.2
    mean = .9*g.design_draught_m
    mass = g.submerged(mean+trim/2,mean-trim/2,0.,rho).displacement_kg
    covers = {c['id']:'quay' for c in params['envelope']['hatch_coamings']}
    actual = stability._in_port_immersion_angles(g,params,covers,mass,rho,trim)
    reference = _nested_heave_immersion_angles(g,params,covers,mass,rho,trim)
    assert actual == pytest.approx(reference,abs=1e-9)


def test_port_near_boundary_rechecks_exact_geometry(isolated,monkeypatch):
    from stowage.checker import stability_tables
    i,g,_ = isolated
    g.gm = .155
    wrapper = SimpleNamespace(**g.__dict__,direct=g)
    monkeypatch.setattr(stability_tables,'cached_geometry',lambda *args:wrapper)
    r = port_report(i)
    assert r.ran and not r.violations
    assert r.metrics['geometry_table_used'] == 0.
    assert r.metrics['geometry_table_limit_fallback'] == 1.


def test_port_near_boundary_rechecks_liquid_tables_without_hull_table(isolated,monkeypatch):
    i,g,_ = isolated
    g.gm = .155
    condition = stability.loading_condition(i,i.onboard)
    condition = replace(condition,liquid_tables={'qualified-liquid':object()})
    monkeypatch.setattr(stability,'loading_condition',lambda *args,**kwargs:condition)
    r = port_report(i)
    assert r.ran and not r.violations
    assert r.metrics['geometry_table_used'] == 0.
    assert r.metrics['geometry_table_limit_fallback'] == 1.


@pytest.fixture(scope='module',params=['feeder_500','feeder_800','feeder_1700'])
def empty_ship(request):
    from stowage.vessel.fleet import load_vessel
    i = build_instance()
    i.vessel = load_vessel(request.param)
    i.vessel.fixed_loads = []
    i.containers = []
    i.onboard = i.onboard.model_copy(update={'vessel_id':i.vessel.id,'placements':[],
        'tank_masses_kg':{},'covers_off':{}})
    i.port.max_draught_m = None
    return i


def test_t4_empty_ship_primitive_diagnostics_each_ship(empty_ship):
    # Function-local T4 evidence. Full-rung lift replay, mask domain, and HEEL
    # transfer are deliberately left to their integration owners in wave 4.1.
    r = port_report(empty_ship)
    assert r.ran,r.error
    assert r.metrics['equilibrium_residual'] < 2e-6
    assert r.metrics['draught_fwd_m'] < empty_ship.vessel.hull.min_draught_fwd_m
    assert 'draught_fwd_min_m' not in r.margins
    assert all(math.isfinite(r.metrics[k]) for k in ('list_rad','gm_m','trim_m',
        'draught_aft_m','draught_fwd_m','displacement_kg'))


def test_explicit_switch_off_matches_omitted_argument(empty_ship):
    default = stability.check_stability(empty_ship,empty_ship.onboard,call_index=0)
    explicit = stability.check_stability(empty_ship,empty_ship.onboard,call_index=0,in_port=False)
    assert default.ran and explicit.ran,(default.error,explicit.error)
    assert default.model_dump() == explicit.model_dump()


@pytest.mark.slow
def test_t10_warm_in_port_api_cost_each_ship(empty_ship,record_property):
    """Opt-in reproducible warm cost including slack-liquid equilibrium and P4."""
    i = empty_ship.model_copy(deep=True)
    i.onboard.tank_masses_kg = {t.id:.5*t.capacity_m3*t.content_density_kg_m3 for t in i.vessel.tanks}
    i.onboard.covers_off = {c.id:'quay' for c in i.vessel.hatch_covers}
    # APPROXIMATION: synthetic balanced background at 90% design draught is a
    # timing fixture, not an approved full-load condition or replay evidence.
    g,p = stability._context(i.vessel)
    rho = i.port.water_density_kg_m3
    target = g.full_submerged(.9*g.design_draught_m,.9*g.design_draught_m,density=rho)
    from stowage.checker.stability_loading import loading_condition
    base = loading_condition(i,i.onboard,geometry=g,ship_params=p,precomputed_soundings=True)
    mass = target.displacement_kg-base.mass_kg
    assert mass > 0
    x = (target.displacement_kg*target.lcb_m-base.mass_kg*base.lcg_m)/mass
    i.vessel.fixed_loads = [(x,0.,3.,mass)]
    assert port_report(i).ran
    pilot = []
    for n in range(5):
        i.vessel.fixed_loads = [(x-(n+1)*.0001,0.,3.,mass)]
        started = perf_counter()
        result = port_report(i)
        pilot.append(perf_counter()-started)
        assert result.ran,result.error
    pilot_seconds = median(pilot)
    record_property('pilot_seconds_per_lift',pilot_seconds)
    assert pilot_seconds*1600 <= 600., (
        f'{i.vessel.id}: warm API pilot projects {pilot_seconds*1600:.1f}s per '
        '1600 lifts; report timing/options before the 414-state benchmark')
    samples = []
    # T10's 414-state workload is opt-in; these are distinct exact physical
    # moments for timing, not a substitute for every-lift plan replay in T4.
    for n in range(414):
        i.vessel.fixed_loads = [(x+n*.0001,0.,3.,mass)]
        started = perf_counter()
        result = port_report(i)
        samples.append(perf_counter()-started)
        assert result.ran,result.error
    elapsed = median(samples)
    record_property('ship',i.vessel.id)
    record_property('warm_seconds_per_lift',elapsed)
    record_property('estimated_1600_lifts_seconds',1600*elapsed)
    record_property('states_evaluated',len(samples))
    print(f'T10 {i.vessel.id}: {len(samples)} states, {elapsed:.6f} s/lift; '
          f'1600 lifts {1600*elapsed:.2f} s')
