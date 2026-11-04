"""Strongest old aggregate, physical-stack and E6/Hall certificates."""
from time import monotonic

from stowage.bounds import lower_bound as counting_bound
from stowage.feasibility.tables import build_tables
from stowage.frozen import active_stubs
from stowage.rules.catalogue import rule_freeze_v1_enabled

from .scope import proof_scope, proof_details, validate_objective
from .unary import optimistic_dg_tables


def lower_bound(instance, *, budget_s=30., seed=0, tables=None, reuse_e6=True):
    """Return a map-wide lower bound, never a constructor capacity screen.

    Every new component receives an optimistic DG table before calculating
    eligibility-dependent necessary demands. Unknown Hall enumeration retains
    the physical certificate; a failed optional solve retains the old bound.
    """
    import math
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError('budget_s must be finite and positive')
    validate_objective(instance)
    if active_stubs():
        raise ValueError('capacity certificate does not support frozen-interface overrides')
    started = monotonic()
    if (instance.onboard.placements or instance.discharge_list or instance.plan_calls
            or instance.forecasts or instance.onboard.covers_off):
        base = counting_bound(instance)
        return dict(instance_id=instance.id, lower_bound=base.weighted,
                    certificate_source='counting_only', status='counting_only',
                    proof_scope=proof_scope('counting_only_fallback'),
                    proof_details=proof_details('counting_only_fallback'),
                    wall_s=monotonic()-started,
                    note='Physical/Hall certificates require empty closed arrival without later loads/forecasts')
    T = tables if tables is not None else build_tables(instance)
    widened = not rule_freeze_v1_enabled() and bool(T.is_dg.any())
    if widened:
        T = optimistic_dg_tables(T)
    from ..scout.layout_bound import lower_bound as old_bound
    from .physical_capacity import lower_bound as physical_bound, E6WitnessCache
    from .port_capacity import lower_bound as hall_bound
    e6_cache = E6WitnessCache() if reuse_e6 else None
    components, errors = {}, {}
    for name, solve, share in [('aggregate', old_bound, .20),
                                ('physical_stack_e6', physical_bound, .70)]:
        try:
            options = dict(e6_cache=e6_cache) if name == 'physical_stack_e6' else {}
            components[name] = solve(instance, budget_s=max(.001, budget_s*share),
                                      seed=seed, tables=T, **options)
        except Exception as error:
            errors[name] = f'{type(error).__name__}: {error}'
    if 'physical_stack_e6' in components:
        try:
            components['two_port_hall'] = hall_bound(instance,
                budget_s=max(.001, budget_s*.10), tables=T,
                base_result=components['physical_stack_e6'], e6_cache=e6_cache)
        except Exception as error:
            errors['two_port_hall'] = f'{type(error).__name__}: {error}'
    if components:
        name, best = max(components.items(), key=lambda item: item[1]['lower_bound'])
        result = dict(best)
    else:
        name = 'counting_only'
        base = counting_bound(instance)
        result = dict(instance_id=instance.id, lower_bound=base.weighted, status='counting_only')
    result.update(certificate_source=name, components=components, errors=errors,
                  legacy_dg_unary_widened=widened,
                  e6_witness_cache=dict(enabled=reuse_e6,
                      hits=e6_cache.hits if e6_cache else 0,
                      misses=e6_cache.misses if e6_cache else 0),
                  proof_scope=proof_scope(name), proof_details=proof_details(name),
                  wall_s=monotonic()-started)
    return result
