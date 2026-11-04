"""Hand integration and catalogue pass/fail tests for IS Code criteria."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.stability_criteria import (
    _coaming_depth, _crossings, _integral, _sharp_bilge, _waterline_length, evaluate_criteria,
)


def condition(*, length=80., gm=1., kg=4., wind=100., gz_scale=1., apex=40., depth=8., phi_f=90.,
              beam=10., cb=1., bilge_keel=False, angles_deg=None, gz_values=None,
              sharp_bilge=False, waterline_length=None, **extra):
    hull = SimpleNamespace(lpp_m=length, beam_m=beam, depth_m=depth,
                           bilge_keel=bilge_keel, windage_area_m2=wind,
                           windage_lever_m=5.)
    box = np.array([[-beam/2, 0.], [beam/2, 0.], [beam/2, depth], [-beam/2, depth]])
    if not sharp_bilge:
        box = np.array([[-beam/2+.1, 0.], [beam/2-.1, 0.], [beam/2, .1],
                        [beam/2, depth], [-beam/2, depth], [-beam/2, .1]])
    span = length if waterline_length is None else waterline_length
    x = length/2+np.array([-1., 1.])*span/(2*math.sqrt(3))
    geometry = SimpleNamespace(
        sections=[[box], [box]], x_m=x, integration_weights_m=np.array([span/2]*2),
        submerged=lambda *args: SimpleNamespace(volume_m3=length*beam*5*cb,
                                                waterplane_area_m2=length*beam))
    angles = np.radians([0., apex, 90.] if angles_deg is None else angles_deg)
    return evaluate_criteria(
        angles, np.array([0., gz_scale, 0.] if gz_values is None else gz_values), gm_m=gm, hull=hull,
        geometry=geometry, displacement_kg=length*beam*5*cb*1025,
        kg_m=kg, draught_m=5., phi_f_rad=math.radians(phi_f),
        hull_params={'envelope': {'hatch_coamings': []},
                     'hull_form': {'family': 'stowlm_offsets_v1'}}, **extra)


def test_exact_piecewise_integral_and_crossings():
    x, y = np.array([0., .5, 1.]), np.array([0., 1., 0.])
    assert _integral(x, y, .25, .75) == pytest.approx(.375)
    assert _crossings(x,y,.5) == [.25,.75]
    assert _crossings(x,y,1.) == [.5]
    with pytest.raises(ValueError, match='cover'):
        _integral(x,y,-.1,.5)


def test_all_part_a_pass_and_hand_areas():
    m, margins, failures = condition()
    assert not [f for f in failures if f[0].startswith('iscode.a2_2')]
    assert m['area_0_30_mrad'] == pytest.approx(.5*.75*math.radians(30))
    assert m['area_0_40_mrad'] == pytest.approx(.5*math.radians(40))
    assert m['area_30_40_mrad'] == pytest.approx(.5*(.75+1)*math.radians(10))
    assert m['gz_max_m'] == 1.
    assert m['angle_gz_max_rad'] == math.radians(40)
    assert all(v >= 0 for k,v in margins.items() if k.startswith('area_'))


@pytest.mark.parametrize('rule', ['gz_area_0_30','gz_area_0_40','gz_area_30_40','gz_at_30'])
def test_part_a_each_lever_area_criterion_fails(rule):
    assert 'iscode.a2_2.'+rule in {v[0] for v in condition(gz_scale=.01)[2]}


def test_part_a_peak_angle_and_gm_fail_and_boundary_pass():
    assert 'iscode.a2_2.max_gz_angle' in {v[0] for v in condition(apex=20.)[2]}
    assert 'iscode.a2_2.initial_gm' in {v[0] for v in condition(gm=.149)[2]}
    assert 'iscode.a2_2.initial_gm' not in {v[0] for v in condition(gm=.15)[2]}
    assert 'iscode.a2_2.max_gz_angle' not in {v[0] for v in condition(apex=25.)[2]}


def test_flooding_truncates_areas_and_peak():
    m, _, _ = condition(phi_f=35.)
    assert m['area_0_40_mrad'] == pytest.approx(.5*.875*math.radians(35))
    assert m['area_30_40_mrad'] == pytest.approx(.5*(.75+.875)*math.radians(5))
    assert m['angle_gz_max_rad'] == math.radians(35)


def test_weather_hand_work_and_all_factors():
    m, _, f = condition()
    assert not [v for v in f if v[0]=='iscode.a2_3.weather']
    slope = 1/math.radians(40)
    gust_root = m['weather_lw2_m']/slope
    start = m['weather_phi0_rad']-m['weather_phi1_rad']
    a = .5*slope*(gust_root-start)**2
    # B crosses the peak at40deg; integrate both straight segments by hand.
    peak = math.radians(40)
    end = math.radians(50)
    b = (.5*slope*(peak-gust_root)**2 + .5*(1.+.8)*(end-peak)
         -m['weather_lw2_m']*(end-peak))
    assert m['weather_area_a_mrad'] == pytest.approx(a)
    assert m['weather_area_b_mrad'] == pytest.approx(b)
    assert m['weather_ratio'] == pytest.approx(b/a)
    assert m['weather_x1'] == 1.
    assert m['weather_x2'] == 1.
    assert m['weather_k'] == 1.
    assert m['weather_r'] == pytest.approx(.61)
    assert m['weather_lw2_m'] == pytest.approx(1.5*m['weather_lw1_m'])
    assert m['weather_phi1_rad'] == pytest.approx(math.radians(109*math.sqrt(.61*m['weather_s'])))


def test_weather_fails_large_wind_deck_edge_and_table_applicability():
    assert 'iscode.a2_3.weather' in {v[0] for v in condition(wind=100000.)[2]}
    _, margins, _ = condition(wind=10000.,depth=5.1)
    assert margins['weather_steady_heel_margin_rad'] < 0
    _, margins, f = condition(kg=10.)
    assert margins['weather_table_applicability'] < 0
    assert 'iscode.a2_3.weather' in {v[0] for v in f}


def test_container_form_factor_and_six_limits():
    m, margins, f = condition(length=120.)
    C = 5*8/100*math.sqrt(100/120)
    assert m['containership_form_factor'] == pytest.approx(C)
    assert m['area_0_flood_mrad'] == pytest.approx(.5*math.radians(90))
    assert not [v for v in f if v[0]=='iscode.b2_3.containership_second_check']
    _, margins, f = condition(length=120.,gz_scale=.001)
    keys = [k for k in margins if k.startswith('containership_') and k!='containership_downflooding_30_margin_rad']
    assert len(keys)==6 and all(margins[k] < 0 for k in keys)
    assert len([v for v in f if v[0]=='iscode.b2_3.containership_second_check']) == 6


@pytest.mark.parametrize('key,coefficient', [
    ('area_0_30_mrad',.009), ('area_0_40_mrad',.016),
    ('area_30_40_mrad',.006), ('gz_at_or_above_30_m',.033),
    ('gz_max_m',.042), ('area_0_flood_mrad',.029)])
def test_each_container_threshold_pass_fail(key, coefficient):
    baseline, _, _ = condition(length=120.)
    threshold_scale = coefficient / baseline['containership_form_factor'] / baseline[key]
    _, passing, _ = condition(length=120., gz_scale=threshold_scale*1.001)
    _, failing, failures = condition(length=120., gz_scale=threshold_scale*.999)
    assert passing['containership_'+key] > 0
    assert failing['containership_'+key] < 0
    assert any(v[0]=='iscode.b2_3.containership_second_check' for v in failures)


@pytest.mark.parametrize('key,limit', [
    ('area_0_30_mrad', .055), ('area_0_40_mrad', .09),
    ('area_30_40_mrad', .03), ('gz_at_or_above_30_m', .2)])
def test_each_part_a_threshold_exact_boundary(key, limit):
    baseline, _, _ = condition()
    threshold_scale = limit / baseline[key]
    _, passing, _ = condition(gz_scale=threshold_scale*1.001)
    _, failing, _ = condition(gz_scale=threshold_scale*.999)
    assert passing[key] > 0
    assert failing[key] < 0


def test_supplied_negative_gz_changes_only_windward_work():
    ordinary, _, _ = condition()
    # Stronger windward righting levers increase energy accumulated during roll.
    signed, _, _ = condition(negative_gz=np.array([0., -2., 0.]))
    assert signed['weather_area_a_mrad'] > ordinary['weather_area_a_mrad']
    assert signed['weather_area_b_mrad'] == pytest.approx(ordinary['weather_area_b_mrad'])
    assert signed['weather_ratio'] < ordinary['weather_ratio']
    with pytest.raises(ValueError, match='match'):
        condition(negative_gz=[0., -1.])


def test_sixteen_degree_steady_wind_limit_fails():
    _, margins, failures = condition(wind=15000.)
    assert margins['weather_steady_heel_margin_rad'] < 0
    assert any('Steady-wind' in f[-1] for f in failures)


def test_period_outside_table_is_failed_and_nonpositive_gm_does_not_pass():
    _, margins, _ = condition(weather_roll_period_s=20.)
    assert margins['weather_table_applicability'] < 0
    _, margins, failures = condition(gm=-.1)
    assert margins['weather_table_applicability'] < 0
    assert 'iscode.a2_2.initial_gm' in {f[0] for f in failures}


def test_weather_table_interpolation_and_missing_keel_data():
    m, _, _ = condition(beam=13.75, cb=.575, bilge_keel=True,
                        weather_bilge_keel_area_m2=2.25*80*13.75/100,
                        weather_roll_period_s=13.)
    assert m['weather_x1'] == pytest.approx(.94)
    assert m['weather_x2'] == pytest.approx(.92)
    assert m['weather_k'] == pytest.approx(.835)
    assert m['weather_s'] == pytest.approx(.059)
    with pytest.raises(ValueError, match='keel area'):
        condition(bilge_keel=True)


def test_coaming_port_starboard_union_and_central_clip():
    hull=SimpleNamespace(lpp_m=120.,beam_m=10.,depth_m=8.)
    boxes=[dict(x_min_m=0.,x_max_m=60.,y_min_m=-4.,y_max_m=0.,z_min_m=8.,z_max_m=9.),
           dict(x_min_m=0.,x_max_m=60.,y_min_m=0.,y_max_m=4.,z_min_m=8.,z_max_m=9.)]
    # Only30m lies in central60m; adjacent half-hatches form an8m hatch.
    assert _coaming_depth(hull,{'envelope':{'hatch_coamings':boxes}})==pytest.approx(8+.6*.5)


@pytest.mark.parametrize('peak, fails', [(24.6, True), (25.4, False)])
def test_max_gz_angle_refined_across_twenty_five_degree_boundary(peak, fails):
    degrees = np.unique(np.r_[np.arange(0., 91., 2.), 25.])
    levers = .6-.001*(degrees-peak)**2
    metrics, _, failures = condition(angles_deg=degrees, gz_values=levers)
    assert math.degrees(metrics['angle_gz_max_rad']) == pytest.approx(peak)
    assert metrics['gz_max_m'] == pytest.approx(.6)
    assert ('iscode.a2_2.max_gz_angle' in {v[0] for v in failures}) is fails


def test_exact_hull_peak_override_supersedes_sampled_peak():
    metrics, _, failures = condition(peak_angle_rad=math.radians(24.6), peak_gz_m=1.01)
    assert metrics['angle_gz_max_rad'] == math.radians(24.6)
    assert metrics['gz_max_m'] == 1.01
    assert 'iscode.a2_2.max_gz_angle' in {v[0] for v in failures}


def test_windward_roll_uses_full_curve_beyond_leeward_flooding():
    metrics, margins, _ = condition(phi_f=10.)
    start = metrics['weather_phi0_rad']-metrics['weather_phi1_rad']
    assert start < -math.radians(10.)
    slope = 1/math.radians(40.)
    gust_root = metrics['weather_lw2_m']/slope
    expected_a = .5*slope*(gust_root-start)**2
    expected_b = .5*slope*(math.radians(10.)-gust_root)**2
    assert metrics['weather_area_a_mrad'] == pytest.approx(expected_a)
    assert metrics['weather_area_b_mrad'] == pytest.approx(expected_b)
    assert metrics['weather_ratio'] == pytest.approx(expected_b/expected_a)
    assert margins['weather_table_applicability'] == 0.


@pytest.mark.parametrize('length, part_b', [(99.99, False), (100., False), (100.01, True)])
def test_d21_length_selects_alternative_criteria_retains_gm_weather(length, part_b):
    metrics, margins, failures = condition(length=length, apex=20., gm=.1, wind=100000.)
    assert metrics['criteria_part_a_2_2_1_to_3_active'] == float(not part_b)
    assert metrics['criteria_part_b_2_3_active'] == float(part_b)
    assert metrics['criteria_initial_gm_active'] == metrics['criteria_weather_active'] == 1.
    rules = {f[0] for f in failures}
    assert ('iscode.a2_2.max_gz_angle' in rules) is not part_b
    assert ('angle_gz_max_rad' in margins) is not part_b
    assert 'iscode.a2_2.initial_gm' in rules
    assert 'iscode.a2_3.weather' in rules
    if part_b:
        assert all(r == 'iscode.a2_2.initial_gm' for r in rules if r.startswith('iscode.a2_2.'))


@pytest.mark.parametrize('flood, expected_end, expected_area', [
    (90., 60., math.radians(30.)), (45., 45., math.radians(26.25))])
def test_container_total_area_ends_at_flooding_or_vanishing(flood, expected_end, expected_area):
    metrics, _, _ = condition(length=120., phi_f=flood,
                              angles_deg=[0., 30., 60., 90.], gz_values=[0., 1., 0., -1.])
    assert metrics['containership_vanishing_angle_detected'] == 1.
    assert metrics['containership_vanishing_angle_rad'] == math.radians(60.)
    assert metrics['containership_total_area_end_rad'] == math.radians(expected_end)
    assert metrics['area_0_flood_mrad'] == pytest.approx(expected_area)


def test_supplied_heeled_deck_edge_root_controls_steady_heel_cap():
    _, old, _ = condition()
    metrics, corrected, _ = condition(deck_edge_angle_rad=math.radians(1.))
    assert metrics['weather_deck_edge_rad'] == math.radians(1.)
    assert corrected['weather_steady_heel_margin_rad'] < old['weather_steady_heel_margin_rad']


def test_container_cannot_credit_missing_thirty_degree_range_after_flooding():
    # A very large lever could otherwise pass a 0–30 area threshold using
    # only pre-flooding 0–20 work. The required angular range must exist.
    _, margins, failures=condition(length=120.,phi_f=20.,gz_scale=100.)
    assert margins['containership_downflooding_30_margin_rad']<0
    assert any(f[0]=='iscode.b2_3.containership_second_check' and
               '30 degree' in f[4] for f in failures)


def test_weather_bilge_classification_uses_polygons_without_retired_params():
    rounded, _, _ = condition()
    sharp, _, _ = condition(sharp_bilge=True)
    assert rounded['weather_k'] == 1.
    assert sharp['weather_k'] == .7


def test_weather_waterline_length_includes_overhang_in_roll_and_keel_factors():
    metrics, _, _ = condition(waterline_length=100., bilge_keel=True,
                              weather_bilge_keel_area_m2=20.)
    assert metrics['weather_waterline_length_m'] == pytest.approx(100.)
    assert metrics['weather_block_coefficient'] == pytest.approx(.8)
    assert metrics['weather_roll_period_s'] == pytest.approx(2*(.373+.023*2-.043)*10)
    assert metrics['weather_k'] == pytest.approx(.88)


def test_waterline_length_solves_section_loft_immersion_boundary_with_trim():
    # A four-metre high box loft rises linearly along x: zbottom=x/10.
    # At level draught 5, the waterline intersects it only for 10<x<50.
    x = 50+np.array([-1., 1.])*100/(2*math.sqrt(3))
    rectangle = np.array([[-5., 0.], [5., 0.], [5., 4.], [-5., 4.]])
    geometry = SimpleNamespace(x_m=x, integration_weights_m=np.array([50., 50.]),
        sections=[[rectangle+np.array([0., station/10])] for station in x])
    assert _waterline_length(geometry, 5., 5., 100.) == pytest.approx(40.)
    # Rising waterplane halves relative slope: intersection is20<x<100.
    assert _waterline_length(geometry, 5., 10., 100.) == pytest.approx(80.)
    assert _sharp_bilge(geometry)


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_production_kcs_waterline_and_bilge_classification(name):
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.hydrostatics import HullGeometry
    params = load_ship_params(name)
    geometry = HullGeometry.from_params(params)
    draught = params['hull']['design_draught_m']
    length = _waterline_length(geometry, draught, draught, geometry.lpp_m)
    assert length > geometry.lpp_m
    assert length <= geometry.buoyant_x_max_m-geometry.buoyant_x_min_m+1e-9
    assert not _sharp_bilge(geometry)


def test_insufficient_forty_degree_area_coverage_is_never_silently_truncated():
    with pytest.raises(ValueError,match='cover 40 degrees or downflooding'):
        condition(length=120.,phi_f=45.,angles_deg=[0.,10.,20.,30.,35.],gz_values=[0.,1.,0.,-1.,-2.])
