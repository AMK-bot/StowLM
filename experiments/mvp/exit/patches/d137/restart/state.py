"""D137 host-only admission manifest and cross-run fresh-map reservations.

No MLX import, model operation, or source install occurs in this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path/'pyproject.toml').is_file())
DEFAULT_OUT = ROOT/'experiments/mvp/exit/deployment-d137'
DEFAULT_MANIFEST = ROOT/'experiments/mvp/exit/patches/d137/installed-manifest.json'


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.d137.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_installed_manifest(path, *, root=ROOT):
    """Require the leader-installed D137 patch's exact content, never apply it.

    Rechecked immediately before each smoke/runner launch. The normal controller
    source pin, epoch-keyed smoke, and runner training receipts still apply.
    """
    path, root = Path(path).resolve(), Path(root).resolve()
    record = json.loads(path.read_text())
    if record.get('schema') != 'exit-d137-installed-v1':
        raise ValueError('D137 installation receipt schema mismatch')
    targets = record.get('targets')
    if not isinstance(targets, dict) or not targets:
        raise ValueError('D137 installation receipt needs nonempty target hashes')
    required = {f'src/llm/exit/{name}.py' for name in ('runner','data','control','selection','dpo')}
    if not required.issubset(targets):
        raise ValueError('D137 receipt omits a required candidate/control/DPO target')
    for relative, expected in targets.items():
        if not isinstance(relative, str):
            raise ValueError('D137 installation target must be a relative path string')
        target = (root/relative).resolve()
        if (Path(relative).is_absolute()
                or not target.is_relative_to(root) or not relative.startswith(('src/','tests/','experiments/'))):
            raise ValueError('D137 installation target escapes approved project files')
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('D137 installation receipt has a malformed SHA256')
        if not target.is_file() or sha(target) != expected:
            raise ValueError(f'D137 installed target does not match receipt: {relative}')
    return dict(path=str(path), sha256=sha(path), targets=len(targets), schema=record['schema'])


def prior_schedule_paths(*, root=ROOT, out=DEFAULT_OUT):
    base = Path(root)/'experiments/mvp/exit'
    return sorted(path.resolve() for path in base.glob('deployment*/schedule.json')
                  if path.parent.resolve() != Path(out).resolve())


def union_prior_reservations(paths):
    """Read only schedule metadata: do not load any held-out or train instances."""
    hashes, seeds, identities, sources = set(), set(), set(), []
    for path in sorted(map(Path, paths)):
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or not isinstance(state.get('generations', {}), dict):
            raise ValueError(f'invalid prior corpus reservation state: {path}')
        local_hashes, local_seeds, local_ids = set(state.get('used', [])), set(state.get('used_seeds', [])), set(state.get('used_ids', []))
        for entries in state.get('generations', {}).values():
            if not isinstance(entries, list):
                raise ValueError('invalid prior generation reservation list')
            for entry in entries:
                if not isinstance(entry, dict) or entry.get('split') != 'train':
                    raise ValueError('prior loop reservation must refer to train only')
                if entry.get('sha256') is not None:local_hashes.add(entry['sha256'])
                if entry.get('seed') is not None:local_seeds.add(entry['seed'])
                if entry.get('instance_id') is not None:local_ids.add(entry['instance_id'])
        if any(not isinstance(h, str) or len(h) != 64 or any(c not in '0123456789abcdef' for c in h) for h in local_hashes):
            raise ValueError('invalid raw input hash in prior schedule')
        if any(type(seed) is not int for seed in local_seeds):
            raise ValueError('invalid seed in prior schedule')
        if any(not isinstance(identity, str) or not identity for identity in local_ids):
            raise ValueError('invalid instance identity in prior schedule')
        hashes |= local_hashes; seeds |= local_seeds; identities |= local_ids
        sources.append(dict(path=str(path.resolve()), sha256=sha(path)))
    return dict(version=2, used=sorted(hashes), used_seeds=sorted(seeds), used_ids=sorted(identities),
                generations={}, validation=None), sources


def initialize_schedule(out, paths):
    """Idempotently seed a new run; reject changed old reservations on restart.

    Once the runner commits a generation, only validate the imported exclusion
    sets remain present; never overwrite live reservations or validation state.
    """
    out = Path(out); schedule_path = out/'schedule.json'
    inherited, sources = union_prior_reservations(paths)
    receipt_path = out/'prior-reservations.json'
    binding = dict(schema='exit-d137-prior-reservations-v1', sources=sources,
        inherited_used=inherited['used'], inherited_used_seeds=inherited['used_seeds'],
        inherited_used_ids=inherited['used_ids'])
    if receipt_path.exists() and json.loads(receipt_path.read_text()) != binding:
        raise RuntimeError('prior schedule inventory changed after restart queue preparation')
    if schedule_path.exists():
        current = json.loads(schedule_path.read_text())
        if not receipt_path.exists():
            raise RuntimeError('existing output has no D137 prior-reservation receipt')
        for name in ('used','used_seeds','used_ids'):
            if not set(inherited[name]).issubset(current.get(name, [])):
                raise RuntimeError('live schedule lost inherited fresh-map exclusions')
    else:
        atomic_json(receipt_path, binding)
        atomic_json(schedule_path, inherited)
    return binding


def frozen_launch_clock(out, *, now=None, minutes=150):
    """Bind a future clock audit to the first admitted generation launch."""
    out = Path(out); path = out/'launch-clock.json'
    if type(minutes) is not int or minutes < 1:
        raise ValueError('audit offset must be positive integer minutes')
    if path.exists():
        record = json.loads(path.read_text())
        started = datetime.fromisoformat(record['first_launch_utc'])
        due = datetime.fromisoformat(record['audit_clock'])
        if (record.get('schema') != 'exit-d137-first-launch-clock-v1' or started.tzinfo is None
                or due.tzinfo is None or record.get('offset_minutes') != minutes
                or due-started != timedelta(minutes=minutes)):
            raise RuntimeError('first-launch audit clock receipt changed')
        return record['audit_clock']
    started = now or datetime.now(timezone.utc)
    if started.tzinfo is None:
        raise ValueError('first launch timestamp must include a timezone')
    record = dict(schema='exit-d137-first-launch-clock-v1', first_launch_utc=started.isoformat(),
                  audit_clock=(started+timedelta(minutes=minutes)).isoformat(), offset_minutes=minutes)
    atomic_json(path, record)
    return record['audit_clock']
