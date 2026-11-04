"""Audit only the new240-second small pipeline; no supervisor/policy edits."""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
EPOCH='b57163150dd412a04d399a10e01ebb043e3508bc9b56696ae97e3d06ef5a4fa6'
CUTOFF='2026-10-29T20:45:00+00:00'
DEADLINE=datetime.fromisoformat('2026-10-30T06:00:00+00:00')
rows=[];current=[]
for base in ['train','val','legacy-small-v1',*[p.name for p in ROOT.glob('quarantine*')if p.is_dir()]]:
 for path in (ROOT/base).rglob('result.json'):
  r=json.loads(path.read_text())
  if r.get('rung')!='small':continue
  r['result']=str(path.relative_to(ROOT));rows.append(r)
  if base in ['train','val']:current.append(r)
first_case={};first_hash={}
for r in rows:
 for key,index in [('case',first_case),('input_sha256',first_hash)]:
  if r.get(key)and r.get('started_utc'):index[r[key]]=min(index.get(r[key],'z'),r['started_utc'])
new=[r for r in current if r.get('config',{}).get('small_ballast_s')==240 and (r.get('internal_ballast_budget_s')==240 or not r.get('completed')) and r.get('runtime_epoch_sha256')==EPOCH]
fresh=[r for r in new if first_case.get(r['case'],'')>=CUTOFF and first_hash.get(r.get('input_sha256'),'')>=CUTOFF]

def stats(selected):
 done=[r for r in selected if r.get('completed')];passed=[];gate_errors=[];mismatch=[]
 for r in done:
  if any(c.get('time_limit_s')!=240 for c in r.get('internal_ballast_calls',[])):mismatch.append(r['case'])
  if r.get('passed'):
   if r.get('c2')is False and r.get('rule_freeze_v1')is False and r.get('stages',{}).get('checker',{}).get('passed')is True and r.get('stages',{}).get('mask',{}).get('passed')is True and r['stages']['mask'].get('decoder_backend')=='irs-securing-necessary-envelope':passed.append(r)
   else:gate_errors.append(r['case'])
 wall=sum(r.get('wall_s')or 0 for r in done);stages={}
 for r in done:
  for name,s in r.get('stages',{}).items():
   if s.get('wall_s')is not None:
    metric=stages.setdefault(name,{'measured_attempts':0,'wall_s':0.0});metric['measured_attempts']+=1;metric['wall_s']+=s['wall_s']
 return dict(started=len(selected),completed=len(done),passed=len(passed),pending_internal_budget_not_yet_recorded=sum(not r.get('completed') and r.get('internal_ballast_budget_s') is None for r in selected),train_gold=sum(r['case'].startswith('train/')for r in passed),pending=len(selected)-len(done),yield_fraction=len(passed)/len(done)if done else None,mean_attempt_wall_s=wall/len(done)if done else None,attempt_wall_per_gold_s=wall/len(passed)if passed else None,gold_per_worker_hour=3600*len(passed)/wall if wall else None,total_completed_wall_s=wall,drop_counts=dict(Counter(r.get('drop_reason')for r in done if not r.get('passed'))),gate_errors=gate_errors,internal_budget_mismatches=mismatch,stage_effort=stages,source_counts={source:{'completed':sum((r.get('source') or r.get('solver') or 'unrecorded')==source for r in done),'passed':sum((r.get('source') or r.get('solver') or 'unrecorded')==source for r in passed)}for source in sorted({r.get('source') or r.get('solver') or 'unrecorded'for r in done})},by_ship={ship:{'completed':sum(r['case'].split('/')[2]==ship for r in done),'passed':sum(r['case'].split('/')[2]==ship for r in passed)}for ship in sorted({r['case'].split('/')[2]for r in selected})})
now=datetime.now(timezone.utc);left=max(0,(DEADLINE-now).total_seconds()/3600);train_now=sum(r.get('completed')and r.get('passed')and r['case'].startswith('train/')for r in current)
first20=sorted(new,key=lambda r:r['started_utc'])[:20]
cohorts={'new240':stats(new),'fresh_first_ever_after_2045_new240':stats(fresh),'first20_new240':stats(first20)}
if len(first20)==20 and all(r.get('completed') for r in first20):
 start=min(datetime.fromisoformat(r['started_utc'])for r in first20);finish=max(datetime.fromisoformat(r['finished_utc'])for r in first20);window=(finish-start).total_seconds()
 cohorts['first20_new240']['calendar_window_s']=window
 cohorts['first20_new240']['calendar_gold_per_worker_hour_assuming_six_steady']=3600*cohorts['first20_new240']['passed']/(6*window)if window else None
 cohorts['first20_new240']['calendar_caveat']='Conservative six-steady-worker divisor; first worker ramp sometimes5/6. Includes dispatch/startup gaps inside firststart-lastfinish, excludes time before firststart.'
configurations={k:stats([r for r in new if r.get('configuration_sha256')==k])for k in sorted({r.get('configuration_sha256')for r in new})}
projections={name:{'train_gold_now_all_policies':train_now,'hours_remaining':left,'constant_worker_scenarios':{str(n):train_now+n*left*s['gold_per_worker_hour']if s['gold_per_worker_hour']is not None else None for n in [5,6,8,10]}}for name,s in cohorts.items()}
out=dict(measured_utc=now.isoformat(),runtime_epoch_sha256=EPOCH,cohorts=cohorts,configuration_cohorts=configurations,projection_0200=projections,method='Only small config.small_ballast_s=240 plus completed row.internal_ballast_budget_s=240 and epochb571; all matching-config pending rows retained even before internal budget trace is recorded; no90/120 rows. Fresh=minimum started_utc by case AND inputsha over active and retained archives >=20:45Z. Gate metadata checked, not re-executed. In-flight attempts excluded from yield denominator and effort; report pending to reveal completion bias.',approximation='Linear completed-attempt wall-effort throughput with continuous workers and unchanged yield; no finite tinyhandoff assumed, no admission/startup correction, incompletecohort may bias throughput high.',rows=[{k:r.get(k)for k in ['case','input_sha256','started_utc','finished_utc','completed','passed','drop_reason','wall_s','configuration_sha256','internal_ballast_budget_s']}for r in sorted(new,key=lambda r:r['started_utc'])])
(ROOT/'D101-SMALL240.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(dict(measured_utc=out['measured_utc'],cohorts=cohorts,projection_0200=projections),indent=2))
