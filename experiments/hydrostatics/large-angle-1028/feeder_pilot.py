"""Short follow-up pilot: instrument an unchanged direct stability check."""
import json, os, resource, time
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
os.environ['STOWAGE_STABILITY_DIRECT']='1'
os.environ['STOWAGE_STABILITY_TIME_BUDGET_S']='120'
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_manual import standard_manual_conditions
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.conventions import make_container_id
from stowage.checker.stability import check_stability
import stowage.checker.stability_curve as sc
start=time.perf_counter(); name='feeder_500'
v=load_vessel(name); p=load_ship_params(name)
recipe=next(r for r in standard_manual_conditions(v,p) if r['condition']=='C1')
# The shared historical fixture currently updates only one of two crane-height
# aliases. Keep them equal in this experiment's validated synthetic instance.
payload=build_instance().model_dump(mode='python')
containers=[]; placements=[]
for serial,(cid,mass) in enumerate(recipe['cargo_cells'],1):
 identifier=make_container_id('QZXU',serial)
 containers.append(dict(id=identifier,iso_type='22G1',length_ft=20,height_m=2.591,
  tare_kg=2200.,gross_mass_kg=mass,max_gross_mass_kg=30480.,pol='THLCH',pod='THBKK'))
 placements.append(dict(slot=cid,container_id=identifier))
payload.update(id=f'large-angle-{name}-C1',vessel=v,containers=containers,
 load_list=[],discharge_list=[],onboard=dict(vessel_id=v.id,placements=placements,
 tank_masses_kg=dict(recipe['departure_tank_masses_kg'])))
payload['yard']['stacks']=[]
tiers=max(len(s.cell_ids) for s in v.stacks if s.on_deck)
payload['port']['max_crane_height_tiers']=tiers
payload['port']['quay_crane']['max_on_deck_tiers']=tiers
payload['port']['quay_crane']['max_lift_height_m']=60.
payload['port']['max_draught_m']=v.hull.max_draught_m
for call in payload['rotation']['calls']: call['max_on_deck_tiers']=None
i=attach_default_voyage(Instance.model_validate(payload))
print('build_s',time.perf_counter()-start,flush=True)
old=sc.build_stability_curve; got=[]
def capture(*args,**kwargs):
 out=old(*args,**kwargs); got.append((args,kwargs,out)); return out
sc.build_stability_curve=capture
rep=check_stability(i,i.onboard,call_index=i.call_index)
print('check_s',time.perf_counter()-start,'ran',rep.ran,'error',rep.error,flush=True)
print(json.dumps(dict(metrics=rep.metrics,violations=[v.model_dump(mode='json') for v in rep.violations]),indent=2),flush=True)
if got:
 args,kwargs,curve=got[-1]
 for deg in (0,10,20,30,40,50):
  import math
  print(deg,curve.at_angle(math.radians(deg)),flush=True)
 print('geometry',type(args[0]),'condition',args[1].mass_kg,args[1].lcg_m,args[1].kg_m,args[1].fsc_m,flush=True)
print('elapsed_s',time.perf_counter()-start,'rss_bytes',resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,flush=True)
