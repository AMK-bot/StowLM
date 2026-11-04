#!/usr/bin/env python3
"""Create a deterministic, metadata-proven TRAIN-only headroom allowlist.

Reads only selected training instance bytes. Other split metadata in manifests is
not an input to sampling. Never discovers maps by unrestricted filesystem glob.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CORPUS = ROOT / 'experiments/mvp/corpus'
MANIFESTS = [CORPUS / 'manifest.json', CORPUS / 'archive/v1/manifest.json',
             CORPUS / 'archive/v2-before-D90/manifest.json',
             CORPUS / 'archive/D126/manifest.pre-D126.json']


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train_path(entry, manifest):
    """Resolve only explicit train/<size>/... manifest records."""
    rel = Path(entry['path'])
    if entry.get('split') != 'train' or rel.is_absolute() or '..' in rel.parts or rel.parts[0] != 'train':
        raise ValueError('not an explicit train manifest record')
    # Historical manifests retain paths relative to their former corpus root.
    roots = [manifest.parent, CORPUS]
    for base in roots:
        path = base / rel
        if path.is_file():
            return path
    return None


def blocks_of(instance):
    """One physical hatch cover/group × nonempty hold or deck region.

    Separate port/starboard covers are separate hatch groups. Empty regions are
    excluded. Stack IDs are restricted to real stacks and actual vessel cells.
    """
    stacks = {s['id']: s for s in instance['vessel']['stacks']}
    cell_ids = {c['id'] for c in instance['vessel']['cells']}
    blocks = []
    for cover in instance['vessel']['hatch_covers']:
        for region in ('hold', 'deck'):
            matching = [s for s in stacks.values()
                        if s.get('hatch_cover_id') == cover['id']
                        and bool(s['on_deck']) == (region == 'deck')]
            stack_ids = sorted(s['id'] for s in matching if set(s['cell_ids']) & cell_ids)
            cells = sorted({c for s in matching for c in s['cell_ids'] if c in cell_ids})
            if cells:
                blocks.append({'id': cover['id'] + '/' + region, 'hatch_cover_id': cover['id'],
                               'region': region, 'stack_ids': stack_ids,
                               'cell_count': len(cells)})
    covered = {s for b in blocks for s in b['stack_ids']}
    uncovered = sorted(s['id'] for s in stacks.values()
                       if set(s['cell_ids']) & cell_ids and s['id'] not in covered)
    return blocks, uncovered


def select_balanced(entries, count):
    """Round-robin ship×generator strata, SHA order (independent of outcomes)."""
    groups = defaultdict(list)
    for entry, manifest in entries:
        groups[(entry['ship'], entry.get('family', 'unknown'))].append((entry, manifest))
    for rows in groups.values():
        rows.sort(key=lambda pair: hashlib.sha256(('D175-headroom-v1:' + pair[0]['path']).encode()).hexdigest())
    selected = []
    taken = Counter()
    ships = sorted({key[0] for key in groups})
    while len(selected) < min(count, len(entries)):
        for ship in ships:
            keys = [key for key in groups if key[0] == ship and groups[key]]
            if keys and len(selected) < count:
                key = min(keys, key=lambda key: (taken[key], key))
                selected.append(groups[key].pop(0))
                taken[key] += 1
    return selected


def build(small_count=40):
    active = json.loads(MANIFESTS[0].read_text())
    population = [(e, MANIFESTS[0]) for e in active['files']
                  if e.get('split') == 'train' and e.get('rung') == 'small'
                  and e.get('active_for_current_release', True)]
    medium = []
    seen_medium = set()
    unavailable = []
    for manifest in MANIFESTS:
        if not manifest.is_file():
            continue
        for entry in json.loads(manifest.read_text()).get('files', []):
            if entry.get('split') != 'train' or entry.get('rung') != 'medium':
                continue
            path = train_path(entry, manifest)
            identity = entry.get('sha256') or (entry.get('ship'), entry.get('seed'))
            if identity in seen_medium:
                continue
            if path is None:
                unavailable.append({'manifest': str(manifest.relative_to(ROOT)), 'path': entry['path']})
                continue
            seen_medium.add(identity)
            medium.append((entry, manifest))
    maps = []
    for entry, manifest in select_balanced(population, small_count) + sorted(medium, key=lambda pair: pair[0]['path']):
        path = train_path(entry, manifest)
        actual_hash = sha256(path)
        if entry.get('sha256') and actual_hash != entry['sha256']:
            raise ValueError(f'manifest SHA mismatch: {path}')
        instance = json.loads(path.read_text())
        blocks, uncovered = blocks_of(instance)
        loads = set(instance['load_list'])
        cargo = [b for b in instance['containers'] if b['id'] in loads]
        categories = Counter((b['pod'], b.get('weight_class', 'unknown')) for b in cargo)
        map_id = f"{entry['rung']}-{entry['ship']}-{entry.get('family', 'unknown')}-{entry['seed']:09d}"
        maps.append({'map_id': map_id, 'input': str(path.relative_to(ROOT)), 'size': entry['rung'],
                     'ship': entry['ship'], 'family': entry.get('family'), 'seed': entry.get('seed'),
                     'split': 'train', 'sha256': actual_hash, 'manifest': str(manifest.relative_to(ROOT)),
                     'manifest_entry_path': entry['path'], 'release_epoch': entry.get('release_epoch'),
                     'schema_version': instance.get('schema_version'),
                     'source_binding_status': entry.get('source_binding_status'),
                     'n_loads': len(cargo), 'n_onboard': len(instance['onboard']['placements']),
                     'hatch_count': len(instance['vessel']['hatch_covers']), 'block_count': len(blocks),
                     'blocks': blocks, 'uncovered_stack_ids': uncovered,
                     'loaded_ports': sorted({b['pod'] for b in cargo}),
                     'declared_weight_classes': sorted({b.get('weight_class', 'unknown') for b in cargo}),
                     'loaded_port_weight_counts': [{'port': p, 'weight_class': w, 'count': n}
                                                   for (p, w), n in sorted(categories.items())]})
    return {'format': 'D175-headroom-train-allowlist-v1',
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'selection': {'small_requested': small_count, 'small_population_manifest_count': len(population),
                          'method': 'round-robin ship×family strata; SHA256(D175-headroom-v1:+path) order',
                          'assumption': 'Order estimated about40 small maps; current population is larger. Study selects40 current train small without outcome selection.',
                          'medium': 'all distinct, present, explicit train/medium records in four corpus manifests; SHA checked; historical epochs preserved'},
            'manifest_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in MANIFESTS if p.is_file()},
            'counts': dict(Counter(m['size'] for m in maps)),
            'counts_by_ship_size': dict(Counter(m['ship'] + ':' + m['size'] for m in maps)),
            'unavailable_medium': unavailable, 'maps': maps}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--small-count', type=int, default=40)
    parser.add_argument('--output', type=Path, default=HERE / 'train_maps.json')
    args = parser.parse_args()
    if args.small_count < 1:
        parser.error('--small-count must be positive')
    result = build(args.small_count)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(args.output), 'counts': result['counts'],
                      'counts_by_ship_size': result['counts_by_ship_size'],
                      'blocks': str(Counter((m['size'], m['block_count']) for m in result['maps']))}))

if __name__ == '__main__':
    main()
