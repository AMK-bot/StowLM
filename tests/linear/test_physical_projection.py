"""Normalization is a representation optimization, never a new physics key."""
from dataclasses import dataclass, fields, is_dataclass

import numpy as np
import pytest

from stowage.linear import cache


def legacy_jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if is_dataclass(value):
        return {field.name: legacy_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): legacy_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [legacy_jsonable(item) for item in value]
    return value


def legacy_physical(value):
    value = legacy_jsonable(value)
    if isinstance(value, dict):
        if 'value' in value and 'unit' in value:
            return legacy_physical(value['value'])
        return {key: legacy_physical(item) for key, item in value.items()
                if key not in ('source', 'sources', 'assumption', 'provenance',
                               'component_versions', 'versions')}
    if isinstance(value, list):
        return [legacy_physical(item) for item in value]
    return value


@dataclass
class Wrapped:
    value: object
    source: str = 'ignored'


def test_jsonable_fast_paths_preserve_subclass_priority_and_nested_values():
    class DumpDict(dict):
        def model_dump(self, **kwargs): return {'overridden': 'dict'}
    class DumpList(list):
        def model_dump(self, **kwargs): return {'overridden': 'list'}
    class DumpFloat(float):
        def model_dump(self, **kwargs): return {'overridden': 'float'}
    class DumpString(str):
        def model_dump(self, **kwargs): return {'overridden': 'str'}
    @dataclass
    class DataDict(dict):
        x: int
    value = {2: [DumpDict(x=3), DumpList([4]), DumpFloat(5), DumpString('six'),
                 DataDict(7), np.array([Wrapped(8)], dtype=object),
                 np.float64(9.), (None, True, 10, 'eleven')]}
    assert cache._jsonable(value) == legacy_jsonable(value)


def test_parameter_projection_memo_uses_fresh_bytes_and_isolates_callers():
    from copy import deepcopy
    params = {'hull': {'lpp_m': {'value': 10., 'unit': 'm', 'source': 'old'}},
              'hull_form': {'family': 'synthetic'}, 'envelope': {},
              'tanks': [{'id': 'T', 'geometry': {'shape': 'box', 'sizes': [1., 2.]}}]}
    cache._physical_parameters_bytes.cache_clear()
    original = cache.physical_parameters(params)
    pristine = deepcopy(original)
    original['tanks'][0]['geometry']['sizes'][0] = 999.
    assert cache.physical_parameters(params) == pristine
    hits = cache._physical_parameters_bytes.cache_info().hits
    params['hull']['lpp_m']['source'] = 'new prose'
    assert cache.physical_parameters(params) == pristine
    params['hull']['lpp_m']['value'] = 11.
    changed = cache.physical_parameters(params)
    assert changed['hull']['lpp_m'] == 11.
    assert pristine['hull']['lpp_m'] == 10.
    assert cache._physical_parameters_bytes.cache_info().hits == hits
    assert cache.physical_parameters(params) == changed


def test_normalize_once_matches_recursive_nested_edge_cases():
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    values = [instance.vessel, instance, Wrapped(np.arange(12).reshape(3, 4)),
        {1: {'value': [np.float64(1.5), {'value': np.array([2., 3.]),
                                     'unit': 'm', 'source': 'ignored'}],
             'unit': 'mixed', 'provenance': 'ignored'},
         'other': ({'versions': {'checker': 'ignored'}, 'x': True}, None)},
        np.array([Wrapped({'value': 7., 'unit': 'kg'}), {1: {'x': 4}, 'source': 'ignored'}], dtype=object)]
    for value in values:
        assert cache._canonical(cache._physical(value)) == cache._canonical(legacy_physical(value))


def test_structured_leaves_preserve_subclass_dump_priority():
    class DumpDict(dict):
        def model_dump(self, **kwargs): return {'overridden': 'dict'}
    class DumpFloat(float):
        def model_dump(self, **kwargs): return {'overridden': 'float'}
    class Model:
        def model_dump(self, **kwargs):
            return {'scalar': DumpFloat(7), 'mapping': DumpDict(x=8)}
    for value in (np.array([DumpDict(x=7)], dtype=object), Model()):
        assert cache._canonical(cache._physical(value)) == cache._canonical(legacy_physical(value))


@pytest.mark.parametrize('ship', ['feeder_500', 'feeder_800', 'feeder_1700'])
@pytest.mark.slow
def test_production_parameters_vessels_and_keys_are_unchanged(ship, monkeypatch):
    from stowage.vessel.fleet import load_ship_params, load_vessel
    parameters = load_ship_params(ship)
    vessel = load_vessel(ship, with_hull=False)
    assert cache._jsonable(parameters) == legacy_jsonable(parameters)
    assert cache._jsonable(vessel) == legacy_jsonable(vessel)
    legacy_parameters = legacy_physical(parameters)
    assert cache._physical(parameters) == legacy_parameters
    expected_vessel = cache.vessel_physics(vessel)
    current = cache._physical
    artifacts = ('geometry', 'integration', 'fsm', 'coefficient', 'calibration', 'instance')
    projected = {artifact: cache.physical_parameters(parameters, artifact) for artifact in artifacts}
    def keys():
        return {artifact: cache.cache_key(kind, vessel_id=ship, params=parameters,
            inputs={'kind': label, 'explicit': [1., 2.]}) for artifact, kind, label in (
                ('geometry', 'vessel', 'HullGeometry'),
                ('integration', 'vessel', 'Gauss-panel force/moment integration'),
                ('fsm', 'vessel', 'continuous upright maximum slack FSM'),
                ('coefficient', 'coefficient_family', ''),
                ('calibration', 'family_calibration', ''), ('instance', 'model', ''))}
    before = keys()
    # The old projection is pure: reuse its independently computed full tree
    # while checking all artifact keys, rather than repeatedly timing it.
    cache._physical_parameters_bytes.cache_clear()
    monkeypatch.setattr(cache, '_physical', lambda value: legacy_parameters
                        if value is parameters else legacy_physical(value))
    try:
        assert cache.vessel_physics(vessel) == expected_vessel
        for artifact in artifacts:
            assert cache.physical_parameters(parameters, artifact) == projected[artifact]
        assert keys() == before
    finally:
        monkeypatch.setattr(cache, '_physical', current)
        cache._physical_parameters_bytes.cache_clear()
