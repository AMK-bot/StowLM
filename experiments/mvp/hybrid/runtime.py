"""Seal executed project code and resources independently of the shared checkout.

The snapshot keeps repository-relative paths, including a pyproject marker for
parameter discovery. Numerical caches remain shared, read-only inputs. Large
qualification records are individual shared links with explicit byte hashes;
all Python evidence is copied, never linked. No model weights are copied.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FORMAT = 'hybrid-runtime-v1'
MANIFEST = 'runtime.json'
HYBRID_MODULES = ('run.py', 'worker.py', 'planner.py', 'candidates.py', 'tables.py',
                  'report.py', 'runtime.py', 'validation.py', 'admission.py', 'processes.py')
FRONTIER_MODULES = ('planner.py', 'alns.py', 'blocked.py', 'blocks.py', 'master.py',
                    'sequence.py', 'sight.py', 'audit.py')
STRATEGY_MODULES = ('compact.py', 'blockrule/rule.py', 'blockrule/capacity_candidate.py',
                    'headroom/layout.py', 'k1b/judge.py')
NUMERICAL_SUFFIXES = frozenset(('.json', '.npz', '.npy', '.csv', '.toml'))
CACHE_GROUPS = ('vessels', 'linear', 'stability', 'feasibility')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stat(path):
    value = Path(path).stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _relative(path, root):
    path = Path(path)
    path = path if path.is_absolute() else root / path
    path = path.absolute()
    if not path.is_relative_to(root) or '..' in path.relative_to(root).parts:
        raise ValueError(f'asset outside source repository: {path}')
    return str(path.relative_to(root))


def _resource_paths(value):
    """Geometry and evidence references, not every arbitrary path in a report."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key == 'offsets_path' or
                    key == 'path' and isinstance(value.get('sha256'), str)):
                yield item
            yield from _resource_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _resource_paths(item)


def _inventory(root, sources):
    files = set(sources)
    files.add('pyproject.toml')
    for base in ('src', 'params'):
        files.update(str(path.relative_to(root)) for path in (root / base).rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts
                     and path.suffix not in ('.pyc', '.pyo') and path.name != '.DS_Store')
    for base, names in (('experiments/mvp/hybrid', HYBRID_MODULES),
                        ('experiments/mvp/frontier', FRONTIER_MODULES),
                        ('experiments/mvp/strategy', STRATEGY_MODULES)):
        files.update(f'{base}/{name}' for name in names if (root / base / name).is_file())
    # Helpers may be added by another executive before the final seal. Capture
    # every immediate hybrid runtime module without walking runs/snapshots.
    files.update(str(path.relative_to(root)) for path in
                 (root / 'experiments/mvp/hybrid').glob('*.py')
                 if not path.name.startswith('test_'))
    helper = 'experiments/mvp/gold_tools/driver.py'
    if (root / helper).is_file():
        files.add(helper)
    # The CPU mask replay loads this tokenizer directly; it never loads weights.
    tokenizer = 'models/glimmer-30b-4bit/tokenizer.json'
    if (root / tokenizer).is_file():
        files.add(tokenizer)
    for name in tuple(files):
        if name.startswith('params/') and Path(name).suffix == '.toml':
            data = tomllib.loads((root / name).read_text())
            for reference in _resource_paths(data):
                relative = _relative(reference, root)
                if not (root / relative).is_file():
                    raise FileNotFoundError(f'parameter asset missing: {relative}')
                files.add(relative)
    shared = set()
    bank = root / 'src/stowage/linear/margin_policy_data.json'
    if bank.is_file():
        for reference in _resource_paths(json.loads(bank.read_text())):
            relative = _relative(reference, root)
            if not (root / relative).is_file():
                raise FileNotFoundError(f'qualification evidence missing: {relative}')
            if Path(relative).suffix == '.py':
                files.add(relative)
            elif relative not in files:
                shared.add(relative)
    return sorted(files), sorted(shared)


def _identity(binding):
    return hashlib.sha256(_canonical({key: binding[key] for key in
        ('format', 'files', 'shared_reads', 'links', 'transforms', 'original_root')})).hexdigest()


def _members(root):
    files, links = set(), set()
    for folder, directories, names in os.walk(root, followlinks=False):
        for name in tuple(directories):
            path = Path(folder) / name
            if path.is_symlink():
                links.add(str(path.relative_to(root)))
                directories.remove(name)
        for name in names:
            path = Path(folder) / name
            (links if path.is_symlink() else files).add(str(path.relative_to(root)))
    return files, links


def _numeric_tree(path):
    """Only numeric resources qualify for a compact shared subtree link."""
    for folder, directories, names in os.walk(path, followlinks=False):
        if any((Path(folder) / name).is_symlink() for name in directories):
            return False
        if any(Path(name).suffix not in NUMERICAL_SUFFIXES for name in names):
            return False
    return True


