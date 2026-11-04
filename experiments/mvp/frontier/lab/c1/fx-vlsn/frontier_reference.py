"""Unmodified ALNS from the same ordered warm start, for budget comparison."""
from alns import search
from stowage.actions.space import Place


def propose(instance, *, seed, budget_s, context):
    rows = context.get('baseline', {}).get('ordered_actions')
    if not rows:
        return []
    actions = tuple(Place(a['box'], a['slot']) for a in rows)
    plans, stats = search(instance, actions, time_limit_s=budget_s, seed=seed,
                          problem=context['problem'], keep=6)
    context['method_metadata'].update(iterations=stats.iterations, accepted=stats.accepted,
                                      improved=stats.improved, infeasible=stats.infeasible,
                                      trace=stats.best_trace, stored_order_preserved=True,
                                      inherited_ballast=True)
    return [s.actions for s in plans]
