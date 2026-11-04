"""JSON/hash consistency checks; this does not replace native plan auditing."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = (
    'method_dense.py', 'constructor_balanced.py', 'pages_fast_dense.py',
    'pages_sight.py', 'pages_balanced.py', 'pages.py', 'pages_base.py',
    'lns.py', 'repair.py', 'physics.py', 'ballast_opt.py', 'run_score.py',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', nargs='+', required=True)
    args = parser.parse_args()
    manifest = json.loads((HERE/'MANIFEST.json').read_text())
    assert len(manifest) == 48
    expected = {r['input']: r for r in manifest}
    assert len(expected) == 48
    for row in manifest:
        assert sha(row['input']) == row['input_sha256']
    snapshot = json.loads((HERE/'DENSE-CORRECTED-SOURCES.json').read_text())['sources']
    for name in RUNTIME:
        path = HERE/name
        assert sha(path) == snapshot[str(path.relative_to(Path.cwd()))], name
    adaptive = json.loads((HERE/'ADAPTIVE-SOURCES.json').read_text())
    adaptive_path = HERE/'method_adaptive.py'
    assert sha(adaptive_path) == adaptive['sources'][str(adaptive_path.relative_to(Path.cwd()))]
    assert sha(HERE/'adaptive-targets.txt') == adaptive['target_list_sha256']
    audit = json.loads((HERE/'AUDIT.json').read_text())
    checked = set()
    for row in audit:
        assert row['passed'] and row['replay_legal'] and row['violations'] == 0
        assert sha(row['report']) == row['report_sha256']
        checked.add((row['input_sha256'], row['plan_sha256']))
    observed, passes, software_errors = set(), 0, []
    for directory in args.runs:
        for path in Path(directory).glob('*.json'):
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or 'instance_id' not in row:
                continue
            assert row['complete']
            assert row['input'] in expected
            assert row['input_sha256'] == expected[row['input']]['input_sha256']
            observed.add(row['input'])
            if row['passed']:
                plan_hash = hashlib.sha256(json.dumps(row['plan'], sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest()
                assert (row['input_sha256'], plan_hash) in checked, path
                assert sum(row['parts'].values()) == row['lifts'], path
                passes += 1
            for attempt in row.get('method_metadata', {}).get('construction', {}).get('attempts', []):
                if 'Error:' in attempt.get('reason', ''):
                    software_errors.append(dict(evidence=str(path), reason=attempt['reason']))
    assert observed == set(expected), f'Missing {set(expected)-observed}'
    archive = json.loads((HERE/'DIAGNOSTIC-ARCHIVE.json').read_text())
    for row in archive:
        assert sha(row['archived']) == row['sha256']
    result = dict(input_hashes=48, runtime_source_hashes=len(RUNTIME),
                  adaptive_source_hashes=1, adaptive_target_list_hashes=1,
                  passing_rows_audited=passes, distinct_native_audits=len(checked),
                  archived_diagnostics=len(archive), software_errors=software_errors,
                  scope='Artifact consistency only; native checks are recorded in AUDIT.json')
    (HERE/'VERIFICATION.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