def _cache_links(repo_root, tree):
    """Reuse numerical caches around individually pinned qualification files.

    A collision-free numeric subtree needs one link, which avoids expanding the
    160k-file linear cache into every job binding. An already-created evidence
    directory is mirrored sparsely. Python/code files are never linked. The
    project import guard also rejects external origins if a source tree later
    gains a Python file; the worker write boundary rejects shared-cache writes.
    """
    links = {}

    def mirror(source, target):
        relative = str(target.relative_to(tree))
        if target.is_symlink() or target.is_file():
            return  # Already copied or individually pinned in shared_reads.
        if source.is_dir() and not source.is_symlink():
            if not target.exists() and _numeric_tree(source):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source, target_is_directory=True)
                links[relative] = str(source)
                return
            target.mkdir(parents=True, exist_ok=True)
            for child in sorted(source.iterdir()):
                mirror(child, target / child.name)
        elif source.is_file() and source.suffix in NUMERICAL_SUFFIXES:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
            links[relative] = str(source)

    for group in CACHE_GROUPS:
        source = repo_root / 'experiments/cache' / group
        if source.is_dir():
            mirror(source, tree / 'experiments/cache' / group)
    return links


def seal_runtime(sources, *, destination=HERE / 'runtime', repo_root=ROOT):
    """Copy one verified source epoch and return its JSON-serializable binding.

    Call only after all implementation edits finish. Existing snapshots are
    verified and reused; they are never updated in place. The destination must
    be inside the hybrid experiment (or the equivalent directory in a fixture).
    """
    repo_root = Path(repo_root).resolve()
    destination = Path(destination).resolve()
    if not destination.is_relative_to(repo_root / 'experiments/mvp/hybrid'):
        raise ValueError('runtime destination must stay inside hybrid')
    destination.mkdir(parents=True, exist_ok=True)
    names, shared = _inventory(repo_root, sources)
    staging = Path(tempfile.mkdtemp(prefix='.sealing-', dir=destination))
    tree = staging / 'root'
    tree.mkdir()
    copied, shared_reads, transforms = {}, {}, {}
    try:
        for name in names:
            relative = _relative(name, repo_root)
            source = repo_root / relative
            before = _stat(source)
            raw = source.read_bytes()
            original_hash = hashlib.sha256(raw).hexdigest()
            if relative in sources and original_hash != sources[relative]:
                raise ValueError(f'source changed before runtime seal: {relative}')
            if before != _stat(source):
                raise ValueError(f'source changed during runtime seal: {relative}')
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o444)
            copied[relative] = hashlib.sha256(raw).hexdigest()
        for name in shared:
            source = repo_root / name
            before = _stat(source)
            digest = _hash(source)
            if before != _stat(source):
                raise ValueError(f'qualification evidence changed during seal: {name}')
            shared_reads[name] = dict(path=str(source), sha256=digest)
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
        links = _cache_links(repo_root, tree)
        binding = dict(format=FORMAT, files=copied, shared_reads=shared_reads,
                       links=links, transforms=transforms, original_root=str(repo_root))
        binding['id'] = _identity(binding)
        final = destination / binding['id']
        binding['root'] = str(final / 'root')
        (staging / MANIFEST).write_bytes(_canonical(binding) + b'\n')
        (staging / MANIFEST).chmod(0o444)
        if final.exists():
            verify_runtime(binding)
            shutil.rmtree(staging)
        else:
            staging.rename(final)
            verify_runtime(binding)
        return binding
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_runtime(binding, *, stat_cache=None):
    """Reject changed/injected code, resources, links, or qualification bytes.

    Default verification hashes every shared record. A long-lived controller
    may supply a private stat_cache: an unchanged device/inode/size/mtime/ctime
    tuple reuses a previously verified byte hash, while changes force rehashing.
    Copied code/resources are always rehashed. No live source file is consulted.
    """
    if binding.get('format') != FORMAT or binding.get('id') != _identity(binding):
        raise ValueError('invalid runtime binding identity')
    root = Path(binding['root']).resolve()
    if root.name != 'root' or root.parent.name != binding['id']:
        raise ValueError('runtime path does not match its identity')
    if json.loads((root.parent / MANIFEST).read_text()) != binding:
        raise ValueError('runtime manifest differs from sealed binding')
    files, symlinks = _members(root)
    if files != set(binding['files']) or symlinks != set(binding['links']) | set(binding['shared_reads']):
        raise ValueError('runtime membership changed')
    for name, expected in binding['files'].items():
        if _hash(root / name) != expected:
            raise ValueError(f'runtime file changed: {name}')
    for name, expected in binding['links'].items():
        if (root / name).resolve() != Path(expected).resolve():
            raise ValueError(f'runtime cache link changed: {name}')
    for name, record in binding['shared_reads'].items():
        path = root / name
        if Path(os.readlink(path)) != Path(record['path']):
            raise ValueError(f'runtime evidence link changed: {name}')
        before = _stat(path)
        key = (str(path), record['sha256'])
        if stat_cache is None or stat_cache.get(key) != before:
            if _hash(path) != record['sha256'] or before != _stat(path):
                raise ValueError(f'runtime evidence changed: {name}')
            if stat_cache is not None:
                stat_cache[key] = before
    return True


