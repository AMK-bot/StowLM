"""Read-only, stdlib D205 medium-plan inventory; no physics or finisher calls.

All inputs are the 18 declared D194 TRAIN ladder maps. Raw stored planner
results take precedence over compacted and post-finisher fallbacks.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
K1B = HERE.parent
FILL = ROOT / 'experiments/mvp/strategy/fillsweep'


def inventory():
    snapshots, warnings = {}, []

    def read(path):
        path = Path(path).resolve()
        relative = path.relative_to(ROOT).as_posix()
        if any(p in {'test', 'test-v3', 'test_v3'} for p in path.parts):
            raise ValueError(f'Forbidden held-out path: {relative}')
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        old = snapshots.setdefault(relative, digest)
        if old != digest:
            raise ValueError(f'Source changed during inventory: {relative}')
        return json.loads(data), relative, digest

    cohort, _, _ = read(FILL / 'cohort.json')
    manifest, _, _ = read(K1B / 'k2/manifest.json')
    if cohort.get('split') != 'train':
        raise ValueError('Expected explicit TRAIN cohort')
    selected = [r for r in cohort['maps'] if r.get('split') == 'train'
                and r.get('fill_target') in (40, 60) and r.get('mass_level') == 'low']
    if len(selected) != 18:
        raise ValueError(f'Expected 18 ladder maps; got {len(selected)}')
    if {r['input'] for r in selected} != set(manifest['inputs']):
        raise ValueError('K2 input set differs from the declared TRAIN ladder')
    declared, by_iid, by_map = [], {}, {}
    for row in sorted(selected, key=lambda r: (r['ship'], r['fill_target'], r['seed'])):
        path = Path(row['input'])
        expected_prefix = 'experiments/mvp/strategy/fillsweep/maps/train/medium/'
        if not path.as_posix().startswith(expected_prefix) or path.is_absolute():
            raise ValueError(f'Not an admitted TRAIN ladder input: {path}')
        payload, source, digest = read(ROOT / path)
        if digest != manifest['inputs'][source] or digest != row.get('sha256', row.get('input_sha256')):
            raise ValueError(f'Input hash mismatch: {source}')
        if payload.get('size_class') != 'medium':
            raise ValueError(f'Non-medium input: {source}')
        if len(payload['load_list']) != row['n_loads']:
            raise ValueError(f'Box-count mismatch: {source}')
        item = {k: row[k] for k in ('map_id', 'ship', 'fill_target', 'mass_level', 'n_loads', 'seed')}
        item.update(instance_id=payload['id'], input=source, input_sha256=digest,
                    split='train', realised_cell_fill=row.get('realised_cell_fill'),
                    physical_cell_demand=row.get('physical_cell_demand'),
                    physical_cell_capacity=row.get('physical_cell_capacity'))
        if item['instance_id'] in by_iid or item['map_id'] in by_map:
            raise ValueError('Duplicate ladder identity')
        declared.append(item)
        by_iid[item['instance_id']] = item
        by_map[item['map_id']] = item

    jobs, examined = [], Counter()

    def add(meta, arm, payload, source, digest, sample, stage):
        plan = payload.get('plan')
        if not isinstance(plan, dict):
            raise ValueError(f'Expected a stored MovePlan: {source}')
        if plan.get('instance_id') != meta['instance_id']:
            raise ValueError(f'Plan/input identity mismatch: {source}')
        jobs.append(dict(meta, arm=arm, sample=sample, source=source,
                         source_sha256=digest, plan=plan, stage=stage))

    for arm, folder in [('8.2 K2', K1B / 'k2'), ('random K2r', K1B / 'k2r')]:
        candidates = folder / 'candidates'
        if not candidates.exists():
            warnings.append(f'{arm}: no stored candidate directory; availability is unknown, not a 0% clear rate.')
        for directory in sorted(candidates.iterdir()) if candidates.exists() else []:
            if not directory.is_dir():
                continue
            if directory.name not in by_iid:
                warnings.append(f'{arm}: ignored non-ladder candidate directory {directory.name}')
                continue
            for path in sorted(directory.glob('sample-*.json')):
                payload, source, digest = read(path)
                examined[arm] += 1
                if payload.get('instance_id') != directory.name:
                    raise ValueError(f'Candidate directory identity mismatch: {source}')
                if payload.get('status') == 'complete':
                    if payload.get('plan') is None:
                        raise ValueError(f'Complete candidate has no plan: {source}')
                    add(by_iid[directory.name], arm, payload, source, digest,
                        payload['sample'], 'pre-finisher-raw')
                elif payload.get('plan') is not None:
                    warnings.append(f'{arm}: non-complete candidate plan excluded: {source}')

        # A published random result is useful only when its raw candidate was
        # not retained. Never count it twice or silently call it pre-finisher.
        if arm == 'random K2r':
            present = {j['instance_id'] for j in jobs if j['arm'] == arm}
            for path in sorted((folder / 'plans').glob('*.json')):
                if path.name.endswith('.report.json') or path.stem not in by_iid or path.stem in present:
                    continue
                payload, source, digest = read(path)
                examined[arm] += 1
                if payload.get('status') != 'ok' or payload.get('plan') is None:
                    warnings.append(f'{arm}: incomplete published fallback excluded: {source}')
                    continue
                add(by_iid[path.stem], arm, payload, source, digest, None,
                    'post-finisher-published-fallback')
                present.add(path.stem)
                warnings.append(f'{arm}: used post-finisher published fallback: {source}')

    for meta in declared:
        folder = FILL / 'runs/main/maps' / meta['map_id']
        for arm, key in [('two-step', 'default'), ('block-rule', 'rule')]:
            evaluation = folder / 'evals' / f'{key}.json'
            if evaluation.exists():
                row, source, _ = read(evaluation)
                if not row.get('solver_complete'):
                    continue
            choices = [(folder / 'plans' / f'{key}.json', 'pre-finisher-raw'),
                       (folder / 'plans' / f'{key}-compact.json', 'pre-finisher-compact-fallback'),
                       (folder / 'finished' / f'{key}-raw.json', 'post-finisher-raw-fallback'),
                       (folder / 'finished' / f'{key}-compact.json', 'post-finisher-compact-fallback')]
            for path, stage in choices:
                if not path.exists():
                    continue
                payload, source, digest = read(path)
                examined[arm] += 1
                if payload.get('plan') is None:
                    continue
                if stage.startswith('pre-finisher') and payload.get('status') != 'ok':
                    warnings.append(f'{arm}: incomplete stored plan excluded: {source}')
                    continue
                if stage.startswith('post-finisher') and not evaluation.exists():
                    warnings.append(f'{arm}: post-finisher fallback lacks complete-plan provenance: {source}')
                    continue
                add(meta, arm, payload, source, digest, None, stage)
                if stage != 'pre-finisher-raw':
                    warnings.append(f'{arm}: used explicitly labelled fallback: {source}')
                break

    arms = ['8.2 K2', 'random K2r', 'two-step', 'block-rule']
    availability = []
    for ship, fill, mass in sorted({(m['ship'], m['fill_target'], m['mass_level']) for m in declared}):
        maps = [m for m in declared if (m['ship'], m['fill_target'], m['mass_level']) == (ship, fill, mass)]
        for arm in arms:
            rows = [j for j in jobs if j['arm'] == arm and
                    (j['ship'], j['fill_target'], j['mass_level']) == (ship, fill, mass)]
            availability.append(dict(arm=arm, ship=ship, fill_target=fill, mass_level=mass,
                                     declared_maps=len(maps), declared_box_counts=[m['n_loads'] for m in maps],
                                     complete_plans=len(rows), maps_with_plan=len({j['map_id'] for j in rows}),
                                     available_box_counts=sorted({j['n_loads'] for j in rows})))
    return dict(created_at=datetime.now(timezone.utc).isoformat(), declared_maps=declared,
                jobs=jobs, availability=availability, examined=dict(examined), warnings=warnings,
                snapshot_hashes=dict(sorted(snapshots.items())))


if __name__ == '__main__':
    result = inventory()
    output = HERE / 'medium-inventory.json'
    output.write_text(json.dumps(result, indent=1, sort_keys=True) + '\n')
    print(json.dumps(dict(output=str(output.relative_to(ROOT)), declared_maps=len(result['declared_maps']),
                          jobs=len(result['jobs']), by_arm=dict(Counter(j['arm'] for j in result['jobs'])),
                          warnings=result['warnings']), sort_keys=True))
