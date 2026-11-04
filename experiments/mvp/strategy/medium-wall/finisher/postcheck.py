"""Detached, serial and resumable independent checks after direct V4 exits."""
import argparse
import fcntl
import subprocess
import sys
import time
import run

parser=argparse.ArgumentParser();parser.add_argument('--detach',action='store_true');args=parser.parse_args()
if args.detach:
    assert (run.HERE/'completion.json').exists(), 'Direct V4 controller must complete first'
    command=[sys.executable,str(run.HERE/'postcheck.py')]
    with (run.HERE/'postcheck.log').open('a') as log:
        child=subprocess.Popen(command,cwd=run.ROOT,stdin=subprocess.DEVNULL,stdout=log,
            stderr=subprocess.STDOUT,start_new_session=True)
    run.save(run.HERE/'postcheck-launch.json',dict(pid=child.pid,command=command,unix=time.time()))
    print(child.pid)
    raise SystemExit(0)
with (run.HERE/'postcheck.lock').open('a') as lock:
    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    record=dict(pid=run.os.getpid(),started_unix=time.time(),checks=[],native_numeric_workers=1)
    run.save(run.HERE/'postcheck-owned.json',record)
    for task in run.read(run.HERE/'tasks.json'):
        command=[sys.executable,str(run.HERE/'check_representative.py'),task['name']]
        child=subprocess.Popen(command,cwd=run.ROOT)
        record['checks'].append(dict(name=task['name'],pid=child.pid,started_unix=time.time()))
        run.save(run.HERE/'postcheck-owned.json',record)
        status=child.wait();record['checks'][-1].update(exit_code=status,ended_unix=time.time())
        run.save(run.HERE/'postcheck-owned.json',record)
        if status:raise RuntimeError(f'representative checker failed: {task["name"]}, {status}')
    record.update(complete=True,ended_unix=time.time())
    run.save(run.HERE/'postcheck-completion.json',record)
