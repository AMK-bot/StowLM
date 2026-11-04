"""Whole public checker on one pinned TRAIN gold plan, fresh process, no mocks."""
import argparse,hashlib,importlib,json,os,resource,sys,time,traceback
from datetime import datetime,timezone
from pathlib import Path
for k in('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','RAYON_NUM_THREADS'):os.environ[k]='1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,sort_keys=True,indent=2,default=str)+'\n')
def run(job):
 d=Path(job['directory']);d.mkdir(parents=True,exist_ok=True);out=d/'result.json';assert not out.exists();start=time.perf_counter();cpu=time.process_time();runtime=Path(job['runtime']);f=json.loads((runtime/'manifest.json').read_text());m=json.loads((runtime/'marker-manifest.json').read_text());reads=set()
 def audit(event,args):
  if event=='open'and args and isinstance(args[0],(str,bytes)):
   try:reads.add(str(Path(os.fsdecode(args[0])).resolve()))
   except (ValueError,OSError):pass
 sys.addaudithook(audit)
 r=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),completed=False,job=job,worker_sha256=sha(__file__),hashseed=os.environ.get('PYTHONHASHSEED'),priority_attempts=[],native_threads=1,gpu=False,stages={});save(out,r)
 try:
  for name,fn in(('setpriority',lambda:os.setpriority(os.PRIO_PROCESS,0,10)),('nice',lambda:os.nice(10))):
   try:fn();r['priority_attempts'].append(dict(method=name,passed=True));break
   except OSError as exc:r['priority_attempts'].append(dict(method=name,passed=False,error=str(exc)))
  assert all(sha(runtime/n)==v for n,v in {**f['files'],**m['files']}.items());assert os.environ['PYTHONPATH']==str(runtime/'src');sys.path.insert(0,str(runtime/'src'))
  pins=job['pins'];assert'/train/'in pins['instance']['source']and'/test/'not in pins['instance']['source'];assert'/train/'in pins['plan']['source'];assert all(sha(p['path'])==p['sha256']for p in pins.values())
  from stowage.params import repo_root
  assert repo_root()==runtime;r['params_repo_root']=str(repo_root())
  from stowage.io import load_instance,load_plan
  from stowage.model import CheckReport
  from stowage.checker.full import check,report_matches_inputs
  from stowage.rules.catalogue import rule_freeze_v1_enabled
  assert not rule_freeze_v1_enabled();instance=load_instance(pins['instance']['path']);plan=load_plan(pins['plan']['path']);historical=CheckReport.model_validate_json(Path(pins['checker']['path']).read_text());assert historical.passed and report_matches_inputs(historical,instance,plan)
  names=('stowage.io','stowage.params','stowage.checker.full','stowage.checker.lashing','stowage.checker.visibility');r['critical_origins']={n:str(Path(importlib.import_module(n).__file__).resolve())for n in names};assert all(Path(p).is_relative_to(runtime/'src')for p in r['critical_origins'].values());phase=time.perf_counter();pc=time.process_time();report=check(instance,plan);r['stages']['full_checker']=dict(wall_s=time.perf_counter()-phase,cpu_s=time.process_time()-pc);payload=report.model_dump(mode='json');save(d/'checker.json',payload);r.update(checker_passed=report.passed,whole_report_fingerprint=digest(payload),report_matches_inputs=report_matches_inputs(report,instance,plan),parts={name:dict(ran=part.ran,error=part.error,violations=len(part.violations),metrics=part.metrics)for name,part in report.parts.items()},violations=len(report.violations))
  # Preserve every report field; separately hash explicit numerical/semantic evidence.
  semantic=dict(passed=payload['passed'],count=payload['count'],shut_out=payload['shut_out'],violations=payload['violations'],parts=payload['parts']);r['numerical_semantic_fingerprint']=digest(semantic);r['fingerprint_policy']='Whole-report SHA excludes NOTHING. Separate semantic SHA covers verdict/count/shutout/violations/allparts; full provenance remains in raw report and whole-report SHA.'
  r['source_after_valid']=all(sha(runtime/n)==v for n,v in {**f['files'],**m['files']}.items());assert r['source_after_valid']
 except Exception as exc:r.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc(),checker_passed=False)
 finally:
  needed=(time.process_time()-cpu)/job.get('duty_fraction',.5)-(time.perf_counter()-start);s0=time.perf_counter()
  if needed>0:time.sleep(needed)
  r.update(completed=True,wall_s=time.perf_counter()-start,cpu_s=time.process_time()-cpu,cooperative_sleep_s=time.perf_counter()-s0,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,opened_files=sorted(reads),loaded_modules={n:getattr(z,'__file__',None)for n,z in sys.modules.items()if n.startswith(('stowage.','llm.'))},ended_utc=datetime.now(timezone.utc).isoformat());save(out,r);print(json.dumps({k:r.get(k)for k in('pid','completed','error','checker_passed','wall_s','cpu_s','stages')}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();run(json.loads(a.job.read_text()))
