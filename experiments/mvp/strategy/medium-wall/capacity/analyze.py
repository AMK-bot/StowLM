#!/usr/bin/env python3
"""TRAIN-only medium capacity audit. Read-only pinned source; one numerical worker."""
from pathlib import Path
import os,sys,json,hashlib,time,collections,datetime
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
RUNTIME=HERE.parent/'search/runtime'
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS'):os.environ[k]='1'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.path[:0]=[str(RUNTIME/'src')]
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix
from stowage.io import load_instance
from stowage.conventions import cells_of_slot,format_slot,parse_slot
from stowage.feasibility import make_feasibility_engine,FeasibilityConfig
from stowage.actions import get_action_space
from stowage.feasibility.global_bounds import GlobalScreen
from stowage.rules.catalogue import rule_freeze_v1

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n');t.replace(p)
def slots_for(d):
 cells={c['id']:c for c in d['vessel']['cells']};slots=set(cells)
 for c in cells:
  a=parse_slot(c)
  for b in (a.bay-1,a.bay+1):
   if b>=2:
    s=format_slot(b,a.row,a.tier)
    if all(p in cells for p in cells_of_slot(s)):slots.add(s)
 return cells,sorted(slots)
def geometry(d,restricted=False,seconds=20):
 from ortools.sat.python import cp_model
 cells,slots=slots_for(d);boxes={b['id']:b for b in d['containers']};groups=collections.defaultdict(list)
 for cid in d['load_list']:groups[boxes[cid]['length_ft'],boxes[cid]['reefer']].append(cid)
 model=cp_model.CpModel();bycell=collections.defaultdict(list);vs={}
 for (length,reefer),ids in groups.items():
  group=[]
  for slot in slots:
   parts=cells_of_slot(slot)
   if ((length==20)!=(len(parts)==1) or not all(cells[c][f'accepts_{length}'] for c in parts) or (reefer and not any(cells[c]['reefer_plug'] for c in parts))):continue
   v=model.new_bool_var(f'{length}_{reefer}_{slot}');group.append(v);vs[length,reefer,slot]=v
   for c in parts:bycell[c].append(v)
  model.add(sum(group)==len(ids))
 occ={c:model.new_bool_var('o_'+c) for c in cells}
 for c in cells:model.add(occ[c]==sum(bycell[c]))
 for stack in d['vessel']['stacks']:
  for lo,hi in zip(stack['cell_ids'],stack['cell_ids'][1:]):model.add(occ[hi]<=occ[lo])
 solver=cp_model.CpSolver();solver.parameters.num_workers=1;solver.parameters.max_time_in_seconds=seconds;solver.parameters.random_seed=0
 status=solver.solve(model);out={'status':solver.status_name(status),'seconds':solver.wall_time,'constraints':'exact length/reefer/cell occupancy/bottom-up support only; no DG/mass/weight-order/physics'}
 if status in (cp_model.FEASIBLE,cp_model.OPTIMAL):
  chosen=collections.defaultdict(list)
  for (length,reefer,slot),var in vs.items():
   if solver.value(var):chosen[length,reefer].append(slot)
  witness={cid:slot for k,ids in groups.items() for cid,slot in zip(ids,sorted(chosen[k]))};out['witness']=witness
  used=[]
  for cid,slot in witness.items():
   ps=cells_of_slot(slot);used.extend(ps);box=boxes[cid];assert (box['length_ft']==20)==(len(ps)==1);assert all(cells[c][f"accepts_{box['length_ft']}"] for c in ps);assert not box['reefer'] or any(cells[c]['reefer_plug'] for c in ps)
  assert len(used)==len(set(used));occupied=set(used)
  for stack in d['vessel']['stacks']:
   seen_empty=False
   for cell in stack['cell_ids']:
    if cell not in occupied:seen_empty=True
    else:assert not seen_empty
  out['independent_witness_audit']='pass'
 return out

