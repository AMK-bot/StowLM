"""D275 pipeline-layer and registered-profile checks, staged before installation.

The p21 guard owns source/evidence/weight verification. This adapter adds only
sealed-path protection, pipeline-layer selection and exact registered recipe
matching. It never loads a model or opens an evaluation input.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import importlib
import json
import math
from pathlib import Path

from experiments.mvp.pipeline.sealed.common import HERE, ROOT, HarnessError, object_hash

IDENTITY_FIELDS = ('manifest_sha256', 'tree_digest')
PROFILE_SELECTION = 'experiments/mvp/pipeline/runner/fixtures/profile-selection.json'


def _error(message, drifts=None):
    error = HarnessError(message)
    if drifts is not None:
        error.drifts = drifts
    return error


def _digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _protected(path, root):
    """Reject actual held-out data and sealed results, including resolved aliases."""
    resolved = path.resolve()
    held_out = (
        root/'experiments/mvp/corpus/test-v3',
        root/'experiments/mvp/pipeline/maps/sealed',
        root/'experiments/mvp/reader/v3/sealed',
        root/'experiments/mvp/pipeline/sealed/runs')
    public_manifest = root/'experiments/mvp/corpus/test-v3/manifest.json'
    if resolved != public_manifest and any(resolved.is_relative_to(p) for p in held_out):
        raise _error('Task2.1 manifest cannot open sealed inputs or sealed results: '+str(path))
    return resolved


def _dependencies():
    try:
        guard = getattr(importlib.import_module('experiments.mvp.pipeline.freeze.verify'), 'require_frozen')
        reject_links = getattr(importlib.import_module('experiments.mvp.pipeline.freeze.paths'), 'reject_symlinks')
    except (ImportError, AttributeError) as error:
        raise _error('Task2.1 require_frozen is unavailable; no sealed claim consumed') from error
    return guard, reject_links


def _checked_manifest(path, *, root=None):
    root = Path(root or ROOT).resolve()
    path = Path(path)
    path = path.absolute() if path.is_absolute() else (root/path).absolute()
    guard, reject_links = _dependencies()
    _protected(path, root)
    try:
        # The guard supports a separately supplied manifest outside the repo.
        anchor = root if path.is_relative_to(root) else Path(path.anchor)
        reject_links(path, anchor)
        payload = path.read_bytes()
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get('scope') != 'pipeline' or data.get('pipeline_layer') is not None:
            raise _error('Sealed evaluation requires the D275 pipeline layer, not a film or composite manifest')
        files = data.get('files')
        if not isinstance(files, dict):
            raise _error('Task2.1 files must be an object')
        normalized = {}
        for name, digest in files.items():
            relative = Path(name)
            if not isinstance(name, str) or relative.is_absolute() or '..' in relative.parts or not _digest(digest):
                raise _error('Task2.1 contains an invalid file path or SHA256')
            source = root/relative
            _protected(source, root)
            reject_links(source, root)
            resolved = source.resolve()
            if not resolved.is_relative_to(root) or str(resolved) in normalized:
                raise _error('Task2.1 contains an escaped or aliased file path')
            normalized[str(resolved)] = digest
        identity = guard(path, root=root, full_weights=True)
        if (not isinstance(identity, dict) or any(not _digest(identity.get(k)) for k in IDENTITY_FIELDS)
                or identity['manifest_sha256'] != hashlib.sha256(payload).hexdigest()
                or identity['tree_digest'] != data.get('tree_sha256')
                or path.read_bytes() != payload):
            raise _error('Task2.1 identity differs from the exact verified manifest bytes')
        return data, normalized, {k:identity[k] for k in IDENTITY_FIELDS}
    except HarnessError:
        raise
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as error:
        drifts = getattr(error, 'drifts', None)
        message = ('Frozen pipeline drift: '+json.dumps(drifts, sort_keys=True) if drifts is not None else
                   'Frozen pipeline verification failed: '+str(error))
        raise _error(message, drifts) from error


def verified_identity(path, *, root=None):
    """Run the authoritative full-weight guard; return only its identity fields."""
    return _checked_manifest(path, root=root)[2]


def _plain(value):
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {key:_plain(item) for key,item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def expanded_engine_kwargs(values):
    """Expand native defaults so omitted Config fields cannot hide recipe drift."""
    from engine.config import Config
    if not isinstance(values, dict) or set(values) != {'budget_s', 'finalize_s', 'seed', 'config'}:
        raise _error('Registered engine recipe must name budget_s, finalize_s, seed and config exactly')
    config = values['config']
    if config is None or isinstance(config, dict):
        config = Config(**(config or {}))
    if not is_dataclass(config) or type(config) is not Config:
        raise _error('Runtime engine config must be the frozen native Config')
    for name in ('budget_s', 'finalize_s'):
        if type(values[name]) not in (int, float) or not math.isfinite(values[name]) or values[name] <= 0:
            raise _error('Registered engine budgets must be positive and finite')
    if type(values['seed']) is not int:
        raise _error('Registered engine seed must be an integer')
    return dict(budget_s=float(values['budget_s']), finalize_s=float(values['finalize_s']),
                seed=values['seed'], config=_plain(config))


def _registered_profile(registration):
    arms = registration.get('arms', {})
    names = [arms.get(arm, {}).get('profile') for arm in ('pipeline', 'instruction')]
    if names != ['standard', 'standard']:
        raise _error('Pipeline and instruction arms must register the same named standard profile')
    registered = registration.get('execution', {}).get('registered_profile')
    if registered is not None and (not isinstance(registered, dict) or registered.get('name') != names[0]):
        raise _error('Registered profile record differs from the named arms')
    return names[0], registered


def verify_pipeline_manifest(path, *, registration_path=None, profile_selection_path=None,
                             profile_loader=None, root=None):
    """Return p21 data/file pins after exact named-profile validation.

    Optional paths/loader permit isolated tests and an explicitly staged protocol;
    normal callers use the canonical registration and runner selection. No legacy
    900-second ceiling, invented artifact schema or duplicate source inventory.
    """
    root = Path(root or ROOT).resolve()
    data, files, _ = _checked_manifest(path, root=root)
    registration_path = Path(registration_path or HERE/'preregistration.json')
    registration = json.loads(registration_path.read_text())
    name, registered = _registered_profile(registration)
    selection_path = Path(profile_selection_path or root/PROFILE_SELECTION).resolve()
    if registered is not None:
        named_path = registered.get('profile_selection')
        if not isinstance(named_path, str) or (root/named_path).resolve() != selection_path:
            raise _error('Registered profile-selection path differs')
    expected = files.get(str(selection_path))
    if expected is None:
        raise _error('Task2.1 does not pin the registered profile selection')
    payload = selection_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise _error('Pinned profile selection changed after freeze verification')
    selection = json.loads(payload)
    if selection.get('schema_version') != 'stowlm.profile-selection/1' or selection.get('measured') is not True:
        raise _error('Named profile has no adopted selection record')
    chosen = selection.get('profiles', {}).get(name)
    if not isinstance(chosen, dict):
        raise _error('Registered profile is absent from the pinned selection')
    selected_kwargs = expanded_engine_kwargs(chosen.get('engine_kwargs'))
    timeout = chosen.get('engine_timeout_s')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise _error('Selected engine wall deadline must be positive and finite')
    if profile_loader is None:
        profile_loader = importlib.import_module('experiments.mvp.pipeline.runner.profiles').get_profile
    runtime = profile_loader(name)
    if not isinstance(runtime, dict) or runtime.get('name') != name:
        raise _error('Runtime profile name differs from registration')
    runtime_kwargs = expanded_engine_kwargs({key:runtime.get(key) for key in ('budget_s', 'finalize_s', 'seed', 'config')})
    leader_choice = (selection.get('decision_authority') == 'leader'
        and selection.get('decision_kind') == 'explicit_partial_study_selection'
        and selection.get('user_authorized') is True and selection.get('stopped') is True)
    if runtime.get('pending_measurement') is not False and not (
            'pending_measurement' not in runtime and leader_choice
            and runtime.get('selection_kind') == 'explicit_partial_study_selection'):
        raise _error('Runtime standard is provisional rather than adopted')
    if (object_hash(runtime_kwargs) != object_hash(selected_kwargs)
            or runtime.get('engine_timeout_s') != timeout):
        raise _error('Runtime standard recipe differs from the registered pinned selection')
    if registered is not None:
        for key in ('budget_s', 'finalize_s', 'seed'):
            if registered.get(key) != selected_kwargs[key]:
                raise _error('Selected standard differs from registered '+key)
        if (registered.get('engine_timeout_s') != timeout
                or registered.get('engine_kwargs_sha256') != object_hash(selected_kwargs)):
            raise _error('Selected standard differs from registered engine recipe/deadline hash')
    if selection_path.read_bytes() != payload:
        raise _error('Profile selection changed during recipe verification')
    return data, files
