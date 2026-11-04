# Copied from experiments/mvp/frontier/lab/c3/c2-hatch/certified.py; engine integration changes are local.
"""Final-stow lower bounds for checker-passing plans under the D11/D15 scope."""
import math
from collections import Counter
from dataclasses import dataclass
from .bound_joint import BoundResult
from .lazy_bound import lower_bound as _lazy_bound


@dataclass
class CertifiedResult(BoundResult):
    lazy_iterations: int = 0
    lazy_cut_counts: dict | None = None
    static_legal_witness: bool = False
    securing_certificates: list | None = None
    proof_scope: str | None = None
    proof_details: dict | None = None



def _domain_guards(instance):
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        raise ValueError('exact final-state cuts are reviewed only with rule-freeze-v1 disabled')
    vessel = instance.vessel
    if ({c.id for c in vessel.cells} & {c.id for c in vessel.fixed_load_cells}
            or {s.id for s in vessel.stacks} & {s.id for s in vessel.fixed_load_stacks}):
        raise ValueError('decision and immutable fixed catalogs must be disjoint')
    # The three-micrometre geometric relaxation was reviewed for ship-sized
    # coordinates. Decline exotic scales rather than silently restrict CP's
    # floor-variable domain or extrapolate an absolute floating error guard.
    values = [value for cell in vessel.cells for value in (cell.lcg_m, cell.tcg_m)]
    values += [s.base_z_m for s in vessel.stacks]
    for box in instance.containers:
        values.append(box.height_m)
        if box.oog:
            values.extend((box.oog.over_height_m, box.oog.over_width_port_m,
                           box.oog.over_width_starboard_m, box.oog.over_length_fore_m,
                           box.oog.over_length_aft_m))
    for heat in vessel.heat_sources:
        values.extend((heat.x_min_m, heat.x_max_m, heat.y_min_m, heat.y_max_m,
                       heat.z_min_m, heat.z_max_m))
    if (any(not math.isfinite(float(v)) or abs(float(v)) > 10000. for v in values)
            or sum(box.height_m for box in instance.containers) > 10000.):
        raise ValueError('exact geometric cuts require finite ship-scale coordinates/heights')

def lower_bound(instance, *, budget_s=15., seed=0, tables=None, upper_bound=None):
    from ..scope import validate_objective, proof_scope, proof_details, source_hashes
    from stowage.frozen import active_stubs
    if active_stubs():
        raise ValueError('certified lower bound does not support frozen-interface overrides')
    coefficients = validate_objective(instance)
    _domain_guards(instance)
    weights = instance.objective
    integral = all(float(w).is_integer() for w in coefficients)
    integral = integral and isinstance(weights.future_restow_lifts_per_box,int) and weights.future_restow_lifts_per_box>=1
    cutoff = (int(upper_bound)-1 if upper_bound is not None and not isinstance(upper_bound, bool) and integral
              and math.isfinite(float(upper_bound)) and 0<=float(upper_bound)<2**53
              and float(upper_bound).is_integer() else None)
    diagnostics = _lazy_bound(instance,budget_s=budget_s,seed=seed,tables=tables,cost_cap=cutoff)
    result = CertifiedResult(**diagnostics['result'],
        lazy_iterations=diagnostics['iterations'],
        lazy_cut_counts=dict(Counter(f"{cut['kind']}:{cut['rule']}" for cut in diagnostics['cuts'])),
        static_legal_witness=diagnostics['static_legal'],
        securing_certificates=[cut for cut in diagnostics['cuts'] if cut['kind'].startswith('source_pinned')])
    if result.status == 'threshold_infeasible':
        result.status = 'cutoff_infeasible'
    result.proof_family = 'global_arbitrary_current_moves_final_stow_relaxation'
    name = ('counting_only_fallback' if result.status == 'counting_only'
            else 'certified_hatch_first_aboard_heat_e6_oog_securing')
    result.proof_scope = proof_scope(name)
    result.proof_details = proof_details(name)
    result.proof_details['source_sha256'] = source_hashes()
    result.proof_details['tested_integer_improvement_target'] = (cutoff+1 if cutoff is not None else None)
    result.support_flags = dict(result.support_flags or {},
        arbitrary_current_restows_allowed=True,
        load_once_restriction=False,
        final_static_oracle='stowage.checker.rules.check_static_rules',
        full_checker_pass_claim=False,
        geometry_outward_scale=1000000,
        fixed_background='immutable_disjoint_cell_and_stack_catalogues',
        integer_objective_cutoff=cutoff,
        proof_scope=result.proof_scope,
        proof_details=result.proof_details,
        geometry_domain='finite coordinates and summed nominal heights <= 10000 m',
        rule_freeze_v1=False)
    return result