def physical_relax(instance,outdir):
 t=time.monotonic();engine=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(lookahead=0,extra={'global_screen':0}));s=GlobalScreen.from_instance(instance,engine.T)
 fixed,free,R=engine._global_state(engine._cur.ex);assert not np.any(fixed)
 A=np.hstack([s.A,s.At]);b_hi=s.hi-s.const;b_lo=s.const-s.lo
 Au=[];bu=[];labels=[];groups=[]
 for i,n in enumerate(s.names):
  g='stack' if n.startswith('stack_mass@') else ('strength' if any(v in n.lower() for v in ('shear','bending','sf@','bm@')) else 'physics')
  if np.isfinite(b_hi[i]):Au.append(A[i]);bu.append(b_hi[i]);labels.append(n+':upper');groups.append(g)
  if np.isfinite(b_lo[i]):Au.append(-A[i]);bu.append(b_lo[i]);labels.append(n+':lower');groups.append(g)
 Au=np.asarray(Au);bu=np.asarray(bu);eq=np.r_[np.ones(len(free)),np.zeros(len(s.tank_lo))][None,:];bounds=[(0,s.u0 if f else 0) for f in free]+list(zip(s.tank_lo,s.tank_hi))
 result=[]
 for stage,allow in [('cell-mass',[]),('stack-mass',['stack']),('physics-and-stack',['stack','physics']),('strength-plus-physics',['stack','physics','strength'])]:
  keep=np.array([g in allow for g in groups]);res=linprog(np.zeros(A.shape[1]),A_ub=csr_matrix(Au[keep]) if keep.any() else None,b_ub=bu[keep] if keep.any() else None,A_eq=csr_matrix(eq),b_eq=[R],bounds=bounds,method='highs',options={'threads':1})
  row={'stage':stage,'status':res.status,'message':res.message,'rows':int(keep.sum())}
  if res.success:
   sl=bu-Au@res.x;row.update(max_primal_violation=max(0.,float(-sl[keep].min())) if keep.any() else 0.,mass_residual_kg=float((eq@res.x)[0]-R),tightest_rows=[{'name':labels[i],'slack':float(sl[i]),'relative_slack':float(sl[i]/max(1.,abs(bu[i])))} for i in sorted(np.flatnonzero(keep),key=lambda i:sl[i]/max(1.,abs(bu[i])))[:15]])
   if stage=='strength-plus-physics':save(outdir/'fractional-witness.json',{'cell_ids':list(engine.T.cell_ids),'y_kg':res.x[:len(free)].tolist(),'adjustable_tank_masses_kg':res.x[len(free):].tolist(),'row_names':labels,'row_slacks':sl.tolist(),'remaining_mass_kg':R,'u0_kg':s.u0,'names':list(s.names)})
  else:row['certificate']='none: solver status only; do not call map infeasible'
  result.append(row)
 np.savez_compressed(outdir/'relaxation.npz',A_ub=Au,b_ub=bu,A_eq=eq,b_eq=[R],lower=np.asarray([b[0] for b in bounds]),upper=np.asarray([b[1] for b in bounds]),labels=np.asarray(labels),groups=np.asarray(groups))
 return {'elapsed_s':time.monotonic()-t,'stages':result,'definition':'Outward necessary C2 affine relaxation; divisible mass and largest stack composition limits, fixed departure context. Passing does not establish exact physics or voyage feasibility.','mass_kg':R,'n_cells':len(free),'u0_kg':s.u0}

