"""Read-only aggregation of cold scorecard rows, with fair-comparison labels.

This does not import a solver or checker, and never invents passing lift values
for failed rows. Only the fixed development-set inputs and this experiment's
own result files are opened. The optional comparison is a fresh E9-corrected
arm; the runner's stored baseline fields are deliberately excluded.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
ROOT = LAB.parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_maps(set_name='medium12'):
    files = {'medium12': LAB/'sets/medium12.txt', 'first': HERE/'first-map.txt'}
    selected = files[set_name]
    output = []
    for line in selected.read_text().splitlines():
        relative = line.strip()
        if not relative or relative.startswith('#'):
            continue
        path = (ROOT/relative).resolve()
        if not path.is_relative_to(ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium'):
            raise ValueError('unexpected path in committed D185 list')
        data = json.loads(path.read_bytes())
        output.append(dict(instance_id=data['id'], input=relative, input_sha256=digest(path)))
    return output


def _read_row(directory, identity):
    path = Path(directory)/f'{identity}.json'
    return json.loads(path.read_bytes()) if path.exists() else None


def inspect_row(row, expected, *, check_sources=True):
    if row is None:
        return dict(instance_id=expected['instance_id'], status='unattempted', resolved=False,
                    complete_replay_legal_stows=0, full_checker_pass=False, lifts=None,
                    binding_rules=[], issues=[])
    issues = []
    if row.get('input_sha256') != expected['input_sha256']:
        issues.append('input_hash_mismatch')
    if row.get('input') != expected['input']:
        issues.append('input_path_mismatch')
    meta = row.get('method_metadata', {}).get('cold_pipeline', {})
    if meta.get('cold') is not True or meta.get('stored_context_used') is not False:
        issues.append('missing_cold_provenance')
    if meta.get('runtime_historical_artifacts_read') is not False:
        issues.append('historical_runtime_provenance_unknown')
    if meta.get('implementation_unchanged') is False:
        issues.append('implementation_changed_during_run')
    if meta.get('loaded_dependencies_unchanged') is False:
        issues.append('loaded_dependencies_changed_during_run')
    if check_sources:
        for relative, source_hash in meta.get('implementation_sha256', {}).items():
            source = (ROOT/relative).resolve()
            if not source.is_relative_to(HERE):
                issues.append('source_manifest_outside_experiment')
            elif not source.exists() or digest(source) != source_hash:
                issues.append('implementation_changed:'+relative)
        for relative, source_hash in meta.get('runtime_dependency_sha256_after', {}).items():
            source = (ROOT/relative).resolve()
            if (not source.is_relative_to(ROOT) or not source.exists() or
                    digest(source) != source_hash):
                issues.append('runtime_dependency_changed:'+relative)
    complete_count = int(meta.get('complete_replay_legal_stows', 0))
    rejected = {item.get('candidate') for item in row.get('candidate_errors', [])}
    runner_legal = sum(c.get('complete_replay_legal') is True and
                       c.get('runner_candidate_index') not in rejected
                       for c in meta.get('candidates', []))
    attempts = meta.get('finish_attempts', [])
    bindings = [dict(candidate_id=attempt.get('candidate_id'),
                     bindings=attempt.get('binding_rules', []),
                     status=attempt.get('finisher', {}).get('status', attempt.get('status')))
                for attempt in attempts]
    # The raw shared-runner pass is authoritative; issues prevent attribution
    # to this frozen cold implementation, without rewriting its raw result.
    raw_pass = row.get('passed') is True
    checked_pass = bool(raw_pass and row.get('plan') is not None and not issues)
    if raw_pass and row.get('plan') is None:
        issues.append('passing_row_has_no_plan')
    evaluations = 0
    for attempt in attempts:
        value = attempt.get('finisher', {}).get('evaluations', 0)
        if isinstance(value, (int, float)):
            evaluations += value
    return dict(instance_id=expected['instance_id'], status=row.get('status'),
        resolved=row.get('complete') is True, raw_runner_pass=raw_pass,
        full_checker_pass=checked_pass, complete_replay_legal_stows=complete_count,
        runner_legal_complete_candidates=runner_legal,
        maps_complete_gate='constructor real replay; runner action replay scored independently',
        lifts=row.get('lifts') if checked_pass else None,
        parts=row.get('parts') if checked_pass else None,
        construction_s=row.get('propose_s'), finish_and_independent_check_s=row.get('finish_s'),
        wall_s=row.get('wall_s'), evaluations=evaluations, finish_attempts=len(attempts),
        binding_rules=bindings,
        certified_fixed_stow_rejections=meta.get('certified_fixed_stow_rejections', 0),
        securing_certificates=[dict(candidate_id=c.get('candidate_id'),
            proof=c.get('securing_screen', {}).get('proof'),
            lower_ratios=c.get('securing_screen', {}).get('lower_ratios', {}))
            for c in meta.get('candidates', []) if c.get('ballast_verdict') == 'proved_cannot_for_fixed_stow'],
        failed_runner_rules=[attempt.get('violations', []) for attempt in row.get('tries', [])
                             if not attempt.get('passed')],
        detail_directory=meta.get('detail_directory'), issues=issues)


def aggregate(directory, *, set_name='medium12', comparator=None, check_sources=True):
    expected = expected_maps(set_name)
    rows, pairs, comparison_issues = [], [], []
    for item in expected:
        raw = _read_row(directory, item['instance_id'])
        row = inspect_row(raw, item, check_sources=check_sources)
        rows.append(row)
        if comparator is None:
            continue
        reference = _read_row(comparator, item['instance_id'])
        if reference is None:
            continue
        method = reference.get('config', {}).get('method', {})
        if (reference.get('input_sha256') != item['input_sha256'] or
                not str(method.get('path', '')).endswith('/c1/leader/method_e9_val.py')):
            comparison_issues.append(item['instance_id']+':not_bound_fresh_e9_arm')
            continue
        if row['resolved'] and reference.get('complete') is True:
            pairs.append(dict(instance_id=item['instance_id'],
                candidate_pass=row['full_checker_pass'], reference_pass=reference.get('passed') is True,
                candidate_lifts=row['lifts'],
                reference_lifts=reference.get('lifts') if reference.get('passed') else None,
                candidate_wall_s=row.get('wall_s'), reference_wall_s=reference.get('wall_s')))
    passing = [row for row in rows if row['full_checker_pass']]
    mutual = [pair for pair in pairs if pair['candidate_pass'] and pair['reference_pass']]
    comparison = dict(kind='no fresh comparator supplied; no solver-gain claim', paired_maps=0)
    if comparator is not None:
        comparison = dict(kind='fresh 0.9.0 plus E9 fix, development maps',
            matched_load_verified=False,
            qualification='Source/budget identity alone does not prove matched machine load; timings are descriptive.',
            paired_maps=len(pairs), candidate_passes=sum(p['candidate_pass'] for p in pairs),
            reference_passes=sum(p['reference_pass'] for p in pairs), mutual_passes=len(mutual),
            candidate_mutual_mean_lifts=mean(p['candidate_lifts'] for p in mutual) if mutual else None,
            reference_mutual_mean_lifts=mean(p['reference_lifts'] for p in mutual) if mutual else None,
            paired_mean_change=mean(p['candidate_lifts']-p['reference_lifts'] for p in mutual) if mutual else None,
            issues=comparison_issues, pairs=pairs)
    return dict(set=set_name, data_role='adaptive TRAIN development; not unseen',
        selected_maps=len(expected), resolved_maps=sum(r['resolved'] for r in rows),
        unattempted_maps=sum(not r['resolved'] for r in rows),
        maps_with_complete_replay_legal_stow=sum(r['complete_replay_legal_stows'] > 0 for r in rows),
        complete_replay_legal_stows=sum(r['complete_replay_legal_stows'] for r in rows),
        full_checker_passes=len(passing),
        passing_mean_projected_lifts=mean(r['lifts'] for r in passing) if passing else None,
        objective_scope='weighted nonproductive lifts including D15 projected later-port work; fixed stow, no later loads',
        physical_infeasibility_proven=False, whole_map_impossibility_claim=False,
        comparison=comparison, statuses=dict(Counter(r['status'] for r in rows)), maps=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=Path, required=True)
    parser.add_argument('--set', choices=('first', 'medium12'), default='medium12')
    parser.add_argument('--fresh-e9-rows', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--allow-changed-sources', action='store_true',
                        help='inspect archived rows without attributing them to current helper bytes')
    args = parser.parse_args()
    if not args.out.resolve().is_relative_to(HERE):
        parser.error('output must remain in c4/c2-benders')
    result = aggregate(args.rows, set_name=args.set, comparator=args.fresh_e9_rows,
                       check_sources=not args.allow_changed_sources)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'maps'}, sort_keys=True))


if __name__ == '__main__':
    main()
