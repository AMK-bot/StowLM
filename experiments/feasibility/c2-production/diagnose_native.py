from pathlib import Path
import importlib.util, random
import numpy as np
s=importlib.util.spec_from_file_location('audit',Path('experiments/feasibility/performance-followup/measure.py')); a=importlib.util.module_from_spec(s); s.loader.exec_module(a)
from stowage.io import load_instance
from stowage.feasibility._lp_highs import PersistentLP
run=PersistentLP._run
interval=PersistentLP.interval

def traced(self,*args,**kwargs):
 out=interval(self,*args,**kwargs)
 if out is None:
  ref,cells,lo,hi=args; print('FAIL',cells,'bounds',lo,hi,'R',ref.R,'fixed',ref.fixed.sum(),'nativeStatus',self.h.getModelStatus(),flush=True)
  cold=self.g._slot_interval_cold(ref,cells,lo,hi)
  print('COLD',None if cold is None else [v[0] for v in cold],flush=True)
 return out
PersistentLP.interval=traced
for rung in ('small','medium'):
 i=load_instance(a.INSTANCES/f'{rung}-current.json'); old=a.make_engine(i,True,'before'); new=a.make_engine(i,True,'after');rng=random.Random(1704)
 def baseline():
  flat=old.legal_index();T,st=old.T,old._cur;b,sl=flat//len(T.slot_ids),flat%len(T.slot_ids); stacks=T.slot_stacks[sl];js=np.maximum(stacks,0);cap=new.T.extra['stack_visibility_top']; room=np.min(np.where(stacks>=0,cap[js]-T.stack_base_z[js]-st.sh[js],np.inf),axis=1);return flat[T.height[b]<=room+1e-9]
 for step in range(4):
  print('START',rung,step,flush=True); want=baseline();got=new.copy().legal_index(); miss=np.setdiff1d(want,got);print('DIFF',rung,step,miss.tolist(),np.setdiff1d(got,want).tolist(),flush=True)
  if len(miss):break
  actions=tuple(old.decode_index(int(f)) for f in want)+old.legal_discharges()
  if not actions:break
  action=rng.choice(actions);old.push(action);new.push(action)
