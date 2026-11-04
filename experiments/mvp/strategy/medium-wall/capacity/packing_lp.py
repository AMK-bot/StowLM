#!/usr/bin/env python3
"""Optimistic fractional box-slot packing, train-only. No physics approximation."""
from pathlib import Path
import os,sys,json,hashlib,time,itertools,collections
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS'):os.environ[k]='1'
sys.path.insert(0,str(HERE));import analyze as inv
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix, vstack
from stowage.conventions import cells_of_slot

def masks(d,boxes):
 st={s['id']:s for s in d['vessel']['stacks']};byspan=collections.defaultdict(list)
 for h in d['vessel']['hatch_covers']:
  span=tuple(sorted({st[s]['bay'] for s in h['hold_stack_ids']+h['deck_stack_ids']}));byspan[span].append(h)
 stations=sorted(byspan.values(),key=lambda hs:-sum(h['lcg_m'] for h in hs)/len(hs));pods=sorted({b['pod'] for b in boxes},key=lambda p:min(c['index'] for c in d['rotation']['calls'] if c['locode']==p and c['index']>d['call_index']));assert len(pods)==2
 caps=[sum(len(st[s]['cell_ids']) for h in hs for s in h['hold_stack_ids']+h['deck_stack_ids']) for hs in stations];demand=[sum(1 if b['length_ft']==20 else 2 for b in boxes if b['pod']==p) for p in pods]
 stop=min(range(1,len(caps)),key=lambda k:(max(0,demand[0]-sum(caps[:k]))+max(0,demand[1]-sum(caps[k:])),(sum(caps[:k])/sum(caps)-demand[0]/sum(demand))**2+(sum(caps[k:])/sum(caps)-demand[1]/sum(demand))**2,k))
 strict=[set(range(stop)),set(range(stop,len(caps)))];deficit=[max(0,dem-sum(caps[i] for i in ss)) for dem,ss in zip(demand,strict)];shared=[set(s) for s in strict]
 if any(deficit):
  opts=[]
  for a,b in [(0,1),(1,0)]:
   x=[set(s) for s in strict];boundary=min(strict[b]) if a==0 else max(strict[b]);x[a].add(boundary)
   # For 2 PODs: Hall checks each side and total are sufficient for TEU flow.
   if all(dem<=sum(caps[i] for i in ss) for dem,ss in zip(demand,x)) and sum(demand)<=sum(caps):opts.append((caps[boundary],boundary,a,x))
  if opts:shared=min(opts,key=lambda v:v[:3])[3]
 cellstation={c:i for i,hs in enumerate(stations) for h in hs for s in h['hold_stack_ids']+h['deck_stack_ids'] for c in st[s]['cell_ids']}
 return pods,stations,caps,demand,strict,shared,cellstation,deficit

