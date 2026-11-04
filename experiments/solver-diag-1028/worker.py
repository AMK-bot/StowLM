"""One pinned-source production planner→finisher→full-checker diagnostic."""
from __future__ import annotations
import argparse, hashlib, json, os, resource, signal, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
class InstanceDeadline(BaseException): pass

def save(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_suffix('.partial');temp.write_text(json.dumps(value,sort_keys=True,default=str)+'\n');temp.replace(path)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def report_row(report):
 return dict(checker_passed=report.passed,shut_out=report.shut_out,
  rules=sorted({v.rule_id for v in report.violations if v.severity=='hard'}),
  states={k:sorted({v.rule_id for v in p.violations if v.severity=='hard'}) for k,p in report.parts.items() if any(v.severity=='hard' for v in p.violations) or not p.ran},
  part_errors={k:p.error for k,p in report.parts.items() if not p.ran},
  violations=[v.model_dump(mode='json') for v in report.violations if v.severity=='hard'])
def main():
 p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--solver',choices=['exact','scalable','twostep','background'],required=True);p.add_argument('--budget',type=float,default=45);p.add_argument('--cap',type=float,default=120)
 a=p.parse_args();a.snapshot=a.snapshot.resolve();a.input=a.input.resolve();a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True)
 sys.path.insert(0,str(HERE.parents[1]/'src'));sys.dont_write_bytecode=True;os.chdir(HERE.parents[1])
 from cache_runtime import redirect_cache, enforce_scope
 redirect_cache(a.snapshot.parent/'runtime'); enforce_scope()
 import stowage.solver
 stowage.solver.__path__=[str(a.snapshot/'src/stowage/solver')]
 outside={str(p.relative_to(HERE.parents[1])):digest(p) for base in ('src/stowage','params') for p in (HERE.parents[1]/base).rglob('*') if p.is_file() and '__pycache__' not in str(p) and '/solver/' not in str(p)}
 
 t=time.monotonic(); row=dict(solver=a.solver,input=str(a.input),input_sha256=digest(a.input),diagnostic=True,evidence_eligible=False,budget_s=a.budget,cap_s=a.cap,checker_passed=None,shippable=False,ballast_status='not_reached',completion='incomplete')
 def checkpoint(**values):
  row.update(values);row['wall_s']=time.monotonic()-t;save(a.output/'result.json',row)
 def timeout(signum,frame):raise InstanceDeadline('diagnostic instance wall cap expired')
 signal.signal(signal.SIGTERM,timeout);signal.signal(signal.SIGALRM,timeout);signal.setitimer(signal.ITIMER_REAL,a.cap)
 try:
  from stowage.io import load_instance, save_plan
  from stowage.model import MovePlan, BallastCondition
  import stowage.checker as checker
  instance=load_instance(a.input);checkpoint(instance_id=instance.id,vessel=instance.vessel.id,n_loads=len(instance.load_list),source_module=checker.__file__)
  if a.solver=='background':
   plan=MovePlan(id=instance.id+'.background-control',instance_id=instance.id,call_index=instance.call_index,moves=[],ballast=BallastCondition(tank_masses_kg={},source='planner'))
   report=checker.check(instance,plan);checkpoint(**report_row(report),control='empty_moves_zero_adjustable_ballast',physical_rules=sorted({v.rule_id for v in report.violations if v.severity=='hard' and v.rule_id!='objective.all_loaded'}));save(a.output/'report.json',report.model_dump(mode='json'));return
  from stowage.planner import run_planner
  if a.solver=='exact':
   from stowage.solver.exact import ExactPlanner
   planner=ExactPlanner()
  elif a.solver=='scalable':
   from stowage.solver.scalable import ScalableSolver
   planner=ScalableSolver()
  else:
   from stowage.solver.twostep import TwoStepPlanner
   planner=TwoStepPlanner()
  # Capture raw cargo outcome without changing public pipeline behavior.
  original_plan=planner.plan
  def capture(*args,**kwargs):
   result=original_plan(*args,**kwargs)
   checkpoint(solver_status=result.status,solve_s=result.wall_time_s,solver_message=result.message,solver_metrics=result.metrics,has_plan=result.plan is not None)
   if result.plan is not None:
    save_plan(result.plan,a.output/'cargo.plan.json',allow_stubs=True)
    from stowage.moves import apply_plan
    replay=apply_plan(instance,result.plan)
    loaded={x.container_id for x in replay.final_state.ship.placements} if isinstance(replay.final_state.ship.placements,list) else set(replay.final_state.ship.placements)
    # Replay checker later supplies authoritative completeness; use LOAD move identities here.
    moved={getattr(m,'container_id',None) for m in result.plan.moves if getattr(m,'kind',None)=='load'}
    checkpoint(loaded_by_moves=len(moved & set(instance.load_list)),completion='complete' if set(instance.load_list)<=moved else 'incomplete')
   if result.status=='timeout':checkpoint(timed_out=True)
   return result
  planner.plan=capture
  result,report=run_planner(planner,instance,seed=0,time_limit_s=a.budget,allow_repair=False)
  values=dict(final_status=result.status,final_message=result.message,ballast_status=result.metrics.get('ballast_status','kept' if result.plan is not None and result.plan.ballast is not None else 'not_reached'),shippable=result.shippable,pipeline_metrics=result.metrics)
  if report is not None:
   values.update(report_row(report));values['completion']='complete' if not report.shut_out else 'incomplete';save(a.output/'report.json',report.model_dump(mode='json'))
  if result.plan is not None:save_plan(result.plan,a.output/'final.plan.json',allow_stubs=True)
  checkpoint(**values)
 except (Exception,InstanceDeadline) as exc:
  checkpoint(error=f'{type(exc).__name__}: {exc}',timed_out=isinstance(exc,(TimeoutError,InstanceDeadline)))
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  try:
   from stowage.solver.ballast_deadline import shutdown_worker
   shutdown_worker()
  except Exception:pass
  start_key=hashlib.sha256(json.dumps(outside,sort_keys=True).encode()).hexdigest()
  save(a.output/'external-source-start.json',outside)
  end={name:digest(HERE.parents[1]/name) for name in outside}
  save(a.output/'external-source-end.json',end)
  checkpoint(external_source_sha256=hashlib.sha256(json.dumps(outside,sort_keys=True).encode()).hexdigest(),external_source_drift=[k for k,v in outside.items() if end.get(k)!=v])
  scale=2**30 if sys.platform=='darwin' else 2**20
  checkpoint(peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/scale,child_peak_rss_gb=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/scale)
if __name__=='__main__':main()
