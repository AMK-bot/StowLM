"""D111 disk-source epochs and durable *between-generation* restart requests.

Hashing changed files does not update modules already imported in a resident MLX
process. Any accepted mask speedup therefore requires process exit, resident GPU
release, a current-code smoke under the shared lock, and fresh-process resume.
This module performs no imports/reloads of production code and no GPU work.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[3]
RESTART_EXIT_CODE = 75
SCHEMA = 'exit-code-boundary-v1'
ACTIVATION_MANIFEST = 'experiments/mvp/exit/patches/d124/activation-manifest.json'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _pins(value):
    if not isinstance(value, Mapping) or not value or any(
            not isinstance(path, str) or not path or not isinstance(digest, str) or not digest
            for path, digest in value.items()):
        raise ValueError('source epoch requires a nonempty path/hash mapping')
    return dict(value)


def source_snapshot(root=None):
    """Match runner source pins using actual bytes, including newly added files."""
    root = Path(root or ROOT)
    paths = [path for base in ('src/llm', 'src/stowage') for path in (root/base).rglob('*')
             if path.is_file() and path.suffix in ('.py', '.json') and '__pycache__' not in path.parts]
    return _pins({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(paths)})


def _speed_path(path):
    parsed = PurePosixPath(path)
    # Reject aliases, traversal and absolute paths rather than broad prefix matches.
    if str(parsed) != path or parsed.is_absolute() or '..' in parsed.parts:
        return False
    return path in ('src/llm/stowlm/mask.py', 'src/llm/stowlm/mask_mlx.py') or (
        parsed.suffix == '.py' and len(parsed.parts) >= 4
        and parsed.parts[:3] in (('src', 'llm', 'securing'), ('src', 'llm', 'maskspeed')))


def _watchdog_activation(old, new, changed):
    """D124 permits only the leader's exact old/new watchdog patch hashes.

    The manifest is outside source pins to avoid a self-referential code hash.
    It grants a process restart plus smoke, never a bypass of either gate.
    """
    try:
        manifest=json.loads((ROOT/ACTIVATION_MANIFEST).read_text())
        if manifest.get('schema')!='exit-d124-activation-v1' or manifest.get('authorization')!='D124':
            return False
        targets=manifest.get('targets')
        if not isinstance(targets,dict) or not targets:return False
        for path,binding in targets.items():
            parsed=PurePosixPath(path)
            if (str(parsed)!=path or parsed.is_absolute() or '..' in parsed.parts
                    or parsed.parts[:3]!=('src','llm','exit') or parsed.suffix!='.py'
                    or path not in changed or not isinstance(binding,dict)):
                return False
            for key,pins in (('old_sha256',old),('new_sha256',new)):
                # A newly introduced watchdog module has no loaded-epoch hash.
                # Require an explicit null binding, never an omitted field or
                # a null binding that hides a previously existing file.
                if key=='old_sha256' and path not in old:
                    if key not in binding or binding[key] is not None:return False
                    continue
                value=binding.get(key)
                if (not isinstance(value,str) or len(value)!=64
                        or any(character not in '0123456789abcdef' for character in value)
                        or pins.get(path)!=value):return False
        return all(path in targets or _speed_path(path) for path in changed)
    except (OSError,ValueError,TypeError,AttributeError):return False


def boundary_plan(loaded_pins, current_pins):
    """Classify disk drift; mixed, deleted or unknown changes fail closed.

    Call only at a completed-generation boundary. The loaded epoch must be the
    snapshot captured by that process, not freshly read pins pretending to be it.
    A speedup classification permits requesting a smoke, never bypassing it.
    """
    old, new = _pins(loaded_pins), _pins(current_pins)
    changed = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    removed = [path for path in changed if path not in new]
    kind = ('unchanged' if not changed else 'mask_speedup'
            if not removed and all(_speed_path(path) for path in changed) else 'checker_watchdog'
            if not removed and _watchdog_activation(old,new,changed) else 'other')
    return dict(changed=changed, removed=removed, kind=kind,
                requires_restart=bool(changed), requires_smoke=kind in ('mask_speedup','checker_watchdog'),
                can_resume=kind != 'other', old_epoch=_digest(old), new_epoch=_digest(new))


def _json_value(value):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'unsupported boundary argument type: {type(value).__name__}')


def save_boundary(path, *, number, population, args, loaded_pins, current_pins):
    """Atomically record next generation/population/sizing, then caller exits75.

    ``number`` is the next not-yet-started generation. Caller must not use this
    helper to restart a generation interrupted by source edits: that is unsafe.
    Existing global GPU reservation is released by the old process before any
    controller-launched smoke. Controller uses smoke_once plus shared reservation.
    """
    plan = boundary_plan(loaded_pins, current_pins)
    if plan['kind'] not in ('mask_speedup','checker_watchdog'):
        raise RuntimeError('restart boundary requires allowlisted mask-speed or hash-authorized D124 changes')
    if type(number) is not int or number < 0:
        raise ValueError('next generation number must be a nonnegative integer')
    if not isinstance(population, list) or not population or any(
            not isinstance(row, dict) or not isinstance(row.get('adapter'), str) or not row['adapter']
            for row in population):
        raise ValueError('restart population requires nonempty adapter records')
    arguments = dict(args) if isinstance(args, Mapping) else vars(args)
    # Materialize exact JSON state now so later mutable caller dictionaries do
    # not change the committed request and invalid numbers cannot be published.
    payload = json.loads(json.dumps(dict(schema=SCHEMA, phase='between_generations',
        exit_code=RESTART_EXIT_CODE, next_generation=number, population=population, args=arguments,
        loaded_pins=_pins(loaded_pins), current_pins=_pins(current_pins), plan=plan,
        utc=datetime.now(timezone.utc).isoformat()), default=_json_value, allow_nan=False))
    record = {**payload, 'state_sha256': _digest(payload)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(record, sort_keys=True, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)
    return record


def restore_boundary(path, current_pins):
    """Read committed state only when fresh disk code matches the requested epoch.

    This validates the restart request, not smoke success; the controller/runner
    must independently require a successful current-code, current-adapter marker.
    """
    record = json.loads(Path(path).read_text())
    if not isinstance(record, dict):
        raise ValueError('restart boundary must be a JSON object')
    payload = {key: value for key, value in record.items() if key != 'state_sha256'}
    if (record.get('schema') != SCHEMA or record.get('phase') != 'between_generations'
            or record.get('exit_code') != RESTART_EXIT_CODE or record.get('state_sha256') != _digest(payload)):
        raise ValueError('restart boundary schema or state content hash mismatch')
    plan = boundary_plan(record['loaded_pins'], record['current_pins'])
    if plan['kind'] not in ('mask_speedup','checker_watchdog') or record.get('plan') != plan:
        raise ValueError('restart boundary source classification mismatch')
    if _pins(current_pins) != record['current_pins']:
        raise RuntimeError('source changed again after restart request; current-code smoke required')
    return record
