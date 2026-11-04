"""Compare completed train-only replay action/token sets; no numerical rerun."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;D=H/'matrix-current-1'
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
controller=json.loads((D/'controller.json').read_text());rows=[]
for item in controller['jobs']:
 p=Path(item['result'])
 if not p.exists():continue
 r=json.loads(p.read_text())
 if not r.get('completed'):continue
 prefix=json.loads(Path(r['job']['prefix']).read_text());assert prefix['seed']in(31100000,31100001)and'/train/'in prefix['instance']['source']and'/test/'not in prefix['instance']['source']
 rows.append(dict(case=prefix['case'],repetition=prefix['repetition'],hashseed=r['job']['hashseed'],cache_mode=r['job']['cache_mode'],path=str(p),reaped=item.get('reaped',False),exit_code=item.get('exit_code'),error=r.get('error'),source_valid=r.get('source_after_valid'),native_actions=r.get('native_legal_actions'),legal_actions=r.get('legal_actions'),token_sets=r.get('token_sets'),force_entries=r.get('securing_cache_entries',[]),diagnostics=r.get('diagnostics'),cpu_s=r.get('cpu_s'),wall_s=r.get('wall_s'),peak_rss_bytes=r.get('peak_rss_bytes'),cooperative_sleep_s=r.get('cooperative_sleep_s')))
groups=[]
for case,rep in sorted({(r['case'],r['repetition'])for r in rows}):
 rr=[r for r in rows if(r['case'],r['repetition'])==(case,rep)];valid=[r for r in rr if not r['error']and r['source_valid']];reference=next((r for r in valid if r['hashseed']==0 and r['cache_mode']=='cold'),valid[0]if valid else None);differences=[]
 for r in valid:
  if reference is None:break
  if r['native_actions']!=reference['native_actions']or r['legal_actions']!=reference['legal_actions']or r['token_sets']!=reference['token_sets']:
   token_keys=sorted(set(r['token_sets'])|set(reference['token_sets']),key=lambda key:(len(key.split(','))if key else 0,key));first=next((dict(token_prefix=key,reference=reference['token_sets'].get(key),variant=r['token_sets'].get(key))for key in token_keys if r['token_sets'].get(key)!=reference['token_sets'].get(key)),None);differences.append(dict(hashseed=r['hashseed'],cache_mode=r['cache_mode'],reference=reference['path'],variant=r['path'],native_action_difference=r['native_actions']!=reference['native_actions'],action_difference=r['legal_actions']!=reference['legal_actions'],first_token_difference=first))
 groups.append(dict(case=case,repetition=rep,completed=len(rr),valid=len(valid),required=6,stable=len(valid)==6 and not differences,differences=differences,variants=[{k:r[k]for k in('hashseed','cache_mode','path','error','source_valid','legal_actions')}for r in rr]))
summary=dict(created_utc=datetime.now(timezone.utc).isoformat(),train_only=True,final=controller['completed'],completed=len(rows),required=24,groups=groups,errors=[dict(path=r['path'],error=r['error'])for r in rows if r['error']],total_cpu_s=sum(r['cpu_s']or 0 for r in rows),total_worker_wall_s=sum(r['wall_s']or 0 for r in rows),cooperative_sleep_s=sum(r['cooperative_sleep_s']or 0 for r in rows),max_separate_worker_lifetime_rss_bytes=max((r['peak_rss_bytes']or 0 for r in rows),default=0),active_job=controller['active_job'],all_reaped=all(r['reaped']for r in rows),variants=rows,scope='Train-only fixed-prefix necessary-securing+production-token-mask determinism diagnostic. No full checker or plan acceptance claim. Current frozen runtime; does not by itself isolate the earlier archived implementation.')
(D/'summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n');print(json.dumps({k:summary[k]for k in('final','completed','required','errors','total_cpu_s','active_job')}));print(json.dumps([{k:g[k]for k in('case','repetition','completed','stable','differences')}for g in groups]))
