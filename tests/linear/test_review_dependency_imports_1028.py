"""Selected numerical identities retain the providers bound by their imports."""
import pytest

from stowage.linear import dependencies


@pytest.fixture
def source_tree(monkeypatch, tmp_path):
    original = dependencies._ROOT
    for name in {name for profile in dependencies._PROFILES.values() for name in profile}:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((original / name).read_bytes())
    monkeypatch.setattr(dependencies, '_ROOT', tmp_path)
    return tmp_path


@pytest.mark.parametrize('old,new', [
    ('from math import cos, factorial, sin, tan',
     'from math import cos, factorial, cos as sin, tan'),
    ('import numpy as np', 'import numpy.linalg as np'),
    ('from stowage.checker.slots import cells_of_slot',
     'from stowage.conventions import cells_of_slot'),
])
def test_changed_loading_import_invalidates_exactly_its_consumers(source_tree, old, new):
    before = {name: dependencies.dependency_fingerprint(name) for name in dependencies._PROFILES}
    target = source_tree / 'checker/stability_loading.py'
    raw = target.read_text()
    assert old in raw
    target.write_text(raw.replace(old, new))
    after = {name: dependencies.dependency_fingerprint(name) for name in before}
    assert {name for name in before if after[name] != before[name]} == {
        'liquid_reference', 'calibration', 'instance'}


@pytest.mark.parametrize('alias,call', [
    ('provider = sin', 'provider(value)'),
    ('provider = {"numeric": sin}', 'provider["numeric"](value)'),
    ('def provider(value):\n    return sin(value)', 'provider(value)'),
])
def test_import_used_through_module_global_is_retained(alias, call):
    raw = f'from math import sin\n{alias}\ndef numeric(value):\n    return {call}\n'.encode()
    changed = raw.replace(b'from math import sin', b'from math import cos as sin')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_transitive_module_global_alias_code_is_retained_too():
    raw = b'from math import sin, cos\nprovider = sin\ndef numeric(value): return provider(value)\n'
    changed = raw.replace(b'provider = sin', b'provider = cos')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


@pytest.mark.parametrize('raw,old,new', [
    (b'from math import sin, cos\ndef numeric(value): return sin(value)\n',
     b'import sin, cos', b'import sin, tan as cos'),
    (b'import math as unused\ndef numeric(value): return value + 1\n',
     b'import math as unused', b'import cmath as unused'),
    (b'from math import sin\ndef numeric(sin): return sin + 1\n',
     b'from math import sin', b'from math import cos as sin'),
    (b'from math import sin\ndef numeric(value):\n    sin = value + 1\n    return sin\n',
     b'from math import sin', b'from math import cos as sin'),
])
def test_all_module_import_bindings_invalidate_conservatively(raw, old, new):
    changed = raw.replace(old, new)
    assert changed != raw
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_selected_class_members_retain_all_module_imports_and_class_fields():
    raw = b'''from dataclasses import dataclass
from math import sin
from pathlib import Path
@dataclass
class Row:
    mass: float = sin(1.)
    evidence: Path = Path("reviewed")
'''
    baseline = dependencies._semantic_hash(raw, ('Row.mass',))
    assert baseline != dependencies._semantic_hash(raw.replace(b'from pathlib import Path',
        b'from pathlib import PurePath as Path'), ('Row.mass',))
    assert baseline != dependencies._semantic_hash(raw.replace(b'from math import sin',
        b'from math import cos as sin'), ('Row.mass',))


def test_selected_nested_scope_retains_all_module_import_bindings():
    raw = b'''from math import sin, cos
def numeric(value):
    def nested(cos):
        return sin(value) + cos
    return nested(value)
'''
    baseline = dependencies._semantic_hash(raw, ('numeric',))
    assert baseline != dependencies._semantic_hash(raw.replace(b'import sin, cos',
        b'import tan as sin, cos'), ('numeric',))
    assert baseline != dependencies._semantic_hash(raw.replace(b'import sin, cos',
        b'import sin, tan as cos'), ('numeric',))
