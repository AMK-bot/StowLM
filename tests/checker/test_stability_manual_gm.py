"""A loading-manual GM minimum survives a looser instance request."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability, stability_criteria, stability_curve
from stowage.checker.stability_loading import LoadingCondition
from tests.checker.test_stability_policy_fixtures import build_instance


_ABSENT = object()


def manual_report(monkeypatch, *, hull_minimum=3., override=_ABSENT, gm=2., policy=_ABSENT):
    instance = build_instance()
    # Supply a complete evidenced vessel baseline; only GM varies in this test.
    # Assignment/model_copy can import invalid data, so public consumption must
    # validate policy even after model construction.
    instance.vessel.hull = instance.vessel.hull.model_copy(update={'min_gm_m': hull_minimum})
    if override is not _ABSENT:
        instance.rule_overrides['iscode.loading.gm'] = {'minimum_gm': override}
    if policy is not _ABSENT:
        instance.rule_overrides['iscode.loading.gm'] = policy
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '2')
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    floating = SimpleNamespace(t_aft_m=5., t_fwd_m=5., mean_draught_m=5.,
                               trim_m=0., heel_rad=0., residual_norm=0.)
    geometry = SimpleNamespace(equilibrium=lambda *args: floating, openings=[],
        full_submerged=lambda *args: SimpleNamespace(centroid_m=(60., 0., 4.), bm_t_m=gm),
        waterplane_normal=lambda *args: np.array([0., 0., 1.]))
    monkeypatch.setattr(stability, '_context', lambda vessel: (geometry, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *a, **k:
                        LoadingCondition(9_000_000., 60., 0., 4., 0., 0.))
    curve = SimpleNamespace(angles=np.array([0., .5, 1.]), gz=np.array([0., .5, .6]),
        negative_gz=np.array([0., -.5, -.6]), drafts=np.full((3, 2), 5.),
        flooding_angle=1., at_angle=lambda phi: (.5, (5., 5.)))
    monkeypatch.setattr(stability_curve, 'build_stability_curve', lambda *a, **k: curve)
    monkeypatch.setattr(stability, '_deck_edge_angle', lambda *a, **k: .5)
    monkeypatch.setattr(stability, '_windage', lambda *a: (100., 5.))
    # Isolate the independent manual-GM row; statutory criteria have their own
    # integration suite and may not mask loss of the vessel's stricter floor.
    monkeypatch.setattr(stability_criteria, 'evaluate_criteria', lambda *a, **k: ({}, {}, []))
    return stability.check_stability(instance, instance.onboard, call_index=0)


@pytest.mark.parametrize('override', [_ABSENT, .15, .5, 3., 4.])
def test_public_manual_gm_uses_strongest_floor(monkeypatch, override):
    report = manual_report(monkeypatch, override=override)
    assert report.ran, report.error
    expected = 4. if override == 4. else 3.
    failures = [v for v in report.violations if v.rule_id == 'iscode.loading.gm']
    assert len(failures) == 1
    assert failures[0].measured == 2.
    assert failures[0].limit == expected
    assert report.margins['gm_m'] == 2.-expected


@pytest.mark.parametrize('override', [_ABSENT, .5, 2.])
def test_public_manual_gm_equality_is_inclusive(monkeypatch, override):
    report = manual_report(monkeypatch, hull_minimum=2., override=override)
    assert report.ran, report.error
    assert report.margins['gm_m'] == 0.
    assert not any(v.rule_id == 'iscode.loading.gm' for v in report.violations)


@pytest.mark.parametrize('hull_minimum', [-1., .1, .15])
def test_public_manual_gm_never_drops_below_statutory_floor(monkeypatch, hull_minimum):
    report = manual_report(monkeypatch, hull_minimum=hull_minimum, override=0., gm=.1)
    assert report.ran, report.error
    failure = next(v for v in report.violations if v.rule_id == 'iscode.loading.gm')
    assert failure.limit == .15
    assert report.margins['gm_m'] == pytest.approx(-.05)


@pytest.mark.parametrize('override', [None, True, False, float('nan'), float('inf'),
                                      -float('inf'), '3', 'invalid', [], {}])
def test_public_manual_gm_invalid_override_fails_closed(monkeypatch, override):
    report = manual_report(monkeypatch, override=override)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'iscode.loading.gm.minimum_gm' in report.error


@pytest.mark.parametrize('hull_minimum', [None, True, False, float('nan'), float('inf'),
                                         -float('inf'), '3', 'invalid', [], {}])
def test_public_manual_gm_invalid_hull_baseline_fails_closed(monkeypatch, hull_minimum):
    report = manual_report(monkeypatch, hull_minimum=hull_minimum, override=4.)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'vessel.hull.min_gm_m' in report.error


@pytest.mark.parametrize('policy', [None, [], 'invalid', True, 3.])
def test_public_manual_gm_malformed_policy_mapping_fails_closed(monkeypatch, policy):
    report = manual_report(monkeypatch, policy=policy)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert 'iscode.loading.gm' in report.error and 'mapping' in report.error
