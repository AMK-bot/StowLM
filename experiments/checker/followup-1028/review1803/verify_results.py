"""Audit final test evidence and immutable inputs; no numerical calculations."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
RUNS = HERE.parent
ROOT = HERE.parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    results = {}
    for name in ('review1803-checker', 'review1803-vessel-dependencies'):
        run = json.loads((RUNS / f'{name}.run.json').read_text())
        assert run['exit_code'] == 0, (name, run['exit_code'])
        suites = list(ET.parse(RUNS / f'{name}.xml').getroot().iter('testsuite'))
        counts = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
                  for key in ('tests', 'errors', 'failures', 'skipped')}
        assert counts['failures'] == counts['errors'] == 0, (name, counts)
        counts['passed'] = counts['tests'] - counts['errors'] - counts['failures'] - counts['skipped']
        results[name] = {**counts, **{key: run[key] for key in (
            'command', 'wall_seconds', 'cpu_seconds', 'peak_rss_gb', 'changed')}}
    corpus = json.loads((RUNS / 'review1803-corpus-summary.json').read_text())
    assert corpus['tests'] == 85 and corpus['failures'] == corpus['errors'] == 0
    assert sum(row['total'] for row in corpus['cases'].values()) == 84
    assert corpus['non_bitwise_numeric_changes'] == []
    owned = json.loads((HERE / 'owned-final-files.json').read_text())
    assert all(digest(ROOT / name) == expected for name, expected in owned.items())
    before = json.loads((HERE / 'before-production-identities.json').read_text())
    after = json.loads((HERE / 'after-production-identities.json').read_text())
    assert before['source_manifest'].keys() == after['source_manifest'].keys()
    changed = [name for name, value in before['source_manifest'].items()
               if value != after['source_manifest'][name]]
    assert changed == ['src/stowage/linear/dependencies.py'], changed
    assert all(digest(ROOT / name) == value for name, value in after['source_manifest'].items())
    assert all(before['profiles'][name]['versions'] == after['profiles'][name]['versions']
               for name in before['profiles'])
    output = dict(suites=results, corpus_pairs=84, non_bitwise_numeric_changes=[],
                  production_source_changes=changed, final_owned_files_unchanged=True,
                  builder_versions_unchanged=True,
                  current_fingerprints={name: row['fingerprint'] for name, row in after['profiles'].items()})
    (HERE / 'verification.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
