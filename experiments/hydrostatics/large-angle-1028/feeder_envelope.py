"""Read-only feeder envelope sensitivities, actual liquid corrections retained.

APPROXIMATION: designer C1 conditions are synthetic, not approved booklets.
Sensitivities hold all weights, tanks, windage and opening declarations fixed.
GZ after downflooding is only the mathematical SEALED-envelope continuation;
it is explicitly excluded from the regulatory areas. No flooded-hull prediction
or checker acceptance is inferred from that post-flooding continuation.
"""
from __future__ import annotations
import argparse
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
import numpy as np
HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
os.environ['STOWAGE_STABILITY_DIRECT']='1'
os.environ.setdefault('STOWAGE_STABILITY_TIME_BUDGET_S','120')
from scipy.optimize import brentq
from stowage.model import Instance, derive_arrival_liquid_state
from stowage.model.fixtures import build_instance
from stowage.conventions import make_container_id
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.vessel.fleet import load_vessel, load_ship_params
from stowage.vessel.fleet_manual import standard_manual_conditions
from stowage.checker.stability import (check_stability,_deck_edge_angle,_refine_peak,
                                      _context,_windage,_weather_keel_area)
from stowage.checker.stability_loading import loading_condition,liquid_cg,curve_correction
from stowage.checker.stability_equilibrium import floating_equilibrium
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_curve import build_stability_curve
from stowage.checker.stability_criteria import evaluate_criteria
from extend19 import geometry_from_sections,vertical_extension
OUT=HERE/'followup'; OUT.mkdir(exist_ok=True)
VARIANTS=('baseline','sealed_cover_0p6','no_coaming_buoyancy','parent19_same_depth','forecastle_2m')

def source_hashes(name, *, extra_paths=()):
 paths=[f'params/fleet/{name}.toml','data/raw/kcs/kcs.fix',
  'src/stowage/vessel/hydrostatics.py','src/stowage/vessel/hull_offsets.py',
  'src/stowage/vessel/fleet_manual.py','src/stowage/vessel/fleet_manual_witness.py',
  'src/stowage/checker/stability.py','src/stowage/checker/stability_curve.py',
  'src/stowage/checker/stability_geometry.py','src/stowage/checker/stability_criteria.py',
  'src/stowage/checker/stability_loading.py','src/stowage/checker/stability_equilibrium.py',
  str(Path(__file__).resolve().relative_to(ROOT)),str((HERE/'extend19.py').relative_to(ROOT))]
 paths.extend(str(p) for p in extra_paths)
 return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}

def manual_instance(name,label='C1'):
 v=load_vessel(name); p=load_ship_params(name)
 recipe=next(r for r in standard_manual_conditions(v,p) if r['condition']==label)
 payload=build_instance().model_dump(mode='python'); containers=[]; placements=[]
 for serial,(cid,mass) in enumerate(recipe['cargo_cells'],1):
  identifier=make_container_id('QZXU',serial)
  containers.append(dict(id=identifier,iso_type='22G1',length_ft=20,height_m=2.591,
   tare_kg=2200.,gross_mass_kg=mass,max_gross_mass_kg=30480.,pol='THLCH',pod='THBKK'))
  placements.append(dict(slot=cid,container_id=identifier))
 payload.update(id=f'large-angle-{name}-{label}',vessel=v,containers=containers,
  load_list=[],discharge_list=[],onboard=dict(vessel_id=v.id,placements=placements,
  tank_masses_kg=dict(recipe['departure_tank_masses_kg'])))
 payload['yard']['stacks']=[]
 # Both current port crane aliases must agree. Shared historical fixture helper
 # currently changes only the nested alias; correct our local payload only.
 tiers=max(len(s.cell_ids) for s in v.stacks if s.on_deck)
 payload['port']['max_crane_height_tiers']=tiers
 payload['port']['quay_crane']['max_on_deck_tiers']=tiers
 payload['port']['quay_crane']['max_lift_height_m']=60.
 payload['port']['max_draught_m']=v.hull.max_draught_m
 for call in payload['rotation']['calls']: call['max_on_deck_tiers']=None
 return attach_default_voyage(Instance.model_validate(payload)),p,recipe

