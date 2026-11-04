"""Source qualification preserves module execution order and dynamic bindings."""
import pytest

from stowage.linear import dependencies


@pytest.mark.parametrize('profile', ['liquid_reference', 'calibration', 'instance'])
@pytest.mark.parametrize('variant', ['conditional', 'assignment_order'])
def test_real_loading_provider_rebinding_and_order_are_qualified(profile, variant):
    raw = (dependencies._ROOT / 'checker/stability_loading.py').read_bytes()
    needle = b'from math import cos, factorial, sin, tan'
    assert needle in raw
    if variant == 'conditional':
        first = raw
        second = raw.replace(needle, needle + b'\nif True:\n    sin = cos')
    else:
        first = raw.replace(needle, b'from math import cos\nsin = cos\n' + needle)
        second = raw.replace(needle, b'from math import cos\n' + needle + b'\nsin = cos')
    symbols = dependencies._PROFILES[profile]['checker/stability_loading.py']
    assert dependencies._semantic_hash(first, symbols) != dependencies._semantic_hash(second, symbols)


@pytest.mark.parametrize('statement', [
    'if True:\n    sin = cos',
    'try:\n    sin = cos\nexcept Exception:\n    pass',
    'for provider in (cos,):\n    sin = provider',
    'with context():\n    sin = cos',
    'match True:\n    case True:\n        sin = cos',
])
def test_module_compound_provider_bindings_are_retained(statement):
    raw = b'from math import sin, cos\ndef numeric(x): return sin(x)\n'
    changed = raw + statement.encode() + b'\n'
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


@pytest.mark.parametrize('setup', [
    'def install():\n    global sin\n    sin = cos\ninstall()',
    'def install():\n    global sin\n    sin = cos\ndef other(value=install()): pass',
    'def install(function):\n    global sin\n    sin = cos\n    return function\n@install\ndef other(): pass',
    'def install():\n    global sin\n    sin = cos\nclass Row:\n    evidence = install()',
])
def test_import_time_helper_bodies_are_followed_transitively(setup):
    raw = f'from math import sin, cos\n{setup}\ndef numeric(x): return sin(x)\n'.encode()
    changed = raw.replace(b'sin = cos', b'sin = sin')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


@pytest.mark.parametrize('unsafe', [
    'from math import *',
    'exec("unused = 1")',
    'evaluate = eval',
    'namespace = globals()',
    'namespace = locals()',
    'namespace = vars()',
    'namespace = module.__dict__',
    'from builtins import exec as install',
    'setattr(module, "provider", 1)',
    'namespace = builtins.globals()',
    'namespace = getattr(module, "namespace")',
])
def test_dynamic_bindings_retain_whole_module_ast(unsafe):
    raw = f'{unsafe}\ndef other(): return 1\ndef numeric(x): return x\n'.encode()
    changed = raw.replace(b'other(): return 1', b'other(): return 2')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_module_definition_order_is_retained():
    first = b'def provider(): return 1\ndef numeric(x): return provider() + x\n'
    second = b'def numeric(x): return provider() + x\ndef provider(): return 1\n'
    assert dependencies._semantic_hash(first, ('numeric', 'provider')) != dependencies._semantic_hash(
        second, ('numeric', 'provider'))


@pytest.mark.parametrize('raw', [
    b'from math import *\ndef numeric(x): return x\n',
    b'namespace = globals()\ndef numeric(x): return x\n',
])
def test_dynamic_bindings_hash_whole_ast_and_validate_selectors(raw):
    assert dependencies._semantic_hash(raw, ('numeric',)) == dependencies._semantic_hash(raw, None)
    assert dependencies._semantic_hash(raw, ('numeric',)) == dependencies._semantic_hash(
        b'# Different source payload\n' + raw, ('numeric',))
    with pytest.raises(AssertionError, match='missing'):
        dependencies._semantic_hash(raw, ('absent',))


@pytest.mark.parametrize('symbols', [None, ('numeric',)])
@pytest.mark.parametrize('read', ['__doc__', 'numeric.__doc__'])
def test_docstring_read_code_is_bound_while_documentation_is_excluded(symbols, read):
    raw = f'"""123"""\ndef numeric(x): return x\nVALUE = {read}\n'.encode()
    assert dependencies._semantic_hash(raw, symbols) == dependencies._semantic_hash(
        b'# Different presentation\n' + raw.replace(b'"""123"""', b'"""456"""'), symbols)
    # Reading documentation is executable syntax, so changing that operation
    # remains bound even though scheme 2 excludes the docstring itself.
    assert dependencies._semantic_hash(raw, symbols) != dependencies._semantic_hash(
        raw.replace(f'VALUE = {read}'.encode(), b'VALUE = None'), symbols)


