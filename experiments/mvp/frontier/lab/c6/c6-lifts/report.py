"""Summarize frozen fresh pairs, certified intervals, and tier completion."""
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean

from compare import comparison, rows

HERE = Path(__file__).resolve().parent


def main():
    split = json.loads((HERE/'SPLIT.json').read_text())
    certificate = rows(HERE/'results/bounds-primary28')
    primary = split['development']+split['held']
    all_result, tables, gates = {}, {}, {}
    spec = importlib.util.spec_from_file_location('completion_for_c6', HERE.parents[1]/'c5/leader/completion.py')
    completion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(completion)
    for name in ('all48', 'quick_tiny', 'quick_small'):
        a, b = rows(HERE/'results'/f'baseline-{name}'), rows(HERE/'results'/f'candidate-{name}')
        if not a and not b:
            continue
        groups = {'all': comparison(a, b)}
        if name == 'all48':
            for label, ids in [('dev6', split['development']), ('held22', split['held']), ('primary28', primary)]:
                groups[label] = comparison(a, b, ids)
            for rung in ('f60', 'f70', 'f80', 'D185'):
                ids = [i for i in set(a)|set(b) if (f'-{rung}-' in i if rung != 'D185' else not i.startswith('D192.'))]
                groups[rung] = comparison(a, b, ids)
        all_result[name] = groups
        tables[f'{name} c5'] = completion.table(a)
        tables[f'{name} c6'] = completion.table(b)
        if name == 'all48':
            for label, ids in [('primary28', primary), ('dev6', split['development']), ('held22', split['held'])]:
                tables[f'{label} c5'] = completion.table({i: a[i] for i in ids if i in a})
                tables[f'{label} c6'] = completion.table({i: b[i] for i in ids if i in b})
        gates[name] = {arm: dict(maps=len(data),
                                  complete_replay_legal=sum(bool(r.get('tries')) for r in data.values()),
                                  full_checker_passes=sum(r['passed'] for r in data.values()),
                                  method_reports_replay_legal=sum(bool(r.get('method_metadata', {}).get('replay_legal')) for r in data.values()))
                       for arm, data in [('baseline', a), ('candidate', b)]}
    b = rows(HERE/'results/candidate-all48')
    fresh = rows(HERE/'results/baseline-all48')
    internal = []
    for identity, row in b.items():
        meta = row.get('method_metadata', {})
        if row['passed'] and 'construction_lifts' in meta:
            internal.append(dict(instance_id=identity, construction_lifts=meta['construction_lifts'],
                                 polished_lifts=meta['polished_lifts'], checked_final_lifts=row['lifts'],
                                 fresh_baseline_lifts=fresh.get(identity, {}).get('lifts')))
    within_process = dict(maps=len(internal), by_map=internal,
                          total_saved=sum(r['construction_lifts']-r['polished_lifts'] for r in internal),
                          regressions=[r for r in internal if r['polished_lifts'] > r['construction_lifts']+1e-9],
                          initial_differs_from_fresh=[r for r in internal if r['fresh_baseline_lifts'] is not None
                                                     and abs(r['construction_lifts']-r['fresh_baseline_lifts']) > 1e-9])
    intervals = []
    for i in primary:
        if i not in certificate or i not in b or not b[i]['passed']:
            continue
        lower, upper = certificate[i]['lower_bound'], b[i]['lifts']
        if lower > upper+1e-6:
            raise AssertionError(f'Invalid bound interval: {i}: {lower} > {upper}')
        intervals.append(dict(instance_id=i, lower_bound=lower, checked_lifts=upper,
                              absolute_gap=upper-lower, gap_over_upper=(upper-lower)/upper,
                              gap_over_lower=(upper-lower)/lower if lower else None,
                              bound_wall_s=certificate[i]['wall_s']))
    gaps = dict(maps=len(intervals), by_map=intervals)
    gaps['at_bound'] = [r['instance_id'] for r in intervals if abs(r['absolute_gap']) <= 1e-9]
    gaps['scope'] = 'fewest weighted lifts among complete full-checker passing plans; D15 fixed stow, no later loads'
    for key in ('lower_bound', 'checked_lifts', 'absolute_gap', 'gap_over_upper', 'gap_over_lower', 'bound_wall_s'):
        gaps['mean_'+key] = mean(r[key] for r in intervals) if intervals else None
    stages = {}
    for stage in ('polish', 'wide_polish'):
        data = [r.get('method_metadata', {}).get(stage) for r in b.values()]
        data = [r for r in data if r]
        stages[stage] = dict(maps=len(data), accepted=sum(r.get('accepted', 0) for r in data),
                             full_check_rejects=sum(r.get('full_check_rejects', 0) for r in data),
                             setup_errors=[r for r in data if 'error' in r or 'adapter_error' in r],
                             mean_wall_s=mean(r.get('wall_s', 0) for r in data) if data else None)
    frozen = json.loads((HERE/'FROZEN.json').read_text())
    changed = [name for name, digest in frozen['sha256'].items()
               if hashlib.sha256((HERE/name).read_bytes()).hexdigest() != digest]
    result = dict(paired=all_result, gaps=gaps, completion=tables, gates=gates, stages=stages,
                  within_process=within_process, changed_frozen_sources=changed)
    (HERE/'RESULTS.json').write_text(json.dumps(result, indent=2)+'\n')
    lines = [completion.markdown(tables), '', '| Set | Passes c5 / c6 | Wins / ties / losses | Mean lifts c5 / c6 | Wall s c5 / c6 |',
             '|---|---:|---:|---:|---:|']
    for name, groups in all_result.items():
        for group, row in groups.items():
            if row['missing']:
                continue
            def number(value):
                return '—' if value is None else f'{value:.2f}'
            lines.append(f'| {name}/{group} | {row["baseline_passes"]} / {row["candidate_passes"]} | '
                         f'{row["wins"]} / {row["ties"]} / {row["losses"]} | '
                         f'{number(row["baseline_mean_lifts"])} / {number(row["candidate_mean_lifts"])} | '
                         f'{number(row["baseline_mean_wall_s"])} / {number(row["candidate_mean_wall_s"])} |')
    lines += ['', 'Certificate gaps: '+json.dumps({k: v for k, v in gaps.items() if k != 'by_map'}),
              '', 'Complete replay and full-checker gates: '+json.dumps(gates),
              '', 'Frozen source changes: '+json.dumps(changed)]
    (HERE/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
