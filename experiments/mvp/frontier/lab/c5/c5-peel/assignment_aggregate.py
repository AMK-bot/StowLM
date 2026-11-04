"""Read-only JSON aggregation for the replacement assignment LP ladder.

No engine imports, map loads, solver work or stored-plan runtime inputs. The
comparison is the exact same 36 inputs from c5-securing's cold frozen-48 arm.
Only L0/T5 is a full checker pass. Run after all rows and five fresh audits;
--allow-incomplete explicitly labels missing measurements.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
LEADER_TOOL = HERE.parent/'leader/completion.py'
BASELINE = HERE.parent/'c5-securing/results/frozen-48'
EXTRA_TINY = 'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000040.json'
spec = importlib.util.spec_from_file_location('leader_assignment_completion', LEADER_TOOL)
completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completion)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paths(path):
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def average(values):
    values = [value for value in values if numeric(value)]
    return mean(values) if values else None


def read_rows(directories):
    rows, sources = {}, {}
    for directory in directories:
        for path in sorted(directory.glob('*.json')):
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or not row.get('instance_id'):
                continue
            key = row['input']
            if key in rows:
                raise ValueError(f'Duplicate input row: {key}')
            rows[key] = row
            sources[key] = dict(path=str(path), sha256=sha(path))
    return rows, sources


def select(rows, expected, allow_incomplete, *, assignment=True):
    extra = sorted(set(rows)-set(expected))
    missing = sorted(set(expected)-set(rows))
    if extra or (missing and not allow_incomplete):
        raise ValueError(f'Coverage mismatch: extra={extra}; missing={missing}')
    selected = {}
    for path in expected:
        row = rows.get(path)
        if row is None:
            row = dict(input=path, instance_id='not-measured:'+path, completed=None, status='not measured')
        elif assignment:
            if 'completed' not in row:
                raise ValueError(f'Assignment row lacks D242 completed field: {path}')
            if row.get('experiment') != 'assignment LP ladder':
                raise ValueError(f'Wrong experiment mixed into assignment rows: {path}')
            complete = row['completed']
            if complete and (complete.get('boxes_left_ashore') or complete.get('boxes_aboard_percent') != 100):
                raise ValueError(f'Completed assignment plan is partial: {path}')
        selected[path] = row
    return selected, missing


def keyed(rows):
    return {row['instance_id']: row for row in rows.values()}


def rung(path):
    for value in ('f60', 'f70', 'f80'):
        if f'/{value}-' in path:
            return value
    return 'medium12'


def group_rows(medium, quick, extra, development):
    groups = dict(all36=medium)
    for name in ('medium12', 'f70', 'f80'):
        groups[name] = {path: row for path, row in medium.items() if rung(path) == name}
    groups['development6'] = {path: row for path, row in medium.items() if path in development}
    groups['frozen_holdout30'] = {path: row for path, row in medium.items() if path not in development}
    groups['quick18'] = quick
    groups['quick_extra_tiny'] = extra
    groups['quick19'] = dict(quick, **extra)
    for size in ('tiny', 'small'):
        groups['quick_'+size] = {path: row for path, row in groups['quick19'].items() if f'/{size}/' in path}
    return groups


def quick_preservation(rows):
    result = {}
    for size in ('tiny', 'small'):
        selected = [row for path, row in rows.items() if f'/{size}/' in path]
        passed = [row for row in selected if row.get('reference_passed')]
        for row in passed:
            complete = row['completed']
            assert complete and complete['level'] == 0, row['instance_id']
            assert row.get('L0_result_identity') is True, row['instance_id']
            assert row.get('L0_plan_unchanged') is True, row['instance_id']
            assert complete['plan'] == row['reference_plan'] == row['plan'], row['instance_id']
        result[size] = dict(maps=len(selected), ordinary_reference_passes=len(passed),
            exact_plan_json_unchanged=len(passed), result_identity_preserved=len(passed),
            comparison='Same-run ordinary assignment method result through the L0 control gate')
    return result


def elastic_diagnostics(row):
    """Keep complete primary diagnostics; summarise only explicitly labelled proxies."""
    found = []
    def visit(value, trail):
        if isinstance(value, dict):
            if value.get('kind') == 'goal_program':
                found.append(dict(value, diagnostic_path=trail))
                return
            for key, child in value.items():
                if key not in ('plan', 'checker_report', 'reference_plan'):
                    visit(child, trail+'.'+key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{trail}[{index}]')
    visit(row.get('trials', []), 'trials')
    return found


def slack_summary(diagnostics):
    counts, sums = Counter(), defaultdict(float)
    statuses = Counter()
    for diagnostic in diagnostics:
        statuses[str(diagnostic.get('status'))] += 1
        for item in diagnostic.get('row_slacks', []):
            slack = item.get('slack')
            if numeric(slack) and slack > 0:
                key = (item['rule_id'], item.get('unit', 'LP row units'))
                counts[key] += 1
                sums[key] += slack
    return dict(models=len(diagnostics), statuses=dict(statuses),
        positive_row_slacks=[dict(rule_id=rule, unit=unit, occurrences=counts[(rule,unit)],
            total_proxy_slack=sums[(rule,unit)]) for rule,unit in sorted(counts)],
        scope='LP row proxies only; never checker breaches or a pass certificate')


def runtime(rows):
    values = [row.get('wall_s') for row in rows.values() if numeric(row.get('wall_s'))]
    complete = [row['completed'] for row in rows.values() if row.get('completed')]
    return dict(measured_maps=len(values), total_wall_s=sum(values),
        mean_wall_s=mean(values) if values else None, median_wall_s=median(values) if values else None,
        mean_lifts_completed=average(row.get('lifts') for row in complete),
        mean_lifts_L0=average(row.get('lifts') for row in complete if row['level'] == 0))


def compare(rows, baseline):
    paired = [(row, baseline[path]) for path,row in rows.items()
              if path in baseline and row.get('status') != 'not measured']
    for current, old in paired:
        if current.get('input_sha256') and old.get('input_sha256'):
            assert current['input_sha256'] == old['input_sha256'], current['input']
    both = [(new, old) for new,old in paired if new.get('completed',{} ) and
            new['completed']['level'] == 0 and old.get('passed') is True]
    return dict(same_map_pairs=len(paired), baseline_L0_passes=sum(old.get('passed') is True for _,old in paired),
        assignment_L0_passes=sum(bool(new.get('completed')) and new['completed']['level']==0 for new,_ in paired),
        new_L0_passes=sum(bool(new.get('completed')) and new['completed']['level']==0 and old.get('passed') is not True for new,old in paired),
        lost_L0_passes=sum(old.get('passed') is True and not (new.get('completed') and new['completed']['level']==0) for new,old in paired),
        mutual_L0_maps=len(both),
        paired_L0_mean_lift_change=average(new['completed'].get('lifts')-old['lifts'] for new,old in both),
        assignment_mean_wall_s=average(new.get('wall_s') for new,_ in paired),
        baseline_mean_wall_s=average(old.get('wall_s') for _,old in paired),
        timing_scope='Cold saved baseline and new cold rows; different work/load, descriptive timings, no matched-budget speed claim')


def audits(directory, all_rows, allow_incomplete):
    result=[]
    for path in sorted(directory.glob('*.json')):
        row=json.loads(path.read_text())
        if not isinstance(row,dict) or 'matching' not in row:
            continue
        assert row['matching'] is True, path
        source=all_rows.get(row['input'])
        if source is None:
            raise ValueError(f'Audit does not belong to this assignment cohort: {path}')
        assert row['level']==source.get('level'), path
        assert row['checker_report']['violations']==source['checker_report']['violations'], path
        assert sorted(r['rule_id']for r in row['rules_broken'])==sorted(r['rule_id']for r in source['rules_broken']),path
        result.append(dict(path=str(path),sha256=sha(path),input=row['input'],level=row['level'],
                           matching=True,fresh_pid=row.get('fresh_pid'),
                           plan_sha256=hashlib.sha256(json.dumps(source['plan'],sort_keys=True).encode()).hexdigest()))
    relaxed=[row for row in result if row['level'] is not None and row['level']>0]
    fresh={row['fresh_pid']for row in relaxed if row['fresh_pid']is not None}
    distinct_plans={(row['input'],row['plan_sha256'])for row in relaxed}
    if not allow_incomplete and (len(relaxed)<5 or len(fresh)<5 or len(distinct_plans)<5):
        raise ValueError(f'Need five distinct relaxed plans from fresh processes: records={len(relaxed)},processes={len(fresh)},plans={len(distinct_plans)}')
    return result,len(relaxed)>=5 and len(fresh)>=5 and len(distinct_plans)>=5


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--medium',type=Path,nargs='+',default=[HERE/'results/assignment36'])
    parser.add_argument('--quick',type=Path,nargs='+',default=[HERE/'results/assignment-quick18'])
    parser.add_argument('--quick-tiny',type=Path,default=HERE/'results/assignment-quick-extra')
    parser.add_argument('--verification',type=Path,default=HERE/'results/assignment-verification')
    parser.add_argument('--baseline',type=Path,default=BASELINE)
    parser.add_argument('--out',type=Path,default=HERE/'results/COMPLETION.json')
    parser.add_argument('--allow-incomplete',action='store_true')
    parser.add_argument('--append-notes',action='store_true')
    args=parser.parse_args()
    expected=paths(HERE/'benchmark36.txt');quick_expected=paths(HERE/'quick18.txt');development=set(paths(HERE/'development6.txt'))
    raw,source=read_rows(args.medium);qraw,qsource=read_rows(args.quick);eraw,esource=read_rows([args.quick_tiny])
    medium,missing=select(raw,expected,args.allow_incomplete)
    quick,qmissing=select(qraw,quick_expected,args.allow_incomplete)
    extra,emissing=select(eraw,[EXTRA_TINY],args.allow_incomplete)
    baseline,bsource=read_rows([args.baseline])
    if len(baseline)!=48:
        raise ValueError(f'Frozen baseline coverage changed: expected48 unique map rows, got{len(baseline)}')
    if set(expected)-set(baseline):raise ValueError('Frozen baseline misses selected36 input paths')
    groups=group_rows(medium,quick,extra,development)
    tables={'assignment/'+name:completion.table(keyed(rows))for name,rows in groups.items()}
    baseline_groups={'all48':baseline}
    for name in ('all36','medium12','f70','f80','development6','frozen_holdout30'):
        baseline_groups[name]={path:baseline[path]for path in groups[name]}
        tables['c5-securing/'+name]=completion.table(keyed(baseline_groups[name]))
    tables['c5-securing/all48']=completion.table(keyed(baseline))
    preservation=quick_preservation(quick)
    manifest=HERE/'assignment-frozen-hashes.json'
    if not manifest.exists() and not args.allow_incomplete:
        raise ValueError('Missing assignment-frozen-hashes.json for final aggregation')
    manifest_hash=sha(manifest)if manifest.exists()else None
    for row in list(raw.values())+list(qraw.values())+list(eraw.values()):
        if manifest_hash is not None and row.get('source_manifest_sha256')!=manifest_hash:
            raise ValueError(f'Row does not match current assignment freeze: {row["instance_id"]}')
    all_rows=dict(medium,**quick,**extra)
    rechecks,audit_complete=audits(args.verification,all_rows,args.allow_incomplete)
    diagnostics={path:elastic_diagnostics(row)for path,row in medium.items()}
    frequencies={f'L{i}':Counter()for i in range(6)}
    per_map=[];safe_ashore=[]
    for path,row in medium.items():
        complete=row.get('completed');partial=row.get('best_safe_partial')
        if complete:frequencies[f'L{complete["level"]}'].update({rule['rule_id']for rule in complete['rules_broken']})
        if partial:safe_ashore.append(len(partial['boxes_left_ashore']))
        per_map.append(dict(input=path,instance_id=row['instance_id'],rung=rung(path),development=path in development,
            completed_at=completion.classify(row),completed_level=complete['level']if complete else None,
            boxes_ashore=len(complete['boxes_left_ashore'])if complete else None,
            safe_partial_percent_aboard=partial['boxes_aboard_percent']if partial else None,
            wall_s=row.get('wall_s'),baseline_L0=baseline[path]['passed'],
            elastic=diagnostics[path],slack_summary=slack_summary(diagnostics[path]),source=source.get(path)))
    baseline_replay=sum(row.get('passed')is True or row.get('method_metadata',{}).get('replay_legal')is True for row in baseline.values())
    result=dict(experiment='assignment LP ladder',contract='D242',
        complete=not missing and not qmissing and not emissing and audit_complete,
        missing=dict(medium=missing,quick18=qmissing,extra_tiny=emissing),
        scope='Only T5/L0 is a full checker pass. Partial cargo is never completed. Pages stopped5of36 excluded.',
        tables=tables,baseline_evidence=dict(directory=str(args.baseline),map_rows=len(baseline),
            L0_passes=sum(row.get('passed')is True for row in baseline.values()),
            replay_legal_complete=baseline_replay,
            discrepancy='48 map rows exist; NOTES47 means complete replay-legal candidates, not missing results. sim.medium.185100210 is no_candidates.'),
        paired_comparison={name:compare(groups[name],baseline)for name in ('all36','medium12','f70','f80','development6','frozen_holdout30')},
        runtime={name:runtime(rows)for name,rows in groups.items()},quick_preservation=preservation,
        mean_boxes_ashore_safe_L3=mean(safe_ashore)if safe_ashore else None,
        completed_rule_map_frequencies={key:dict(value)for key,value in frequencies.items()},
        slack_summary=slack_summary([d for ds in diagnostics.values()for d in ds]),
        audits=rechecks,audit_relaxed_fresh_complete=audit_complete,maps=per_map,
        development_inputs=sorted(development),sources=dict(assignment_manifest_sha256=manifest_hash,
            leader_completion_sha256=sha(LEADER_TOOL),baseline_rows=bsource,quick_rows=qsource,extra_rows=esource))
    md='# Assignment LP ladder: measured completion\n\n'+result['scope']+'\n\n'+completion.markdown(tables)+'\n\n'
    md+='Coverage '+('complete'if result['complete']else 'INCOMPLETE')+'; five fresh relaxed-plan audits '+str(audit_complete)+'.\n\n'
    md+='Cold baseline:48 map rows,47 replay-legal complete candidates,28 L0 passes; all36 selected inputs covered. The historical NOTES47 is a replay gate, not row coverage.\n\n'
    md+='Same-map comparisons and wall seconds: '+json.dumps(result['paired_comparison'],sort_keys=True)+'. Timings are descriptive across different work/load.\n\n'
    md+='LP slack diagnostics are row proxies; unchanged checker breaches are stored separately in each result. Summary: '+json.dumps(result['slack_summary'],sort_keys=True)+'.\n\n'
    md+='Quick preservation (original18 only): '+json.dumps(preservation,sort_keys=True)+'. Extra tiny is included in quick19 completion.\n\n'
    md+='Safe L3 partial mean boxes ashore: '+str(result['mean_boxes_ashore_safe_L3'])+'. Broken-rule frequencies by completed level: '+json.dumps(result['completed_rule_map_frequencies'],sort_keys=True)+'.\n\n'
    md+='Six development inputs:\n\n'+'\n'.join('- '+path for path in sorted(development))+'\n'
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(tables,indent=2,sort_keys=True)+'\n')
    (HERE/'ASSIGNMENT-MEASURED.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    (HERE/'ASSIGNMENT-MEASURED.md').write_text(md)
    if args.append_notes:
        with (HERE/'NOTES.md').open('a')as stream:stream.write('\n\n'+md)
    print(completion.markdown(tables))


if __name__=='__main__':main()
