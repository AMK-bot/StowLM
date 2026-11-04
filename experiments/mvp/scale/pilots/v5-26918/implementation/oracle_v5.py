"""Native E9 parameter-resolution parity with the unchanged public checker.

No rule, value, tolerance or geometric equation is changed. Frozen methods
locally import the explicit-only helper, so three native import bindings must
be bridged to the shared catalogue-aware resolver. Public functions stay intact.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

VERSION = "scale-oracle-5-shared-e9-parameter-resolution"
METHODS = ("_oog_zones", "_clearance", "_e9_batch")
_MISSING = object()


def _bridge(method, resolver):
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "stowage.checker.rules_special":
            selected = [alias for alias in node.names if alias.name == "_parameter"]
            if selected:
                node.names = [alias for alias in node.names if alias.name != "_parameter"]
                count += len(selected)
    if count != 1:
        raise RuntimeError(f"Native {method.__name__} import-binding mismatch: {count}")
    namespace = dict(method.__globals__)
    namespace["_parameter"] = resolver
    exec(compile(ast.fix_missing_locations(tree), "<scale/oracle_v5/native_import_bridge>", "exec"), namespace)
    return namespace[method.__name__]


class _State:
    def __init__(self, module, cls, originals, replacements, previous_global, resolver):
        self.module, self.cls = module, cls
        self.originals, self.replacements = originals, replacements
        self.previous_global, self.resolver = previous_global, resolver
        self.references = 0

    def release(self):
        self.references -= 1
        if self.references:
            return
        for name, replacement in self.replacements.items():
            if getattr(self.cls, name) is replacement:
                setattr(self.cls, name, self.originals[name])
        if getattr(self.module, "_parameter", _MISSING) is self.resolver:
            if self.previous_global is _MISSING:
                delattr(self.module, "_parameter")
            else:
                self.module._parameter = self.previous_global


class Installation:
    def __init__(self, state):
        self.state, self.active = state, True
        state.references += 1

    def restore(self):
        if self.active:
            self.active = False
            self.state.release()

    __call__ = restore

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.restore()


def install():
    from stowage.feasibility import _engine
    from stowage.checker import rules_special
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    cls = _engine.IncrementalEngine
    existing = getattr(_engine, "_parameter", _MISSING)
    state = getattr(existing, "_scale_oracle_v5_state", None)
    if state is not None and all(getattr(cls, name) is value for name, value in state.replacements.items()):
        return Installation(state)
    original_parameter = rules_special._parameter
    shared_clearance = rules_special._clearance_parameter

    def parameter(instance, rule, *names):
        if (not rule_freeze_v1_enabled() and rule == "expert.e9.over_width_length"
                and names and set(names) <= {"oog_clearance", "minimum_clearance_m"}):
            return shared_clearance(instance, rule, "oog_clearance", "minimum_clearance_m")
        return original_parameter(instance, rule, *names)

    originals = {name: getattr(cls, name) for name in METHODS}
    replacements = {name: _bridge(method, parameter) for name, method in originals.items()}
    state = _State(_engine, cls, originals, replacements, existing, parameter)
    parameter._scale_oracle_v5_state = state
    _engine._parameter = parameter
    for name, method in replacements.items():
        setattr(cls, name, method)
    return Installation(state)
