"""Join measured score rows to bounds and certified V6 comparisons."""
from pathlib import Path
import argparse
import json
import statistics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--out', default=str(HERE/'comparison.json'))
    args = ap.parse_args()
    bounds = {r['instance_id']: r for r in json.loads((HERE/'bound_results.json').read_text())}
    v6 = {}
    for path in sorted((ROOT/'experiments/mvp/scale/runs/v6/maps').glob('*/summary.json')):
        row = json.loads(path.read_text())
        if row.get('checker_passed') and row.get('job', {}).get('split') == 'train':
            v6[row['job']['input']] = row
    rows = []
    for bid, bound in bounds.items():
        measured = []
        for run in args.runs:
            path = Path(run)/f'{bid}.json'
            if path.exists():
                r = json.loads(path.read_text())
                r['evidence'] = str(path)
                measured.append(r)
        passed = [r for r in measured if r.get('passed')]
        best = min(passed, key=lambda r: r['lifts']) if passed else None
        old_path = ROOT/'experiments/mvp/frontier/lab/c1/fx-scout/results/quick-3'/f'{bid}.json'
        old = json.loads(old_path.read_text()) if old_path.exists() else None
        v = v6.get(bound['input'])
        row = dict(instance_id=bid, input=bound['input'], lower_bound=bound['lower_bound'],
                   passed=best is not None, lifts=best['lifts'] if best else None,
                   parts=best['parts'] if best else None,
                   absolute_gap=best['lifts']-bound['lower_bound'] if best else None,
                   relative_gap=(best['lifts']-bound['lower_bound'])/best['lifts'] if best else None,
                   v6_lifts=v['count']['weighted_nonproductive'] if v else None,
                   scout_lifts=old['lifts'] if old and old.get('passed') else None,
                   evidence=best['evidence'] if best else None,
                   best_wall_s=best['wall_s'] if best else None)
        rows.append(row)
        print(json.dumps(row), flush=True)
    paired = [r for r in rows if r['passed'] and r['v6_lifts'] is not None]
    result = dict(maps=rows, passes=sum(r['passed'] for r in rows),
                  paired_v6_maps=len(paired),
                  paired_mean_lifts=statistics.mean(r['lifts'] for r in paired) if paired else None,
                  paired_v6_mean_lifts=statistics.mean(r['v6_lifts'] for r in paired) if paired else None,
                  note='Best checked portfolio over listed runs; timings are selected-run wall times, not cold portfolio totals.')
    Path(args.out).write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
