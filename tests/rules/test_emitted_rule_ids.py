"""Every emitted rule_id, including checker diagnostics, needs a catalogue ID.

This source audit follows Violation constructors, their rule-argument wrappers,
the checker's emit callbacks and finite tuple producers. It never treats every
dotted string as a rule, imports physics, or grants a namespace exemption. An
unresolved emission fails separately: an arbitrary f-string is not an inventory.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from stowage.rules.catalogue import load_catalogue


@dataclass(eq=False)
class _Scope:
    node: ast.AST
    parent: _Scope | None = None
    bindings: dict = field(default_factory=dict)


class _Emissions:
    def __init__(self, path: str, source: str):
        self.path, self.tree = path, ast.parse(source)
        self.scopes, self.functions, self.parents = {}, {}, {}
        self.ids, self.unresolved = {}, []
        self.constructors = {"Violation"} | {
            alias.asname or alias.name for node in ast.walk(self.tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names if alias.name == "Violation"
        }
        self._index(self.tree, _Scope(self.tree))
        self.wrappers = {}
        for node in ast.walk(self.tree):
            scope = self.scopes[node]
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    self._bind(scope, target, node.value)
            elif isinstance(node, (ast.For, ast.comprehension)):
                self._bind(scope, node.target, node.iter, iterate=True)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                  and node.args):
                scope.bindings.setdefault(node.func.value.id, []).append((ast.List(elts=[node.args[0]]), (), False))
        # Discover wrappers from actual constructor/tuple sinks, then wrappers
        # of those wrappers. Positional indices come from their signatures.
        for _ in range(len(self.functions) + 1):
            changed = False
            for call in (n for n in ast.walk(self.tree) if isinstance(n, ast.Call)):
                expression = self._argument(call)
                scope = self.scopes[call]
                if isinstance(expression, ast.Name) and isinstance(scope.node, ast.FunctionDef):
                    parameters = [a.arg for a in self._parameters(scope.node)]
                    if expression.id in parameters and scope.node not in self.wrappers:
                        self.wrappers[scope.node] = expression.id
                        changed = True
            if not changed:
                break

    def _index(self, node, scope):
        self.scopes[node] = scope
        for child in ast.iter_child_nodes(node):
            self.parents[child] = node
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nested = _Scope(child, scope)
                if isinstance(child, ast.FunctionDef):
                    self.functions[(scope, child.name)] = child
                self._index(child, nested)
            else:
                self._index(child, scope)

    def _bind(self, scope, target, value, projection=(), iterate=False):
        if isinstance(target, ast.Name):
            scope.bindings.setdefault(target.id, []).append((value, projection, iterate))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for index, part in enumerate(target.elts):
                self._bind(scope, part, value, (*projection, index), iterate)

    @staticmethod
    def _parameters(function):
        return [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]

    def _function(self, call):
        name = call.func.id if isinstance(call.func, ast.Name) else None
        scope = self.scopes[call]
        while scope is not None:
            candidate = self.functions.get((scope, name))
            if candidate is not None:
                return candidate
            scope = scope.parent
        if (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                and call.func.value.id in {"self", "cls"}):
            scope = self.scopes[call]
            while scope is not None and not isinstance(scope.node, ast.ClassDef):
                scope = scope.parent
            return self.functions.get((scope, call.func.attr))
        return None

    def _argument(self, call):
        if ((isinstance(call.func, ast.Name) and call.func.id in self.constructors)
                or isinstance(call.func, ast.Attribute) and call.func.attr == "Violation"):
            return next((k.value for k in call.keywords if k.arg == "rule_id"), ast.Name(id="UNRESOLVED_VIOLATION"))
        if isinstance(call.func, ast.Attribute) and call.func.attr == "model_copy":
            update = next((k.value for k in call.keywords if k.arg == "update"), None)
            if isinstance(update, ast.Dict):
                for key, value in zip(update.keys, update.values):
                    if isinstance(key, ast.Constant) and key.value == "rule_id":
                        return value
        # evaluate_criteria returns failures tuples; stability.py forwards their
        # first element to its Violation wrapper with violation(*args).
        if (self.path.endswith("checker/stability_criteria.py") and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "failures"
                and call.func.attr == "append" and call.args):
            row = call.args[0]
            return row.elts[0] if isinstance(row, ast.Tuple) and row.elts else row
        function = self._function(call)
        if function in self.wrappers:
            parameter = self.wrappers[function]
            keyword = next((k.value for k in call.keywords if k.arg == parameter), None)
            if keyword is not None:
                return keyword
            positional = [*function.args.posonlyargs, *function.args.args]
            names = [a.arg for a in positional]
            index = names.index(parameter) if parameter in names else None
            if index is not None:
                if isinstance(call.func, ast.Attribute):
                    index -= 1  # bound self/cls
                if index < len(call.args):
                    return call.args[index]
            defaults = dict(zip([a.arg for a in function.args.kwonlyargs], function.args.kw_defaults))
            defaults.update(zip([a.arg for a in positional][-len(function.args.defaults):], function.args.defaults))
            return defaults.get(parameter, ast.Name(id="UNRESOLVED_RULE_ARGUMENT"))
        # Explicit callback protocol used by rules/background/evidence/weight.
        # Restrict it to an actual formal 'emit' parameter, never set.add etc.
        if isinstance(call.func, ast.Name) and call.func.id == "emit":
            scope = self.scopes[call]
            while scope is not None:
                if isinstance(scope.node, ast.FunctionDef) and "emit" in {a.arg for a in self._parameters(scope.node)}:
                    return call.args[0] if call.args else ast.Name(id="UNRESOLVED_EMIT_ARGUMENT")
                scope = scope.parent
        return None

    def _values(self, expression, scope, projection=(), iterate=False, seen=frozenset()):
        if expression is None:
            return set(), {"missing expression"}
        if isinstance(expression, ast.Name):
            # A loop target has the finite value of that enclosing loop, not
            # every earlier/later reuse of the same local variable name.
            ancestor = self.parents.get(expression)
            while ancestor is not None and ancestor is not scope.node:
                if isinstance(ancestor, ast.For):
                    def selected(target, indices=()):
                        if isinstance(target, ast.Name):
                            return indices if target.id == expression.id else None
                        if isinstance(target, (ast.Tuple, ast.List)):
                            for index, item in enumerate(target.elts):
                                match = selected(item, (*indices, index))
                                if match is not None:
                                    return match
                        return None
                    component = selected(ancestor.target)
                    if component is not None:
                        return self._values(ancestor.iter, scope, (*component, *projection), True, seen)
                ancestor = self.parents.get(ancestor)
            key = (id(scope), expression.id, projection, iterate)
            if key in seen:
                return set(), {f"cyclic name {expression.id}"}
            owner = scope
            while owner is not None and expression.id not in owner.bindings:
                owner = owner.parent
            if owner is not None:
                result, unknown = set(), set()
                bindings = owner.bindings[expression.id]
                # 'rule' outside its prior loop is explicitly rebound (e.g.
                # port.call.length). Do not carry an expired loop's map keys.
                if expression.id == "rule":
                    assigned = [item for item in bindings if not item[2]
                                and getattr(item[0], "lineno", 0) <= getattr(expression, "lineno", float("inf"))]
                    if assigned:
                        def unconditional(value):
                            parent = self.parents.get(value)
                            while parent is not None and parent is not owner.node:
                                if isinstance(parent, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
                                    return False
                                parent = self.parents.get(parent)
                            return parent is owner.node
                        dominating = [item for item in assigned if unconditional(item[0])]
                        start = max((getattr(item[0], "lineno", 0) for item in dominating), default=0)
                        bindings = [item for item in assigned if getattr(item[0], "lineno", 0) >= start]
                for value, selected, loop in bindings:
                    values, errors = self._values(value, owner, (*selected, *projection), loop or iterate, seen | {key})
                    result |= values
                    unknown |= errors
                return result, unknown
            return set(), {f"unbound {expression.id}"}
        if iterate:
            if isinstance(expression, (ast.Tuple, ast.List, ast.Set)):
                result, unknown = set(), set()
                for row in expression.elts:
                    values, errors = self._values(row, scope, projection, False, seen)
                    result |= values
                    unknown |= errors
                return result, unknown
            # E9's returned owner -> rules -> killed-cell sets have fixed keys
            # in _killed_cells. Derive those keys from that producer's AST.
            if (self.path.endswith("checker/rules_special.py") and isinstance(expression, ast.Call)
                    and ast.unparse(expression) == "rules.items()" and projection == (0,)):
                producer = next((fn for fn in self.functions.values() if fn.name == "_killed_cells"), None)
                dictionaries = [] if producer is None else [n.value for n in ast.walk(producer)
                    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "rules" for t in n.targets)
                    and isinstance(n.value, ast.Dict)]
                if dictionaries and any(isinstance(n, ast.Call) and ast.unparse(n.func) == "_killed_cells"
                                        for n in ast.walk(scope.node)):
                    keys = {k.value for d in dictionaries for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if sum(len(d.keys) for d in dictionaries) == len(keys):
                        return keys, set()
            return set(), {f"non-finite iterable {ast.unparse(expression)}"}
        if projection:
            if isinstance(expression, (ast.Tuple, ast.List)) and projection[0] < len(expression.elts):
                return self._values(expression.elts[projection[0]], scope, projection[1:], False, seen)
            return set(), {f"unresolved tuple component {ast.unparse(expression)}"}
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return {expression.value}, set()
        if isinstance(expression, ast.IfExp):
            a, e = self._values(expression.body, scope, seen=seen)
            b, f = self._values(expression.orelse, scope, seen=seen)
            return a | b, e | f
        if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
            a, e = self._values(expression.left, scope, seen=seen)
            b, f = self._values(expression.right, scope, seen=seen)
            return {left + right for left in a for right in b}, e | f
        if isinstance(expression, ast.JoinedStr):
            result, unknown = {""}, set()
            for part in expression.values:
                values, errors = self._values(part.value if isinstance(part, ast.FormattedValue) else part, scope, seen=seen)
                result = {left + right for left in result for right in values}
                unknown |= errors
            return result, unknown
        return set(), {f"unresolved {ast.unparse(expression)}"}

    def _forwarded_criteria(self, call, expression):
        if not (self.path.endswith("checker/stability.py") and isinstance(expression, ast.Starred)
                and ast.unparse(expression.value) == "args"):
            return False
        scope = self.scopes[call]
        ancestor = self.parents.get(call)
        while ancestor is not None and ancestor is not scope.node:
            if isinstance(ancestor, ast.For) and ast.unparse(ancestor.target) == "args":
                if ast.unparse(ancestor.iter) != "broken":
                    return False
                break
            ancestor = self.parents.get(ancestor)
        else:
            return False
        bindings = scope.bindings.get("broken", [])
        return any(isinstance(value, ast.Call) and ast.unparse(value.func) == "evaluate_criteria"
                   and selected == (2,) for value, selected, _ in bindings)

    def collect(self):
        for call in (n for n in ast.walk(self.tree) if isinstance(n, ast.Call)):
            expression = self._argument(call)
            if expression is None:
                continue
            scope = self.scopes[call]
            if (scope.node in self.wrappers and isinstance(expression, ast.Name)
                    and expression.id == self.wrappers[scope.node]):
                continue  # its arguments, not its formal name, are audited
            if self._forwarded_criteria(call, expression):
                continue  # independently audited tuple-producing checker module
            # A model_copy may preserve its own receiver's existing ID. This
            # introduces no new ID; every changed alternative still resolves
            # independently. No other object/attribute or namespace is waived.
            if (isinstance(call.func, ast.Attribute) and call.func.attr == "model_copy"
                    and isinstance(call.func.value, ast.Name)):
                receiver = call.func.value.id
                options = (scope.bindings.get(expression.id, []) if isinstance(expression, ast.Name)
                           else [(expression, (), False)])
                values, unknown = set(), set()
                if not options:
                    unknown.add(f"unbound copied rule {ast.unparse(expression)}")
                for value, projection, loop in options:
                    if (not projection and not loop and isinstance(value, ast.Attribute)
                            and value.attr == "rule_id" and isinstance(value.value, ast.Name)
                            and value.value.id == receiver):
                        continue
                    found, errors = self._values(value, scope, projection, loop)
                    values |= found
                    unknown |= errors
            else:
                values, unknown = self._values(expression, scope)
            location = f"{self.path}:{call.lineno}"
            for value in values:
                self.ids.setdefault(value, set()).add(location)
            if unknown:
                self.unresolved.append(f"{location}: {ast.unparse(expression)} ({'; '.join(sorted(unknown))})")
        return self.ids, self.unresolved


def _inventory():
    root = Path(__file__).resolve().parents[2]
    ids, unresolved = {}, []
    for directory in (root / "src/stowage/moves", root / "src/stowage/checker"):
        for path in sorted(directory.rglob("*.py")):
            discovered, unknown = _Emissions(str(path.relative_to(root)), path.read_text()).collect()
            for rule, sites in discovered.items():
                ids.setdefault(rule, set()).update(sites)
            unresolved.extend(unknown)
    return ids, unresolved


def _assert_catalogued(ids, catalogue_ids):
    missing = {rule: sorted(sites) for rule, sites in ids.items() if rule not in catalogue_ids}
    assert not missing, f"Emitted rule IDs missing from catalogue: {missing}"


def test_every_finite_emitted_rule_id_exists_in_catalogue():
    ids, _ = _inventory()
    assert {"expert.e8.under_deck", "expert.e8.on_deck", "iscode.a2_2.gz_area_30_40",
            "iscode.loading.displacement", "expert.e9.over_width_length"} <= ids.keys()
    _assert_catalogued(ids, {rule.id for rule in load_catalogue().rules})


def test_every_rule_id_emission_has_a_finite_auditable_inventory():
    _, unresolved = _inventory()
    assert not unresolved, "Unresolved rule_id emission sites:\n" + "\n".join(unresolved)


def test_audit_detects_a_new_literal_and_constant_backed_emitter():
    source = """
