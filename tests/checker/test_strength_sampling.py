"""Checker-level cut selection against independently balanced synthetic beams."""

from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import strength


def _beam(monkeypatch, *, points=(), segments=(), local_buoyancy=0.,
          lower_sf=-1e9, upper_sf=1e9, lower_bm=-1e9, upper_bm=1e9,
          envelope_xs=None):
    """Isolate cut/envelope assessment from the independently tested hull solve."""
    edges = [0., 50., 51., 100.]
    x, weights, buoyancy = [], [], []
    for left, right in zip(edges, edges[1:]):
        midpoint, half = (left+right)/2, (right-left)/2
        x.extend([midpoint-half/np.sqrt(3), midpoint+half/np.sqrt(3)])
        weights.extend([half, half])
        buoyancy.extend([local_buoyancy if left == 50. else 0.]*2)
    submerged = SimpleNamespace(x_m=np.array(x), integration_weights_m=np.array(weights),
        sectional_areas_m2=np.array(buoyancy), vcb_m=0.)
    condition = SimpleNamespace(submerged=submerged, trim_m=0., heel_rad=0.,
        t_aft_m=1., t_fwd_m=1.)
    def at(value, xx):
        return value(xx) if callable(value) else value
    stations = [SimpleNamespace(x_m=xx, min_shear_n=at(lower_sf, xx),
        max_shear_n=at(upper_sf, xx), min_bending_nm=at(lower_bm, xx),
        max_bending_nm=at(upper_bm, xx)) for xx in (edges if envelope_xs is None else envelope_xs)]
    hull = SimpleNamespace(stations=stations, hull_offsets_ref='params/fleet/synthetic.toml',
        lpp_m=100., beam_m=20., depth_m=10.)
    geometry = SimpleNamespace(lpp_m=100., beam_m=20., depth_m=10.)
    instance = SimpleNamespace(vessel=SimpleNamespace(id='synthetic', hull=hull),
        port=SimpleNamespace(water_density_kg_m3=1.))
    ship = SimpleNamespace(vessel_id='synthetic', covers_off={})
    monkeypatch.setattr(strength, '_geometry', lambda name: ({}, geometry))
    monkeypatch.setattr(strength, '_condition_loads', lambda *args:
        (condition, 1000., [], list(points), list(segments), (50.5, 0., 0.)))
    return strength.check_strength(instance, ship, call_index=7)


@pytest.mark.parametrize('sign', [1, -1], ids=['aft_positive', 'forward_negative'])
def test_checker_finds_both_sides_of_unsampled_concentrated_load(monkeypatch, sign):
    # S11A 2.2.1, local PDF p. 8: "calculated at each section along the ship length".
    # Uniform upward 1000 kg/m on [50,51] balances 1000 kg at 50.5 m.
    # All prescribed cuts have zero SF, but its one-sided limits are +/-500g N.
    report = _beam(monkeypatch, points=[(50.5, 1000.)], local_buoyancy=1000.,
        upper_sf=4000. if sign > 0 else 1e9,
        lower_sf=-4000. if sign < 0 else -1e9)
    assert report.ran, report.error
    assert report.metrics['max_sf_ratio'] == pytest.approx(500*strength.G/4000)
    failures = [v for v in report.violations if v.unit == 'N' and v.measured*sign > 0]
    assert failures, 'Strength must assess the omitted one-sided concentrated-load cut.'
    assert failures[0].measured == pytest.approx(sign*500*strength.G)


def _balanced_polynomial():
    # q(u)=1000g(1-6u+6u^2), u=x-50 in [0,1]. Both its force and
    # first moment vanish: raw end SF and BM close without a correction.
    k = 1000*strength.G
    return [(50., 51., k, -6*k, 6*k)]


def test_checker_finds_continuous_interior_shear_extrema(monkeypatch):
    report = _beam(monkeypatch, segments=_balanced_polynomial(), lower_sf=-900., upper_sf=900.)
    assert report.ran, report.error
    # SF(u)=1000g(u-3u^2+2u^3), extrema at (3 +/- sqrt(3))/6.
    peak = 1000*strength.G*np.sqrt(3)/18
    assert report.metrics['max_sf_ratio'] == pytest.approx(peak/900.)
    assert any(v.unit == 'N' and v.measured > 900. for v in report.violations)
    assert any(v.unit == 'N' and v.measured < -900. for v in report.violations)


