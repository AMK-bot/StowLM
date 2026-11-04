"""Publish completed dev status metadata while the sealed cohort is pending.

This helper never reads map bytes or sealed status files. Hashes come from
generator attestations; the dev-only lab adapter verifies them when loading.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main():
    requests = json.loads((HERE / 'requests.json').read_text())
    specs = [row for row in requests['specs'] if row['split'] == 'dev']
    assert len(specs) == 32
    assert len({row['seed'] for row in specs}) == 32
    assert len({row['path'] for row in specs}) == 32
    expected_cells = {(ship, fill, family): 4
                      for ship in ('feeder_500', 'feeder_800')
                      for fill in (60, 70)
                      for family in ('random_yard', 'simulated_yard')}
    assert Counter((row['ship'], row['fill_target'], row['generator'])
                   for row in specs) == Counter(expected_cells)
    rows = []
    for spec in specs:
        assert type(spec['seed']) is int
        path = (ROOT / spec['path']).resolve()
        assert path.is_relative_to(HERE / 'dev') and path.suffix == '.json'
        row = json.loads((HERE / 'status' / f"{spec['seed']}.json").read_text())
        assert all(row[key] == value for key, value in spec.items())
        assert row['split'] == 'dev'
        assert row['generation_status'] == 'accepted'
        assert row['schema_valid'] is True
        assert re.fullmatch(r'[0-9a-f]{64}', row['sha256'])
        rows.append(row)

    # Importing this module loads no stowage code and invokes no generator.
    sys.path.insert(0, str(HERE))
    import generate
    assert 'stowage' not in sys.modules
    generate.report_dev(rows)
    (HERE / 'dev.txt').write_text(''.join(row['path'] + '\n' for row in rows))
    generate.write(HERE / 'DEV-MANIFEST.json', dict(
        format='pipeline-envelope-development-manifest-v1',
        status='partial-dev-only; final sealed manifest pending',
        counts=dict(dev=32), maps=rows,
        hash_verification='Generator-attested hashes; checked on dev loader access',
        sealed_files_opened=False))
    print(json.dumps(dict(event='development_set_ready', maps=32,
                          manifest='DEV-MANIFEST.json', set='dev.txt')))


if __name__ == '__main__':
    main()
