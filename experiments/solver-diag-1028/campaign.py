"""Resumable bounded diagnostics. Every child PID is recorded and owned."""
import argparse,concurrent.futures,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
THREADS=('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')
def run(label,case,solver):
 out=HERE/label/'runs'/solver/case['id'];out.mkdir(parents=True,exist_ok=True)
 final=out/'done.json'
 if final.exists():return json.loads(final.read_text())
 budget=45 if case['rung']=='tiny' else 120;cap=120 if case['rung']=='tiny' else 300
 cmd=[sys.executable,str(HERE/'worker.py'),'--snapshot',str(HERE/label/'snapshot'),'--input',case['path'],'--output',str(out),'--solver',solver,'--budget',str(budget),'--cap',str(cap)]
 env={**os.environ,**{k:'1' for k in THREADS},'PYTHONDONTWRITEBYTECODE':'1'}
 start=time.monotonic()
 with (out/'stdout.log').open('w') as stdout,(out/'stderr.log').open('w') as stderr:
  child=subprocess.Popen(cmd,cwd=HERE.parents[1],env=env,stdout=stdout,stderr=stderr)
  (out/'pid.json').write_text(json.dumps(dict(pid=child.pid,owner_pid=os.getpid(),started=time.time(),command=cmd)))
  try:code=child.wait(timeout=cap+10)
  except subprocess.TimeoutExpired:
   child.terminate()
   try:code=child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();code=child.wait()
 result=json.loads((out/'result.json').read_text()) if (out/'result.json').exists() else dict(error='worker produced no checkpoint')
 result.update({k:case[k] for k in ('id','generator','rung','vessel','release_member')});result.update(label=label,solver=solver,returncode=code,process_wall_s=time.monotonic()-start)
 final.write_text(json.dumps(result,sort_keys=True)+'\n')
 print(json.dumps({k:result.get(k) for k in ('label','id','solver','solver_status','completion','ballast_status','checker_passed','wall_s','error')}),flush=True)
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('label');p.add_argument('--workers',type=int,default=1);p.add_argument('--background-only',action='store_true');p.add_argument('--limit',type=int);a=p.parse_args()
 cases=json.loads((HERE/'selection.json').read_text())['rows'];jobs=[]
 for case in cases:
  for solver in (['background'] if a.background_only else ['scalable','twostep']+(['exact'] if case['rung']=='tiny' else [])):jobs.append((case,solver))
 if a.limit:jobs=jobs[:a.limit]
 started=time.monotonic()
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
  results=list(pool.map(lambda x:run(a.label,*x),jobs))
 name='background' if a.background_only else 'outcomes'
 (HERE/a.label/(name+'.jsonl')).write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in results))
 (HERE/a.label/(name+'-compute.json')).write_text(json.dumps(dict(workers=a.workers,wall_s=time.monotonic()-started,process_sum_s=sum(r['process_wall_s'] for r in results),jobs=len(results)),indent=2)+'\n')
