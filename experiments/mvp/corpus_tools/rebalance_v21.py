"""D90 inventory-only amendment. Stop the producer before --execute.

No existing instance, audit, tiny request or evaluation artifact is rewritten.
APPROXIMATION: val rounds 35/35/30 percent to 25/24/21 instances; train is exact.
Generation and the full checker remain separate stages.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from build import CORPUS, FAMILIES, VESSELS, atomic_json, sha256, shard_path

SETTINGS = {
    'feeder_500': {'booking_slack': .25, 'booking_mass_fraction': .55},
    'feeder_800': {'booking_slack': .25, 'booking_mass_fraction': .45},
    'feeder_1700': {'booking_slack': .35, 'booking_mass_fraction': .45},
}
QUOTAS = {'train': (945, 945, 810), 'val': (25, 24, 21)}


def committed(spec, root):
    path = shard_path(spec, root)
    audit = path.with_suffix('.audit.json')
    if not audit.exists():
        if path.exists():
            raise ValueError(f'uncommitted instance; finish/repair interrupted draw first: {path}')
        return False
    value = json.loads(audit.read_text())
    if (value.get('config') != spec or value.get('accepted') is not True
            or value.get('rule_freeze_v1_enabled') is not False
            or not path.exists() or sha256(path) != value.get('instance_sha256')):
        raise ValueError(f'invalid committed shard: {path}')
    return True


def protected_hashes(root):
    """Pin actual data and audits, including all tiny and evaluation files."""
    return {str(p.relative_to(root)): sha256(p)
            for split in ('test', 'showcase', 'train', 'val')
            for p in sorted((root / split).rglob('*.json')) if p.is_file()}


def plan(root):
    root = Path(root)
    inventories = {}
    old_requests = {}
    reserved = set()
    for name in ('test', 'train', 'val'):
        path = root / f'{name}.requested.json'
        if path.exists():
            request = json.loads(path.read_text())
            reserved.update(s['seed'] for s in request['specs'])
            if name != 'test':
                old_requests[name] = request
    for path in (root / 'showcase').glob('*.audit.json'):
        reserved.add(json.loads(path.read_text())['config']['seed'])
    for split, quota in QUOTAS.items():
        old = old_requests[split]
        tiny = [s for s in old['specs'] if s['rung'] == 'tiny']
        small = [s for s in old['specs'] if s['rung'] == 'small']
        if not small or any(s['rung'] == 'medium' for s in old['specs']):
            raise ValueError('expected D84 tiny/small inventory')
        groups = {}
        for vessel, count in zip(VESSELS, quota):
            existing = [s for s in small if s['vessel'] == vessel]
            if any(any(s.get(k) != v for k, v in SETTINGS[vessel].items()) for s in existing):
                raise ValueError(f'cannot change existing settings: {vessel}')
            if any(committed(s, root) for s in existing[count:]):
                raise ValueError(f'completed shards exceed D90 prefix quota: {split}/{vessel}')
            groups[vessel] = existing[:count]
        # First two retained alternating prefixes determine the exact family
        # remainder for the new 1700 draws. Train totals 1350/1350, val 35/35.
        target_family = sum(quota) // 2
        counts = Counter(s['generator'] for rows in groups.values() for s in rows)
        if any(n > target_family for n in counts.values()):
            raise ValueError('retained prefixes cannot meet global family balance')
        next_seed = {'train': 31_100_000, 'val': 41_100_000}[split]
        for vessel, count in zip(VESSELS, quota):
            rows = groups[vessel]
            while len(rows) < count:
                while next_seed in reserved:
                    next_seed += 1
                i = len(rows)
                family = min(FAMILIES, key=lambda f: (counts[f], (FAMILIES.index(f) - i) % 2))
                template = deepcopy(small[0])
                template.update(vessel=vessel, seed=next_seed, generator=family,
                    port='laem_chabang_like' if i % 4 < 2 else 'singapore_like',
                    noise=(.2, .5, .8)[(i // 6) % 3], **SETTINGS[vessel])
                rows.append(template)
                reserved.add(next_seed)
                next_seed += 1
                counts[family] += 1
        if set(counts.values()) != {target_family}:
            raise ValueError(f'unbalanced families: {counts}')
        # Train-first is enforced by campaign.jobs. Within its small phase put
        # new 1700 draws first in each rotation, enabling immediate streaming.
        ordered = [groups[v][i] for i in range(max(quota))
                   for v in ('feeder_1700', 'feeder_500', 'feeder_800') if i < len(groups[v])]
        updated = deepcopy(old)
        updated.update(specs=tiny + ordered, ship_settings=SETTINGS,
                       resize_decision='D90', corpus_version='v2.1',
                       counts={'tiny': len(tiny), 'small': sum(quota), 'medium': 0},
                       small_counts_by_ship=dict(zip(VESSELS, quota)))
        inventories[split] = updated
    return inventories


def apply(root, *, check_frozen=None):
    """Inventory-only mutation, idempotent and fail-closed on changed data."""
    root = Path(root)
    inventories = plan(root)
    snapshot = (check_frozen or protected_hashes)(root)
    archive = root / 'archive/v2-before-D90'
    archive.mkdir(parents=True, exist_ok=True)
    for name in ('train.requested.json', 'val.requested.json', 'token_allocation.json'):
        source = root / name
        backup = archive / name
        if source.exists() and not backup.exists():
            backup.write_bytes(source.read_bytes())
    for split, request in inventories.items():
        atomic_json(root / f'{split}.requested.json', request)
    allocation_path = root / 'token_allocation.json'
    allocation = json.loads(allocation_path.read_text())
    allocation['ship_settings'] = SETTINGS
    allocation['allocation'].update(method='D90 qualified-small three-ship mix; preserved accepted tiny',
        small_counts_by_ship={s: dict(zip(VESSELS, q)) for s, q in QUOTAS.items()},
        resize_decision='D90')
    atomic_json(allocation_path, allocation)
    if (check_frozen or protected_hashes)(root) != snapshot:
        raise ValueError('protected corpus data changed during amendment; stop concurrent writer')
    result = {'decision': 'D90', 'revision': 'v2.1',
              'requested': {s: r['counts'] for s, r in inventories.items()},
              'small_counts_by_ship': {s: r['small_counts_by_ship'] for s, r in inventories.items()},
              'ship_settings': SETTINGS, 'protected_hashes': snapshot,
              'manifest_refresh_required': True}
    atomic_json(root / 'D90-inventory-amendment.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=CORPUS)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute:
        result = apply(args.root)
        print(json.dumps({k: v for k, v in result.items() if k != 'protected_hashes'}, indent=2))
    else:
        value = plan(args.root)
        print(json.dumps({'dry_run': True, 'small_counts_by_ship':
            {s: row['small_counts_by_ship'] for s, row in value.items()}, 'ship_settings': SETTINGS}, indent=2))


if __name__ == '__main__':
    main()
