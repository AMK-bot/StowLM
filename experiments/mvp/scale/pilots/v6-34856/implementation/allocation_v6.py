"""V6 search budget allocation: reserve 75% for carrier, total unchanged.

Exactly one AST constant changes in TwoStepPlanner.plan's _preplan deadline.
Terminal still receives the original total deadline. All native validation,
late-candidate handling, checks, statuses and exception paths remain untouched.
"""
from __future__ import annotations

import ast
from functools import update_wrapper
import inspect
import textwrap
from types import FunctionType

FRACTION = .75
_EXPECTED = 'start + limit * 0.5'


def rewrite(source):
    tree = ast.parse(textwrap.dedent(source))
    expected = ast.dump(ast.parse(_EXPECTED,mode='eval').body,include_attributes=False)
    matches = []
    for node in ast.walk(tree):
        if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
                and node.func.id=='_preplan'):
            continue
        for keyword in node.keywords:
            if (keyword.arg=='deadline' and
                    ast.dump(keyword.value,include_attributes=False)==expected):
                matches.append(keyword.value.right.right)
    if len(matches)!=1 or not isinstance(matches[0],ast.Constant) or matches[0].value!=.5:
        raise ValueError(f'Expected one native _preplan 50% deadline, found {len(matches)}')
    matches[0].value=FRACTION
    return ast.fix_missing_locations(tree)


def clone(original):
    original=inspect.unwrap(original)
    tree=rewrite(inspect.getsource(original))
    namespace=dict(original.__globals__)
    exec(compile(tree,'<scale/allocation_v6/native_plan>','exec'),namespace)
    compiled=namespace[original.__name__]
    replacement=FunctionType(compiled.__code__,original.__globals__,original.__name__,
                             original.__defaults__,original.__closure__)
    replacement.__kwdefaults__=original.__kwdefaults__
    update_wrapper(replacement,original)
    replacement._scale_allocation_v6=True
    replacement._scale_carrier_fraction=FRACTION
    return replacement


class Installation:
    def __init__(self,cls,original,replacement):
        self.cls,self.original,self.replacement=cls,original,replacement

    def restore(self):
        if self.cls.plan is self.replacement:
            self.cls.plan=self.original

    __call__=restore

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.restore()


def install():
    from stowage.solver.twostep import TwoStepPlanner
    original=TwoStepPlanner.plan
    if getattr(original,'_scale_allocation_v6',False):
        return Installation(TwoStepPlanner,original,original)
    replacement=clone(original)
    TwoStepPlanner.plan=replacement
    return Installation(TwoStepPlanner,original,replacement)
