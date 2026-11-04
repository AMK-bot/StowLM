"""Independent checks of the visually transcribed S11A wave equations."""
import math
from types import SimpleNamespace

import numpy as np

import pytest

from stowage.checker.strength_limits import design_summary, permissible_stations, wave_loads, wave_parameter, minimum_section_modulus_m3, assumed_shear_area_m2, _bow_flare_coefficient, _partial_gauss_integral, s7_implied_design_swbm_nm, _assumed_scantlings, _double_bottom_height_m
from stowage.params import load_param_file
from stowage.vessel.hydrostatics import HullGeometry


KW = dict(length_m=120., beam_m=20., draught_m=7., block_coefficient=.8,
          waterplane_coefficient=.85, bow_flare_coefficient=.1)


@pytest.mark.parametrize('name', ['feeder_500','feeder_800','feeder_1700'])
def test_harbour_envelopes_use_separate_wave_signs_and_preserve_seagoing_shear(name):
    from copy import deepcopy

    params = load_param_file('fleet',name)
    original_params = deepcopy(params)
    geometry = HullGeometry.from_params(params)
    seagoing = permissible_stations(params,geometry)
    original_stations = [station.model_dump() for station in seagoing]
    assert original_stations == [station.model_dump() for station in
                                 permissible_stations(params,geometry,in_port=False)]
    harbour = permissible_stations(params,geometry,in_port=True)
    summary = design_summary(params,geometry)
    kw = {key: summary[key] for key in KW}
    assert len(harbour) == len(seagoing)
    for sea,port in zip(seagoing,harbour,strict=True):
        wave = wave_loads(min(geometry.lpp_m,max(0.,sea.x_m)),**kw)
        assert port.x_m == sea.x_m
        assert port.max_bending_nm == sea.max_bending_nm+.5*abs(wave['hogging_nm'])
        assert port.min_bending_nm == -(-sea.min_bending_nm+.5*abs(wave['sagging_nm']))
        # P8 retains precisely the seagoing values, including overhangs.
        assert port.max_shear_n == sea.max_shear_n
        assert port.min_shear_n == sea.min_shear_n
    assert [station.model_dump() for station in seagoing] == original_stations
    assert params == original_params
    # Opting into harbour values cannot change a subsequent legacy call.
    assert [station.model_dump() for station in permissible_stations(params,geometry)] == original_stations


def test_wave_parameter_both_branches_and_separate_references():
    # At its reference length C=1 exactly, for each type.
    for shear, constant in ((False,315),(True,330)):
        ref = constant*.85**-1.3
        assert wave_parameter(ref,.85,shear=shear) == 1
        assert wave_parameter(ref/4,.85,shear=shear) == pytest.approx(1-1.5*.5**2.2)
        assert wave_parameter(ref*4,.85,shear=shear) == pytest.approx(.55)


def test_wave_midship_hand_formula_and_distribution_corners():
    L,B,T,cb,cw = 120,20,7,.8,.85
    c = 1-1.5*(1-math.sqrt(L/(315*cw**-1.3)))**2.2
    nh = .3*cb/cw*math.sqrt(T)
    ns = 4.5*1.02/(cw*math.sqrt(cb)*L**.3)
    base = 1.5*.85*L**3*c*cw*(B/L)**.8*1000
    mid = wave_loads(60,**KW)
    assert mid['hogging_nm'] == pytest.approx(base*nh)
    assert mid['sagging_nm'] == pytest.approx(-base*ns)
    assert wave_loads(.1*L,**KW)['hogging_nm'] == pytest.approx(.15*base*nh)
    assert wave_loads(.8*L,**KW)['hogging_nm'] == pytest.approx(.25*base*nh)
    assert wave_loads(.35*L,**KW)['sagging_nm'] == pytest.approx(-base*ns)
    assert wave_loads(.6*L,**KW)['sagging_nm'] == pytest.approx(-base*ns)


