"""Strict paired checker and exact leader completion tables; no numerical solve."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LABELS = {'frontier09':'Fresh 0.9', 'engine':'Fresh frozen engine',
          'pages':'Fresh frozen pages', 'layover':'Cold arrival LP',
          'stored-frontier09':'Stored C4 0.9', 'stored-engine':'Stored C4 engine',
          'stored-layover':'Stored C4 pages'}


def load(name):
    spec = importlib.util.spec_from_file_location('_c6_report_'+name, HERE/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def avg(values):
    values = list(values)
    return mean(values) if values else None


def fmt(value):
    return '—' if value is None else f'{value:.2f}'.rstrip('0').rstrip('.')


def aggregate(rows, records, arms):
    index = {(r['instance_id'],r['method']):r for r in rows}
    groups = []
    for size in ('tiny','small','medium'):
        for occupancy in (.3,.5):
            ids = [Path(r['path']).stem for r in records if r['size']==size and r['occupancy_target']==occupancy]
            group = dict(size=size, occupancy=occupancy, maps=len(ids), methods={}, pairs={})
            for arm in arms:
                items = [index[(identity,arm)] for identity in ids if (identity,arm) in index]
                passes = [r for r in items if r['passed']]
                group['methods'][arm] = dict(maps=len(items), passes=len(passes),
                    replay_complete=sum(r.get('replay_complete') is True for r in items),
                    replay_unavailable=sum(r.get('replay_complete') is None for r in items),
                    mean_lifts=avg(r['lifts'] for r in passes),
                    mean_wall_s=avg(r['wall_s'] for r in items),
                    mean_arrival_now=avg(r['aboard_restows']['now_lifts'] for r in passes),
                    mean_arrival_later=(avg(r['aboard_restows']['future_lifts'] for r in passes)
                        if all(r['aboard_restows']['future_lifts'] is not None for r in passes) else None),
                    mean_restow_now=avg(r['parts']['restow_now'] for r in passes),
                    mean_restow_later=avg(r['parts']['restow_later'] for r in passes))
            if 'layover' in arms:
                for arm in arms:
                    if arm=='layover':
                        continue
                    pairs = [(index[(identity,arm)],index[(identity,'layover')]) for identity in ids
                        if all((identity,a) in index and index[(identity,a)]['passed'] for a in (arm,'layover'))]
                    group['pairs'][arm] = dict(maps=len(pairs), baseline_lifts=avg(a['lifts'] for a,b in pairs),
                                               method_lifts=avg(b['lifts'] for a,b in pairs))
            groups.append(group)
    return dict(groups=groups, rows=len(rows), arms=arms)


def validate(rows, records, arms, runtime, *, allow_partial=False):
    expected_seeds = set(range(331401000,331401072))
    if {r['seed'] for r in records} != expected_seeds or len(records)!=72:
        raise ValueError('Requires original 72 predeclared seeds')
    by_id = {Path(r['path']).stem:r for r in records}
    expected = {(identity,arm) for identity in by_id for arm in arms}
    seen, configs, profiles = set(), {}, {}
    sources = {arm:runtime.provenance(arm) for arm in arms}
    for row in rows:
        key = (row['instance_id'],row['method'])
        if key not in expected or key in seen:
            raise ValueError(f'Duplicate or foreign row: {key}')
        seen.add(key)
        record = by_id[key[0]]
        digest = hashlib.sha256(runtime.input_path(record).read_bytes()).hexdigest()
        if row.get('input_sha256')!=digest or record['sha256']!=digest:
            raise ValueError(f'Input hash mismatch: {key}')
        if row.get('source_sha256')!=sources[key[1]]:
            raise ValueError(f'Stale source provenance: {key}')
        if row.get('complete') is not True or row.get('cold') is not True:
            raise ValueError(f'Not completed atomic cold invocation: {key}')
        if row.get('size')!=record['size'] or row.get('occupancy')!=record['occupancy_target']:
            raise ValueError(f'Cell mismatch: {key}')
        config = json.dumps(row['config'],sort_keys=True)
        if key[1] in configs and configs[key[1]]!=config:
            raise ValueError(f'Mixed budgets within arm: {key[1]}')
        configs[key[1]]=config
        profile = row.get('budget_profile_sha256')
        if key[1] in profiles and profiles[key[1]]!=profile:
            raise ValueError(f'Mixed profiles: {key[1]}')
        profiles[key[1]]=profile
        if row.get('passed') and (not row.get('plan') or row.get('lifts') is None or row.get('replay_complete') is not True):
            raise ValueError(f'Pass missing independent checked complete plan: {key}')
        if row.get('completed') and (row.get('replay_complete') is not True or row.get('boxes_left_ashore')):
            raise ValueError(f'Partial falsely marked completed: {key}')
    if not allow_partial and seen!=expected:
        raise ValueError(f'Missing primary rows: {len(seen)}/{len(expected)}')
    return dict(expected_rows=len(expected), rows=len(seen), complete=seen==expected, configs=configs, profiles=profiles)


def subset_completions(rows, arms, dev_seeds, records, completion_module):
    """Separate tuning inputs from remaining TRAIN without calling either unseen."""
    development_ids={Path(record['path']).stem for record in records if record['seed'] in set(dev_seeds)}
    groups={}
    for label,development in (('DEV12',True),('Remaining TRAIN60',False)):
        for arm in arms:
            selected={row['instance_id']:row for row in rows
                if row['method']==arm and (row['instance_id'] in development_ids)==development}
            groups[f'{label}/{LABELS.get(arm,arm)}']=completion_module.table(selected)
    return groups


def replay_diagnostics(rows, records, arms):
    """Costs of complete replay witnesses rejected by the ordinary full gate."""
    results=[]
    for size in ('tiny','small','medium'):
        for occupancy in (.3,.5):
            for arm in arms:
                selected=[row for row in rows if row['size']==size and row['occupancy']==occupancy
                    and row['method']==arm and row.get('replay_complete') is True and not row['passed']]
                costs=[row for row in selected if row.get('diagnostic_lifts') is not None]
                levels={str(level):sum((row.get('completed') or {}).get('level')==level for row in selected)
                        for level in range(6)}
                results.append(dict(size=size,occupancy=occupancy,method=arm,maps=len(selected),
                    cost_maps=len(costs),completion_levels=levels,
                    no_tier_completion=sum(not row.get('completed') for row in selected),
                    mean_diagnostic_lifts=avg(row['diagnostic_lifts'] for row in costs),
                    mean_arrival_now=(avg(row['aboard_restows']['now_lifts'] for row in costs)
                        if all(row.get('aboard_restows',{}).get('now_lifts') is not None for row in costs) else None),
                    mean_arrival_later=(avg(row['aboard_restows']['future_lifts'] for row in costs)
                        if all(row.get('aboard_restows',{}).get('future_lifts') is not None for row in costs) else None),
                    mean_wall_s=avg(row['wall_s'] for row in selected)))
    return results


def render_diagnostics(diagnostics):
    lines=['| Size | Occupancy | Arm | Complete replay, non-T5 | Cost available | Completed L0/L1/L2/L3/L4/L5; none | Diagnostic lifts | Arrival restows now/later | Mean seconds/map |',
           '|---|---:|---|---:|---:|---|---:|---:|---:|']
    for row in diagnostics:
        counts='/'.join(str(row['completion_levels'][str(level)]) for level in range(6))
        lines.append(f"| {row['size']} | {100*row['occupancy']:.0f}% | {LABELS.get(row['method'],row['method'])} | "
            f"{row['maps']} | {row['cost_maps']} | {counts}; {row['no_tier_completion']} | "
            f"{fmt(row['mean_diagnostic_lifts'])} | {fmt(row['mean_arrival_now'])}/{fmt(row['mean_arrival_later'])} | "
            f"{fmt(row['mean_wall_s'])} |")
    return '\n'.join(lines)


def render(summary, completion, subset_completion=None, diagnostic_table=None):
    lines = ['# Cycle 6 cold layover comparison', '',
        'Same 72 TRAIN synthetic Stage B snapshots and seeds as Cycle 4; twelve maps per cell. '
        'Occupancy targets 30%/50% (actual tiny 33.3%/50%, small 29.4%/50%, medium 30%/50%). '
        'Stored C4 arms are historical references, not fresh speed comparisons. All other arms run cold. '
        'A pass requires the unchanged full checker and independently complete legal replay. '
        'Replay reports independently returned complete plans / unavailable gates. '
        'An as-is arm returning no finished plan may discard internal stows, so unavailable is not proof '
        'that it constructed no complete stow. '
        'Lifts/restows average full passes only; later lifts use fixed-stow D15 with no later loads. '
        'Wall seconds average all invocations including failures.', '',
        '| Size | Occupancy | Arm | Maps | Replay returned/unavailable | Passes | Mean lifts | Arrival restows now/later | All restows now/later | Mean seconds/map |',
        '|---|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for group in summary['groups']:
        for arm,r in group['methods'].items():
            lines.append(f"| {group['size']} | {100*group['occupancy']:.0f}% | {LABELS.get(arm,arm)} | {r['maps']} | "
                f"{r['replay_complete']}/{r['replay_unavailable']} | {r['passes']}/{group['maps']} | {fmt(r['mean_lifts'])} | "
                f"{fmt(r['mean_arrival_now'])}/{fmt(r['mean_arrival_later'])} | "
                f"{fmt(r['mean_restow_now'])}/{fmt(r['mean_restow_later'])} | {fmt(r['mean_wall_s'])} |")
    if summary.get('stored_pages_only'):
        lines += ['', 'Archived C4 Pages is the accepted original cold run at its unchanged recorded construction '
            'and finishing work counts. It is a paired historical cost reference; its recorded time ran at a '
            'different machine load and supports no fresh speed claim. Original failed full reports were '
            'discarded: non-T5 completion tiers and exact breaches are **unavailable**, not zero and not partial. '
            'The completion and non-T5 diagnostic tables below cover fresh arms only. Archived row bytes, '
            'input hashes, native checker sources and parameters are verified against the formal freeze.']
    lines += ['', '## Completion by tier', '',
        'Exact local copy of leader `completion.py`; counts are exclusive first completed levels. '
        'Only hard checker violations determine required peeling. Unknown IDs and failed checker parts fail closed. '
        'Partial means a required new/retained box remains ashore and never counts as completed.', '', completion,
        '', '## Predeclared development split', '',
        'DEV12 contains the first two seeds in each size/occupancy cell, fixed in `DEV.json` before tuning. '
        'The remaining sixty maps are TRAIN data, not sealed or unseen. '
        'The main 72-map table above includes both groups. The same unchanged leader completion function '
        'classifies both groups below; counts are exclusive first completed levels.', '',
        subset_completion or 'No split table supplied.',
        '', '## Non-T5 complete replay diagnostics', '',
        'These plans complete a legal cargo replay but fail the full checker. '
        'Their diagnostic costs are not feasible upper bounds or mutual-pass cost comparisons. '
        'The main lift/restow table remains restricted to full passes. Costs/restows average rows with an '
        'available selected concrete witness; historical rows may expose only a replay flag and no selected '
        'cost or tier, shown separately. Any unknown arrival-later attribution leaves the group mean unknown. '
        'Wall time includes the entire invocation for each selected non-T5 complete replay row.', '',
        diagnostic_table or 'No diagnostic table supplied.',
        '', '## Mutual full-checker passes', '', '| Size | Occupancy | Comparator | Pairs | Comparator lifts | LP lifts |',
        '|---|---:|---|---:|---:|---:|']
    for group in summary['groups']:
        for arm,p in group['pairs'].items():
            lines.append(f"| {group['size']} | {100*group['occupancy']:.0f}% | {LABELS.get(arm,arm)} | {p['maps']} | "
                         f"{fmt(p['baseline_lifts'])} | {fmt(p['method_lifts'])} |")
    return '\n'.join(lines)+'\n'


def validate_archived_pages(rows, paths, records, frozen, runtime):
    """Audit accepted archived costs without rerunning or inventing failed reports."""
    pinned=frozen.get('archived_pages')
    if not pinned:
        raise ValueError('Freeze does not pin archived Pages rows')
    expected={Path(record['path']).name:record for record in records}
    if set(paths)!=set(expected) or set(pinned['rows_sha256'])!=set(expected):
        raise ValueError('Archived Pages rows do not match all 72 cohort filenames')
    native={key:value for key,value in runtime.provenance('engine').items()
            if key.startswith('src/stowage/') or key.startswith('params/')}
    if native!=pinned['native_source_sha256']:
        raise ValueError('Native checker/parameter sources changed since freeze')
    page_profile=json.loads((HERE/'pages_copy/PROFILE.json').read_text())
    expected_config=dict(budget_s=60.,work_config=page_profile['work_config'],
        finishing_config=page_profile['finishing_config'],**page_profile['native_settings'])
    for name,row in rows.items():
        record=expected[name]
        if hashlib.sha256(paths[name].read_bytes()).hexdigest()!=pinned['rows_sha256'][name]:
            raise ValueError(f'Archived Pages row changed since freeze: {name}')
        input_digest=hashlib.sha256(runtime.input_path(record).read_bytes()).hexdigest()
        if (row.get('input_sha256')!=record['sha256'] or input_digest!=record['sha256']
                or frozen['input_sha256'][str(record['seed'])]!=input_digest
                or row.get('input')!=record['path'] or row.get('instance_id')!=Path(name).stem):
            raise ValueError(f'Archived Pages input mismatch: {name}')
        if row.get('size')!=record['size'] or row.get('occupancy')!=record['occupancy_target']:
            raise ValueError(f'Archived Pages cell mismatch: {name}')
        if row.get('config')!=expected_config:
            raise ValueError(f'Archived Pages work profile differs: {name}')
        old_native={key:value for key,value in row['source_sha256'].items()
                    if key.startswith('src/stowage/') or key.startswith('params/')}
        if old_native!=native:
            raise ValueError(f'Archived Pages native checker/parameters differ: {name}')
        if row.get('complete') is not True or row.get('method')!='layover':
            raise ValueError(f'Invalid original Pages invocation: {name}')
        if row.get('passed') and (row.get('replay_complete') is not True or not row.get('plan') or row.get('lifts') is None):
            raise ValueError(f'Archived Pages pass lacks its accepted plan: {name}')
    return dict(maps=len(rows),row_bytes_pinned=True,input_hashes_match=True,native_checker_and_params_match=True,
                failed_tier_reports='unavailable',cold='original archived cold invocation')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default='results/primary')
    parser.add_argument('--arms',nargs='+',default=['layover'])
    parser.add_argument('--allow-partial',action='store_true')
    parser.add_argument('--no-stored',action='store_true')
    parser.add_argument('--freeze',default='FREEZE.json')
    parser.add_argument('--stored-pages-only',action='store_true',
                        help='Keep unchanged archived Pages cost reference; completion tables cover fresh arms only')
    args=parser.parse_args()
    source=(HERE/args.out).resolve()
    if not source.is_relative_to(HERE/'results'):
        raise ValueError('Results must remain local')
    records=json.loads((HERE/'cohort/MANIFEST.json').read_text())['maps']
    rows=[json.loads(p.read_text()) for arm in args.arms for p in sorted((source/arm).glob('*.json'))]
    runtime=load('runner')
    validation=validate(rows,records,args.arms,runtime,allow_partial=args.allow_partial)
    frozen=None
    if not args.allow_partial or not args.no_stored:
        frozen=json.loads((HERE/args.freeze).read_text())
    if not args.allow_partial:
        for arm in args.arms:
            if arm not in frozen['arms'] or frozen['source_sha256'][arm]!=runtime.provenance(arm):
                raise ValueError(f'Runtime does not match pre-run freeze: {arm}')
            if validation['profiles'][arm]!=frozen['profile_sha256']:
                raise ValueError(f'Budget does not match pre-run freeze: {arm}')
        by_id={Path(record['path']).stem:record for record in records}
        for row in rows:
            if row.get('arm_order')!=runtime.arm_order(by_id[row['instance_id']],frozen['arms']):
                raise ValueError(f'Arm order differs from frozen requested arms: {row["instance_id"]}')
    fresh_rows=list(rows)
    archived_validation=None
    if not args.no_stored:
        for arm in (('layover',) if args.stored_pages_only else ('frontier09','engine','layover')):
            paths={path.name:path for path in sorted((HERE/'results/stored-cycle4'/arm).glob('*.json'))}
            archived={name:json.loads(path.read_text()) for name,path in paths.items()}
            if args.stored_pages_only:
                archived_validation=validate_archived_pages(archived,paths,records,frozen,runtime)
            for row in archived.values():
                record=next(r for r in records if Path(r['path']).stem==row['instance_id'])
                if row['input_sha256']!=record['sha256']:
                    raise ValueError('Historical row input mismatch')
                row['method']='stored-'+arm
                rows.append(row)
    historical=(['stored-layover'] if args.stored_pages_only else ['stored-frontier09','stored-engine','stored-layover'])
    arms=(historical if not args.no_stored else [])+args.arms
    summary=aggregate(rows,records,arms)
    summary['validation']=validation
    summary['stored_pages_only']=bool(args.stored_pages_only and not args.no_stored)
    summary['archived_pages_validation']=archived_validation
    completion_module=load('completion')
    completion={}
    scoring_arms=args.arms if args.stored_pages_only else arms
    scoring_rows=fresh_rows if args.stored_pages_only else rows
    for group in summary['groups']:
        for arm in scoring_arms:
            selected={r['instance_id']:r for r in scoring_rows if r['method']==arm and r['size']==group['size'] and r['occupancy']==group['occupancy']}
            completion[f"{group['size']}/{100*group['occupancy']:.0f}%/{LABELS.get(arm,arm)}"]=completion_module.table(selected)
    dev_seeds=json.loads((HERE/'DEV.json').read_text())['seeds']
    split=subset_completions(scoring_rows,scoring_arms,dev_seeds,records,completion_module)
    summary['development_split_completion']=split
    diagnostics=replay_diagnostics(scoring_rows,records,scoring_arms)
    summary['non_t5_complete_replay_diagnostics']=diagnostics
    runtime.dump(source/'comparison.json',summary)
    runtime.dump(source/'COMPLETION.json',completion)
    runtime.dump(source/'COMPLETION-SPLIT.json',split)
    runtime.dump(source/'REPLAY-DIAGNOSTICS.json',diagnostics)
    (HERE/'TABLE.md').write_text(render(summary,completion_module.markdown(completion),
                                      completion_module.markdown(split),render_diagnostics(diagnostics)))
    (HERE/'COMPLETION.md').write_text(completion_module.markdown(completion)+'\n')
    (HERE/'COMPLETION-SPLIT.md').write_text(completion_module.markdown(split)+'\n')
    (HERE/'REPLAY-DIAGNOSTICS.md').write_text(render_diagnostics(diagnostics)+'\n')
    print(json.dumps(validation))


if __name__=='__main__':
    main()
