"""A new measured margin must replace any already assembled cached policy."""
from types import SimpleNamespace as NS

import pytest


@pytest.fixture
def assembly(monkeypatch):
    import stowage.linear.physics as physics
    import stowage.linear.cache as cache
    import stowage.linear.family_calibration as calibration
    import stowage.linear.margin_policy as policy
    import stowage.linear.reusable as reusable
    from stowage import versions
    physics._CACHE.clear()
    state = {'policy': 'first-measured-envelope', 'calibration_calls': 0}
    monkeypatch.setattr(physics, '_params_for', lambda instance: ('test', {'tanks': []}))
    monkeypatch.setattr(cache, 'model_cache_key', lambda *args: 'same-physical-instance')
    monkeypatch.setattr(calibration, 'reference_inputs', lambda parameters: {'method': 1})
    monkeypatch.setattr(calibration, 'reference_inputs_cached', lambda parameters: calibration.reference_inputs(parameters))
    monkeypatch.setattr(calibration, 'synchronize_reference', lambda reference: None)
    monkeypatch.setattr(policy, 'policy_inputs', lambda: {'content': state['policy']})
    monkeypatch.setattr(reusable, 'coefficient_family', lambda *args, **kwargs: object())
    def calibrated(*args, **kwargs):
        state['calibration_calls'] += 1
        return object(), {'current_envelope': state['policy']}
    monkeypatch.setattr(calibration, 'calibrated_family', calibrated)
    monkeypatch.setattr(reusable, 'assemble_instance', lambda family, instance, band, errors, diagnostics:
        NS(version=versions.LINEAR_MODEL_VERSION, measurement_diagnostics=diagnostics,
           private_safety_metadata='must survive a provenance-only refresh'))
    instance = NS(vessel=NS(hull=object()), onboard=NS(covers_off=[]), rule_overrides={})
    yield physics, instance, object(), state
    physics._CACHE.clear()


def test_changed_measured_policy_replaces_assembled_model(assembly):
    physics, instance, band, state = assembly
    first = physics.build_model(instance, band)
    assert physics.build_model(instance, band) is first
    assert state['calibration_calls'] == 1
    state['policy'] = 'larger-measured-unsafe-envelope'
    replacement = physics.build_model(instance, band)
    assert replacement is not first
    assert replacement.measurement_diagnostics['current_envelope'] == state['policy']
    assert state['calibration_calls'] == 2


def test_component_provenance_refresh_keeps_physics_and_private_metadata(assembly, monkeypatch):
    from stowage import versions
    physics, instance, band, state = assembly
    first = physics.build_model(instance, band)
    monkeypatch.setattr(versions, 'LINEAR_MODEL_VERSION', 'test-metadata-only')
    current = physics.build_model(instance, band)
    assert current.version == 'test-metadata-only'
    assert current.private_safety_metadata == first.private_safety_metadata
    assert current.measurement_diagnostics is first.measurement_diagnostics
    assert state['calibration_calls'] == 1


def test_nonphysical_manifest_provenance_refreshes_binding_without_recalibration(assembly, monkeypatch):
    from stowage.linear import operations
    physics, instance, band, state = assembly
    first = physics.build_model(instance, band)
    first._manifest_ids = ('same-box',)
    first._manifest_fingerprint = 'prior-provenance'
    monkeypatch.setattr(operations, 'departure_manifest_identity',
                        lambda instance: (('same-box',), 'current-provenance'))
    current = physics.build_model(instance, band)
    assert current._manifest_fingerprint == 'current-provenance'
    assert first._manifest_fingerprint == 'prior-provenance'
    assert current.private_safety_metadata == first.private_safety_metadata
    assert state['calibration_calls'] == 1


def test_certificate_audit_bytes_do_not_discard_same_numerical_reference(assembly, monkeypatch):
    from stowage.linear import family_calibration
    physics, instance, band, state = assembly
    audit = {'date_only_checksum': 'first-date'}
    monkeypatch.setattr(family_calibration, 'reference_inputs',
        lambda parameters: {'method': 1, 'dispatch': 'same-qualified-numeric-table',
                            'files': dict(audit)})
    first = physics.build_model(instance, band)
    audit['date_only_checksum'] = 'renewed-date'
    assert physics.build_model(instance, band) is first
    assert state['calibration_calls'] == 1


def test_nonphysical_fixed_load_evidence_refreshes_strict_binding_without_recalibration(assembly, monkeypatch):
    from stowage.linear import cache
    physics, instance, band, state = assembly
    instance.vessel.fixed_loads = [('same-numeric-load', 'prior-evidence')]
    monkeypatch.setattr(cache, 'fixed_loads_fingerprint',
                        lambda instance: instance.vessel.fixed_loads[0][1])
    first = physics.build_model(instance, band)
    first._fixed_loads_fingerprint = 'prior-evidence'
    instance.vessel.fixed_loads = [('same-numeric-load', 'current-evidence')]
    current = physics.build_model(instance, band)
    assert current._fixed_loads_fingerprint == 'current-evidence'
    assert first._fixed_loads_fingerprint == 'prior-evidence'
    assert current.private_safety_metadata == first.private_safety_metadata
    assert state['calibration_calls'] == 1
