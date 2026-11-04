"""Run one admitted owned child with an OS CPU ceiling; no foreign signals."""
import argparse
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime,timezone
from pathlib import Path
from worker import HERE,ROOT,sha,save,snapshot


def child_cpu_limit():
    resource.setrlimit(resource.RLIMIT_CPU,(65,70))


def main(manifest):
    spec=json.loads(manifest.read_text());assert len(spec['jobs'])==1
    job=spec['jobs'][0];folder=Path(job['directory']);folder.mkdir(parents=True,exist_ok=True)
    result=folder/'result.json';assert not result.exists(),'Use a fresh private pilot output'
    frozen=json.loads(Path(job['freeze_manifest']).read_text());before=snapshot(True)
    assert before==frozen['lookahead_runtime_sources']
    assert sha(spec['worker'])==frozen['worker_sha256']
    save(folder/'job.json',job);save(folder/'launcher-source-before.json',before)
    path=Path(spec['directory'])/'limited-launcher.json';assert not path.exists(),'Preserve the original pilot attempt; use a fresh labelled root'
    start=time.perf_counter();c0=time.process_time();usage0=resource.getrusage(resource.RUSAGE_CHILDREN)
    row=dict(launcher_pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),manifest_sha256=sha(manifest),launcher_sha256=sha(__file__),worker_sha256=sha(spec['worker']),completed=False,cpu_limit=dict(worker_internal_s=job.get('cpu_cap_s'),worker_internal_policy='Best-effort SIGPROF timer may be caught by runtime; not a strict limit.',os_soft_s=65,os_hard_s=70,mechanism='RLIMIT_CPU lowered in this owned child only before exec'),native_threads=1,gpu=False)
    save(path,row)
    command=[sys.executable,spec['worker'],'--job',str(folder/'job.json')];row['command']=command
    try:
        with (folder/'worker.log').open('w')as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,preexec_fn=child_cpu_limit,
                                     env={**os.environ,'UV_CACHE_DIR':str(ROOT/'.uv-cache'),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'})
            row['child_pid']=process.pid;save(path,row)
            row['exit_code']=process.wait();row['reaped']=True
    except Exception as exc:
        row['launcher_error']=f'{type(exc).__name__}: {exc}'
    finally:
        usage=resource.getrusage(resource.RUSAGE_CHILDREN);after=snapshot(True);save(folder/'launcher-source-after.json',after)
        raw=json.loads(result.read_text())if result.exists()else{}
        row.update(completed=True,ended_utc=datetime.now(timezone.utc).isoformat(),wall_s=time.perf_counter()-start,
                   launcher_cpu_s=time.process_time()-c0,child_cpu_s=(usage.ru_utime+usage.ru_stime)-(usage0.ru_utime+usage0.ru_stime),
                   child_lifetime_peak_rss_bytes=usage.ru_maxrss,source_valid=before==after,
                   worker_result_completed=raw.get('completed',False),worker_statuses=[r.get('status')for r in raw.get('rollouts',[])],
                   worker_result_sha256=sha(result)if result.exists()else None)
        if row.get('exit_code')!=0 or not raw.get('completed'):
            row.update(aborted_partial=True,termination='OS-resource-limit-or-worker-exit; never classified as a mask dead end'if row.get('exit_code',0)<0 else'launcher/worker incomplete or failed')
            save(folder/'aborted-partial.json',dict(launcher=str(path),**row))
        else:row.update(aborted_partial=False,termination='Worker returned; inspect explicit per-rollout status for internal cpu_limit/error.')
        save(path,row);print(json.dumps(row))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True);args=parser.parse_args();main(args.manifest)