def test_wave_shear_fig7_end_and_sign_conversion():
    L,B,cb,cw = 120,20,.8,.85
    c = 1-1.5*(1-math.sqrt(L/(330*cw**-1.3)))**2.2
    base = .85*L**2*c*cw*(B/L)**.8*1000
    ns = max(1,4.5*1.02/(cw*math.sqrt(cb)*L**.3))
    nh = .3*cb/cw*math.sqrt(7)
    assert wave_loads(.15*L,**KW)['min_shear_n'] == pytest.approx(-5.2*base*(.3+.7*nh))
    assert wave_loads(.65*L,**KW)['min_shear_n'] == pytest.approx(-5.7*base*(.25+.75*ns))
    assert wave_loads(.4*L,**KW)['max_shear_n'] == pytest.approx(4*base)
    # Fig.7: lower curve starts at .25 F_sag_aft at AE; zero at FE.
    aft = 5.2*base*(.3+.7*ns)
    assert wave_loads(0,**KW)['max_shear_n'] == pytest.approx(.25*aft)
    assert wave_loads(.075*L,**KW)['max_shear_n'] == pytest.approx(.625*aft)
    assert wave_loads(.9*L,**KW)['max_shear_n'] == pytest.approx(.4*5.7*base*nh)
    assert wave_loads(L,**KW)['max_shear_n'] == 0


@pytest.mark.parametrize('name', ['feeder_500','feeder_800','feeder_1700'])
def test_feeder_envelopes_reserve_wave_from_s7_stress_budget(name):
    params = load_param_file('fleet',name)
    geometry = HullGeometry.from_params(params)
    stations = permissible_stations(params,geometry)
    summary = design_summary(params,geometry)
    L, B, cb = summary['length_m'], summary['beam_m'], summary['block_coefficient']
    z_min = (10.75-((300-L)/100)**1.5)*L**2*B*(max(cb,.6)+.7)*1e-6
    kw = {key: summary[key] for key in KW}
    mid_wave=wave_loads(L/2,**kw)
    c = 10.75-((300-L)/100)**1.5
    cb_r = max(cb,.6)
    hog_design = c*L**2*B*(122.5-15*cb_r)
    sag_design = 65*c*L**2*B*(cb_r+.7)
    expected_z = 1.1*max(z_min,(mid_wave['hogging_nm']+hog_design)/(235e6/1.24),
                        (-mid_wave['sagging_nm']+sag_design)/(235e6/1.24))
    assert summary['assumed_effective_net_section_modulus_m3'] == pytest.approx(expected_z)
    assert summary['section_modulus_design_margin'] == 1.1
    kw = {key: summary[key] for key in KW}
    xs = [s.x_m for s in stations]
    assert xs == sorted(set(xs))
    # Retain full read-out coverage; finite end capacities avoid the old
    # artificial near-AP maximum caused by a vanishing sine limit.
    assert xs[0] == pytest.approx(geometry.buoyant_x_min_m)
    assert xs[-1] == pytest.approx(geometry.buoyant_x_max_m)
    assert xs[0] < 0 < L < xs[-1]
    assert set(geometry.x_m) <= set(xs)
    assert stations[0].max_shear_n > 1e6
    for box in params['envelope']['hatch_coamings']:
        assert box['x_min_m'] in xs and box['x_max_m'] in xs
    sigma, tau = 235e6/1.24, 235e6/(1.13*math.sqrt(3))
    for station in stations:
        rule_x = min(L,max(0.,station.x_m))
        wave = wave_loads(rule_x,**kw)
        assert station.max_bending_nm == pytest.approx(sigma*expected_z-wave['hogging_nm'])
        assert -station.min_bending_nm == pytest.approx(sigma*expected_z+wave['sagging_nm'])
        profile = np.interp(rule_x/L,[0,.1,.15,.3,.4,.6,.7,.85,.9,1],
                            [.6,.85,1.2,1.2,1,1,1.2,1.2,.85,.6])
        capacity = tau*summary['assumed_effective_net_shear_area_m2']*profile
        assert station.max_shear_n == pytest.approx(capacity-wave['max_shear_n'])
        assert -station.min_shear_n == pytest.approx(capacity+wave['min_shear_n'])
    # Hogging and sagging wave amplitudes differ; this must survive into SW.
    mid = min(stations,key=lambda s:abs(s.x_m-L/2))
    assert -mid.min_bending_nm < mid.max_bending_nm
    # SF capacity peaks around quarter points, rather than amidships.
    for sign in (1,-1):
        value = lambda s: s.max_shear_n if sign == 1 else -s.min_shear_n
        peak_station = max(stations,key=value)
        assert .15-1e-12 <= peak_station.x_m/L <= .35 or .65 <= peak_station.x_m/L <= .85+1e-12
        assert value(peak_station) > value(mid)
    assert not summary['torsion_required']
    # Independent dense maximization verifies no interior peak wave/profile
    # ratio was missed by the production merged-knot calculation.
    required=[]
    for u in np.linspace(0,1,2001):
        wave=wave_loads(float(u*L),**kw)
        p = np.interp(u,[0,.1,.15,.3,.4,.6,.7,.85,.9,1],[.6,.85,1.2,1.2,1,1,1.2,1.2,.85,.6])
        required.append(max(wave['max_shear_n'],-wave['min_shear_n'])/(tau*p))
    D=geometry.depth_m
    h_db=params['hull'].get('double_bottom_height_m')
    if h_db is None:
        h_db=max(t['geometry']['z_max_m'] for t in params['tanks'] if t['id'].startswith(('DB-','WB-')))
    web_area=2*((8+.04*L-1.25)*1e-3*D+(6.5+.03*L-1.25)*1e-3*(D-h_db))
    expected_area=max(.95*web_area,1.1*max(required))
    assert summary['assumed_design_swbm_hogging_nm'] == pytest.approx(hog_design)
    assert summary['assumed_design_swbm_sagging_magnitude_nm'] == pytest.approx(sag_design)
    assert summary['assumed_double_bottom_height_m'] == h_db
    assert summary['assumed_effective_net_shear_area_m2'] == pytest.approx(expected_area,rel=2e-5)


