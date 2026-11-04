"""Compare checked rows by size, distinguishing historical and fresh evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
import sys

HERE = Path(__file__).resolve().parent
FRONTIER = HERE.parents[1]
ROOT = FRONTIER.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(FRONTIER / 'lab'))
from catalog import ensure_safe, load_baseline
from summary import summarize

PARTS = ('relocations', 'hatch_now', 'hatch_later', 'restow_now', 'restow_later')


def read_rows(directory):
    """Accept ordinary or sharded scorecard output; reject duplicates and stale input pairs."""
    directory = Path(directory).resolve()
    try:
        rel = directory.relative_to(ROOT).as_posix()
    except ValueError:
        rel = ''
    forbidden = ('experiments/mvp/testv3', 'experiments/mvp/frontier/runs/test-',
                 'experiments/mvp/frontier/runs/unseen15-', 'data/raw/rcsppsuite')
    if any(rel.startswith(prefix) for prefix in forbidden):
        raise ValueError(f'Sealed result directory: {rel}')
    rows = {}
    for path in sorted(directory.rglob('*.json')):
        # Only scorecard map rows, never plan snapshots or temporary files.
        if path.name in ('summary.json', 'run.json', 'MANIFEST.json'):
            continue
        row = json.loads(path.read_text())
        if not isinstance(row, dict) or not row.get('instance_id') or row.get('complete') is not True:
            continue
        ensure_safe(row['input'])
        identity = row['instance_id']
        if identity in rows:
            raise ValueError(f'Duplicate candidate evidence: {identity}')
        rows[identity] = row
    return rows


def baseline_rows():
    """Use raw stored runs beyond quick; missing evidence remains absent or null."""
    baseline = load_baseline()
    for name in ('val-0.9', 'tiny48-0.9'):
        directory = FRONTIER / 'runs' / name
        audit_path = directory / 'AUDIT.json'
        audits = {row['instance_id']: row for row in json.loads(audit_path.read_text())} if audit_path.exists() else {}
        for path in sorted(directory.glob('*.json')):
            if path.name == 'AUDIT.json':
                continue
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or not row.get('instance_id') or not row.get('input'):
                continue
            ensure_safe(row['input'])
            identity = row['instance_id']
            old = baseline.get(identity, {})
            parts = {key: audits.get(identity, {}).get(key) for key in PARTS}
            # Existing catalog parts remain valid if no separate audit is available.
            if all(value is None for value in parts.values()):
                parts = old.get('parts')
            baseline[identity] = dict(row, lifts=row.get('weighted'), parts=parts,
                                     source=str(path.relative_to(ROOT)))
    return baseline


def compare(rows, baseline, *, seed=0):
    for row in rows:
        old = baseline.get(row['instance_id'], {})
        for key in ('input', 'input_sha256'):
            if old.get(key) and row.get(key) and old[key] != row[key]:
                raise ValueError(f'Mismatched {key} for {row["instance_id"]}')
    report = summarize(rows, baseline, seed=seed)
    for size, group in report['by_size'].items():
        selected = [row for row in rows if row.get('size') == size
                    or (row.get('size') is None and (f'.{size}.' in row['instance_id']))]
        wall = [row['wall_s'] for row in selected if row.get('wall_s') is not None]
        old_wall = [baseline[row['instance_id']]['wall_s'] for row in selected
                    if baseline.get(row['instance_id'], {}).get('wall_s') is not None]
        group['sum_wall_s'] = sum(wall) if wall else None
        group['baseline_mean_wall_s'] = mean(old_wall) if old_wall else None
        group['parts'] = {}
        for part in PARTS:
            pairs = [(row['parts'][part], baseline[row['instance_id']]['parts'][part]) for row in selected
                     if row.get('passed') is True and baseline.get(row['instance_id'], {}).get('passed') is True
                     and (row.get('parts') or {}).get(part) is not None
                     and (baseline.get(row['instance_id'], {}).get('parts') or {}).get(part) is not None]
            group['parts'][part] = dict(pairs=len(pairs),
                mean=mean(a for a, _ in pairs) if pairs else None,
                baseline_mean=mean(b for _, b in pairs) if pairs else None,
                mean_change=mean(a-b for a, b in pairs) if pairs else None)
    return report


def print_report(report, label):
    print(label)
    for size, row in report['by_size'].items():
        print(f"{size}: passes {row['passes']}/{row['maps']}; reference "
              f"{row['baseline_passes']}/{row['baseline_measured']} measured; "
              f"unknown {len(row['baseline_unmeasured_ids'])}")
        print(f"  pairs={row['mutual_passes']} mean lifts={row['mean_lifts']} vs "
              f"{row['baseline_mean_lifts']}; delta={row['paired_mean_change']} "
              f"95% CI={row['paired_mean_change_ci95']}; "
              f"W/T/L={row['wins']}/{row['ties']}/{row['losses']}")
        print(f"  wall mean={row['mean_wall_s']}s, sum={row['sum_wall_s']}s; "
              f"reference mean={row['baseline_mean_wall_s']}s")
        for part, values in row['parts'].items():
            print(f"  {part}: {values['mean']} vs {values['baseline_mean']} "
                  f"({values['pairs']} pairs, delta={values['mean_change']})")
    if report.get('unclassified_errors'):
        print(f"Unclassified errors: {len(report['unclassified_errors'])}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', help='scorecard output or parent of shard directories')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--json', dest='json_out', help='optional comparison JSON inside engine/')
    parser.add_argument('--scout', nargs='?', const=str(FRONTIER / 'lab/c1/fx-scout/results/quick-3'),
                        help='also compare medium rows against this scout output (default quick-3)')
    parser.add_argument('--engine10', nargs='?', const=str(FRONTIER / 'engine/results/quick-0.10.0'),
                        help='also compare against stored Engine 0.10.0 scorecard rows')
    parser.add_argument('--engine11', nargs='?', const=str(FRONTIER / 'engine/results/quick-0.11.0'),
                        help='also compare against Engine 0.11.0 scorecard output')
    parser.add_argument('--fresh-e9', help='fresh 0.9.0+E9 result directory; use lab/c1/leader/method_e9_val.py for a matched arm')
    args = parser.parse_args(argv)
    rows = list(read_rows(args.results).values())
    report = compare(rows, baseline_rows(), seed=args.seed)
    print_report(report, 'Historical stored Frontier 0.9.0 (unequal effort/load; descriptive only)')
    payload = {'frontier09': report, 'comparison_scope': {
        'frontier09': 'historical stored rows; unequal effort and machine load, not a fair solver-gain comparator',
        'medium_lifts': 'projected later-port lifts under a fixed-stow, no-later-loads model',
        'e9': 'extra passes due to E9 correction are a bug fix, not a solver gain'}}
    if args.engine10:
        engine10 = compare(rows, read_rows(args.engine10), seed=args.seed)
        print_report(engine10, 'Frontier Engine 0.10.0 (separate comparator)')
        payload['engine10'] = engine10
    if args.engine11:
        engine11 = compare(rows, read_rows(args.engine11), seed=args.seed)
        print_report(engine11, 'Frontier Engine 0.11.0 (separate development arm; inspect effort/load)')
        payload['engine11'] = engine11
    if args.fresh_e9:
        fresh = compare(rows, read_rows(args.fresh_e9), seed=args.seed)
        print_report(fresh, 'Fresh Frontier 0.9.0 + E9 (matched-load comparator when run at the same load)')
        payload['fresh_e9'] = fresh
        payload['comparison_scope']['fresh_e9'] = dict(
            results=str(Path(args.fresh_e9).resolve()),
            method='experiments/mvp/frontier/lab/c1/leader/method_e9_val.py',
            requirement='same maps, fresh run and comparable machine load; method retains runs/val-0.9 budgets')
    if args.scout:
        medium = [row for row in rows if row.get('size') == 'medium']
        scout = compare(medium, read_rows(args.scout), seed=args.seed)
        print_report(scout, 'fx-scout quick-3 (separate comparator)')
        payload['scout'] = scout
    run_path = Path(args.results) / 'run.json'
    if run_path.exists():
        payload['run'] = json.loads(run_path.read_text())
        print(f"Measured launcher elapsed: {payload['run'].get('elapsed_s')}s")
    if args.json_out:
        path = Path(args.json_out).resolve()
        if not path.is_relative_to((FRONTIER / 'engine').resolve()):
            parser.error('comparison output must stay inside engine/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
