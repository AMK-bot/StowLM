# Copied from lab/c5/leader/completion.py; D242 completion summary, no solver changes.
"""Completion by tier (user order, 1 Nov 2026; D242): how many maps complete at T5, T4, T3 and so on.

A map is completed at a tier when every box is aboard and it obeys that tier and every tier above it.
Column T5 means nothing was peeled, which is an ordinary full-checker pass. A plan leaving any box ashore
is partial and is never counted as completed. Accepts plain lab/score.py rows and peel ladder rows.

Usage: completion.py [--group name=dir ...] [--markdown]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

COLUMNS = ('T5', 'T4', 'T3', 'T2', 'T1', 'T0 only')
LEVEL_COLUMN = dict(zip(range(6), COLUMNS))
SKIP = {'summary.json', 'run.json', 'MANIFEST.json', 'AUDIT.json', 'COMPLETION.json'}


def read_rows(directory):
    """One row per map, from one or more directories separated by commas."""
    rows = {}
    for part in str(directory).split(','):
        for path in sorted(Path(part).rglob('*.json')):
            if path.name in SKIP:
                continue
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or not row.get('instance_id'):
                continue
            if row['instance_id'] in rows:
                raise ValueError(f'Duplicate map: {row["instance_id"]}')
            rows[row['instance_id']] = row
    return rows


def _safe_partial_share(row):
    partial = row.get('best_safe_partial')
    if not partial:
        return None
    aboard = partial.get('boxes_aboard_percent')
    if aboard is None:
        raise ValueError(f'{row["instance_id"]}: best_safe_partial lacks boxes_aboard_percent')
    return float(aboard)


def classify(row):
    """Return the completion column for one row, or None when no completed plan exists."""
    completed = row.get('completed')
    if completed:
        return LEVEL_COLUMN[int(completed['level'])]
    if 'completed' in row:
        return None
    # A plain scorecard row has no ladder: only a full-checker pass completes it.
    if row.get('passed') is True:
        return 'T5'
    if row.get('level') is not None and not row.get('boxes_left_ashore'):
        return LEVEL_COLUMN[int(row['level'])]
    return None


def table(rows):
    counts = {column: 0 for column in COLUMNS}
    none = 0
    shares = []
    for row in rows.values():
        column = classify(row)
        if column is None:
            none += 1
        else:
            counts[column] += 1
        share = _safe_partial_share(row)
        if share is not None:
            shares.append(share)
    cumulative, running = {}, 0
    for column in COLUMNS:
        running += counts[column]
        cumulative[column] = running
    return dict(maps=len(rows), counts=counts, cumulative=cumulative, not_completed=none,
                safe_partials=len(shares), mean_percent_boxes_aboard_in_safe_partials=mean(shares) if shares else None)


def markdown(results):
    head = '| Set | Maps | ' + ' | '.join(COLUMNS) + ' | Not completed | Safe partial plans, mean % boxes aboard |'
    lines = [head, '|---|---:|' + '---:|'*(len(COLUMNS)+2)]
    for name, result in results.items():
        partial = result['mean_percent_boxes_aboard_in_safe_partials']
        partial = '—' if partial is None else f"{result['safe_partials']}, {partial:.1f} %"
        cells = ' | '.join(str(result['counts'][column]) for column in COLUMNS)
        lines.append(f"| {name} | {result['maps']} | {cells} | {result['not_completed']} | {partial} |")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', action='append', required=True, help='name=dir[,dir...]')
    parser.add_argument('--markdown', action='store_true')
    args = parser.parse_args()
    results = {}
    for item in args.group:
        name, directory = item.split('=', 1)
        results[name] = table(read_rows(directory))
    print(markdown(results) if args.markdown else json.dumps(results, indent=1))


if __name__ == '__main__':
    main()
