"""One-core detached unit verification; does not execute the canary campaign."""
import datetime,json,os,subprocess,time
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
command=['uv','run','pytest','experiments/campaign-g1','-o','addopts=','-q',
         '--basetemp=/private/tmp/w2-canary-fixes-tests']
env=os.environ.copy()
env.update(UV_CACHE_DIR=str(ROOT/'.uv-cache'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',
           VECLIB_MAXIMUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
start=time.monotonic()
with (HERE/'canary-fixes-suite.log').open('wb') as log:
    child=subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    (HERE/'canary-fixes-suite.pid').write_text(str(child.pid)+'\n')
    state=dict(pid=child.pid,launcher_pid=os.getpid(),command=command,
        started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),cores=1)
    record=HERE/'canary-fixes-suite-process.json'
    record.write_text(json.dumps(state,indent=2)+'\n')
    try:code=child.wait(timeout=180)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:child.kill();child.wait()
        code=124
state.update(exit_code=code,wall_seconds=time.monotonic()-start)
record.write_text(json.dumps(state,indent=2)+'\n')
print(json.dumps(state),flush=True)
