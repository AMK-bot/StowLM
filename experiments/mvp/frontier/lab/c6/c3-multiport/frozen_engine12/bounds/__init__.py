"""Map-wide hatch bound and explicitly conditional relocation certificates."""
from dataclasses import asdict

from .hatch import BoundResult, lower_bound
from .relocation import solve_relaxation


def _legacy_map_bound(instance, *, budget_s=10., seed=0, problem=None, restows=True):
    """This map-wide bound applies to checker-passing plans under D11/D15.

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
    record['certificate_source'] = ('counting_only_fallback' if result.status == 'counting_only'
                                    else 'hatch_restow' if result is tightened else 'hatch_only')
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



def map_bound(instance, *, budget_s=10., seed=0, problem=None, restows=True,
              certified=False, upper_bound=None):
    """Strongest guarded certificate; optional exact stage adds no rejection gate.

    The old route remains available if exact model construction, an oracle or
    numerical proof is unsupported. A witness is only a proposed construction.
    """
    from .scope import proof_scope, proof_details
    value, record = _legacy_map_bound(instance,
        budget_s=budget_s*(.25 if certified else 1.), seed=seed,
        problem=problem, restows=restows)
    record['proof_scope'] = proof_scope(record['certificate_source'])
    record['proof_details'] = proof_details(record['certificate_source'])
    if not certified:
        return value, record
    tables = getattr(getattr(problem, 'engine', None), 'T', None)
    try:
        from .certified import lower_bound as exact_lower_bound
        result = exact_lower_bound(instance, budget_s=budget_s*.75, seed=seed,
                                   tables=tables, upper_bound=upper_bound)
    except Exception as error:
        record['certified_error'] = f'{type(error).__name__}: {error}'
        return value, record
    details = asdict(result)
    old_seconds = record['wall_time_s']
    summary = dict(lower_bound=result.lower_bound, status=result.status,
                   wall_time_s=result.wall_time_s,
                   lazy_iterations=result.lazy_iterations,
                   lazy_cut_counts=result.lazy_cut_counts,
                   proof_scope=result.proof_scope,
                   proof_details=result.proof_details,
                   origin=('counting_only_fallback' if result.status == 'counting_only'
                           else 'certified_final_stow_witness'),
                   relaxation_assignment=result.relaxation_assignment,
                   first_aboard_order=result.first_aboard_order,
                   current_order_flags=result.current_order_flags,
                   static_legal_witness=result.static_legal_witness)
    if (result.lower_bound > value
            or (result.lower_bound == value and result.status != 'counting_only')):
        previous = record
        record = details
        record['certificate_source'] = ('counting_only_fallback' if result.status == 'counting_only'
                                        else 'certified')
        record['scope'] = 'map-wide weighted lifts'
        record['restow_closure'] = restows
        record['previous_certificate'] = previous
        record['mandatory_relocation_lower_bound'] = result.relocations
        value = result.lower_bound
    record['certified'] = summary
    record['wall_time_s'] = old_seconds+result.wall_time_s
    return value, record

def relocation_bound(problem, scored, *, budget_s=2., seed=0):
    """Diagnostic fixed-stow relocation bound, never a map-wide D11 bound."""
    assignment = {action.box: action.slot for action in scored.actions}
    result = solve_relaxation(problem, assignment, time_limit_s=budget_s, seed=seed)
    record = asdict(result)
    for key in ('relocated', 'mandatory_nonloads'):
        record[key] = sorted(record[key])
    record['scope'] = 'fixed stow; monotone Place-only plans without ship restows'
    return record