def geometry_variant(native,p,name):
 if name=='baseline': return native
 if name in ('sealed_cover_0p6','no_coaming_buoyancy'):
  q={**p,'envelope':copy.deepcopy(p['envelope'])}
  if name=='no_coaming_buoyancy': q['envelope']['hatch_coamings']=[]
  else:
   # APPROXIMATION: upper-bound probe crediting the entire declared 0.6m
   # cover cuboid as a closed buoyant body. Qualification is not established.
   for b in q['envelope']['hatch_coamings']: b['z_max_m']+=.6
  return StabilityGeometry(q)
 sections=[]
 for x,section in zip(native.x_m,native.sections):
  main=np.array(section[0],copy=True)
  if name=='parent19_same_depth':
   # APPROXIMATION: map an independently vertically-extended 19m parent to
   # the SAME target T and D. Lower hull and coaming geometry are unchanged.
   t,d=native.design_draught_m,native.depth_m
   main[:,1]=np.where(main[:,1]>t,t+(main[:,1]-t)*5.2/8.2,main[:,1])
   n=len(main)//2
   main=np.vstack((main[:n],[main[n-1,0],d],[main[n,0],d],main[n:]))
  elif name=='forecastle_2m' and x>=.9*native.lpp_m:
   # APPROXIMATION: hypothetical full-breadth closed forecastle, 2m high
   # over forward10%L; no weight/windage change. Not a known ship structure.
   # Its start lies beyond all existing coamings, so solids do not overlap.
   assert all(b['x_max_m']<.9*native.lpp_m for b in p['envelope']['hatch_coamings'])
   main=vertical_extension(main,old_top_m=native.depth_m,new_top_m=native.depth_m+2.)
  sections.append([main,*[poly.copy() for poly in section[1:]]])
 return geometry_from_sections(native,sections)

def evaluate(instance,ship,p,g,condition=None,baseline_wind=None):
 start=time.perf_counter(); rho=instance.port.water_density_kg_m3; h=instance.vessel.hull
 c=condition or loading_condition(instance,ship,geometry=g,ship_params=p,
   free_surface_method=1,precomputed_soundings=True,small_tank_exemption=True)
 if c.errors: raise ValueError(c.errors)
 floating=floating_equilibrium(g,c,rho,h.lpp_m,liquid_cg)
 curve=build_stability_curve(g,c,rho,initial_drafts=(floating.t_aft_m,floating.t_fwd_m),
   correction_solver=lambda cc,a,t:curve_correction(cc,a,t,method=1),free_surface_method=1)
 peak,peakgz=_refine_peak(curve.angles,curve.gz,lambda a:curve.at_angle(a)[0])
 upright=g.full_submerged(*curve.drafts[0],0.,rho)
 gm_solid=float(np.dot(np.asarray(upright.centroid_m)-[c.lcg_m,0.,c.kg_m],
   g.waterplane_normal(*curve.drafts[0],0.)))+upright.bm_t_m
 deck=_deck_edge_angle(g,curve.angles,curve.drafts,draft_solver=lambda a:curve.at_angle(a)[1])
 wind=baseline_wind or _windage(instance,ship,p,floating.t_aft_m,floating.t_fwd_m)
 # Keep original coaming dimensions in D-prime when adding only cover volume,
 # or removing buoyancy as a qualification sensitivity. No threshold relaxation.
 metrics,margins,broken=evaluate_criteria(curve.angles,curve.gz,gm_m=gm_solid-c.fsc_m,
   hull=h,geometry=g,displacement_kg=c.mass_kg,kg_m=c.kg_m+c.fsc_m,
   draught_m=floating.mean_draught_m,phi_f_rad=curve.flooding_angle,
   windage_area_m2=wind[0],windage_lever_m=wind[1],hull_params=p,
   negative_gz=curve.negative_gz,weather_bilge_keel_area_m2=_weather_keel_area(instance),
   draught_aft_m=floating.t_aft_m,draught_fwd_m=floating.t_fwd_m,
   rule_overrides=instance.rule_overrides,deck_edge_angle_rad=deck,
   peak_angle_rad=peak if peak is not None and peak<=curve.flooding_angle else None,
   peak_gz_m=peakgz if peak is not None and peak<=curve.flooding_angle else None)
 C=metrics['containership_form_factor']
 row=dict(mass_kg=c.mass_kg,lcg_m=c.lcg_m,tcg_m=c.tcg_m,kg_m=c.kg_m,
  fsc_m=c.fsc_m,gm_m=gm_solid-c.fsc_m,deck_edge_deg=math.degrees(deck),
  flooding_deg=math.degrees(curve.flooding_angle),
  draft_aft_m=floating.t_aft_m,draft_fwd_m=floating.t_fwd_m,
  total_area_end_deg=math.degrees(metrics['containership_total_area_end_rad']),
  metrics=metrics,required_b_areas_mrad={k:v/C for k,v in
   [('area_0_30_mrad',.009),('area_0_40_mrad',.016),('area_30_40_mrad',.006),('area_0_flood_mrad',.029)]},
  required_a_areas_mrad=dict(area_0_30_mrad=.055,area_0_40_mrad=.09,area_30_40_mrad=.03),
  b_total_area_ratio=metrics['area_0_flood_mrad']/(.029/C),
  failed_rules=[dict(rule=x[0],measured=x[1],limit=x[2],message=x[4]) for x in broken],
  sealed_curve=[],regulatory_curve=[dict(heel_deg=math.degrees(a),gz_m=float(z))
   for a,z in zip(curve.angles,curve.gz)],
  geometry_digest=g._geometry_digest.hex(),elapsed_s=0.)
 for deg in range(0,51,5):
  gz,drafts=curve.at_angle(math.radians(deg))
  row['sealed_curve'].append(dict(heel_deg=deg,gz_m=float(gz),
    aft_m=float(drafts[0]),fore_m=float(drafts[1]),
    within_downflooding=deg<=row['flooding_deg']+1e-8))
 row['elapsed_s']=time.perf_counter()-start
 return row,c,wind