def test_checker_finds_continuous_interior_bending_extremum(monkeypatch):
    report = _beam(monkeypatch, segments=_balanced_polynomial(), lower_bm=-300.)
    assert report.ran, report.error
    # BM(u)=-500g*u^2(1-u)^2, its minimum is -1000g/32 at u=1/2.
    assert report.metrics['max_bm_ratio'] == pytest.approx(1000*strength.G/32/300.)
    assert any(v.unit == 'N m' and v.measured < -300. for v in report.violations)


@pytest.mark.parametrize('name', ['feeder_500','feeder_800','feeder_1700'])
def test_harbour_sag_fails_even_when_a_mixed_sign_envelope_would_pass(monkeypatch, name):
    from stowage.checker.strength_limits import permissible_stations
    from stowage.params import load_param_file
    from stowage.vessel.hydrostatics import HullGeometry

    params = load_param_file('fleet',name)
    geometry = HullGeometry.from_params(params)
    stations = permissible_stations(params,geometry,in_port=True)
    mid = min(stations,key=lambda station:abs(station.x_m-geometry.lpp_m/2))
    hog,sag = mid.max_bending_nm,-mid.min_bending_nm
    assert hog > sag
    target_sag = (hog+sag)/2
    # Independently balanced polynomial beam: BM_min=-k/32. Give it a sag
    # between the two harbour allowances; the hog allowance must not rescue it.
    k = 32*target_sag
    segments = [(50.,51.,k,-6*k,6*k)]
    report = _beam(monkeypatch,segments=segments,lower_bm=-sag,upper_bm=hog,
                   lower_sf=-1e12,upper_sf=1e12)
    assert report.ran, report.error
    assert report.metrics['max_bm_ratio'] == pytest.approx(target_sag/sag)
    assert any(v.measured == pytest.approx(-target_sag) and
               v.limit == -sag and v.severity == 'hard' for v in report.violations)
    mixed_sign = _beam(monkeypatch,segments=segments,lower_bm=-hog,upper_bm=hog,
                      lower_sf=-1e12,upper_sf=1e12)
    assert mixed_sign.ran, mixed_sign.error
    assert mixed_sign.metrics['max_bm_ratio'] == pytest.approx(target_sag/hog)
    assert not mixed_sign.violations


