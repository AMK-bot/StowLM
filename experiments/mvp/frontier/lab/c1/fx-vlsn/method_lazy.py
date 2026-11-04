"""Live warm-start cyclic search followed by exact lazy component fusion."""
from time import monotonic

from method import _starts
from search import run
from lazy_fusion import recombine


def propose(instance, *, seed, budget_s, context):
    p=context['problem']
    starts=_starts(p,context)
    if not starts:
        return []
    began=monotonic()
    candidates,stats=run(p,starts,budget_s=budget_s*.70,seed=seed)
    fused=[]
    if len(candidates)>1 and monotonic()<began+budget_s:
        fused=recombine(p,candidates[:6],deadline=began+budget_s,seed=seed)
        stats['lazy_fusion']=dict(recombine.last_stats)
    stats['actual_proposal_s']=monotonic()-began
    context['method_metadata'].update(stats)
    # At most two challengers leave room for the certified original fallback
    # under the scorecard's three finishing/checking attempts.
    return fused+candidates[:1]+[starts[0]]
