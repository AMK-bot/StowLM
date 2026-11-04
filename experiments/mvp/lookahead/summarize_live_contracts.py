"""Audit current-live contract replay without touching frozen primary evidence."""
import json,hashlib,statistics
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;D=H/'formal-live-v2-contracts'
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')
f=read(D/'runtime-freeze-live.json');controllers=[read(D/f'shard{i}/controller.json')for i in(0,1)];rows=[];children=[]
for c in controllers:
 for j in c['jobs']:
  children.append({k:j.get(k)for k in('case','pid','reaped','exit_code','result')})
  p=Path(j['result'])
  if not p.exists():continue
  r=read(p);r['_result_path']=str(p);rows.append(r)
completed=[r for r in rows if r.get('completed')];passed=[r for r in completed if r.get('contract_passed')];errors=[dict(case=r['job']['case']['case'],error=r.get('error'),source_valid=r.get('source_valid'),result=r['_result_path'])for r in completed if not r.get('contract_passed')or not r.get('source_valid')]
checks=[]
for r in completed:
 p=Path(r['_result_path']);before=read(p.parent/'source-before.json');after=read(p.parent/'source-after.json');job=r['job'];bindings={kind:sha(job['case'][kind]['path'])==job['case'][kind]['sha256']for kind in('instance','plan','checker')};checks.append(dict(case=job['case']['case'],result=str(p),source_exact=before==after==f['lookahead_runtime_sources'],worker_exact=r['worker_sha256']==f['worker_sha256'],tokenizer_sha256=r.get('tokenizer_sha256'),bindings=bindings,source_epoch_before=r['source_epoch_before'],source_epoch_after=r['source_epoch_after'],mask_mlx_imported='llm.stowlm.mask_mlx'in r.get('loaded_modules',{}),mlx_imported=r.get('mlx_imported')))
current={p:sha(H.parents[2]/p)for p in f['lookahead_runtime_sources']};drift=[p for p,v in f['lookahead_runtime_sources'].items()if current.get(p)!=v];final=all(c.get('completed')for c in controllers);valid=final and len(completed)==100 and len(passed)==100 and not errors and not drift and all(x['source_exact']and x['worker_exact']and all(x['bindings'].values())for x in checks)and all(x.get('reaped')and x.get('exit_code')==0 for x in children)
exposure={}
for rung in('all','tiny','small'):
 rr=completed if rung=='all'else[r for r in completed if r['job']['case']['rung']==rung];dd=[r.get('diagnostics',{}).get('lookahead',{})for r in rr];exposure[rung]=dict(contracts=len(rr),native_contract_true=sum(d.get('native_executor_contract')is True for d in dd),nonzero_probes=sum(d.get('candidate_probes',0)>0 for d in dd),candidate_probes=sum(d.get('candidate_probes',0)for d in dd),all_unknown_or_unprobed=sum(d.get('candidate_probes',0)==0 for d in dd),states=sum(d.get('states_evaluated',0)for d in dd),unknown=sum(d.get('unknown_states',0)for d in dd),deferred_reasons={key:sum(d.get('deferred_reasons',{}).get(key,0)for d in dd)for key in set().union(*(d.get('deferred_reasons',{})for d in dd))})
summary=dict(created_utc=datetime.now(timezone.utc).isoformat(),final=final,valid=valid,epoch=f['epoch'],required=100,completed=len(completed),passed=len(passed),errors=errors,by_rung={rung:dict(completed=sum(r['job']['case']['rung']==rung for r in completed),passed=sum(r['job']['case']['rung']==rung for r in passed))for rung in('tiny','small')},tokens=sum(r.get('tokens',0)for r in passed),actions=sum(r.get('actions',0)for r in passed),worker_cpu_s=sum(r.get('cpu_s',0)for r in rows),worker_wall_sum_s=sum(r.get('wall_s',0)for r in rows),sleep_s=sum(r.get('sleep_s',0)for r in rows),max_worker_lifetime_rss_bytes=max((r.get('peak_rss_bytes',0)for r in rows),default=0),controllers=controllers,children=children,active_children=[j for j in children if not j.get('reaped')],source_checks=checks,current_source_drift=drift,exposure=exposure,all_token_checks_bound_to_historical_full_checker=True,scope='Fresh100 current-live DEFAULT12/128 production-tokenmask+securing+lookahead contracts. Independent from archived primary randomrollouts. No new physics/full-checker run; reports are historically checker-passed and bound to exactinput/plan bytes.',summarizer_sha256=sha(__file__))
write(D/'summary.json',summary);print(json.dumps({k:summary[k]for k in('final','valid','completed','passed','errors','active_children','current_source_drift')}))
