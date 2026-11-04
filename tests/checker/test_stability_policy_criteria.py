"""Regression coverage for non-relaxable, dimensionally distinct Code limits."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.stability_criteria import evaluate_criteria


def criterion_condition(*, length=80., gm=.1, kg=4., phi_f=90., rule_overrides=None):
    hull = SimpleNamespace(lpp_m=length, beam_m=10., depth_m=8., bilge_keel=False,
                           windage_area_m2=100., windage_lever_m=5.)
    box = np.array([[-5., 0.], [5., 0.], [5., 8.], [-5., 8.]])
    geometry = SimpleNamespace(
        sections=[[box], [box]], x_m=length/2+np.array([-1., 1.])*length/(2*math.sqrt(3)),
        integration_weights_m=np.array([length/2]*2),
        submerged=lambda *a: SimpleNamespace(volume_m3=length*10*5, waterplane_area_m2=length*10))
    return evaluate_criteria(
        np.radians([0., 40., 90.]), np.array([0., 1., 0.]), gm_m=gm,
        hull=hull, geometry=geometry, displacement_kg=length*10*5*1025,
        kg_m=kg, draught_m=5., phi_f_rad=math.radians(phi_f),
        hull_params={'envelope': {'hatch_coamings': []}}, rule_overrides=rule_overrides)


@pytest.mark.parametrize('rule', [
    'gz_area_0_30', 'gz_area_0_40', 'gz_area_30_40', 'gz_at_30', 'max_gz_angle', 'initial_gm'])
def test_statutory_part_a_minimum_cannot_be_weakened(rule):
    with pytest.raises(ValueError, match='weaken.*Code minimum'):
        criterion_condition(rule_overrides={'iscode.a2_2.'+rule: {'minimum': 0.}})


@pytest.mark.parametrize('minimum', [float('nan'), float('inf'), -float('inf'), None, True, 'invalid'])
def test_statutory_minimum_invalid_policy_is_value_error(minimum):
    with pytest.raises(ValueError, match='criterion override'):
        criterion_condition(rule_overrides={'iscode.a2_2.initial_gm': {'minimum': minimum}})


@pytest.mark.parametrize('gm, expected_failure', [(.199, True), (.2, False), (.201, False)])
def test_tightened_initial_gm_has_inclusive_boundary(gm, expected_failure):
    _, margins, failures = criterion_condition(gm=gm,
        rule_overrides={'iscode.a2_2.initial_gm': {'minimum': .2}})
    assert margins['gm_m'] == pytest.approx(gm-.2)
    assert any(f[0] == 'iscode.a2_2.initial_gm' for f in failures) is expected_failure


def test_area_override_does_not_change_radian_downflooding_guard():
    _, margins, failures = criterion_condition(gm=1., phi_f=40.,
        rule_overrides={'iscode.a2_2.gz_area_0_30': {'minimum': .7}})
    assert margins['area_0_30_mrad'] < 0
    assert margins['downflooding_30_margin_rad'] == pytest.approx(math.radians(10.))
    assert not any('Downflooding' in f[-1] for f in failures)


@pytest.mark.parametrize('rule,length', [('iscode.a2_3.weather', 80.),
                                         ('iscode.b2_3.containership_second_check', 120.)])
def test_mixed_unit_rule_rejects_ambiguous_minimum(rule, length):
    with pytest.raises(ValueError, match='ambiguous.*minimum'):
        criterion_condition(length=length, rule_overrides={rule: {'minimum': 0.}})


def test_weather_ratio_and_radian_heel_margin_have_independent_limits():
    _, baseline, _ = criterion_condition(gm=1.)
    _, tightened, _ = criterion_condition(gm=1., rule_overrides={
        'iscode.a2_3.weather': {'minimum_ratio': 2., 'minimum_steady_heel_margin_rad': .01}})
    assert tightened['weather_ratio'] == pytest.approx(baseline['weather_ratio']-1.)
    assert tightened['weather_steady_heel_margin_rad'] == pytest.approx(
        baseline['weather_steady_heel_margin_rad']-.01)
    assert tightened['weather_table_applicability'] == baseline['weather_table_applicability']


@pytest.mark.parametrize('key, value', [('minimum_ratio', .9), ('minimum_steady_heel_margin_rad', -.01),
                                       ('minimum_ratio', float('nan'))])
def test_weather_typed_limits_cannot_weaken_or_be_nonfinite(key, value):
    with pytest.raises(ValueError, match='criterion override'):
        criterion_condition(rule_overrides={'iscode.a2_3.weather': {key: value}})


def test_weather_applicability_cannot_be_overridden():
    with pytest.raises(ValueError, match='applicability.*not overridable'):
        criterion_condition(kg=10., rule_overrides={
            'iscode.a2_3.weather': {'minimum_table_applicability': 0.}})


def test_containership_overrides_are_specific_to_each_area_or_lever():
    _, baseline, _ = criterion_condition(length=120., gm=1.)
    _, tightened, _ = criterion_condition(length=120., gm=1., rule_overrides={
        'iscode.b2_3.containership_second_check': {'minimum_gz_max_m': 2.}})
    assert tightened['containership_gz_max_m'] == -1.
    for key in baseline:
        if key != 'containership_gz_max_m':
            assert tightened[key] == baseline[key]


def test_containership_typed_minimum_cannot_weaken_code():
    with pytest.raises(ValueError, match='weaken.*Code minimum'):
        criterion_condition(length=120., rule_overrides={
            'iscode.b2_3.containership_second_check': {'minimum_area_0_30_mrad': 0.}})


@pytest.mark.parametrize('rule', ['iscode.a2_2.initial_gm', 'iscode.a2_3.weather',
                                 'iscode.b2_3.containership_second_check'])
@pytest.mark.parametrize('policy', [None, [], 'invalid', 0, False])
def test_criterion_rule_policy_must_be_a_mapping(rule, policy):
    with pytest.raises(ValueError, match='criterion override.*mapping'):
        criterion_condition(rule_overrides={rule: policy})


@pytest.mark.parametrize('overrides', [[], ['invalid'], '', 'invalid', 0, False])
def test_criterion_overrides_must_be_a_mapping(overrides):
    with pytest.raises(ValueError, match='criterion override.*mapping'):
        criterion_condition(rule_overrides=overrides)


@pytest.mark.parametrize('policy', [{'minimum': 0.}, None, []])
def test_public_checker_rejects_relaxed_statutory_gm_as_missing_policy(monkeypatch, policy):
    """The production entry point must pass overrides into the actual criteria."""
    from stowage.checker import stability, stability_curve
    from stowage.model import VesselFieldProvenance
    from stowage.model.fixtures import build_instance

    instance = build_instance()
    instance.vessel.fixed_loads = []
    # ASSUMPTION: this isolated box condition has an explicitly declared
    # synthetic loading manual. The independently specified endpoints contain
    # its 4.1e6 kg, zero-trim condition; no instance relaxation supplies them.
    bounds = {'min_trim_m': -2., 'max_trim_m': 2.,
              'min_displacement_kg': 4_000_000., 'max_displacement_kg': 4_200_000.,
              'bilge_keel_area_m2': 0.}
    evidence = {key+'_provenance': VesselFieldProvenance(
        assumption=f'Explicit synthetic box-test loading-manual declaration: {key}={value}')
        for key, value in bounds.items()}
    instance.vessel.hull = instance.vessel.hull.model_copy(update={
        'lpp_m': 80., 'beam_m': 10., 'depth_m': 8., 'bilge_keel': False,
        **bounds, **evidence})
    instance.rule_overrides = {'iscode.a2_2.initial_gm': policy}
    floating = SimpleNamespace(t_aft_m=5., t_fwd_m=5., trim_m=0., heel_rad=0.,
                               mean_draught_m=5., residual_norm=0.)
    box = np.array([[-5., 0.], [5., 0.], [5., 8.], [-5., 8.]])
    geometry = SimpleNamespace(
        openings=(), equilibrium=lambda *a: floating,
        full_submerged=lambda *a: SimpleNamespace(centroid_m=(40., 0., 2.5), bm_t_m=1.6,
                                                   volume_m3=4000., waterplane_area_m2=800.),
        waterplane_normal=lambda *a: np.array([0., 0., 1.]),
        sections=[[box], [box]], x_m=40+np.array([-1., 1.])*80/(2*math.sqrt(3)),
        integration_weights_m=np.array([40., 40.]))
    condition = SimpleNamespace(errors=(), mass_kg=4100000., lcg_m=40., tcg_m=0., kg_m=4., fsc_m=0.,
                                omitted_tanks=(), liquid_usage={})
    curve = SimpleNamespace(angles=np.radians([0., 40., 90.]), gz=np.array([0., 1., 0.]),
        negative_gz=np.array([0., -1., 0.]), drafts=np.full((3, 2), 5.),
        flooding_angle=math.pi/2, at_angle=lambda a: (1., (5., 5.)))
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '2')
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    monkeypatch.setattr(stability, '_context', lambda v: (geometry, {'envelope': {'hatch_coamings': []}}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *a, **k: condition)
    monkeypatch.setattr(stability, '_deck_edge_angle', lambda *a, **k: .5)
    monkeypatch.setattr(stability, '_refine_peak', lambda *a: (None, None))
    monkeypatch.setattr(stability, '_windage', lambda *a: (100., 5.))
    monkeypatch.setattr(stability_curve, 'build_stability_curve', lambda *a, **k: curve)
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'initial_gm' in report.error
    assert ('weaken' if isinstance(policy, dict) else 'mapping') in report.error
