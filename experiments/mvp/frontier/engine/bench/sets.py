"""Fixed holdout selections from approved stored metadata, before engine results."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
FRONTIER = HERE.parents[1]
ROOT = FRONTIER.parents[2]
LAB = FRONTIER / 'lab'
sys.dont_write_bytecode = True
sys.path.insert(0, str(LAB))
from catalog import ensure_safe, load_set


def stored_rows(directory):
    rows = []
    for path in sorted(directory.glob('*.json')):
        row = json.loads(path.read_text())
        if isinstance(row, dict) and row.get('instance_id') and row.get('input'):
            ensure_safe(row['input'])
            rows.append((path, row))
    return sorted(rows, key=lambda item: item[1]['instance_id'])


def select():
    """No planner outcomes participate, except the predeclared V6 certification rule."""
    quick = {size: set(load_set('quick_' + size)) for size in ('tiny', 'small')}
    result = {}
    sources = {}
    for size, directory in (('small', 'val-0.9'), ('tiny', 'tiny48-0.9')):
        rows = stored_rows(FRONTIER / 'runs' / directory)
        name = 'val_' + size
        result[name] = [row['input'] for _, row in rows
                        if f'.{size}.' in row['instance_id'] and row['input'] not in quick[size]]
        sources[name] = [str(path.relative_to(ROOT)) for path, row in rows
                         if row['input'] in result[name]]
    medium = list(load_set('quick_medium'))
    sources['medium'] = ['experiments/mvp/frontier/lab/sets/quick_medium.txt']
    other = []
    for path in sorted((ROOT / 'experiments/mvp/scale/runs/v6/maps').glob('*/summary.json')):
        row = json.loads(path.read_text())
        job = row.get('job', {})
        if (row.get('checker_passed') is True and row.get('full_checker_unmodified') is True
                and job.get('split') == 'train' and job.get('input') not in medium):
            rel = ensure_safe(job['input'])
            if rel not in [p for p, _ in other]:
                other.append((rel, str(path.relative_to(ROOT))))
    # At most five additional certificates, sorted by evidence path, without engine results.
    for rel, source in other[:5]:
        medium.append(rel)
        sources['medium'].append(source)
    cohort_path = ROOT / 'experiments/mvp/strategy/blockrule/cohort-medium.json'
    cohort = json.loads(cohort_path.read_text())
    assert cohort['split'] == 'train'
    rows = [row for row in cohort['maps'] if row['split'] == 'train']
    for ship in sorted({row['ship'] for row in rows}):
        row = min((r for r in rows if r['ship'] == ship),
                  key=lambda r: (r['n_loads'], r['seed'], r['input']))
        rel = ensure_safe(row['input'])
        if rel not in medium:
            medium.append(rel)
    sources['medium'].append(str(cohort_path.relative_to(ROOT)))
    result['medium'] = medium
    for paths in result.values():
        assert len(paths) == len(set(paths))
        for path in paths:
            ensure_safe(path)
    return result, sources


def build():
    selections, sources = select()
    manifest = dict(schema_version=1, selection_uses_engine_results=False,
        rules={
            'val_small': 'Every validation small stored row in val-0.9, ordered by instance id, excluding quick_small.',
            'val_tiny': 'Every validation tiny stored row in tiny48-0.9, ordered by instance id, excluding quick_tiny.',
            'medium': 'quick_medium first; up to five other V6 full-checker-certified TRAIN inputs in sorted summary-path order; then smallest n_loads D185 map per sorted ship class, ties by seed then path. Deduplicate preserving order.',
        }, sources=sources, sets=selections,
        inputs={rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
                for paths in selections.values() for rel in paths})
    for name, paths in selections.items():
        path = HERE / f'{name}.txt'
        path.write_text(''.join(p + '\n' for p in paths))
        assert load_set(path) == paths
    (HERE / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print({name: len(paths) for name, paths in selections.items()})


if __name__ == '__main__':
    build()
