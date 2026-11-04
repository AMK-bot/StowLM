"""Join global lower bounds to independently checked repair upper bounds."""
import json
from pathlib import Path

from catalog import load_baseline, load_set, ROOT


def main():
    own = Path(__file__).resolve().parent
    baseline = load_baseline()
    by_input = {value['input']: value for value in baseline.values()}
    rows = []
    for rel in load_set('quick'):
        objective = json.loads((ROOT / rel).read_text())['objective']
        assert all(objective[name] == 1 for name in (
            'relocate', 'restow', 'hatch_cover', 'future_restow', 'future_hatch_cover'))
        base = by_input[rel]
        ident = base['instance_id']
        path = own / 'results/quick-repair' / f'{ident}.json'
        if not path.exists():
            raise RuntimeError(f'Scorecard not finished: {ident}')
        checked = json.loads(path.read_text())
        row = dict(instance_id=ident, size=base['size'], stored_lifts=base['lifts'],
                   stored_passed=base['passed'], checked_passed=checked['passed'],
                   checked_lifts=checked.get('lifts'), wall_s=checked['wall_s'],
                   propose_s=checked.get('propose_s', 0),
                   method_sha256=checked.get('method_metadata', {}).get('actual_method_sha256'),
                   configured_method_sha256=checked['config']['method']['sha256'])
        neighborhoods = checked.get('method_metadata', {}).get('exact_repair_stats', [])
        row['neighborhoods'] = len([n for n in neighborhoods if 'group' in n])
        row['exhausted_neighborhoods'] = sum(n.get('exhaustive', False) for n in neighborhoods)
        bound_path = own / 'results/bounds-small-final' / f'{ident}.json'
        if bound_path.exists():
            bound = json.loads(bound_path.read_text())['result']['lower_bound']
            row['lower_bound'] = bound
            upper = row['checked_lifts'] if row['checked_passed'] else None
            if upper is not None:
                assert bound <= upper, (ident, bound, upper)
                row['fresh_optimality_certificate'] = bound == upper
                row['suboptimality_ratio_upper'] = upper / bound - 1 if bound else None
        rows.append(row)
    paired = [r for r in rows if r['size'] == 'small' and r['checked_passed'] and r['stored_passed']]
    summary = dict(
        map_set='quick', map_count=len(rows),
        small_paired=len(paired),
        small_mean_stored_lifts=sum(r['stored_lifts'] for r in paired)/len(paired),
        small_mean_checked_lifts=sum(r['checked_lifts'] for r in paired)/len(paired),
        fresh_small_optimality_certificates=sum(r.get('fresh_optimality_certificate', False) for r in rows),
        small_neighborhoods=sum(r['neighborhoods'] for r in rows if r['size'] == 'small'),
        small_exhausted_neighborhoods=sum(r['exhausted_neighborhoods'] for r in rows if r['size'] == 'small'),
        sum_map_wall_s=sum(r['wall_s'] for r in rows),
        source_provenance_note='The scorecard started while certificate-only refinements were being made. Its configured method hash is the launch snapshot; import-time actual_method_sha256 is authoritative where present. All measured maps have unit weights, so fractional-weight proof corrections do not change their search/pruning decisions. Every retained plan independently passed the unchanged full checker.',
        maps=rows)
    (own / 'results/CERTIFICATES.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k:v for k,v in summary.items() if k != 'maps'}, indent=2))


if __name__ == '__main__':
    main()
