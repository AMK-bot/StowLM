"""Frozen-runtime fixed-prefix replay. Runtime policy remains unmodified."""
import argparse,ast,hashlib,importlib,json,os,resource,sys,time,traceback
from dataclasses import asdict
from pathlib import Path
from datetime import datetime,timezone
for key in('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','RAYON_NUM_THREADS'):os.environ[key]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,sort_keys=True,indent=2,default=str)+'\n')
def run(job):
 folder=Path(job['directory']);folder.mkdir(parents=True,exist_ok=True);out=folder/'result.json';assert not out.exists()
 start=time.perf_counter();c0=time.process_time();runtime=Path(job['runtime']).resolve();manifest=json.loads((runtime/'manifest.json').read_text());access=set()
 def audit(event,args):
  if event=='open'and args and isinstance(args[0],(str,bytes)):
   try:access.add(str(Path(os.fsdecode(args[0])).resolve()))
   except (OSError,ValueError):pass
 sys.addaudithook(audit)
 row=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),completed=False,job=job,worker_sha256=sha(__file__),pythonhashseed=os.environ.get('PYTHONHASHSEED'),native_threads=1,gpu=False,stages={})
 save(out,row)
 try:
  assert all(sha(runtime/name)==digest for name,digest in manifest['files'].items())
  assert os.environ['PYTHONPATH']==str(runtime/'src');sys.path.insert(0,str(runtime/'src'))
  names=('stowage.io','stowage.feasibility._engine','stowage.solver.securing_screen','stowage.checker.lashing','llm.stowlm.mask','llm.stowlm.format','llm.securing.mask_filter')
  row['critical_origins']={name:str(Path(importlib.import_module(name).__file__).resolve())for name in names};assert all(Path(p).is_relative_to(runtime/'src')for p in row['critical_origins'].values())
  from stowage.io import load_instance
  from stowage.actions import get_action_space
  from stowage.actions.space import Place,Discharge,Relocate,RestowOff
  from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
  from stowage.solver.securing_screen import NecessaryDeckSecuringScreen
  from llm.securing import SecuringEngine
  from llm.stowlm.format import ActionCodec,load_tokenizer
  from llm.stowlm.mask import MaskedDecoder,MaskConfig
  from stowage.rules.catalogue import rule_freeze_v1_enabled
  assert not rule_freeze_v1_enabled()
  prefix_path=Path(job['prefix']);assert sha(prefix_path)==job['prefix_sha256'];prefix=json.loads(prefix_path.read_text());assert prefix['seed']in(31100000,31100001)and prefix['case'].startswith('train20-')and'/train/'in prefix['instance']['source'];assert'/test/'not in prefix['instance']['source'];assert sha(prefix['instance']['path'])==prefix['instance']['sha256'];assert sha(prefix['trace']['path'])==prefix['trace']['sha256']
  constructors=dict(Place=Place,Discharge=Discharge,Relocate=Relocate,RestowOff=RestowOff)
  def parse(text):
   x=ast.parse(text,mode='eval').body;assert isinstance(x,ast.Call)and isinstance(x.func,ast.Name)and x.func.id in constructors;assert all(isinstance(a,ast.Constant)for a in x.args)and all(k.arg and isinstance(k.value,ast.Constant)for k in x.keywords);return constructors[x.func.id](*(a.value for a in x.args),**{k.arg:k.value.value for k in x.keywords})
  actions=[parse(x)for x in prefix['actions']];phase=time.perf_counter();instance=load_instance(prefix['instance']['path']);space=get_action_space();native=make_feasibility_engine(instance,space,config=FeasibilityConfig(extra={'global_screen':0}));codec=ActionCodec(load_tokenizer(runtime/'models/glimmer-30b-4bit/tokenizer.json'),[b.id for b in instance.containers]);row['stages']['initialize_s']=time.perf_counter()-phase
  row['priority_attempts']=[]
  for name,fn in [('setpriority',lambda:os.setpriority(os.PRIO_PROCESS,0,10)),('nice',lambda:os.nice(10))]:
   try:fn();row['priority_attempts'].append(dict(method=name,passed=True));break
   except OSError as exc:row['priority_attempts'].append(dict(method=name,passed=False,error=str(exc)))
  sleep_total=0.
  def duty():
   nonlocal sleep_total
   needed=(time.process_time()-c0)/job.get('duty_fraction',.5)-(time.perf_counter()-start)
   if needed>0:
    began=time.perf_counter();time.sleep(needed);sleep_total+=time.perf_counter()-began
  shared=NecessaryDeckSecuringScreen(instance);root=native;row['warmup']=[]
  if job['cache_mode']=='history-warm':
   history_pin=job['prior_baseline_history'];assert sha(history_pin['path'])==history_pin['sha256'];history=json.loads(Path(history_pin['path']).read_text());assert history['job']['case']['case']==prefix['case']
   for rep in history_pin['repetitions']:
    trace=next(x for x in history['rollouts']if x['arm']=='baseline'and x['repetition']==rep);trial=root.copy();screened=SecuringEngine(trial,screen=shared);record=dict(repetition=rep,masked_historical_actions=[],steps=0);phase=time.perf_counter();pc0=time.process_time()
    for item in trace['steps']:
     action=parse(item['action']);legal=screened.legal_actions()
     if action in legal:screened.push(action)
     else:record['masked_historical_actions'].append(dict(depth=trial.depth,action=repr(action)));trial.push(action)
     record['steps']+=1
    screened.legal_actions();record.update(cpu_s=time.process_time()-pc0,wall_s=time.perf_counter()-phase,diagnostics=screened.screen_diagnostics());row['warmup'].append(record);save(out,row);duty()
  native=root.copy();row['cache_mode']=job['cache_mode'];row['shared_native_cache_sizes_before_target']={k:len(v)for k,v in native.__dict__.items()if isinstance(v,dict)and('cache'in k or'memo'in k)}
  row['securing_cache_size_before_target']=len(shared.cache)
  phase=time.perf_counter()
  for i,action in enumerate(actions):
   try:native.push(action)
   except Exception as exc:row['prefix_replay_failure']=dict(depth=i,action=repr(action),error=str(exc));raise
  row['stages']['native_prefix_replay_s']=time.perf_counter()-phase;row['replayed_depth']=native.depth;assert native.depth==len(actions)
  engine=SecuringEngine(native,screen=shared);phase=time.perf_counter();legal=engine.legal_actions();row['stages']['securing_legal_query_s']=time.perf_counter()-phase;row['legal_actions']=[repr(x)for x in legal];row['native_legal_actions']=[repr(x)for x in native.legal_actions()];row['diagnostics']=engine.screen_diagnostics();row['dead_end']=str(engine.dead_end())
  phase=time.perf_counter();tokens={};sequences=[codec.encode_action(a)for a in legal]
  if engine.is_complete():sequences=[codec.encode_plan([])]
  for sequence in sequences or [()]:
   dec=MaskedDecoder(engine.copy(),codec,MaskConfig(lookahead=0))
   for depth in range(len(sequence)+1):
    if depth==len(sequence)and sequence:break
    key=','.join(map(str,sequence[:depth]));allowed=list(dec.allowed_token_ids());assert key not in tokens or tokens[key]==allowed;tokens[key]=allowed
    if depth<len(sequence):dec.advance(sequence[depth])
  row['securing_cache_entries']=[dict(key=repr(key),**asdict(value))for key,value in sorted(shared.cache.items(),key=lambda x:repr(x[0]))];row['token_sets']=tokens;row['stages']['actual_token_queries_s']=time.perf_counter()-phase;row['tokenizer_sha256']=sha(runtime/'models/glimmer-30b-4bit/tokenizer.json');row['runtime_epoch']=manifest['epoch'];row['source_after_valid']=all(sha(runtime/name)==digest for name,digest in manifest['files'].items());assert row['source_after_valid'];duty();row['cooperative_sleep_s']=sleep_total
 except Exception as exc:row.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
 finally:
  row.update(completed=True,wall_s=time.perf_counter()-start,cpu_s=time.process_time()-c0,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,ended_utc=datetime.now(timezone.utc).isoformat(),loaded_modules={n:getattr(m,'__file__',None)for n,m in sys.modules.items()if n.startswith(('stowage.','llm.'))},opened_files=sorted(access));save(out,row);print(json.dumps({k:row.get(k)for k in('pid','completed','error','wall_s','cpu_s','peak_rss_bytes','stages')}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();run(json.loads(a.job.read_text()))
