"""Freeze a separately labelled synthetic, independently full-checked witness.

This uses the integration test's declared balancing envelope and broad synthetic
strength ratings. It is a search regression, never production throughput data.
The single dry 20-foot export is real cargo and the complete move sequence is
replayed by the unchanged full checker. No regulatory threshold is overridden.
"""
import json
import os
from pathlib import Path
import runpy
import time

for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


if __name__=='__main__':
    from stowage.io import save_instance,save_plan
    from stowage.model import BallastCondition,Instance,MovePlan
    from stowage.model.moves import HatchCoverOff,HatchCoverOn,Load
    from stowage.checker.full import check
    from stowage.solver.ballast_deadline import run_with_deadline
    helpers=runpy.run_path(str(ROOT/'tests/solver/test_ballast_integration.py'))
    instance=helpers['_fleet_context']('feeder_500')
    template=next(c for c in instance.containers if c.id=='QZXU0000035')
    # A synthetic ordinary dry box, with no shipper special request. Retain
    # its declared mass/size and use a declared synthetic CSC rating.
    target=template.model_copy(update={'special_stowage':[], 'max_gross_mass_kg':36000.})
    instance.containers=[target if c.id==target.id else c for c in instance.containers]
    instance.load_list=[target.id]
    instance=Instance.model_validate(instance.model_dump())
    stacks={s.id:s for s in instance.vessel.stacks}
    cells=[next(c for c in instance.vessel.cells if c.id==stack.cell_ids[0])
           for stack in instance.vessel.stacks if int(stack.cell_ids[0][-2:])<80]
    cell=min(cells,key=lambda c:abs(c.lcg_m-instance.vessel.hull.lpp_m/2)+abs(c.tcg_m))
    cover=next(h for h in instance.vessel.hatch_covers if cell.stack_id in h.hold_stack_ids)
    plan=MovePlan(id='synthetic-feasible-one-export',instance_id=instance.id,call_index=0,moves=[
        HatchCoverOff(call_index=0,cover_id=cover.id),
        Load(call_index=0,container_id=target.id,src='A-003-01-01',dst=cell.id),
        HatchCoverOn(call_index=0,cover_id=cover.id)])
    witness=plan.model_copy(update={'ballast':BallastCondition(source='planner',tank_masses_kg={
        t.id:0. for t in instance.vessel.tanks if t.adjustable})})
    folder=HERE/'synthetic-witness'
    folder.mkdir(exist_ok=True)
    save_instance(instance,folder/'instance.json')
    save_plan(plan,folder/'input-plan.json')
    save_plan(witness,folder/'witness-plan.json')
    started=time.monotonic()
    report=run_with_deadline(check,(instance,witness),deadline=started+15.)
    (folder/'witness-check.json').write_text(report.model_dump_json(indent=2)+'\n')
    print(json.dumps(dict(passed=report.passed,wall_s=time.monotonic()-started,
        violations=[dict(rule_id=v.rule_id,message=v.message) for v in report.violations])),flush=True)
