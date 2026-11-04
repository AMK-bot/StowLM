#!/usr/bin/env python3
"""Static CP candidate; no acceptance claim until native replay and full check."""
from pathlib import Path
import sys,os,json,collections,math,time
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4];sys.path.insert(0,str(HERE));import analyze as inv
from ortools.sat.python import cp_model
from stowage.conventions import cells_of_slot

def construct(job,seconds=600):
 p=ROOT/job['input'];assert inv.sha(p)==job['sha256'];d=json.loads(p.read_text());boxes0={b['id']:b for b in d['containers']};boxes=[boxes0[c] for c in d['load_list']];cells,slots=inv.slots_for(d);stacks={s['id']:s for s in d['vessel']['stacks']};model=cp_model.CpModel();bycell=collections.defaultdict(list);bystack=collections.defaultdict(list);choices={};rank={'L':1,'M':2,'H':3};mass={};height={};t=time.monotonic()
 for box in boxes:
  vs=[]
  for slot in slots:
   ps=cells_of_slot(slot);ss={cells[c]['stack_id'] for c in ps};on=stacks[next(iter(ss))]['on_deck']
   if (box['length_ft']==20)!=(len(ps)==1) or not all(cells[c][f"accepts_{box['length_ft']}"] for c in ps) or (box['reefer'] and not any(cells[c]['reefer_plug'] for c in ps)):continue
   if 'on_deck' in box['special_stowage'] and not on:continue
   if 'under_deck' in box['special_stowage'] and on:continue
   if box['dg'] and (any(box['dg']['imdg_class'] not in stacks[s]['dg_classes_allowed'] for s in ss) or (box['dg']['stowage_category']=='E' and not on)):continue
   v=model.new_bool_var(f"{box['id']}_{slot}");choices[box['id'],slot]=v;vs.append(v)
   for c in ps:bycell[c].append((v,box))
   for s in ss:bystack[s].append((v,box,len(ps)))
  model.add(sum(vs)==1)
 occ={c:model.new_bool_var('o_'+c) for c in cells};rk={c:model.new_int_var(0,3,'rank_'+c) for c in cells}
 for c in cells:
  model.add(occ[c]==sum(v for v,b in bycell[c]));model.add(rk[c]==sum(rank[b['weight_class']]*v for v,b in bycell[c]))
 for sid,s in stacks.items():
  # Occupied ranks >= upper ranks ensures both support and legacy E2 class order.
  for lo,hi in zip(s['cell_ids'],s['cell_ids'][1:]):model.add(rk[lo]>=rk[hi])
  if not s['on_deck']:
   for c in s['cell_ids']:model.add(occ[c]==1)
  short=model.new_bool_var('short_'+sid);long=model.new_bool_var('long_'+sid);mixed=model.new_bool_var('mixed_'+sid)
  v20=[v for v,b,n in bystack[sid] if n==1];v40=[v for v,b,n in bystack[sid] if n==2]
  if v20:model.add_max_equality(short,v20)
  else:model.add(short==0)
  if v40:model.add_max_equality(long,v40)
  else:model.add(long==0)
  model.add(mixed<=short);model.add(mixed<=long);model.add(mixed>=short+long-1)
  expr=sum(math.ceil(b['gross_mass_kg']*1000/n)*v for v,b,n in bystack[sid]);base=s['max_mass_kg'];cap20=s.get('max_mass_20_kg') or base;cap40=s.get('max_mass_40_kg') or base
  model.add(expr<=math.floor(cap20*1000)).only_enforce_if([short,long.Not()]);model.add(expr<=math.floor(cap40*1000)).only_enforce_if([long,short.Not()]);model.add(expr<=math.floor(base*1000)).only_enforce_if(mixed)
  model.add(sum(math.ceil((b['height_m']+(b['oog']['over_height_m'] if b['oog'] else 0))*1000)*v for v,b,n in bystack[sid])<=math.floor(s['max_height_m']*1000))
 # Keep low cargo KG; later agents can retry physics-aware alternatives.
 model.minimize(sum(round((cells[cells_of_slot(slot)[0]]['vcg_base_m']+box['height_m']/2)*1000*box['gross_mass_kg']/1000)*v for (cid,slot),v in choices.items() for box in [boxes0[cid]]))
 solver=cp_model.CpSolver();solver.parameters.num_workers=1;solver.parameters.max_time_in_seconds=seconds;solver.parameters.random_seed=0;status=solver.solve(model);out={'map_id':job['map_id'],'input':job['input'],'input_sha256':job['sha256'],'status':solver.status_name(status),'elapsed_s':time.monotonic()-t,'solve_s':solver.wall_time,'branches':solver.num_branches,'conflicts':solver.num_conflicts,'objective':solver.objective_value,'best_bound':solver.best_objective_bound,'constraints':'length/reefer/cell occupancy/support/legacy class E2 order/composition-specific stack masses/stack heights/static DG allowed classes/on-under-deck; full holds sufficient for E6','omissions':'DG pair segregation, OOG footprint, away-from-heat, visibility, dynamic securing, physical stability and voyage; native replay/checker required','runtime_snapshot_sha256':inv.sha(inv.RUNTIME/'snapshot.json'),'script_sha256':inv.sha(__file__)}
 if status in (cp_model.FEASIBLE,cp_model.OPTIMAL):
  out['witness']={cid:slot for (cid,slot),v in choices.items() if solver.value(v)};assert len(out['witness'])==len(boxes)
  occupied={};loads=collections.defaultdict(list)
  for cid,slot in out['witness'].items():
   b=boxes0[cid];ps=cells_of_slot(slot)
   assert (b['length_ft']==20)==(len(ps)==1)
   assert all(cells[c][f"accepts_{b['length_ft']}"] for c in ps)
   assert not b['reefer'] or any(cells[c]['reefer_plug'] for c in ps)
   for c in ps:assert c not in occupied;occupied[c]=b;loads[cells[c]['stack_id']].append((b,len(ps)))
  audit=[]
  for sid,s in stacks.items():
   last=4
   for c in s['cell_ids']:
    if c in occupied:r=rank[occupied[c]['weight_class']];assert r<=last;last=r
    else:last=0
    if not s['on_deck']:assert c in occupied
   ns={n for b,n in loads[sid]};cap=(s.get('max_mass_20_kg') or s['max_mass_kg']) if ns=={1} else ((s.get('max_mass_40_kg') or s['max_mass_kg']) if ns=={2} else s['max_mass_kg'])
   used=sum(b['gross_mass_kg']/n for b,n in loads[sid]);high=sum(b['height_m']+(b['oog']['over_height_m'] if b['oog'] else 0) for b,n in loads[sid]);assert used<=cap+1e-6;assert high<=s['max_height_m']+1e-6
   audit.append({'stack':sid,'mass_kg':used,'composition_cap_kg':cap,'height_m':high,'height_cap_m':s['max_height_m']})
  out['independent_witness_audit']='pass';out['stack_audit']=audit
 return out

def main():
 inv.save(HERE/'cp-owned.json',{'pid':os.getpid(),'workers':1});manifest=json.loads((ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text())
 while not json.loads((HERE/'packing-summary.json').read_text()).get('completed'):time.sleep(10)
 for seed in (185100000,185100010):
  job=next(j for j in manifest['maps'] if j['seed']==seed);assert job['split']=='train';p=HERE/'maps'/job['map_id']/'construct-cp.json'
  if p.exists():continue
  out=construct(job);inv.save(p,out);print(job['map_id'],out['status'],out['elapsed_s'],flush=True)
 print('DONE',flush=True)
if __name__=='__main__':main()
