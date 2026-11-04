"""Read-only cohort audit; writes only D100-fresh.json when invoked."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CUTOFF = datetime.fromisoformat('2026-10-29T20:45:00+00:00')
DEADLINE = datetime.fromisoformat('2026-10-30T06:00:00+00:00')

def summary(rows):
    completed = [r for r in rows if r.get('completed')]
    passed = [r for r in completed if r.get('passed')]
    effort = sum(r.get('wall_s') or 0 for r in completed)
    stage_effort = {}
    for r in completed:
        for name, stage in r.get('stages', {}).items():
            if stage.get('wall_s') is not None:
                metric = stage_effort.setdefault(name, {'measured_attempts': 0, 'wall_s': 0.0})
                metric['measured_attempts'] += 1
                metric['wall_s'] += stage['wall_s']
    return dict(started=len(rows), completed=len(completed), passed=len(passed),
                train_passed=sum(r['case'].startswith('train/') for r in passed),
                stage_effort=stage_effort,
                by_ship={ship: dict(completed=sum(r['case'].split('/')[2] == ship for r in completed), passed=sum(r['case'].split('/')[2] == ship for r in passed)) for ship in sorted({r['case'].split('/')[2] for r in rows})},
                pending=len(rows)-len(completed),
                yield_fraction=len(passed)/len(completed) if completed else None,
                completed_attempt_wall_s=effort,
                gold_per_worker_hour=3600*len(passed)/effort if effort else None,
                drops=dict(Counter(r.get('drop_reason') for r in completed if not r.get('passed'))))

now = datetime.now(timezone.utc)
records = []
current = []
for base in ['train', 'val', 'legacy-small-v1', 'quarantine-pre-epoch', 'quarantine-d97-warmstart']:
    for path in (ROOT/base).rglob('result.json'):
        row = json.loads(path.read_text())
        if row.get('rung') != 'small':
            continue
        row['record_path'] = str(path.relative_to(ROOT))
        records.append(row)
        if base in ['train', 'val']:
            current.append(row)
first_case = {}
first_hash = {}
for row in records:
    timestamp = row.get('started_utc')
    if not timestamp:
        continue
    started = datetime.fromisoformat(timestamp)
    for key, lookup in [('case', first_case), ('input_sha256', first_hash)]:
        identity = row.get(key)
        if identity:
            lookup[identity] = min(started, lookup.get(identity, started))
fresh = [r for r in current if first_case.get(r['case'], now) >= CUTOFF
         and first_hash.get(r.get('input_sha256'), now) >= CUTOFF]
recovery_cases = {r['case'] for r in json.loads((ROOT/'D98-RERUN.json').read_text())['rows']}
recovery = [r for r in current if r['case'] in recovery_cases]
d98 = [r for r in current if r.get('config',{}).get('gold_constructor_fallback_v1')]
sample12_cases = {r['case'] for r in json.loads((ROOT/'D100-FRESH-TARGETS.json').read_text())['requested_rerun']}
sample12 = [r for r in current if r['case'] in sample12_cases]
postcutoff = [r for r in current if datetime.fromisoformat(r['started_utc']) >= CUTOFF]
train_now = sum(r.get('passed') and r.get('completed') and r['case'].startswith('train/') for r in current)
hours_left = max(0, (DEADLINE-now).total_seconds()/3600)
cohorts = {'fresh_inputs': summary(fresh), 'balanced_fresh_sample_12': summary(sample12), 'recovery_14': summary(recovery),
           'all_d98_selected_retries': summary(d98), 'postcutoff_attempts_including_retries': summary(postcutoff),
           'all_current_policy_mixture': summary(current)}
projections = {}
for name in ['all_d98_selected_retries', 'all_current_policy_mixture', 'fresh_inputs', 'balanced_fresh_sample_12']:
    rate = cohorts[name]['gold_per_worker_hour']
    projections[name] = dict(rate=rate, train_gold_now=train_now, hours_remaining=hours_left,
        at_five_continuous_workers=train_now+5*hours_left*rate if rate is not None else None,
        at_six_continuous_workers=train_now+6*hours_left*rate if rate is not None else None,
        at_eight_continuous_workers=train_now+8*hours_left*rate if rate is not None else None,
        at_ten_continuous_workers=train_now+10*hours_left*rate if rate is not None else None)
result = dict(measured_utc=now.isoformat(), cutoff_utc=CUTOFF.isoformat(),deadline_utc=DEADLINE.isoformat(),
    methodology='First-ever attempt = minimum started_utc over active train/val and all retained legacy/quarantine result records, checked by BOTH case and exact input_sha256. Completion after cutoff alone does not make an input fresh. Recovery cohort is the pinned D98 requested_rerun set. Gold is completed and passed (driver full checker plus mask gates).',
    assumptions=['Historical archive coverage includes every retained small campaign record; no filesystem mtimes are used as attempt timestamps.',
                 'APPROXIMATION: projections divide measured completed-attempt wall effort by qualified gold count, multiply by continuously occupied single-thread workers and hours left; exclude admission and startup waits, in-flight effort, future policy changes, corpus cap, and tiny-to-small ramp.',
                 'D98 selected failed-instance retries are a deliberately hard cohort and not an unbiased estimate of fresh yield. Mixed current policies are also not fresh-input evidence.'],
    target_1000=dict(required_gold_per_worker_hour_at_ten=max(0,1000-train_now)/(10*hours_left) if hours_left else None, required_gold_per_worker_hour_at_eight=max(0,1000-train_now)/(8*hours_left) if hours_left else None),
    handoff='No finite tiny-to-small handoff assumed; current tiny throughput stalled. Worker scenarios are constant-capacity bounds.',
    records_scanned=len(records), distinct_case_count=len(first_case),cohorts=cohorts,capacity_scenarios=projections,
    fresh_rows=[dict(case=r['case'],started_utc=r['started_utc'],completed=r.get('completed'),passed=r.get('passed')) for r in fresh],
    postcutoff_rows=[dict(case=r['case'],started_utc=r['started_utc'],first_ever_utc=first_case[r['case']].isoformat(),completed=r.get('completed'),passed=r.get('passed'),drop_reason=r.get('drop_reason')) for r in postcutoff])
(ROOT/'D100-fresh.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(dict(measured_utc=result['measured_utc'],cohorts=cohorts,capacity_scenarios=projections),indent=2))
