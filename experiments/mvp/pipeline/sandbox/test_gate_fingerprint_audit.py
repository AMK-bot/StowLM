"""A semantic fingerprint must survive string interning and detect code edits."""
import marshal
import sys

from .holdout import code_content, code_fingerprint


def test_fingerprint_is_invariant_to_marshal_intern_flag_changes():
    namespace = {}
    exec(compile("def probe():\n return 'marshal regression constant with spaces 938171133'\n",
                 '<independent-audit>', 'exec'), namespace)
    code = namespace['probe'].__code__
    before_marshal = marshal.dumps(code)
    before_content = code_content(code)
    before_fingerprint = code_fingerprint(code)
    interned = sys.intern(code.co_consts[1])
    assert interned is code.co_consts[1]
    assert before_marshal != marshal.dumps(code)
    assert before_content == code_content(code)
    assert before_fingerprint == code_fingerprint(code)
    assert namespace['probe']() == interned


def test_fingerprint_refuses_actual_constant_change():
    namespace = {}
    exec(compile("def probe():\n return 'original value'\n", '<independent-audit>', 'exec'), namespace)
    code = namespace['probe'].__code__
    changed = code.replace(co_consts=(None, 'different value'))
    assert code.co_code == changed.co_code
    assert code_content(code) != code_content(changed)
    assert code_fingerprint(code) != code_fingerprint(changed)
