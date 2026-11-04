"""IO-only scorecard summary: claims, fresh replay audits and full passes stay separate.

Example:
  python summarize_score.py --score old120=results/score-medium12 \
      --score rmp60=results/score-medium12-rmp \
      --audit old120=results/score-audit.json \
      --audit rmp60=results/score-audit-rmp.json \
      --out results/score-comparison.json --table-out results/score-comparison.md

Different construction budgets are labelled explicitly, never turned into a
causal speed-up claim. This script loads JSON only; it never imports a solver,
opens an instance or trusts a method claim as evidence of a full-checker pass.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean


def average(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return mean(values) if values else None


def mapped(items):
    result = {}
    for item in items:
        label, separator, path = item.partition('=')
        if not separator or not label or not path:
            raise ValueError('Expected LABEL=PATH, got '+repr(item))
        if label in result:
            raise ValueError('Duplicate label '+label)
        result[label] = Path(path)
    return result


def load_rows(folder):
    rows = []
    for path in sorted(folder.glob('*.json')):
        row = json.loads(path.read_text())
        if isinstance(row, dict) and row.get('instance_id') and row.get('complete') is True:
            rows.append(row)
    identities = [row['instance_id'] for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError('Duplicate score instance IDs in '+str(folder))
    return rows


def load_audit(path):
    if path is None or not path.exists():
        return None, {}
    data = json.loads(path.read_text())
    entries = data.get('rows', []) if isinstance(data, dict) else data
    return data, {row['instance_id']: row for row in entries if row.get('instance_id')}


def summarize(label, folder, audit_path=None, expected=12):
    rows = load_rows(folder)
    audit_data, audited = load_audit(audit_path)
    per_map, rules_by_map, rule_occurrences, closest_rules, initial_rules = [], Counter(), Counter(), Counter(), Counter()
    failures, rmp_statuses = Counter(), Counter()
    for row in rows:
        identity = row['instance_id']
        metadata = row.get('method_metadata', {})
        ladder = metadata.get('ladder', [])
        first = next((rung for rung in ladder if rung.get('rung') == 'plain'), ladder[0] if ladder else {})
        if first.get('full_check') is False:
            initial_rules.update(set(first.get('rules', [])))
        audit = audited.get(identity)
        failed_rungs = [rung for rung in ladder if rung.get('full_check') is False]
        rule_set = set()
        for rung in failed_rungs:
            rule_set.update(rung.get('rules', []))
            rule_occurrences.update(set(rung.get('rules', [])))
        for attempt in row.get('tries', []):
            if attempt.get('passed') is not True:
                rule_set.update(attempt.get('violations', []))
        rules_by_map.update(rule_set)
        closest = min(failed_rungs, key=lambda rung: (len(rung.get('rules', [])),
                      rung.get('lifts') if rung.get('lifts') is not None else float('inf')), default={})
        closest_rules.update(set(closest.get('rules', [])))
        if not row.get('passed'):
            failures[row.get('failure_reason') or row.get('status') or 'unknown'] += 1
        rmp = [record for rung in ladder for record in rung.get('rmp', [])]
        rmp_statuses.update(record.get('status', 'UNKNOWN') for record in rmp)
        replay_lifts = [rung.get('lifts') for rung in ladder if rung.get('replay_complete') is True]
        replay_lifts = [value for value in replay_lifts if isinstance(value, (int, float))]
        per_map.append(dict(instance_id=identity, n_boxes=row.get('n_boxes'),
            input=row.get('input'), budget_s=row.get('config', {}).get('budget_s'),
            finalize_s=row.get('config', {}).get('finalize_s'), status=row.get('status'),
            metadata_replay_complete=bool(metadata.get('replay_complete')),
            ladder_any_replay_complete=any(rung.get('replay_complete') is True for rung in ladder),
            replay_certificate_actions=len(metadata.get('replay_actions', [])),
            fresh_replay_legal=audit.get('replay_legal') if audit else None,
            fresh_replay_loaded=audit.get('loaded') if audit else None,
            fresh_replay_lifts=audit.get('replay_lifts') if audit else None,
            full_checker_pass=bool(row.get('passed')),
            fresh_full_checker_pass=audit.get('full_checker_pass') if audit else None,
            passed_lifts=row.get('lifts') if row.get('passed') else None,
            best_ladder_replay_lifts=min(replay_lifts) if replay_lifts else None,
            baseline_passed=row.get('baseline_passed'), baseline_lifts=row.get('baseline_lifts'),
            constructor_wall_s=row.get('propose_s'), scorecard_wall_s=row.get('wall_s'),
            finish_s=row.get('finish_s'), initial_model_s=metadata.get('initial_model_s'),
            first_stow_s=first.get('s'), first_lp_s=first.get('lp_s'),
            first_lp_uncovered=first.get('lp_uncovered'), first_lp_objective=first.get('lp_objective'),
            first_full_check=first.get('full_check'), first_full_check_rules=first.get('rules', []),
            first_stop_reason=first.get('reason'), first_left=first.get('left'),
            first_dive_s=first.get('dive_s'), first_tail=first.get('tail'),
            ladder_lp_s=sum(rung.get('lp_s', 0.) for rung in ladder),
            ladder_dive_s=sum(rung.get('dive_s', 0.) for rung in ladder),
            ladder_full_check_s=sum(rung.get('full_check_s', 0.) for rung in ladder),
            ladder_full_checks=sum('full_check' in rung for rung in ladder),
            ladder_full_check_passes=sum(rung.get('full_check') is True for rung in ladder),
            rmp_s=sum(record.get('seconds', 0.) for record in rmp),
            rmp_statuses=[record.get('status', 'UNKNOWN') for record in rmp], rmp_records=rmp,
            failure_rules=sorted(rule_set), closest_failed_stow_rules=sorted(closest.get('rules', [])),
            failure_reason=row.get('failure_reason'), ladder=ladder,
            source_sha256=row.get('config', {}).get('source_sha256'),
            method_sha256=row.get('config', {}).get('method', {}).get('sha256'),
            pages_sha256=metadata.get('pages_sha256')))
    known = [row for row in per_map if isinstance(row['baseline_passed'], bool)]
    mutual = [row for row in per_map if row['full_checker_pass'] and row['baseline_passed'] is True
              and isinstance(row['passed_lifts'], (int, float)) and isinstance(row['baseline_lifts'], (int, float))]
    audit_covered = [row for row in per_map if row['fresh_replay_legal'] is not None]
    passed = [row for row in per_map if row['full_checker_pass']]
    replay_claims = sum(row['metadata_replay_complete'] for row in per_map)
    return dict(label=label, folder=str(folder), audit_path=str(audit_path) if audit_path else None,
        expected_maps=expected, completed_maps=len(rows), cohort_complete=len(rows) == expected,
        budgets_s=sorted({row['budget_s'] for row in per_map if row['budget_s'] is not None}),
        finalize_budgets_s=sorted({row['finalize_s'] for row in per_map if row['finalize_s'] is not None}),
        metadata_replay_complete=replay_claims,
        metadata_complete_428=sum(row['metadata_replay_complete'] and row['n_boxes'] == 428 for row in per_map),
        fresh_audit_present=audit_data is not None, fresh_audit_covered_maps=len(audit_covered),
        fresh_audit_complete=len(audit_covered) == expected and len(rows) == expected,
        fresh_replay_legal=sum(row['fresh_replay_legal'] is True for row in per_map) if audit_data is not None else None,
        fresh_complete_428=sum(row['fresh_replay_legal'] is True and row['n_boxes'] == 428 for row in per_map) if audit_data is not None else None,
        metadata_fresh_replay_mismatches=[row['instance_id'] for row in audit_covered
                                         if row['metadata_replay_complete'] != row['fresh_replay_legal']],
        metadata_ladder_mismatches=[row['instance_id'] for row in per_map
                                    if row['metadata_replay_complete'] != row['ladder_any_replay_complete']],
        full_checker_passes=len(passed), mean_passed_lifts=average(row['passed_lifts'] for row in passed),
        full_checker_passes_428=sum(row['full_checker_pass'] and row['n_boxes'] == 428 for row in per_map),
        fresh_full_checker_passes=sum(row['fresh_full_checker_pass'] is True for row in per_map) if audit_data is not None else None,
        scorecard_fresh_full_mismatches=[row['instance_id'] for row in audit_covered
                                        if row['full_checker_pass'] != row['fresh_full_checker_pass']],
        baseline_known_maps=len(known), baseline_unknown_maps=len(rows)-len(known),
        baseline_passes=sum(row['baseline_passed'] is True for row in known),
        mutual_passes=len(mutual), paired_mean_lifts=average(row['passed_lifts'] for row in mutual),
        paired_baseline_mean_lifts=average(row['baseline_lifts'] for row in mutual),
        paired_mean_lift_change=average(row['passed_lifts']-row['baseline_lifts'] for row in mutual),
        mean_constructor_wall_s=average(row['constructor_wall_s'] for row in per_map),
        mean_scorecard_wall_s=average(row['scorecard_wall_s'] for row in per_map),
        total_ladder_lp_s=sum(row['ladder_lp_s'] for row in per_map),
        total_ladder_dive_s=sum(row['ladder_dive_s'] for row in per_map),
        total_ladder_full_check_s=sum(row['ladder_full_check_s'] for row in per_map),
        total_ladder_full_checks=sum(row['ladder_full_checks'] for row in per_map),
        total_ladder_full_check_passes=sum(row['ladder_full_check_passes'] for row in per_map),
        initial_stow_full_checks=sum(isinstance(row['first_full_check'], bool) for row in per_map),
        rmp_status_histogram=dict(sorted(rmp_statuses.items())),
        failure_rule_map_histogram=dict(sorted(rules_by_map.items())),
        failure_rule_rung_histogram=dict(sorted(rule_occurrences.items())),
        initial_stow_failure_rule_map_histogram=dict(sorted(initial_rules.items())),
        closest_failed_stow_rule_map_histogram=dict(sorted(closest_rules.items())),
        failure_reason_histogram=dict(sorted(failures.items())), rows=per_map)


def markdown(result):
    def value(item):
        return 'unmeasured' if item is None else f'{item:.3f}' if isinstance(item, float) else str(item)
    lines = ['Construction budgets differ between labelled runs; these wall times do not establish a causal speed-up.',
             'Metadata replay claims, fresh replay audits and full-checker passes are separate gates. '
             'LP times include restricted-master calls; method dive times include the phase 3 cost polish. '
             'Construction wall time includes the ladder\'s internal full checks.', '',
             '| Run | Budget (s) | Rows | Replay claimed | Fresh replay | Full passes | Fresh full passes | Mean passed lifts | Baseline known | Mutual passes | Mean construction (s) | Mean total (s) |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for group in result['runs']:
        cells = (group['label'], ','.join(map(str, group['budgets_s'])), group['completed_maps'],
                 group['metadata_replay_complete'], group['fresh_replay_legal'], group['full_checker_passes'],
                 group['fresh_full_checker_passes'], group['mean_passed_lifts'], group['baseline_known_maps'],
                 group['mutual_passes'], group['mean_constructor_wall_s'], group['mean_scorecard_wall_s'])
        lines.append('| '+' | '.join(value(item) for item in cells)+' |')
    for group in result['runs']:
        lines.extend(('', group['label']+':', '',
            '| Map | Boxes | Claimed / fresh replay | Full pass | Fresh replay lifts | Passed lifts | First stow (s) | First LP (s) | First dive (s) | RMP statuses | First stop / boxes left | Construction / total (s) | Failure rules |',
            '| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |'))
        for row in group['rows']:
            cells = (row['instance_id'], row['n_boxes'],
                     str(row['metadata_replay_complete'])+' / '+value(row['fresh_replay_legal']),
                     row['full_checker_pass'], row['fresh_replay_lifts'], row['passed_lifts'], row['first_stow_s'], row['first_lp_s'],
                     row['first_dive_s'], ', '.join(row['rmp_statuses']) or 'none',
                     (row['first_stop_reason'] or row['first_tail'] or 'unrecorded')+' / '+value(row['first_left']),
                     value(row['constructor_wall_s'])+' / '+value(row['scorecard_wall_s']),
                     ', '.join(row['failure_rules']) or 'none')
            lines.append('| '+' | '.join(value(item) for item in cells)+' |')
        lines.extend(('', '| Failed full-check rule | Maps with rule | Failed rungs with rule |',
                      '| --- | ---: | ---: |'))
        for rule, count in group['failure_rule_map_histogram'].items():
            lines.append(f"| {rule} | {count} | {group['failure_rule_rung_histogram'].get(rule, 0)} |")
    return '\n'.join(lines)+'\n'


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--score', action='append', required=True, metavar='LABEL=FOLDER')
    parser.add_argument('--audit', action='append', default=[], metavar='LABEL=FILE')
    parser.add_argument('--expected', type=int, default=12)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--table-out', type=Path, required=True)
    args = parser.parse_args()
    folders, audits = mapped(args.score), mapped(args.audit)
    if set(audits)-set(folders):
        parser.error('Every audit label must name a --score run')
    result = dict(runs=[summarize(label, folder, audits.get(label), args.expected)
                        for label, folder in folders.items()],
                  comparisons_are_budget_labelled_not_causal_speedups=True,
                  gate='Independent full checker defines passes; fresh apply_plan replay defines audited complete stows.')
    atomic(args.out, json.dumps(result, indent=2, sort_keys=True)+'\n')
    atomic(args.table_out, markdown(result))
    for group in result['runs']:
        print(json.dumps({key: group[key] for key in ('label', 'completed_maps', 'metadata_replay_complete',
              'fresh_audit_covered_maps', 'fresh_replay_legal', 'full_checker_passes', 'fresh_full_checker_passes')}))


if __name__ == '__main__':
    main()
