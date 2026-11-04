"""Live numerical providers remain authoritative after warming checker helpers."""
import pytest

from stowage.checker import stability, visibility
from stowage.checker.perf import evaluation_cache
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.model import ShipState
from tests.checker.test_full_cases import _reference_prism
from tests.checker.test_stability_policy_fixtures import build_instance
from tests.checker.test_stability_review import balanced_feeder


def test_warmed_direct_condition_does_not_bypass_equilibrium_failure(monkeypatch):
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1')
    instance = balanced_feeder(half_ballast=True)
    warm = stability.check_stability(instance, instance.onboard, call_index=0)
    assert warm.ran, warm.error
    calls = []

    def failed(*args, **kwargs):
        calls.append(True)
        raise ValueError('injected warmed equilibrium failure')

    monkeypatch.setattr(StabilityGeometry, 'equilibrium', failed)
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert calls
    assert not report.ran
    assert 'injected warmed equilibrium failure' in report.error
    assert {violation.rule_id for violation in report.violations} == {'checker.internal_error'}


def test_scalar_geometry_provider_requires_no_private_visibility_payload(monkeypatch):
    instance = build_instance()
    solid = _reference_prism()
    assert not hasattr(solid, '_visibility_parameter_payload')
    instance.vessel.hull = instance.vessel.hull.model_copy(update=dict(
        lpp_m=100., beam_m=12., depth_m=8.,
        hull_offsets_ref='params/fleet/independent_prism.toml',
        lightship_mass_kg=2_460_000., lightship_lcg_m=50.,
        lightship_tcg_m=0., lightship_vcg_m=2.))
    instance.vessel.tanks = []
    instance.vessel.fixed_loads = []
    instance.port.water_density_kg_m3 = 1025.
    ship = ShipState(vessel_id=instance.vessel.id)
    monkeypatch.setattr(visibility, '_geometry', lambda name: (solid, {}))
    # The rejected whole-condition memo failed here before doing any physics.
    with evaluation_cache():
        for _ in range(2):
            condition = visibility._loading_condition(instance, ship)
            assert condition.mass_kg == 2_460_000.
            assert condition.t_aft_m == pytest.approx(2., abs=1e-9)
            assert condition.t_fwd_m == pytest.approx(2., abs=1e-9)
            assert condition.gm_m == pytest.approx(5., abs=1e-9)