def test_whole_module_profile_uses_the_same_ast_scheme_for_dynamic_bindings():
    raw = b'namespace = globals()\ndef numeric(x): return x\n'
    assert dependencies._semantic_hash(raw, None) == dependencies._semantic_hash(raw, ('numeric',))
    assert dependencies._semantic_hash(raw, None) == dependencies._semantic_hash(
        b'# Changed bytes\n' + raw, None)


@pytest.mark.parametrize('setup', [
    'providers=[]\ndef run(f): providers.append(f())\n@run\ndef ignored(): return sin',
    'providers=[]\nclass Evidence:\n    def make(): return sin\n    providers.append(make())',
])
def test_decorator_callbacks_and_class_local_initializers_retain_whole_ast(setup):
    raw = f'from math import sin, cos\n{setup}\ndef numeric(x): return providers[0](x)\n'.encode()
    changed = raw.replace(b'return sin', b'return cos')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_helper_defined_inside_exception_handler_retains_its_body():
    raw = b'''try:
    raise RuntimeError()
except RuntimeError:
    def provider(x): return x + 1
def numeric(x): return provider(x)
'''
    changed = raw.replace(b'x + 1', b'x + 2')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_computed_field_keyword_expression_is_not_discarded():
    raw = b'''def install(value): return value
VALUE = Field(default=1, description=install(2))
def numeric(x): return VALUE + x
'''
    changed = raw.replace(b'description=install(2)', b'description=install(3)')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


@pytest.mark.parametrize('setup', [
    '''class Meta(type):
    def __new__(m, name, bases, ns):
        providers.append(ns['make']())
        return type.__new__(m, name, bases, ns)
class Evidence(metaclass=Meta):
    def make(): return sin''',
    '''class Base:
    def __init_subclass__(cls):
        providers.append(cls.make())
class Evidence(Base):
    def make(): return sin''',
])
def test_implicit_class_construction_hooks_retain_whole_ast(setup):
    raw = f'from math import sin, cos\nproviders=[]\n{setup}\ndef numeric(x): return providers[0](x)\n'.encode()
    changed = raw.replace(b'return sin', b'return cos')
    assert dependencies._semantic_hash(raw, ('numeric',)) == dependencies._semantic_hash(raw, None)
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_deferred_annotation_factories_retain_whole_ast():
    raw = b'''from __future__ import annotations
from math import sin, cos
from typing import get_type_hints
def factory(): return sin
def evidence() -> factory(): pass
def numeric(x): return get_type_hints(evidence)['return'](x)
'''
    changed = raw.replace(b'factory(): return sin', b'factory(): return cos')
    assert dependencies._semantic_hash(raw, ('numeric',)) == dependencies._semantic_hash(raw, None)
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_module_imports_and_assignments_are_not_hoisted():
    first = b'from math import cos\nsin = cos\nfrom math import sin\ndef numeric(x): return sin(x)\n'
    second = b'from math import cos\nfrom math import sin\nsin = cos\ndef numeric(x): return sin(x)\n'
    assert dependencies._semantic_hash(first, ('numeric',)) != dependencies._semantic_hash(second, ('numeric',))


def test_unselected_function_body_is_conservatively_retained():
    raw = b'from math import sin\ndef other(x): return x + 1\ndef numeric(x): return sin(x)\n'
    changed = raw.replace(b'other(x): return x + 1', b'other(x): return x + 2')
    assert dependencies._semantic_hash(raw, ('numeric',)) != dependencies._semantic_hash(changed, ('numeric',))


def test_comments_and_docstrings_are_excluded_by_ast_scheme():
    raw = b'from math import sin\ndef numeric(x):\n    """A label."""\n    return sin(x)\n'
    changed = b'# Different presentation\n' + raw.replace(b'A label.', b'Another label.')
    assert dependencies._semantic_hash(raw, ('numeric',)) == dependencies._semantic_hash(changed, ('numeric',))
