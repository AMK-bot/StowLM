"""Summarise admitted baseline plans without treating missing plans as zero cost."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import mean


def fmt(value):
    return '—' if value is None else f'{value:.3f}'


def summarize_rows(rows):
    """Report conditional rates with their denominators; drift never qualifies.

    A terminal failure is a non-ok terminal outcome after an ok pre-stow.
    Fixed-slot failures are a separate implementation-reported subset. Missing
    submissions mean unknown pass rate, rather than a zero-checker pass rate.
    Historical rows without epoch pins remain raw observations only.
    """
    result = {}
    for rung in dict.fromkeys(row['rung'] for row in rows):
        cohort = [row for row in rows if row['rung'] == rung]
        qualified = [row for row in cohort if row.get('epoch_stable') is True]
        observed = qualified
        reached = [row for row in observed if row.get('metrics', {}).get('preplan_status') == 'ok'
                   and row.get('metrics', {}).get('terminal_status', 'not_started') != 'not_started']
        failures = [row for row in reached if row['metrics']['terminal_status'] != 'ok']
        checked = [row for row in observed if 'checker_seconds' in row and row.get('ballast_present')]
        passed = sum(row.get('checker_passed', False) for row in checked)
        result[rung] = dict(requests=len(cohort), qualified_requests=len(qualified),
            quarantined=sum(row.get('epoch_stable') is False for row in cohort),
            unpinned=sum('epoch_stable' not in row for row in cohort),
            legal=sum(bool(row.get('legal')) for row in qualified),
            terminal_reached=len(reached), terminal_failures=len(failures),
            fixed_slot_failures=sum(bool(row['metrics'].get('fixed_slot_failure')) for row in reached),
            terminal_failure_frequency=len(failures)/len(reached) if reached else None,
            checker_after_ballast_submissions=len(checked), checker_after_ballast_passes=passed,
            checker_after_ballast_pass_rate=passed/len(checked) if checked else None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    rows = json.loads(args.results.read_text())
    if args.json:
        print(json.dumps(summarize_rows(rows), indent=2, sort_keys=True))
        return
    summaries = summarize_rows(rows)
    print('| Rung | Legal / qualified / requests | Terminal failures / reached | Checker passes / after-ballast submissions | D11 weighted lifts | Mean lifts by type | Gap to LB | Raw wall s | Tiny Δ optimum |')
    print('|---|---:|---:|---:|---:|---|---:|---:|---:|')
    for rung in ('tiny', 'small', 'medium', 'full'):
        cohort = [row for row in rows if row['rung'] == rung]
        if not cohort:
            continue
        qualified = [row for row in cohort if row.get('epoch_stable') is True]
        legal = [row for row in qualified if row.get('legal')]
        summary = summaries[rung]
        objectives = [row['objective'] for row in legal]
        gaps = [row['gap_to_lb'] for row in legal]
        lifts = ', '.join(f'{key}={mean(row["lifts"][key] for row in legal):g}' for key in legal[0]['lifts']) if legal else '—'
        deltas = [row['difference_to_optimum'] for row in legal if row.get('difference_to_optimum') is not None]
        print(f'| {rung} | {len(legal)}/{len(qualified)}/{len(cohort)} | {summary["terminal_failures"]}/{summary["terminal_reached"]} | {summary["checker_after_ballast_passes"]}/{summary["checker_after_ballast_submissions"]} | {fmt(mean(objectives)) if objectives else "—"} | {lifts} | {fmt(mean(gaps)) if gaps else "—"} | {mean(row["process_seconds"] for row in cohort):.3f} | {fmt(mean(deltas)) if deltas else "—"} |')


if __name__ == '__main__':
    main()
