"""Read stored starts; compact, rank as K1b, and admit only literal mask replay passes.

No model loading, decoding, finisher, or repair runs here. The hybrid planner applies
its ordinary objective and sight gates after this loader returns.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

from stowage.model import MovePlan

ROOT = Path(__file__).resolve().parents[3]
STRATEGY = ROOT / 'experiments/mvp/strategy'
DATA_ROOT = Path(os.environ.get('HYBRID_ORIGINAL_ROOT', ROOT)).resolve()
DATA_STRATEGY = DATA_ROOT / 'experiments/mvp/strategy'
if str(STRATEGY) not in sys.path:
    sys.path.insert(0, str(STRATEGY))
import compact
from k1b.judge import rank

DEFAULT_PATHS = {
    'none': (),
    'llm': (DATA_STRATEGY / 'k1b/k1c/candidates', DATA_STRATEGY / 'k1b/candidates'),
    'random': (DATA_STRATEGY / 'k1b/k1c-random/candidates', DATA_STRATEGY / 'k1b/k1r/candidates'),
}


def default_paths(arm):
    """The stored 8.2 and random pools, or no starts for the control arm."""
    return DEFAULT_PATHS[arm]


def _display(path):
    try:
        return str(path.relative_to(DATA_ROOT))
    except ValueError:
        return str(path)


def _arm(path):
    for arm, roots in DEFAULT_PATHS.items():
        if any(path.is_relative_to(root) for root in roots):
            return arm
    return 'adapter'


def candidate_files(instance_id, paths):
    """Accept a candidates root, adapter root, per-instance folder, or JSON file.

    Files are never recursively collected across instances. Missing instance
    folders are ordinary incomplete candidate coverage, recorded by the caller.
    """
    if isinstance(paths, (str, Path)):
        paths = (paths,)
    found = []
    seen = set()
    for supplied in paths:
        root = Path(supplied).resolve()
        if root.is_file():
            files = [root] if root.suffix == '.json' else []
        else:
            folders = [root / instance_id, root / 'candidates' / instance_id]
            if root.name == instance_id:
                folders.insert(0, root)
            files = [p for folder in folders for p in sorted(folder.glob('sample*.json'))]
        for path in files:
            path = path.resolve()
            if path not in seen:
                seen.add(path)
                found.append(path)
    return found


def load_starts(instance, paths, limit=3):
    """Return ``([(origin_label, MovePlan), ...], JSON-serializable metadata)``.

    K1b's exact rank() sorts by chosen cost then sample index and deduplicates
    move signatures. Compaction replaces a draft only when strictly cheaper.
    Walk that order until ``limit`` distinct starts have replayed successfully.
    """
    if limit < 0:
        raise ValueError('limit must be nonnegative')
    if isinstance(paths, (str, Path)):
        paths = (paths,)
    paths = tuple(paths)
    files = candidate_files(instance.id, paths)
    metadata = dict(instance_id=instance.id, paths=[_display(Path(p).resolve()) for p in paths],
                    limit=limit, n_files=len(files), n_complete=0, n_distinct=0,
                    candidates=[], admitted=[])
    entries = []
    for index, path in enumerate(files):
        record = dict(path=_display(path), label=f'{_arm(path)}:{_display(path)}',
                      source_arm=_arm(path), admitted=False)
        metadata['candidates'].append(record)
        try:
            raw = path.read_bytes()
            record['sha256'] = hashlib.sha256(raw).hexdigest()
            sample = json.loads(raw)
            record['sample'] = int(sample.get('sample', index))
            if sample.get('instance_id', instance.id) != instance.id:
                record['reason'] = 'candidate instance_id mismatch'
                continue
            data = sample.get('plan') if 'plan' in sample else sample
            if data is None:
                record['reason'] = 'no completed plan'
                continue
            plan = MovePlan.model_validate(data)
            if plan.instance_id != instance.id or plan.call_index != instance.call_index:
                record['reason'] = 'plan instance_id or call_index mismatch'
                continue
            compacted, info = compact.compact_plan(instance, plan)
            original_cost = info['before']['total']
            use_compact = compacted is not None and info['compact_raw']['total'] < original_cost
            chosen = compacted if use_compact else plan
            signature = hashlib.sha256(json.dumps(
                [move.model_dump(mode='json') for move in chosen.moves], sort_keys=True).encode()).hexdigest()
            record.update(cost=info['compact_raw']['total'] if use_compact else original_cost,
                          original_cost=original_cost, compacted=use_compact,
                          signature=signature, compaction=info)
            entries.append(dict(sample=record['sample'], cost=record['cost'],
                                signature=signature, plan=chosen, record=record))
        except Exception as error:
            record['reason'] = f'{type(error).__name__}: {error}'
    ordered = rank(entries)
    metadata.update(n_complete=len(entries), n_distinct=len(ordered))
    unique_records = {id(entry['record']) for entry in ordered}
    for entry in entries:
        if id(entry['record']) not in unique_records:
            entry['record']['reason'] = 'duplicate move signature'
    starts = []
    for position, entry in enumerate(ordered):
        record = entry['record']
        record['rank'] = position
        if len(starts) >= limit:
            record['reason'] = 'admission limit reached'
            continue
        replay = compact.mask_ok(instance, entry['plan'])
        record['mask_replay'] = replay
        if replay is not True:
            record['reason'] = 'mask replay failed'
            continue
        record['admitted'] = True
        starts.append((record['label'], entry['plan']))
        metadata['admitted'].append(dict(label=record['label'], source_arm=record['source_arm'],
                                         path=record['path'], sample=record['sample'],
                                         cost=record['cost'], compacted=record['compacted'],
                                         signature=record['signature']))
    return starts, metadata
