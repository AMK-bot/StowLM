"""Hand the unchanged gated queue to an existing Metal-capable host session."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]


def launch(extra=()):
    from llm.exit.runtime import preflight_mlx_device
    device=preflight_mlx_device()  # Before cancellation/queue mutation or a smoke claim.
    out=ROOT/'experiments/mvp/exit/deployment'
    out.mkdir(parents=True,exist_ok=True)
    with (out/'.deployment.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('existing controller still active; wait for cooperative cancellation') from error
        fcntl.flock(lock,fcntl.LOCK_UN)
    (out/'deployment.cancel').unlink(missing_ok=True)
    command=[sys.executable,str(ROOT/'experiments/mvp/exit/deploy_when_ready.py'),
        '--parent','experiments/mvp/sft/stowlm-v1/adapter',
        '--completion-marker','experiments/mvp/sft/stowlm-v1/DONE.json',
        '--gens','8','--maps','48','--k','8','--B','8','--population','2','--steps','120',*extra]
    env=os.environ.copy();env['STOWLM_MLX_CACHE_LIMIT_GB']='2'
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                 'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):env[name]='1'
    stamp=datetime.now(timezone.utc).isoformat()
    (out/'deployment-status.json').write_text(json.dumps(dict(state='host_controller_launching',utc=stamp)))
    with (out/'controller.log').open('a') as stream:
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=stream,
            stderr=subprocess.STDOUT,start_new_session=True)
    record=dict(pid=process.pid,command=command,utc=stamp,decision='D123 Metal-capable host handoff',
                cache_limit_gb=2,device=device)
    (out/'controller-launch.json').write_text(json.dumps(record,indent=2)+'\n')
    with (out/'.gen0-watcher.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:pass  # Existing detached watcher retains ownership.
        else:
            fcntl.flock(lock,fcntl.LOCK_UN)
            with (out/'gen0-watcher.log').open('a') as stream:
                watcher=subprocess.Popen([sys.executable,str(ROOT/'experiments/mvp/exit/watch_gen0.py')],
                    cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            record['watcher_pid']=watcher.pid
    print(json.dumps(record),flush=True)
    return record


if __name__=='__main__':launch(sys.argv[1:])
