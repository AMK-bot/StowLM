"""Shared admission and resumable command accounting for the pre-final run."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
THREADS = ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','BLIS_NUM_THREADS')

def abort_check():
    if (ROOT/'ABORT').exists():
        raise RuntimeError('qualification aborted: '+(ROOT/'ABORT').read_text())

def alive(pid):
    try: os.kill(pid,0); return True
    except ProcessLookupError: return False
    except PermissionError: return True  # Other tool sandbox: retain its reservation.

@contextmanager
def reserve(name, cores=1):
    token=uuid.uuid4().hex
    began=time.monotonic()
    while True:
        abort_check()
        with (ROOT/'compute.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            p=ROOT/'compute-active.json'
            active=json.loads(p.read_text()) if p.exists() else []
            active=[r for r in active if alive(r['holder_pid'])]
            capacity=json.loads((ROOT/'compute-capacity.json').read_text())['workers']
            if not 1<=cores<=capacity<=9: raise ValueError('invalid CPU admission')
            if len(active)+cores<=capacity:
                active.extend(dict(holder_pid=os.getpid(),name=name,token=token,core=i)
                              for i in range(cores))
                p.write_text(json.dumps(active,indent=2)+'\n')
                break
            p.write_text(json.dumps(active,indent=2)+'\n')
        time.sleep(.3)
    try: yield time.monotonic()-began
    finally:
        with (ROOT/'compute.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            p=ROOT/'compute-active.json'
            active=json.loads(p.read_text())
            p.write_text(json.dumps([r for r in active if r.get('token')!=token],indent=2)+'\n')

def run(name, args, *, cores=1, env_extra=None, reuse=True):
    """Run Python script or -m module, all numerical children share <=9 slots."""
    logs=ROOT/'compute-logs'; logs.mkdir(exist_ok=True)
    name=name.replace('/','--')
    path=logs/(name+'.execution.json')
    argv=list(map(str,args))
    if argv and Path(argv[0]).resolve()==Path(sys.executable).resolve(): argv=argv[1:]
    command=['/usr/bin/nice','-n','10',sys.executable,str(ROOT/'compute_worker.py'),*argv]
    if path.exists():
        prior=json.loads(path.read_text())
        if reuse and prior.get('exit_code')==0 and prior['command']==command:
            return prior
        raise ValueError('preserve prior command record before retry: '+str(path))
    with reserve(name,cores) as wait:
        abort_check()
        env=os.environ.copy(); env.update({k:'1' for k in THREADS})
        if env_extra: env.update(env_extra)
        began=time.monotonic()
        record=dict(name=name,command=command,cores=cores,admission_wait_seconds=wait,
                    start_utc=datetime.now(timezone.utc).isoformat())
        with (logs/(name+'.log')).open('w') as log:
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env)
            record['pid']=process.pid;path.write_text(json.dumps(record,indent=2)+'\n')
            _,status,usage=os.wait4(process.pid,0)
            code=os.waitstatus_to_exitcode(status);process.returncode=code
        record.update(exit_code=code,seconds=time.monotonic()-began,
                      finished_utc=datetime.now(timezone.utc).isoformat(),
                      max_rss_bytes=usage.ru_maxrss,cpu_seconds=usage.ru_utime+usage.ru_stime)
        path.write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps(record),flush=True)
        if code: raise RuntimeError(f'{name} failed ({code}); see {logs/name}.log')
        abort_check()
        return record

if __name__=='__main__':
    run(sys.argv[1],sys.argv[2:])
