"""D58 separate empty-background ballast witness pilot (unchanged checker).

A sounding-table nonlinear moment fit sizes a symmetric ballast recipe for a
minimum arrival draught. This is a design initializer, not a certificate: the
unchanged full checker evaluates all three D57 states independently.
"""
from pathlib import Path
from types import SimpleNamespace
import json,time,sys
import numpy as np
from scipy.optimize import linprog,minimize,brentq
from stowage.vessel import fleet
from stowage.vessel.fleet_balance import _hull,_known_points,stability_ballast_masses
from stowage.vessel.fleet_conditions import slice_condition,departure_tank_masses
from stowage.vessel.fleet_slices import slice_preset,recondition_slice
from stowage.model import Vessel,Instance,MovePlan
from stowage.model.fixtures import build_instance
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.checker import check
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from design_lashing import design_lashing
HERE=Path(__file__).resolve().parent


def size_witness(vessel,params,ratio=3.4):
 geometry=_hull(params);h=params['hull'];baseline=stability_ballast_masses(params)
 points=_known_points(params,vessel.fixed_loads,0.,(0.,0.,0.),baseline)
 base_mass=sum(p[3]for p in points);known_x=sum(p[3]*p[0]for p in points)
 consumables=sum(.97*t['capacity_m3']*t['content_density_kg_m3']for t in params['tanks']if not t.get('adjustable',False))
 retained=.1;arrival_draft=h['beam_m']/ratio
 target=max(base_mass,geometry.submerged(arrival_draft,arrival_draft).displacement_kg+(1-retained)*consumables)
 if target<=base_mass+1e-5:return baseline,dict(policy='booking_recipe_already_sufficient',target_departure_mass_kg=base_mass)
 draft=brentq(lambda d:geometry.submerged(d,d).displacement_kg-target,.1,h['design_draught_m']*1.1)
 lcb=geometry.submerged(draft,draft).lcb_m
 # Ballast variables are common P/S pair fill fractions, preserving symmetry.
 groups={}
 for tank in params['tanks']:
  if tank.get('adjustable',False):groups.setdefault(tank['id'].rsplit('-',1)[0],[]).append(tank)
 names=sorted(groups);capacities=np.array([sum(t['capacity_m3']*t['content_density_kg_m3']for t in groups[n])for n in names])
 lower=np.array([1. if n.startswith('DB-')else 0. for n in names]);upper=np.ones(len(names))
 # Remove the old ballast points before sizing the complete witness recipe.
 base_x=known_x
 for tank in params['tanks']:
  m=baseline.get(tank['id'],0.);base_x-=m*tank['table'][-1]['lcg_m']
 base_nonballast=base_mass-sum(baseline.values())
 wanted_mass=target-base_nonballast;wanted_x=target*lcb-base_x
 def moments(fractions):
  mass=x=z=fsm=0.
  for name,fraction in zip(names,fractions):
   for t in groups[name]:
    volume=t['capacity_m3']*fraction;m=volume*t['content_density_kg_m3'];rows=t['table'];vol=[r['volume_m3']for r in rows]
    mass+=m;x+=m*np.interp(volume,vol,[r['lcg_m']for r in rows]);z+=m*np.interp(volume,vol,[r['vcg_m']for r in rows]);fsm+=t['content_density_kg_m3']*np.interp(volume,vol,[r['fsm_m4']for r in rows])
  return np.array([mass,x,z,fsm])
 xs=np.array([sum(t['capacity_m3']*t['content_density_kg_m3']*t['table'][-1]['lcg_m']for t in groups[n])for n in names])
 zs=np.array([sum(t['capacity_m3']*t['content_density_kg_m3']*t['table'][-1]['vcg_m']for t in groups[n])for n in names])
 initial=linprog(zs/1e6,A_eq=np.vstack([capacities/1e6,xs/1e8]),b_eq=[wanted_mass/1e6,wanted_x/1e8],bounds=list(zip(lower,upper)),method='highs',options={'threads':1})
 if not initial.success:return None,dict(error=initial.message,target=target,wanted_ballast=wanted_mass)
 result=minimize(lambda f:sum(moments(f)[2:])/1e6,initial.x,method='SLSQP',bounds=list(zip(lower,upper)),constraints=[dict(type='eq',fun=lambda f:(moments(f)[:2]-[wanted_mass,wanted_x])/[1e6,1e8])],options={'ftol':1e-11,'maxiter':250})
 m=moments(result.x);res=m[:2]-[wanted_mass,wanted_x]
 if not result.success or np.max(abs(res)/[1.,100.])>.05:return None,dict(error=result.message,residual=res.tolist(),target=target,wanted_ballast=wanted_mass)
 recipe={t['id']:float(f*t['capacity_m3']*t['content_density_kg_m3'])for n,f in zip(names,result.x)for t in groups[n]}
 return recipe,dict(target_departure_mass_kg=target,target_arrival_bd=ratio,arrival_consumable_fraction=retained,design_water_density_kg_m3=geometry.density_kg_m3,target_departure_draft_m=draft,ballast_t=sum(recipe.values())/1000,estimated_ballast_z_fsm_moment_kgm=float(sum(m[2:])),moment_residual=res.tolist(),fractions=dict(zip(names,result.x.tolist())))


