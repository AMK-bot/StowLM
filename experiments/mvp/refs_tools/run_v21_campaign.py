"""Resume-safe reference campaign; admit only currently unreserved fleet cores."""
from pathlib import Path
from datetime import datetime, timezone
import json, os, re, resource, subprocess, sys, time
ROOT=Path(__file__).resolve().parents[3]
os.chdir(ROOT)
TOOLS=ROOT/'experiments/mvp/refs_tools'
OUT=ROOT/'experiments/mvp/runs/test-bounds-small-v21'
OUT.mkdir(parents=True,exist_ok=True)
LEDGER=ROOT/'experiments/fleet/ledger.md'
OWNER='mvp-refs/v21-small'
def write(name,value): (OUT/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def capacity():
 text=LEDGER.read_text(); rows=[]
 for line in text.splitlines():
  parts=[x.strip() for x in line.split('|')]
  if len(parts)>5 and parts[1] not in ('executive/job','---') and not parts[1].startswith(OWNER):
   match=re.match(r'(\d+)',parts[3])
   if match: rows.append((parts[1],int(match[1])))
 return text,max(0,16-sum(v for _,v in rows)),rows
try: os.nice(10)
except OSError: pass
# Prior measured no-screen10:151.992s pilot;589.664s/remaining9 on2cores.
# Conservative envelope20 solverattempts*300s +bounds5*30s,<=103min on1core.
workers=0
while not workers:
 text,free,rows=capacity()
 load=os.getloadavg()[0]
 print(json.dumps(dict(event='admission',free_ledger=free,load1=load,utc=datetime.now(timezone.utc).isoformat())),flush=True)
 if free and load<16:
  workers=min(3,free)
  line=f'| {OWNER} | 6.3 D92/D94 | {workers} | {workers*3} | no | prior151.992s pilot;20solverattempts plus5bounds; <=103min/1core or35min/3cores conservative; native1, scopedV4, C2off; PID{os.getpid()} | {datetime.now(timezone.utc).isoformat()} |\n'
  with LEDGER.open('a') as f:f.write('\n'+line)
  # A concurrent admission can only shrink our own reservation before launch.
  _,free_after,_=capacity()
  if free_after<workers:
   LEDGER.write_text(''.join(l for l in LEDGER.read_text().splitlines(True) if not l.startswith('| '+OWNER+' |')))
   workers=0
   continue
 else: time.sleep(30)
start=time.monotonic(); history=[]
write('compute.json',dict(pid=os.getpid(),cores=workers,ram_gb=workers*3,gpu=False,estimate_s=6150/workers,nice=os.getpriority(os.PRIO_PROCESS,0),status='running'))
base=[sys.executable,str(TOOLS/'run_small_v2.py')]
common=['--release',str(TOOLS/'small-v21-release-frozen.json'),'--source-root',str(TOOLS/'frozen-runtime-small-v21'),'--workers',str(workers)]
jobs=[('bounds','test-bounds-small-v21-extra', ['--instance-list',str(TOOLS/'small-v21-extra-instances.txt')]),
 ('solver','test-solver-small-v21-screen',['--securing-screen','--seed-policy','instance','--limit','1']),
 ('solver','test-solver-small-v21-screen',['--securing-screen','--seed-policy','instance']),
 ('solver','test-solver-small-v21-noscreen-extra',['--instance-list',str(TOOLS/'small-v21-extra-instances.txt')])]
try:
 for method,folder,extra in jobs:
  cmd=base+[method]+common+extra+['--output-dir',str(ROOT/'experiments/mvp/runs'/folder)]
  tick=time.monotonic();print('RUN '+ ' '.join(cmd),flush=True)
  p=subprocess.run(cmd)
  history.append(dict(command=cmd,exit_code=p.returncode,wall_s=time.monotonic()-tick))
  write('campaign-progress.json',history)
  if p.returncode: raise RuntimeError(f'Job failed: {folder}')
finally:
 elapsed=time.monotonic()-start
 write('compute.json',dict(pid=os.getpid(),cores=workers,ram_gb=workers*3,gpu=False,estimate_s=6150/workers,actual_s=elapsed,nice=os.getpriority(os.PRIO_PROCESS,0),max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,status='complete' if len(history)==4 and all(j['exit_code']==0 for j in history) else 'incomplete',jobs=history))
 LEDGER.write_text(''.join(l for l in LEDGER.read_text().splitlines(True) if not l.startswith('| '+OWNER+' |')))
 with (ROOT/'experiments/fleet/history.md').open('a') as f:f.write(f'\n| {OWNER} | {datetime.now(timezone.utc).isoformat()} | {workers}CPU/{workers*3}GB/noGPU; estimate{6150/workers:.1f}s vs actual{elapsed:.3f}s; see test-bounds-small-v21/compute.json; nice denied if0 |\n')
