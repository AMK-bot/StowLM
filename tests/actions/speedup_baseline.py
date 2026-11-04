"""Build an on-disk pre-speedup comparison without undoing concurrent changes.

This helper returns complete source texts. Writing those texts into a frozen
snapshot, before importing stowage, also changes the whole-AST linear dependency
identity; swapping runtime aliases alone does not provide a faithful baseline.
"""
from __future__ import annotations

import ast


_PUBLIC_COMMIT = '''
def _validated_commit(self, trial: Expander, moves: list[Move]) -> list[Move]:
    state = self.world_state()
    try:
        for move in moves:
            state = apply_move(self.instance, state, move)
    except IllegalMoveError as exc:
        raise ValueError(f"unexpandable: {exc.violation.rule_id}: {exc.violation.message}") from exc
    self.__dict__.update(trial.__dict__)
    return moves
'''


def _drop_import_names(node: ast.ImportFrom, names: set[str]):
    node.names = [alias for alias in node.names
                  if (alias.asname or alias.name) not in names]
    return node if node.names else None


class _Uncache(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        if node.name == '_memoize_string':
            return None
        if node.name in {'parse_slot', 'parse_yard', 'cells_of_slot', 'is_valid_container_id'}:
            node.decorator_list = [decorator for decorator in node.decorator_list
                                   if not isinstance(decorator, ast.Name)
                                   or decorator.id != '_memoize_string']
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if any(isinstance(target, ast.Name) and target.id == '_StringResult'
               for target in node.targets):
            return None
        return self.generic_visit(node)

    def visit_ImportFrom(self, node):
        return _drop_import_names(node, {'lru_cache', 'wraps', 'Callable', 'TypeVar'})


class _PublicRestow(ast.NodeTransformer):
    """Remove only Replay dispatch plumbing, preserving current rejection logic."""
    def visit_ImportFrom(self, node):
        if node.module == 'stowage.moves._physical':
            return _drop_import_names(node, {'Replay'})
        if node.module == 'stowage.frozen':
            return _drop_import_names(node, {'FROZEN', 'active_stubs'})
        return node

    def visit_Assign(self, node):
        targets = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if 'public_replay' in targets:
            return None
        if targets == {'ship'} and isinstance(node.value, ast.IfExp):
            if isinstance(node.value.test, ast.Name) and node.value.test.id == 'public_replay':
                return None
        if targets == {'replay'} and isinstance(node.value, ast.IfExp):
            if isinstance(node.value.test, ast.Name) and node.value.test.id == 'public_replay':
                node.value = node.value.body
        return self.generic_visit(node)

    def visit_If(self, node):
        if isinstance(node.test, ast.Name) and node.test.id == 'public_replay':
            return [self.visit(item) for item in node.body]
        return self.generic_visit(node)

    def visit_Call(self, node):
        # Only the D64 prefix verdict consumes this Replay ship snapshot.
        if (isinstance(node.func, ast.Attribute) and node.func.attr == 'evaluate'
                and len(node.args) == 1 and isinstance(node.args[0], ast.Name)
                and node.args[0].id == 'ship'):
            node.args[0] = ast.Attribute(value=ast.Name(id='replay', ctx=ast.Load()),
                                         attr='ship', ctx=ast.Load())
        return self.generic_visit(node)


def _without_anchor(module):
    # The native anchor is owned by these speedups, not the public engine.
    if any(isinstance(node, ast.Name) and node.id == '_NATIVE_APPLY_MOVE'
           and isinstance(node.ctx, ast.Load) for node in ast.walk(module)):
        return module
    body = []
    for node in module.body:
        if isinstance(node, ast.ImportFrom):
            node = _drop_import_names(node, {'_NATIVE_APPLY_MOVE', '_NATIVE_INITIAL_STATE'})
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == '_NATIVE_APPLY_MOVE'
                for target in node.targets):
            node = None
        if node is not None:
            body.append(node)
    module.body = body
    return module


def baseline_sources(sources: dict[str, str]) -> dict[str, str]:
    """Return pre-port source files, retaining unrelated files and owner edits.

    Keys may be package-relative names or full snapshot paths. Already baseline
    files are returned byte for byte, making repeated preparation idempotent.
    """
    result = dict(sources)
    for key, raw in sources.items():
        normalized = str(key).replace('\\', '/')
        if normalized.endswith('conventions.py') and '_memoize_string' in raw:
            module = _Uncache().visit(ast.parse(raw))
        elif normalized.endswith('actions/expander.py') and '_NATIVE_APPLY_MOVE' in raw:
            module = ast.parse(raw)
            for node in ast.walk(module):
                if isinstance(node, ast.ClassDef) and node.name == 'Expander':
                    node.body = [ast.parse(_PUBLIC_COMMIT).body[0]
                                 if isinstance(item, ast.FunctionDef) and item.name == '_validated_commit'
                                 else item for item in node.body]
            module = _without_anchor(module)
        elif normalized.endswith('feasibility/_engine.py') and '_NATIVE_APPLY_MOVE' in raw:
            module = ast.parse(raw)
            for node in ast.walk(module):
                if isinstance(node, ast.ClassDef):
                    node.body = [_PublicRestow().visit(item)
                                 if isinstance(item, ast.FunctionDef) and item.name == '_restow_places'
                                 else item for item in node.body]
            module = _without_anchor(module)
        else:
            continue
        result[key] = ast.unparse(ast.fix_missing_locations(module)) + '\n'
    return result
