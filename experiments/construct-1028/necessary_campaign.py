"""D60/necessary-direction continuation with immutable eligible/fresh specs."""
from __future__ import annotations
import argparse,ast,shutil,concurrent.futures,datetime,json,os,signal,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1];OUT=HERE/'necessary'
ENV={**os.environ,**{k:'1' for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')},'PYTHONDONTWRITEBYTECODE':'1'}
sys.dont_write_bytecode=True
if __name__=='__mp_main__':
 from cache_runtime import redirect_cache,enforce_scope
 redirect_cache(HERE/'runtime');enforce_scope()

def save(path,row):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.partial');tmp.write_text(json.dumps(row,indent=2,default=str)+'\n');tmp.replace(path)
def cargo_version():
 tree=ast.parse((ROOT/'src/stowage/versions.py').read_text())
 return next(n.value.value for n in tree.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='CARGO_GENERATOR_VERSION')
def post_d60():return tuple(int(n) for n in cargo_version().split('.'))>(6,0,0)
def run(command,directory,cap):
 directory.mkdir(parents=True,exist_ok=True);start=time.monotonic()
 with (directory/'stdout.log').open('w') as out,(directory/'stderr.log').open('w') as err:
  child=subprocess.Popen(command,cwd=ROOT,env=ENV,stdout=out,stderr=err)
  save(directory/'pid.json',{'pid':child.pid,'owner_pid':os.getpid(),'command':command,'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cap_s':cap})
  try:code=child.wait(timeout=cap+5)
  except subprocess.TimeoutExpired:
   child.terminate()
   try:code=child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();code=child.wait()
 return code,time.monotonic()-start

def fresh_specs():
 from stowage.generate.instances_v3 import scenario_specs
 specs=scenario_specs(seed=10_282_000,profiles=['small','medium'],c2_screened=True)
 result=[]
 for ship in ('feeder_500','feeder_800','feeder_1700'):
  for gen in ('random_yard','simulated_yard'):
   result.append(next(s for s in specs if s['size']=='small' and s['vessel']==ship and s['generator']==gen))
  result.append(next(s for s in specs if s['size']=='medium' and s['vessel']==ship and s['generator']=='simulated_yard'))
 return result

def generate_one(index):
 from cache_runtime import redirect_cache,enforce_scope
 redirect_cache(HERE/'runtime');enforce_scope()
 if not post_d60():raise RuntimeError('D60 cargo version has not landed')
 from stowage.generate.instances_v3 import shard,physics_metadata
 from stowage.versions import CARGO_GENERATOR_VERSION
 spec=json.loads((OUT/'selection-fresh.json').read_text())[index];start=time.monotonic();row={'spec':spec,'id':f'd60fresh-{index:03d}','source':'fresh_D60','cargo_generator_version':CARGO_GENERATOR_VERSION,'physics_start':physics_metadata(c2_screened=True)}
 def expired(*_):raise TimeoutError('D60 generation 300s cap')
 signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,300)
 try:
  row.update(shard((str(OUT/'generation/instances'),spec)))
  if row.get('status') in ('generated','resumed'):
   data=json.loads(Path(row['path']).read_text());version=data['provenance']['versions'].get('cargo_generator', CARGO_GENERATOR_VERSION)
   row['instance_cargo_version_recorded']='cargo_generator' in data['provenance']['versions']
   if version!=CARGO_GENERATOR_VERSION:raise RuntimeError(f'fresh generator version mismatch {version}')
   ids=set(data['load_list']);bad=[b['id'] for b in data['containers'] if b['id'] in ids and b['gross_mass_kg']>30500]
   if bad:raise RuntimeError(f'D60 fresh booking exceeds declared securing mass rating: {bad}')
 except Exception as e:row.update(status='error',error=f'{type(e).__name__}: {e}')
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  from stowage.solver.ballast_deadline import shutdown_worker
  shutdown_worker()
 row.update(wall_s=time.monotonic()-start,physics_end=physics_metadata(c2_screened=True));save(OUT/'generation/outcomes'/f'{index:03d}.json',row)

def solve_one(row,solver='construct'):
 directory=OUT/'runs'/solver/row['id']
 if (directory/'done.json').exists():return json.loads((directory/'done.json').read_text())
 budget=900 if row['spec']['size']=='medium' else 300
 cmd=[sys.executable,str(HERE/'worker.py'),'--input',row['path'],'--output',str(directory),'--solver',solver,'--budget',str(budget),'--cap',str(budget+120)]
 code,wall=run(cmd,directory,budget+120)
 result=json.loads((directory/'result.json').read_text()) if (directory/'result.json').exists() else {'error':'no checkpoint'}
 result.update(id=row['id'],spec=row['spec'],source=row.get('source','saved_eligible'),returncode=code,process_wall_s=wall);save(directory/'done.json',result)
 print(json.dumps({k:result.get(k) for k in ('id','solver_status','clean_complete_pass','process_wall_s','error')}),flush=True)
 return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--phase',choices=['prepare','saved','fresh','integration'],default='saved');p.add_argument('--workers',type=int,default=2);p.add_argument('--generate-one',type=int);p.add_argument('--ids',nargs='+');p.add_argument('--solver',default='construct');p.add_argument('--retry-unavailable',action='store_true');p.add_argument('--new-generation-epoch',action='store_true');a=p.parse_args()
 if a.generate_one is not None:return generate_one(a.generate_one)
 if a.phase=='prepare':
  if a.new_generation_epoch and (OUT/'generation').exists():
   archive=OUT/'epochs'/('generation-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S'))
   archive.mkdir(parents=True,exist_ok=True);shutil.move(str(OUT/'generation'),str(archive/'generation'))
  save(OUT/'selection-fresh.json',fresh_specs());deadline=time.monotonic()+1800
  save(OUT/'prepare-pid.json',{'pid':os.getpid(),'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'wait_for':'cargo version >6.0.0'})
  while not post_d60():
   save(OUT/'prepare-wait.json',{'cargo_version':cargo_version(),'waiting':True})
   if time.monotonic()>=deadline:raise TimeoutError('D60 version wait exceeded30min; no preD60 fresh instance used')
   time.sleep(10)
  save(OUT/'prepare-wait.json',{'cargo_version':cargo_version(),'waiting':False})
  def job(index):
   directory=OUT/'generation/logs'/f'{index:03d}';code,wall=run([sys.executable,str(Path(__file__).resolve()),'--generate-one',str(index)],directory,330)
   return {'index':index,'returncode':code,'wall_s':wall}
  with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:rows=list(pool.map(job,range(9)))
  save(OUT/'generation/dispatch.json',rows);return
 if a.phase=='saved':rows=json.loads((OUT/'selection-saved.json').read_text())['eligible']
 else:rows=[json.loads(p.read_text()) for p in sorted((OUT/'generation/outcomes').glob('*.json')) if json.loads(p.read_text()).get('status') in ('generated','resumed')]
 if a.ids:rows=[r for r in rows if r['id'] in a.ids]
 if a.retry_unavailable:
  archive=OUT/'epochs'/('qualification-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S'))
  for row in rows:
   directory=OUT/'runs'/a.solver/row['id'];done=directory/'done.json'
   if done.exists() and 'qualification unavailable' in json.loads(done.read_text()).get('solver_metrics',{}).get('attribution',''):
    target=archive/'runs'/a.solver/row['id'];target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(directory),str(target))
 save(OUT/f'admission-{a.phase}-{a.solver}.json',{'workers':a.workers,'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'requests':rows,'direction':'necessary','repair':False})
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(lambda r:solve_one(r,a.solver),rows))
 save(OUT/f'{a.phase}-{a.solver}-results.json',results)
if __name__=='__main__':main()