UNKNOWN = 'expert.physical.not_catalogued'
def report(rule):
    return Violation(rule_id=rule)
report(UNKNOWN)
Violation(rule_id='port.synthetic.new_rule')
"""
    ids, unknown = _Emissions("synthetic.py", source).collect()
    assert not unknown
    assert set(ids) == {"expert.physical.not_catalogued", "port.synthetic.new_rule"}
    with pytest.raises(AssertionError, match="missing from catalogue"):
        _assert_catalogued(ids, {rule.id for rule in load_catalogue().rules})


def test_audit_detects_an_unbounded_dynamic_emitter():
    ids, unknown = _Emissions("synthetic.py", "def report(kind):\n    return Violation(rule_id=f'instructions.{kind}')\n").collect()
    assert not ids
    assert len(unknown) == 1 and "kind" in unknown[0]


def test_audit_detects_removing_either_actual_crossing_entry():
    ids, _ = _inventory()
    crossing = {"port.e3.quay_crossing", "port.e3.yard_crossing"}
    assert crossing <= ids.keys()
    catalogue_ids = {rule.id for rule in load_catalogue().rules}
    with pytest.raises(AssertionError, match="missing from catalogue") as caught:
        _assert_catalogued({rule: ids[rule] for rule in crossing}, catalogue_ids - crossing)
    assert all(rule in str(caught.value) for rule in crossing)


def test_finite_loop_expansion_alias_and_non_emission_strings():
    source = """
