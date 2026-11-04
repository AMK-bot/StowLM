"""Serial catch-up for medium candidates published during the first audit.

No physics runs here. Wait for the active worker, freeze a fresh inventory,
and hand off cooperatively from the original supplemental worker before
launching the same source-bound resumable audit. Stops when caught up.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OUT = HERE/'extensions'
ROOT = HERE.parents[4]
sys.path.insert(0,str(HERE))
from medium_jobs import inventory


def write(path,row):
    temp=path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps(row,indent=1,sort_keys=True)+'\n')
    temp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def pending(inv):
    missing=[]
    for job in inv['jobs']:
        arm=job['arm'].replace(' ','_').replace('/','_')
        sample='na' if job['sample'] is None else f'{job["sample"]:02d}'
        path=OUT/'rows/medium'/arm/job['instance_id']/f'sample-{sample}.json'
        row=json.loads(path.read_text()) if path.exists() else {}
        if (row.get('source_sha256') != job['source_sha256']
                or row.get('input_sha256') != job['input_sha256']):
            missing.append({k:v for k,v in job.items() if k!='plan'})
    return missing


def main():
    guard=(OUT/'followup.lock').open('a+')
    fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (OUT/'followup.pid').write_text(str(os.getpid())+'\n')
    while True:
        status=json.loads((OUT/'status.json').read_text())
        if status['status']=='failed':
            write(OUT/'followup-status.json',dict(status='blocked_worker_error',updated_at=now(),worker=status))
            return 1
        if status['status']!='complete':
            write(OUT/'followup-status.json',dict(status='waiting_current_serial_batch',updated_at=now(),worker=status))
            time.sleep(20)
            continue
        inv=inventory()
        missing=pending(inv)
        write(OUT/'followup-inventory.json',inv)
        write(OUT/'pending-medium.json',dict(created_at=now(),count=len(missing),jobs=missing))
        if not missing:
            write(OUT/'followup-status.json',dict(status='caught_up',updated_at=now(),available_plans=len(inv['jobs'])))
            return 0
        handoff_at=now()
        write(HERE/'extensions-active.json',dict(status='followup_handoff',pid=os.getpid(),created_at=handoff_at))
        # A fresh waiting acknowledgment prevents overlap even if the original
        # worker began a spare-core sample just before the marker was renewed.
        while True:
            old=json.loads((HERE/'part1-status.json').read_text())
            if ((old.get('status')=='supplementary_waiting' and old.get('updated_at','')>=handoff_at
                    and old.get('admission',{}).get('extension_priority') is True)
                    or (HERE/'part1.DONE').exists()):
                break
            time.sleep(5)
        with (OUT/'worker.log').open('a') as log:
            process=subprocess.Popen([sys.executable,'-B',str(HERE/'extended_audit.py')],cwd=ROOT,
                  env=os.environ.copy(),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        (OUT/'worker.pid').write_text(str(process.pid)+'\n')
        write(OUT/'followup-status.json',dict(status='serial_followup_started',updated_at=now(),pid=process.pid,pending=len(missing)))
        code=process.wait()
        if code:
            write(OUT/'followup-status.json',dict(status='worker_failed',updated_at=now(),pid=process.pid,exit_code=code))
            return code


if __name__=='__main__':
    raise SystemExit(main())
