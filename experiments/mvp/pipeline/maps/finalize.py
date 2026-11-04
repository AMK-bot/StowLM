"""Publish accepted cohorts using the completed POST scan, without rescanning.

Only status/audit/cache metadata are decoded. Map bytes, including sealed
maps, are read solely by SHA-256. External development outputs are never read.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path

import generate
from capacity import wait_for_slot
from seed_audit import find_seeds

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = HERE / 'runtime'


def metadata(name):
    return json.loads((HERE / name).read_text())


def resolve_post(request):
    """Recognize only exact, newly used public dev identities in path hits."""
    pre_path = HERE / 'SEED-AUDIT-PRE.json'
    pre = metadata(pre_path.name)
    assert pre['disjoint'] is True and not pre['collisions'] and not pre['errors']
    assert generate.sha(pre_path) == request['seed_audit_sha256']
    roots = {row['seed'] for row in request['specs']}
    assert set(pre['seeds']) == roots
    raw_path = HERE / 'SEED-AUDIT-POST-RAW.json'
    if raw_path.exists():
        raw_bytes = raw_path.read_bytes()
    else:
        raw_bytes = (HERE / 'SEED-AUDIT-POST.json').read_bytes()
    raw = json.loads(raw_bytes)
    assert 'allowed_development_uses' not in raw, 'original raw POST evidence required'
    assert set(raw['seeds']) == roots and not raw['errors']
    auditor_sha = generate.sha(HERE / 'seed_audit.py')
    assert raw['audit_script_sha256'] == auditor_sha == pre['audit_script_sha256']
    assert raw['paths_checked'] > 0 and raw['metadata_files_checked'] > 0
    dev_ids = {row['seed']: f"pipeline.dev.{Path(row['path']).stem}.s{row['seed']}"
               for row in request['specs'] if row['split'] == 'dev'}
    allowed_prefix = ('experiments', 'mvp', 'frontier', 'engine', 'peel_ladder', 'assignment-ballast')
    for collision in raw['collisions']:
        assert collision['source'] == 'path', 'metadata collision cannot be resolved as dev usage'
        path = Path(collision['path'])
        assert not path.is_absolute() and '..' not in path.parts
        assert len(path.parts) > 8 and path.parts[:6] == allowed_prefix
        assert path.parts[6].isascii() and path.parts[6].isdigit() and int(path.parts[6]) > 0
        seeds = set(collision['seeds'])
        assert seeds and seeds <= dev_ids.keys(), 'sealed or unknown root collision'
        assert set(find_seeds(collision['path'], roots)) == seeds
        assert all(dev_ids[seed] == path.parts[7] for seed in seeds), 'unknown development lineage'
    # Preserve the completed scan byte for byte before replacing its resolved view.
    if not raw_path.exists():
        with raw_path.open('xb') as handle:
            handle.write(raw_bytes)
    assert raw_path.read_bytes() == raw_bytes
    resolved = deepcopy(raw)
    resolved.update(
        disjoint=True, collisions=[], evidence=[],
        raw_audit_sha256=generate.sha(raw_path),
        raw_audit_script_sha256=raw['audit_script_sha256'],
        post_resolution_script_sha256=generate.sha(Path(__file__)),
        pre_audit_sha256=generate.sha(pre_path),
        post_audit_note_sha256=generate.sha(HERE / 'POST-AUDIT-NOTE.md'),
        allowed_development_uses=deepcopy(raw['collisions']),
        sealed_seed_collisions=[],
        method=('Completed full POST scan retained without rescan. Only path collisions '
                'under assignment-ballast/numeric-PID/exact-registered-dev-ID are recognized '
                'as subsequent development use; PRE established prior disjointness. '
                'External output contents and sealed maps were not decoded.'))
    generate.write(HERE / 'SEED-AUDIT-POST.json', resolved)
    return resolved


def verified_rows(request):
    assert request['specs'] == generate.specs()
    rows = []
    for spec in request['specs']:
        row = metadata(f"status/{spec['seed']}.json")
        assert all(row[key] == value for key, value in spec.items())
        assert row['generation_status'] == 'accepted' and row['schema_valid'] is True
        if row['split'] == 'sealed':
            assert set(row) == set(spec) | {'sha256', 'schema_valid', 'generation_status'}
        path = (ROOT / row['path']).resolve()
        assert path.is_relative_to(HERE / row['split']) and path.suffix == '.json'
        assert generate.sha(path) == row['sha256'], 'map hash changed'
        rows.append(row)
    assert len(rows) == len({row['path'] for row in rows}) == len({row['seed'] for row in rows}) == 56
    assert Counter(row['split'] for row in rows) == Counter(dev=32, sealed=24)
    for split, repeats in (('dev', 4), ('sealed', 3)):
        actual = Counter((row['ship'], row['fill_target'], row['generator'])
                         for row in rows if row['split'] == split)
        expected = Counter({(ship, fill, family): repeats
                            for ship in ('feeder_500', 'feeder_800')
                            for fill in (60, 70)
                            for family in ('random_yard', 'simulated_yard')})
        assert actual == expected
    return rows


def main():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        assert os.environ.get(key) == '1', f'{key}=1 required'
    wait_for_slot()
    request = metadata('requests.json')
    assert request['generator_sha256'] == generate.sha(HERE / 'generate.py')
    assert request['prereg_sha256'] == generate.sha(HERE / 'PREREG.md')
    assert request['runtime_snapshot_sha256'] == generate.sha(HERE / 'runtime-snapshot.json')
    snapshot = metadata('runtime-snapshot.json')
    assert snapshot['builder_sha256'] == generate.sha(RUNTIME / 'experiments/mvp/corpus_tools/build.py')
    for name, digest in request['source_files'].items():
        path = (RUNTIME / name).resolve()
        assert path.is_relative_to(RUNTIME)
        assert generate.sha(path) == digest, 'frozen runtime source changed'
    rows = verified_rows(request)
    post = resolve_post(request)
    for split in ('dev', 'sealed'):
        (HERE / f'{split}.txt').write_text(''.join(row['path'] + '\n' for row in rows if row['split'] == split))
    generate.write(HERE / 'cohort.json', dict(format='pipeline-envelope-cohort-v1', maps=rows))
    generate.report_dev(rows)
    names = (
        'generate.py', 'capacity.py', 'prepare_runtime.py', 'probe_runtime.py',
        'runtime-cache-status.json', 'seed_audit.py', 'lab_set.py', 'test_maps.py',
        'PREREG.md', 'AMENDMENT-1.md', 'resolve_seed_audit.py', 'seed_audit_lexical_v1.py',
        'SEED-AUDIT-PRE-RAW.json', 'requests-pre-key-preservation.json', 'requests.json',
        'runtime-snapshot.json', 'SEED-AUDIT-PRE.json', 'SEED-AUDIT-POST.json',
        'dev.txt', 'sealed.txt', 'cohort.json', 'DEV-REPORT.json', 'DEV-REPORT.md',
        'prepare_dev_set.py', 'DEV-MANIFEST.json', 'prepare_linear_cache.py',
        'linear-cache-manifest.json', 'RUN.md', 'finalize.py', 'POST-AUDIT-NOTE.md',
        'SEED-AUDIT-POST-RAW.json')
    paths = [HERE / name for name in names]
    paths += [RUNTIME / name for name in request['source_files']]
    paths += [RUNTIME / 'experiments/mvp/corpus_tools/build.py']
    linear = metadata('linear-cache-manifest.json')
    assert linear['runtime_snapshot_sha256'] == request['runtime_snapshot_sha256']
    assert linear['helper_sha256'] == generate.sha(HERE / 'prepare_linear_cache.py')
    assert linear['map_files_opened'] is False and linear['plans_used'] is False
    for name, digest in linear['files'].items():
        path = (HERE / name).resolve()
        assert path.is_relative_to(RUNTIME / 'experiments/cache/linear')
        assert generate.sha(path) == digest, 'linear acceleration artifact changed'
        paths.append(path)
    # Record policy rejection honestly; no production gate or physics is bypassed.
    open_issues = []
    if linear['completed'] is not True:
        open_issues.append(dict(component='production_linear_cache', completed=False,
            reason='Production linear policy epoch is stale; requalification required. Physical policy rejection retained.',
            evidence='experiments/mvp/pipeline/maps/linear-cache-manifest.json'))
    generate.write(HERE / 'MANIFEST.json', dict(format='pipeline-envelope-manifest-v1',
        maps=rows, counts=dict(dev=32, sealed=24),
        files={path.relative_to(ROOT).as_posix(): generate.sha(path) for path in paths},
        sealed_policy='Hash/count/schema status only until task 3.1; no planner run',
        workers=2, open_issues=open_issues,
        post_audit_allowed_development_uses=len(post['allowed_development_uses']),
        linear_cache_completed=linear['completed']))
    print(json.dumps(dict(event='published', dev=32, sealed=24,
        manifest_sha256=generate.sha(HERE / 'MANIFEST.json'), open_issues=open_issues)))


if __name__ == '__main__':
    main()
