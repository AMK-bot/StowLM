"""Map-wide hatch bound and explicitly conditional relocation certificates."""
from dataclasses import asdict

from .hatch import BoundResult, lower_bound
from .relocation import solve_relaxation


def map_bound(instance, *, budget_s=10., seed=0, problem=None, restows=True):
    """Only this map-wide weighted bound may prove unrestricted optimality.

    Fixed-stow relocation bounds require a monotone Place-only loading family;
    they are diagnostics and must never be added to the map-wide certificate.
    """
    tables = getattr(getattr(problem, 'engine', None), 'T', None)
    old = lower_bound(instance, budget_s=budget_s * (.25 if restows else 1.),
                      seed=seed, tables=tables)
    result = old
    tightened = None
    if restows:
        from .hatch_restow import lower_bound as restow_lower_bound
        tightened = restow_lower_bound(instance, budget_s=budget_s * .75,
                                       seed=seed, tables=tables)
        if tightened.lower_bound >= old.lower_bound:
            result = tightened
    record = asdict(result)
    record['scope'] = 'map-wide weighted lifts'
    record['restow_closure'] = restows
    record['certificate_source'] = 'hatch_restow' if result is tightened else 'hatch_only'
    record['hatch_only_lower_bound'] = old.lower_bound
    record['hatch_only_status'] = old.status
    record['hatch_only_s'] = old.wall_time_s
    if tightened is not None:
        record['hatch_restow_lower_bound'] = tightened.lower_bound
        record['hatch_restow_status'] = tightened.status
        record['hatch_restow_s'] = tightened.wall_time_s
        record['hatch_restow_unary_dg_widened'] = getattr(tightened, 'unary_dg_widened', False)
    record['mandatory_relocation_lower_bound'] = result.relocations
    record['wall_time_s'] = old.wall_time_s + (tightened.wall_time_s if tightened else 0.)
    return result.lower_bound, record


def relocation_bound(problem, scored, *, budget_s=2., seed=0):
    """Diagnostic fixed-stow relocation bound, never a map-wide D11 bound."""
    assignment = {action.box: action.slot for action in scored.actions}
    result = solve_relaxation(problem, assignment, time_limit_s=budget_s, seed=seed)
    record = asdict(result)
    for key in ('relocated', 'mandatory_nonloads'):
        record[key] = sorted(record[key])
    record['scope'] = 'fixed stow; monotone Place-only plans without ship restows'
    return record
