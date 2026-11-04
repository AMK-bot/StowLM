import importlib.util,json,os,pathlib,resource,select,signal,subprocess,time,urllib.request
BASE=pathlib.Path('/private/tmp/stowlm-judge-99-3-xzmzsouq');COPY=BASE/'diagnostic-code-only';LOGS=BASE/'logs/app-diagnostic';LOGS.mkdir(parents=True,exist_ok=True)
env=os.environ.copy()
for key in tuple(env):
 if key in {'VIRTUAL_ENV','UV_PROJECT_ENVIRONMENT','PYTHONHOME','SPARK_CREDIT_READY','HF_TOKEN','MODEL_API_KEY'} or any(x in key.upper() for x in ('API_KEY','ACCESS_TOKEN','SECRET')):env.pop(key,None)
env.update(PYTHONPATH=str(BASE/'guard'),UV_CACHE_DIR=str(BASE/'uv-cache-full'),HF_HOME=str(BASE/'empty-hf'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',UV_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
def record(r):
 with (LOGS/'processes.jsonl').open('a') as f:f.write(json.dumps(r)+'\n')
 print(json.dumps(r),flush=True)
def limit():resource.setrlimit(resource.RLIMIT_CPU,(60,65))
origin="""import importlib.util,pathlib,sys,os,json
root=pathlib.Path.cwd().resolve()
def check_origins():
 origins={}
 for name,module in list(sys.modules.items()):
  if name.split('.')[0] not in ('stowage','llm','app','eval'):continue
  p=getattr(module,'__file__',None)
  if p:assert pathlib.Path(p).resolve().is_relative_to(root),(name,p);origins[name]=p
 print('project_origins',json.dumps(origins,sort_keys=True),flush=True)
"""
def run(name,code):
 began=time.monotonic();before=resource.getrusage(resource.RUSAGE_CHILDREN)
 args=['uv','run','--offline','--no-sync','python','-c',origin+code+'\ncheck_origins()\n']
 with (LOGS/f'{name}.stdout').open('wb') as out,(LOGS/f'{name}.stderr').open('wb') as err:
  p=subprocess.Popen(args,cwd=COPY,env=env,stdout=out,stderr=err,start_new_session=True,preexec_fn=limit)
  r={'name':name,'pid':p.pid,'state':'started','start_epoch':time.time(),'argv':args};record(r)
  try:rc=p.wait(timeout=120);timeout=False
  except subprocess.TimeoutExpired:
   p.terminate()
   try:rc=p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.kill();rc=p.wait(timeout=5)
   timeout=True
 after=resource.getrusage(resource.RUSAGE_CHILDREN)
 r.update(state='reaped',exit_code=rc,wall_s=time.monotonic()-began,cpu_s=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,peak_child_rss_bytes=after.ru_maxrss,timed_out=timeout);record(r)

run('securing-config',"""
from stowage.io import load_instance
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
from llm.securing import SecuringEngine,SecuringConfigurationError
i=load_instance('data/fixtures/empty_ship.json')
e=make_feasibility_engine(i,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
s=SecuringEngine(e)
print('ordinary_synthetic_construction',json.dumps({'constructed':True,'bounds_available':s.screen.bounds_available,'metrics':s.securing_metrics()}),flush=True)
i2=i.model_copy(deep=True)
i2.vessel.hull.hull_offsets_ref='params/fleet/feeder_500.toml#hull_form'
e.instance=i2
try:SecuringEngine(e)
except SecuringConfigurationError as exc:print('known_fleet_missing_asset',type(exc).__name__,str(exc),flush=True)
else:raise AssertionError('declared missing KCS asset failed open')
""")
run('spark-offline',"""
from app.instruct import InstructService
s=InstructService(None,live=False)
status=s.live_status();result=s.parse('Fresh clone rehearsal 99.3 unrecorded request; do not call Spark')
assert status['live'] is False and result['cost_usd']=='0' and result['refusal']['code']=='no_recorded_response'
print('spark_keyless_offline',json.dumps({'status':status,'result':result}),flush=True)
""")

# Use the actual CLI main, with only an owned-PID announcement added by this harness.
name='app-http';began=time.monotonic();before=resource.getrusage(resource.RUSAGE_CHILDREN)
args=['uv','run','--offline','--no-sync','python','-c',origin+"print('OWNED_PYTHON_PID',os.getpid(),flush=True)\nfrom app.__main__ import main\ncheck_origins()\nmain()\n",'--instance','data/fixtures/empty_ship.json','--no-discovery','--no-companions','--port','0']
outpath=LOGS/f'{name}.stdout';errpath=LOGS/f'{name}.stderr';owned_python=None;port=None
with errpath.open('wb') as err,outpath.open('w') as out:
 p=subprocess.Popen(args,cwd=COPY,env=env,stdout=subprocess.PIPE,stderr=err,start_new_session=True,preexec_fn=limit,text=True,bufsize=1)
 r={'name':name,'pid':p.pid,'state':'started','start_epoch':time.time(),'argv':args};record(r)
 deadline=time.monotonic()+120
 while time.monotonic()<deadline and p.poll() is None:
  ready,_,_=select.select([p.stdout],[],[],.25)
  if not ready:continue
  line=p.stdout.readline();out.write(line);out.flush()
  if line.startswith('OWNED_PYTHON_PID '):owned_python=int(line.split()[1]);record({'name':name,'python_pid':owned_python,'state':'owned-child-announced'})
  if line.startswith('StowLM demo: http://127.0.0.1:'):
   port=int(line.split('127.0.0.1:')[1].split()[0]);break
 responses=[]
 if port is not None:
  for path in ['/api/health','/api/demo','/api/instruct/examples']:
   url=f'http://127.0.0.1:{port}{path}'
   with urllib.request.urlopen(url,timeout=30) as response:
    raw=response.read();(LOGS/(path.replace('/','_')+'.body')).write_bytes(raw)
    item={'path':path,'status':response.status,'bytes':len(raw),'content_type':response.headers.get('content-type')};responses.append(item)
  record({'name':name,'state':'http-responses','responses':responses})
 if p.poll() is None:
  # Both IDs were created by this harness; signal only those exact recorded PIDs.
  target=owned_python or p.pid
  os.kill(target,signal.SIGTERM)
  try:rc=p.wait(timeout=5)
  except subprocess.TimeoutExpired:
   os.kill(target,signal.SIGKILL);rc=p.wait(timeout=5)
  stopped=True
 else:rc=p.returncode;stopped=False
 remaining=p.stdout.read();out.write(remaining)
after=resource.getrusage(resource.RUSAGE_CHILDREN)
r.update(state='reaped',exit_code=rc,port=port,http_responses=responses,stopped_own_server=stopped,wall_s=time.monotonic()-began,cpu_s=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,peak_child_rss_bytes=after.ru_maxrss);record(r)
