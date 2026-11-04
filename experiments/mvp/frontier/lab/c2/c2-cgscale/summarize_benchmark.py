"""Summarize atomic paired benchmark rows, preserving incomplete measurements."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def rows(folder):
    return {p.stem: json.loads(p.read_text()) for p in folder.glob('seed-*.json')
            if p.stem.removeprefix('seed-').isdigit()}


def ratio(before, after):
    return before/after if before is not None and after is not None and after > 0 else None


def timing_markdown(result):
    def number(value):
        if value is None:
            return 'pending'
        return f'{value:.4f}' if 0 < abs(value) < .01 else f'{value:.3f}'
    def pair(row, metric):
        return number(row['original'].get(metric))+' → '+number(row['optimized'].get(metric))
    def gate(value):
        return 'yes' if value is True else 'no' if value is False else 'pending'
    lines = ['| Seed | Boxes | LP old → new (s) | LP × | Phase 3 old → new (s) | Dive old → new (s) | Phase 3 + dive × | Uncovered old → new | Replay old → new |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for row in result['paired']:
        lines.append('| '+ ' | '.join((row['key'].removeprefix('seed-'), str(row['n_boxes']),
            pair(row, 'lp_s'), number(row['lp_speedup']), pair(row, 'phase3_s'), pair(row, 'dive_s'),
            number(row['phase3_dive_speedup']), pair(row, 'lp_uncovered'),
            gate(row['original']['replay_legal'])+' → '+gate(row['optimized']['replay_legal'])))+' |')
    lines.extend(('', 'Old is the original page master/pricer/dive with the process-only E9 mask fix. '
                  'Rows with `static_adapter_sha256` use the verified identical E8-only static adapter; '
                  'their initialization and total wall time are excluded from original end-to-end speedups. '
                  'Each variant has 60 s for phase 1 and a shared 60 s for phase 3 plus diving. '
                  'A retained feasible first-fit incumbent skips phase 3. '
                  'Replay legality is separate from full-checker passes.'))
    if 'rmp' in result.get('new_variants', []):
        lines.extend(('', 'RMP phase-1 time includes its bounded restricted-master integer call. '
                      'The JSON records each RMP status and time separately in `root_rmp`.'))
    return '\n'.join(lines)+'\n'


def summarize(original, optimized):
    paired = []
    for key in sorted(original.keys() | optimized.keys()):
        old, new = original.get(key, {}), optimized.get(key, {})
        out = dict(key=key, instance_id=(new or old).get('instance_id'),
                   n_boxes=(new or old).get('n_boxes'))
        for name, row in (('original', old), ('optimized', new)):
            out[name] = {k: row.get(k) for k in ('lp_s', 'phase3_s', 'dive_s', 'phase3_dive_s',
                                                'initialization_s', 'problem_initialization_s', 'wall_s',
                                                'complete_stow', 'replay_legal', 'lifts', 'complete', 'error',
                                                'source_sha256', 'static_adapter_sha256', 'variant',
                                                'dependency_sha256', 'root_rmp', 'rmp')}
            out[name]['lp_uncovered'] = row.get('lp', {}).get('uncovered')
            out[name]['lp_objective'] = row.get('lp', {}).get('objective')
            out[name]['identical_attribute_classes'] = row.get('class_census', {}).get('rows')
            out[name]['patterns_legal'] = row.get('pattern_audit', row.get('root_pattern_audit', {})).get('passed')
            out[name]['audited_patterns'] = row.get('pattern_audit', row.get('root_pattern_audit', {})).get('columns')
            out[name]['illegal_patterns'] = row.get('pattern_audit', row.get('root_pattern_audit', {})).get('illegal')
        out['lp_speedup'] = ratio(old.get('lp_s'), new.get('lp_s'))
        out['dive_speedup'] = ratio(old.get('dive_s'), new.get('dive_s'))
        out['phase3_dive_speedup'] = ratio(old.get('phase3_dive_s'), new.get('phase3_dive_s'))
        regression = {}
        for metric in ('uncovered', 'objective'):
            a, b = old.get('lp', {}).get(metric), new.get('lp', {}).get(metric)
            if a is not None and b is not None:
                regression[metric+'_change'] = b-a
                # Better is allowed; an original zero gets only numerical tolerance.
                regression[metric+'_tolerance'] = max(.01*abs(a), 1e-6)
                regression[metric+'_within_1pct'] = b-a <= regression[metric+'_tolerance']
        out['regression'] = regression
        paired.append(out)
    relevant = [r for r in paired if r['n_boxes'] in (121, 229)]
    totals = {}
    for metric in ('lp_s', 'dive_s', 'phase3_dive_s', 'wall_s'):
        measured = [r for r in paired if r['original'].get(metric) is not None and r['optimized'].get(metric) is not None]
        if metric == 'wall_s':
            # The static adapter preserves the measured LP/dive, but its initialization
            # is optimized and cannot stand in for original end-to-end wall time.
            measured = [r for r in measured if r['original'].get('static_adapter_sha256') is None]
        before = sum(r['original'][metric] for r in measured)
        after = sum(r['optimized'][metric] for r in measured)
        totals[metric] = dict(pairs=len(measured), original_sum=before, optimized_sum=after,
                              speedup=ratio(before, after))
        if metric == 'wall_s':
            totals[metric]['scope'] = 'raw_original_without_static_adapter_only'
    by_boxes = {}
    for count in sorted({r['n_boxes'] for r in paired if r['n_boxes'] is not None}):
        group = [r for r in paired if r['n_boxes'] == count]
        entry = dict(maps=len(group))
        for variant in ('original', 'optimized'):
            entry[variant+'_complete_replay_legal'] = sum(r[variant]['replay_legal'] is True for r in group)
        for metric in ('lp_s', 'dive_s', 'phase3_dive_s'):
            measured = [r for r in group if r['original'].get(metric) is not None and r['optimized'].get(metric) is not None]
            before = sum(r['original'][metric] for r in measured)
            after = sum(r['optimized'][metric] for r in measured)
            entry[metric] = dict(pairs=len(measured), speedup=ratio(before, after),
                                 original_mean=before/len(measured) if measured else None,
                                 optimized_mean=after/len(measured) if measured else None)
        by_boxes[str(count)] = entry
    root_rmp = [record for row in paired for record in (row['optimized'].get('root_rmp') or [])]
    return dict(paired=paired, aggregate_timings=totals, by_boxes=by_boxes,
        new_variants=sorted({r['optimized']['variant'] for r in paired if r['optimized']['variant']}),
        new_root_rmp_s=sum(record.get('seconds', 0.) for record in root_rmp),
        new_root_rmp_status_histogram=dict(sorted(Counter(record.get('status', 'UNKNOWN') for record in root_rmp).items())),
        original_finished=sum(r['original']['complete'] is True for r in paired),
        optimized_finished=sum(r['optimized']['complete'] is True for r in paired),
        optimized_patterns_audited=sum(r['optimized']['audited_patterns'] or 0 for r in paired),
        original_patterns_audited=sum(r['original']['audited_patterns'] or 0 for r in paired),
        optimized_pattern_audits_passed=all(r['optimized']['patterns_legal'] is True for r in paired),
        original_pattern_audits_passed=all(r['original']['patterns_legal'] is True for r in paired),
        small_medium_regression_passed=bool(relevant) and all(
        all(row['regression'].get(k+'_within_1pct', False) for k in ('uncovered', 'objective'))
        for row in relevant),
        optimized_complete_replay_legal=sum(r['optimized']['replay_legal'] is True for r in paired),
        original_complete_replay_legal=sum(r['original']['replay_legal'] is True for r in paired),
        optimized_428_complete_replay_legal=sum(r['n_boxes'] == 428 and r['optimized']['replay_legal'] is True for r in paired))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('original', type=Path)
    parser.add_argument('optimized', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--table-out', type=Path)
    args = parser.parse_args()
    result = summarize(rows(args.original), rows(args.optimized))
    text = json.dumps(result, indent=2, sort_keys=True)+'\n'
    if args.out:
        temporary = args.out.with_suffix('.tmp')
        temporary.write_text(text)
        temporary.replace(args.out)
    if args.table_out:
        temporary = args.table_out.with_suffix('.tmp')
        temporary.write_text(timing_markdown(result))
        temporary.replace(args.table_out)
    print(text)