def main():
 parser=argparse.ArgumentParser();parser.add_argument('ship',choices=['feeder_500','feeder_800','feeder_1700'])
 parser.add_argument('--condition',default='C1');parser.add_argument('--historical',action='store_true')
 parser.add_argument('--variants',default=','.join(VARIANTS));args=parser.parse_args()
 if args.historical and args.ship!='feeder_500':parser.error('historical fixture is feeder_500 only')
 start=time.perf_counter(); extra_paths=[];identity=None;before=source_hashes(args.ship)
 if args.historical:
  from historical_fixture import read_historical_fixture,require_fixture_hash
  instance,identity=read_historical_fixture(ROOT,Instance)
  extra_paths=[identity['source_path'],str((HERE/'historical_fixture.py').relative_to(ROOT))]
  # Historical background and tanks remain embedded and unchanged; use current
  # identical-digest hull geometry directly. No canonical acceptance is claimed.
  recipe={'condition':'historical_background',**identity,
          'source_sha256':identity['source_instance_sha256']}
 else: instance,p,recipe=manual_instance(args.ship,args.condition)
 if identity:
  before=source_hashes(args.ship,extra_paths=extra_paths)
  require_fixture_hash(before,identity)
  p=load_ship_params('feeder_500')
 native=StabilityGeometry(p)
 states=[('departure',instance.onboard)]
 if args.historical:
  liquid=derive_arrival_liquid_state(instance.onboard,instance.voyage,vessel=instance.vessel)
  # The voyage API returns tank quantities, not a whole ShipState. Retain the
  # unchanged placements/covers and substitute only its arrival tank masses.
  arrival=instance.onboard.model_copy(update={'tank_masses_kg':dict(liquid.tank_masses_kg)})
  states.append(('arrival',arrival))
 result=dict(date_utc=datetime.now(timezone.utc).isoformat(),ship=args.ship,
  representative_condition=recipe,source_hashes_before=before,rows=[],
  approximation='All probes hold mass/cargo/tanks/openings/windage fixed; post-flood GZ is only sealed-envelope mathematical continuation. Not a full-checker acceptance or certified booklet.')
 if identity:result.update(identity)
 for phase,ship in states:
  base,c,wind=evaluate(instance,ship,p,native)
  base.update(phase=phase,variant='baseline'); result['rows'].append(base)
  print(phase,'baseline',base['deck_edge_deg'],base['flooding_deg'],base['b_total_area_ratio'],flush=True)
  for variant in args.variants.split(','):
   if variant=='baseline':continue
   g=geometry_variant(native,p,variant)
   row,_,_=evaluate(instance,ship,p,g,c,wind)
   row.update(phase=phase,variant=variant,
     delta_total_area_mrad=row['metrics']['area_0_flood_mrad']-base['metrics']['area_0_flood_mrad'],
     delta_gz_30_m=row['sealed_curve'][6]['gz_m']-base['sealed_curve'][6]['gz_m'],
     delta_gz_40_m=row['sealed_curve'][8]['gz_m']-base['sealed_curve'][8]['gz_m'])
   result['rows'].append(row)
   print(phase,variant,row['b_total_area_ratio'],row['delta_total_area_mrad'],flush=True)
 suffix='historical' if args.historical else args.condition
 after=source_hashes(args.ship,extra_paths=extra_paths)
 if identity:require_fixture_hash(after,identity)
 result.update(source_hashes_after=after,
   source_stable=before==after,elapsed_s=time.perf_counter()-start,
   peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
 target=OUT/f'{args.ship}-{suffix}.json'
 target.write_text(json.dumps(result,indent=2,default=float)+'\n')
 if not result['source_stable']: raise RuntimeError('Source changed during probe; results flagged stale')
 print('saved',target,'elapsed',result['elapsed_s'],'rss',result['peak_rss_bytes'],flush=True)
if __name__=='__main__':main()
