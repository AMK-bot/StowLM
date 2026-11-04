"""Pinned real-tool hero campaign. Paid phase requires a separate freeze marker."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .native_runtime import native_caches

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE / 'fixtures' / 'hero-final'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    temporary.write_text(json.dumps(value, indent=2, default=str) + '\n')
    temporary.replace(path)


def prepare():
    from experiments.mvp.pipeline.lattice import lookup, refusal_lookup, gate_plan
    from experiments.mvp.pipeline.lattice.store import _catalogue
    source = ROOT / 'experiments/mvp/pipeline/lattice'
    hero = json.loads((source / 'hero.json').read_text())['hero']
    store = OUT / 'store'
    store.mkdir(parents=True, exist_ok=True)
    catalogue = json.loads((source / 'store/catalogue.json').read_text())
    selected = {}
    rows = {}
    for key, digest in catalogue.items():
        path = source / 'store' / (key + '.json')
        if sha256_file(path) != digest:
            raise ValueError('Catalogue changed during snapshot')
        row = json.loads(path.read_text())
        if row['instance_sha256'] != hero['instance_sha256']:
            continue
        if not key.startswith(('9381', '72cc', '3398', 'e47b')):
            continue
        shutil.copyfile(path, store / path.name)
        selected[key] = digest
        rows[key] = row
    save(store / 'catalogue.json', selected)
    pinned = sha256_file(store / 'catalogue.json')
    _catalogue(store, refresh=True, expected_sha256=pinned)
    snapshot = dict(created_utc=datetime.now(timezone.utc).isoformat(), hero=hero,
                    source_hero_sha256=sha256_file(source / 'hero.json'),
                    source_catalogue_sha256=sha256_file(source / 'store/catalogue.json'),
                    catalogue_sha256=pinned,
                    catalogue_projection='Primary hero baseline, proved refusal, DG clearance and bay closure only.',
                    scenarios_sha256=sha256_file(HERE / 'fixtures/hero-scenarios.json'))
    save(OUT / 'snapshot.json', snapshot)
    checked = {}
    for key, row in rows.items():
        start = time.perf_counter()
        print('Native warm starting', row.get('id', key), flush=True)
        if row.get('status') == 'proved_refusal':
            value = refusal_lookup(hero['instance_sha256'], row['canonical_rules'], store_dir=store)
        else:
            value = lookup(hero['instance_sha256'], row['canonical_rules'], store_dir=store)
        save(OUT / 'native' / (key + '.json'), value)
        checked[key] = value
        print('Native warm complete', key[:12], bool(value), round(time.perf_counter()-start, 3), flush=True)
    screen = json.loads((ROOT / hero['screening_result']).read_text())
    original = screen['original_binding_plan']
    instance = next(row['instance'] for row in rows.values())
    start = time.perf_counter()
    print('Native original-candidate gate starting', flush=True)
    failed = gate_plan(instance, original, [], base_plan=original)
    save(OUT / 'native/original-candidate-gate.json', failed)
    print('Native original-candidate gate complete', failed.get('passed'), round(time.perf_counter()-start, 3), flush=True)
    prepared = dict(snapshot=snapshot, store=store, rows=rows, checked=checked,
                    instance=instance, original=original, failed_gate=failed)
    save(OUT / 'ready.json', dict(created_utc=datetime.now(timezone.utc).isoformat(),
                                 checked={key: bool(v) for key,v in checked.items()},
                                 original_passed=failed.get('passed')))
    return prepared


def main():
    with native_caches():
        prepared = prepare()
        marker = OUT / 'run-authorization.json'
        while not marker.exists():
            print('Native evidence ready; waiting for final code/test freeze.', flush=True)
            time.sleep(30)
        from .hero_run import run
        run(prepared)


if __name__ == '__main__':
    main()
