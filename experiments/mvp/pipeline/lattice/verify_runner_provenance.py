"""Cheap native runner handoff verification; never searches or repeats gauges."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from . import store

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def verify(hero_path=HERE / 'fixtures/hero-base.json', *, store_dir=HERE / 'store', expected_catalogue_sha256=None,
           candidate_path=None):
    """Authenticate the handoff using runner helpers, without a plan verdict."""
    from experiments.mvp.pipeline.runner.stored import authenticate_candidate, authenticate_file
    from experiments.mvp.pipeline.runner.assembly import assemble, assembly_metadata
    from experiments.mvp.pipeline.runner.core import _engine_identity
    from experiments.mvp.reader.schema import canonicalize, context_from_instance
    from stowage.frozen import active_stubs, require_clean
    from stowage.model import MovePlan

    store_dir = Path(store_dir).resolve()
    hero_path = Path(hero_path).resolve() if hero_path is not None else None
    hero = json.loads(hero_path.read_text()) if hero_path is not None else None
    key = store.entry_key(hero['instance_sha256'], hero['canonical_rules']) if hero is not None else Path(candidate_path).stem
    catalogue_path = store_dir / 'catalogue.json'
    catalogue_digest = sha256_file(catalogue_path)
    if expected_catalogue_sha256 is not None and catalogue_digest != expected_catalogue_sha256:
        raise ValueError('catalogue differs from the independently pinned digest')
    catalogue = json.loads(catalogue_path.read_text())
    candidate_path = Path(candidate_path).resolve() if candidate_path is not None else store_dir / (key + '.json')
    raw = candidate_path.read_bytes()
    authenticate_file(candidate_path, expected_sha256=catalogue[key], data=raw)
    candidate = json.loads(raw)
    candidate_digest = sha256_json(candidate)
    authenticate_candidate(candidate, expected_sha256=candidate_digest)
    if not store._binding_source_matches(candidate):
        raise ValueError('candidate binding runtime source is missing or stale')
    if key != store.entry_key(candidate['instance_sha256'], candidate['canonical_rules']):
        raise ValueError('stored candidate key differs from exact instance and canonical rules')
    if hero is not None:
        for field in ('instance_sha256', 'canonical_rules', 'engine_version', 'engine_manifest_sha256'):
            if hero[field] != candidate[field]:
                raise ValueError(f'hero fixture {field} differs from authenticated stored candidate')
        if sha256_json(hero['plan']) != sha256_json(candidate['plan']):
            raise ValueError('hero fixture and stored plan differ')
    original_path = ROOT / candidate['instance_path']
    packet = dict(packet_id='lattice-native-provenance-check', created_utc=datetime.now(timezone.utc).isoformat(),
                  synthetic=True, base_instance=dict(path=str(original_path), sha256=sha256_file(original_path)),
                  standing='', voyage_sentences=[], profile='standard')
    instance = assemble(packet)
    metadata = assembly_metadata(packet, instance)
    instance_digest = sha256_json(instance.model_dump(mode='json'))
    if instance_digest != candidate['instance_sha256'] or not metadata['hash_equal']:
        raise ValueError('runner assembly differs from the stored original instance')
    if sha256_json(candidate['instance']) != instance_digest:
        raise ValueError('embedded stored instance differs from original assembly')
    context = context_from_instance(instance)
    canonical_rules = [canonicalize(rule, context) for rule in candidate['canonical_rules']]
    if canonical_rules != candidate['canonical_rules']:
        raise ValueError('stored rule order or canonical envelopes differ from the runner input')
    identity = _engine_identity()
    if not identity['engine_manifest']['passed'] or any(candidate[field] != identity[field] for field in
                                                       ('engine_version', 'engine_manifest_sha256')):
        raise ValueError('runner pinned engine provenance differs from stored provenance')
    plan = MovePlan.model_validate(candidate['plan'])
    require_clean(instance, plan, what='lattice native runner provenance handoff')
    if active_stubs() or plan.instance_id != instance.id:
        raise ValueError('stored plan is stub-tainted or names another instance')
    computed = datetime.fromisoformat(candidate['computed_utc'].replace('Z', '+00:00'))
    if computed.tzinfo is None or candidate.get('mode') != 'stored' or candidate.get('status') != 'checked_plan':
        raise ValueError('stored plan lacks its computed date, stored label, or checked-plan status')
    # Authentication, identity and schemas only. Launch regating remains required.
    return dict(passed=True, verification='runner authentication and identity only; no checker or gauge rerun',
                checked_utc=datetime.now(timezone.utc).isoformat(), hero_fixture=str(hero_path.relative_to(ROOT)) if hero_path else None,
                candidate_file=str(candidate_path.relative_to(ROOT)), candidate_file_sha256=catalogue[key],
                catalogue_sha256=catalogue_digest, runner_stored_sha256=candidate_digest,
                trust_basis='local catalogue snapshot; pin this catalogue digest independently for demo release',
                instance_sha256=instance_digest, canonical_rules=canonical_rules, assembly=metadata,
                engine_version=identity['engine_version'], engine_manifest_sha256=identity['engine_manifest_sha256'],
                binding_source_sha256=candidate['binding_source_sha256'], mode='stored', computed_utc=candidate['computed_utc'],
                binding_compatibility=store.binding_compatibility(candidate),
                checker_rerun=False, gauge_rerun=False, live_search=False, launch_regating_required=True)


def verify_set(*, store_dir=HERE / 'store', fixture_paths=None, expected_catalogue_sha256=None):
    """Authenticate a demo set or every checked stored plan at one catalogue pin."""
    from experiments.mvp.pipeline.runner.stored import authenticate_file
    store_dir = Path(store_dir).resolve()
    catalogue_path = store_dir / 'catalogue.json'
    raw = catalogue_path.read_bytes()
    pin = hashlib.sha256(raw).hexdigest()
    if expected_catalogue_sha256 is not None and pin != expected_catalogue_sha256:
        raise ValueError('catalogue differs from the independently pinned digest')
    catalogue = json.loads(raw)
    entries, skipped = [], []
    paths = [(Path(path), None) for path in fixture_paths] if fixture_paths is not None else []
    if fixture_paths is None:
        for key, digest in sorted(catalogue.items()):
            path = store_dir / (key + '.json')
            authenticate_file(path, expected_sha256=digest)
            row = json.loads(path.read_text())
            if row.get('status') != 'checked_plan':
                skipped.append(dict(key=key, status=row.get('status')))
                continue
            paths.append((None, path))
    for fixture, candidate in paths:
        try:
            entries.append(verify(fixture, store_dir=store_dir, expected_catalogue_sha256=pin, candidate_path=candidate))
        except Exception as error:
            entries.append(dict(passed=False, fixture=str(fixture) if fixture else None,
                                candidate_file=str(candidate) if candidate else None,
                                error=f'{type(error).__name__}: {error}'))
    changed = sha256_file(catalogue_path) != pin
    return dict(passed=bool(entries) and all(row['passed'] for row in entries) and not changed,
                checked_utc=datetime.now(timezone.utc).isoformat(), catalogue_sha256=pin,
                catalogue_changed_during_verification=changed, entries=entries, plans_authenticated=len(entries),
                skipped_nonplans=skipped, verification='runner authentication and identity only; no checker or gauge rerun',
                checker_rerun=False, gauge_rerun=False, live_search=False, launch_regating_required=True)


def _write(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(row, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hero', type=Path, default=HERE / 'fixtures/hero-base.json')
    parser.add_argument('--store-dir', type=Path, default=HERE / 'store')
    parser.add_argument('--output', type=Path, default=HERE / 'runner-provenance.json')
    parser.add_argument('--expected-catalogue-sha256')
    parser.add_argument('--wait', action='store_true')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--demo-set', action='store_true', help='Authenticate hero base, hero replan and backup base fixtures')
    selection.add_argument('--all-stored', action='store_true', help='Authenticate every checked plan in the current catalogue')
    args = parser.parse_args()
    started = dict(pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat(),
                   command=[sys.executable, *sys.argv], status='waiting_for_hero_fixture')
    _write(HERE / 'runner-provenance-process.json', started)
    while args.wait and not args.hero.exists():
        time.sleep(5)
    try:
        if args.demo_set or args.all_stored:
            fixtures = [HERE / 'fixtures' / name for name in ('hero-base.json', 'hero-replan.json', 'backup-base.json')] \
                if args.demo_set else None
            result = verify_set(store_dir=args.store_dir, fixture_paths=fixtures,
                                expected_catalogue_sha256=args.expected_catalogue_sha256)
        else:
            result = verify(args.hero, store_dir=args.store_dir, expected_catalogue_sha256=args.expected_catalogue_sha256)
    except Exception as error:
        result = dict(passed=False, checked_utc=datetime.now(timezone.utc).isoformat(),
                      verification='runner authentication and identity only; no checker or gauge rerun',
                      error=f'{type(error).__name__}: {error}', checker_rerun=False, gauge_rerun=False, live_search=False)
    _write(args.output, result)
    _write(HERE / 'runner-provenance-process.json', dict(started, status='passed' if result['passed'] else 'failed'))
    print(json.dumps({key: value for key, value in result.items() if key != 'entries'}), flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
