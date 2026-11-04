"""Aggregate fresh D30 holdouts without merging calibration and test populations.

Per-station CSV retains directional max/p99 and the installed default reserve.
Public dispatch checks are individual samples; they never receive a fabricated
percentile or enlarge the reserve being validated. Run after d30_validate.py.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

SHIPS = ('feeder_500', 'feeder_1000', 'feeder_1700')
LEVELS = ('light', 'medium', 'full')
SIDES = ('lower_unsafe', 'upper_unsafe')


def flat(value):
    return value if isinstance(value, list) else [value]


def peak(value):
    return max(flat(value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=Path(__file__).resolve().parents[1]/'fresh')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    cases = {}
    for path in sorted(args.input.rglob('*.json')):
        record = json.loads(path.read_text())
        if isinstance(record, dict) and record.get('ship') in SHIPS and record.get('level') in LEVELS and 'diagnostics' in record:
            key = f"{record['ship']}/{record['level']}"
            if key in cases:
                raise ValueError(f'duplicate case: {key}')
            cases[key] = (path, record)
    rows, brief = [], []
    for key, (path, record) in sorted(cases.items()):
        calibration = record['calibration']
        reserves = record['default_reserves']
        certificate = calibration['margin_policy']
        stations = certificate['station_x_m']
        for population, statistics in (('family_calibration', calibration['signed_errors']),
                                       ('physical_holdout', record['diagnostics']['signed_errors'])):
            for side in SIDES:
                for metric, values in statistics[side].items():
                    if metric == 'gm_m' and side == 'upper_unsafe':
                        continue
                    p99 = flat(statistics['p99'][side][metric])
                    reserve = flat(reserves[side][metric])
                    for index, observed in enumerate(flat(values)):
                        rows.append(dict(case=key, population=population, side=side, metric=metric,
                            station_x_m=stations[index] if metric in ('sf_n','bm_nm') else '',
                            maximum=observed, p99=p99[index], reserve=reserve[index],
                            excess=max(0., observed-reserve[index]), covered=observed <= reserve[index]))
        public = record.get('public_checker_comparison')
        if record['level'] == 'light' and not public:
            raise ValueError(f'{key}: missing required light-case public capture')
        if public:
            for side, metrics in public.get('affine_solver_unsafe', {}).items():
                for metric, values in metrics.items():
                    reserve = flat(reserves[side][metric])
                    for index, observed in enumerate(flat(values)):
                        rows.append(dict(case=key, population='captured_public_state', side=side, metric=metric,
                            station_x_m=stations[index] if metric in ('sf_n','bm_nm') else '',
                            maximum=observed, p99='', reserve=reserve[index],
                            excess=max(0.,observed-reserve[index]), covered=observed <= reserve[index]))
        held = record['diagnostics']['signed_errors']
        family = calibration['signed_errors']
        brief.append(dict(case=key, source_path=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), samples=record['diagnostics']['samples'],
            heldout_gm_unsafe_max_m=held['lower_unsafe']['gm_m'],
            heldout_gm_unsafe_p99_m=held['p99']['lower_unsafe']['gm_m'],
            heldout_gm_conservatism_max_m=held['lower_conservatism']['gm_m'],
            heldout_gm_conservatism_p99_m=held['p99']['lower_conservatism']['gm_m'],
            family_gm_unsafe_max_m=family['lower_unsafe']['gm_m'],
            family_gm_unsafe_p99_m=family['p99']['lower_unsafe']['gm_m'],
            family_gm_conservatism_max_m=family['lower_conservatism']['gm_m'],
            family_gm_conservatism_p99_m=family['p99']['lower_conservatism']['gm_m'],
            installed_gm_reserve_m=reserves['lower_unsafe']['gm_m'],
            heldout_list_branch_counts=held.get('list_branch_counts'),
            family_list_branch_counts=family.get('list_branch_counts'),
            physical_inputs_unchanged=record['physical_inputs_unchanged'],
            heldout_covered=record['all_measured_unsafe_errors_covered'],
            public_checked=public is not None,
            public_covered=record['captured_public_residuals_covered'] if public else None,
            original_public_equality_passed=public.get('passed') if public else None,
            empty_default_intervals=record['empty_default_intervals'],
            impossible_default_constant_rows=record.get('impossible_default_constant_rows', []),
            reference_transfer=certificate['reference_transfer'],
            timing_seconds=record['timings_seconds']))
    missing = [f'{ship}/{level}' for ship in SHIPS for level in LEVELS if f'{ship}/{level}' not in cases]
    failures = [row for row in rows if not row['covered']]
    envelopes = []
    for population, side, metric in sorted({(row['population'], row['side'], row['metric']) for row in rows}):
        selected = [row for row in rows if (row['population'], row['side'], row['metric']) == (population,side,metric)]
        maximum = max(selected, key=lambda row: row['maximum'])
        excess = max(selected, key=lambda row: row['excess'])
        percentile_rows = [row for row in selected if row['p99'] != '']
        percentile = max(percentile_rows, key=lambda row: row['p99']) if percentile_rows else None
        envelopes.append(dict(population=population, side=side, metric=metric,
            maximum=maximum['maximum'], maximum_case=maximum['case'], maximum_station_x_m=maximum['station_x_m'],
            p99=percentile['p99'] if percentile else None,
            p99_case=percentile['case'] if percentile else None,
            p99_station_x_m=percentile['station_x_m'] if percentile else None,
            maximum_excess=excess['excess'], excess_case=excess['case'],
            excess_station_x_m=excess['station_x_m']))
    valid = not missing and not failures and all(case['physical_inputs_unchanged'] and case['heldout_covered']
        and case['public_covered'] is not False and not case['empty_default_intervals']
        and not case['impossible_default_constant_rows'] for case in brief)
    result = dict(status='passed' if valid else 'incomplete_or_failed', missing_cases=missing,
        cases=brief, failed_metric_rows=failures, metric_envelopes=envelopes,
        populations='family calibration and physical heldout remain distinct; family rows repeat per level, not independent samples',
        public_scope='one captured light state per ship; no public percentile or full-plan acceptance claim',
        warning='Empirical sample coverage is not a uniform physics proof; exact checking remains mandatory.')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'fresh-summary.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    if rows:
        with (args.output/'fresh-metrics.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    lines = ['# Fresh D30 validation', '', f"Status: **{result['status']}**; {len(brief)}/9 cases.", '',
        '| Case | GM unsafe max/p99 (m) | GM conservatism max/p99 (m) | GM reserve (m) | All heldout bounds | Public capture | Empty/impossible rows |',
        '|---|---:|---:|---:|---|---|---|']
    for case in brief:
        public = 'not sampled' if case['public_covered'] is None else str(case['public_covered'])
        lines.append(f"| {case['case']} | {case['heldout_gm_unsafe_max_m']:.6g} / {case['heldout_gm_unsafe_p99_m']:.6g} | "
            f"{case['heldout_gm_conservatism_max_m']:.6g} / {case['heldout_gm_conservatism_p99_m']:.6g} | "
            f"{case['installed_gm_reserve_m']:.6g} | {case['heldout_covered']} | {public} | "
            f"{len(case['empty_default_intervals'])}/{len(case['impossible_default_constant_rows'])} |")
    lines += ['', 'Every directional metric/station and its installed reserve is retained in `fresh-metrics.csv`.', '',
        result['populations']+'. '+result['public_scope']+'.', '', result['warning']]
    lines += ['', '## Physical heldout envelope', '',
        'Each p99 is the largest within-case/station p99, not a pooled percentile. SI units follow metric names.', '',
        '| Direction / metric | Max unsafe | Max-case/station | Largest p99 | P99-case/station | Max excess over reserve |',
        '|---|---:|---|---:|---|---:|']
    for item in envelopes:
        if item['population'] != 'physical_holdout':
            continue
        maximum_site = item['maximum_case']+(f" @{item['maximum_station_x_m']:.6g}m" if item['maximum_station_x_m'] != '' else '')
        p99_site = item['p99_case']+(f" @{item['p99_station_x_m']:.6g}m" if item['p99_station_x_m'] != '' else '')
        lines.append(f"| {item['side']}/{item['metric']} | {item['maximum']:.6g} | {maximum_site} | "
            f"{item['p99']:.6g} | {p99_site} | {item['maximum_excess']:.6g} |")
    if missing:
        lines += ['', 'Missing: '+', '.join(missing)+'.']
    (args.output/'fresh-summary.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':result['status'], 'cases':len(brief), 'failed_rows':len(failures), 'missing':missing}))
    if args.require_complete and not valid:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
