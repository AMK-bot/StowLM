"""Diagnostic only: native C1/local sequence, C2 disabled, exact finisher/checker.

Never counted as production mask, construction success or shippable evidence.
"""
from __future__ import annotations
import json,os,signal,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;BASE=HERE.parent
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
sys.path.insert(0,str(BASE));sys.dont_write_bytecode=True
from cache_runtime import redirect_cache
redirect_cache(BASE/'runtime')

def main():
 from stowage.io import load_instance,save_plan
 from stowage.solver.construct import _build_assignment_model,_selected,_nogood
 from stowage.solver import construct_sequence as seq
 from stowage.feasibility import make_feasibility_engine,FeasibilityConfig
 from stowage.checker.rules import check_static_rules
 from stowage.checker import check
 from stowage.model import Placement
 from stowage.solver.ballast import finish_ballast
 from stowage.solver.ballast_deadline import shutdown_worker
 row=next(r for r in json.loads((HERE/'selection-saved.json').read_text())['eligible'] if r['id']=='saved-025');i=load_instance(row['path']);out=HERE/'diagnostics/static-native-saved025';out.mkdir(parents=True,exist_ok=True)
 start=time.monotonic();deadline=start+150;r={'instance_id':i.id,'input':row['path'],'diagnostic_only':True,'production_mask':False,'shippable':False,'pid':os.getpid(),'stage':'building'}
 def save(**kw):r.update(kw);r['wall_s']=time.monotonic()-start;(out/'result.json').write_text(json.dumps(r,indent=2,default=str)+'\n')
 def expired(*_):raise TimeoutError('static diagnostic150s cap')
 signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,150)
 seq.make_feasibility_engine=lambda inst,space:make_feasibility_engine(inst,space,config=FeasibilityConfig(extra={'global_screen':0}))
 try:
  m=_build_assignment_model(i,physics=False,deadline=deadline);save(stage='assignment')
  for iteration in range(3):
   m.solver.SetTimeLimit(int(min(60,max(.001,deadline-time.monotonic()))*1000));status=m.solver.Solve();save(round=iteration,mip_status=status)
   if status not in (m.solver.OPTIMAL,m.solver.FEASIBLE):break
   assignment=_selected(m);ship=i.onboard.model_copy(update={'placements':[Placement(container_id=c,slot=s) for c,s in assignment.items()]});v=check_static_rules(i,ship,call_index=i.call_index)
   hard=[x.model_dump(mode='json') for x in v.violations if x.severity=='hard'];save(static_hard=hard)
   if hard:_nogood(m,assignment);continue
   (out/'assignment.json').write_text(json.dumps(assignment,indent=2)+'\n');save(stage='C1_local_sequence')
   diagnostics={}
   try:actions,plan=seq.sequence(i,assignment,min(deadline-32,time.monotonic()+45),diagnostics=diagnostics)
   except ValueError as e:save(sequence_error=str(e),sequence_details=getattr(e,'details',{}));_nogood(m,assignment);continue
   save(actions=len(actions),sequence_diagnostics=diagnostics,stage='finishing');save_plan(plan,out/'cargo.plan.json',allow_stubs=True)
   outcome=finish_ballast(i,plan,time_limit_s=min(30,max(.01,deadline-time.monotonic())));save(finisher_status=outcome.status)
   if outcome.plan:
    report=check(i,outcome.plan);save(checker_passed=report.passed,hard_rules=sorted({v.rule_id for v in report.violations if v.severity=='hard'}));(out/'report.json').write_text(report.model_dump_json(indent=2)+'\n');save_plan(outcome.plan,out/'final.plan.json',allow_stubs=True)
    if report.passed:break
   _nogood(m,assignment)
  save(stage='finished')
 except Exception as e:save(stage='failed',error=f'{type(e).__name__}: {e}')
 finally:signal.setitimer(signal.ITIMER_REAL,0);shutdown_worker()
if __name__=='__main__':main()
