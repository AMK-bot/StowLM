"""Replacement interpreter; keep the interrupted attempt and its paid decisions.

Native frames are authenticated preparation receipts from the same campaign,
not recomputed by this launcher. Missing keys use the real lattice and binding.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from experiments.mvp.pipeline.contract.schema import sha256_file
from .hero_campaign import HERE, ROOT, OUT as ORIGINAL, save
from .native_runtime import native_caches

OUT = HERE/'fixtures/hero-verified'
SKIP = ('hero_zero_capacity_choices',)


def pin_preparation():
    """One host-created receipt manifest; this is frozen before paid work."""
    path = OUT/'receipt-manifest.json'
    if path.exists():
        raise FileExistsError('Native receipt manifest already pinned')
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ORIGINAL/'store', OUT/'store', dirs_exist_ok=True)
    shutil.copyfile(ORIGINAL/'snapshot.json', OUT/'snapshot.json')
    entries = {}
    for source in (ORIGINAL/'native').glob('*.json'):
        if source.name.startswith('additional-'):
            continue
        destination = OUT/'native'/source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        entries[source.stem] = dict(path=str(destination.relative_to(HERE)), sha256=sha256_file(source),
            original_file=str(source.relative_to(HERE)), original_sha256=sha256_file(source))
    save(path, dict(created_utc=datetime.now(timezone.utc).isoformat(), entries=entries,
        catalogue_sha256=sha256_file(OUT/'store/catalogue.json'),
        freshness_scope='Actual native gates computed during this campaign preparation; prior-process immutable receipts, not new numerical checks.',
        interrupted_attempt=str(ORIGINAL.relative_to(HERE)),
        excluded_first_provider_case=SKIP[0],
        continuation='Seven cases had no provider decision while paused; their first provider invocation is the continuation cohort. Original exposed zero-capacity case remains N/A.'))


def prepared():
    from .native_handoff import NativeHandoff
    from experiments.mvp.pipeline.lattice.store import _catalogue
    manifest = json.loads((OUT/'receipt-manifest.json').read_text())
    store = OUT/'store'
    _catalogue(store, refresh=True, expected_sha256=manifest['catalogue_sha256'])
    catalogue = json.loads((store/'catalogue.json').read_text())
    rows = {}
    checked = {}
    for key, digest in catalogue.items():
        path = store/(key+'.json')
        if sha256_file(path) != digest:
            raise ValueError('Authentic source row changed')
        rows[key] = json.loads(path.read_text())
    for key, entry in manifest['entries'].items():
        path = HERE/entry['path']
        original = HERE/entry['original_file']
        if (not path.resolve().is_relative_to(OUT.resolve()) or sha256_file(path) != entry['sha256']
                or sha256_file(original) != entry['original_sha256']):
            raise ValueError('Native preparation receipt changed')
        if key in rows:
            checked[key] = json.loads(path.read_text())
    handoff = NativeHandoff(store_dir=store, catalogue_sha256=manifest['catalogue_sha256'],
        receipts={key:dict(path=HERE/entry['path'], sha256=entry['sha256'])
                  for key,entry in manifest['entries'].items() if key in rows and rows[key].get('status') != 'proved_refusal'})
    for key, row in rows.items():
        if row.get('status') == 'proved_refusal':
            from experiments.mvp.pipeline.lattice import refusal_lookup
            checked[key] = refusal_lookup(row['instance_sha256'],row['canonical_rules'],store_dir=store)
        else:
            # Fail before any paid call rather than silently launching a cold
            # full native regate when a required preparation receipt is invalid.
            checked[key] = handoff.validated_receipt(row['instance_sha256'],row['canonical_rules'])
        if checked[key] is None:
            raise RuntimeError('Required authenticated native handoff unavailable: '+key)
    snapshot = json.loads((OUT/'snapshot.json').read_text())
    screen = json.loads((ROOT/snapshot['hero']['screening_result']).read_text())
    return dict(snapshot=snapshot, store=store, rows=rows, checked=checked,
                instance=next(iter(rows.values()))['instance'], original=screen['original_binding_plan'],
                failed_gate=json.loads((OUT/'native/original-candidate-gate.json').read_text()),
                lattice_lookup=handoff)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pin-preparation', action='store_true')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.pin_preparation:
        pin_preparation()
        return
    with native_caches():
        value = prepared()
        from . import hero_run
        hero_run.OUT = OUT
        hero_run.validate_native(value)
        if args.validate_only:
            save(OUT/'handoff-validated.json', dict(created_utc=datetime.now(timezone.utc).isoformat(),
                 label='Receipt authenticity, report binding, reader rules, engine and physics identity checked; no new numerical checker invocation.',
                 checked_keys=list(value['checked'])))
            print('All native campaign receipts authenticated; no paid call.', flush=True)
            return
        hero_run.run(value, verified=True, skip_scenarios=SKIP)


if __name__ == '__main__':
    main()
