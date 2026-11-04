"""Leader-authorized extra5 ablation; stop queue before main campaign reaches it."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT/'experiments/mvp/refs_tools'
OUT = ROOT/'experiments/mvp/runs/test-solver-small-v21-noscreen-extra'
LEDGER = ROOT/'experiments/fleet/ledger.md'
OWNER = 'mvp-refs/v21-extra'
OUT.mkdir(parents=True, exist_ok=True)
os.chdir(ROOT)
try:
    os.nice(10)
except OSError:
    pass


def record(value):
    (OUT/'extra-compute.json').write_text(json.dumps(value, indent=2)+'\n')


def reservations():
    rows = {}
    for line in LEDGER.read_text().splitlines():
        parts = [p.strip() for p in line.split('|')]
        if len(parts)>5 and parts[1] != OWNER:
            match = re.match(r'(\d+)', parts[3])
            if match:
                rows[parts[1]] = int(match[1])
    return rows


def release():
    LEDGER.write_text(''.join(line for line in LEDGER.read_text().splitlines(True)
                             if not line.startswith('| '+OWNER+' |')))


base = dict(pid=os.getpid(), gpu=False, nice=os.getpriority(os.PRIO_PROCESS,0))
record(dict(base, status='queued'))
workers = 0
while not workers:
    completed = sum(json.loads(p.read_text()).get('completed', False)
                    for p in (OUT.parent/'test-solver-small-v21-screen/instances').glob('*/record.json'))
    if completed >= 10:
        record(dict(base, status='not_started_parent_near_extra', official_completed=completed))
        sys.exit(0)
    rows = reservations()
    free = max(0, 16-sum(rows.values()))
    parent = rows.get('mvp-refs/v21-small', 0)
    workers = min(2, free, max(0,3-parent)) if os.getloadavg()[0]<16 else 0
    print(json.dumps(dict(event='admission', free=free, parent=parent, workers=workers,
                          load=os.getloadavg()[0], official_completed=completed)), flush=True)
    if not workers:
        time.sleep(30)
        continue
    with LEDGER.open('a') as f:
        f.write(f'\n| {OWNER} | 6.3 ablation extra5 | {workers} | {workers*3} | no | prior150s/case; <=25min/1CPU or13min/2CPU; native1/scopedV4/C2off; PID{os.getpid()} | {datetime.now(timezone.utc).isoformat()} |\n')
    after = reservations()
    if sum(after.values())+workers>16 or after.get('mvp-refs/v21-small',0)+workers>3:
        release()
        workers = 0

command = [sys.executable,str(TOOLS/'run_small_v2.py'),'solver',
           '--release',str(TOOLS/'small-v21-release-frozen.json'),
           '--source-root',str(TOOLS/'frozen-runtime-small-v21'),
           '--workers',str(workers), '--instance-list',str(TOOLS/'small-v21-extra-instances.txt'),
           '--output-dir',str(OUT)]
start=time.monotonic()
record(dict(base,status='running',cores=workers,ram_gb=3*workers,estimate_s=1500/workers,command=command))
try:
    child=subprocess.Popen(command)
    record(dict(base,status='running',worker_pid=child.pid,cores=workers,ram_gb=3*workers,
                estimate_s=1500/workers,command=command))
    result=child.wait()
finally:
    elapsed=time.monotonic()-start
    record(dict(base,status='complete' if locals().get('result')==0 else 'incomplete',
                exit_code=locals().get('result'), cores=workers,ram_gb=3*workers,
                estimate_s=1500/workers,actual_s=elapsed,command=command,
                max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))
    release()
    with (ROOT/'experiments/fleet/history.md').open('a') as f:
        f.write(f'\n| {OWNER} | {datetime.now(timezone.utc).isoformat()} | {workers}CPU/{3*workers}GB/noGPU; estimate{1500/workers:.1f}s vs actual{elapsed:.3f}s; test-solver-small-v21-noscreen-extra/extra-compute.json |\n')
