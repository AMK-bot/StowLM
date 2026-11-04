"""Run separately pinned v4.4 small and tiny cohorts within two admitted cores."""
from pathlib import Path
from datetime import datetime,timezone
import json,os,re,resource,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[3];os.chdir(ROOT)
RUNS=ROOT/'experiments/mvp/runs';LEDGER=ROOT/'experiments/fleet/ledger.md';OWNER='mvp-refs/twostep-v44'
small=RUNS/'test-twostep-small-v21-v44';tiny=RUNS/'test-twostep-tiny-v44'
def write(path,data):path.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
def capacity():
 n=0
 for line in LEDGER.read_text().splitlines():
  parts=[v.strip() for v in line.split('|')]
  if len(parts)>5 and parts[1]!=OWNER:
   m=re.match(r'(\d+)',parts[3])
   if m:n+=int(m[1])
 return max(0,16-n)
try:os.nice(10)
except OSError:pass
while capacity()<2 or os.getloadavg()[0]>16:
 print(json.dumps(dict(event='queued',spare_including_own=capacity(),load1=os.getloadavg()[0])),flush=True);time.sleep(30)
est_small=json.loads((small/'pilot-compute.json').read_text())['actual_s']*14/2*1.5
est_tiny=json.loads(next((tiny/'instances').glob('*/record.json')).read_text())['timing']['total_s']*14/2*1.5
line=f'| {OWNER} | 6.3 small15+tiny15 v4.4 | 2 | 6 | no | pilotsPASSsmall42.954s/tiny34.252spipeline; remaining14each estsmall{est_small:.1f}s+tiny{est_tiny:.1f}s incl50%margin; native1/C2off/scopedV4/no repair; supervisor{os.getpid()} | {datetime.now(timezone.utc).isoformat()} |\n'
text=LEDGER.read_text();assert sum(l.startswith('| '+OWNER+' |') for l in text.splitlines())==1
LEDGER.write_text(''.join(line if l.startswith('| '+OWNER+' |') else l for l in text.splitlines(True)))
assert capacity()>=2,'Intervening fleet admission; no new numerical work started'
jobs=[(small,[sys.executable,'experiments/mvp/refs_tools/run_small_twostep.py','--workers','2'],est_small), (tiny,[sys.executable,'experiments/mvp/refs_tools/run_tiny_twostep_v44.py','--workers','2'],est_tiny)]
receipts=[];begin=time.monotonic();code=1
try:
 for folder,cmd,estimate in jobs:
  tick=time.monotonic();p=subprocess.Popen(cmd);d=dict(command=cmd,pid=p.pid,parent_pid=os.getpid(),estimate_s=estimate,cores=2,ram_gb=6,gpu=False,nice=os.getpriority(os.PRIO_PROCESS,0),started_utc=datetime.now(timezone.utc).isoformat());write(folder/'campaign-process.json',d)
  code=p.wait();d.update(exit_code=code,actual_s=time.monotonic()-tick,max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,rss_scope='Supervisor lifetime maximum for completed children, not aggregate simultaneous RAM',status='complete' if code==0 else 'incomplete');write(folder/'campaign-compute.json',d);receipts.append(d);print(json.dumps(d),flush=True)
  if code:break
finally:
 elapsed=time.monotonic()-begin;write(small/'combined-campaign-compute.json',dict(cores=2,ram_gb=6,gpu=False,estimate_s=est_small+est_tiny,actual_s=elapsed,jobs=receipts,status='complete' if code==0 and len(receipts)==2 else 'incomplete'))
 LEDGER.write_text(''.join(l for l in LEDGER.read_text().splitlines(True) if not l.startswith('| '+OWNER+' |')))
 with (ROOT/'experiments/fleet/history.md').open('a') as f:f.write(f'\n| {OWNER} | {datetime.now(timezone.utc).isoformat()} | 2CPU/6GB/noGPU native1; pilots small42.954s vs360s,tinypipeline34.252s vs120s; main estimate{est_small+est_tiny:.3f}s vsactual{elapsed:.3f}s; cohort receipts test-twostep-small-v21-v44 andtest-twostep-tiny-v44; nice denied if0; preflight toolingfailure retained no numericalcase |\n')
sys.exit(code)