def solve(d,mode,folder):
 boxes_byid={b['id']:b for b in d['containers']};boxes=[boxes_byid[c] for c in d['load_list']];cells,slots=inv.slots_for(d);cellids=sorted(cells);stacks={s['id']:s for s in d['vessel']['stacks']};stackids=sorted(stacks);pods,stations,caps,demand,strict,shared,cellstation,deficit=masks(d,boxes);allowed=strict if mode=='strict' else shared
 ci={c:i for i,c in enumerate(cellids)};si={s:i for i,s in enumerate(stackids)};nbox=len(boxes);ncell=len(cells);nstack=len(stacks)
 ur=[];uc=[];uv=[];er=[];ec=[];ev=[];columns=[];massweights=[];static=(mode!='geometry')
 for bi,box in enumerate(boxes):
  for slot in slots:
   ps=cells_of_slot(slot)
   if (box['length_ft']==20)!=(len(ps)==1) or not all(cells[c][f"accepts_{box['length_ft']}"] for c in ps) or (box['reefer'] and not any(cells[c]['reefer_plug'] for c in ps)):continue
   ss={cells[c]['stack_id'] for c in ps};on=stacks[next(iter(ss))]['on_deck']
   if static:
    if 'on_deck' in box['special_stowage'] and not on:continue
    if 'under_deck' in box['special_stowage'] and on:continue
    if box['dg'] and any(box['dg']['imdg_class'] not in stacks[s]['dg_classes_allowed'] for s in ss):continue
   if mode in ('strict','shared') and not all(cellstation[c] in allowed[pods.index(box['pod'])] for c in ps):continue
   col=len(columns);columns.append((bi,slot));er.append(bi);ec.append(col);ev.append(1.)
   for c in ps:ur.append(ci[c]);uc.append(col);uv.append(1.)
   for s in ss:ur.append(ncell+si[s]);uc.append(col);uv.append(box['gross_mass_kg']/len(ps))
 n=len(columns);tcol=n
 # The objective is the least possible maximum utilisation of optimistic stack caps.
 maxcaps=[max(stacks[s]['max_mass_kg'],stacks[s].get('max_mass_20_kg') or 0,stacks[s].get('max_mass_40_kg') or 0) for s in stackids]
 for j,cap in enumerate(maxcaps):ur.append(ncell+j);uc.append(tcol);uv.append(-cap)
 # Fractional support: occupied above <= occupied below. Height, weight order, E6, DG pair separation, securing, voyage physics omitted.
 sup=0
 bycell=collections.defaultdict(list)
 for col,(_,slot) in enumerate(columns):
  for c in cells_of_slot(slot):bycell[c].append(col)
 for stack in stacks.values():
  for lo,hi in zip(stack['cell_ids'],stack['cell_ids'][1:]):
   for col in bycell[hi]:ur.append(ncell+nstack+sup);uc.append(col);uv.append(1.)
   for col in bycell[lo]:ur.append(ncell+nstack+sup);uc.append(col);uv.append(-1.)
   sup+=1
 Aub=coo_matrix((uv,(ur,uc)),shape=(ncell+nstack+sup,n+1)).tocsr();bub=np.r_[np.ones(ncell),np.zeros(nstack+sup)];Aeq=coo_matrix((ev,(er,ec)),shape=(nbox,n+1)).tocsr();c=np.r_[np.zeros(n),1.];start=time.monotonic();res=linprog(c,A_ub=Aub,b_ub=bub,A_eq=Aeq,b_eq=np.ones(nbox),bounds=[(0,1)]*n+[(0,None)],method='highs',options={'threads':1,'time_limit':300})
 row={'mode':mode,'status':int(res.status),'message':res.message,'seconds':time.monotonic()-start,'variables':n+1,'rows':Aub.shape[0]+nbox,'strict_pod_capacity_deficit_cells':dict(zip(pods,deficit)),'stations':[{'covers':[h['id'] for h in hs],'capacity_cells':cap} for hs,cap in zip(stations,caps)],'physical_demand_cells':dict(zip(pods,demand)),'strict_station_runs':[sorted(s) for s in strict],'shared_station_runs':[sorted(s) for s in shared]}
 if res.success:
  sl=bub-Aub@res.x;row.update(minimax_stack_utilisation=float(res.fun),optimistic_mass_capacity_feasible=bool(res.fun<=1+1e-7),max_ineq_violation=float(max(0,-sl.min())),max_assignment_residual=float(np.abs(Aeq@res.x-1).max()),fractional_assignments=sum(1 for x in res.x[:-1] if 1e-7<x<1-1e-7),hatches=[])
  hstats={h['id']:{'id':h['id'],'cells':0.,'mass_kg':0.} for h in d['vessel']['hatch_covers']}
  for col,(bi,slot) in enumerate(columns):
   for cell in cells_of_slot(slot):
    h=stacks[cells[cell]['stack_id']]['hatch_cover_id'];hstats[h]['cells']+=float(res.x[col]);hstats[h]['mass_kg']+=float(res.x[col])*boxes[bi]['gross_mass_kg']/len(cells_of_slot(slot))
  row['hatches']=list(hstats.values());np.savez_compressed(folder/f'{mode}-packing-witness.npz',x=res.x,box_index=np.asarray([bi for bi,s in columns]),slot=np.asarray([s for bi,s in columns]),A_ub_data=Aub.data,A_ub_indices=Aub.indices,A_ub_indptr=Aub.indptr,A_ub_shape=Aub.shape,b_ub=bub,A_eq_data=Aeq.data,A_eq_indices=Aeq.indices,A_eq_indptr=Aeq.indptr,A_eq_shape=Aeq.shape,b_eq=np.ones(nbox))
 elif mode=='strict' and any(deficit):row['certificate']={'type':'POD-region pigeonhole','explanation':'A POD requires more physical cells than all cells in its allowed stations. This certifies this restricted mask impossible, not the original map.','deficit_cells':dict(zip(pods,deficit))}
 else:row['certificate']=None
 return row

def main():
 inv.save(HERE/'packing-owned.json',{'pid':os.getpid(),'workers':1});m=json.loads((ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text());allrows=[]
 for job in m['maps']:
  assert job['split']=='train' and '/maps/train/medium/' in job['input'];p=ROOT/job['input'];assert inv.sha(p)==job['sha256'];d=json.loads(p.read_text());folder=HERE/'maps'/job['map_id'];rows=[]
  for mode in ('geometry','mass','strict','shared'):
   out=folder/(mode+'-packing.json')
   if out.exists():row=json.loads(out.read_text())
   else:row=solve(d,mode,folder);inv.save(out,row)
   rows.append(row);print(job['map_id'],mode,row['status'],row.get('minimax_stack_utilisation'),flush=True)
  cargo=[b for b in d['containers'] if b['id'] in d['load_list']];allrows.append({'map_id':job['map_id'],'nominal_teu':sum(b['length_ft']/20 for b in cargo),'physical_cell_demand':sum(1 if b['length_ft']==20 else 2 for b in cargo),'maps':rows});inv.save(HERE/'packing-summary.json',{'script_sha256':inv.sha(__file__),'runtime_snapshot_sha256':inv.sha(inv.RUNTIME/'snapshot.json'),'maps':allrows,'completed':len(allrows)==12})
 print('DONE',flush=True)
if __name__=='__main__':main()
