"""Summarize every preserved checkpoint; count success only after full check."""
import argparse
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--regression', type=Path, default=HERE/'regression_results')
parser.add_argument('--slices', type=Path, default=HERE/'slices_results')
parser.add_argument('--out', type=Path, default=HERE)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
summary = {}
lines = ['| Group | Case | Witness | First status / s | Warm status / s |',
         '|---|---|---|---|---|']
for group in ('regression', 'slices'):
    root = args.regression if group == 'regression' else args.slices
    rows = [json.loads(path.read_text()) for path in sorted(root.glob('*/result.json'))]
    record = dict(cases=len(rows), calls=sum(len(row.get('calls', [])) for row in rows))
    if group == 'regression':
        def witness_check(row):
            if 'witness_check' in row:
                return row['witness_check']
            source = Path(row.get('source', ''))/'result.json'
            return json.loads(source.read_text()).get('witness_check', {}) if source.is_file() else {}
        record['witness_passed'] = sum(witness_check(row).get('passed') is True for row in rows)
    else:
        record.update(legal=sum(row.get('legal') is True for row in rows),
                      static_passed=sum(row.get('static', {}).get('ran') is True and
                                        not row.get('static', {}).get('violations', []) for row in rows),
                      baseline_full_passed=sum(row.get('baseline_passed') is True for row in rows))
    for repeat in (0, 1):
        calls = [row['calls'][repeat] for row in rows if len(row.get('calls', [])) > repeat]
        times = [call['wall_s'] for call in calls]
        record['warm' if repeat else 'first'] = dict(
            calls=len(calls), success=sum(call.get('full_checker_passed') is True for call in calls),
            status_counts={status:sum(call['outcome']['status']==status for call in calls)
                           for status in sorted({call['outcome']['status'] for call in calls})},
            median_s=statistics.median(times) if times else None, max_s=max(times, default=None))
        if group == 'regression':
            record['warm' if repeat else 'first']['valid_witness_success'] = sum(
                witness_check(row).get('passed') is True and len(row.get('calls', [])) > repeat and
                row['calls'][repeat].get('full_checker_passed') is True for row in rows)
    for row in rows:
        case = row.get('id') or row.get('vessel', '?')+' '+row.get('condition', '?')
        witness = str(witness_check(row).get('passed', '—')) if group == 'regression' else '—'
        cells = [f"{call['outcome']['status']} / {call['wall_s']:.3f}" for call in row.get('calls', [])]
        cells += ['—']*(2-len(cells))
        lines.append(f'| {group} | {case} | {witness} | {cells[0]} | {cells[1]} |')
    summary[group] = record
(args.out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
(args.out/'measurements.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(summary, indent=2))