def test_check_strength_in_port_selects_harbour_limits_without_mutating_input(monkeypatch):
    from copy import deepcopy
    from stowage.checker import strength_limits
    from stowage.params import load_param_file
    from stowage.vessel.hydrostatics import HullGeometry

    params = load_param_file('fleet','feeder_500')
    geometry = HullGeometry.from_params(params)
    sea_stations = strength_limits.permissible_stations(params,geometry)
    harbour_stations = strength_limits.permissible_stations(params,geometry,in_port=True)
    mid = min(range(len(sea_stations)),key=lambda i:abs(sea_stations[i].x_m-geometry.lpp_m/2))
    target_sag = -(sea_stations[mid].min_bending_nm+harbour_stations[mid].min_bending_nm)/2
    # Self-balanced polynomial beam has BM_min=-k*w^2/32. Isolate envelope
    # selection from the independently tested equilibrium/load construction.
    length = geometry.lpp_m
    width = .8*length
    k = 32*target_sag/width**2
    segments = [((length-width)/2,(length+width)/2,k,-6*k/width,6*k/width**2)]
    aft,fore = sea_stations[0].x_m,sea_stations[-1].x_m
    centre,half = (aft+fore)/2,(fore-aft)/2
    x = np.array([centre-half/np.sqrt(3),centre+half/np.sqrt(3)])
    submerged = SimpleNamespace(x_m=x,integration_weights_m=np.array([half]*2),
        sectional_areas_m2=np.zeros(2),vcb_m=0.)
    condition = SimpleNamespace(submerged=submerged,trim_m=0.,heel_rad=0.,t_aft_m=1.,t_fwd_m=1.)
    hull = SimpleNamespace(stations=sea_stations,hull_offsets_ref='params/fleet/feeder_500.toml',
        lpp_m=length,beam_m=geometry.beam_m,depth_m=geometry.depth_m)
    instance = SimpleNamespace(vessel=SimpleNamespace(id='synthetic',hull=hull),
        port=SimpleNamespace(water_density_kg_m3=1025.))
    ship = SimpleNamespace(vessel_id='synthetic',covers_off={})
    before_instance,before_ship = deepcopy(instance),deepcopy(ship)
    monkeypatch.setattr(strength,'_geometry',lambda name:(params,geometry))
    monkeypatch.setattr(strength,'_condition_loads',lambda *args:
        (condition,1000.,[],[],segments,(length/2,0.,0.)))
    selected = []
    original = strength_limits.permissible_stations
    def record(*args, **kwargs):
        selected.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(strength_limits,'permissible_stations',record)

    legacy = strength.check_strength(instance,ship,call_index=0)
    assert legacy == strength.check_strength(instance,ship,call_index=0,in_port=False)
    harbour = strength.check_strength(instance,ship,call_index=0,in_port=True)
    assert legacy.ran and harbour.ran, (legacy.error,harbour.error)
    assert legacy.metrics['max_bm_ratio'] > 1
    assert harbour.metrics['max_bm_ratio'] < 1
    assert legacy.metrics['max_sf_ratio'] == harbour.metrics['max_sf_ratio']
    assert selected == [{'in_port': True}]
    assert instance == before_instance and ship == before_ship
    assert legacy == strength.check_strength(instance,ship,call_index=0)


@pytest.mark.parametrize('kind', ['shear', 'bending'])
def test_checker_finds_ratio_extremum_against_local_linear_envelope(monkeypatch, kind):
    # A capacity gradient moves the maximum utilisation away from the
    # force/moment extremum. Checking physical extrema alone is insufficient.
    from numpy.polynomial import Polynomial

    if kind == 'shear':
        numerator = Polynomial([0., 1., -3., 2.])*1000*strength.G
        denominator = Polynomial([500., 1000.])
        kwargs = {'upper_sf': lambda xx: float(denominator(np.clip(xx-50., 0., 1.)))}
        metric = 'max_sf_ratio'
    else:
        numerator = Polynomial([0., 0., .5, -1., .5])*1000*strength.G
        denominator = Polynomial([50., 250.])
        kwargs = {'lower_bm': lambda xx: -float(denominator(np.clip(xx-50., 0., 1.)))}
        metric = 'max_bm_ratio'
    roots = (numerator.deriv()*denominator-numerator*denominator.deriv()).roots()
    candidates = [r.real for r in roots if abs(r.imag) < 1e-10 and 0. < r.real < 1.]
    expected = max(float(numerator(u)/denominator(u)) for u in candidates)
    report = _beam(monkeypatch, segments=_balanced_polynomial(), **kwargs)
    assert report.ran, report.error
    assert expected > 1.
    assert report.metrics[metric] == pytest.approx(expected, rel=1e-9)
    assert report.violations