def main():
 parents={};real_load=fleet.load_vessel
 for ship in ('feeder_500','feeder_800','feeder_1700'):
  cache=max(Path('experiments/cache/vessels').glob(ship+'.full.hull.*.json'),key=lambda p:p.stat().st_mtime);parents[ship]=Vessel.model_validate_json(cache.read_text())
 def cached(name,*,with_hull=True):return parents[name].model_copy(deep=True)if with_hull and name in parents else real_load(name,with_hull=with_hull)
 fleet.load_vessel=cached
 for ship in (sys.argv[1:]or parents):
  started=time.monotonic();params=fleet.load_ship_params(ship)
  v=recondition_slice(slice_preset(parents[ship],'medium'),expected_load_mass_kg={'feeder_500':1914802.66,'feeder_800':3691076.12,'feeder_1700':6999133.90}[ship],seed=0)
  for ratio in (3.4,3.425,3.45):
   recipe,audit=size_witness(v,params,ratio)
   if recipe is None:print(json.dumps(dict(ship=ship,ratio=ratio,audit=audit)),flush=True);continue
   masses=departure_tank_masses(v);masses.update(recipe)
   p=build_instance().model_dump();p.update(id='medium-witness-probe',vessel=v,containers=[],load_list=[],discharge_list=[],onboard=dict(vessel_id=v.id,placements=[],tank_masses_kg=masses));p['yard']['stacks']=[];p['port']['max_draught_m']=v.hull.max_draught_m
   for call in p['rotation']['calls']:call['max_on_deck_tiers']=None
   inst=attach_default_voyage(Instance.model_validate(p));plan=MovePlan(id='empty',instance_id=inst.id,call_index=inst.call_index,moves=[])
   report=check(inst,plan);stress=design_lashing(inst,SimpleNamespace(tank_masses_kg=masses))
   coef=dict(zip(('area_0_30_mrad','area_0_40_mrad','area_30_40_mrad','gz_at_or_above_30_m','gz_max_m','area_0_flood_mrad'),(.009,.016,.006,.033,.042,.029)))
   states={}
   for name,part in report.parts.items():
    if name.startswith('condition:')and name.endswith(':stability'):
     m=part.metrics;C=m.get('containership_form_factor');phase=next((x for x in ('departure','mid_voyage','arrival')if'condition.phase: '+x in m),name)
     states[phase]=dict(gm=m.get('gm_m'),kg=m.get('kg_m'),draft=m.get('draught_mean_m'),bd=m.get('weather_beam_over_draught'),b23=min(m[k]*C/a for k,a in coef.items())if C else None)
   row=dict(ship=ship,ratio=ratio,passed=report.passed,reasons=sorted({x.rule_id for x in report.violations}),states=states,audit=audit,recipe=recipe,design_passed=all(x['passed']for x in stress.values()),design_max_ratio=max(z for x in stress.values()for k,z in x['metrics'].items()if k.startswith('max_')and k.endswith('_ratio')),wall_s=time.monotonic()-started)
   (HERE/(ship+f'-witness-{ratio}.instance.json')).write_text(inst.model_dump_json())
   print(json.dumps(row,sort_keys=True),flush=True)
   if row['passed']and row['design_passed']:break
if __name__=='__main__':main()
