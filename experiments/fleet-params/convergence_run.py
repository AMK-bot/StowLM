"""Measure explicitly requested fleet convergence jobs; single-thread workers."""
from __future__ import annotations
import argparse
import datetime
import json
import os
from pathlib import Path
import resource
import subprocess
import time

parser=argparse.ArgumentParser()
parser.add_argument('job',choices=('warm','test'))
parser.add_argument('--workers',type=int,choices=range(1,5),default=2)
options=parser.parse_args()
here=Path(__file__).resolve().parent
command=(['uv','run','python','-m','stowage.cache_warm','--workers',str(options.workers)] if options.job=='warm'
         else ['uv','run','pytest','-q','tests/params','tests/vessel'])
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
    os.environ[key]='1'
started=datetime.datetime.now().astimezone().isoformat();start=time.monotonic()
with (here/f'convergence-{options.job}.log').open('w') as log:
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    pid_file=here/f'convergence-{options.job}-running.json'
    pid_file.write_text(json.dumps(dict(pid=process.pid,started=started,command=command))+'\n')
    for line in process.stdout:
        log.write(line);log.flush()
        if options.job=='warm':
            try:
                item=json.loads(line)
                print(json.dumps({k:v for k,v in item.items() if k in ('ship','complete','errors','warnings','elapsed_seconds','report')}),flush=True)
            except ValueError:print(line[:1000],end='',flush=True)
        else:print(line,end='',flush=True)
    returncode=process.wait()
result=dict(command=command,started=started,finished=datetime.datetime.now().astimezone().isoformat(),returncode=returncode,wall_s=time.monotonic()-start,peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,threads=1)
(here/f'convergence-{options.job}.json').write_text(json.dumps(result,indent=2)+'\n')
pid_file.unlink()
if options.job=='warm':
    events=[]
    for line in (here/'convergence-warm.log').read_text().splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    rows=[event for event in events if 'ship' in event]
    final=next((event for event in reversed(events) if 'complete' in event and 'report' in event),{})
    (here/'convergence-cache-summary.json').write_text(json.dumps(dict(
        complete=all(row.get('complete',False) for row in rows) and len(rows)==3,
        elapsed_seconds=final.get('elapsed_seconds'),ships=rows),indent=2)+'\n')
print('RESULT',json.dumps(result),flush=True)
raise SystemExit(returncode)
