"""Verify stored-reference outputs and save a concise reproduction record."""
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]


def main():
    baseline = json.loads((LAB / 'baseline.json').read_text())['maps']
    report = {'sets': {}, 'differences': [], 'missing_stored_plans': []}
    for size in ('tiny', 'small', 'medium'):
        folder = HERE / 'results' / f'stored-quick-{size}'
        if not folder.exists():
            continue
        rows = [json.loads(p.read_text()) for p in folder.glob('*.json') if p.name != 'summary.json']
        expected = (LAB / 'sets' / f'quick_{size}.txt').read_text().splitlines()
        assert {r['input'] for r in rows} == set(expected), f'Incomplete {size} run'
        paired = []
        for row in rows:
            old = baseline[row['instance_id']]
            if old['stored'] is None:
                assert not row['passed'], f'Unexpected stored candidate: {row["instance_id"]}'
                report['missing_stored_plans'].append(row['instance_id'])
                continue
            assert row['passed'], f'Stored plan did not finish: {row["instance_id"]}: {row["failure_reason"]}'
            paired.append(row)
            if row['lifts'] != old['lifts']:
                report['differences'].append({'instance_id': row['instance_id'],
                    'stored_lifts': old['lifts'], 'refinished_lifts': row['lifts']})
            assert row['assignment'] == old['stored'], f'Changed stored assignment: {row["instance_id"]}'
        report['sets'][size] = {'maps': len(rows), 'passes': sum(r['passed'] for r in rows),
            'stored_plans': len(paired), 'lifts_reproduced': sum(r['lifts'] == baseline[r['instance_id']]['lifts'] for r in paired),
            'mean_wall_s': mean(r['wall_s'] for r in rows),
            'mean_passing_wall_s': mean(r['wall_s'] for r in paired) if paired else None}
    (HERE / 'REFERENCE.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
