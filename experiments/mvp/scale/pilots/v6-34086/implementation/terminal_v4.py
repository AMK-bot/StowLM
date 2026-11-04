"""Prefer the carrier's exact mass-to-slot replay in native fast terminal.

The one AST change affects selection between successful native replays only.
Both policies retain their existing order, deadlines, actual legality/securing
checks, bounded slot adjustments, expanded-move replay and measured move cost.
A successful yard-first plan remains the fallback if representatives fail.
"""
from __future__ import annotations

import ast
from functools import update_wrapper
import inspect
import textwrap

_OLD = 'best_cost is None or cost < best_cost'
_NEW = 'policy == "carrier_representatives" or best_cost is None or cost < best_cost'


def rewrite(source):
    """Fail closed unless the exact one native selection condition is found."""
    tree = ast.parse(textwrap.dedent(source))
    expected = ast.dump(ast.parse(_OLD, mode='eval').body, include_attributes=False)
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
               and ast.dump(node.test, include_attributes=False) == expected]
    if len(matches) != 1:
        raise ValueError(f'Expected one fast-terminal selection comparator, found {len(matches)}')
    matches[0].test = ast.copy_location(ast.parse(_NEW, mode='eval').body, matches[0].test)
    return ast.fix_missing_locations(tree)


def clone(original):
    tree = rewrite(inspect.getsource(original))
    namespace = dict(original.__globals__)
    exec(compile(tree, '<scale/terminal_v4/native_fast_terminal>', 'exec'), namespace)
    replacement = namespace[original.__name__]
    update_wrapper(replacement, original)
    replacement._scale_terminal_v4 = True
    replacement._scale_terminal_selection = 'successful_carrier_representatives_else_yard_first'
    return replacement


class Installation:
    def __init__(self, module, original, replacement):
        self.module, self.original, self.replacement = module, original, replacement

    def restore(self):
        if self.module._fast_terminal is self.replacement:
            self.module._fast_terminal = self.original

    __call__ = restore

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.restore()


def install():
    """Call after frozen runtime loading; return a restorable installation."""
    from stowage.solver import twostep_terminal
    original = twostep_terminal._fast_terminal
    if getattr(original, '_scale_terminal_v4', False):
        # Nested callers must not undo an installation owned by another scope.
        return Installation(twostep_terminal, original, original)
    replacement = clone(original)
    twostep_terminal._fast_terminal = replacement
    return Installation(twostep_terminal, original, replacement)
