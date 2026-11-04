"""Freeze completed tiny first5/10/20 audits; forecast small using only new240 rates."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CORPUS=ROOT.parent/'corpus'
source=json.loads((ROOT/'D101-FIRST20.json').read_text())
now=datetime.now(timezone.utc)
for configuration,cohort in source['configuration_cohorts'].items():
 for n in [5,10,20]:
  selected=cohort['rows'][:n]
  if len(selected)!=n or not all(r['completed']for r in selected):continue
  rows=[json.loads(Path(r['result']).read_text())for r in selected]
  effort={};passed=[]
  for r in rows:
   if r.get('passed'):
    assert hashlib.sha256(Path(r['input_path']).read_bytes()).hexdigest()==r['input_sha256']
    assert hashlib.sha256((ROOT/r['case']/'plan.json').read_bytes()).hexdigest()==r['plan_sha256']
    assert r['stages']['checker']['passed']and r['stages']['mask']['passed']
    assert r['c2']is False and r['rule_freeze_v1']is False
    passed.append(r)
   for stage,s in r['stages'].items():
    if s.get('wall_s')is not None:
     m=effort.setdefault(stage,{'n':0,'wall_s':0.0});m['n']+=1;m['wall_s']+=s['wall_s']
  for e in effort.values():e['mean_s']=e['wall_s']/e['n']
  wall=sum(r['wall_s']for r in rows)
  calendar_start=min(datetime.fromisoformat(r['started_utc'])for r in rows)
  calendar_finish=max(datetime.fromisoformat(r['finished_utc'])for r in rows)
  calendar_window_s=(calendar_finish-calendar_start).total_seconds()
  out=dict(measured_utc=now.isoformat(),configuration_sha256=configuration,
   calendar_window_s=calendar_window_s,calendar_gold_per_worker_hour_assuming_two_steady=3600*len(passed)/(2*calendar_window_s)if calendar_window_s else None,
   calendar_wall_effort_per_gold_s=2*calendar_window_s/len(passed)if passed else None,
   first_n_by_attempt_start=n,completed=n,passed=len(passed),total_wall_s=wall,
   mean_attempt_wall_s=wall/n,attempt_wall_per_gold_s=wall/len(passed)if passed else None,
   stage_cost=effort,drop_counts=dict(Counter(r['drop_reason']for r in rows if not r['passed'])),
   input_plan_hash_and_gate_records_verified=True)
  (ROOT/(f'D101-FIRST{n}.json' if n != 20 else 'D101-FIRST20-MILESTONE.json')).write_text(json.dumps(out,indent=2)+'\n')
  print(json.dumps(out))
  if n!=20:continue
  active=[]
  for path in (ROOT/'train/tiny').rglob('result.json'):
   active.append(json.loads(path.read_text()))
  train_gold=sum(r.get('completed')and r.get('passed')for r in active)
  done={r['case']for r in active if r.get('completed')}
  inputs=set()
  for audit in (CORPUS/'train/tiny').rglob('*.audit.json'):
   a=json.loads(audit.read_text());path=audit.with_name(audit.name.removesuffix('.audit.json')+'.json')
   if a.get('accepted')is True and a.get('rule_freeze_v1_enabled')is False and path.exists():inputs.add(str(path.relative_to(CORPUS)).removesuffix('.json'))
  remaining=len(inputs-done);max_gold=train_gold+remaining;target=800
  if train_gold>=target:phase_s=0;phase_reason='target_already_met'
  elif max_gold<target:
   phase_s=remaining*out['mean_attempt_wall_s']/2;phase_reason='currently_committed_inventory_exhausts_before800_even_at100percent'
  elif out['attempt_wall_per_gold_s']is not None:
   phase_s=(target-train_gold)*out['attempt_wall_per_gold_s']/2;phase_reason='target800_within_current_inventory'
  else:phase_s=None;phase_reason='zero_passing_tiny_sample_no_finite_gold_target_eta'
  small=json.loads((ROOT/'D101-SMALL240.json').read_text());stats=small['cohorts']['new240']
  small_now=small['projection_0200']['new240']['train_gold_now_all_policies']
  fixed_stats=small['cohorts'].get('first20_new240',{})
  ideal_small_stats=fixed_stats if fixed_stats.get('completed')==20 else stats
  rate=ideal_small_stats['gold_per_worker_hour']
  left=max(0,(datetime.fromisoformat('2026-10-30T06:00:00+00:00')-now).total_seconds()/3600)
  phase_hours=phase_s/3600 if phase_s is not None else None
  scenarios={}
  for ceiling in [8,10]:
   before=min(left,phase_hours)if phase_hours is not None else left
   after=max(0,left-before)
   scenarios[str(ceiling)]=dict(worker_hours=6*before+ceiling*after,
      projected_small_train=small_now+rate*(6*before+ceiling*after)if rate is not None else None)
  calendar_small=small['cohorts'].get('first20_new240',{}).get('calendar_gold_per_worker_hour_assuming_six_steady')
  if train_gold>=target:calendar_phase_hours=0
  elif max_gold<target:calendar_phase_hours=remaining*(2*calendar_window_s/n)/2/3600
  elif passed:calendar_phase_hours=(target-train_gold)*out['calendar_wall_effort_per_gold_s']/2/3600
  else:calendar_phase_hours=None
  calendar_scenarios={}
  for ceiling in [8,10]:
   before=min(left,calendar_phase_hours)if calendar_phase_hours is not None else left;after=max(0,left-before)
   calendar_scenarios[str(ceiling)]=dict(worker_hours=6*before+ceiling*after,projected_small_train=small_now+calendar_small*(6*before+ceiling*after)if calendar_small is not None else None)
  forecast=dict(calendar_sensitivity=dict(tiny_calendar_handoff_hours=calendar_phase_hours,tiny_calendar_handoff_utc=(now+timedelta(hours=calendar_phase_hours)).isoformat()if calendar_phase_hours is not None else None,small_first20_calendar_gold_per_worker_hour=calendar_small,small_first20=small['cohorts'].get('first20_new240'),phased_projection_six_then_ceiling=calendar_scenarios),measured_utc=now.isoformat(),tiny_sample=out,tiny_train_now=train_gold,tiny_target=target,
   committed_tiny_train_inputs=len(inputs),remaining_schedulable_inputs=remaining,
   maximum_train_gold_from_committed_inventory=max_gold,
   tiny_handoff_reason=phase_reason,estimated_handoff_hours=phase_hours,
   estimated_handoff_utc=(now+timedelta(seconds=phase_s)).isoformat()if phase_s is not None else None,
   new240_small_snapshot_utc=small['measured_utc'],new240_small_stats=stats,small_fixed20_stats=fixed_stats,ideal_small_rate_basis='completefixedfirst20'if fixed_stats.get('completed')==20 else 'rollingcompletedpendingcensored',ideal_small_gold_per_worker_hour=rate,small_train_now=small_now,
   deadline_hours_remaining=left,phased_projection_six_then_ceiling=scenarios,
   approximation='First20 tiny measured completed-attempt wall effort per gold with2continuousworkers until800; if800unreachable from currently committed input count, instead model inventoryexhaustion. Small uses ONLY new240 completefixedfirst20 effort whenavailable (otherwise rollingcompleted, possiblycensored),6workers before tinyhandoff then8sandboxfallback or10configuredceiling. No admission waits or future source/policy changes. In-flight slow small attempts excluded may inflate rate; report pending. Gold gates are never weakened. Current inventory only; future streaming inputs not assumed.')
  (ROOT/'D101-PHASED-PROJECTION.json').write_text(json.dumps(forecast,indent=2)+'\n')
  print(json.dumps({k:v for k,v in forecast.items()if k not in ['tiny_sample','new240_small_stats']}))
