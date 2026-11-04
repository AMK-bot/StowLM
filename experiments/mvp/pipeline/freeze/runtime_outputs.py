"""Validated runtime scratch exclusions shared by the freeze builder and guard."""
from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from .paths import reject_symlinks

SCHEMA = 'stowlm.runtime-outputs/1'
CONFIG = 'experiments/mvp/pipeline/freeze/runtime-outputs.json'
DEFAULT_DIRECTORIES = ('experiments/mvp/frontier/engine/peel_ladder/assignment-ballast',)
# Runtime additions are data, never an open-ended exemption for unknown files.
# HTML is an observed discovery report. Binary cache/tensor data (.bin and
# .safetensors) retain existing supported data-format compatibility; all formats
# still undergo executable-bit and shebang checks.
DATA_SUFFIXES = frozenset({'.json', '.jsonl', '.npz', '.npy', '.csv', '.txt', '.md',
                           '.log', '.lock', '.sqlite', '.html', '.bin', '.safetensors'})
_BROAD = {'src', 'app', 'params', 'data', 'models', 'experiments', 'experiments/mvp',
          'experiments/cache',
          'experiments/mvp/frontier', 'experiments/mvp/frontier/engine',
          'experiments/mvp/frontier/engine/peel_ladder',
          'experiments/mvp/frontier/engine/results', 'experiments/mvp/pipeline'}
_CACHE_ROOTS = {'experiments/cache/' + name for name in ('vessels', 'stability', 'linear')}


def source_path(name, *, root=None):
    """Treat unknown formats, executable bits and shebangs as source.

    A name alone can reject an unknown format. Callers classifying existing
    runtime files must supply the repository root to inspect the actual file.
    """
    if Path(name).suffix.lower() not in DATA_SUFFIXES:
        return True
    if root is not None:
        root = Path(root).resolve()
        path = root / name
        reject_symlinks(path, root)
        if path.stat().st_mode & 0o111:
            return True
        with path.open('rb') as stream:
            if stream.read(2) == b'#!':
                return True
    return False


def contains(name, policy):
    name = Path(name).as_posix()
    directories = policy.get('directories', []) if isinstance(policy, dict) else policy
    return any(name == prefix or name.startswith(prefix + '/') for prefix in directories)


def is_ignored_addition(name, policy, *, root=None):
    return contains(name, policy) and not source_path(name, root=root)


def _names(directories):
    if not isinstance(directories, (list, tuple)) or not all(isinstance(name, str) for name in directories):
        raise ValueError('runtime output directories must be a list of canonical paths')
    result = []
    for name in directories:
        parts = PurePosixPath(name).parts
        if (not name or name.startswith('/') or name != PurePosixPath(name).as_posix()
                or any(char in name for char in '\\*?[]{}\x00\n\r:')
                or any(part in {'.', '..', '.git', '.keys', '.aws', '.ssh', '.codex', '.agents', 'testv3', 'test-v3'} for part in parts)
                or parts[:1] in {('models',), ('params',)}
                or 'sealed' in parts and parts[:4] != ('experiments', 'mvp', 'pipeline', 'sealed')):
            raise ValueError('unsafe runtime output directory: ' + name)
        minimum = 5 if parts[:1] == ('experiments',) else 4
        if (len(parts) < minimum and name not in _CACHE_ROOTS) or name in _BROAD:
            raise ValueError('runtime output directory is too broad: ' + name)
        if name in result:
            raise ValueError('duplicate runtime output directory: ' + name)
        result.append(name)
    result.sort()
    for index, name in builtins.enumerate(result):
        if any(name.startswith(parent + '/') for parent in result[:index]):
            raise ValueError('overlapping runtime output directory: ' + name)
    return result


def enumerate(root, policy):
    """List every runtime leaf, including code hidden under cache directories."""
    root = Path(root).resolve()
    result = []
    for name in policy.get('directories', []):
        start = root / name
        reject_symlinks(start, root)
        if not start.exists():
            continue
        if not start.is_dir():
            raise ValueError('runtime output path is not a directory: ' + name)
        for directory, dirs, files in os.walk(start, followlinks=False):
            for child in [*(Path(directory) / name for name in dirs), *(Path(directory) / name for name in files)]:
                reject_symlinks(child, root)
            result.extend((Path(directory) / filename).relative_to(root).as_posix() for filename in files)
    return sorted(result)


