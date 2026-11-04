"""Rebuild checker-only tables from exact primary benchmark rows; no solving."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
ARMS = ('frontier09', 'engine', 'layover')
LABELS = {'frontier09': 'Fresh 0.9.0', 'engine': 'Frozen engine', 'layover': 'Arrival pages + LP'}


def mean(values):
    values = list(values)
    return statistics.mean(values) if values else None


def fmt(value):
    return '—' if value is None else f'{value:.2f}'.rstrip('0').rstrip('.')


def aggregate(rows, records):
    indexed = {(r['instance_id'], r['method']): r for r in rows}
    groups = []
    for size in ('tiny', 'small', 'medium'):
        for occupancy in (.30, .50):
            maps = [r for r in records if r['size'] == size and r['occupancy_target'] == occupancy]
            ids = [Path(r['path']).stem for r in maps]
            group = dict(size=size, occupancy=occupancy, expected_maps=len(maps), methods={}, pairs={})
            for arm in ARMS:
                items = [indexed[(identity, arm)] for identity in ids if (identity, arm) in indexed]
                passing = [r for r in items if r['passed']]
                group['methods'][arm] = dict(maps=len(items), passes=len(passing),
                    replay_complete=sum(r.get('replay_complete') is True for r in items),
                    replay_unavailable=sum(r.get('replay_complete') is None for r in items),
                    mean_lifts=mean(r['lifts'] for r in passing),
                    mean_wall_s=mean(r['wall_s'] for r in items),
                    mean_restow_now=mean(r['parts']['restow_now'] for r in passing),
                    mean_restow_later=mean(r['parts']['restow_later'] for r in passing),
                    mean_aboard_restow_now=mean(r['aboard_restows']['now_lifts'] for r in passing),
                    mean_aboard_restow_later=(mean(r['aboard_restows']['future_lifts'] for r in passing)
                        if all(r['aboard_restows']['future_lifts'] is not None for r in passing) else None),
                    aboard_future_attribution_missing=sum(r['aboard_restows']['future_lifts'] is None for r in passing))
            for baseline in ('frontier09', 'engine'):
                pairs = [(indexed[(identity, baseline)], indexed[(identity, 'layover')])
                    for identity in ids if all((identity, arm) in indexed and indexed[(identity, arm)]['passed']
                        for arm in (baseline, 'layover'))]
                group['pairs'][baseline] = dict(maps=len(pairs),
                    baseline_lifts=mean(a['lifts'] for a,b in pairs), method_lifts=mean(b['lifts'] for a,b in pairs),
                    baseline_wall_s=mean(a['wall_s'] for a,b in pairs), method_wall_s=mean(b['wall_s'] for a,b in pairs))
            groups.append(group)
    return dict(groups=groups, rows=rows, expected_rows=len(records)*len(ARMS), observed_rows=len(rows))


def validate_rows(rows, records, current_sources, *, allow_partial=False):
    """Refuse mixed/stale measurements or missing cells in the final report."""
    by_id = {Path(record['path']).stem: record for record in records}
    predeclared = {(size, occupancy, 331401000+12*(2*i+j)+replicate)
        for i,size in enumerate(('tiny','small','medium'))
        for j,occupancy in enumerate((.30,.50)) for replicate in range(12)}
    selected = {(r['size'],r['occupancy_target'],r['seed']) for r in records}
    if (len(by_id) != len(records) or len(selected) != len(records)
            or not selected.issubset(predeclared)):
        raise ValueError('manifest contains duplicate or undeclared cohort maps')
    if not allow_partial and selected != predeclared:
        raise ValueError('final report requires all twelve predeclared seeds in each of six cells')
    expected = {(identity, arm) for identity in by_id for arm in ARMS}
    seen = set()
    for row in rows:
        key = (row['instance_id'], row['method'])
        if key in seen or key not in expected:
            raise ValueError(f'duplicate or foreign benchmark row: {key}')
        seen.add(key)
        record = by_id[key[0]]
        if row.get('complete') is not True:
            raise ValueError(f'incomplete atomic result: {key}')
        digest = hashlib.sha256((ROOT/record['path']).read_bytes()).hexdigest()
        if (digest != record['sha256'] or row.get('input_sha256') != digest
                or row.get('input') != record['path']):
            raise ValueError(f'input hash/path mismatch: {key}')
        if row.get('source_sha256') != current_sources[key[1]]:
            raise ValueError(f'stale or mixed runtime source provenance: {key}')
        if row.get('size') != record['size'] or row.get('occupancy') != record['occupancy_target']:
            raise ValueError(f'cell metadata mismatch: {key}')
        if row.get('passed') and (row.get('lifts') is None or not row.get('plan')
                or row.get('replay_complete') is not True):
            raise ValueError(f'passing row lacks checked plan/objective/complete replay: {key}')
    if not allow_partial and (len(records) != 72 or seen != expected):
        raise ValueError(f'final report requires 72 maps and 216 unique rows; found {len(records)} maps/{len(seen)} rows')
    return dict(expected_rows=len(expected), observed_rows=len(seen), source_and_input_hashes_match=True,
        complete=len(records)==72 and seen==expected)


def render(summary):
    lines = ['# Cycle 4 layover comparison', '',
        'TRAIN: 72 fresh predeclared seeds, twelve maps per size/arrival-occupancy cell. '
        'Native non-DG dry20 masses, about 70% total cargo occupancy; structural Stage B snapshots. '
        'No solver-outcome selection. No sealed data. Occupancy labels are targets: '
        'the 30% cells are actually tiny 33.3%, small 29.4%, medium 30%; all 50% cells are exactly 50%.', '',
        '**Pass** is independent unchanged full-checker acceptance. **Replay** is a complete legal current-call '
        'loading/discharge replay, including retained arrival cargo. `?` is an unavailable internal gate when '
        'an as-is comparator returns no plan. Lifts and restows are means among passing plans; '
        'later-port lifts follow the fixed-stow D15 projection with no later loads. Wall time includes failures.', '',
        '| Size | Occupancy | Arm | Measured | Replay / ? | Pass | Lifts | Restow now/later | Arrival restow now/later | Wall s |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for group in summary['groups']:
        for arm in ARMS:
            row = group['methods'][arm]
            lines.append(f"| {group['size']} | {round(100*group['occupancy'])}% | {LABELS[arm]} | "
                f"{row['maps']}/{group['expected_maps']} | {row['replay_complete']} / {row['replay_unavailable']} | "
                f"{row['passes']}/{group['expected_maps']} | {fmt(row['mean_lifts'])} | "
                f"{fmt(row['mean_restow_now'])}/{fmt(row['mean_restow_later'])} | "
                f"{fmt(row['mean_aboard_restow_now'])}/{fmt(row['mean_aboard_restow_later'])} | {fmt(row['mean_wall_s'])} |")
    lines += ['', 'Mutual full-checker passes only (paired lift comparison):', '',
        '| Size | Occupancy | Comparator | Pairs | Comparator lifts | Method lifts | Comparator / method wall s |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for group in summary['groups']:
        for arm in ('frontier09', 'engine'):
            row = group['pairs'][arm]
            lines.append(f"| {group['size']} | {round(100*group['occupancy'])}% | {LABELS[arm]} | {row['maps']} | "
                f"{fmt(row['baseline_lifts'])} | {fmt(row['method_lifts'])} | "
                f"{fmt(row['baseline_wall_s'])} / {fmt(row['method_wall_s'])} |")
    lines += ['', 'Comparators run freshly in isolated Python processes with native behavior. Fresh 0.9.0 receives '
        '`search_s=min(120,max(20,6×new loads))`, `finalize_s=60`, two tries, seed 0. '
        'The frozen engine receives `budget_s=None`, `finalize_s=60`, seed 0, retaining native default extra '
        'budgets (120 seconds for ≤60 new loads, 375 otherwise) and native search/finishing floors '
        '(effective finishing 300 seconds at ≤60 new loads, 60 otherwise). '
        'This is an as-is comparison; work budgets differ and are recorded on every row. '
        'The method uses deterministic construction work counts and local ballast-LP finishing; '
        'its exact work configuration and completion counts are recorded in row metadata. '
        'All arms use one numerical thread and one master worker. Primary jobs use at most two admitted '
        'shards after BENCH-RUNNING cleared (one active shard is allowed at the tail); '
        'other shared-machine load can vary and is recorded. '
        'The six arm orders are counterbalanced twice within each twelve-map cell; '
        'every row records start/end system load averages. '
        'The baseline retains the native E9 bug; any E9-only gain must be labelled a bug fix.', '',
        f"Rows present: {summary['observed_rows']}/{summary['expected_rows']}. "
        'No cached stored baseline rows are used. Source/input hashes and exact solver configuration '
        'are checked on resume. Failed objectives are never treated as upper bounds.']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='results/cycle4')
    parser.add_argument('--allow-partial', action='store_true', help='Diagnostic progress only; final report requires all 216 rows')
    args = parser.parse_args()
    source = (HERE/args.out).resolve()
    if not source.is_relative_to(HERE/'results'):
        raise ValueError('result source must remain inside local results')
    records = json.loads((HERE/'cohort/MANIFEST.json').read_text())['maps']
    rows = [json.loads(path.read_text()) for arm in ARMS for path in sorted((source/arm).glob('*.json'))]
    spec = importlib.util.spec_from_file_location('_c4_report_runtime', HERE/'runner.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    validation = validate_rows(rows, records, {arm:runtime.provenance(arm) for arm in ARMS},
        allow_partial=args.allow_partial)
    summary = aggregate(rows, records)
    summary['validation'] = validation
    (source/'comparison.json').write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')
    (HERE/'TABLE.md').write_text(render(summary))
    print(json.dumps(dict(rows=len(rows), expected_rows=summary['expected_rows'], table=str(HERE/'TABLE.md'))))


if __name__ == '__main__':
    main()