@pytest.mark.parametrize('length,c', [(90,10.75-2.1**1.5),(300,10.75),
                                     (325,10.75),(350,10.75),(500,9.75)])
def test_s7_new_build_minimum_units_branches_and_cb_floor(length,c):
    # Hand calculation returns cm³; convert by 1e-6, not 1e-3 or 1e-9.
    expected = c*length**2*20*(.6+.7)*1e-6
    assert minimum_section_modulus_m3(length,20,.5) == pytest.approx(expected)
    assert minimum_section_modulus_m3(length,20,.6) == pytest.approx(expected)


@pytest.mark.parametrize('u,p', [(0,.6),(.1,.85),(.15,1.2),(.25,1.2),
                                      (.30,1.2),(.35,1.1),(.4,1),(.5,1),
                                      (.6,1),(.65,1.1),(.7,1.2),(.75,1.2),
                                      (.85,1.2),(.90,.85),(1,.6),(-.02,.6),(1.02,.6)])
def test_assumed_shear_area_quarter_reinforcement(u,p):
    assert assumed_shear_area_m2(u*100,100,.4) == pytest.approx(.4*p)


@pytest.mark.parametrize('cb', [.5,.6,.8])
def test_s7_implied_design_moments_independent_hand_calculation(cb):
    c=10.75-1.8**1.5
    cb_r=max(.6,cb)
    hog,sag=s7_implied_design_swbm_nm(120,20,cb)
    assert hog == pytest.approx(c*120**2*20*(122.5-15*cb_r))
    assert sag == pytest.approx(65*c*120**2*20*(cb_r+.7))


@pytest.mark.parametrize('L,D,h_db,expected', [(107.8,8.25,1.2,.28656312),
    (107.8,9.2,1.2,.32232056),(124.41,11.3,1.4,.421),(160,14,1.4,.590)])