def _import_paths(binding):
    root = Path(binding['root'])
    return [root / 'experiments/mvp/hybrid', root / 'experiments/mvp/frontier',
            root / 'experiments/mvp/strategy', root / 'src', root]


def runtime_environment(binding):
    """Environment additions; merge these after the controller's ordinary env."""
    root = Path(binding['root'])
    return dict(PYTHONPATH=os.pathsep.join(map(str, _import_paths(binding))),
                HYBRID_RUNTIME_ROOT=str(root),
                HYBRID_RUNTIME_MANIFEST=str(root.parent / MANIFEST),
                STOWAGE_VESSEL_CACHE_DIR=str(root / 'experiments/cache/vessels'),
                PYTHONDONTWRITEBYTECODE='1')


class _ProjectImportGuard(importlib.abc.MetaPathFinder):
    def __init__(self, binding):
        self.root = Path(binding['root']).resolve()
        self.files = set(binding['files'])
        self.names = {path.name for path in (self.root / 'src').iterdir() if path.is_dir()}
        self.names.add('experiments')
        self.names.update(path.stem for base in _import_paths(binding)[:3]
                          for path in base.glob('*.py'))
        self.names.update(path.name for base in _import_paths(binding)[:3]
                          for path in base.iterdir() if path.is_dir())

    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] not in self.names:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None:
            raise ImportError(f'project import absent from sealed runtime: {fullname}')
        if spec.origin and spec.origin not in ('namespace', 'built-in', 'frozen'):
            source = Path(spec.origin).resolve()
            if not source.is_relative_to(self.root) or str(source.relative_to(self.root)) not in self.files:
                raise ImportError(f'project import outside sealed runtime: {fullname}: {source}')
        for location in spec.submodule_search_locations or ():
            if not Path(location).resolve().is_relative_to(self.root):
                raise ImportError(f'project namespace outside sealed runtime: {fullname}: {location}')
        return spec


def activate_runtime(binding):
    """Worker-only import isolation, before importing any project modules.

    This also blocks editable-install import finders from falling back to live
    project packages missing from the snapshot. Third-party site-packages stay.
    """
    root = Path(binding['root']).resolve()
    original = Path(binding['original_root']).resolve()
    def live_path(value):
        path = Path(value or os.getcwd()).resolve()
        return not path.is_relative_to(root) and (path == original or any(
            path.is_relative_to(original / base) for base in ('src', 'experiments', 'params')))
    sys.path[:] = list(map(str, _import_paths(binding))) + [p for p in sys.path if not live_path(p)]
    assert_runtime_imports(binding)
    sys.meta_path.insert(0, _ProjectImportGuard(binding))
    install_resource_paths(binding)
    assert_runtime_imports(binding)


def install_resource_paths(binding):
    """Reuse gold's scoped absolute-resource relocation, without changing bytes.

    Only these two copied modules' Path names are replaced. The bank's original
    SHA and cached certificate identity stay intact. Python's global pathlib,
    open functions, and external input/candidate paths are unaffected.
    """
    root = Path(binding['root']).resolve()
    modules = ('stowage.linear.margin_policy', 'stowage.generate.physics_identity')
    present = [name for name in modules
               if f"src/{name.replace('.', '/')}.py" in binding['files']]
    if not present:
        return []
    helper = 'experiments/mvp/gold_tools/driver.py'
    if helper not in binding['files']:
        raise ImportError('sealed runtime lacks the gold resource-path helper')
    from experiments.mvp.gold_tools.driver import make_epoch_resource_path
    proxy = make_epoch_resource_path(root, binding['files'].keys() | binding['shared_reads'].keys(),
                                     binding['original_root'])
    for name in present:
        module = importlib.import_module(name)
        filename = Path(module.__file__).resolve()
        if not filename.is_relative_to(root / 'src') or str(filename.relative_to(root)) not in binding['files']:
            raise ImportError(f'cannot relocate live-module resources: {name}')
        module.Path = proxy
        if hasattr(module, '_ROOT'):
            module._ROOT = proxy(module._ROOT)
    return present


def assert_runtime_imports(binding):
    """Check actual loaded project origins, including direct-file imports."""
    root = Path(binding['root']).resolve()
    original = Path(binding['original_root']).resolve()
    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, '__file__', None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if path.is_relative_to(root):
            relative = str(path.relative_to(root))
            if relative not in binding['files']:
                raise ImportError(f'unsealed runtime import: {name}: {path}')
        elif any(path.is_relative_to(original / base) for base in ('src', 'experiments')):
            raise ImportError(f'live project import: {name}: {path}')
    return True
