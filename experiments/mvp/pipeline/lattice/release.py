"""Freeze authenticated stored bytes; native launch regating is still required."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from .store import entry_key
from .acceptance import recorded_gate_pass

HERE = Path(__file__).resolve().parent
MINIMUM_INSTRUCTIONS = 10
_SHA256 = re.compile('[0-9a-f]{64}')


def _inside_feature(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE.resolve()):
        raise ValueError('release paths must stay inside lattice')
    return path


def _write_json(path, value):
    path.write_text(canonical_json(value) + '\n')


def _recorded_plan_pass(row):
    return (row.get('status') == 'checked_plan' and isinstance(row.get('instance'), dict)
            and row.get('instance_sha256') == sha256_json(row['instance'])
            and recorded_gate_pass(row['instance'], row.get('plan'), row.get('canonical_rules'), row.get('gate')))


def _fixture_key(path, entries, *, primary_sha256, role):
    raw = path.read_bytes()
    fixture = json.loads(raw)
    if fixture.get('instance_sha256') != primary_sha256:
        raise ValueError(f'{role} fixture is not for the primary hero')
    key = entry_key(fixture['instance_sha256'], fixture['canonical_rules'])
    if key not in entries:
        raise ValueError(f'{role} fixture key is absent from the source catalogue')
    row = entries[key]['row']
    if not _recorded_plan_pass(row):
        raise ValueError(f'{role} does not have a recorded complete gate pass')
    if role == 'shot6-backup' and (len(row['canonical_rules']) != 1 or
            row['canonical_rules'][0].get('constraint', {}).get('type') != 'max_tiers'):
        raise ValueError('shot6-backup must be one primary max_tiers instruction')
    if not isinstance(fixture.get('plan'), dict) or sha256_json(fixture['plan']) != sha256_json(row.get('plan')):
        raise ValueError(f'{role} fixture differs from its authenticated stored plan')
    return key


def freeze_release(*, source_store=None, release_dir=None, hero_path=None, fixtures_dir=None,
                   include_demo=True, expected_catalogue_sha256=None):
    """Publish one immutable-by-convention snapshot, without running a planner.

    Existing destinations are never replaced. Catalogue and entry bytes come
    from one exclusive source lock; demo-store is a separate three-key copy.
    """
    source = _inside_feature(source_store or HERE / 'store')
    hero_path = _inside_feature(hero_path or HERE / 'hero.json')
    fixtures = _inside_feature(fixtures_dir or HERE / 'fixtures')
    now = datetime.now(timezone.utc)
    destination = _inside_feature(release_dir or HERE / 'releases' / now.strftime('%Y%m%dT%H%M%S.%fZ'))
    if destination == source or destination.is_relative_to(source):
        raise ValueError('release metadata and snapshot must be outside the mutable input store')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = None
    with (destination.parent / '.release.lock').open('a+') as publish_lock:
        fcntl.flock(publish_lock, fcntl.LOCK_EX)
        if destination.exists():
            raise FileExistsError(f'release destination already exists: {destination}')
        with (source / '.catalogue.lock').open('a+') as source_lock:
            fcntl.flock(source_lock, fcntl.LOCK_EX)
            catalogue_raw = (source / 'catalogue.json').read_bytes()
            catalogue_pin = hashlib.sha256(catalogue_raw).hexdigest()
            if expected_catalogue_sha256 is not None and catalogue_pin != expected_catalogue_sha256:
                raise ValueError('source catalogue differs from the independently pinned digest')
            catalogue = json.loads(catalogue_raw)
            if not isinstance(catalogue, dict) or not catalogue:
                raise ValueError('source catalogue is empty or malformed')
            entries = {}
            for key, digest in sorted(catalogue.items()):
                if not isinstance(key, str) or not _SHA256.fullmatch(key) or \
                        not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                    raise ValueError('source catalogue contains a malformed key or digest')
                raw = (source / (key + '.json')).read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError(f'source entry failed catalogue authentication: {key}')
                row = json.loads(raw)
                if entry_key(row['instance_sha256'], row['canonical_rules']) != key:
                    raise ValueError(f'source entry has a different instance/rule key: {key}')
                entries[key] = dict(raw=raw, row=row, sha256=digest)
            hero = json.loads(hero_path.read_text())
            primary = hero['hero']['instance_sha256']
            if not isinstance(primary, str) or not _SHA256.fullmatch(primary):
                raise ValueError('hero primary instance SHA256 is malformed')
            instructions = [key for key, value in entries.items()
                            if value['row'].get('instance_sha256') == primary
                            and value['row'].get('canonical_rules')
                            and _recorded_plan_pass(value['row'])]
            if len(instructions) < MINIMUM_INSTRUCTIONS:
                raise ValueError(f'need at least {MINIMUM_INSTRUCTIONS} gated primary instructions; found {len(instructions)}')
            selected = {'shot6-backup': _fixture_key(fixtures / 'shot6-backup.json', entries,
                                                    primary_sha256=primary, role='shot6-backup')}
            if include_demo:
                selected.update({role: _fixture_key(fixtures / (role + '.json'), entries,
                                                   primary_sha256=primary, role=role)
                                 for role in ('hero-base', 'hero-replan')})
                if len(set(selected.values())) != 3:
                    raise ValueError('demo base, replan and shot6 fallback must be three distinct stored keys')
            try:
                stage = Path(tempfile.mkdtemp(prefix='.' + destination.name + '.', dir=destination.parent))
                store_out = stage / 'store'
                store_out.mkdir()
                for key, value in entries.items():
                    (store_out / (key + '.json')).write_bytes(value['raw'])
                (store_out / 'catalogue.json').write_bytes(catalogue_raw)
                demo_pin = None
                if include_demo:
                    demo = stage / 'demo-store'
                    demo.mkdir()
                    demo_catalogue = {key: catalogue[key] for key in sorted(set(selected.values()))}
                    for key in demo_catalogue:
                        (demo / (key + '.json')).write_bytes(entries[key]['raw'])
                    _write_json(demo / 'catalogue.json', demo_catalogue)
                    demo_pin = hashlib.sha256((demo / 'catalogue.json').read_bytes()).hexdigest()
                index = [dict(key=key, source_file_sha256=value['sha256'],
                              instance_sha256=value['row']['instance_sha256'], status=value['row'].get('status'),
                              computed_utc=value['row'].get('computed_utc')) for key, value in entries.items()]
                counts = dict(entries=len(entries), primary_instructions=len(instructions),
                              checked_plans=sum(_recorded_plan_pass(value['row']) for value in entries.values()),
                              bases=sum(value['row'].get('status') == 'checked_plan' and not value['row']['canonical_rules']
                                        for value in entries.values()),
                              proved_refusals=sum(value['row'].get('status') == 'proved_refusal' for value in entries.values()))
                metadata = dict(schema_version='stowlm.lattice-release/1', created_utc=now.isoformat(), mode='stored',
                                verification='authenticated byte copy only; no native gate or search',
                                launch_regating_required=True, native_checks_performed=False,
                                source_store=str(source), release_dir=str(destination),
                                primary_instance_sha256=primary, source_catalogue_sha256=catalogue_pin,
                                catalogue_sha256=catalogue_pin, demo_catalogue_sha256=demo_pin,
                                demo_keys=selected if include_demo else None, shot6_backup_key=selected['shot6-backup'],
                                counts=counts, entries=index)
                _write_json(stage / 'release.json', metadata)
                # The destination lock serializes this API's publishers. Rename
                # exposes both stores and metadata together, never a partial copy.
                if destination.exists():
                    raise FileExistsError(f'release destination already exists: {destination}')
                stage.rename(destination)
                stage = None
                return metadata
            finally:
                if stage is not None:
                    shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-store', type=Path)
    parser.add_argument('--release-dir', type=Path)
    parser.add_argument('--hero', type=Path)
    parser.add_argument('--fixtures-dir', type=Path)
    parser.add_argument('--expected-catalogue-sha256')
    parser.add_argument('--no-demo-store', action='store_true')
    args = parser.parse_args()
    result = freeze_release(source_store=args.source_store, release_dir=args.release_dir, hero_path=args.hero,
                            fixtures_dir=args.fixtures_dir, include_demo=not args.no_demo_store,
                            expected_catalogue_sha256=args.expected_catalogue_sha256)
    print(canonical_json({key: result[key] for key in ('release_dir', 'created_utc', 'catalogue_sha256',
                                                     'demo_catalogue_sha256', 'counts', 'launch_regating_required')}))


if __name__ == '__main__':
    main()
