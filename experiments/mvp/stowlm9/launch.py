"""Detached launch/resume. Only records and starts this job's own processes."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import uuid

from run import HERE, ROOT, prepare, write


def main():
    manifest = prepare()
    launch_id = uuid.uuid4().hex
    children = []
    for stream in ('rule','frontier'):
        if stream=='frontier' and (HERE/'frontier/PAUSED.json').exists():
            children.append(dict(stream=stream,status='paused by leader; explicit resume required'))
            continue
        directory = HERE/stream
        directory.mkdir(exist_ok=True)
        with (directory/'controller.lock').open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                children.append(dict(stream=stream,status='already running'))
                continue
        command = ['uv','run','--offline','--no-sync','python',str(HERE/'run.py'),'supervise',stream]
        with (directory/'controller.log').open('a') as log:
            child = subprocess.Popen(command,cwd=ROOT,env=dict(os.environ),stdin=subprocess.DEVNULL,
                                     stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record = dict(stream=stream,pid=child.pid,command=command,manifest_id=manifest['manifest_id'],launch_id=launch_id)
        write(directory/'launches'/f'{launch_id}.json',record)
        children.append(record)
    print(json.dumps(children,indent=1),flush=True)


if __name__=='__main__':
    main()
