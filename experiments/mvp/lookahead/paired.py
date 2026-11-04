"""Paired action-uniform rollouts, real factories and shared exact IRS memoization."""
from worker import HERE,ROOT,sha,canonical,save,snapshot,priority,cpu_cap
import argparse,json,random,resource,sys,time,traceback
from datetime import datetime,timezone
from pathlib import Path

def run(job):
 start=time.perf_counter();cstart=time.process_time();sleep=0.;folder=Path(job['directory']);folder.mkdir(parents=True,exist_ok=True);path=folder/'result.json';assert not path.exists(),'Use a new immutable job directory'
 before=snapshot(True);save(folder/'source-before.json',before);row=dict(pid=__import__('os').getpid(),job=job,started_utc=datetime.now(timezone.utc).isoformat(),completed=False,worker_sha256=sha(__file__),helper_sha256=sha(HERE/'worker.py'),source_epoch_before=canonical(before),priority_attempts=priority(),duty_fraction=job.get('duty_fraction',.9),sleep_s=0,rollouts=[],matched_prefixes=[],native_threads=1,gpu=False,checker_run=False,physical_acceptance_claim=False,cache_policy='Actual baseline and final-default ON factories; same NecessaryDeckSecuringScreen exact cache, independent native roots/expanders; no reused divergent legal sets.',policy_order='production -> lookahead -> securing outer')
 save(path,row)
 def duty():
  nonlocal sleep
  needed=(time.process_time()-cstart)/row['duty_fraction']-(time.perf_counter()-start)
  if needed>0:
   s=time.perf_counter();time.sleep(needed);sleep+=time.perf_counter()-s
 try:
  if job.get('freeze_manifest'):
   frozen=json.loads(Path(job['freeze_manifest']).read_text());assert before==frozen['lookahead_runtime_sources'],'Runtime epoch drift'
  from stowage.io import load_instance
  from stowage.actions import get_action_space
  from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
  from stowage.solver.securing_screen import NecessaryDeckSecuringScreen
  from llm.stowlm.format import ActionCodec,load_tokenizer
  from llm.stowlm.mask import MaskConfig
  from llm.securing import SecuringEngine,securing_decoder_factory
  from llm.lookahead import LookaheadConfig,LookaheadEngine,lookahead_decoder_factory
  from llm.lookahead.bounds import OptimisticCapacity
  case=job['case'];assert sha(case['instance']['path'])==case['instance']['sha256'];instance=load_instance(case['instance']['path']);space=get_action_space();n=job.get('rollouts',20);phase=time.perf_counter();native=make_feasibility_engine(instance,space,config=FeasibilityConfig(extra={'global_screen':0}));codec=ActionCodec(load_tokenizer(ROOT/'models/glimmer-30b-4bit/tokenizer.json'),[b.id for b in instance.containers]);row['native_codec_initialization_s']=time.perf_counter()-phase;row['tokenizer_sha256']=sha(ROOT/'models/glimmer-30b-4bit/tokenizer.json');config=LookaheadConfig(**job.get('lookahead_config',{}))
  phase=time.perf_counter();base=securing_decoder_factory()(instance,native.copy(),codec,MaskConfig(lookahead=0),n);shared=base[0].engine.screen;row['baseline_factory_s']=time.perf_counter()-phase
  phase=time.perf_counter();on=lookahead_decoder_factory(securing_screen=shared,config=config)(instance,native.copy(),codec,MaskConfig(lookahead=0),n);row['lookahead_factory_s']=time.perf_counter()-phase;assert on[0].engine.screen is shared and on[0].engine.expander is not base[0].engine.expander
  phase=time.perf_counter();capacity=OptimisticCapacity(instance,config);row['matched_static_geometry_init_s']=time.perf_counter()-phase
  for repetition in range(n):
   seed=int.from_bytes(__import__('hashlib').sha256(f"lookahead-v1:{case['instance']['sha256']}:{repetition}".encode()).digest()[:8],'big')
   for arm,decoder in [('baseline',base[repetition]),('lookahead',on[repetition])]:
    engine=decoder.engine;rng=random.Random(seed);began=time.perf_counter();c0=time.process_time();result=dict(arm=arm,repetition=repetition,seed=seed,steps=[],status=None,matched_probe_cpu_s=0.,matched_probe_wall_s=0.)
    try:
     with cpu_cap(job.get('cpu_cap_s',120)):
      while not engine.is_complete():
       phase=time.perf_counter();actions=engine.legal_actions();qms=(time.perf_counter()-phase)*1000
       if not actions:result.update(status='dead_end_proxy',dead_end=str(engine.dead_end()),query_empty_ms=qms);break
       if arm=='baseline'and repetition==0 and job.get('matched_prefix_overhead',True):
        p0=time.perf_counter();pc0=time.process_time();phase=time.perf_counter();cached=engine.legal_index();base_ms=(time.perf_counter()-phase)*1000;phase=time.perf_counter();inner=LookaheadEngine(engine.inner.copy(),config=config,capacity=capacity);probe=SecuringEngine(inner,screen=shared);init_ms=(time.perf_counter()-phase)*1000;phase=time.perf_counter();filtered=probe.legal_index();query_ms=(time.perf_counter()-phase)*1000;row['matched_prefixes'].append(dict(repetition=repetition,depth=engine.depth,baseline_cached_query_ms=base_ms,new_default_query_ms=query_ms,wrapper_initialization_ms=init_ms,baseline_actions=len(cached)+len(engine.legal_discharges()),lookahead_actions=len(filtered)+len(probe.legal_discharges()),lookahead_diagnostics=probe.screen_diagnostics().get('lookahead')));result['matched_probe_cpu_s']+=time.process_time()-pc0;result['matched_probe_wall_s']+=time.perf_counter()-p0
       action=actions[rng.randrange(len(actions))];phase=time.perf_counter();engine.push(action);result['steps'].append(dict(legal_actions=len(actions),query_ms=qms,push_ms=(time.perf_counter()-phase)*1000,action=repr(action)))
      else:result['status']='complete_proxy'
     result['diagnostics']=engine.screen_diagnostics()
    except Exception as exc:result.update(status='cpu_limit'if isinstance(exc,TimeoutError)else'error',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
    result.update(wall_s=time.perf_counter()-began,cpu_s=time.process_time()-c0);result['sampling_cpu_s']=result['cpu_s']-result['matched_probe_cpu_s'];result['sampling_wall_s']=result['wall_s']-result['matched_probe_wall_s'];save(folder/f'{arm}-{repetition:02}.json',result);row['rollouts'].append(result);save(path,row);duty()
  assert not native.actions()and native.depth==0
  row['rollouts_complete']=len(row['rollouts'])==2*n
 except Exception as exc:row.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
 finally:
  after=snapshot(True);save(folder/'source-after.json',after);row.update(completed=True,wall_s=time.perf_counter()-start,cpu_s=time.process_time()-cstart,sleep_s=sleep,source_valid=before==after,source_epoch_after=canonical(after),source_drift=[k for k in before.keys()|after.keys()if before.get(k)!=after.get(k)],peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,mlx_imported=any(k.startswith(('mlx.','mlx_lm'))for k in sys.modules));save(path,row);print(json.dumps({k:row.get(k)for k in ['pid','completed','rollouts_complete','error','wall_s','cpu_s','sleep_s','peak_rss_bytes','source_valid']}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();run(json.loads(a.job.read_text()))
