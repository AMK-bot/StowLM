"""D30 whole-module AST hashing excludes documentation, never executable scope."""
import pytest

from stowage.linear import dependencies


SOURCE=b'''"""Module documentation."""
from math import sin
LIMIT = 2
class Row:
    """Class documentation."""
    mass: float = 1.
    def method(self):
        """Method documentation."""
        return self.mass
def numeric(value=LIMIT):
    """Function documentation."""
    def nested():
        """Nested documentation."""
        return value
    return sin(nested())
async def other():
    """Async documentation."""
    return "runtime string"
'''


@pytest.mark.parametrize('symbols',[None,('numeric',),('Row.mass',)])
@pytest.mark.parametrize('changed',[
    b'# Added comment\n'+SOURCE,
    SOURCE.replace(b'LIMIT = 2',b'LIMIT = 2  # inline comment'),
    SOURCE.replace(b'documentation.',b'reviewed documentation.'),
    SOURCE.replace(b'"""Module documentation."""\n',b''),
    SOURCE.replace(b'    """Class documentation."""\n',b''),
    SOURCE.replace(b'        """Nested documentation."""\n',b''),
    SOURCE.replace(b'    """Async documentation."""\n',b''),
    SOURCE.replace(b'LIMIT = 2',b'LIMIT=2\n\n'),
])
def test_comments_docstrings_and_positions_do_not_change_whole_ast_hash(symbols,changed):
    assert dependencies._semantic_hash(SOURCE,symbols)==dependencies._semantic_hash(changed,symbols)


@pytest.mark.parametrize('old,new',[
    (b'from math import sin',b'from math import cos as sin'),
    (b'LIMIT = 2',b'LIMIT = 3'),
    (b'value=LIMIT',b'value=1'),
    (b'mass: float = 1.',b'mass: float = 2.'),
    (b'return self.mass',b'return 2*self.mass'),
    (b'return value',b'return value+1'),
    (b'"runtime string"',b'"changed runtime string"'),
    (b'def numeric',b'@staticmethod\ndef numeric'),
])
def test_all_code_and_runtime_constants_remain_bound_outside_selectors(old,new):
    # Selecting one field must still bind every other definition and import.
    changed=SOURCE.replace(old,new)
    assert changed!=SOURCE
    assert dependencies._semantic_hash(SOURCE,('Row.mass',))!=dependencies._semantic_hash(changed,('Row.mass',))


def test_module_execution_order_and_non_docstring_expressions_remain_bound():
    raw=b'first()\nsecond()\n"retained expression"\n'
    for changed in (b'second()\nfirst()\n"retained expression"\n',
                    b'first()\nsecond()\n"changed expression"\n'):
        assert dependencies._semantic_hash(raw,None)!=dependencies._semantic_hash(changed,None)


@pytest.mark.parametrize('raw',[b'def broken(:\n',b'\x00',b'class Row:\n'])
def test_malformed_source_fails_closed_without_raw_byte_fallback(raw):
    with pytest.raises(SyntaxError):
        dependencies._semantic_hash(raw,None)


def test_hash_scheme_version_migrates_every_profile_even_with_identical_sources(monkeypatch):
    assert dependencies.LINEAR_DEPENDENCY_HASH_SCHEME_VERSION=='2'
    monkeypatch.setattr(dependencies,'dependency_contents',lambda artifact:{'same.py':'same-hash'})
    before={name:dependencies.dependency_fingerprint(name) for name in dependencies._PROFILES}
    monkeypatch.setattr(dependencies,'LINEAR_DEPENDENCY_HASH_SCHEME_VERSION','next-scheme')
    assert all(dependencies.dependency_fingerprint(name)!=value for name,value in before.items())
