"""Serial admitted matrix with live Mach/free-memory gate, checkpoints, owned PIDs."""
import ctypes,json,os,resource,subprocess,sys,time,hashlib,re
from datetime import datetime,timezone
from pathlib import Path
H=Path(__file__).resolve().parent;spec=json.loads((H/'matrix-preregistration.json').read_text());out=H/'matrix-current-1';out.mkdir(exist_ok=True);worker=H/'replay_worker_v2.py';worker_sha=hashlib.sha256(worker.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')
def now():return datetime.now(timezone.utc).isoformat()
def gate():
 lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib');lib.mach_host_self.restype=ctypes.c_uint;host=lib.mach_host_self()
 def sample():
  ticks=(ctypes.c_uint*4)();count=ctypes.c_uint(4);code=lib.host_statistics(host,3,ctypes.byref(ticks),ctypes.byref(count));assert code==0,(code,count.value);return list(ticks)
 first=sample();time.sleep(.5);second=sample();delta=[b-a for a,b in zip(first,second)];busy=(sum(delta)-delta[2])/sum(delta)*os.cpu_count()
 p=subprocess.Popen(['/usr/bin/memory_pressure','-Q'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);text,_=p.communicate();match=re.search(r'free percentage:\s*(\d+)%',text);assert p.returncode==0 and match,text
 free=int(match.group(1));return dict(utc=now(),mach_busy_cores=busy,hardware_logical_cores=os.cpu_count(),free_percentage=free,memory_probe_pid=p.pid,memory_probe_reaped=True,pass_gate=busy+1<=15 and free>=15)
state_path=out/'controller.json';assert not state_path.exists(),'Use resume controller deliberately; do not overwrite prior state'
state=dict(pid=os.getpid(),started_utc=now(),completed=False,worker_sha256=worker_sha,controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),native_threads=1,gpu=False,active_job=None,jobs=[],resource_gate_history=[],admission='Root scoped24 serial1CPU/<1GB/50%duty. Global fleet ledger overbooked/read-only; gate each job busy+1<=15,free>=15%.')
# Prioritize all seeds/cache variants for the first train31100001rep4 prefix.
jobs=sorted(spec['jobs'],key=lambda j:(0 if j['case']=='train20-31100001'and j['repetition']==4 else 1,j['case'],j['repetition'],j['hashseed'],j['cache_mode']))
start=time.perf_counter();save(state_path,state)
for job in jobs:
 job['duty_fraction']=.5;folder=Path(job['directory']);folder.mkdir(parents=True,exist_ok=True);result=folder/'result.json'
 if result.exists():raise AssertionError('Preserve prior output; no hidden retries')
 while True:
  try:g=gate()
  except Exception as exc:g=dict(utc=now(),pass_gate=False,error=f'{type(exc).__name__}: {exc}')
  state['resource_gate_history'].append(g);save(state_path,state)
  if g['pass_gate']:break
  time.sleep(10)
 assert hashlib.sha256(worker.read_bytes()).hexdigest()==worker_sha
 save(folder/'job.json',job);record=dict(case=job['case'],repetition=job['repetition'],hashseed=job['hashseed'],cache_mode=job['cache_mode'],started_utc=now(),result=str(result),admission_gate=g);state['jobs'].append(record);state['active_job']=record;save(state_path,state)
 ledger=json.loads((H/'compute-ledger.json').read_text());ledger['running']=[dict(task='D112',cores=1,ram_gb=1,gpu=False,native_threads=1,job=job,duty_fraction=.5,estimate_cpu_s=240)];save(H/'compute-ledger.json',ledger)
 def cap():resource.setrlimit(resource.RLIMIT_CPU,(job['os_cpu_soft_s'],job['os_cpu_hard_s']))
 with(folder/'worker.log').open('w')as log:
  p=subprocess.Popen([sys.executable,str(worker),'--job',str(folder/'job.json')],cwd=job['runtime'],env={**os.environ,'PYTHONPATH':str(Path(job['runtime'])/'src'),'PYTHONHASHSEED':str(job['hashseed']),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'},preexec_fn=cap,stdout=log,stderr=subprocess.STDOUT);record['pid']=p.pid;save(state_path,state);u0=resource.getrusage(resource.RUSAGE_CHILDREN);began=time.perf_counter();record['exit_code']=p.wait();record['reaped']=True
 u=resource.getrusage(resource.RUSAGE_CHILDREN);record.update(wall_s=time.perf_counter()-began,child_cpu_s=u.ru_utime+u.ru_stime-u0.ru_utime-u0.ru_stime,child_lifetime_peak_rss_bytes=u.ru_maxrss,ended_utc=now());r=json.loads(result.read_text())if result.exists()else{};record.update(error=r.get('error'),result_completed=r.get('completed',False));state['active_job']=None;save(state_path,state)
 ledger=json.loads((H/'compute-ledger.json').read_text());ledger['running']=[];ledger['completed'].append(dict(**record,estimate_cpu_s=240,cores=1,ram_gb=1));ledger['queued']=[str(len(jobs)-len(state['jobs']))+' admitted matrix jobs pending live resource gate'];save(H/'compute-ledger.json',ledger)
state.update(completed=True,wall_s=time.perf_counter()-start,ended_utc=now());save(state_path,state);print(json.dumps({k:state[k]for k in('pid','completed','wall_s')}))
