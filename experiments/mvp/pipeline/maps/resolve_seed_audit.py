"""Resolve only documented decimal false positives; retain full-scan evidence."""
import hashlib
import json
from pathlib import Path
from seed_audit import metadata_seed_hits, requested_seeds

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    assert not (HERE/'requests.json').exists(), 'resolve before registration only'
    raw_path = HERE/'SEED-AUDIT-PRE-RAW.json'
    raw = json.loads(raw_path.read_text())
    assert raw['seeds'] == requested_seeds() and not raw['errors']
    assert raw['audit_script_sha256'] == sha(HERE/'seed_audit_lexical_v1.py')
    files = {r['path']:r['sha256'] for r in raw['files']}
    collisions, resolved = [], []
    for row in raw['collisions']:
        assert row['source']=='metadata', 'path hits require a new seed registration'
        path = ROOT/row['path']
        assert path.suffix=='.json' and sha(path)==files[row['path']], 'evidence changed'
        hits = metadata_seed_hits(path.read_text(),set(requested_seeds()),path.suffix)
        if hits:
            collisions.append(dict(row,seeds=hits))
        else:
            resolved.append(dict(row,reason='lexical candidate exists only in nonintegral JSON numeric values',
                                 metadata_sha256=files[row['path']]))
    result = dict(raw,disjoint=not collisions,collisions=collisions,evidence=collisions,
        audit_script_sha256=sha(HERE/'seed_audit.py'),raw_audit_sha256=sha(raw_path),
        raw_audit_script_sha256=sha(HERE/'seed_audit_lexical_v1.py'),
        resolution_script_sha256=sha(Path(__file__)),amendment_sha256=sha(HERE/'AMENDMENT-1.md'),
        resolved_decimal_candidates=resolved,
        method='Original complete path/metadata scan retained; all candidate-file hashes rechecked and candidates structurally resolved; no rescan claimed')
    (HERE/'SEED-AUDIT-PRE.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    assert result['disjoint']
    print(json.dumps(dict(disjoint=True,resolved_decimal_candidates=len(resolved),paths_checked=result['paths_checked'],metadata_files_checked=result['metadata_files_checked'])))

if __name__=='__main__':
    main()
