"""Load canonical dense KCS campaign instances for format sizing.

The campaign manifest is the source of truth: select its lowest seed per
vessel/generator profile and verify the exact shard SHA256 before validation.
No cargo, hull, yard, physics limit or provenance field is modified here.
Source: experiments/instances-v2/{manifest.json,README.md} (campaign 2.0.0).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from stowage.io import load_instance
from stowage.conventions import canonical_vessel_id

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'experiments/instances-v2/manifest.json'


def load_canonical_dense(vessel_name: str, *, generator: str = 'random_yard'):
    """Return the lowest-seed manifested dense Instance and its verified metadata."""
    vessel_name = canonical_vessel_id(vessel_name)
    profiles = {'feeder_500': ('realistic_500', 400),
                'feeder_800': ('realistic_800', 800)}
    if vessel_name not in profiles or generator not in {'random_yard', 'simulated_yard'}:
        raise ValueError('Expected feeder_500/feeder_800 and random_yard/simulated_yard')
    profile, loads = profiles[vessel_name]
    payload = MANIFEST.read_bytes()
    manifest = json.loads(payload)
    group_key = next((key for key in manifest['groups']
                      if key.split('/', 1)[0] == generator
                      and canonical_vessel_id(key.split('/', 1)[1]) == profile), None)
    if group_key is None:
        raise ValueError(f'No canonical dense profile {generator}/{profile}')
    group = manifest['groups'][group_key]
    candidates = [row for row in group['shards']
                  if row['spec']['dense_20ft'] is True
                  and canonical_vessel_id(row['spec']['vessel']) == vessel_name
                  and row['spec']['n_boxes'] == loads]
    if not candidates:
        raise ValueError(f'No canonical dense shard for {group_key}')
    row = min(candidates, key=lambda row: (row['seed'], row['instance']))
    path = MANIFEST.parent / group_key / row['instance']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != row['sha256']:
        raise ValueError(f'Canonical shard SHA256 mismatch: {path}')
    instance = load_instance(path)
    if len(instance.load_list) != loads or instance.family != row['family']:
        raise ValueError(f'Canonical shard facts disagree with manifest: {path}')
    return instance, dict(
        source=str(path.relative_to(ROOT)), source_sha256=digest,
        manifest_source=str(MANIFEST.relative_to(ROOT)),
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        campaign_version=manifest['campaign_version'], manifest_group=group_key,
        seed=row['seed'], spec=row['spec'], manifest_group_count=group['count'],
    )
