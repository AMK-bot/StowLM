"""Admit and record the leader-authorized fifteen-case baseline campaign."""
from pathlib import Path
from datetime import datetime,timezone
import json,os,re,resource,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[3];os.chdir(ROOT)
OUT=ROOT/'experiments/mvp/runs/test-twostep-small-v21';LEDGER=ROOT/'experiments/fleet/ledger.md';OWNER='mvp-refs/twostep-v21'
def write(name,data): (OUT/name).write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
def available():
 rows=[]
 for line in LEDGER.read_text().splitlines():
  p=[v.strip() for v in line.split('|')]
  if len(p)>5 and p[1]!=OWNER:
   m=re.match(r'(\d+)',p[3])
   if m:rows.append((p[1],int(m[1])))
 return max(0,16-sum(n for _,n in rows)),rows
try:os.nice(10)
except OSError:pass
while True:
 spare,rows=available();load=os.getloadavg()[0]
 print(json.dumps(dict(event='admission',available_including_own=spare,load1=load,utc=datetime.now(timezone.utc).isoformat())),flush=True)
 if spare>=1 and load<=16:break
 time.sleep(30)
workers=min(3,spare)
pilot=json.loads((OUT/'pilot-compute.json').read_text());estimate=pilot['actual_s']*14*1.5/workers
line=f'| {OWNER} | 6.3 two-step v4.3.0 | {workers} | {workers*3} | no | pilotPASS59.575s/514MB; remaining14 estimate{estimate:.1f}s incl50%margin; native1/C2off/scopedV4/no repair; supervisor{os.getpid()} | {datetime.now(timezone.utc).isoformat()} |\n'
text=LEDGER.read_text();assert sum(l.startswith('| '+OWNER+' |') for l in text.splitlines())==1
LEDGER.write_text(''.join(line if l.startswith('| '+OWNER+' |') else l for l in text.splitlines(True)))
# Check for intervening reservations before starting numerical work.
spare,_=available();assert spare>=workers,'Concurrent fleet admission changed capacity; no numerical job started'
command=[sys.executable,str(ROOT/'experiments/mvp/refs_tools/run_small_twostep.py'),'--workers',str(workers)]
start=time.monotonic();child=subprocess.Popen(command)
write('campaign-process.json',dict(pid=child.pid,parent_pid=os.getpid(),command=command,cores=workers,ram_gb=workers*3,gpu=False,estimate_s=estimate,start_utc=datetime.now(timezone.utc).isoformat(),live_load1=load,nice=os.getpriority(os.PRIO_PROCESS,0)))
code=None
try:code=child.wait()
finally:
 elapsed=time.monotonic()-start
 write('campaign-compute.json',dict(command=command,pid=child.pid,parent_pid=os.getpid(),exit_code=code,status='complete' if code==0 else 'incomplete',estimate_s=estimate,actual_s=elapsed,cores=workers,ram_gb=workers*3,gpu=False,max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,nice=os.getpriority(os.PRIO_PROCESS,0)))
 LEDGER.write_text(''.join(l for l in LEDGER.read_text().splitlines(True) if not l.startswith('| '+OWNER+' |')))
 with (ROOT/'experiments/fleet/history.md').open('a') as f:f.write(f'\n| {OWNER} | {datetime.now(timezone.utc).isoformat()} | {workers}CPU/{workers*3}GB/noGPU; pilot59.575s vs360s estimate; remaining14 estimate{estimate:.3f}s vsactual{elapsed:.3f}s; see test-twostep-small-v21/campaign-compute.json; nice attempted, denied if0 |\n')
sys.exit(code if code is not None else 1)
