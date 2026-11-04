"""CPU-only real-factory contracts and ACTION-uniform rollout proxies."""
from __future__ import annotations
import os
for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','RAYON_NUM_THREADS']:os.environ[k]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
import argparse,hashlib,json,random,resource,signal,sys,time,traceback
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(d):return hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.partial');tmp.write_text(json.dumps(d,sort_keys=True,indent=2,default=str)+'\n');tmp.replace(p)
def snapshot(lookahead=False):
 folders=['src/stowage','src/llm/stowlm','src/llm/securing','src/llm/instructions','params']+(['src/llm/lookahead']if lookahead else[])
 return {str(p.relative_to(ROOT)):sha(p)for f in folders for p in sorted((ROOT/f).rglob('*'))if p.is_file()and p.suffix in {'.py','.json','.toml'}}
def priority():
 results=[]
 for label,fn in [('os.setpriority',lambda:os.setpriority(os.PRIO_PROCESS,0,10)),('os.nice',lambda:os.nice(10))]:
  try:fn();results.append(dict(method=label,passed=True));break
  except OSError as exc:results.append(dict(method=label,passed=False,error=str(exc)))
 return results
@contextmanager
def cpu_cap(seconds):
    """Operational watchdog: expiry is unavailable, never a physical verdict.

    A caught alarm still rejects normal return. Nested scopes preserve the
    earlier timer and restore its remaining time instead of renewing it.
    """
    if isinstance(seconds, bool) or not 0 < float(seconds) < float('inf'):
        raise ValueError('watchdog seconds must be finite and positive')
    seconds = float(seconds)
    clock = time.process_time
    started = clock()
    previous = signal.getsignal(signal.SIGPROF)
    previous_timer = signal.getitimer(signal.ITIMER_PROF)
    previous_due = started + previous_timer[0] if previous_timer[0] > 0 else None
    own_due = started + seconds
    due = min(own_due, previous_due) if previous_due is not None else own_due
    expired_flag = False
    forwarded_previous = False

    def expired(*args):
        nonlocal expired_flag, forwarded_previous
        expired_flag = True
        if (previous_due is not None and previous_due <= own_due
                and clock() >= previous_due and not forwarded_previous and callable(previous)):
            forwarded_previous = True
            previous(*args)
        raise TimeoutError(f'External watchdog exceeded {seconds}s (including swallowed alarm)')

    def reject_expiry():
        if expired_flag or clock() >= due:
            expired()

    signal.signal(signal.SIGPROF, expired)
    signal.setitimer(signal.ITIMER_PROF, max(1e-9, due-clock()))
    try:
        try:
            yield
        except Exception:
            # A late failure report/ordinary exception is still unavailable;
            # operator BaseException cancellation keeps its original meaning.
            reject_expiry()
            raise
        else:
            reject_expiry()
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, previous)
        if previous_due is not None:
            remaining = previous_due-clock()
            interval = previous_timer[1]
            if remaining <= 0 and interval > 0:
                remaining = interval-((-remaining) % interval)
            if remaining > 0:
                signal.setitimer(signal.ITIMER_PROF, remaining, interval)
