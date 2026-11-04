"""Algebra and branch-oracle separation for the offline qualification driver."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification/boundary.py'
_SPEC = importlib.util.spec_from_file_location('epoch_boundary_driver', _PATH)
_DRIVER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DRIVER)


def test_branch_uses_shift_not_raw_coordinate_and_fresh_caches(monkeypatch):
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.linear import reference_equilibrium
    condition = LoadingCondition(10000., 1., .3, 4., 0., 0.,
        cg_cache={'original': 1}, upright_cache={'upright': 2}, liquid_usage={'table': 7})
    capacity = SimpleNamespace(transverse_moment_shift_kgm=200., capacity_kgm=9999999., attitude='seed')
    calls = []
    def equilibrium(geometry, shifted, density, *, initial):
        assert initial == 'seed'
        assert shifted.cg_cache == {} and shifted.cg_cache is not condition.cg_cache
        assert shifted.liquid_usage == {} and shifted.liquid_usage is not condition.liquid_usage
        assert shifted.upright_cache == condition.upright_cache
        calls.append(shifted.tcg_m)
        return SimpleNamespace(heel_rad=.03+(shifted.tcg_m-.32))
    monkeypatch.setattr(reference_equilibrium, 'liquid_equilibrium', equilibrium)
    result = _DRIVER.branch_crossing(None, condition, capacity, 1025., 1, .03)
    assert calls == pytest.approx([.3199, .32, .3201])
    assert result['outward_heel_offset_rad'][0] < 0 < result['outward_heel_offset_rad'][2]
    assert condition.cg_cache == {'original': 1}


def test_branch_wrong_direction_fails(monkeypatch):
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.linear import reference_equilibrium
    condition = LoadingCondition(10000., 1., 0., 4., 0., 0.)
    capacity = SimpleNamespace(transverse_moment_shift_kgm=0., attitude=None)
    monkeypatch.setattr(reference_equilibrium, 'liquid_equilibrium',
        lambda geometry, shifted, density, initial: SimpleNamespace(heel_rad=.03-shifted.tcg_m))
    with pytest.raises(ValueError, match='ambiguous'):
        _DRIVER.branch_crossing(None, condition, capacity, 1025., 1, .03)


def test_resume_rejects_changed_reference_without_numerical_work(monkeypatch, tmp_path):
    import hashlib
    import json
    from stowage.checker import stability_geometry
    from stowage.linear import cache, family_calibration
    monkeypatch.setattr(stability_geometry, 'StabilityGeometry', lambda parameters: None)
    monkeypatch.setattr(cache, 'response_calibration_identity', lambda family: 'response')
    monkeypatch.setattr(family_calibration, 'reference_inputs', lambda parameters: {'qualified': True})
    monkeypatch.setattr(family_calibration, 'reference_cache_identity', lambda value: value)
    family = SimpleNamespace(parameters={}, template=SimpleNamespace(max_list_rad=.03,
        band=SimpleNamespace(max_abs_heel_rad=.04)))
    checkpoint = tmp_path/'progress.json'
    context = {'response_identity':'response', 'reference':{'qualified':False},
        'driver_sha256':hashlib.sha256(_PATH.read_bytes()).hexdigest(),
        'seed':2, 'requested':1, 'branch_checks':True, 'direct_transfer':True, 'limit_rad':.03}
    checkpoint.write_text(json.dumps({'context':context, 'processed':1,
        'observations':[{'original':True}], 'rejections':[]}))
    with pytest.raises(ValueError, match='stale boundary checkpoint'):
        _DRIVER.observe(family, None, samples=1, seed=2, progress_path=checkpoint)
    context['reference'] = {'qualified':True}
    checkpoint.write_text(json.dumps({'context':context, 'processed':1,
        'observations':[{'original':True}], 'rejections':[]}))
    assert _DRIVER.observe(family, None, samples=1, seed=2,
        progress_path=checkpoint) == [{'original':True}]


def test_explicit_roundoff_reserve_covers_numerical_residual_but_not_physical_error():
    import numpy as np
    family = SimpleNamespace(template=SimpleNamespace(
        band=SimpleNamespace(displacement_min_kg=1e7, displacement_max_kg=2e7),
        km_line=(1e8, 10.), cell_tcg_m=np.array([-10.,10.]),
        tank_tcg_m=np.array([-8.,8.]), tank_fsm_max_kgm=np.array([1e7])))
    domain = {'kg_min_m':0., 'kg_max_m':20., 'fixed_y_abs_max_m':15.}
    floor = _DRIVER.roundoff_reserve_kgm(family, domain)
    assert floor == pytest.approx(64*np.finfo(float).eps*1.01e9)
    def observation(error):
        return {'sides':{side:{'unsafe_kgm':error, 'reference_transfer_unsafe_kgm':0.}
                         for side in _DRIVER.SIDES}}
    measured, physical, transfer, reserve, excess = _DRIVER.qualification_envelopes(
        [observation(0.)], [observation(4.1e-10)], roundoff_kgm=floor)
    for side in _DRIVER.SIDES:
        assert measured[side] == physical[side] == 0.
        assert transfer[side] == floor
        assert reserve[side] == pytest.approx(1.1*floor)
        assert excess[side] < 0
    *_, physical_excess = _DRIVER.qualification_envelopes(
        [observation(0.)], [observation(.01)], roundoff_kgm=floor)
    assert all(value > 0 for value in physical_excess.values())


def test_bootstrap_only_prepares_coefficients_never_calibrates(monkeypatch):
    from stowage.linear import physics, reusable, family_calibration, cache
    instance = object()
    parameters = {'id':'feeder_500'}
    family = SimpleNamespace(parameters=parameters)
    driver = SimpleNamespace(
        HELPER={'cargo_instance':lambda ship, level:(instance, {})},
        snapshot=lambda ship, params:{'reference':{'qualified':True, 'method':1}},
        prepare=lambda *args:pytest.fail('general response calibration forbidden'))
    monkeypatch.setattr(physics, '_prepared_params_for', lambda current:('feeder_500', parameters))
    def coefficients(current, *, parameters):
        assert current is instance
        assert parameters == {'id':'feeder_500'}
        return family
    monkeypatch.setattr(reusable, 'coefficient_family', coefficients)
    monkeypatch.setattr(family_calibration, 'domain_for', lambda fam, current:{'domain':'current'})
    monkeypatch.setattr(family_calibration, 'calibrated_family',
        lambda *args, **kwargs:pytest.fail('general response calibration forbidden'))
    monkeypatch.setattr(cache, 'response_calibration_identity', lambda fam:'current-response')
    actual, current, metadata, pins = _DRIVER.prepare_boundary('feeder_500', driver)
    assert actual is family and current is instance
    assert metadata == {'response_calibration_identity':'current-response',
        'reference_identity':{'qualified':True, 'method':1}, 'family_domain':{'domain':'current'}}
    assert pins['reference']['qualified'] is True


def test_builder_binding_preserves_observations_and_rejects_changed_primitive_pin(tmp_path):
    import hashlib
    from stowage.linear import boundary_calibration
    evidence = tmp_path/'raw.json'
    evidence.write_text('{"measured":true}')
    record = dict(response_identity='response', reference_identity={'method':1},
        domain={'kg_min_m':0., 'kg_max_m':20.}, station_x_m=[0.,1.],
        water_density_kg_m3=1025., limit_rad=.03, gm_min_m=.5,
        kind='boundary-moment-kgm', measured_unsafe_kgm={'lower_unsafe':0.},
        evidence=[{'path':str(evidence), 'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}])
    bound = _DRIVER.bind_builder(record)
    assert 'builder_version' not in record
    assert bound['measured_unsafe_kgm'] == record['measured_unsafe_kgm']
    assert evidence.read_text() == '{"measured":true}'
    assert bound['builder_version'] == boundary_calibration.BOUNDARY_CALIBRATION_VERSION
    assert len(bound['builder_inputs_hash']) == 64
    assert len(bound['evidence']) == 2
    bound['evidence'][-1]['sha256'] = 'changed'
    with pytest.raises(ValueError, match='cannot rebind'):
        _DRIVER.bind_builder(bound)
