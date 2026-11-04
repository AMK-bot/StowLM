"""Preserve full reports and compare only with explicit elapsed-time exclusions."""
import json,hashlib,copy
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;D=H/'fullcheck';c=json.loads((D/'controller.json').read_text())
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def remove_measured_times(payload):
 excluded=[]
 def visit(x,path=()):
  if isinstance(x,dict):
   result={}
   for key,value in x.items():
    if 'metrics'in path and(key=='elapsed_s'or key.endswith('.elapsed_s')):excluded.append(dict(path=list((*path,key)),value=value))
    else:result[key]=visit(value,(*path,key))
   return result
  if isinstance(x,list):return[visit(value,(*path,i))for i,value in enumerate(x)]
  return x
 return visit(payload),excluded
rows=[]
for job in c['jobs']:
 p=Path(job['result']);r=json.loads(p.read_text())
 if not r.get('completed'):continue
 reportpath=p.parent/'checker.json';payload=json.loads(reportpath.read_text())if reportpath.exists()else None;normal,excluded=remove_measured_times(payload)if payload is not None else(None,[])
 rows.append(dict(hashseed=job['hashseed'],treatment=job['treatment'],pid=job['pid'],reaped=job.get('reaped'),exit_code=job.get('exit_code'),result=str(p),checker=str(reportpath),checker_passed=r.get('checker_passed'),error=r.get('error'),source_valid=r.get('source_after_valid'),cpu_s=r.get('cpu_s'),wall_s=r.get('wall_s'),sleep_s=r.get('cooperative_sleep_s'),peak_rss_bytes=r.get('peak_rss_bytes'),stages=r.get('stages'),whole_report_fingerprint=r.get('whole_report_fingerprint'),excluding_only_recorded_elapsed_metrics_fingerprint=digest(normal)if normal is not None else None,excluded_measured_timing_fields=excluded,violations=payload.get('violations',[])if payload else[],parts={name:dict(ran=part.get('ran'),error=part.get('error'),violations=len(part.get('violations',[])))for name,part in payload.get('parts',{}).items()}if payload else{}))
provided=[r for r in rows if r['treatment']=='provided'];final=c['completed'];summary=dict(created_utc=datetime.now(timezone.utc).isoformat(),final=final,required=4,completed=len(rows),train_only=True,plan='TRAIN-gold-small-30100025',provided_hashseeds=[r['hashseed']for r in provided],provided_reports_identical_excluding_only_elapsed_metrics=len(provided)==3 and len({r['excluding_only_recorded_elapsed_metrics_fingerprint']for r in provided})==1,all_children_reaped=all(r['reaped']for r in rows),active=c['active_job'],rows=rows,total_cpu_s=sum(r['cpu_s']or 0 for r in rows),controller_wall_s=c.get('wall_s'),max_separate_worker_lifetime_rss_bytes=max((r['peak_rss_bytes']or 0 for r in rows),default=0),scope='Actual public full.check() on exactly pinned historical TRAIN gold input+plan; cold freshprocess, oldsourceidentical, real asset treatment, no source mocks/no relaxed rule or checker budget. Raw reports preserve EVERYTHING. Hash normalization removes only explicitly listed elapsed_s metrics, no physical values, verdicts, error messages or provenance.',interpretation='Missing asset fails closed. Provided historical gold may time out in existing cold-process stability wall budgets; timeout means incomplete checking, not physical infeasibility and never acceptance. No successful current whole-check claim unless checker_passed is true.')
(D/'summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n');print(json.dumps({k:summary[k]for k in('final','completed','provided_reports_identical_excluding_only_elapsed_metrics','active','total_cpu_s')}))
