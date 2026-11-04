"""Serial resumable owned-child runner; one admitted CPU per controller."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from worker import HERE,ROOT,save,sha

def run(manifest):
    spec=json.loads(manifest.read_text());out=Path(spec['directory']);out.mkdir(parents=True,exist_ok=True)
    state=dict(started_utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),manifest_sha256=sha(manifest),worker_sha256=sha(spec['worker']),jobs=[],completed=False)
    start=time.perf_counter();save(out/'controller.json',state)
    for job in spec['jobs']:
        folder=Path(job['directory']);folder.mkdir(parents=True,exist_ok=True);result=folder/'result.json'
        if result.exists() and json.loads(result.read_text()).get('completed'):
            state['jobs'].append(dict(case=job['case']['case'],skipped_completed=True,result=str(result)));save(out/'controller.json',state);continue
        if result.exists():
            prior=folder/f'interrupted-{time.time_ns()}';prior.mkdir()
            for item in list(folder.iterdir()):
                if item.is_file():item.rename(prior/item.name)
        save(folder/'job.json',job);command=[sys.executable,spec['worker'],'--job',str(folder/'job.json')]
        record=dict(case=job['case']['case'],command=command,started_utc=datetime.now(timezone.utc).isoformat(),result=str(result));state['jobs'].append(record)
        with (folder/'worker.log').open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'UV_CACHE_DIR':str(ROOT/'.uv-cache'),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'})
            record['pid']=process.pid;save(out/'controller.json',state);began=time.perf_counter()
            try:record['exit_code']=process.wait(timeout=spec.get('watchdog_s',650))
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                record.update(exit_code=process.returncode,watchdog=True)
            record.update(reaped=True,wall_s=time.perf_counter()-began,ended_utc=datetime.now(timezone.utc).isoformat());save(out/'controller.json',state)
    state.update(completed=True,wall_s=time.perf_counter()-start,ended_utc=datetime.now(timezone.utc).isoformat());save(out/'controller.json',state)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);a=p.parse_args();run(a.manifest)
