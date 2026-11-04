"""Diagnostic exact-assignment then public mask/finisher; never a gold writer."""
from __future__ import annotations
import argparse,json,os,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
sys.dont_write_bytecode=True

# macOS spawn imports this file as __mp_main__; keep child cache writes scoped.
if __name__ == "__mp_main__":
    from cache_runtime import redirect_cache, enforce_scope
    redirect_cache(HERE / "runtime")
    enforce_scope()

def main():
 p=argparse.ArgumentParser();p.add_argument('--id',default='saved-025');a=p.parse_args()
 from cache_runtime import redirect_cache,enforce_scope
 redirect_cache(HERE/'runtime');enforce_scope()
 from stowage.io import load_instance,save_plan
 from stowage.solver.construct import _build_assignment_model,_selected,_sequence,SequenceFailure
 from stowage.solver.ballast import finish_ballast
 from stowage.checker import check
 from stowage.checker.rules import check_static_rules
 from stowage.model import Placement
 row=next(r for r in json.loads((HERE/'selection-saved.json').read_text()) if r['id']==a.id)
 i=load_instance(row['path']);out=HERE/'diagnostics'/a.id;out.mkdir(parents=True,exist_ok=True)
 start=time.monotonic();r={'input':row['path'],'instance_id':i.id,'id':a.id,'diagnostic':True,'phase':'building'}
 def save(**kw):
  r.update(kw);r['wall_s']=time.monotonic()-start;(out/'probe.json').write_text(json.dumps(r,indent=2,default=str)+'\n')
 m=_build_assignment_model(i,physics=False);m.solver.SetTimeLimit(60000);status=m.solver.Solve();save(mip_status=status,phase='assignment',assignment_s=time.monotonic()-start)
 if status not in (m.solver.OPTIMAL,m.solver.FEASIBLE):return
 assignment=_selected(m);(out/'assignment.json').write_text(json.dumps(assignment,indent=2)+'\n')
 ship=i.onboard.model_copy(update={'placements':[p for p in i.onboard.placements if p.container_id not in i.discharge_list]+[Placement(container_id=c,slot=s) for c,s in assignment.items()]})
 static=check_static_rules(i,ship,call_index=i.call_index);save(static_pass=not any(v.severity=='hard' for v in static.violations),static_violations=[v.model_dump(mode='json') for v in static.violations if v.severity=='hard'])
 try:
  actions,plan=_sequence(i,assignment,time.monotonic()+90);save(mask_pass=True,actions=len(actions))
 except (ValueError,TimeoutError) as e:
  save(mask_pass=False,sequence_error=str(e),sequence_details=getattr(e,'details',None))
  actions,plan=_sequence(i,assignment,time.monotonic()+30,replay_mask=False)
 save_plan(plan,out/'cargo.plan.json',allow_stubs=True);save(phase='finisher')
 outcome=finish_ballast(i,plan,time_limit_s=30);save(finisher_status=outcome.status)
 if outcome.plan:
  report=check(i,outcome.plan);save(checker_pass=report.passed,hard_rules=sorted({v.rule_id for v in report.violations if v.severity=='hard'}))
  (out/'report.json').write_text(report.model_dump_json(indent=2)+'\n');save_plan(outcome.plan,out/'final.plan.json',allow_stubs=True)
 save(phase='finished')
 from stowage.solver.ballast_deadline import shutdown_worker
 shutdown_worker()
if __name__=='__main__':main()
