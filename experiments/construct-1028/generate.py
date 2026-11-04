"""Preselected fresh production specs; failed requests remain in denominator."""
from __future__ import annotations
import argparse, json, os, signal, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[k]='1'
sys.dont_write_bytecode=True
from cache_runtime import redirect_cache,enforce_scope

def main():
 p=argparse.ArgumentParser();p.add_argument('--index',type=int);p.add_argument('--manifest',action='store_true');a=p.parse_args()
 redirect_cache(HERE/'runtime');enforce_scope()
 from stowage.generate.instances_v3 import scenario_specs,shard,physics_metadata
 specs=scenario_specs(seed=10_281_000,profiles=['small','medium'],c2_screened=True)
 selected=[]
 for ship in ('feeder_500','feeder_800','feeder_1700'):
  for gen in ('random_yard','simulated_yard'):
   selected.append(next(s for s in specs if s['size']=='small' and s['vessel']==ship and s['generator']==gen))
  selected.append(next(s for s in specs if s['size']=='medium' and s['vessel']==ship and s['generator']=='simulated_yard'))
 if a.manifest:
  (HERE/'selection-fresh.json').write_text(json.dumps(selected,indent=2)+'\n');return
 start=time.monotonic();spec=selected[a.index];row={'spec':spec,'id':f'fresh-{a.index:03d}','source':'fresh'}
 def expired(*_):raise TimeoutError('fresh generation 300s cap')
 signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,300)
 try:row.update(shard((str(HERE/'generation/instances'),spec)))
 except Exception as e:row.update(status='error',error=f'{type(e).__name__}: {e}')
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  from stowage.solver.ballast_deadline import shutdown_worker
  shutdown_worker()
 row['wall_s']=time.monotonic()-start
 path=HERE/'generation/outcomes'/f'{a.index:03d}.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(row,indent=2,default=str)+'\n')
 print(json.dumps(row,default=str),flush=True)
if __name__=='__main__':main()
