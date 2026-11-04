"""Resumable fixed-witness verification, then at most one standard V4 finish.

Uses the released500000 search slot. CPU≤4with the other3selected map searches;
no new search allowance, map, physical tolerance or generator change.
"""
from pathlib import Path
import fcntl,json,os,subprocess,sys,time,hashlib
H=Path(__file__).resolve().parent;ROOT=H.parents[4];MAP='medium-feeder_500-random_yard-185100000'
CP=H.parent/'capacity/maps'/MAP/'construct-cp.json'
OUT=H/'cp-review';OUT.mkdir(parents=True,exist_ok=True)
def read(p):return json.loads(Path(p).read_text())
def save(p,r):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.tmp');t.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');t.replace(p)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def child(command,logname):
 with (OUT/logname).open('a') as log:p=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
 record=dict(pid=p.pid,command=command,started_unix=time.time());owned.append(record);save(OUT/'owned-pids.json',owned)
 code=p.wait();record.update(returncode=code,ended_unix=time.time());save(OUT/'owned-pids.json',owned)
 return code
with (OUT/'controller.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if (OUT/'complete.json').exists():sys.exit(0)
 save(OUT/'controller.json',dict(pid=os.getpid(),started_unix=time.time(),cpu_slot='released500000searchslot',verification_overhead_separate_from_search=True))
 owned=[];started=time.monotonic()
 while True:
  main=all((H/'runs'/MAP/c['name']/'summary.json').exists() for c in read(H/'preregister.json')['configs'])
  extra=(H/'runs'/MAP/'legacy_wide_beam/summary.json').exists()
  if CP.exists() and main and extra:break
  save(OUT/'progress.json',dict(waiting_for_cp=not CP.exists(),waiting_for_map_search=not(main and extra),updated_unix=time.time()));time.sleep(5)
 cp=read(CP);summary=dict(cp_status=cp['status'],cp_sha256=sha(CP),map_id=MAP,checks={},verification_overhead_separate_from_search=True)
 if not cp.get('witness'):
  summary.update(status='no_cp_witness',elapsed_s=time.monotonic()-started);save(OUT/'complete.json',summary);sys.exit(0)
 target=H/'cp-candidates'/MAP
 if not (target/'summary.json').exists():child([sys.executable,str(H/'candidate.py'),'--map',MAP,'--witness',str(CP),'--out',str(target),'--budget','300'],'candidate.log')
 summary['candidate']=read(target/'summary.json') if (target/'summary.json').exists() else dict(error='candidateworker producednosummary')
 promising=[]
 for kind,filename in [('native_terminal','result.json'),('direct_witness','direct-result.json')]:
  result=target/filename
  if not result.exists():continue
  out=H/'cp-checks'/kind
  if not (out/'summary.json').exists():child([sys.executable,str(H/'borrowed_check_candidate.py'),'--map',MAP,'--result',str(result),'--out',str(out)],kind+'-check.log')
  if not (out/'summary.json').exists():continue
  check=read(out/'summary.json');summary['checks'][kind]=check
  if check['checker_passed']:
   summary.update(status='full_checker_pass',certificate_kind=kind,result_path=str(result.relative_to(ROOT)),report_path=str((out/'full-report.json').relative_to(ROOT)));save(OUT/'complete.json',summary);sys.exit(0)
  report=read(out/'full-report.json');fixed=[name for name in ('moves','rules') if report['parts'].get(name,{}).get('passed') is False]
  if not fixed:promising.append((kind,result))
  else:summary.setdefault('fixed_cargo_rejections',{})[kind]=fixed
 if promising:
  kind,result=promising[0];summary['selected_finisher_candidate']=kind;save(OUT/'pre-finisher.json',summary)
  child([sys.executable,str(H/'finish_candidate.py'),'--map',MAP,'--result',str(result),'--name','cp500000','--budget','3600'],'finisher.log')
  p=H/'cp-finisher/runs/cp500000/complete.json'
  summary['finisher']=read(p) if p.exists() else dict(error='No completefinisherresult')
  summary['status']='finisher_complete'
 else:summary['status']='no_passing_or_ballast_only_candidate'
 summary['elapsed_s']=time.monotonic()-started;save(OUT/'complete.json',summary)
