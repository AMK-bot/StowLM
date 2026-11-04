"""Private paired wide-config pilot, exact archived runtime, owned CPU-limited child."""
import json,os,resource,subprocess,sys,time,hashlib
from datetime import datetime,timezone
from pathlib import Path
H=Path(__file__).resolve().parent;A=H/'runtime-v2';P=H/'prototype/wide-config32-512/pilot-paired-archive'
P.mkdir(parents=True,exist_ok=True)
assert not (P/'launcher.json').exists()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
job=json.loads((H/'prototype/wide-config32-512/pilot/manifest.json').read_text())['jobs'][0]
job.update(directory=str(P/'test15-28100010'),cpu_cap_s=90,matched_prefix_overhead=True,freeze_manifest=str(A/'experiments/mvp/lookahead/runtime-freeze-v2.json'))
folder=Path(job['directory']);folder.mkdir(exist_ok=True);save(folder/'job.json',job)
sys.path.insert(0,str(A/'src'))
import importlib
names=('stowage.io','stowage.feasibility._engine','stowage.solver.securing_screen','llm.stowlm.mask','llm.securing.mask_filter','llm.lookahead.mask_filter','llm.lookahead.support_bounds')
origins={name:str(Path(importlib.import_module(name).__file__).resolve())for name in names}
assert all(Path(p).is_relative_to(A/'src')for p in origins.values())
row=dict(launcher_pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),completed=False,amendment='Paired cold pilot rather than ON-only: parent authorization before optional data. Same preregistered case, repetition seed, and config.',critical_import_origins=origins,os_cpu_soft_s=95,os_cpu_hard_s=100,launcher_sha256=sha(__file__),worker_sha256=sha(A/'experiments/mvp/lookahead/paired_v2.py'))
save(P/'launcher.json',row)
start=time.perf_counter()
def cap():resource.setrlimit(resource.RLIMIT_CPU,(95,100))
with (P/'worker.log').open('w')as log:
 cmd=[sys.executable,str(A/'experiments/mvp/lookahead/paired_v2.py'),'--job',str(folder/'job.json')];row['command']=cmd
 p=subprocess.Popen(cmd,cwd=A,env={**os.environ,'PYTHONPATH':str(A/'src'),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'},preexec_fn=cap,stdout=log,stderr=subprocess.STDOUT);row['child_pid']=p.pid;save(P/'launcher.json',row);row['exit_code']=p.wait();row['reaped']=True
u=resource.getrusage(resource.RUSAGE_CHILDREN);row.update(completed=True,wall_s=time.perf_counter()-start,child_cpu_s=u.ru_utime+u.ru_stime,child_lifetime_peak_rss_bytes=u.ru_maxrss,ended_utc=datetime.now(timezone.utc).isoformat());save(P/'launcher.json',row);print(json.dumps(row))
