"""D110 bounded whole-pool replay selection and source-epoch admission."""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[3]


def _resolve(path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _instance_tokens(instance, raw=None):
    canonical = json.dumps(instance, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    tokens = {'canonical:' + hashlib.sha256(canonical).hexdigest()}
    if raw is not None:
        tokens.add('raw:' + hashlib.sha256(raw).hexdigest())
    tokens.update(f'{key}:{instance[key]}' for key in ('id', 'seed') if instance.get(key) is not None)
    return tokens


def _metadata(path):
    row = json.loads(Path(path).read_text())
    source = row.get('instance_path') or row.get('input_path')
    instance = row.get('instance') or {}
    if not source and not instance:
        raise ValueError(f'replay row lacks instance identity: {path}')
    source = _resolve(source) if source else None
    raw = source.read_bytes() if source else None
    if raw is not None:
        instance = json.loads(raw)
    tokens = _instance_tokens(instance, raw)
    digest = (hashlib.sha256(raw).hexdigest() if raw is not None else
              hashlib.sha256(json.dumps(instance, sort_keys=True).encode()).hexdigest())
    parts = source.parts if source else ()
    rung = instance.get('size_class') or row.get('rung')
    ship = (instance.get('family') or {}).get('vessel')
    if 'train' in parts:
        at = parts.index('train')
        if len(parts) > at + 2:
            rung, ship = parts[at + 1:at + 3]
    return dict(path=Path(path), source=source, digest=digest,
                identities=tokens, rung=rung or '?',
                ship=ship or (instance.get('vessel') or {}).get('id') or '?')


def choose_replay(pool, current_paths, generation, limit):
    """Seeded, rung/ship-balanced sample from *all* currently available records.

    Reads metadata only; expensive current-epoch verification occurs for the
    bounded selected rows during dataset admission. A duplicate map contributes
    once, preferring its first record (callers put earlier winners before gold).
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError('replay limit must be a nonnegative integer')
    if not limit:
        return []
    current = {_resolve(path) for path in current_paths}
    excluded = set()
    for path in current:
        raw = path.read_bytes()
        excluded |= _instance_tokens(json.loads(raw), raw)
    buckets, seen = defaultdict(list), set()
    for path in pool:
        row = _metadata(path)
        if row['source'] in current or row['identities'] & (excluded | seen):
            continue
        # Full identity admission also runs on every selected row in data.py.
        if row['source'] and any(part in ('val', 'test', 'showcase') for part in row['source'].parts):
            continue
        seen |= row['identities']
        buckets[row['rung'], row['ship']].append(row)
    rng = random.Random(generation)
    names = sorted(buckets)
    rng.shuffle(names)
    queues = {}
    for name in names:
        values = sorted(buckets[name], key=lambda row: (row['digest'], str(row['path'])))
        rng.shuffle(values)
        queues[name] = deque(row['path'] for row in values)
    result = []
    while len(result) < limit and any(queues.values()):
        for name in names:
            if queues[name] and len(result) < limit:
                result.append(queues[name].popleft())
    return result


def checker_epoch():
    """Hash actual checker/physics sources; version strings alone miss patches."""
    from stowage.versions import CHECKER_VERSION
    digest = hashlib.sha256(CHECKER_VERSION.encode())
    # Conservative scope includes helper physics, move count and rule dependencies.
    for path in sorted((ROOT / 'src/stowage').rglob('*.py')):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def outcome_dependency_id():
    """Identity of the outcome dependencies this process loaded (source + params/hull/tokenizer/packages). Cached per
    process: replay admission runs per row. Cache writes re-assert it against disk."""
    from llm.exit.outcome_epoch import epoch_id, process_snapshot
    return epoch_id(process_snapshot())


def assert_outcome_dependencies_current():
    from llm.exit.outcome_epoch import assert_current, process_snapshot
    assert_current(process_snapshot())


def ensure_current_replay(path, instance, plan, report, cache_dir, *, verifier=None):
    """Readmit a selected replay target at the current bound checker source epoch.

    Never changes gold artifacts. Unknown epochs are stale, so existing gold is
    conservatively rechecked once into this dataset's owned cache. Cached reports
    remain bound to both exact inputs via report_matches_inputs.
    """
    from stowage.checker import CHECKER_VERSION, report_matches_inputs
    from stowage.model import CheckReport, MovePlan
    from llm.exit.selection import Candidate, verify_candidate
    epoch, dependencies = checker_epoch(), outcome_dependency_id()
    def current(row):  # checker source AND the params/hull/tokenizer it consumed (rev-v4-j P2)
        return row.get('exit_checker_epoch') == epoch and row.get('exit_outcome_dependency_id') == dependencies
    record = json.loads(Path(path).read_text())
    parsed = CheckReport.model_validate(report)
    def bound(value, target):
        return (value.passed and value.provenance.versions.get('checker') == CHECKER_VERSION
                and report_matches_inputs(value, instance, target))
    if not bound(parsed, plan):
        # Old version reports are allowed only through a fresh complete check.
        if parsed.provenance.versions.get('checker') == CHECKER_VERSION:
            raise ValueError('replay checker report is not passed and bound to its inputs')
    if current(record) and bound(parsed, plan):
        return plan, report, False
    key = hashlib.sha256((instance.model_dump_json() + plan.model_dump_json()).encode()).hexdigest()
    target = Path(cache_dir) / f'{key}.record.json'
    if target.exists():
        cached = json.loads(target.read_text())
        cached_plan = MovePlan.model_validate(cached['plan'])
        cached_report = CheckReport.model_validate(cached['report'])
        if current(cached) and bound(cached_report, cached_plan):
            return cached_plan, cached['report'], False
    result = (verifier or verify_candidate)(instance, Candidate(plan, 'replay'))
    if not result.passed or result.plan is None or not result.details.get('report'):
        raise ValueError('replay target failed current-epoch full verification')
    fresh = CheckReport.model_validate(result.details['report'])
    if not bound(fresh, result.plan):
        raise ValueError('replay verifier returned an unbound report')
    if checker_epoch() != epoch:
        raise RuntimeError('checker source changed during replay verification')
    assert_outcome_dependencies_current()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(instance=instance.model_dump(mode='json'),
        plan=result.plan.model_dump(mode='json'), report=result.details['report'],
        exit_checker_epoch=epoch, exit_outcome_dependency_id=dependencies, replay_source=str(path))))
    return result.plan, result.details['report'], True


def adapter_training_hashes(adapters):
    """Return verified training-input hashes, or None for incomplete lineage.

    Missing data or an inherited adapter whose training inventory cannot be
    established makes the honest map label 'new_to_loop'. No guesses from gold.
    """
    hashes, visited, pending = set(), set(), list(adapters)
    while pending:
        adapter = _resolve(pending.pop())
        if adapter in visited:
            continue
        visited.add(adapter)
        config_path = Path(adapter) / 'adapter_config.json'
        if not config_path.exists():
            return None
        config = json.loads(config_path.read_text())
        train = config.get('stowlm_train_config') or {}
        dataset = (train.get('data_dir') or train.get('data') or train.get('dataset')
                   or train.get('dataset_dir'))
        # Parent-lineage completeness must be explicit; an adapter's latest
        # dataset alone does not prove what all ancestors trained on.
        complete = config.get('exit_training_input_hashes')
        if isinstance(complete, list) and all(isinstance(value, str) and len(value) == 64 for value in complete):
            hashes.update(complete)
            continue
        if not dataset:
            return None
        sidecar_path = adapter / 'adapters.safetensors.provenance.json'
        if not sidecar_path.exists():
            return None
        sidecar = json.loads(sidecar_path.read_text())
        training = sidecar.get('training') or {}
        recorded_config = training.get('config') or {}
        parents = (sidecar.get('provenance') or {}).get('parents', [])
        parent = (train.get('extra') or {}).get('exit_parent')
        if (recorded_config.get('data_dir') != train.get('data_dir') or
                (recorded_config.get('extra') or {}).get('exit_parent') != parent):
            return None
        if parent:
            pending.append(parent)
        elif any(not item.startswith(('base-model-sha256:', 'sft-data-sha256:',
                    'sft-entries-sha256:', 'resumed-from-checkpoint-adapter-sha256:')) for item in parents):
            return None
        file_hashes = (training.get('data_identity') or {}).get('files_sha256') or {}
        train_file = _resolve(dataset) / 'train.jsonl'
        expected = file_hashes.get('train.jsonl')
        if not expected or not train_file.is_file():
            return None
        raw = train_file.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            return None
        for line in raw.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            value = ((row.get('meta') or {}).get('gold') or {}).get('input_sha256')
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                return None
            hashes.add(value)
    return hashes
