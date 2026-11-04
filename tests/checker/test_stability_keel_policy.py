"""Weather-keel input regressions at the helper and public-checker boundaries."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability, stability_criteria, stability_curve
from stowage.checker.stability_loading import LoadingCondition
from stowage.model import VesselFieldProvenance
from tests.checker.test_stability_criteria import condition
from tests.checker.test_stability_review import balanced_feeder


@pytest.mark.parametrize('bilge_keel', [False, True])
@pytest.mark.parametrize('area_m2', [math.nan, math.inf, -math.inf, -1.])
def test_weather_keel_area_must_be_finite_and_nonnegative(area_m2, bilge_keel):
    # IS Code Part A 2.3.4, PDF p.15 (printed p.14), defines Ak as
    # "total overall area of bilge keels" (or projected bar-keel area).
    # Nonfinite values cannot select a physical table 2.3.4-3 roll factor.
    with pytest.raises(ValueError, match='keel area'):
        condition(bilge_keel=bilge_keel, weather_bilge_keel_area_m2=area_m2)


@pytest.mark.parametrize('bilge_keel', [False, True])
@pytest.mark.parametrize('sharp_bilge', [False, True])
@pytest.mark.parametrize('area_ratio,expected_k', [(0., 1.), (2.25, .835)])
def test_explicit_keel_area_controls_roll_factor(area_ratio, expected_k, sharp_bilge, bilge_keel):
    # D40's explicit area is authoritative; the legacy flag cannot supply or
    # override it. Table 2.3.4-3, PDF p.16, gives k=1 at Ak=0.
    metrics, _, _ = condition(bilge_keel=bilge_keel, sharp_bilge=sharp_bilge,
                              weather_bilge_keel_area_m2=area_ratio*80.*10./100.)
    assert metrics['weather_k'] == pytest.approx(expected_k)


def _keel_instance(monkeypatch, area_m2, bilge_keel):
    instance = balanced_feeder()
    context = stability._context(instance.vessel)
    # This invented keel/manual policy is a foreign synthetic test vessel,
    # not a rewrite of the canonical fleet loading manual. Inject only its
    # physical geometry; no production identity or params source pin is kept.
    instance.vessel.id = 'synthetic_keel_policy'
    instance.vessel.slice_of = None
    instance.vessel.sources = ['Synthetic keel-policy test with independently injected geometry']
    instance.onboard.vessel_id = instance.vessel.id
    monkeypatch.setattr(stability, '_context', lambda vessel: context)
    hull = instance.vessel.hull
    # ASSUMPTION: broad synthetic test manual isolates the keel path from
    # operating-envelope failures. No production load-line evidence is claimed.
    evidence = VesselFieldProvenance(assumption='Synthetic keel-path test policy')
    for key, value in [('min_trim_m', -10.), ('max_trim_m', 10.),
                       ('min_displacement_kg', 1.), ('max_displacement_kg', 1e9),
                       ('bilge_keel_area_m2', area_m2)]:
        setattr(hull, key, value)
        setattr(hull, key+'_provenance', evidence)
    hull.bilge_keel = bilge_keel
    instance.rule_overrides = {}
    return instance


@pytest.mark.parametrize('bilge_keel', [False, True])
def test_public_checker_bilge_keel_without_area_fails_closed(monkeypatch, bilge_keel):
    instance = _keel_instance(monkeypatch, None, bilge_keel)
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert report.error is not None and 'bilge' in report.error.lower()
    assert {violation.rule_id for violation in report.violations} == {
        'checker.missing_vessel_data',
    }


def test_public_checker_keel_area_without_evidence_fails_closed(monkeypatch):
    instance = _keel_instance(monkeypatch, 0., False)
    instance.vessel.hull.bilge_keel_area_m2_provenance = None
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert report.error is not None and 'bilge' in report.error.lower()
    assert {violation.rule_id for violation in report.violations} == {
        'checker.missing_vessel_data',
    }


@pytest.mark.parametrize('area_ratio', [0., 2.25])
def test_public_checker_declared_keel_area_reaches_weather(monkeypatch, area_ratio):
    instance = _keel_instance(monkeypatch, 0., True)
    hull = instance.vessel.hull
    # Pick a physical area; the Code table uses actual LWL, including overhang,
    # rather than Lpp. The reported LWL places this area between table rows2/2.5.
    hull.bilge_keel_area_m2 = area_ratio*hull.lpp_m*hull.beam_m/100.
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    expected_k = 1.
    if area_ratio:
        ratio = hull.bilge_keel_area_m2*100./(
            report.metrics['weather_waterline_length_m']*hull.beam_m)
        assert 2. < ratio < 2.5
        expected_k = .88+(ratio-2.)*(.79-.88)/.5
    assert report.metrics['weather_k'] == pytest.approx(expected_k)


@pytest.mark.parametrize('override', [0., 10., 20.])
def test_public_checker_weather_keel_override_reaches_criteria(monkeypatch, override):
    instance = _keel_instance(monkeypatch, 20., False)
    instance.rule_overrides = {'iscode.a2_3.weather': {'bilge_keel_area_m2': override}}
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '2')
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    floating = SimpleNamespace(t_aft_m=5., t_fwd_m=5., mean_draught_m=5.,
                               trim_m=0., heel_rad=0., residual_norm=0.)
    geometry = SimpleNamespace(equilibrium=lambda *args: floating, openings=[],
        full_submerged=lambda *args: SimpleNamespace(centroid_m=(60., 0., 4.), bm_t_m=2.),
        waterplane_normal=lambda *args: np.array([0., 0., 1.]))
    monkeypatch.setattr(stability, '_context', lambda vessel: (geometry, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kwargs:
                        LoadingCondition(9_000_000., 60., 0., 4., 0., 0.))
    curve = SimpleNamespace(angles=np.array([0., .5, 1.]), gz=np.array([0., .5, .6]),
        negative_gz=np.array([0., -.5, -.6]), drafts=np.full((3, 2), 5.),
        flooding_angle=1., at_angle=lambda phi: (.5, (5., 5.)))
    monkeypatch.setattr(stability_curve, 'build_stability_curve', lambda *args, **kwargs: curve)
    monkeypatch.setattr(stability, '_deck_edge_angle', lambda *args, **kwargs: .5)
    monkeypatch.setattr(stability, '_windage', lambda *args: (100., 5.))
    captured = {}
    def criteria(*args, **kwargs):
        captured.update(kwargs)
        return {}, {}, []
    monkeypatch.setattr(stability_criteria, 'evaluate_criteria', criteria)
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    assert captured['weather_bilge_keel_area_m2'] == override


@pytest.mark.parametrize('policy', [None, [], {'bilge_keel_area_m2': 21.},
    {'bilge_keel_area_m2': -1.}, {'bilge_keel_area_m2': math.nan},
    {'bilge_keel_area_m2': math.inf}, {'bilge_keel_area_m2': -math.inf},
    {'bilge_keel_area_m2': True}, {'bilge_keel_area_m2': '0'},
    {'bilge_keel_area_m2': None}])
def test_public_checker_weather_keel_override_invalid_fails_early(monkeypatch, policy):
    instance = _keel_instance(monkeypatch, 20., False)
    instance.rule_overrides = {'iscode.a2_3.weather': policy}
    monkeypatch.setattr(stability, '_context', lambda vessel: (_ for _ in ()).throw(
        AssertionError('invalid keel policy must fail before geometry')))
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'iscode.a2_3.weather' in report.error


@pytest.mark.parametrize('missing', ['area', 'evidence'])
def test_public_checker_weather_keel_override_cannot_supply_unknown_baseline(monkeypatch, missing):
    instance = _keel_instance(monkeypatch, None if missing == 'area' else 20., False)
    if missing == 'evidence':
        instance.vessel.hull.bilge_keel_area_m2_provenance = None
    instance.rule_overrides = {'iscode.a2_3.weather': {'bilge_keel_area_m2': 0.}}
    monkeypatch.setattr(stability, '_context', lambda vessel: (_ for _ in ()).throw(
        AssertionError('unknown baseline must fail before geometry')))
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'bilge' in report.error.lower()


def test_public_checker_reduced_keel_area_increases_weather_roll(monkeypatch):
    instance = _keel_instance(monkeypatch, 20., False)
    original = stability.check_stability(instance, instance.onboard, call_index=0)
    assert original.ran, original.error
    instance.rule_overrides = {'iscode.a2_3.weather': {'bilge_keel_area_m2': 0.}}
    tightened = stability.check_stability(instance, instance.onboard, call_index=0)
    assert tightened.ran, tightened.error
    assert original.metrics['weather_k'] < 1.
    assert tightened.metrics['weather_k'] == 1.
    assert tightened.metrics['weather_phi1_rad'] > original.metrics['weather_phi1_rad']
