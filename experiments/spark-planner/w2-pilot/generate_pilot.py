"""Generate fixed pilot requests under active rules, retaining every outcome."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.dont_write_bytecode=True
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.partial')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True,default=str)+'\n');tmp.replace(path)

def runtime():
    module_spec=importlib.util.spec_from_file_location('pilot_cache_runtime',ROOT/'experiments/solver-diag-1028/cache_runtime.py')
    module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
    module.HERE=HERE;module.ROOT=ROOT
    module.redirect_cache(HERE/'runtime')
    return module

class GenerationDeadline(BaseException): pass

def worker(index):
    runtime()
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate.instances_v3 import physics_metadata,shard
    original=json.loads((HERE/'selection.json').read_text())['specs'][index]
    started=time.perf_counter()
    with rule_freeze_v1(enabled=True):
        before=physics_metadata(c2_screened=original['c2_screened'])
        spec={**original,'physics_sha256':before['sha256']}
        row=dict(index=index,spec=spec,label='pilot, pre-freeze epoch, not a result',generation_epoch=before['sha256'],physics_start=before,started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        def expired(signum,frame):raise GenerationDeadline('pilot generation exceeded its outer wall cap (PILOT_GEN_CAP_S)')
        signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,float(os.environ.get('PILOT_GEN_CAP_S','300')))
        try:
            row.update(shard((str(HERE/'instances'),spec)))
        except (Exception,GenerationDeadline) as exc:
            row.update(status='error',error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
        finally:signal.setitimer(signal.ITIMER_REAL,0)
        row.update(physics_end=physics_metadata(c2_screened=spec['c2_screened']))
        row['source_changed']=row['physics_end']!=before
    row['elapsed_s']=time.perf_counter()-started
    save(HERE/'generation-outcomes'/f'{index:03d}.json',row)
    print(json.dumps({k:row.get(k) for k in ('index','status','path','error_type','error','elapsed_s','source_changed')}),flush=True)
    try:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
    except Exception:pass

def main():
    if len(sys.argv)>1:worker(int(sys.argv[1]));return
    module=runtime()
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.generate.instances_v3 import physics_metadata
    selection=json.loads((HERE/'selection.json').read_text())
    with rule_freeze_v1(enabled=True):
        current={str(c2).lower():physics_metadata(c2_screened=c2) for c2 in (False,True)}
    selection['physics_at_generation_dispatch']=current
    selection['generation_dispatch_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    selection['generation_epoch_policy']='Refresh physics independently at start of each fixed request; preserve per-request start/end hashes. Source changes fail the unchanged-source producer gate. No outcomes replaced.'
    save(HERE/'selection.json',selection)
    queue=list(range(len(selection['specs'])));running={};pids=[]
    with (HERE/'generation-results.jsonl').open('a') as output:
        while queue or running:
            while queue and len(running)<3:
                i=queue.pop(0);log=(HERE/'generation-outcomes'/f'{i:03d}.log');log.parent.mkdir(parents=True,exist_ok=True)
                handle=log.open('a');process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),str(i)],stdout=handle,stderr=subprocess.STDOUT,env=os.environ.copy())
                running[i]=(process,handle);pids.append(dict(index=i,pid=process.pid));save(HERE/'generation-pids.json',pids)
            for i,(process,handle) in list(running.items()):
                code=process.poll()
                if code is None:continue
                handle.close();del running[i]
                path=HERE/'generation-outcomes'/f'{i:03d}.json'
                row=json.loads(path.read_text()) if path.exists() else dict(index=i,status='worker_error',exit_code=code,spec=selection['specs'][i],log=str(HERE/'generation-outcomes'/f'{i:03d}.log'))
                output.write(json.dumps(row,sort_keys=True,default=str)+'\n');output.flush();os.fsync(output.fileno())
                print(json.dumps({k:row.get(k) for k in ('index','status','path','error_type','error','elapsed_s','source_changed')}),flush=True)
            if running:time.sleep(.25)

if __name__=='__main__':main()
