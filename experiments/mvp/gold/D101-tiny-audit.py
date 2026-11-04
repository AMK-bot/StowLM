"""Read-only tiny timeout retry enumeration and first-20 D101 cohort audit."""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
CUTOFF = '2026-10-29T20:40:00+00:00'
EPOCH = 'b57163150dd412a04d399a10e01ebb043e3508bc9b56696ae97e3d06ef5a4fa6'

def records():
    for split in ['train', 'val']:
        for path in (ROOT/split/'tiny').rglob('result.json'):
            row = json.loads(path.read_text())
            row['result'] = str(path.resolve())
            yield row

def selected_fields(row):
    return {k: row.get(k) for k in ['case','result','input_path','input_sha256','started_utc',
            'finished_utc','error','drop_reason','configuration_sha256','runtime_epoch_sha256']}

def retries(rows):
    targets=[]
    excluded=Counter()
    failed=0
    for row in rows:
        if not row.get('completed') or row.get('passed') or (row.get('finished_utc') or '') < CUTOFF:
            continue
        failed += 1
        if row.get('drop_reason') != 'ballast':
            excluded['other_drop_reason'] += 1
            continue
        stage=row.get('stages',{}).get('ballast',{})
        metrics=stage.get('metrics',{})
        error=(row.get('error') or '').lower()
        status=str(metrics.get('ballast_status','')).lower()
        proven=status in ['infeasible','proven_infeasible'] or any(metrics.get(k) is True for k in ['infeasibility_proved','proven_infeasible','infeasible_proved'])
        unproven='infeasibility has not been proved' in error
        timeout='timeout' in error or 'time limit' in error or 'timeout' in str(metrics.get('not_found_cause','')).lower()
        if proven:
            excluded['proven_infeasible'] += 1
            continue
        if not (timeout or (unproven and 'not found' in error)):
            excluded['not_timeout_or_unproven_notfound'] += 1
            continue
        target=selected_fields(row)
        target.update(ballast_status=status,not_found_cause=metrics.get('not_found_cause'),infeasibility_proved=False)
        targets.append(target)
    targets.sort(key=lambda r:(not r['case'].startswith('train/'),r['started_utc'],r['case']))
    return dict(measured_utc=datetime.now(timezone.utc).isoformat(),cutoff_utc=CUTOFF,
                selection='Active tiny rows only; completed failed attempts FINISHED>=20:40Z, final drop_reason=ballast, explicit timeout or unproved not-found. Excludes other-stage failures and positive infeasibility proofs. Train first; no source result edits.',
                failed_completed_since_cutoff=failed,selected=len(targets),
                counts_by_split=dict(Counter(r['case'].split('/')[0] for r in targets)),
                counts_by_ship=dict(Counter(r['case'].split('/')[2] for r in targets)),
                excluded=dict(excluded),requested_rerun=targets)

def first20(rows):
    matching=[r for r in rows if r.get('config',{}).get('ballast_s') == 120
              and r.get('config',{}).get('ballast_v4') is True
              and r.get('runtime_epoch_sha256') == EPOCH]
    configurations=Counter(r.get('configuration_sha256') for r in matching)
    cohorts={}
    for configuration in configurations:
        # Select by start order before excluding pending rows: fast completions cannot replace slow failures.
        cohort=sorted([r for r in matching if r.get('configuration_sha256') == configuration],key=lambda r:(r['started_utc'],r['case']))[:20]
        complete=[r for r in cohort if r.get('completed')]
        passed=[]
        gate_errors=[]
        for r in complete:
            if not r.get('passed'):
                continue
            if not (r.get('c2') is False and r.get('rule_freeze_v1') is False
                    and r.get('stages',{}).get('checker',{}).get('passed') is True
                    and r.get('stages',{}).get('mask',{}).get('passed') is True):
                gate_errors.append(r['case'])
                continue
            passed.append(r)
        effort=sum(r.get('wall_s') or 0 for r in complete)
        stages={}
        for r in complete:
            for name, stage in r.get('stages',{}).items():
                if stage.get('wall_s') is not None:
                    metric=stages.setdefault(name,dict(measured_attempts=0,wall_s=0.0))
                    metric['measured_attempts']+=1
                    metric['wall_s']+=stage['wall_s']
        cohorts[configuration]=dict(started=len(cohort),completed=len(complete),passed=len(passed),
            pending=len(cohort)-len(complete),yield_fraction=len(passed)/len(complete) if complete else None,
            first20_finished=len(cohort)==20 and len(complete)==20,gate_errors=gate_errors,
            train_gold=sum(r['case'].startswith('train/') for r in passed),
            drop_counts=dict(Counter(r.get('drop_reason') for r in complete if not r.get('passed'))),
            completed_attempt_wall_s=effort,gold_per_worker_hour=3600*len(passed)/effort if effort else None,
            per_stage_effort=stages,rows=[dict(**selected_fields(r),completed=r.get('completed'),passed=r.get('passed')) for r in cohort])
    return dict(measured_utc=datetime.now(timezone.utc).isoformat(),runtime_epoch_sha256=EPOCH,
                method='Config ballast_s=120 and ballast_v4=true plus pinned D101 epoch; separate cohorts per configuration_sha256; first20 by attempt started_utc, including pending rows before truncation. Yield denominator is completed attempts only; report pending and first20_finished. Historical ballast45/V4-off attempts never enter. Reads recorded full checker/mask outcomes; no gates rerun.',
                matched=len(matching),configuration_cohorts=cohorts)

parser=argparse.ArgumentParser()
parser.add_argument('--mode',choices=['requeue','first20','both'],default='both')
args=parser.parse_args()
rows=list(records())
if args.mode in ['requeue','both']:
    data=retries(rows)
    (ROOT/'D101-TINY-REQUEUE.json').write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({k:v for k,v in data.items() if k!='requested_rerun'}))
if args.mode in ['first20','both']:
    data=first20(rows)
    (ROOT/'D101-FIRST20.json').write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(dict(matched=data['matched'],cohorts={k:{f:v for f,v in c.items() if f not in ['rows','per_stage_effort']}for k,c in data['configuration_cohorts'].items()})))
