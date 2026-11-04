"""Six-core resumable generation/solve queue with a campaign wall deadline."""
from __future__ import annotations
import argparse,concurrent.futures,datetime,heapq,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
THREADS=('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')
def save(path,row):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.partial');tmp.write_text(json.dumps(row,sort_keys=True,default=str)+'\n');tmp.replace(path)
def execute(job,deadline):
 kind,index,solver,spec=job
 directory=HERE/('generation/logs' if kind=='generation' else 'runs/'+solver)/f'{index:03d}'
 directory.mkdir(parents=True,exist_ok=True)
 if kind=='generation':cmd=[sys.executable,str(HERE/'generate.py'),'--index',str(index)];cap=300
 else:
  gen=json.loads((HERE/'generation/outcomes'/f'{index:03d}.json').read_text());budget={'tiny':120,'small':300,'medium':600}[spec['size']];cap=budget+90
  cmd=[sys.executable,str(HERE/'worker.py'),'--input',gen['path'],'--output',str(directory),'--solver',solver,'--budget',str(budget),'--ballast-budget','30','--cap',str(cap)]
 cap=max(.1,min(cap,deadline-time.time()))
 if kind=='solver':cmd[cmd.index('--cap')+1]=str(cap)
 env={**os.environ,**{name:'1' for name in THREADS},'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
 started=time.monotonic();error=None
 with (directory/'stdout.log').open('w') as out,(directory/'stderr.log').open('w') as err:
  child=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=out,stderr=err)
  save(directory/'pid.json',dict(pid=child.pid,owner_pid=os.getpid(),started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),command=cmd,cap_s=cap))
  try:code=child.wait(timeout=cap+5)
  except subprocess.TimeoutExpired:
   error='owned worker exceeded outer cap';child.terminate()
   try:code=child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();code=child.wait()
 if kind=='generation':
  path=HERE/'generation/outcomes'/f'{index:03d}.json';row=json.loads(path.read_text()) if path.exists() else dict(status='error',error=error or 'no generation checkpoint')
 else:row=json.loads((directory/'result.json').read_text()) if (directory/'result.json').exists() else dict(error=error or 'no solver checkpoint')
 row.update(index=index,requested_spec=spec,job_kind=kind,solver=solver,returncode=code,process_wall_s=time.monotonic()-started)
 if error:row['dispatch_error']=error
 save(directory/'done.json',row)
 print(json.dumps(dict(index=index,kind=kind,solver=solver,status=row.get('status',row.get('final_status')),completion=row.get('completion'),passed=row.get('clean_complete_pass'),wall_s=row['process_wall_s'],error=row.get('error'))),flush=True)
 return row

def main():
 p=argparse.ArgumentParser();p.add_argument('--workers',type=int,default=6);p.add_argument('--minutes',type=float,default=65);a=p.parse_args()
 manifest=json.loads((HERE/'generation/selection.json').read_text());specs=manifest['specs'];deadline=time.time()+a.minutes*60
 save(HERE/'dispatch-admission.json',dict(workers=a.workers,deadline_utc=datetime.datetime.fromtimestamp(deadline,datetime.timezone.utc).isoformat(),planned_instances=len(specs),solver_budgets=dict(tiny=120,small=300,medium=600),ballast_budget=30,repair=False))
 queue=[];serial=0;results=[];queued=set()
 def add(job,priority):
  nonlocal serial
  key=job[:3]
  if key not in queued:heapq.heappush(queue,(priority,serial,job));serial+=1;queued.add(key)
 def solvers(index,spec):
  choices=('exact','scalable','twostep') if spec['size']=='tiny' else ('scalable','twostep')
  for solver in choices:
   done=HERE/'runs'/solver/f'{index:03d}'/'done.json'
   if done.exists():results.append(json.loads(done.read_text()))
   else:add(('solver',index,solver,spec),1 if index%4==0 or spec['size']=='medium' else 3)
 for index,spec in enumerate(specs):
  outcome=HERE/'generation/outcomes'/f'{index:03d}.json'
  if outcome.exists():
   row=json.loads(outcome.read_text())
   if row.get('status') in ('generated','resumed'):solvers(index,spec)
   else:results.append(row)
  else:add(('generation',index,None,spec),0 if index%4==0 or spec['size']=='medium' else 2)
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  active={}
  while (queue or active) and time.time()<deadline+12:
   slots=a.workers
   control=HERE/'control.json'
   if control.exists():slots=min(6,max(1,int(json.loads(control.read_text()).get('workers',slots))))
   while queue and len(active)<slots and time.time()<deadline-2:
    _,_,job=heapq.heappop(queue);active[pool.submit(execute,job,deadline)]=job
   if not active:break
   ready,_=concurrent.futures.wait(active,timeout=2,return_when=concurrent.futures.FIRST_COMPLETED)
   for future in ready:
    job=active.pop(future)
    try:row=future.result()
    except Exception as exc:row=dict(job_kind=job[0],index=job[1],solver=job[2],error=f'{type(exc).__name__}: {exc}')
    results.append(row)
    if job[0]=='generation' and row.get('status') in ('generated','resumed'):solvers(job[1],job[3])
   save(HERE/'progress.json',dict(completed_jobs=len(results),active=len(active),queued=len(queue),remaining_s=max(0,deadline-time.time())))
  unstarted=[job for _,_,job in queue]
 save(HERE/'unstarted.json',unstarted);save(HERE/'dispatch-summary.json',dict(completed_jobs=len(results),unstarted=len(unstarted),ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
if __name__=='__main__':main()
