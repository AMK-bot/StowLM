"""Manifest-bound reports; checked incumbents only, no solver/native imports."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import importlib.util
import io
import json
import math
from pathlib import Path
from statistics import mean
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
DEV_INDICES = {0, 4, 8, 13, 17, 21}


def kernel():
    name = '_c6_multiport_worker_kernel'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE/'run.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def load_manifest(*, phase='dev', limit=None):
    run = kernel()
    if phase not in ('dev','frozen') or phase == 'frozen' and limit is not None:
        raise ValueError('Frozen phase always selects all32; --limit is a development smoke option')
    manifest = json.loads((HERE/'cohort-manifest.json').read_text())
    if (manifest.get('split'), manifest.get('cohort')) != ('TRAIN', 'c6-multiport-mixed-v1'):
        raise ValueError('Wrong cohort identity')
    cells = [(v, p, f, 0) for v in ('feeder_500', 'feeder_800')
             for p in (3, 4, 5, 6) for f in (60, 70, 80)]
    cells += [(v, p, 80, 1) for v in ('feeder_500', 'feeder_800') for p in (3, 4, 5, 6)]
    seen, rows = set(), manifest['maps']
    for entry in rows:
        index = entry['seed'] - 263360000
        if index not in range(32) or index in seen:
            raise ValueError('Unknown/duplicate frozen seed')
        seen.add(index)
        if (entry['vessel'], entry['ports'], entry['fill_pct'], entry['repeat']) != cells[index]:
            raise ValueError('Changed cohort stratum')
        if entry['size'] != 'medium' or entry['development'] is not (index in DEV_INDICES):
            raise ValueError('Changed fixed development membership')
        name = f"medium.{entry['vessel']}.p{entry['ports']}.f{entry['fill_pct']}.s{entry['seed']}.json"
        path = (ROOT/entry['path']).resolve()
        if path != (HERE/'cohort'/name).resolve() or run.digest(path) != entry['sha256']:
            raise ValueError('Changed input bytes/path')
        payload = json.loads(path.read_text())
        if payload['id'] != entry['instance_id'] or payload['seed'] != entry['seed']:
            raise ValueError('Input identity mismatch')
        if len(payload['load_list']) != entry['n_boxes'] or payload['provenance'].get('tainted'):
            raise ValueError('Changed load inventory or provenance')
        if len(entry['by_pod']) != entry['ports'] or not entry['fill_pct']/100-.02-1e-9 <= entry['fill'] <= entry['fill_pct']/100+1e-9:
            raise ValueError('Invalid POD/fill stratum')
        generation = (ROOT/entry['generation_path']).resolve()
        if generation != path.with_suffix('.generation.json') or run.digest(generation) != entry['generation_sha256']:
            raise ValueError('Changed generation evidence')
    for name, digest in manifest['source_sha256'].items():
        path = (ROOT/name).resolve()
        if not (path.is_relative_to(ROOT/'src') or path.is_relative_to(ROOT/'params') or path == HERE/'cohort.py'):
            raise ValueError('Unapproved generator provenance path')
        if run.digest(path) != digest:
            raise ValueError('Changed generator provenance source')
    selected = sorted((r for r in rows if phase == 'frozen' or r['development']), key=lambda r:r['seed'])
    if phase == 'frozen':
        if seen != set(range(32)) or manifest.get('complete') is not True:
            raise ValueError('Frozen phase requires complete32 cohort')
        frozen = json.loads((HERE/'FREEZE.json').read_text())
        for arm in run.ARMS:
            if frozen['bindings'][arm] != run.bindings(arm):
                raise ValueError('Frozen runtime/source/cohort bindings changed')
    elif limit is None and {r['seed']-263360000 for r in selected} != DEV_INDICES:
        raise ValueError('Development phase requires fixed6 maps, or explicit smoke --limit')
    if limit is not None:
        if limit < 1:
            raise ValueError('Limit must be positive')
        selected = selected[:limit]
    if not selected:
        raise ValueError('No selected manifest rows')
    return selected


def configurations(entries, arms):
    run = kernel()
    configs = {(e['instance_id'], a):run.config_for(e, a) for e in entries for a in arms}
    common = ('native_sha256', 'params_sha256', 'cohort_sha256', 'policy')
    if len({json.dumps({k:c[k] for k in common}, sort_keys=True) for c in configs.values()}) != 1:
        raise ValueError('Comparison source/policy bindings differ')
    for arm in arms:
        if len({c['implementation_sha256'] for (identity,a),c in configs.items() if a == arm}) != 1:
            raise ValueError('An arm implementation changed while binding the cohort')
    return configs


def validate_row(row, entry, arm, config, *, require_complete=True):
    run = kernel()
    expected = dict(instance_id=entry['instance_id'], input=entry['path'], input_sha256=entry['sha256'],
        size=entry['size'], ports=entry['ports'], vessel=entry['vessel'], fill_pct=entry['fill_pct'],
        actual_fill=entry['fill'], n_boxes=entry['n_boxes'], development=entry['development'], arm=arm, config=config,
        run_key=run.run_key(config, entry['sha256']), cold=True)
    if any(row.get(k) != v for k,v in expected.items()):
        raise ValueError('Mismatched row identity/config/run key')
    if require_complete and row.get('complete') is not True:
        raise ValueError('Incomplete final row')
    if require_complete and row.get('outer_timeout') and row.get('resource_policy') != run.POLICY:
        raise ValueError('Timeout row lacks owned-controller resource-policy evidence')
    if type(row.get('passed')) is not bool or not isinstance(row.get('wall_s'), (int,float)) or not math.isfinite(row['wall_s']):
        raise ValueError('Missing verdict or finite elapsed time')
    if row.get('completed'):
        from frozen_reporting.tiers import analyze_report
        analysis = analyze_report(row.get('report'))
        completed = row['completed']
        loads = json.loads((ROOT/entry['path']).read_text())['load_list']
        if (not row.get('plan') or not isinstance(row.get('assignment'), dict)
            or not set(loads) <= set(row['assignment']) or analysis['ashore_count'] != 0
            or not analysis['eligible'] or completed['level'] != analysis['level']
            or completed['analysis'] != analysis or row['passed'] != analysis['passed']
            or completed.get('column') != ('T5','T4','T3','T2','T1','T0 only')[analysis['level']]
            or row.get('lifts') != analysis['lifts']):
            raise ValueError('Completed row lacks consistent concrete all-aboard checked evidence')
        if row['passed'] != (completed['level'] == 0):
            raise ValueError('T5/full-pass disagreement')
        count = row['report']['count']
        expected_parts = dict(relocations=count['relocations'],hatch_now=count['hatch_cover_lifts'],
            hatch_later=sum(p['hatch_cover_lifts'] for p in count['future']),restow_now=count['restow_lifts'],
            restow_later=sum(p['restow_lifts'] for p in count['future']))
        if row.get('parts') != expected_parts:
            raise ValueError('Lift components disagree with saved native report')
    elif row['passed'] or row.get('plan') or row.get('report') or row.get('lifts') is not None:
        raise ValueError('Pass/plan without completed checked evidence')
    return row


def read_row(output, entry, arm, config, *, optional=False):
    path = Path(output)/arm/f"{entry['instance_id']}.json"
    if optional and not path.exists():
        return None
    return validate_row(json.loads(path.read_text()), entry, arm, config)


def _average(rows, key):
    values = [r[key] for r in rows if isinstance(r.get(key), (int,float))]
    return mean(values) if values else None


def _timed_row(output, row):
    """Prefer externally registered launch/reap time; preserve Capture timing."""
    result = dict(row,capture_wall_s=row['wall_s'],timing_source='Capture clock; no matching external ledger')
    if row.get('outer_timeout'):
        result.update(wall_s=kernel().CAP_S,timing_source='declared censored whole-arm/map cap')
        return result
    marker = row.get('capture_started_epoch') or (row.get('completed') or {}).get('checked_epoch')
    candidates = []
    paths = list((Path(output)/'pids').glob('worker-*.json'))
    paths += list((Path(output)/'pids').glob('manual-worker-*.json'))
    for path in paths:
        ledger = json.loads(path.read_text())
        if (ledger.get('run_key') == row['run_key'] and ledger.get('instance_id') == row['instance_id']
            and ledger.get('arm') == row['arm'] and ledger.get('returncode') == 0
            and ledger.get('reason') is None and marker is not None
            and ledger['started'] <= marker <= ledger.get('reaped',-1)):
            candidates.append(ledger)
    if len(candidates) > 1:
        raise ValueError('Ambiguous external timing ledger for completed row')
    if candidates:
        ledger = candidates[0]
        elapsed = ledger['reaped']-ledger['started']
        if not 0 <= elapsed <= kernel().CAP_S:
            raise ValueError('Normal row external time exceeds declared cap')
        result.update(wall_s=elapsed,timing_source='registered worker launch to observed reap',
                      worker_pid=ledger['pid'])
    return result


def summarize(output, *, entries=None, arms=None, configs=None, phase='dev', limit=None):
    from frozen_reporting.completion import table
    run = kernel()
    output = Path(output)
    entries = entries or load_manifest(phase=phase, limit=limit)
    arms = tuple(arms or run.ARMS)
    if phase == 'frozen' and set(arms) != set(run.ARMS):
        raise ValueError('Frozen reporting requires both configured arms')
    configs = configs or configurations(entries, arms)
    rows, per_map = {}, []
    for entry in entries:
        for arm in arms:
            row = read_row(output, entry, arm, configs[entry['instance_id'],arm], optional=True)
            if row:
                row = _timed_row(output,row)
                rows[entry['instance_id'],arm] = row
                completed = row.get('completed')
                analysis = (completed or {}).get('analysis') or {}
                evidence_kind = 'completed_checked_plan' if completed else 'no_selected_checked_plan'
                if not completed and row.get('best_ineligible'):
                    analysis = row['best_ineligible'].get('analysis') or {}
                    evidence_kind = 'ineligible_candidate_diagnostic_only'
                per_map.append(dict(instance_id=entry['instance_id'], arm=arm, vessel=entry['vessel'],
                    ports=entry['ports'], fill_pct=entry['fill_pct'], actual_fill=entry['fill'],
                    n_boxes=entry['n_boxes'], development=entry['development'],
                    passed=row['passed'], tier=(row.get('completed') or {}).get('column'), lifts=row.get('lifts'),
                    **{k:(row.get('parts') or {}).get(k) for k in ('relocations','hatch_now','hatch_later','restow_now','restow_later')},
                    wall_s=row['wall_s'], capture_wall_s=row['capture_wall_s'], timing_source=row['timing_source'],
                    outer_timeout=bool(row.get('outer_timeout')),
                    evidence_kind=evidence_kind,
                    diagnostic_reason='; '.join(analysis.get('ineligible_reasons',[])) or None,
                    hard_rules=analysis.get('broken_rule_ids', []), breaches=analysis.get('broken_rules', []),
                    total_breach=analysis.get('total_breach'), unknown_breaches=analysis.get('unmeasured_occurrences'),
                    failure_reason=row.get('failure_reason')))
    groups = {}
    for dev in (True, False):
        selected = [e for e in entries if e['development'] is dev]
        if not selected:
            continue
        name = 'development' if dev else 'nondevelopment'
        by_arm, strata, paired, port_fill = {}, {}, {}, {}
        for arm in arms:
            rs = {e['instance_id']:rows[e['instance_id'],arm] for e in selected if (e['instance_id'],arm) in rows}
            by_arm[arm] = dict(table(rs), expected_maps=len(selected), pending=len(selected)-len(rs),
                outer_timeouts=sum(bool(r.get('outer_timeout')) for r in rs.values()))
        for cell in sorted({(e['vessel'],e['ports'],e['fill_pct']) for e in selected}):
            ids = [e['instance_id'] for e in selected if (e['vessel'],e['ports'],e['fill_pct']) == cell]
            label = f'{cell[0]}/p{cell[1]}/f{cell[2]}'
            strata[label] = {}
            both = [i for i in ids if len(arms) == 2 and all((i,a) in rows and rows[i,a]['passed'] for a in arms)]
            paired[label] = dict(paired_T5_maps=len(both), arms={})
            for arm in arms:
                rs = [rows[i,arm] for i in ids if (i,arm) in rows]
                passed = [r for r in rs if r['passed']]
                matched = [rows[i,arm] for i in both]
                stats = lambda values: dict(mean_lifts=_average(values,'lifts'), mean_wall_s=_average(values,'wall_s'),
                    mean_restow_lifts=mean((r.get('parts') or {}).get('restow_now',0)+(r.get('parts') or {}).get('restow_later',0) for r in values) if values else None)
                strata[label][arm] = dict(expected_maps=len(ids), measured=len(rs), T5_passes=len(passed),
                    own_T5_pass_means=stats(passed))
                paired[label]['arms'][arm] = stats(matched)
        for ports, fill in sorted({(e['ports'],e['fill_pct']) for e in selected}):
            ids = [e['instance_id'] for e in selected if (e['ports'],e['fill_pct']) == (ports,fill)]
            port_fill[f'p{ports}/f{fill}'] = {arm:dict(expected_maps=len(ids),
                measured=sum((i,arm) in rows for i in ids),
                T5_passes=sum((i,arm) in rows and rows[i,arm]['passed'] for i in ids)) for arm in arms}
        groups[name] = dict(by_arm=by_arm, by_ports_fill_counts=port_fill,
            by_vessel_ports_fill=strata, paired_T5_within_vessel_ports_fill=paired)
    result = dict(phase=phase, expected_jobs=len(entries)*len(arms), completed_jobs=len(rows), groups=groups,
        policy=run.POLICY, pooling='No lift means across vessels, port counts or fill strata; own-pass and paired-pass means distinct')
    result['timing'] = 'External launch-to-observed-reap seconds where registered; outer timeouts censored at600; Capture fallback explicitly labeled per map'
    result['diagnostics'] = 'Ineligible-candidate hard rules/breaches are diagnostic only; they never count as completion, tier or lift evidence'
    run.write_json(output/'summary.json', result)
    fields = ['instance_id','arm','vessel','ports','fill_pct','actual_fill','n_boxes','development','passed','tier','lifts',
              'relocations','hatch_now','hatch_later','restow_now','restow_later','wall_s','capture_wall_s','timing_source','outer_timeout',
              'evidence_kind','diagnostic_reason','hard_rules','breaches','total_breach','unknown_breaches','failure_reason']
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in per_map:
        writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    _text(output/'per-map.csv', stream.getvalue())
    lines = [f'# {phase} results', '', result['pooling'], '', result['diagnostics'], '']
    for name, group in groups.items():
        lines += [f'## {name}', '', '| Arm | Measured / expected | T5 | T4 | T3 | T2 | T1 | T0 only | Not completed | Timeouts |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for arm, row in group['by_arm'].items():
            lines.append(f"| {arm} | {row['maps']} / {row['expected_maps']} | "+' | '.join(str(v) for v in row['counts'].values())+f" | {row['not_completed']} | {row['outer_timeouts']} |")
        lines += ['', 'Own T5 pass means within each vessel / port count / requested fill cell:', '',
                  '| Cell | Arm | T5 passes / measured / expected | Mean lifts | Mean restow lifts | Mean seconds |',
                  '|---|---|---:|---:|---:|---:|']
        for cell, arms_at_cell in group['by_vessel_ports_fill'].items():
            for arm, stats in arms_at_cell.items():
                avg = stats['own_T5_pass_means']
                lines.append(f"| {cell} | {arm} | {stats['T5_passes']} / {stats['measured']} / {stats['expected_maps']} | {avg['mean_lifts']} | {avg['mean_restow_lifts']} | {avg['mean_wall_s']} |")
        lines += ['', 'Paired T5 means require both arms to pass the same map within the same cell:', '',
                  '| Cell | Paired maps | Arm | Mean lifts | Mean restow lifts | Mean seconds |',
                  '|---|---:|---|---:|---:|---:|']
        for cell, pairs in group['paired_T5_within_vessel_ports_fill'].items():
            for arm, avg in pairs['arms'].items():
                lines.append(f"| {cell} | {pairs['paired_T5_maps']} | {arm} | {avg['mean_lifts']} | {avg['mean_restow_lifts']} | {avg['mean_wall_s']} |")
    lines += ['', '## Per map', '', '| Map | Arm | Vessel / ports / fill | T5 | Tier | Lifts | Restow lifts | Seconds | Evidence kind | Hard rules / breaches |',
              '|---|---|---|---:|---|---:|---:|---:|---|---|']
    for row in per_map:
        restows = (row['restow_now'] or 0)+(row['restow_later'] or 0) if row['lifts'] is not None else None
        evidence = json.dumps(row['breaches'], sort_keys=True).replace('|','\\|')
        lines.append(f"| {row['instance_id']} | {row['arm']} | {row['vessel']} / {row['ports']} / {row['fill_pct']} | {int(row['passed'])} | {row['tier'] or '—'} | {row['lifts']} | {restows} | {row['wall_s']:.2f} | {row['evidence_kind']} | {evidence} |")
    _text(output/'report.md', '\n'.join(lines)+'\n')
    return result


def _text(path, text):
    import os
    temporary = path.with_name(path.name+f'.{os.getpid()}.tmp')
    temporary.write_text(text)
    temporary.replace(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='results/dev')
    parser.add_argument('--phase', choices=('dev','frozen'), default='dev')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--arms', nargs='+', choices=('engine12','port_lp'))
    args = parser.parse_args()
    output = (HERE/args.out).resolve()
    output.relative_to(HERE)
    output.mkdir(parents=True,exist_ok=True)
    print(json.dumps(summarize(output, phase=args.phase, limit=args.limit, arms=args.arms),indent=2))
