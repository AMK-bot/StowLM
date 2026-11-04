"""JSON-only D242 completion tables; no engine imports, checker or solver work.

Example: aggregate.py --medium results/frozen36-v2 --quick results/quick18
Default requires benchmark36, quick18, the extra tiny case and five rechecks. Use
--allow-incomplete only for an explicitly stopped/incomplete measurement.
Historical pre-D242 partial-return runs must not be mixed with new measurements.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
LEADER_TOOL = HERE.parent / 'leader' / 'completion.py'
EXTRA_TINY = 'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000040.json'
spec = importlib.util.spec_from_file_location('leader_completion', LEADER_TOOL)
completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completion)


def lines(path):
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(directories):
    return completion.read_rows(','.join(str(path) for path in directories))


def select(rows, expected, allow_incomplete):
    by_input = {row['input']: row for row in rows.values()}
    if len(by_input) != len(rows):
        raise ValueError('Repeated input path under distinct instance IDs')
    extra = sorted(set(by_input)-set(expected))
    missing = sorted(set(expected)-set(by_input))
    if extra or (missing and not allow_incomplete):
        raise ValueError(f'Coverage mismatch: extra={extra}, missing={missing}')
    selected = {row['instance_id']: row for path, row in by_input.items() if path in expected}
    for path in missing:
        selected['not-measured:'+path] = dict(instance_id='not-measured:'+path,
            input=path, completed=None, status='not measured')
    return selected, missing


def size(row):
    return 'tiny' if '.tiny.' in row['instance_id'] or '/tiny/' in row['input'] else 'small'


def rung(row):
    if '/f70-' in row['input']:
        return 'f70'
    if '/f80-' in row['input']:
        return 'f80'
    return 'medium12'


def completed_record(row):
    if 'completed' in row:
        return row['completed']
    return row if completion.classify(row) is not None else None


def require_completion_schema(rows):
    for row in rows.values():
        if 'completed' not in row:
            raise ValueError(f'New measurement lacks completed field: {row["instance_id"]}')
        complete = row['completed']
        if complete and complete.get('boxes_left_ashore'):
            raise ValueError(f'Completed plan leaves boxes ashore: {row["instance_id"]}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--medium', type=Path, nargs='+', required=True)
    parser.add_argument('--quick', type=Path, nargs='+', required=True)
    parser.add_argument('--quick-tiny', type=Path, default=HERE/'results/quick-extra')
    parser.add_argument('--verification', type=Path, default=HERE/'results/verification')
    parser.add_argument('--out', type=Path, default=HERE/'results/COMPLETION.json')
    parser.add_argument('--markdown', type=Path, default=HERE/'MEASURED.md')
    parser.add_argument('--allow-incomplete', action='store_true')
    parser.add_argument('--append-notes', action='store_true')
    args = parser.parse_args()
    expected = lines(HERE/'benchmark36.txt')
    quick_expected = lines(HERE/'quick18.txt')
    development = lines(HERE/'development6.txt')
    medium, missing_medium = select(read(args.medium), expected, args.allow_incomplete)
    quick, missing_quick = select(read(args.quick), quick_expected, args.allow_incomplete)
    extra, missing_extra = select(read([args.quick_tiny]), [EXTRA_TINY], args.allow_incomplete)
    require_completion_schema(medium)
    require_completion_schema(extra)
    quick19 = dict(quick)
    if set(quick19) & set(extra):
        raise ValueError('Extra tiny case duplicates original quick18 map')
    quick19.update(extra)
    groups = {'all36': medium}
    for group in ('medium12', 'f70', 'f80'):
        groups[group] = {key: row for key, row in medium.items() if rung(row) == group}
    groups['development6'] = {key: row for key, row in medium.items() if row['input'] in development}
    groups['frozen_holdout30'] = {key: row for key, row in medium.items() if row['input'] not in development}
    groups['quick18'] = quick
    groups['quick19'] = quick19
    groups['quick_extra_tiny'] = extra
    for group in ('tiny', 'small'):
        groups['quick_'+group] = {key: row for key, row in quick19.items() if size(row) == group}
    tables = {name: completion.table(rows) for name, rows in groups.items()}
    preservation = {}
    for group in ('tiny', 'small'):
        rows = [row for row in quick.values() if size(row) == group]
        reference_passes = [row for row in rows if row.get('reference_passed')]
        for row in reference_passes:
            retained = completed_record(row)
            assert row.get('L0_result_identity') is True, row['instance_id']
            assert row.get('L0_plan_unchanged') is True, row['instance_id']
            assert retained is not None and retained['level'] == 0, row['instance_id']
            assert retained['plan'] == row['reference_plan'], row['instance_id']
        preservation[group] = dict(maps=len(rows), reference_passes=len(reference_passes),
            exact_plan_json_unchanged=len(reference_passes), result_identity_preserved=len(reference_passes),
            comparison='Same-run ordinary engine result passed through ladder L0 gate')
    frequencies = {f'L{level}': Counter() for level in range(6)}
    safe_ashore = []
    per_map = []
    for row in medium.values():
        complete = completed_record(row)
        partial = row.get('best_safe_partial')
        if partial:
            safe_ashore.append(len(partial.get('boxes_left_ashore', [])))
        if complete:
            frequencies[f'L{complete["level"]}'].update({rule['rule_id'] for rule in complete['rules_broken']})
        per_map.append(dict(instance_id=row['instance_id'], input=row['input'], rung=rung(row),
            development=row['input'] in development, completed_at=completion.classify(row),
            completed_level=complete['level'] if complete else None,
            completed_lifts=complete.get('lifts') if complete else None,
            safe_partial_percent_aboard=partial.get('boxes_aboard_percent') if partial else None,
            safe_partial_boxes_ashore=len(partial.get('boxes_left_ashore', [])) if partial else None))
    rechecks=[]
    for path in sorted(args.verification.glob('*.json')):
        row=json.loads(path.read_text())
        if 'matching' not in row:
            continue
        assert row['matching'] is True, path
        rechecks.append(dict(path=str(path), input=row['input'], level=row['level'], matching=True, sha256=sha(path)))
    if len(rechecks) < 5 and not args.allow_incomplete:
        raise ValueError(f'Need at least five matching fresh-process rechecks; have {len(rechecks)}')
    result=dict(schema_version='D242-completion-v1',
        scope='Only T5/L0 means a full checker pass; partial plans never count as completed.',
        complete_measurement=not missing_medium and not missing_quick and not missing_extra and len(rechecks) >= 5,
        missing_medium=missing_medium, missing_quick=missing_quick, missing_extra_tiny=missing_extra,
        groups=tables, development_inputs=development,
        quick_preservation=preservation, safe_partials=len(safe_ashore),
        mean_boxes_ashore_in_safe_L3_partials=mean(safe_ashore) if safe_ashore else None,
        completed_broken_rule_map_frequencies_by_level={key: dict(value) for key,value in frequencies.items()},
        fresh_checker_rechecks=rechecks, fresh_checker_recheck_count=len(rechecks),
        maps=per_map,
        sources=dict(completion_tool_sha256=sha(LEADER_TOOL),
                     frozen_code_manifest_sha256=sha(HERE/'frozen-code-hashes.json')))
    md = '# Measured completion by tier\n\n'+result['scope']+'\n\n'+completion.markdown(tables)+'\n\n'
    md += 'Coverage: '+('all36, quick19 and five fresh rechecks complete.' if result['complete_measurement'] else
        f'incomplete; medium missing {len(missing_medium)}, original quick18 missing {len(missing_quick)}, extra tiny missing {len(missing_extra)}, fresh rechecks {len(rechecks)}.')+'\n\n'
    md += 'Safe L3 partial plans: '+str(len(safe_ashore))+'; mean boxes ashore: '+str(result['mean_boxes_ashore_in_safe_L3_partials'])+'.\n\n'
    md += 'Completed broken-rule frequencies by level (maps): '+json.dumps(result['completed_broken_rule_map_frequencies_by_level'], sort_keys=True)+'.\n\n'
    md += 'Quick same-run L0 preservation: '+json.dumps(preservation, sort_keys=True)+'.\n\n'
    md += f'Fresh-process ordinary-checker rechecks: {len(rechecks)} matching records.\n\n'
    md += 'Six development inputs:\n\n'+'\n'.join('- '+path for path in development)+'\n'
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tables, indent=2, sort_keys=True)+'\n')
    args.markdown.write_text(md)
    (HERE/'MEASURED.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    if args.append_notes:
        notes=HERE/'NOTES.md'
        if '## Source freeze' not in notes.read_text():
            raise ValueError('Refusing to append measured table before source freeze')
        with notes.open('a') as stream:
            stream.write('\n\n'+md)
    print(completion.markdown(tables))


if __name__ == '__main__':
    main()
