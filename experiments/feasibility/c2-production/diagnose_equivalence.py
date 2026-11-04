from pathlib import Path
import importlib.util, random, json
import numpy as np
s=importlib.util.spec_from_file_location('audit',Path('experiments/feasibility/performance-followup/measure.py')); a=importlib.util.module_from_spec(s); s.loader.exec_module(a)
from stowage.io import load_instance
for rung in ('small','medium'):
 i=load_instance(a.INSTANCES/f'{rung}-current.json'); old=a.make_engine(i,True,'before'); new=a.make_engine(i,True,'after'); rng=random.Random(1704)
 def baseline():
  flat=old.legal_index(); T,st=old.T,old._cur; b,sl=flat//len(T.slot_ids),flat%len(T.slot_ids); stacks=T.slot_stacks[sl];js=np.maximum(stacks,0); cap=new.T.extra['stack_visibility_top']; room=np.min(np.where(stacks>=0,cap[js]-T.stack_base_z[js]-st.sh[js],np.inf),axis=1);return flat[T.height[b]<=room+1e-9]
 for step in range(5):
  want=baseline(); got=new.copy().legal_index(); miss=np.setdiff1d(want,got);extra=np.setdiff1d(got,want)
  print(rung,step,len(want),len(got),'missing',miss.tolist(),'extra',extra.tolist(),flush=True)
  if len(miss):
   for f in miss:
    b,sl=divmod(f,len(new.T.slot_ids));cells=[int(c) for c in new.T.slot_cells[sl] if c>=0];g=new._global; ref=new._cur.ref; m=new.T.mass[b]
    print('action',new.decode_index(int(f)),'mass',m,'nativecached',new._cur.c2_sols.get(sl),flush=True)
    cold=g._slot_interval_cold(ref,cells,0.,min(ref.R,g.u0*len(cells))); native=g.slot_interval(ref,cells,0.,min(ref.R,g.u0*len(cells)))
    print('native',None if native is None else [x[0] for x in native],'cold',None if cold is None else [x[0] for x in cold],flush=True)
    print('singleoldC2',old._c2(old._cur,np.array([b]),np.array([sl])),'singlenewC2',new._c2(new._cur,np.array([b]),np.array([sl])),flush=True)
   break
  actions=tuple(old.decode_index(int(f)) for f in want)+old.legal_discharges()
  if not actions: break
  act=rng.choice(actions);print('push',act,flush=True);old.push(act);new.push(act)