def validate(root, policy, *, import_closure=(), check_contents=True):
    root = Path(root).resolve()
    if not isinstance(policy, dict):
        raise ValueError('runtime output policy must be an object')
    result = dict(policy, directories=_names(policy.get('directories', [])))
    if result.get('schema_version', SCHEMA) != SCHEMA:
        raise ValueError('unsupported runtime output policy schema')
    result['schema_version'] = SCHEMA
    for name in result['directories']:
        path = root / name
        reject_symlinks(path, root)
        if path.exists() and not path.is_dir():
            raise ValueError('runtime output path is not a directory: ' + name)
    for name in import_closure or ():
        if contains(name, result):
            raise ValueError('imported module under runtime output directory: ' + name)
    if check_contents:
        for name in enumerate(root, result):
            if source_path(name, root=root):
                raise ValueError('source file under runtime output directory: ' + name)
    return result


def load(root, *, import_closure=()):
    root = Path(root).resolve()
    path = root / CONFIG
    reject_symlinks(path, root)
    present = path.is_file()
    raw = path.read_bytes() if present else None
    value = json.loads(raw) if present else {'schema_version': SCHEMA, 'directories': list(DEFAULT_DIRECTORIES)}
    if not isinstance(value, dict) or set(value) != {'schema_version', 'directories'}:
        raise ValueError('runtime output config requires schema_version and directories only')
    policy = dict(value, config_path=CONFIG, config_present=present,
                  config_sha256=hashlib.sha256(raw).hexdigest() if present else None)
    return validate(root, policy, import_closure=import_closure)


def _pinned_config_snapshot(policy, expected):
    """Recover only a snapshot whose complete config bytes match the old pin.

    A policy edit must not expand an older manifest's runtime exclusions. The
    supported config serializations let verification retain the old exclusion
    set while its ordinary file audit reports changed config bytes. Unknown
    serializations fail closed rather than trusting unauthenticated metadata.
    """
    value = {key: policy[key] for key in ('schema_version', 'directories')}
    for indent in (None, 2):
        for ending in ('', '\n'):
            raw = (json.dumps(value, sort_keys=True, indent=indent) + ending).encode()
            if hashlib.sha256(raw).hexdigest() == expected:
                return value
    raise ValueError('runtime output config differs from frozen pin')


def from_manifest(root, manifest, *, allow_changed_config=False):
    """Use the frozen snapshot; legacy manifests inherit no new exclusions."""
    policy = manifest.get('selection', {}).get('runtime_outputs')
    if policy is None:
        return {'schema_version': SCHEMA, 'directories': []}
    if not isinstance(policy, dict) or policy.get('config_path') != CONFIG:
        raise ValueError('invalid frozen runtime output policy')
    if policy.get('config_present') is True:
        expected = manifest.get('files', {}).get(CONFIG)
        if not expected or policy.get('config_sha256') != expected:
            raise ValueError('runtime output config is not independently pinned')
        path = Path(root).resolve() / CONFIG
        reject_symlinks(path, Path(root).resolve())
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            if not allow_changed_config:
                raise ValueError('runtime output config differs from frozen pin')
            value = _pinned_config_snapshot(policy, expected)
        else:
            value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'directories'}
                or value.get('schema_version') != SCHEMA
                or _names(value['directories']) != _names(policy.get('directories', []))):
            raise ValueError('runtime output directories differ from pinned config')
    elif (policy.get('config_present') is not False or policy.get('config_sha256') is not None
          or _names(policy.get('directories', [])) != list(DEFAULT_DIRECTORIES)):
        raise ValueError('invalid runtime output fallback policy')
    closure = manifest.get('selection', {}).get('import_closure', {}).get('paths', [])
    return validate(root, policy, import_closure=closure, check_contents=False)