def run(job):
 started=time.perf_counter();cpu_start=time.process_time();sleep_total=0.;prio=priority();folder=Path(job['directory']);folder.mkdir(parents=True,exist_ok=True);path=folder/'result.json'
 if path.exists():
  prior=json.loads(path.read_text());assert not prior.get('completed'),'Completed result is immutable';path.rename(folder/f'interrupted-{time.time_ns()}.json')
 arm=job['arm'];on=arm=='lookahead';before=snapshot(on);save(folder/'source-before.json',before)
 row=dict(pid=os.getpid(),job=job,started_utc=datetime.now(timezone.utc).isoformat(),completed=False,mode=job['mode'],arm=arm,worker_sha256=sha(__file__),source_epoch_before=canonical(before),native_threads=1,gpu=False,priority_attempts=prio,duty_fraction=job.get('duty_fraction',.5),checker_run=False,physical_acceptance_claim=False,rollouts=[])
 def duty():
  nonlocal sleep_total
  target=(time.process_time()-cpu_start)/row['duty_fraction']-(time.perf_counter()-started)
  if target>0:
   began=time.perf_counter();time.sleep(target);sleep_total+=time.perf_counter()-began
 try:
  if job.get('freeze_manifest'):
   frozen=json.loads(Path(job['freeze_manifest']).read_text());assert snapshot(False)==frozen['base_sources'],'Production baseline source drift'
   if on:assert before==frozen['lookahead_runtime_sources'],'Lookahead epoch drift'
  from stowage.io import load_instance,load_plan
  from stowage.actions import get_action_space
  from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
  from stowage.rules.catalogue import rule_freeze_v1_enabled
  from llm.stowlm.format import ActionCodec,load_tokenizer
  from llm.stowlm.mask import MaskConfig
  from llm.securing import securing_decoder_factory
  assert not rule_freeze_v1_enabled()
  case=job['case'];assert sha(case['instance']['path'])==case['instance']['sha256'];instance=load_instance(case['instance']['path']);space=get_action_space();phase=time.perf_counter()
  tokenizer=load_tokenizer(ROOT/'models/glimmer-30b-4bit/tokenizer.json');row['tokenizer_sha256']=sha(ROOT/'models/glimmer-30b-4bit/tokenizer.json');codec=ActionCodec(tokenizer,[b.id for b in instance.containers]);engine=make_feasibility_engine(instance,space,config=FeasibilityConfig(extra={'global_screen':0}))
  if on:
   from llm.lookahead import LookaheadConfig,lookahead_decoder_factory
   factory=lookahead_decoder_factory(securing=True,config=LookaheadConfig(**job.get('lookahead_config',{})))
  else:factory=securing_decoder_factory()
  n=1 if job['mode']=='contract'else job.get('rollouts',20);decoders=factory(instance,engine,codec,MaskConfig(lookahead=0),n);row.update(construction_s=time.perf_counter()-phase,factory_name=factory.name)
  if job['mode']=='contract':
   from stowage.model import CheckReport
   from stowage.checker.full import report_matches_inputs
   assert sha(case['plan']['path'])==case['plan']['sha256'];plan=load_plan(case['plan']['path']);report=CheckReport.model_validate_json(Path(case['checker']['path']).read_text());assert report.passed and report_matches_inputs(report,instance,plan),'Historical report not bound to pinned exact plan/input'
   row['historical_checker_bound']=True;actions=space.from_plan(instance,plan);decoder=decoders[0];allowed_ms=[]
   with cpu_cap(job.get('cpu_cap_s',120)):
    for ti,token in enumerate(codec.encode_plan(actions)):
     phase=time.perf_counter();allowed=decoder.allowed_token_ids();allowed_ms.append((time.perf_counter()-phase)*1000)
     if token not in allowed:
      row.update(rejected_token_index=ti,rejected_action_index=len(decoder.actions),rejected_token=token,decoder_status=decoder.status,dead_end=str(decoder.dead_end));raise AssertionError('Gold token masked')
     decoder.advance(token)
   assert decoder.status=='complete'and decoder.actions==actions
   row.update(contract_passed=True,actions=len(actions),tokens=len(allowed_ms),allowed_token_ms=allowed_ms,diagnostics=decoder.engine.screen_diagnostics())
   duty()
  else:
   for repetition,decoder in enumerate(decoders):
    output=folder/f'rollout-{repetition:02}.json'
    if output.exists():
     old=json.loads(output.read_text());assert old['source_epoch']==canonical(before);row['rollouts'].append(old);continue
    seed=int.from_bytes(hashlib.sha256(f"lookahead-v1:{case['instance']['sha256']}:{repetition}".encode()).digest()[:8],'big');rng=random.Random(seed);engine=decoder.engine;phase=time.perf_counter();c0=time.process_time();result=dict(repetition=repetition,seed=seed,source_epoch=canonical(before),steps=[],status=None,sampling='uniform over complete legal ACTIONS; never token-uniform',checker_run=False)
    try:
     with cpu_cap(job.get('cpu_cap_s',120)):
      while not engine.is_complete():
       q=time.perf_counter();actions=engine.legal_actions();query_ms=(time.perf_counter()-q)*1000
       if not actions:result.update(status='dead_end_proxy',dead_end=str(engine.dead_end()),query_empty_ms=query_ms);break
       action=actions[rng.randrange(len(actions))];q=time.perf_counter();engine.push(action);result['steps'].append(dict(legal_actions=len(actions),query_ms=query_ms,push_ms=(time.perf_counter()-q)*1000,action=repr(action)))
      else:result['status']='complete_proxy'
     result['diagnostics']=engine.screen_diagnostics()
    except Exception as exc:result.update(status='cpu_limit'if isinstance(exc,TimeoutError)else'error',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
    result.update(wall_s=time.perf_counter()-phase,cpu_s=time.process_time()-c0);save(output,result);row['rollouts'].append(result);save(path,row);duty()
   row['rollouts_complete']=len(row['rollouts'])==n
 except Exception as exc:row.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
 finally:
  after=snapshot(on);save(folder/'source-after.json',after);row.update(completed=True,wall_s=time.perf_counter()-started,cpu_s=time.process_time()-cpu_start,sleep_s=sleep_total,source_epoch_after=canonical(after),source_valid=before==after,source_drift=[k for k in before.keys()|after.keys()if before.get(k)!=after.get(k)],peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,loaded_modules={name:getattr(module,'__file__',None)for name,module in sys.modules.items()if name.startswith(('llm.','stowage.'))},mlx_imported=any(name.startswith(('mlx.','mlx_lm'))for name in sys.modules));save(path,row);print(json.dumps({k:row.get(k)for k in ['pid','mode','arm','completed','error','contract_passed','rollouts_complete','wall_s','cpu_s','sleep_s','source_valid','peak_rss_bytes']}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();run(json.loads(a.job.read_text()))
