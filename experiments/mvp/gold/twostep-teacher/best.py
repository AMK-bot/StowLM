"""D150 report: how often two-step beats stored gold re-verified now, under the checker's weighted moves. Reads rows/ and
writes summary.json and summary.md (mean gap, per rung/ship). Report only: the 8.3 teacher export is d151.py.
Gap = re-verified gold minus two-step, on maps where both pass (positive: two-step uses fewer moves). A tie keeps gold.
Usage: best.py
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics as st

HERE = Path(__file__).resolve().parent


def winner(row):
    """(source, costs): the fewer verified moves, gold on a tie, None if neither passed."""
    costs = {name: (row.get(name) or {}).get('moves') if (row.get(name) or {}).get('passed') else None
             for name in ('gold', 'two_step')}
    live = {k: v for k, v in costs.items() if v is not None}
    if not live:
        return None, costs
    return ('two_step' if 'gold' not in live or ('two_step' in live and live['two_step'] < live['gold']) else 'gold'), costs


def table(rows):
    both = [r for r in rows if r['gold'] is not None and r['two_step'] is not None]
    gaps = [r['gold']-r['two_step'] for r in both]
    return dict(maps=len(rows), gold_passed=sum(r['gold'] is not None for r in rows),
                two_step_passed=sum(r['two_step'] is not None for r in rows), both_passed=len(both),
                no_teacher=sum(r['source'] is None for r in rows),
                two_step_better=sum(g > 0 for g in gaps), equal=sum(g == 0 for g in gaps), gold_better=sum(g < 0 for g in gaps),
                two_step_only=sum(r['gold'] is None and r['two_step'] is not None for r in rows),
                gold_only=sum(r['gold'] is not None and r['two_step'] is None for r in rows),
                mean_gold=st.mean(r['gold'] for r in both) if both else None,
                mean_two_step=st.mean(r['two_step'] for r in both) if both else None,
                mean_gap=st.mean(gaps) if gaps else None, mean_gap_where_two_step_better=st.mean(g for g in gaps if g > 0) if any(g > 0 for g in gaps) else None,
                mean_best=st.mean(min(x for x in (r['gold'], r['two_step']) if x is not None) for r in rows if r['source']) if any(r['source'] for r in rows) else None)


def markdown(groups):
    head = ('| Group | Maps | Gold passed now | Two-step passed | Two-step better / equal / gold better | Two-step only / gold only | '
            'No teacher | Mean gold vs two-step (both pass) | Mean gap | Mean best |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    lines = [head]
    for name, t in groups.items():
        means = f"{t['mean_gold']:.2f} vs {t['mean_two_step']:.2f} | {t['mean_gap']:.2f}" if t['both_passed'] else '— | —'
        best = f"{t['mean_best']:.2f}" if t['mean_best'] is not None else '—'
        lines.append(f"| {name} | {t['maps']} | {t['gold_passed']} | {t['two_step_passed']} | {t['two_step_better']} / {t['equal']} / "
                     f"{t['gold_better']} | {t['two_step_only']} / {t['gold_only']} | {t['no_teacher']} | {means} | {best} |")
    return '\n'.join(lines)+'\n'


def main():
    rows, crashed, summary_rows = [json.loads(p.read_text()) for p in sorted((HERE/'rows').glob('*.json'))], [], []
    issues = Counter()
    for row in rows:
        if row.get('crashed'):
            crashed.append(row['case'])
            continue
        for name in ('gold', 'two_step'):
            detail = row.get(name) or {}
            issues[f"{name}: unavailable"] += bool(detail.get('unavailable'))
            issues[f"{name}: finisher time limit"] += bool(detail.get('finisher_timeout'))
            issues[f"{name}: failed"] += not detail.get('passed')
        source, costs = winner(row)
        summary_rows.append(dict(case=row['case'], rung=row['rung'], ship=row['ship'], family=row['family'], solver=row['solver'],
                                 stored_gold=row['stored_moves'], source=source, **costs))
    groups = {'all': summary_rows}
    by = defaultdict(list)
    for row in summary_rows:
        by[row['rung']].append(row)
        by[f"{row['rung']} / {row['ship']}"].append(row)
    groups.update(sorted(by.items()))
    tables = {name: table(group) for name, group in groups.items()}
    (HERE/'summary.json').write_text(json.dumps(dict(tables=tables, issues=dict(issues), crashed=crashed, maps=summary_rows), indent=1, sort_keys=True)+'\n')
    text = markdown(tables)
    (HERE/'summary.md').write_text(text)
    print(text, json.dumps(dict(issues=dict(issues), crashed=len(crashed))))


if __name__ == '__main__':
    main()
