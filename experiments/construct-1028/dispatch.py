"""Bounded owned-PID resumable experiment dispatcher; workers are one core."""
from __future__ import annotations
import argparse,concurrent.futures,datetime,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
ENV={**os.environ,**{k:'1' for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')},'PYTHONDONTWRITEBYTECODE':'1'}
def save(path,row):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.partial');tmp.write_text(json.dumps(row,indent=2,default=str)+'\n');tmp.replace(path)
def execute(job):
 kind,ident,path,solver,budget=job
 directory=HERE/('generation/logs' if kind=='gen' else 'runs/'+solver)/ident
 if (directory/'done.json').exists():return json.loads((directory/'done.json').read_text())
 directory.mkdir(parents=True,exist_ok=True)
 if kind=='gen':cmd=[sys.executable,str(HERE/'generate.py'),'--index',str(path)];cap=330
 else:
  cap=budget+120
  cmd=[sys.executable,str(HERE/'worker.py'),'--input',str(path),'--output',str(directory),'--solver',solver,'--budget',str(budget),'--cap',str(cap)]
 start=time.monotonic()
 with (directory/'stdout.log').open('w') as out,(directory/'stderr.log').open('w') as err:
  child=subprocess.Popen(cmd,cwd=ROOT,env=ENV,stdout=out,stderr=err)
  save(directory/'pid.json',{'pid':child.pid,'owner_pid':os.getpid(),'command':cmd,'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
  try:code=child.wait(timeout=cap+10)
  except subprocess.TimeoutExpired:
   child.terminate()
   try:code=child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();code=child.wait()
 result_path=(HERE/'generation/outcomes'/f'{path:03d}.json') if kind=='gen' else directory/'result.json'
 row=json.loads(result_path.read_text()) if result_path.exists() else {'error':'no worker checkpoint'}
 row.update(id=ident,returncode=code,process_wall_s=time.monotonic()-start)
 save(directory/'done.json',row)
 print(json.dumps({k:row.get(k) for k in ('id','solver','status','clean_complete_pass','error','process_wall_s')}),flush=True)
 return row

def main():
 p=argparse.ArgumentParser();p.add_argument('--phase',choices=['prepare','tiny','saved','fresh'],required=True);p.add_argument('--workers',type=int,default=4);p.add_argument('--solver',default='construct');p.add_argument('--limit',type=int);p.add_argument('--ids',nargs='+');a=p.parse_args()
 jobs=[]
 if a.phase=='prepare':
  subprocess.run([sys.executable,str(HERE/'generate.py'),'--manifest'],env=ENV,check=True)
  for i,_ in enumerate(json.loads((HERE/'selection-fresh.json').read_text())):jobs.append(('gen',f'{i:03d}',i,None,300))
 else:
  saved=json.loads((HERE/'selection-saved.json').read_text())
  rows=saved if a.phase in ('tiny','saved') else [json.loads(p.read_text()) for p in sorted((HERE/'generation/outcomes').glob('*.json'))]
  for row in rows:
   spec=row['spec']
   if a.phase=='tiny' and spec['size']!='tiny':continue
   if a.phase=='saved' and spec['size']=='tiny':continue
   if not row.get('path') or row.get('status') in ('error','rejected'):continue
   budget={'tiny':120,'small':300,'medium':900}[spec['size']]
   jobs.append(('solver',row['id'],row['path'],'scalable' if a.phase=='tiny' else a.solver,budget))
 if a.ids:jobs=[job for job in jobs if job[1] in a.ids]
 if a.limit:jobs=jobs[:a.limit]
 save(HERE/f'admission-{a.phase}.json',{'jobs':jobs,'workers':a.workers,'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:rows=list(pool.map(execute,jobs))
 save(HERE/f'summary-{a.phase}-{a.solver}.json',rows)
if __name__=='__main__':main()