from stowage.model import Violation as V
def emit(rule):
    return V(rule_id=rule)
for suffix, irrelevant in [('one', 0), ('two', 1)]:
    emit('port.synthetic.' + suffix)
code = 'heel_step'
log('port.synthetic.not_emitted')
"""
    ids, unknown = _Emissions("synthetic.py", source).collect()
    assert not unknown
    assert set(ids) == {"port.synthetic.one", "port.synthetic.two"}


def test_audit_detects_rule_ids_rewritten_on_existing_violations():
    source = "result = violation.model_copy(update={'rule_id': 'port.synthetic.rewritten'})"
    ids, unknown = _Emissions("synthetic.py", source).collect()
    assert not unknown
    assert set(ids) == {"port.synthetic.rewritten"}
    with pytest.raises(AssertionError, match="missing from catalogue"):
        _assert_catalogued(ids, {rule.id for rule in load_catalogue().rules})


def test_conditional_reassignment_cannot_hide_an_unbounded_emitted_id():
    source = """
def report(kind, condition):
    rule = f'instructions.{kind}'
    if condition:
        rule = 'checker.internal_error'
    return Violation(rule_id=rule)
"""
    ids, unknown = _Emissions("synthetic.py", source).collect()
    assert set(ids) == {"checker.internal_error"}
    assert len(unknown) == 1 and "kind" in unknown[0]
