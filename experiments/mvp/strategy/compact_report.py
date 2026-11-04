"""Tables for compact.py rows. Library: rows(folder), table(rows), breakdown(rows). Usage: compact_report.py <folder>"""
from __future__ import annotations

import json
from pathlib import Path
import statistics as st
import sys

KEYS = ('relocations', 'restow_now', 'hatch_now', 'restow_later', 'hatch_later', 'total')


def rows(folder):
    return [json.loads(p.read_text()) for p in sorted((Path(folder)/'rows').glob('*.json'))]


def group(items, label):
    passed_before = [r for r in items if r['before_passed']]
    both = [r for r in passed_before if r['final_passed']]
    return (f"| {label} | {len(items)} | {len(passed_before)} | {sum(r['final_passed'] for r in items)} | "
            f"{sum(r['kept'] == 'compact' and r['before_passed'] for r in items)} | {sum(r['kept'] == 'compact' and not r['before_passed'] for r in items)} | "
            f"{sum(r['attempted'] and r['kept'] == 'original' for r in items)} | "
            + (f"{st.mean(r['before_moves'] for r in both):.2f} | {st.mean(r['final_moves'] for r in both):.2f} |" if both else '— | — |'))


def table(items):
    lines = ['| Group | Plans | Passed before | Passed after | Cheaper, kept | Rescued a failed plan | Tried, not kept | Mean moves before | After |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for rung in ('tiny', 'small'):
        sel = [r for r in items if r['rung'] == rung]
        lines.append(group(sel, rung))
        lines.append(group([r for r in sel if r['before']['restow_now'] > 0], f'{rung}, with same-call restows'))
        lines.append(group([r for r in sel if r['before']['restow_now'] == 0], f'{rung}, no same-call restows'))
    return lines


def final_parts(row):
    if row['kept'] == 'compact':
        return row['check']['after']
    return row['before']


def breakdown(items):
    lines = ['| Group | Plans | '+' | '.join(KEYS)+' |', '|---|---:|'+'---:|'*len(KEYS)]
    for rung in ('tiny', 'small'):
        sel = [r for r in items if r['rung'] == rung and r['before_passed'] and r['final_passed']]
        if not sel:
            continue
        lines.append(f'| {rung} before | {len(sel)} | '+' | '.join(f"{st.mean(r['before'][k] for r in sel):.2f}" for k in KEYS)+' |')
        lines.append(f'| {rung} after | {len(sel)} | '+' | '.join(f"{st.mean(final_parts(r)[k] for r in sel):.2f}" for k in KEYS)+' |')
    return lines


def kept_replays(items):
    """Replay results of compacted plans actually kept; a plan that never reached replay isn't one of them (rev-compact P3)."""
    values = [str((r['check'] or {}).get('mask_replay')) for r in items if r['kept'] == 'compact']
    return {value: values.count(value) for value in sorted(set(values))}


if __name__ == '__main__':
    items = rows(sys.argv[1])
    print('\n'.join(table(items)), '\n', '\n'.join(breakdown(items)), sep='')
    tried = [r for r in items if r['attempted']]
    print('mask replay of kept compacted plans:', kept_replays(items))
    print('failure rules:', {r['key']: (r['check'].get('rules') or r['check'].get('message')) for r in tried if not r['check'].get('passed')})
    print('not compactable:', {r['key']: r.get('reason') for r in items if r.get('reason')})
