"""D58 builder diagnostic: full checker, unchanged physics, empty decision list."""
import sys,time,json
from pathlib import Path
from stowage.model import Vessel
from stowage.vessel import load_vessel,slice_preset,recondition_slice
from stowage.vessel.fleet_conditions import slice_condition,departure_tank_masses
from stowage.model.fixtures import build_instance
from stowage.model import Instance,MovePlan
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.checker import check
# Diagnostic cache reuse: D58 modifies no hull, tanks, policies or parameters.
# Canonical-policy validation otherwise rebuilds all KN curves on every cargo
# allocation source edit. Final tests must use the ordinary fleet loader.
from stowage.vessel import fleet
_real_load = fleet.load_vessel
_physical_parents = {}
def _diagnostic_load(name, *, with_hull=True):
 if with_hull and name in _physical_parents:
  return _physical_parents[name].model_copy(deep=True)
 return _real_load(name,with_hull=with_hull)
fleet.load_vessel = _diagnostic_load
COEF=dict(zip(('area_0_30_mrad','area_0_40_mrad','area_30_40_mrad','gz_at_or_above_30_m','gz_max_m','area_0_flood_mrad'),(.009,.016,.006,.033,.042,.029)))
for ship in (sys.argv[1:] or ['feeder_500','feeder_800','feeder_1700']):
 for size in __import__('os').environ.get('PROBE_SIZES','tiny,small,medium').split(','):
  start=time.monotonic()
  try:
   # Diagnostic only: hull production inputs do not change in D58. Reuse
   # the latest already assembled D56 hull while changing background code.
   cached=max(Path('experiments/cache/vessels').glob(ship+'.full.hull.*.json'),key=lambda p:p.stat().st_mtime)
   parent=Vessel.model_validate_json(cached.read_text())
   _physical_parents[ship]=parent
   assert parent.hull.max_gm_lashing_m is not None and parent.tanks and parent.voyage_policy.groups
   v=slice_preset(parent,size)
   mass={'tiny':50000,'small':250000,'medium':{'feeder_500':1900000,'feeder_800':3700000,'feeder_1700':6900000}[ship]}[size]
   v=recondition_slice(v,expected_load_mass_kg=mass,seed=1)
   p=build_instance().model_dump()
   p.update(vessel=v,id='bg-probe',containers=[],load_list=[],discharge_list=[],onboard=dict(vessel_id=v.id,placements=[],tank_masses_kg=departure_tank_masses(v)))
   p['yard']['stacks']=[];p['port']['max_draught_m']=v.hull.max_draught_m
   for call in p['rotation']['calls']:call['max_on_deck_tiers']=None
   inst=attach_default_voyage(Instance.model_validate(p)); Path('experiments/background-1028/'+ship+'-'+size+'.builder.instance.json').write_text(inst.model_dump_json());plan=MovePlan(id='empty',instance_id=inst.id,call_index=inst.call_index,moves=[])
   print('checking '+ship+' '+size, file=sys.stderr,flush=True); rep=check(inst,plan); states={}
   for k,part in rep.parts.items():
    if k.startswith('condition:') and k.endswith(':stability'):
     m=part.metrics;C=m.get('containership_form_factor')
     states[k]=dict(ratio=min(m[x]*C/c for x,c in COEF.items()) if C else None,gm=m.get('gm_m'),kg=m.get('kg_m'),weather={k:v for k,v in m.items() if k.startswith('weather')})
   lash={k:part.metrics for k,part in rep.parts.items() if k.startswith('condition:') and k.endswith(':lashing')}
   c=slice_condition(v)
   print(json.dumps(dict(ship=ship,size=size,states=states,violations=[dict(rule=x.rule_id,msg=x.message[:200],measured=x.measured,limit=x.limit) for x in rep.violations],lashing=lash,ballast=c['stability_ballast_mass_kg'],bg_t=c['fixed_cargo_mass_kg']/1000,screen=c['gm_screen'],wall=time.monotonic()-start)),flush=True)
  except Exception as e:print(json.dumps(dict(ship=ship,size=size,error=str(e),wall=time.monotonic()-start)),flush=True)
