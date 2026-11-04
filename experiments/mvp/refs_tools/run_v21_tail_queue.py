"""Run untouched official tail once, then promote byte-identical checkpoints."""
from pathlib import Path
from datetime import datetime, timezone
import ctypes
import errno
import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
TOOLS=ROOT/'experiments/mvp/refs_tools'
RUNS=ROOT/'experiments/mvp/runs'
PARENT=RUNS/'test-solver-small-v21-screen'
OUT=RUNS/'test-solver-small-v21-screen-tail'
LEDGER=ROOT/'experiments/fleet/ledger.md'
OWNER='mvp-refs/v21-tail'
OUT.mkdir(parents=True,exist_ok=True)
os.chdir(ROOT)
try: os.nice(10)
except OSError: pass
base=dict(pid=os.getpid(),nice=os.getpriority(os.PRIO_PROCESS,0),gpu=False)
def write(name,obj): (OUT/name).write_text(json.dumps(obj,indent=2)+'\n')
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def release(): LEDGER.write_text(''.join(l for l in LEDGER.read_text().splitlines(True) if not l.startswith('| '+OWNER+' |')))
manifest=json.loads((PARENT/'manifest.json').read_text())
selected=manifest['selection'][-3:]
extension='--remaining800' in sys.argv
if extension:
    selected=[x for x in manifest['selection'] if x['ship']=='feeder_800'
              and Path(x['path']).stem in ('seed-028100001','seed-028100005')
              and not (PARENT/'instances'/Path(x['path']).stem).exists()]
    if not selected:
        print('Remaining800 already started; no duplicate attempts',flush=True)
        sys.exit(0)
compute_name='tail-extension-compute.json' if extension else 'tail-compute.json'
receipt_name='promotion-extension-receipt.json' if extension else 'promotion-receipt.json'
selection_name='extension-selected-instances.json' if extension else 'selected-instances.json'
names=[str(x['path']) for x in selected]
write(selection_name,names)
workers=0
write(compute_name,dict(base,status='queued'))
while not workers:
    completed=sum(json.loads(p.read_text()).get('completed',False) for p in (PARENT/'instances').glob('*/record.json'))
    if completed>=(13 if extension else 9):
        write(compute_name,dict(base,status='not_started_parent_near_tail',completed=completed))
        sys.exit(0)
    if any((PARENT/'instances'/Path(x).stem).exists() for x in names):
        raise RuntimeError('Tail target already exists: refusing duplicate attempt')
    rows={}
    for line in LEDGER.read_text().splitlines():
        p=[x.strip() for x in line.split('|')]
        if len(p)>5 and p[1]!=OWNER:
            m=re.match(r'(\d+)',p[3])
            if m: rows[p[1]]=int(m[1])
    workers=min(2,max(0,16-sum(rows.values())),max(0,3-rows.get('mvp-refs/v21-small',0))) if os.getloadavg()[0]<16 else 0
    print(json.dumps(dict(event='admission',workers=workers,load=os.getloadavg()[0],completed=completed)),flush=True)
    if not workers:
        time.sleep(30)
        continue
    with LEDGER.open('a') as f:
        f.write(f'\n| {OWNER} | 6.3 official tail3 | {workers} | {3*workers} | no | prior150s/case; <=10min/2CPU or20min/1CPU; scopedV4/C2off/native1; PID{os.getpid()} | {datetime.now(timezone.utc).isoformat()} |\n')
command=[sys.executable,str(TOOLS/'run_small_v2.py'),'solver','--release',str(TOOLS/'small-v21-release-frozen.json'),
         '--source-root',str(TOOLS/'frozen-runtime-small-v21'),'--workers',str(workers),
         '--securing-screen','--seed-policy','instance','--instance-list',str(OUT/selection_name),
         '--output-dir',str(OUT)]
start=time.monotonic()
receipt=[]
try:
    proc=subprocess.Popen(command)
    write(compute_name,dict(base,status='running',worker_pid=proc.pid,command=command,cores=workers,estimate_s=1200/workers))
    result=proc.wait()
    if result: raise RuntimeError(f'Worker exit {result}')
    tail_manifest=json.loads((OUT/'manifest.json').read_text())
    if tail_manifest['config_sha256']!=manifest['config_sha256']:
        raise RuntimeError('Official config SHA mismatch: refusing promotion')
    if any((PARENT/'instances'/Path(x).stem).exists() for x in names):
        raise RuntimeError('Tail target unexpectedly exists: refusing all promotion')
    # macOS atomic exclusive rename prevents replacing even a concurrently created target.
    libc=ctypes.CDLL(None,use_errno=True)
    rename_excl=libc.renamex_np
    rename_excl.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint]
    rename_excl.restype=ctypes.c_int
    for item in selected:
        source=OUT/'instances'/Path(item['path']).stem
        row=json.loads((source/'record.json').read_text())
        if not row['completed'] or row['source_drift'] or row['config_sha256']!=manifest['config_sha256']:
            raise RuntimeError('Invalid tail checkpoint: refusing promotion')
        target=PARENT/'instances'/source.name
        temp=target.with_name('.promote-'+source.name+'-'+str(os.getpid()))
        shutil.copytree(source,temp)
        hashes={p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file()}
        if hashes!={p.relative_to(temp).as_posix():digest(p) for p in temp.rglob('*') if p.is_file()}:
            raise RuntimeError('Copied hashes mismatch')
        if rename_excl(os.fsencode(temp),os.fsencode(target),4):
            failure=ctypes.get_errno()
            raise OSError(failure,os.strerror(failure),str(target))
        receipt.append(dict(spec_key=item['spec_key'],source=str(source),target=str(target),sha256=hashes))
        write(receipt_name,dict(command=command,config_sha256=manifest['config_sha256'],promotions=receipt))
finally:
    elapsed=time.monotonic()-start
    write(compute_name,dict(base,status='complete' if len(receipt)==len(selected) else 'incomplete',
          command=command,cores=workers,ram_gb=3*workers,estimate_s=1200/workers,actual_s=elapsed,
          max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,promoted=len(receipt)))
    release()
    with (ROOT/'experiments/fleet/history.md').open('a') as f:
        f.write(f'\n| {OWNER} | {datetime.now(timezone.utc).isoformat()} | {workers}CPU/{3*workers}GB/noGPU; estimate{1200/workers:.1f}s vs actual{elapsed:.3f}s; test-solver-small-v21-screen-tail/{compute_name} |\n')
