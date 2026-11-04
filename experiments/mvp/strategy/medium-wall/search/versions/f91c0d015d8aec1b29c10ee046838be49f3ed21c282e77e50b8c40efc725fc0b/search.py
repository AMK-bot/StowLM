"""D191 train-only, CPU-only external search wrappers; no source mutation."""
from __future__ import annotations
import argparse, ast, contextlib, fcntl, hashlib, importlib, importlib.util, inspect, json, os
from pathlib import Path
import resource, signal, subprocess, sys, textwrap, time, traceback
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
RUNTIME=HERE/'runtime'
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS','STOWAGE_IN_PORT_WORKERS'):
 os.environ[key]='1'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(RUNTIME/'src'))
sys.path.append(str(ROOT))

def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads(Path(p).read_text())
def save(p,v):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
 q=p.with_name(p.name+f'.tmp-{os.getpid()}'); q.write_text(json.dumps(v,indent=2,sort_keys=True,default=str)+'\n'); q.replace(p)
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path); obj=importlib.util.module_from_spec(spec);sys.modules[name]=obj;spec.loader.exec_module(obj);return obj

def freeze():
 cohort=read(ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json')
 # Near-complete baseline failures: 120/121,120/121,228/229,427/428.
 seeds=[185100000,185100010,185100101,185100210]
 jobs=[dict(r) for r in cohort['maps'] if r['seed'] in seeds]
 assert len(jobs)==4 and cohort['split']=='train'
 configs=[
 dict(name='long_unchanged',budget_s=3600,beam_width=4,greedy_cap_s=10,widen=False,staged=False,port_blocks=False),
 dict(name='long_greedy',budget_s=3600,beam_width=4,greedy_cap_s=1500,widen=False,staged=False,port_blocks=False),
 dict(name='staged_hatch',budget_s=3600,beam_width=16,greedy_cap_s=1500,widen=False,staged=True,port_blocks=False),
 dict(name='foreaft_wide',budget_s=3600,beam_width=32,greedy_cap_s=900,widen=True,staged=True,port_blocks=True),
 dict(name='constructor_wide',budget_s=3600,beam_width=32,greedy_cap_s=10,widen=True,staged=False,port_blocks=False)]
 record=dict(created_unix=time.time(),cohort_sha256=digest(ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json'),snapshot_sha256=read(RUNTIME/'snapshot.json')['snapshot_sha256'],split='train',jobs=jobs,configs=configs,workers_max=4,threads_per_worker=1,selection='Outcome-selected diagnostic near-completion failures; two feeder500 yard families (120/121), feeder800 random replicate1 (228/229), feeder1700 simulated replicate0 (427/428). No unbiased rescue-rate claim.',hypotheses=['Same settings with 1h allowance distinguishes bounded exhaustion from deadlines','Extending greedy cap beyond10s may recover the last containers','Whole-hatch hold concentration can avoid expensive covers and fragmentation','Fore-aft paired stations avoid side separation; boundary sharing prevents aggregate TEU shortfall','Widened fallback allows32768 backtracks per strategy,16strategies,12 full-fact cargo profiles; greedy128 slot alternatives'],completion_gate='Solver completion is provisional; physical replay, terminal, finisher and full checker mandatory. No infeasibility claims.')
 p=HERE/'preregister.json'
 if p.exists():
  old=read(p); assert old['jobs']==jobs and old['configs']==configs; return old
 save(p,record);return record

TELEMETRY={}
LAST_WRITE=0.
def capture(canonical,engine,trace,ex):
 global LAST_WRITE
 depth=len(trace); TELEMETRY['nodes_seen']=TELEMETRY.get('nodes_seen',0)+1
 if depth>TELEMETRY.get('max_depth',-1):
  TELEMETRY['max_depth']=depth
  positions={cid:where[1] for cid,where in ex.where.items() if where[0]=='ship'}
  TELEMETRY['deepest_positions']=positions
  TELEMETRY['remaining']=[c for c in canonical.load_list if c not in positions]
  TELEMETRY['max_aboard']=len(positions)
 if time.monotonic()-LAST_WRITE>30:
  save(TELEMETRY['path'],{k:v for k,v in TELEMETRY.items() if k!='path'});LAST_WRITE=time.monotonic()
def exhausted(engine):
 TELEMETRY['exhausted_prefixes']=TELEMETRY.get('exhausted_prefixes',0)+1
 if TELEMETRY['exhausted_prefixes']<16:
  try:
   reason=engine.dead_end()
   TELEMETRY.setdefault('dead_end_samples',[]).append(str(reason))
  except Exception as exc:TELEMETRY.setdefault('diagnostic_errors',[]).append(str(exc))

def patch(config,folder):
 import stowage.solver.twostep as tw
 assert Path(tw.__file__).resolve().is_relative_to(RUNTIME)
 tw.capture=capture;tw.exhausted=exhausted
 src=inspect.getsource(tw._fast_prestow)
 src=src.replace('engine, trace, expander, iterator = nodes[-1]','engine, trace, expander, iterator = nodes[-1]\n        capture(canonical, engine, trace, expander)')
 src=src.replace('        else:\n            nodes.pop()','        else:\n            exhausted(engine)\n            nodes.pop()')
 if config.get('restricted_first'):
  old='return (cid in load, ranks[cid],\n                    not bool(b.dg or b.reefer or b.oog or b.special_stowage),'
  new='return (cid in load, not bool(b.dg or b.reefer or b.oog or b.special_stowage),\n                    ranks[cid],'
  assert src.count(old)==1
  src=src.replace(old,new)
 if config['staged']:
  src=src.replace('    hull = canonical.vessel.hull', '    cover_station = {h.id: tuple(sorted({stacks[sid].bay for sid in h.hold_stack_ids+h.deck_stack_ids if sid in stacks})) for h in canonical.vessel.hatch_covers}\n    def station(covers): return tuple(sorted({cover_station.get(c, ()) for c in covers}))\n    hull = canonical.vessel.hull')
  src=src.replace('        stack_pod, hatch_pod = {}, {}','        stack_pod, hatch_pod = {}, {}\n        station_fill = {}')
  src=src.replace('            m = boxes[cid].gross_mass_kg','            station_fill[station(covers)] = station_fill.get(station(covers), 0) + (not deck)\n            m = boxes[cid].gross_mass_kg')
  src=src.replace('return (restow, overstows, deck, height,','return (restow, deck, -station_fill.get(station(covers), 0), height, overstows,')
 p=folder/'effective_fast_prestow.py';p.write_text(src);exec(compile(src,str(p),'exec'),tw.__dict__)
 src=inspect.getsource(tw._preplan)
 old='greedy_budget = min(10., .4 * max(0., deadline - now))'
 assert old in src
 src=src.replace(old,f"greedy_budget = min({config['greedy_cap_s']!r}, .9 * max(0., deadline - now))")
 p=folder/'effective_preplan.py';p.write_text(src);exec(compile(src,str(p),'exec'),tw.__dict__)
 if config['widen']:
  import stowage.solver.twostep_construct as tc
  original=tc.ScalableConfig
  def widened(*a,**kw):
   kw.update(backtrack_limit=32768,max_restarts=config.get('max_restarts',15),beam_width=16,branch_factor=64,early_beam_width=32,candidate_boxes=64,candidate_slots=128)
   if config.get('securing_screen'):
    kw.update(securing_screen=True,heavy_first_ordering=True)
   return original(*a,**kw)
  tc.ScalableConfig=widened
  import stowage.solver.construct_v4 as cv
  src=inspect.getsource(cv._choices);assert 'reps[:min(3, len(reps))]' in src
  src=src.replace('reps[:min(3, len(reps))]','reps[:min(12, len(reps))]')
  p=folder/'effective_constructor_choices.py';p.write_text(src);exec(compile(src,str(p),'exec'),cv.__dict__)
 if config.get('carrier_fraction'):
  src=textwrap.dedent(inspect.getsource(tw.TwoStepPlanner.plan))
  old='deadline=start + limit * 0.5';assert src.count(old)==1
  src=src.replace(old,f"deadline=start + limit * {config['carrier_fraction']!r}")
  p=folder/'effective_planner_method.py';p.write_text(src);exec(compile(src,str(p),'exec'),tw.__dict__)
  tw.TwoStepPlanner.plan=tw.plan
 import stowage.solver.construct_v4 as cv
 observed_choices=cv._choices
 def observe_choices(instance,node,future,rng,metrics,attempt,deadline=float('inf'),**kw):
  counter=metrics.get('v4_reservation_prunes',0)
  probes_before=metrics.get('legal_candidates',0)
  values=observed_choices(instance,node,future,rng,metrics,attempt,deadline,**kw)
  obs=TELEMETRY.setdefault('constructor',dict(max_depth=-1,empty_prefixes=0))
  if node.engine.depth>obs['max_depth']:
   obs['max_depth']=node.engine.depth
   obs['positions']={cid:loc[1] for cid,loc in node.ex.where.items() if loc[0]=='ship'}
   obs['remaining']=[cid for cid in instance.load_list if cid not in obs['positions']]
  if not values:
   obs['empty_prefixes']+=1
   cause=('deadline' if time.monotonic()>=deadline else 'reservation_prefix_deficit' if metrics.get('v4_reservation_prunes',0)>counter else 'native_domain_empty' if metrics.get('legal_candidates',0)==probes_before else 'selected_profiles_only_forced_restows')
   histogram=obs.setdefault('empty_causes',{});histogram[cause]=histogram.get(cause,0)+1
   if obs['empty_prefixes']<=24:
    obs.setdefault('samples',[]).append(dict(depth=node.engine.depth,attempt=attempt,reservation_pruned=metrics.get('v4_reservation_prunes',0)>counter,deficits=metrics.get('v4_last_deficits'),capacity=getattr(future,'last_capacity',None),legal_pair_count=metrics.get('legal_candidates',0)-probes_before,deadline_expired=time.monotonic()>=deadline))
   if obs['empty_prefixes']<=4 and metrics.get('v4_reservation_prunes',0)>counter:
    remaining=[cid for cid in instance.load_list if node.ex.where.get(cid,(None,))[0]!='ship']
    try:
     probe=module('medium_wall_prefix_diagnosis',HERE/'diagnose_prefix.py')
     obs.setdefault('relaxation_probes',[]).append(dict(depth=node.engine.depth,**probe.diagnose(future,node.ex,remaining)))
    except Exception as exc:obs.setdefault('relaxation_probe_errors',[]).append(str(exc))
  if time.monotonic()-TELEMETRY.get('constructor_last_flush',0)>30:
   TELEMETRY['constructor_last_flush']=time.monotonic()
   save(TELEMETRY['path'],{k:v for k,v in TELEMETRY.items() if k!='path'})
  return values
 cv._choices=observe_choices
 return tw

class Deadline(BaseException):pass
def alarm(signum,frame):raise Deadline('Hard search worker watchdog')

def worker(mapid,configname):
 record=read(HERE/'preregister.json');job=next(r for r in record['jobs'] if r['map_id']==mapid);extras=read(HERE/'supplemental-config.json')['configs'] if (HERE/'supplemental-config.json').exists() else [];config=next(c for c in record['configs']+extras if c['name']==configname)
 config=dict(config)
 if configname=='constructor_wide':
  config.update(securing_screen=True,heavy_first_ordering=True,max_restarts=3,carrier_fraction=.9,amendment='amendment-contiguous-constructor.json')
 past=[read(p) for p in (HERE/'runs'/mapid).glob('*/summary.json')]
 used=sum(float(r.get('elapsed_s',0)) for r in past)
 remaining_configs=sum(not (HERE/'runs'/mapid/c['name']/'summary.json').exists() for c in record['configs'])
 map_cap=3000 if job['ship']=='feeder_500' else 3600
 if configname!='long_unchanged':
  config['budget_s']=max(0.,min(config['budget_s'],(map_cap-10.-used)/max(1,remaining_configs)))
 config['aggregate_map_budget_s']=map_cap
 config['prior_used_s']=used
 folder=HERE/'runs'/mapid/configname;folder.mkdir(parents=True,exist_ok=True)
 if (folder/'summary.json').exists():return
 for helper in (HERE/'search.py',HERE/'diagnose_prefix.py'):
  (folder/('wrapper-'+helper.name)).write_bytes(helper.read_bytes())
 save(folder/'worker.json',dict(pid=os.getpid(),started_unix=time.time(),config=config,input_sha256=job['sha256'],snapshot_sha256=record['snapshot_sha256'],wrapper_sha256=digest(Path(__file__)),external_sources={p.name:digest(p) for p in (HERE/'search.py',HERE/'diagnose_prefix.py')}))
 assert digest(ROOT/job['input'])==job['sha256']
 from stowage.io import load_instance
 from stowage.feasibility import FeasibilityConfig
 from stowage.rules.catalogue import rule_freeze_v1
 from stowage.moves import apply_plan
 inst=load_instance(ROOT/job['input'])
 TELEMETRY.update(path=folder/'greedy-progress.json',max_depth=-1,started_unix=time.time())
 tw=patch(config,folder)
 layoutmod=module('medium_wall_layout',RUNTIME/'experiments/mvp/strategy/headroom/layout.py')
 # The pinned rule imports layout using its package name. Bind exactly the pinned copy.
 sys.modules['experiments.mvp.strategy.headroom.layout']=layoutmod
 rule=module('experiments.mvp.strategy.blockrule.rule',RUNTIME/'experiments/mvp/strategy/blockrule/rule.py')
 # rule.py adjusts sys.path at import; restore pin precedence before dependent imports.
 sys.path.insert(0,str(RUNTIME/'src'))
 capacity=module('medium_wall_capacity',RUNTIME/'experiments/mvp/strategy/blockrule/capacity_candidate.py')
 model=layoutmod.BlockModel.from_instance(inst)
 layout=capacity.capacity_relaxed_layout(inst,model) if config['port_blocks'] else None
 if layout:save(folder/'layout.json',layout.to_dict())
 context=layoutmod.constraint_rule(inst,layout,model) if layout else contextlib.nullcontext()
 summary=dict(map_id=mapid,config=config,solver_complete=False,checker_certified=False,pid=os.getpid(),snapshot_sha256=record['snapshot_sha256'])
 signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,max(.1,min(config['budget_s']+180,map_cap-used-5)))
 start=time.monotonic()
 try:
  with rule_freeze_v1(enabled=False),context:
   result=tw.TwoStepPlanner(beam_width=config['beam_width'],feasibility_config=FeasibilityConfig(extra={'global_screen':0})).plan(inst,seed=0,time_limit_s=config['budget_s'])
  summary.update(status=result.status,message=result.message,metrics=result.metrics,wall_time_s=result.wall_time_s,has_plan=result.plan is not None)
  save(folder/'result.json',result.model_dump(mode='json'))
  if result.plan is not None:
   replay=apply_plan(inst,result.plan)
   issues=layoutmod.audit_layout(inst,result.plan,layout,model) if layout else []
   summary.update(replay_legal=replay.legal,replay_rules=[v.rule_id for v in replay.violations],layout_violations=issues,solver_complete=result.status=='ok' and replay.legal and not issues)
   if summary['solver_complete']:
    from stowage.conventions import cells_of_slot
    boxes={b.id:b for b in inst.containers};cells={c.id:c for c in inst.vessel.cells};mass=mx=my=mz=0.
    for p in replay.final_state.ship.placements:
     b=boxes[p.container_id];support=[cells[c] for c in cells_of_slot(p.slot)];m=b.gross_mass_kg;mass+=m
     mx+=m*sum(c.lcg_m for c in support)/len(support);my+=m*sum(c.tcg_m for c in support)/len(support);mz+=m*sum(c.vcg_base_m+b.height_m/2 for c in support)/len(support)
    summary['cargo']=dict(mass_kg=mass,lcg_m=mx/mass,tcg_m=my/mass,vcg_m=mz/mass)
 except BaseException as exc:
  summary.update(status='watchdog_timeout' if isinstance(exc,Deadline) else 'exception',error=str(exc),traceback=traceback.format_exc())
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  usage=resource.getrusage(resource.RUSAGE_SELF)
  summary.update(elapsed_s=time.monotonic()-start,completed_unix=time.time(),cpu_user_s=usage.ru_utime,cpu_system_s=usage.ru_stime,max_rss_bytes=usage.ru_maxrss)
  save(folder/'greedy-progress.json',{k:v for k,v in TELEMETRY.items() if k!='path'})
  save(folder/'summary.json',summary)

def controller(detach=False):
 record=freeze()
 heartbeat=HERE/'progress.json'
 if heartbeat.exists() and not (HERE/'completion.json').exists() and time.time()-heartbeat.stat().st_mtime<30:
  raise RuntimeError('Existing controller heartbeat is fresh; do not duplicate active workers')
 lock=(HERE/'controller.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise RuntimeError('Search controller lock is held')
 if detach:
  fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
  command=[sys.executable,str(Path(__file__).resolve()),'run']
  with (HERE/'controller.log').open('a') as log:p=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  save(HERE/'launch.json',dict(pid=p.pid,command=command,started_unix=time.time()));print(p.pid);return
 save(HERE/'controller.json',dict(pid=os.getpid(),started_unix=time.time(),workers_max=4))
 queues={r['map_id']:list(record['configs']) for r in record['jobs']};active={};started=[]
 while queues or active:
  for mapid in list(queues):
   if mapid in active:continue
   while queues[mapid] and (HERE/'runs'/mapid/queues[mapid][0]['name']/'summary.json').exists():queues[mapid].pop(0)
   if not queues[mapid]:del queues[mapid];continue
   c=queues[mapid].pop(0);folder=HERE/'runs'/mapid/c['name'];folder.mkdir(parents=True,exist_ok=True)
   command=[sys.executable,str(Path(__file__).resolve()),'worker','--map',mapid,'--config',c['name']]
   with (folder/'worker.log').open('a') as log:p=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
   active[mapid]=(p,c['name']);started.append(dict(pid=p.pid,map_id=mapid,config=c['name'],command=command,started_unix=time.time()));save(HERE/'owned-pids.json',started)
  for mapid,(p,c) in list(active.items()):
   if p.poll() is not None:
    if not (HERE/'runs'/mapid/c/'summary.json').exists():save(HERE/'runs'/mapid/c/'summary.json',dict(map_id=mapid,config=c,status='worker_exit',returncode=p.returncode))
    del active[mapid]
  save(HERE/'progress.json',dict(active=[dict(map_id=m,pid=p.pid,config=c) for m,(p,c) in active.items()],remaining=sum(len(v) for v in queues.values()),completed_unix=None if queues or active else time.time()))
  time.sleep(5 if active else .1)
 save(HERE/'completion.json',dict(completed_unix=time.time(),workers=len(started),snapshot_sha256=record['snapshot_sha256']))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','launch','run','worker']);p.add_argument('--map');p.add_argument('--config');a=p.parse_args()
 if a.mode=='freeze':freeze()
 elif a.mode=='worker':worker(a.map,a.config)
 else:controller(a.mode=='launch')
