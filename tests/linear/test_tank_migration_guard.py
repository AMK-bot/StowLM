"""Unsupported tank migrations must never inherit rectangular calibration."""
from types import SimpleNamespace

import pytest

from stowage.linear import physics
from stowage.model.fixtures import build_instance


def _instance_with_test_manual():
    from stowage.model import VesselFieldProvenance
    instance=build_instance()
    # ASSUMPTION: independent synthetic test-only policy; these dispatch tests
    # do no physical work and must not fail first on unavailable D40 evidence.
    values={'min_trim_m':-1.,'max_trim_m':1.,
            'min_displacement_kg':1.,'max_displacement_kg':1.e9}
    values.update({name+'_provenance':VesselFieldProvenance(
        assumption='ASSUMPTION: isolated dispatch-test loading manual') for name in tuple(values)})
    return instance.model_copy(update={'vessel':instance.vessel.model_copy(update={
        'hull':instance.vessel.hull.model_copy(update=values)})})


@pytest.mark.parametrize('shape',['unknown_solid','bounding_box_placeholder'])
def test_unknown_tank_migration_fails_before_cache_or_geometry(monkeypatch,shape):
    instance=_instance_with_test_manual()
    params={'id':'synthetic','tanks':[{'id':'CURVED-P','geometry':{'shape':shape}}]}
    monkeypatch.setattr(physics,'_params_for',lambda instance:('synthetic',params))
    class ForbiddenCache(dict):
        def __contains__(self,key):
            raise AssertionError('memory calibration cache must not be consulted')
    monkeypatch.setattr(physics,'_CACHE',ForbiddenCache())
    def forbidden(*args,**kwargs):
        raise AssertionError('cache/geometry must not be consulted')
    monkeypatch.setattr('stowage.linear.cache.model_cache_key',forbidden)
    monkeypatch.setattr('stowage.linear.cache.load_model',forbidden)
    monkeypatch.setattr('stowage.linear.reusable.coefficient_family',forbidden)
    monkeypatch.setattr('stowage.linear.family_calibration.reference_inputs',forbidden)
    monkeypatch.setattr('stowage.linear.family_calibration.reference_inputs_cached',forbidden)
    monkeypatch.setattr(physics,'_geometry_from_params',forbidden)
    with pytest.raises(ValueError,match='requires supported exact tank geometry'):
        physics.build_model(instance)


@pytest.mark.parametrize('shape',['rectangular_prism','hull_sections','hull_intersection'])
def test_supported_tank_cache_behavior_is_preserved(monkeypatch,shape):
    instance=_instance_with_test_manual()
    params={'id':'synthetic','tanks':[{'id':'BOX-P','geometry':{'shape':shape}}]}
    monkeypatch.setattr(physics,'_params_for',lambda instance:('synthetic',params))
    monkeypatch.setattr(physics,'_CACHE',{})
    from stowage.versions import LINEAR_MODEL_VERSION
    cached=SimpleNamespace(vessel_id=instance.vessel.id,version=LINEAR_MODEL_VERSION,
                           measurement_diagnostics={})
    family=SimpleNamespace(parameters=params)
    band=object();errors=object();diagnostics={'samples':192}
    calls=[]
    monkeypatch.setattr('stowage.linear.cache.model_cache_key',lambda *args:'rectangular-cache-key')
    # This is dispatch/cache behavior, not a geometry fixture. Its intentionally
    # incomplete tank record must never construct a real solid or exact table.
    monkeypatch.setattr('stowage.linear.family_calibration.reference_inputs',lambda p:{'method':1})
    monkeypatch.setattr('stowage.linear.family_calibration.reference_inputs_cached',lambda p:{'method':1})
    def coefficients(current,p,*,force_rebuild):
        assert current is instance and p is params and not force_rebuild
        calls.append('coefficients')
        return family
    def calibration(current,booking,*,samples,force_rebuild):
        assert current is family and booking is instance and samples==192 and not force_rebuild
        calls.append('calibration')
        return errors,diagnostics
    def assemble(current,booking,local,**kwargs):
        assert current is family and booking is instance and local is band
        assert kwargs=={'errors':errors,'diagnostics':diagnostics}
        calls.append('constants')
        return cached
    monkeypatch.setattr('stowage.linear.reusable.coefficient_family',coefficients)
    monkeypatch.setattr('stowage.linear.family_calibration.calibrated_family',calibration)
    monkeypatch.setattr('stowage.linear.reusable.assemble_instance',assemble)
    assert physics.build_model(instance,band) is cached
    assert calls==['coefficients','calibration','constants']
    # A second identical instance hit does not re-enter family preparation.
    assert physics.build_model(instance,band) is cached
    assert calls==['coefficients','calibration','constants']
