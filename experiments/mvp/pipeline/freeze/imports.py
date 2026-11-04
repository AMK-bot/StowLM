"""Conservative import closure in an isolated, imports-only meta-path interpreter.

Original module bodies are never executed. The loader follows all import nodes,
including lazy imports and literal import_module calls, but never imports an
external dependency. Thus graph discovery cannot load MLX, a model, or network.
"""
from __future__ import annotations

import ast
import importlib
import importlib.abc
import importlib.util
import json
from functools import lru_cache
from pathlib import Path
import subprocess
import sys

ENTRYPOINTS = (
    'app.__main__', 'app.v6', 'experiments.mvp.pipeline.runner.core',
    'experiments.mvp.pipeline.reader_final.reader',
    'experiments.mvp.pipeline.radio.read_transcripts',
    'experiments.mvp.pipeline.sealed.launch', 'experiments.mvp.pipeline.sealed.driver',
    'experiments.mvp.pipeline.intake', 'experiments.mvp.pipeline.explain',
    'experiments.mvp.pipeline.copilot',
)


def allowed(root, path):
    relative = path.relative_to(root)
    if 'sealed' in relative.parts and relative.parts[:4] != ('experiments', 'mvp', 'pipeline', 'sealed'):
        raise ValueError(f'sealed data excluded: {relative}')
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f'symlink excluded: {relative}')
        if parent == root:
            break
    return relative.as_posix()


class ImportGraph(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, root, excluded_roots=()):
        self.root = root
        self.excluded_roots = []
        for name in excluded_roots:
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                raise ValueError(f'invalid import exclusion root: {name}')
            self.excluded_roots.append(root / relative)
        self.prefixes = (root, root / 'src', root / 'experiments/mvp/frontier',
                         root / 'experiments/mvp/frontier/lab')
        self.paths = set()
        self.entries = {}

    def excluded(self, path):
        return any(path == prefix or path.is_relative_to(prefix) for prefix in self.excluded_roots)

    @lru_cache(maxsize=None)
    def children(self, path):
        return {child.name for child in path.iterdir()} if path.is_dir() else set()

    @lru_cache(maxsize=None)
    def locate(self, name):
        for prefix in self.prefixes:
            base = prefix.joinpath(*name.split('.'))
            path = base.with_suffix('.py')
            if not self.excluded(path) and path.is_file() and path.name in self.children(base.parent):
                allowed(self.root, path)
                return path, False
            if not self.excluded(base) and base.is_dir():
                allowed(self.root, base)
                path = base / '__init__.py'
                return (path if path.is_file() else None), True
        return None

    def find_spec(self, fullname, path=None, target=None):
        found = self.locate(fullname)
        if found is None:
            return None
        self.entries[fullname] = found
        return importlib.util.spec_from_loader(fullname, self, is_package=found[1])

    def create_module(self, spec):
        return None

    def follow(self, name):
        if name and self.locate(name) is not None:
            importlib.import_module(name)

    def exec_module(self, module):
        path, package = self.entries[module.__name__]
        if path is None:
            return
        self.paths.add(allowed(self.root, path))
        source = ast.parse(path.read_text(), filename=str(path))
        context = module.__name__ if package else module.__name__.rpartition('.')[0]
        for node in ast.walk(source):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.follow(alias.name)
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ''
                if node.level:
                    name = importlib.util.resolve_name('.' * node.level + name, context)
                self.follow(name)
                for alias in node.names:
                    if alias.name != '*':
                        self.follow(name + '.' + alias.name)
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                called = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
                if called in {'import_module', '__import__'} and isinstance(node.args[0].value, str):
                    name = node.args[0].value
                    self.follow(importlib.util.resolve_name(name, context) if name.startswith('.') else name)


def _discover(root, entrypoints, excluded_roots=()):
    hook = ImportGraph(root, excluded_roots)
    sys.meta_path.insert(0, hook)
    present, missing = [], []
    try:
        for name in entrypoints:
            if hook.locate(name) is None:
                missing.append(name)
            else:
                present.append(name)
                hook.follow(name)
    finally:
        sys.meta_path.remove(hook)
    return {'paths': sorted(hook.paths), 'entrypoints': present, 'missing_entrypoints': missing,
            'excluded_roots': sorted(str(path.relative_to(root)) for path in hook.excluded_roots),
            'method': 'subprocess meta-path import-only AST loader; follows lazy imports and literal dynamic imports; no original module body or external import executes'}


def discover(root, entrypoints=ENTRYPOINTS, *, excluded_roots=None):
    if excluded_roots is None:
        from experiments.mvp.pipeline.freeze.product_outputs import EXCLUDED_ROOTS
        excluded_roots = EXCLUDED_ROOTS
    result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), str(Path(root).resolve()),
                             json.dumps(list(entrypoints)), json.dumps(list(excluded_roots))],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError('import closure failed: ' + result.stderr.strip())
    return json.loads(result.stdout)


if __name__ == '__main__':
    print(json.dumps(_discover(Path(sys.argv[1]).resolve(), json.loads(sys.argv[2]),
                               json.loads(sys.argv[3]) if len(sys.argv) > 3 else ()), sort_keys=True))
