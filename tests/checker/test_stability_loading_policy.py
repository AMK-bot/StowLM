"""Loading-manual policy must be explicit and checked as signed SI limits."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability, stability_criteria, stability_curve
from stowage.checker.stability_loading import LoadingCondition
from stowage.model import VesselFieldProvenance
from stowage.model.fixtures import build_instance


@pytest.fixture
def policy_instance():
    instance = build_instance()
    for field, value in (('min_trim_m', -.5), ('max_trim_m', 2.),
                         ('min_displacement_kg', 8_000_000.), ('max_displacement_kg', 10_000_000.)):
        setattr(instance.vessel.hull, field, value)
        setattr(instance.vessel.hull, field+'_provenance',
                VesselFieldProvenance(assumption='Synthetic test loading manual'))
    instance.vessel.hull.bilge_keel_area_m2 = 0.
    instance.vessel.hull.bilge_keel_area_m2_provenance = VesselFieldProvenance(assumption='Synthetic no-keels test hull')
    # ASSUMPTION: independent synthetic loading-manual policy for this test;
    # these values are not inferred from its hydrostatic table.
    instance.rule_overrides = {
        'iscode.loading.trim': {'minimum': -.5},
        'iscode.loading.displacement': {'minimum': 8_000_000., 'maximum': 10_000_000.},
    }
    return instance


def _computed_report(monkeypatch, instance, *, trim=0., mass=9_000_000., expect_ran=True):
    """Exercise public loading checks with independently supplied solver results."""
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '2')
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    floating = SimpleNamespace(t_aft_m=5.+trim/2, t_fwd_m=5.-trim/2,
        mean_draught_m=5., trim_m=trim, heel_rad=0., residual_norm=0.)
    geometry = SimpleNamespace(
        equilibrium=lambda *args: floating, openings=[],
        full_submerged=lambda *args: SimpleNamespace(centroid_m=(60., 0., 4.), bm_t_m=2.),
        waterplane_normal=lambda *args: np.array([0., 0., 1.]))
    monkeypatch.setattr(stability, '_context', lambda vessel: (geometry, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kwargs:
        LoadingCondition(mass, 60., 0., 4., 0., 0.))
    angles = np.array([0., .5, 1.])
    curve = SimpleNamespace(angles=angles, gz=np.array([0., .5, .6]),
        negative_gz=np.array([0., -.5, -.6]), drafts=np.full((3, 2), 5.),
        flooding_angle=1., at_angle=lambda phi: (.5, (5., 5.)))
    monkeypatch.setattr(stability_curve, 'build_stability_curve', lambda *args, **kwargs: curve)
    monkeypatch.setattr(stability, '_deck_edge_angle', lambda *args, **kwargs: .5)
    monkeypatch.setattr(stability, '_windage', lambda *args: (100., 5.))
    monkeypatch.setattr(stability_criteria, 'evaluate_criteria', lambda *args, **kwargs: ({}, {}, []))
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    if expect_ran:
        assert report.ran, report.error
    return report


def test_signed_trim_rejects_asymmetric_lower_limit(monkeypatch, policy_instance):
    report = _computed_report(monkeypatch, policy_instance, trim=-1.)
    broken = [v for v in report.violations if v.rule_id == 'iscode.loading.trim']
    assert len(broken) == 1
    assert broken[0].measured == -1.
    assert broken[0].limit == -.5
    assert report.margins['trim_min_m'] == -.5


@pytest.mark.parametrize('trim', [-.5, 2.])
def test_signed_trim_boundaries_are_inclusive(monkeypatch, policy_instance, trim):
    report = _computed_report(monkeypatch, policy_instance, trim=trim)
    assert not any(v.rule_id == 'iscode.loading.trim' for v in report.violations)


@pytest.mark.parametrize('policy', [{}, {'minimum': None}, {'minimum': float('nan')},
    {'minimum': float('-inf')}, {'minimum': 'invalid'}, {'minimum': True},
    {'minimum': 3.}, {'minimum': -.5, 'maximum': 3.},
    {'minimum': -.5, 'maximum': float('inf')}])
def test_missing_or_invalid_trim_policy_fails_closed(monkeypatch, policy_instance, policy):
    if 'minimum' not in policy:
        policy_instance.vessel.hull.min_trim_m = None
    policy_instance.rule_overrides['iscode.loading.trim'] = policy
    monkeypatch.setattr(stability, '_context', lambda vessel: (_ for _ in ()).throw(
        AssertionError('invalid policy must fail before geometry')))
    report = stability.check_stability(policy_instance, policy_instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'iscode.loading.trim' in report.error


def test_trim_maximum_override_can_tighten(monkeypatch, policy_instance):
    policy_instance.rule_overrides['iscode.loading.trim']['maximum'] = 1.
    report = _computed_report(monkeypatch, policy_instance, trim=1.5)
    assert any(v.rule_id == 'iscode.loading.trim' and v.limit == 1. for v in report.violations)


@pytest.mark.parametrize('rule', ['iscode.loading.trim', 'iscode.loading.displacement'])
def test_absent_loading_policy_rule_fails_closed(monkeypatch, policy_instance, rule):
    del policy_instance.rule_overrides[rule]
    policy_instance.vessel.hull.min_trim_m = None if rule == 'iscode.loading.trim' else -.5
    if rule == 'iscode.loading.displacement':
        policy_instance.vessel.hull.min_displacement_kg = None
    monkeypatch.setattr(stability, '_context', lambda vessel: (_ for _ in ()).throw(
        AssertionError('absent policy must fail before geometry')))
    report = stability.check_stability(policy_instance, policy_instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert rule in report.error


@pytest.mark.parametrize('malformed', [None, [], 'invalid'])
@pytest.mark.parametrize('rule', [None, 'iscode.loading.trim', 'iscode.loading.displacement'])
def test_malformed_loading_policy_mapping_fails_closed(monkeypatch, policy_instance, malformed, rule):
    # Imported/mutated instances can bypass the model's original validation.
    if rule is None:
        object.__setattr__(policy_instance, 'rule_overrides', malformed)
    else:
        policy_instance.rule_overrides[rule] = malformed
    monkeypatch.setattr(stability, '_context', lambda vessel: (_ for _ in ()).throw(
        AssertionError('malformed policy must fail before geometry')))
    report = stability.check_stability(policy_instance, policy_instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert (rule or 'rule_overrides') in report.error


def test_nonfinite_hull_trim_maximum_fails_closed(monkeypatch, policy_instance):
    policy_instance.vessel.hull = policy_instance.vessel.hull.model_copy(update={'max_trim_m': float('nan')})
    policy_instance.rule_overrides['iscode.loading.trim']['maximum'] = 1.
    report = stability.check_stability(policy_instance, policy_instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'max_trim_m must be finite' in report.error


@pytest.mark.parametrize('policy', [{}, {'minimum': 8_000_000.}, {'maximum': 10_000_000.},
    {'minimum': 10_000_000., 'maximum': 8_000_000.},
    {'minimum': None, 'maximum': 10_000_000.},
    {'minimum': 'invalid', 'maximum': 10_000_000.},
    {'minimum': float('nan'), 'maximum': 10_000_000.},
    {'minimum': 8_000_000., 'maximum': float('inf')},
    {'minimum': False, 'maximum': 10_000_000.}])
def test_missing_or_invalid_displacement_policy_fails_closed(monkeypatch, policy_instance, policy):
    for key, field in (('minimum', 'min_displacement_kg'), ('maximum', 'max_displacement_kg')):
        if key not in policy:
            setattr(policy_instance.vessel.hull, field, None)
    policy_instance.rule_overrides['iscode.loading.displacement'] = policy
    monkeypatch.setattr(stability, '_context', lambda vessel: (_ for _ in ()).throw(
        AssertionError('invalid policy must fail before geometry')))
    report = stability.check_stability(policy_instance, policy_instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'iscode.loading.displacement' in report.error


@pytest.mark.parametrize('mass', [8_000_000., 10_000_000.])
def test_displacement_boundaries_are_inclusive(monkeypatch, policy_instance, mass):
    report = _computed_report(monkeypatch, policy_instance, mass=mass)
    assert not any(v.rule_id == 'iscode.loading.displacement' for v in report.violations)


@pytest.mark.parametrize(('mass', 'limit'), [(7_999_999., 8_000_000.), (10_000_001., 10_000_000.)])
def test_both_displacement_limits_are_enforced(monkeypatch, policy_instance, mass, limit):
    report = _computed_report(monkeypatch, policy_instance, mass=mass)
    assert any(v.rule_id == 'iscode.loading.displacement' and v.limit == limit
               for v in report.violations)


def test_equal_loading_policy_bounds_are_inclusive(monkeypatch, policy_instance):
    policy_instance.rule_overrides['iscode.loading.trim'] = {'minimum': 0., 'maximum': 0.}
    policy_instance.rule_overrides['iscode.loading.displacement'] = {'minimum': 9_000_000., 'maximum': 9_000_000.}
    report = _computed_report(monkeypatch, policy_instance)
    assert not any(v.rule_id in {'iscode.loading.trim', 'iscode.loading.displacement'}
                   for v in report.violations)


@pytest.mark.parametrize(('trim', 'maximum'), [(-.49, 2.), (.99, 1.)])
def test_table_guard_covers_both_signed_trim_boundaries(policy_instance, trim, maximum):
    policy_instance.rule_overrides['iscode.loading.trim']['maximum'] = maximum
    floating = SimpleNamespace(t_aft_m=5., t_fwd_m=5., trim_m=trim, heel_rad=0.)
    assert stability._floating_near_limit(policy_instance, floating)


def test_table_guard_without_policy_remains_a_conservative_boolean(policy_instance):
    # Solver probes also use this numerical guard before a final checker call.
    # Missing policy cannot approve a state, but must not escape as an exception.
    policy_instance.rule_overrides = {}
    policy_instance.vessel.hull.min_trim_m = None
    floating = SimpleNamespace(t_aft_m=5., t_fwd_m=5., trim_m=0., heel_rad=0.)
    assert stability._floating_near_limit(policy_instance, floating) is True


POLICY_FIELDS = (
    ('min_trim_m', 'iscode.loading.trim', 'minimum', -.5, -.25, -.75),
    ('max_trim_m', 'iscode.loading.trim', 'maximum', 2., 1.5, 2.5),
    ('min_displacement_kg', 'iscode.loading.displacement', 'minimum', 8_000_000., 8_500_000., 7_500_000.),
    ('max_displacement_kg', 'iscode.loading.displacement', 'maximum', 10_000_000., 9_500_000., 10_500_000.),
)


@pytest.fixture
def hull_policy_instance(policy_instance):
    hull = policy_instance.vessel.hull
    for field, _, _, value, _, _ in POLICY_FIELDS:
        setattr(hull, field, value)
        setattr(hull, field+'_provenance', VesselFieldProvenance(source='Independent synthetic test manual'))
    policy_instance.rule_overrides = {}
    return policy_instance


@pytest.mark.parametrize(('trim', 'mass', 'rule'), [(-.5, 9_000_000., None), (2., 9_000_000., None),
    (-.500001, 9_000_000., 'iscode.loading.trim'), (2.000001, 9_000_000., 'iscode.loading.trim'),
    (0., 8_000_000., None), (0., 10_000_000., None),
    (0., 7_999_999.999999, 'iscode.loading.displacement'), (0., 10_000_000.000001, 'iscode.loading.displacement')])
def test_d40_hull_only_policy_boundaries(monkeypatch, hull_policy_instance, trim, mass, rule):
    report = _computed_report(monkeypatch, hull_policy_instance, trim=trim, mass=mass)
    loading = [v for v in report.violations if v.rule_id in {'iscode.loading.trim', 'iscode.loading.displacement'}]
    assert [v.rule_id for v in loading] == ([] if rule is None else [rule])


@pytest.mark.parametrize(('field', 'rule', 'key', 'baseline', 'tight', 'loose'), POLICY_FIELDS)
def test_d40_each_override_can_only_tighten(hull_policy_instance, field, rule, key, baseline, tight, loose):
    hull_policy_instance.rule_overrides = {rule: {key: tight}}
    limits = stability._loading_policy(hull_policy_instance)
    assert limits[[row[0] for row in POLICY_FIELDS].index(field)] == tight
    hull_policy_instance.rule_overrides[rule][key] = loose
    with pytest.raises(ValueError, match='must not weaken'):
        stability._loading_policy(hull_policy_instance)


@pytest.mark.parametrize(('field', 'rule', 'key', 'baseline', 'tight', 'loose'), POLICY_FIELDS)
@pytest.mark.parametrize('outside', [False, True])
def test_d40_tightened_override_is_enforced_at_boundary(monkeypatch, hull_policy_instance,
        field, rule, key, baseline, tight, loose, outside):
    hull_policy_instance.rule_overrides = {rule: {key: tight}}
    measured = tight + ((-1e-6 if key == 'minimum' else 1e-6) if outside else 0.)
    report = _computed_report(monkeypatch, hull_policy_instance,
        trim=measured if rule == 'iscode.loading.trim' else 0.,
        mass=measured if rule == 'iscode.loading.displacement' else 9_000_000.)
    broken = [v for v in report.violations if v.rule_id == rule]
    assert len(broken) == int(outside)
    if outside:
        assert broken[0].measured == measured
        assert broken[0].limit == tight


@pytest.mark.parametrize(('field', 'rule', 'key', 'baseline', 'tight', 'loose'), POLICY_FIELDS)
def test_d40_missing_endpoint_fails_even_with_explicit_override(monkeypatch, hull_policy_instance, field, rule, key, baseline, tight, loose):
    setattr(hull_policy_instance.vessel.hull, field, None)
    setattr(hull_policy_instance.vessel.hull, field+'_provenance', None)
    report = stability.check_stability(hull_policy_instance, hull_policy_instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert field in report.error
    hull_policy_instance.rule_overrides = {rule: {key: baseline}}
    report = _computed_report(monkeypatch, hull_policy_instance, expect_ran=False)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert field in report.error


def test_unknown_trim_baseline_cannot_be_replaced_by_permissive_instance_policy(monkeypatch, hull_policy_instance):
    hull_policy_instance.vessel.hull.min_trim_m = None
    hull_policy_instance.vessel.hull.min_trim_m_provenance = None
    hull_policy_instance.rule_overrides = {'iscode.loading.trim': {'minimum': -100.}}
    report = _computed_report(monkeypatch, hull_policy_instance, trim=-1., expect_ran=False)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'min_trim_m' in report.error


@pytest.mark.parametrize(('field', 'rule', 'key', 'baseline', 'tight', 'loose'), POLICY_FIELDS)
@pytest.mark.parametrize('evidence', [None, {'source': ''}, {'assumption': '  '}, {}])
def test_d40_hull_policy_requires_evidence(hull_policy_instance, field, rule, key, baseline, tight, loose, evidence):
    object.__setattr__(hull_policy_instance.vessel.hull, field+'_provenance', evidence)
    # A tighter override still relies on this baseline as the permitted envelope.
    hull_policy_instance.rule_overrides = {rule: {key: tight}}
    with pytest.raises(ValueError, match='provenance'):
        stability._loading_policy(hull_policy_instance)


@pytest.mark.parametrize(('field', 'rule', 'key', 'baseline', 'tight', 'loose'), POLICY_FIELDS)
@pytest.mark.parametrize('bad', [True, float('inf'), float('nan'), 'invalid', '1', pytest.param(10**1000, id='overflow')])
def test_d40_invalid_hull_policy_fails_closed(hull_policy_instance, field, rule, key, baseline, tight, loose, bad):
    object.__setattr__(hull_policy_instance.vessel.hull, field, bad)
    with pytest.raises(ValueError, match=field):
        stability._loading_policy(hull_policy_instance)


@pytest.mark.parametrize('field', ['min_displacement_kg', 'max_displacement_kg'])
@pytest.mark.parametrize('bad', [0., -1.])
def test_d40_displacement_policy_must_be_positive(hull_policy_instance, field, bad):
    object.__setattr__(hull_policy_instance.vessel.hull, field, bad)
    with pytest.raises(ValueError, match='positive'):
        stability._loading_policy(hull_policy_instance)


@pytest.mark.parametrize(('rule', 'key'), [('iscode.loading.displacement', 'minimum'),
    ('iscode.loading.displacement', 'maximum')])
@pytest.mark.parametrize('bad', [0., -1.])
def test_d40_displacement_override_must_be_positive(hull_policy_instance, rule, key, bad):
    hull_policy_instance.rule_overrides = {rule: {key: bad}}
    with pytest.raises(ValueError, match='positive'):
        stability._loading_policy(hull_policy_instance)


@pytest.mark.parametrize(('minimum', 'maximum'), [(-2., -.5), (.5, 2.), (1., 1.)])
def test_d40_signed_trim_baselines_need_no_symmetry(hull_policy_instance, minimum, maximum):
    hull_policy_instance.vessel.hull = hull_policy_instance.vessel.hull.model_copy(
        update={'min_trim_m': minimum, 'max_trim_m': maximum})
    assert stability._loading_policy(hull_policy_instance)[:2] == (minimum, maximum)


@pytest.mark.parametrize('fields', [('min_trim_m', 'max_trim_m'), ('min_displacement_kg', 'max_displacement_kg')])
def test_d40_reversed_hull_policy_fails_closed(hull_policy_instance, fields):
    low, high = fields
    object.__setattr__(hull_policy_instance.vessel.hull, low, getattr(hull_policy_instance.vessel.hull, high)+1.)
    with pytest.raises(ValueError, match='minimum must not exceed maximum'):
        stability._loading_policy(hull_policy_instance)
