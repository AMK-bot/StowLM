import sys,json,time
from pathlib import Path
from measure import draw
from stowage.rules.catalogue import rule_freeze_v1
from stowage.generate.background_acceptance import background_instance
from stowage.vessel.fleet_conditions import empty_background_witness_tank_masses
from stowage.model import MovePlan,BallastCondition
from stowage.checker import check
with rule_freeze_v1():
 i=draw('feeder_1700','small',7); b=background_instance(i); r=empty_background_witness_tank_masses(b.vessel)
 for t in b.vessel.tanks:
  if t.adjustable:b.onboard.tank_masses_kg[t.id]=r.get(t.id,0.)
 p=MovePlan(id='first-check',instance_id=b.id,call_index=b.call_index,moves=[],ballast=BallastCondition(source='planner',tank_masses_kg={t.id:b.onboard.tank_masses_kg.get(t.id,0.) for t in b.vessel.tanks if t.adjustable}))
 start=time.perf_counter();report=check(b,p);data=dict(elapsed_s=time.perf_counter()-start,port_max_draught_m=b.port.max_draught_m,report=report.model_dump(mode='json'))
 Path(__file__).with_name('first-explicit-witness-check.json').write_text(json.dumps(data,default=str));print('passed',report.passed,'time',data['elapsed_s'],flush=True)
 for n,x in report.parts.items():
  if x.error or x.violations:print(n,'ran',x.ran,'error',x.error,'violations',[(v.rule_id,v.message) for v in x.violations],flush=True)
