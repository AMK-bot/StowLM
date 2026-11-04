"""V5 native greedy ordering: lower roofs before soft overstow preference.

Exactly one nested score return tuple changes. Native legal domains, restow-first
preference, geometry screens, slot shortlist, deadlines, backtracking and E6
completion checks remain intact. The bounded search can choose different or
exclude previous branches; no exhaustion or score is a feasibility proof.
"""
from __future__ import annotations

import ast
from functools import update_wrapper
import inspect
import textwrap
from types import FunctionType

_OLD = ('restow', 'overstows', 'deck', 'height')
_NEW = ('restow', 'deck', 'height', 'overstows')


def rewrite(source):
    """Fail closed unless the native nested score has its exact known prefix."""
    tree = ast.parse(textwrap.dedent(source))
    matches = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef) or function.name != 'score':
            continue
        for node in function.body:
            if (isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple)
                    and len(node.value.elts) >= 4
                    and all(isinstance(value, ast.Name) for value in node.value.elts[:4])
                    and tuple(value.id for value in node.value.elts[:4]) == _OLD):
                matches.append(node.value)
    if len(matches) != 1:
        raise ValueError(f'Expected one native greedy score tuple, found {len(matches)}')
    values = matches[0].elts
    matches[0].elts = [values[0], values[2], values[3], values[1], *values[4:]]
    return ast.fix_missing_locations(tree)


def clone(original):
    original = inspect.unwrap(original)
    tree = rewrite(inspect.getsource(original))
    namespace = dict(original.__globals__)
    exec(compile(tree, '<scale/greedy_v5/native_fast_prestow>', 'exec'), namespace)
    compiled = namespace[original.__name__]
    # Keep LIVE native globals: carrier_v4 scopes its geometry hook after this
    # clone is installed. A frozen copy would silently bypass that native hook.
    replacement = FunctionType(compiled.__code__, original.__globals__,
                               original.__name__, original.__defaults__, original.__closure__)
    replacement.__kwdefaults__ = original.__kwdefaults__
    update_wrapper(replacement, original)
    replacement._scale_greedy_v5 = True
    replacement._scale_greedy_score = _NEW
    return replacement


class Installation:
    def __init__(self, module, original, replacement):
        self.module, self.original, self.replacement = module, original, replacement

    def restore(self):
        if self.module._fast_prestow is self.replacement:
            self.module._fast_prestow = self.original

    __call__ = restore

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.restore()


def install():
    """Install after frozen runtime load; return a restorable handle."""
    from stowage.solver import twostep
    original = twostep._fast_prestow
    if getattr(original, '_scale_greedy_v5', False):
        return Installation(twostep, original, original)
    replacement = clone(original)
    twostep._fast_prestow = replacement
    return Installation(twostep, original, replacement)