def test_approved_double_skin_shear_area_and_depth_dependence(L,D,h_db,expected):
    # Hold wave inputs fixed to isolate geometry-derived web depth. Formula
    # and rounded design-review magnitudes are checked independently.
    kw={**KW,'length_m':L,'beam_m':L/6}
    _,area=_assumed_scantlings(kw,D,h_db)
    side=(8+.04*L-1.25)*1e-3
    inner=(6.5+.03*L-1.25)*1e-3
    assert area == pytest.approx(.95*2*(side*D+inner*(D-h_db)))
    assert area == pytest.approx(expected,abs=.0006)


def test_double_bottom_height_tracks_declared_or_db_geometry():
    params={'hull': {}, 'tanks': [
        {'id': 'WB-1-P', 'kind': 'ballast',
         'geometry': {'z_min_m': .15, 'z_max_m': 1.2}},
        {'id': 'FO-P', 'kind': 'fuel',
         'geometry': {'z_min_m': .15, 'z_max_m': 4.5}}]}
    assert _double_bottom_height_m(params,9.2) == pytest.approx(1.2)
    params['hull']['double_bottom_height_m']=1.45
    assert _double_bottom_height_m(params,9.2) == 1.45
    params['hull']['double_bottom_height_m']=9.2
    with pytest.raises(ValueError,match='below hull depth'):
        _double_bottom_height_m(params,9.2)


def test_rejects_outside_wave_rule_domain():
    with pytest.raises(ValueError,match='applicability'):
        wave_loads(50, **{**KW,'length_m':80})


def test_polygon_bow_flare_partial_gauss_panels_and_coaming_exclusion():
    # Independent analytic loft: full section width b(z,x)=10+.1x+.4z.
    # For T=5,D=10, b_D-b_T=2; bow area difference = .2L*2.
    # A single panel straddles .8L, so cropping nodes would lose the bow.
    L = 100.
    x = np.array([L/2-L/(2*math.sqrt(3)),L/2+L/(2*math.sqrt(3))])
    sections=[]
    for xx in x:
        base=10+.1*xx
        bare=np.array([[-base/2,0],[base/2,0],[(base+4)/2,10],[-(base+4)/2,10]])
        # A broad watertight coaming would spoil deck breadth if included.
        coaming=np.array([[-30,10],[30,10],[30,12],[-30,12]])
        sections.append([bare,coaming])
    geometry=SimpleNamespace(lpp_m=L,depth_m=10.,x_m=x,
                             integration_weights_m=np.array([50.,50.]),sections=sections)
    assert _bow_flare_coefficient(geometry,5.) == pytest.approx(.4)
    assert _partial_gauss_integral(geometry,3+2*x,80.,100.) == pytest.approx(3660.)
    # Full-panel integration must preserve the supplied Gauss integral exactly.
    assert _partial_gauss_integral(geometry,3+2*x,0.,100.) == pytest.approx(np.dot(3+2*x,[50.,50.]))


