"""Validate the canonical release without solving or assigning new splits.

A content digest pins instance bytes independently of timing metadata. Gold
producers should consume these same hashes and split specifications, rather
than sampling after seeing solver outcomes (see eval.splits for leakage policy).
"""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from eval.splits import canonical_spec, spec_from_instance
from stowage.frozen import require_clean
from stowage.generate.instances_screen import count_unresolved_provisions
from stowage.generate.instances_v2 import COUNTS, scenario_specs, family_tags, split_metadata, configs_compatible
from stowage.io import load_instance


def input_fingerprints():
    """Pin validation-time code and parameter bytes, including catalogue metadata."""
    project = Path(__file__).resolve().parents[2]
    files = {*project.joinpath('params').rglob('*.toml'),
             *project.joinpath('src/stowage/generate').glob('instances*.py'),
             project/'src/stowage/generate/random_yard.py',
             project/'src/stowage/generate/cargo.py', project/'src/eval/splits.py',
             project/'src/stowage/rules/catalogue.toml', project/'src/stowage/versions.py',
             Path(__file__).resolve()}
    return {str(path.relative_to(project)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


def main():
    root = Path(__file__).parent
    started = perf_counter()
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['split_status'] == 'assigned'
    specs = scenario_specs()
    rows = []
    keys, ids = set(), set()
    by_group = defaultdict(Counter)
    versions = defaultdict(set)
    for spec in specs:
        group_name = spec['generator'] + '/' + spec['profile']
        group = manifest['groups'][group_name]
        assert group['count'] == COUNTS[spec['profile']]
        path = root / group_name / f"seed-{spec['seed']:08d}.json"
        sidecar = path.with_suffix('.audit.json')
        audit = json.loads(sidecar.read_text())
        checksum = sha256(path.read_bytes()).hexdigest()
        assert configs_compatible(audit['config'], spec, audit['attempts']) and audit['instance_sha256'] == checksum
        row = next(r for r in group['shards'] if r['spec']['seed'] == spec['seed'])
        assert row['sha256'] == checksum and row['family'] == family_tags(spec)
        instance = load_instance(path)
        require_clean(instance, what='canonical v2 release')
        assert instance.stage == 'A' and not instance.onboard.placements
        assert len(instance.load_list) == spec['n_boxes']
        assert instance.family == family_tags(spec)
        split = split_metadata(spec)
        assert split['split'] in ('train', 'val', 'test')
        for key in ('split', 'spec_key', 'split_spec', 'test_family'):
            assert row[key] == split[key], (path, key)
        assert canonical_spec(spec_from_instance(instance)) == split['split_spec']
        assert split['spec_key'] not in keys and instance.id not in ids
        keys.add(split['spec_key']); ids.add(instance.id)
        targets = set(instance.load_list)
        boxes = [box for box in instance.containers if box.id in targets]
        assert count_unresolved_provisions(boxes) == 0
        stats = by_group[group_name]
        stats['instances'] += 1
        stats['load_boxes'] += len(boxes)
        stats['dg_boxes'] += sum(box.dg is not None for box in boxes)
        stats['instances_with_dg'] += any(box.dg is not None for box in boxes)
        stats[split['split']] += 1
        for key, version in instance.provenance.versions.items():
            versions[key].add(version)
        rows.append({'instance': str(path.relative_to(root)), 'sha256': checksum,
                     'spec_key': split['spec_key'], 'split': split['split']})
    assert len(rows) == 1160 and len(keys) == 1160
    release = {'count': len(rows), 'groups': dict(sorted((k, dict(v)) for k,v in by_group.items())),
               'content_sha256': sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
               'manifest_sha256': sha256((root/'manifest.json').read_bytes()).hexdigest(),
               'component_versions': {k: sorted(v) for k,v in sorted(versions.items())},
               'validation_time_input_sha256': input_fingerprints(),
               'validation_s': perf_counter()-started,
               'note': 'Clean schema/checksum/split/provision validation only; no loading plan or full feasibility certificate.'}
    (root/'release.json').write_text(json.dumps(release, indent=2, sort_keys=True)+'\n')
    print(json.dumps(release, sort_keys=True))


if __name__ == '__main__':
    main()
