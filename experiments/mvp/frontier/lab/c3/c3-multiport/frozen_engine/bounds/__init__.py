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
    bound = lower_bound
    if restows:
        from .hatch_restow import lower_bound as bound
    result = bound(instance, budget_s=budget_s, seed=seed, tables=tables)
    record = asdict(result)
    record['scope'] = 'map-wide weighted lifts'
    record['restow_closure'] = restows
    record['mandatory_relocation_lower_bound'] = result.relocations
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
