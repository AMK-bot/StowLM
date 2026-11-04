"""Verify durable D191 evidence without loading any held-out instance."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((HERE / 'baseline.json').read_text())
    assert baseline['maps'] == 12
    assert baseline['counts'] == {
        'default': {'dead_end': 10, 'ok': 2},
        'rule': {'dead_end': 11, 'timeout': 1}}
    assert len({row['map_id'] for row in baseline['rows']}) == 12
    manifest = json.loads((HERE.parent / 'blockrule/cohort-medium.json').read_text())
    assert manifest['split'] == 'train'
    for row in manifest['maps']:
        assert '/maps/train/medium/' in row['input']
        assert digest(ROOT / row['input']) == row['sha256'], row['input']
    assert all(row['arms'][arm]['greedy_status'] == 'timeout'
               and row['arms'][arm]['greedy_budget_s'] == 10
               for row in baseline['rows'] for arm in ('default', 'rule'))
    for rel, expected in baseline['artifacts_sha256'].items():
        assert digest(ROOT / rel) == expected, rel
    snapshot_path = HERE / 'search/runtime/snapshot.json'
    snapshot = json.loads(snapshot_path.read_text())
    for rel, expected in snapshot['files'].items():
        assert digest(snapshot_path.parent / rel) == expected, rel
    scripts = []
    for path in HERE.rglob('*.py'):
        if path.is_relative_to(snapshot_path.parent):
            continue
        ast.parse(path.read_text(), filename=str(path))
        scripts.append(str(path.relative_to(ROOT)))
    output = {
        'passed': True, 'verified_utc': datetime.now(timezone.utc).isoformat(),
        'baseline_artifacts': len(baseline['artifacts_sha256']),
        'snapshot_files': len(snapshot['files']),
        'snapshot_sha256': snapshot['snapshot_sha256'], 'scripts_parsed': scripts,
        'assertions': ['12 unique train maps', 'all24 greedy allowances10s',
                       'baseline stage counts', 'baseline artifact hashes',
                       'immutable runtime byte hashes', 'wrapper syntax']}
    (HERE / 'verification.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output))


if __name__ == '__main__':
    main()