def test_production_kcs_inputs_have_no_retired_shape_parameters():
    params=load_param_file('fleet','feeder_500')
    assert params['hull_form']['family'] == 'stowlm_offsets_v1'
    for key in ('aft_run_fraction','fore_run_fraction','bilge_radius_m','deck_flare_fraction'):
        assert key not in params['hull_form']
    geometry=HullGeometry.from_params(params)
    summary=design_summary(params,geometry)
    wet=geometry.submerged(params['hull']['scantling_draught_m'],params['hull']['scantling_draught_m'])
    L,B,T=geometry.lpp_m,geometry.beam_m,params['hull']['scantling_draught_m']
    assert summary['block_coefficient'] == pytest.approx(wet.volume_m3/(L*B*T))
    assert summary['waterplane_coefficient'] == pytest.approx(wet.waterplane_area_m2/(L*B))
    # Source design-waterline/deck values are independently evaluated using
    # an even/odd horizontal ray intersection, rather than private helpers.
    def breadth(poly,z):
        if z == max(poly[:,1]):
            ys=poly[poly[:,1]==z,0]
            return max(ys)-min(ys)
        ys=[]
        for a,b in zip(poly,np.roll(poly,-1,axis=0)):
            if (a[1]<=z<b[1]) or (b[1]<=z<a[1]):
                ys.append(a[0]+(z-a[1])*(b[0]-a[0])/(b[1]-a[1]))
        ys.sort()
        return sum(ys[i+1]-ys[i] for i in range(0,len(ys),2))
    widths=np.array([breadth(p[0],max(p[0][:,1]))-breadth(p[0],T) for p in geometry.sections])
    # Composite trapezoid on independent panel endpoint extrapolations is
    # exact for the declared linear-within-panel width approximation.
    integral=0.
    for j in range(0,len(geometry.x_m),2):
        x0,x1=geometry.x_m[j:j+2]
        c=(x0+x1)/2; half=geometry.integration_weights_m[j]
        lo,hi=max(.8*L,c-half),min(L,c+half)
        if hi>lo:
            f=lambda x: widths[j]+(x-x0)*(widths[j+1]-widths[j])/(x1-x0)
            integral+=(hi-lo)*(f(lo)+f(hi))/2
    assert summary['bow_flare_coefficient'] == pytest.approx(integral/(.2*L*(geometry.depth_m-T)))


def test_kcs_depth_update_changes_geometry_driven_shear_area():
    params=load_param_file('fleet','feeder_500')
    areas=[]
    for depth in (8.25,9.2):
        params['hull']['depth_m']=depth
        params['envelope']['deck_z_m']=depth
        for box in params['envelope']['hatch_coamings']:
            height=box['z_max_m']-box['z_min_m']
            box['z_min_m'],box['z_max_m']=depth,depth+height
        geometry=HullGeometry.from_params(params)
        summary=design_summary(params,geometry)
        assert summary['depth_m'] == depth
        side=(8+.04*geometry.lpp_m-1.25)*1e-3
        inner=(6.5+.03*geometry.lpp_m-1.25)*1e-3
        h_db=_double_bottom_height_m(params,depth)
        expected=.95*2*(side*depth+inner*(depth-h_db))
        areas.append(summary['assumed_effective_net_shear_area_m2'])
        assert areas[-1] == pytest.approx(expected)
        # Fleet geometry changes affect the wave allowance; check the approved
        # sizing relation at both depths rather than a historical modulus.
        kw = {key: summary[key] for key in (
            'length_m', 'beam_m', 'draught_m', 'block_coefficient',
            'waterplane_coefficient', 'bow_flare_coefficient')}
        wave = wave_loads(geometry.lpp_m/2, **kw)
        hog_design, sag_design = s7_implied_design_swbm_nm(
            geometry.lpp_m, geometry.beam_m, summary['block_coefficient'])
        z_min = minimum_section_modulus_m3(
            geometry.lpp_m, geometry.beam_m, summary['block_coefficient'])
        expected_z = 1.10*max(
            z_min, (wave['hogging_nm']+hog_design)/(235e6/1.24),
            (-wave['sagging_nm']+sag_design)/(235e6/1.24))
        assert summary['assumed_effective_net_section_modulus_m3'] == pytest.approx(expected_z)
    assert areas[1] > areas[0]


def test_double_bottom_height_accepts_section_tank_bounds():
    # Section-defined tanks retain declared bounds; no rectangular-prism
    # approximation or detailed-section scan is needed for this height.
    params={'hull': {}, 'tanks': [
        {'id': 'DB-A-P', 'kind': 'ballast', 'geometry': {
            'shape': 'hull_sections', 'z_min_m': .15, 'z_max_m': 1.281,
            'sections': []}},
        {'id': 'HT-P', 'kind': 'ballast', 'geometry': {
            'shape': 'hull_sections', 'z_min_m': .15, 'z_max_m': 5.0,
            'sections': []}}]}
    assert _double_bottom_height_m(params,9.2) == pytest.approx(1.281)
