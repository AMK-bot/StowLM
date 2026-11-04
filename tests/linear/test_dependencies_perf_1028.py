"""The October stencil/slot refactors retain every transitive numeric input."""
import pytest

from stowage.linear import dependencies


@pytest.fixture
def source_tree(monkeypatch, tmp_path):
    original = dependencies._ROOT
    for name in {name for profile in dependencies._PROFILES.values() for name in profile}:
        target = tmp_path/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((original/name).read_bytes())
    monkeypatch.setattr(dependencies, '_ROOT', tmp_path)
    return tmp_path


@pytest.mark.parametrize('path,old,new,affected', [
    ('checker/stability_liquid_tables.py', 'value < axis[0]-1e-13',
     'value < axis[0]-2e-13', ('liquid_reference', 'calibration', 'instance')),
    ('checker/stability_liquid_tables.py', '_stencil_reference, np.isfinite, np.searchsorted',
     '_stencil_reference, np.isfinite, np.searchsorted, np.clip',
     ('liquid_reference', 'calibration', 'instance')),
    ('checker/stability_liquid_tables.py',
     '_ORIGINAL_STENCIL_DEPENDENCIES = _stencil_dependencies()',
     '_ORIGINAL_STENCIL_DEPENDENCIES = ()',
     ('liquid_reference', 'calibration', 'instance')),
    ('checker/stability_reuse.py', 'value = scalar_type(value)',
     'value = 2*scalar_type(value)', ('liquid_reference', 'calibration', 'instance')),
    ('checker/stability_reuse.py', 'array.dtype.type is not np.float64',
     'array.dtype.type is not np.float32', ('liquid_reference', 'calibration', 'instance')),
    ('checker/stability_reuse.py', '_SCALAR_TYPES = (bool, int, float,',
     '_SCALAR_TYPES = (bool, int, np.float64,', ('liquid_reference', 'calibration', 'instance')),
    ('checker/stability_reuse.py', '_ORIGINAL_AXIS_DEPENDENCIES = _axis_dependencies()',
     '_ORIGINAL_AXIS_DEPENDENCIES = ()', ('liquid_reference', 'calibration', 'instance')),
    ('checker/perf.py', 'return _ENABLED.get()', 'return True',
     ('geometry', 'fsm', 'fixed_tank', 'coefficient', 'liquid_reference', 'calibration', 'instance')),
    ('checker/perf.py', 'return _CACHE.get() if reuse_enabled() else None', 'return None',
     ('geometry', 'fsm', 'fixed_tank', 'coefficient', 'liquid_reference', 'calibration', 'instance')),
    ('checker/slots.py', 'return conventions.cells_of_slot(slot_id)',
     'return conventions.cells_of_slot(slot_id)[::-1]', ('calibration', 'instance')),
    ('checker/slots.py', 'conventions.parse_slot is _PARSE_FUNCTION',
     'conventions.parse_slot is not _PARSE_FUNCTION', ('calibration', 'instance')),
])
def test_every_moved_numeric_provider_invalidates_its_consumers(source_tree, path, old, new, affected):
    before = {name:dependencies.dependency_fingerprint(name) for name in dependencies._PROFILES}
    target = source_tree/path
    original = target.read_text()
    assert old in original
    target.write_text(original.replace(old, new))
    after = {name:dependencies.dependency_fingerprint(name) for name in dependencies._PROFILES}
    assert {name for name in before if before[name] != after[name]} == set(affected)


def test_warm_reference_identity_binds_the_arithmetic_not_just_dispatch(source_tree):
    """cache_warm -> reference_inputs -> liquid_reference sees stencil edits."""
    before = dependencies.dependency_contents('liquid_reference')
    target = source_tree/'checker/stability_liquid_tables.py'
    raw = target.read_text()
    target.write_text(raw.replace('weights[i] *= (value-nodes[j])/(nodes[i]-nodes[j])',
                                  'weights[i] *= 2*(value-nodes[j])/(nodes[i]-nodes[j])'))
    after = dependencies.dependency_contents('liquid_reference')
    assert after['checker/stability_liquid_tables.py'] != before['checker/stability_liquid_tables.py']
    assert all(after[name] == value for name, value in before.items()
               if name != 'checker/stability_liquid_tables.py')