def test_assessment_sweep_matches_direct_integration_at_over_1000_cuts():
    from stowage.checker.strength_assessment import assessment_cuts

    # A deterministic dense oracle challenges the independent event sweep:
    # overlapping blocks, arbitrary polynomial breakpoints, and multiple point
    # forces at one location. Direct integration sums each load separately.
    rng = np.random.default_rng(713)
    segments = [(0., 100., 150., -1.7, .008),
                (3., 70., -450., 0., 0.), (20., 90., -275., 0., 0.)]
    for _ in range(20):
        left, right = sorted(rng.uniform(0., 100., size=2))
        segments.append((left, right, *rng.uniform(-20., 20., size=3)))
    points = [(23.25, -1700.), (23.25, -900.), (60.75, 1300.),
              (60.75, -1300.), (0., -700.), (100., -1400.)]
    stations = [SimpleNamespace(x_m=float(xx), min_shear_n=-1e10, max_shear_n=1e10,
        min_bending_nm=-1e12, max_bending_nm=1e12) for xx in np.linspace(0., 100., 1001)]
    cuts = assessment_cuts(segments, points, stations)
    assert len(cuts) > 1000
    locations = [cut[0] for cut in cuts]
    expected_sf, expected_bm = strength._integrate_segments(locations, segments, points)
    for index, (xx, _, _, side) in enumerate(cuts):
        if side == 'aft':
            expected_sf[index] -= sum(force for position, force in points if position == xx)
    np.testing.assert_allclose([cut[1] for cut in cuts], expected_sf, rtol=2e-11, atol=1e-6)
    np.testing.assert_allclose([cut[2] for cut in cuts], expected_bm, rtol=2e-11, atol=1e-4)


@pytest.mark.parametrize('seed', [13, 29, 47, 97])
def test_assessment_ratio_candidates_cover_independent_dense_envelope_oracle(seed):
    from stowage.checker.strength_assessment import assessment_cuts

    rng = np.random.default_rng(seed)
    segments = [(0., 100., 0., 0., 0.)]
    for _ in range(12):
        left, right = sorted(rng.uniform(0., 100., size=2))
        segments.append((left, right, *rng.uniform(-50., 50., size=3)))
    points = [(float(xx), float(force)) for xx, force in
              zip(rng.uniform(0., 100., size=8), rng.uniform(-1e5, 1e5, size=8))]
    xs = np.array([0., 17., 44., 69., 100.])
    capacities = 10**rng.uniform(3., 7., size=(len(xs), 4))
    capacities[:, (0, 2)] *= -1
    stations = [SimpleNamespace(x_m=xx, min_shear_n=values[0], max_shear_n=values[1],
        min_bending_nm=values[2], max_bending_nm=values[3]) for xx, values in zip(xs, capacities)]
    cuts = assessment_cuts(segments, points, stations)
    # Include every physical breakpoint and both point-load sides in addition
    # to 10,001 uniform samples. This oracle never solves derivative roots.
    dense_xs = np.unique([*np.linspace(0., 100., 10001), *xs,
        *(xx for left, right, *_ in segments for xx in (left, right)),
        *(xx for position, _ in points for xx in (np.nextafter(position, -np.inf), position))])
    dense_sf, dense_bm = strength._integrate_segments(dense_xs, segments, points)
    def maxima(locations, sf, bm):
        envelope = np.column_stack([np.interp(locations, xs, capacities[:, k]) for k in range(4)])
        return np.array([np.max(sf/np.where(sf >= 0., envelope[:, 1], envelope[:, 0])),
            np.max(bm/np.where(bm >= 0., envelope[:, 3], envelope[:, 2]))])
    dense_maxima = maxima(dense_xs, dense_sf, dense_bm)
    candidate_maxima = maxima([v[0] for v in cuts], np.array([v[1] for v in cuts]),
        np.array([v[2] for v in cuts]))
    assert np.all(candidate_maxima >= dense_maxima*(1-1e-10))
    np.testing.assert_allclose(candidate_maxima, dense_maxima, rtol=3e-5, atol=1e-8)


@pytest.mark.parametrize('kwargs', [
    {'lower_sf': np.nan}, {'upper_sf': np.inf}, {'lower_bm': 0.},
    {'envelope_xs': [0., 50., 50., 100.]},
    {'envelope_xs': [0., 50., 51.]},
    {'envelope_xs': [50., 51., 100.]},
    {'envelope_xs': [50.]},
], ids=['nan', 'infinite', 'wrong_sign', 'duplicate', 'uncovered_fore', 'uncovered_aft', 'one_station'])
def test_checker_fails_closed_for_invalid_or_incomplete_permissible_envelope(monkeypatch, kwargs):
    report = _beam(monkeypatch, **kwargs)
    assert not report.ran
    assert report.error
    assert report.violations
    assert all(v.rule_id != 'iacs.s11a.longitudinal_strength' for v in report.violations)
