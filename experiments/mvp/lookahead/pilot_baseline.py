"""Bounded one-core action-uniform baseline timing; no model/checker/finisher."""
import os
for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','RAYON_NUM_THREADS']:os.environ[k]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
import hashlib,json,random,resource,signal,time
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def snapshot():
 return {str(p.relative_to(ROOT)):sha(p)for folder in ['src/stowage','src/llm/securing']for p in sorted((ROOT/folder).rglob('*.py'))}
def main():
 from stowage.io import load_instance
 from stowage.actions import get_action_space
 from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
 from llm.securing import wrap_engine
 inv=json.loads((HERE/'inventory.json').read_text());case=next(c for c in inv['contracts']if c['rung']=='tiny');instance=load_instance(case['instance']['path']);before=snapshot();start=time.perf_counter();r0=resource.getrusage(resource.RUSAGE_SELF)
 row=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),case=case['case'],rung='tiny',seed=0,native_threads=1,gpu=False,wall_cap_s=30,source_before=before,steps=[],checker_run=False,physical_acceptance_claim=False)
 def timeout(*_):raise TimeoutError('pilot wall cap30s')
 signal.signal(signal.SIGALRM,timeout);signal.alarm(30)
 try:
  phase=time.perf_counter();engine=wrap_engine(make_feasibility_engine(instance,get_action_space(),config=FeasibilityConfig(extra={'global_screen':0})));row['construct_s']=time.perf_counter()-phase;rng=random.Random(0)
  while not engine.is_complete():
   phase=time.perf_counter();actions=engine.legal_actions();query=time.perf_counter()-phase
   if not actions:row.update(status='dead_end_proxy',dead_end=str(engine.dead_end()));break
   action=actions[rng.randrange(len(actions))];phase=time.perf_counter();engine.push(action);row['steps'].append(dict(actions=len(actions),query_s=query,push_s=time.perf_counter()-phase,selected=repr(action)))
  else:row['status']='complete_proxy'
  row['diagnostics']=engine.screen_diagnostics()
 except Exception as exc:row.update(status='error',error=f'{type(exc).__name__}: {exc}')
 finally:
  signal.alarm(0);r1=resource.getrusage(resource.RUSAGE_SELF);after=snapshot();row.update(wall_s=time.perf_counter()-start,cpu_s=r1.ru_utime+r1.ru_stime-r0.ru_utime-r0.ru_stime,peak_rss_bytes=r1.ru_maxrss,source_after=after,source_valid=before==after,worker_sha256=sha(__file__));(HERE/'pilot-baseline.json').write_text(json.dumps(row,sort_keys=True,indent=2)+'\n');print(json.dumps({k:row[k]for k in ['pid','status','wall_s','cpu_s','peak_rss_bytes','source_valid']}))
if __name__=='__main__':main()
