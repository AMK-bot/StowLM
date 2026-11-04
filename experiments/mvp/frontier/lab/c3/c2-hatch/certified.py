"""Public dataclass interface for globally admissible final-stow lower bounds."""
import math
from collections import Counter
from dataclasses import dataclass
from bound_joint import BoundResult
from lazy_bound import lower_bound as _lazy_bound


@dataclass
class CertifiedResult(BoundResult):
    lazy_iterations: int = 0
    lazy_cut_counts: dict | None = None
    static_legal_witness: bool = False
    securing_certificates: list | None = None


def lower_bound(instance, *, budget_s=15., seed=0, tables=None, upper_bound=None):
    weights = instance.objective
    coefficients = (weights.relocate,weights.restow,weights.hatch_cover,
                    weights.future_restow,weights.future_hatch_cover)
    if any(not math.isfinite(float(w)) or float(w)<0 for w in coefficients):
        raise ValueError('certified lower bounds require finite nonnegative weights')
    integral = all(float(w).is_integer() for w in coefficients)
    integral = integral and isinstance(weights.future_restow_lifts_per_box,int) and weights.future_restow_lifts_per_box>=1
    cutoff = (int(upper_bound)-1 if upper_bound is not None and integral
              and math.isfinite(float(upper_bound)) and 0<=float(upper_bound)<2**53
              and float(upper_bound).is_integer() else None)
    diagnostics = _lazy_bound(instance,budget_s=budget_s,seed=seed,tables=tables,cost_cap=cutoff)
    result = CertifiedResult(**diagnostics['result'],
        lazy_iterations=diagnostics['iterations'],
        lazy_cut_counts=dict(Counter(f"{cut['kind']}:{cut['rule']}" for cut in diagnostics['cuts'])),
        static_legal_witness=diagnostics['static_legal'],
        securing_certificates=[cut for cut in diagnostics['cuts'] if cut['kind'].startswith('source_pinned')])
    result.proof_family = 'global_arbitrary_current_moves_final_stow_relaxation'
    result.support_flags = dict(result.support_flags or {},
        arbitrary_current_restows_allowed=True,
        load_once_restriction=False,
        final_static_oracle='stowage.checker.rules.check_static_rules',
        full_checker_pass_claim=False,
        geometry_outward_scale=1000000,
        fixed_background='immutable_disjoint_cell_and_stack_catalogues',
        integer_objective_cutoff=cutoff)
    return result