def main():
 manifest_path=ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json';manifest=json.loads(manifest_path.read_text());assert manifest['split']=='train';rows=[]
 binding={'manifest_sha256':sha(manifest_path),'runtime_snapshot_sha256':sha(RUNTIME/'snapshot.json'),'runtime_identity':json.loads((RUNTIME/'snapshot.json').read_text()).get('identity'),'script_sha256':sha(__file__),'workers':1,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};save(HERE/'binding.json',binding);save(HERE/'owned.json',{'pid':os.getpid(),'argv':sys.argv,'workers':1})
 for job in manifest['maps']:
  assert job['split']=='train' and job['size']=='medium' and '/maps/train/medium/' in job['input'];p=ROOT/job['input'];assert sha(p)==job['sha256'];assert sha(ROOT/job['audit'])==job['audit_sha256'];folder=HERE/'maps'/job['map_id'];folder.mkdir(parents=True,exist_ok=True)
  cached=folder/'summary.json'
  if cached.exists():rows.append(json.loads(cached.read_text()));print(job['map_id'],'cached',flush=True);continue
  d=json.loads(p.read_text());a=json.loads((ROOT/job['audit']).read_text());ids=set(d['load_list']);cargo=[b for b in d['containers'] if b['id'] in ids];assert not d['onboard']['placements'];stacks={s['id']:s for s in d['vessel']['stacks']};cells=d['vessel']['cells'];mass=sum(b['gross_mass_kg'] for b in cargo);teu=sum(1 if b['length_ft']==20 else 2 for b in cargo)
  caps={sid:max(s['max_mass_kg'],s.get('max_mass_20_kg') or 0,s.get('max_mass_40_kg') or 0) for sid,s in stacks.items()};hatches=[]
  for h in d['vessel']['hatch_covers']:
   ss=set(h['hold_stack_ids']+h['deck_stack_ids']);hatches.append({'id':h['id'],'lcg_m':h['lcg_m'],'tcg_m':h['tcg_m'],'cells':sum(c['stack_id'] in ss for c in cells),'hold_cells':sum(c['stack_id'] in h['hold_stack_ids'] for c in cells),'optimistic_mass_cap_kg':sum(caps[s] for s in ss),'deck_optimistic_mass_cap_kg':sum(caps[s] for s in h['deck_stack_ids']),'deck_cells':sum(c['stack_id'] in h['deck_stack_ids'] for c in cells)})
  row={k:job[k] for k in ('map_id','input','sha256','ship','family','seed')};row.update(loads=len(cargo),mass_kg=mass,teu=teu,capacity_teu=len(cells),teu_fraction=teu/len(cells),generator_mass_fraction=a['booking_utilisation']['cargo_mass_fraction'],generator_mass_budget_kg=mass/a['booking_utilisation']['cargo_mass_fraction'],optimistic_stack_capacity_kg=sum(caps.values()),mass_fraction_of_optimistic_stack_capacity=mass/sum(caps.values()),classes=dict(collections.Counter(b['weight_class'] for b in cargo)),lengths=dict(collections.Counter(b['length_ft'] for b in cargo)),pod=[{'pod':pod,'boxes':len(bs),'teu':sum(1 if b['length_ft']==20 else 2 for b in bs),'mass_kg':sum(b['gross_mass_kg'] for b in bs),'classes':dict(collections.Counter(b['weight_class'] for b in bs))} for pod,bs in sorted(((pod,[b for b in cargo if b['pod']==pod]) for pod in {b['pod'] for b in cargo}))],reefer=sum(b['reefer'] for b in cargo),dg=sum(b['dg'] is not None for b in cargo),hatches=hatches,initial_ballast_kg=sum(d['onboard']['tank_masses_kg'].values()),background_acceptance=a['background_last']['ballast_context'],generator_conditioning=a['conditioning'])
  row['geometry']=geometry(d)
  save(folder/'geometry.json',row['geometry']);row['geometry']={k:v for k,v in row['geometry'].items() if k!='witness'}
  try:
   instance=load_instance(p)
   with rule_freeze_v1(enabled=False):row['physical_relaxation']=physical_relax(instance,folder)
  except Exception as e:row['physical_relaxation']={'status':'error','error':repr(e)}
  save(cached,row);rows.append(row);save(HERE/'summary.json',{'binding':binding,'maps':rows});print(job['map_id'],row['teu_fraction'],row['mass_fraction_of_optimistic_stack_capacity'],row['geometry']['status'],row['physical_relaxation'].get('stages',row['physical_relaxation']),flush=True)
 save(HERE/'summary.json',{'binding':binding,'maps':rows,'completed':True});print('DONE',flush=True)
if __name__=='__main__':main()
