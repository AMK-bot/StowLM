"""Observation-only one-filter relaxation of an already rejected monotone prefix.

Never alters the solver, its tables, bounds, oracle or checker. A recovered domain
is not a completion witness; all prefix placements remain and other constraints
still interact. At most four saved empty-prefix samples use this diagnostic.
"""
import copy,inspect,textwrap
_CACHE={}
def diagnose(future,ex,remaining):
 import stowage.solver.construct_v4 as cv
 if not _CACHE:
  source=textwrap.dedent(inspect.getsource(cv.FutureDomains.evaluate))
  changes={
   'height':('allowed = (~closed[js])[None, :] & (t.height[indices, None] <= hroom[None, :] + 1e-9)','allowed = (~closed[js])[None, :] & (t.height[indices, None] <= float("inf"))'),
   'weight_class_order':('allowed &= t.rank[indices, None] <= ranks[js][None, :] + t.e2_tol','allowed &= True'),
   'stack_mass':('allowed &= t.share[indices, None] <= (optimistic_mass_cap - masses[js])[None, :] + 1e-6','allowed &= True'),
   'twenty_over_long':('allowed &= ~((t.length[indices, None] == 20) & nlong[js][None, :])','allowed &= True'),
   'long_over_twenty_in_hold':('allowed &= ~((t.length[indices, None] != 20) & n20[js][None, :] & (~t.stack_deck[js])[None, :])','allowed &= True'),
   'heavy_over_nonheavy':('allowed &= ~( (t.mass[indices, None] >= t.e1_threshold) & nonheavy[js][None, :])','allowed &= True'),
   'occupancy':('domain &= free[None, :]','domain &= True'),
  }
  for name,(old,new) in changes.items():
   assert source.count(old)==1,(name,source.count(old))
   namespace=dict(cv.__dict__);exec(compile(source.replace(old,new),'<observation-only-prefix-'+name+'>','exec'),namespace);_CACHE[name]=namespace['evaluate']
 clone=copy.copy(future);indices,base=cv.FutureDomains.evaluate(clone,ex,remaining);counts=base.sum(axis=1);zero=[remaining[i] for i,n in enumerate(counts) if not n]
 row=dict(scope='one-filter relaxed future domains at rejected monotone prefix; no feasibility certificate',remaining=len(remaining),zero_domain_boxes=zero,base_domain_slots={cid:int(counts[i]) for i,cid in enumerate(remaining)})
 for name,evaluate in _CACHE.items():
  variant=copy.copy(future);_,mask=evaluate(variant,ex,remaining);n=mask.sum(axis=1)
  row[name]=dict(zero_domain_boxes_recovered=[cid for i,cid in enumerate(remaining) if not counts[i] and n[i]],recovered_slot_counts={cid:int(n[i]) for i,cid in enumerate(remaining) if not counts[i] and n[i]})
 return row
