"""Single explicitly admitted owned child, deterministic process seed and strict OS CPU cap."""
import json,os,subprocess,resource,sys,time,hashlib
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;inv=json.loads((H/'inventory.json').read_text());prefix=next(x for x in inv['prefixes']if x['case']=='train20-31100001'and x['repetition']==4);folder=H/'pilot-current-cold-seed0';folder.mkdir(exist_ok=True);assert not(folder/'launcher.json').exists();runtime=Path(inv['runtime']);job=dict(directory=str(folder),runtime=str(runtime),prefix=prefix['path'],prefix_sha256=prefix['sha256'],native_cache='cold',securing_cache='cold',hashseed=0);(folder/'job.json').write_text(json.dumps(job,indent=2)+'\n')
def save(x):(folder/'launcher.json').write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')
def cap():resource.setrlimit(resource.RLIMIT_CPU,(55,60))
row=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),estimate=dict(cpu_cores=1,ram_gb=1,typical_wall_s=[10,30],os_cpu_soft_s=55,os_cpu_hard_s=60),worker_sha256=hashlib.sha256((H/'replay_worker.py').read_bytes()).hexdigest(),launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),completed=False);save(row);start=time.perf_counter()
with(folder/'worker.log').open('w')as log:
 cmd=[sys.executable,str(H/'replay_worker.py'),'--job',str(folder/'job.json')];p=subprocess.Popen(cmd,cwd=runtime,env={**os.environ,'PYTHONPATH':str(runtime/'src'),'PYTHONHASHSEED':'0','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'},preexec_fn=cap,stdout=log,stderr=subprocess.STDOUT);row['child_pid']=p.pid;save(row);row['exit_code']=p.wait();row['reaped']=True
u=resource.getrusage(resource.RUSAGE_CHILDREN);row.update(completed=True,wall_s=time.perf_counter()-start,child_cpu_s=u.ru_utime+u.ru_stime,child_lifetime_peak_rss_bytes=u.ru_maxrss,ended_utc=datetime.now(timezone.utc).isoformat());save(row);print(json.dumps(row))
