# Copied from experiments/mvp/frontier/lab/c4/c2-hatch/constructor_target.py; engine integration changes are local.
"""Cold, independently targeted first-feasible construction experiment.

The plain packing model supplies an admissible starting target. Infeasibility
of the *restricted constructive model* at that target only advances this
candidate search; it never raises the independent global lower bound.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from time import monotonic

from .constructor import (Construction, build_constructor_model, install_e9,
                         witness_actions, _exclude_ordered_witness, _static_cuts)
from .constructor_fast import add_cold_hints
from stowage.model import Placement


@dataclass
class TargetConstruction(Construction):
    independent_lower_bound: float | None = None
    independent_bound_status: str | None = None
    independent_bound_s: float = 0.
    initial_target: int | None = None
    final_target: int | None = None
    target_infeasible: list | None = None
    family: str = "independent_target_load_once_construction_not_a_global_bound"


def integral_objective(instance):
    objective = instance.objective
    weights = (objective.relocate, objective.restow, objective.hatch_cover,
               objective.future_restow, objective.future_hatch_cover)
    return (all(math.isfinite(float(value)) and 0 <= float(value) < 2**40
                and float(value).is_integer() for value in weights)
            and isinstance(objective.future_restow_lifts_per_box, int)
            and 1 <= objective.future_restow_lifts_per_box < 2**20
            and float(objective.future_restow) * objective.future_restow_lifts_per_box < 2**40)


def set_target(context, total_target, previous_selector=None):
    """Activate only the new total-cost target, including the forced-yard term.

    The previous target may be retired only after its construction solve was
    proved infeasible. Static/securing/replay nogoods remain in the model.
    This function produces no certificate and performs no solve.
    """
    model = context["model"]
    model.clear_assumptions()
    if previous_selector is not None:
        model.add(previous_selector == 0)
    selector = model.new_bool_var(f"construction_target_{total_target}")
    forced = context["base"].relocations * int(context["weights"].relocate)
    scaled_cap = (int(total_target) - forced) * context["scale"]
    model.add(sum(context["terms"]) + context["constant"] <= scaled_cap).only_enforce_if(selector)
    model.add_assumption(selector)
    return selector


def construct(instance, *, budget_s=30., seed=0, problem=None, tables=None,
              work_limit=None, apply_e9_fix=True):
    """Find a practical candidate at a cold mathematical target, without history.

    Plain packing receives at most min(4s,15% total). The joint load-once model
    then uses first-feasible sustained solves at the integral starting target.
    Only proven restricted-target INFEASIBLE permits target+1; UNKNOWN ends the
    search. Actual replay lifts and global independent LB remain separate.
    """
    from alns import Problem
    from . import bound as plain
    from .bound_joint import solve_model
    from .geometry_cuts import add_all_oog_pairs
    from .securing_cuts import add_securing_cut
    from stowage.checker.rules import check_static_rules

    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError("budget_s must be positive and finite")
    from ..work import SolverWork, WorkExhausted
    from .certified import _domain_guards
    from ..scope import validate_objective
    from stowage.frozen import active_stubs
    if active_stubs():
        raise ValueError('target construction does not support frozen-interface overrides')
    validate_objective(instance)
    _domain_guards(instance)
    started = monotonic()
    work = SolverWork(work_limit, wall_s=budget_s) if work_limit is not None else None
    output = TargetConstruction(target_infeasible=[])
    if not integral_objective(instance):
        output.status = "unsupported_nonintegral_or_large_objective"
        output.wall_time_s = monotonic() - started
        return output
    if apply_e9_fix:
        install_e9()
    plain_started = monotonic()
    plain_budget = min(4., budget_s * .15)
    cheap = plain.build_model(instance, budget_s=plain_budget if work is None else budget_s,
        seed=seed, tables=tables, **(dict(work_budget=work) if work is not None else {}))
    if not isinstance(cheap, dict):
        output.status = "unsupported_arrival_or_forecast"
        output.wall_time_s = monotonic() - started
        return output
    cheap["solve_budget_s"] = max(.001, plain_budget - (monotonic() - plain_started))
    if work is not None:
        cheap['deterministic_cap'] = work.limit*.15
    try:
        independent = plain.solve_model(cheap)
    except WorkExhausted as error:
        output.status = str(error)
        output.wall_time_s = monotonic()-started
        output.proof_work = work.metadata(stop_reason=str(error))
        return output
    cheap.pop('deterministic_cap', None)
    output.independent_lower_bound = independent.lower_bound
    output.independent_bound_status = independent.status
    output.independent_bound_s = monotonic() - plain_started
    output.log.append(dict(stage="independent_plain_packing", result=asdict(independent),
                           elapsed_s=output.independent_bound_s))
    if independent.status == "infeasible" or not math.isfinite(independent.lower_bound):
        output.status = "unknown_no_finite_independent_target"
        output.wall_time_s = monotonic() - started
        return output
    remaining = budget_s - (monotonic() - started)
    if remaining <= .01 or (work is not None and not work.available()):
        output.status = "unknown_budget_after_independent_bound"
        output.wall_time_s = monotonic() - started
        return output
    context = build_constructor_model(instance, budget_s=remaining,
        seed=seed, tables=cheap["T"],
        **(dict(work_budget=work) if work is not None else {}))
    if not isinstance(context, dict):
        output.status = "unsupported_arrival_or_forecast"
        output.wall_time_s = monotonic() - started
        return output
    # Rounding down by a tiny tolerance is conservative at floating integer
    # boundaries; an integral objective admits ceiling of any valid real LB.
    target = int(math.ceil(max(0., independent.lower_bound) - 1e-9))
    output.initial_target = target
    output.final_target = target
    selector = set_target(context, target)
    context["stop_after_first_solution"] = True
    output.log.append(dict(stage="targeted_first_feasible", initial_target=target,
        hinted_birth_order=add_cold_hints(context), eager_oog_pairs=add_all_oog_pairs(context),
        forced_yard_constant=context["base"].relocations * int(instance.objective.relocate),
        safe_unary_domains=context["unary_info"]))
    problem = problem or Problem(instance)
    while budget_s - (monotonic() - started) > .01 and (work is None or work.available()):
        context["solve_budget_s"] = budget_s - (monotonic() - started)
        try:
            result = solve_model(context)
        except WorkExhausted as error:
            output.status = str(error)
            break
        output.iterations += 1
        row = dict(status=result.status, target=target, objective=result.relaxation_incumbent,
                   elapsed_s=monotonic() - started)
        output.log.append(row)
        assignment = result.relaxation_assignment
        if assignment is None:
            if result.status == "infeasible":
                output.target_infeasible.append(target)
                target += 1
                output.final_target = target
                selector = set_target(context, target, selector)
                continue
            output.status = "unknown_at_construction_target"
            break
        ship = instance.onboard.model_copy(update={"placements": [
            Placement(container_id=bid, slot=slot) for bid, slot in assignment.items()],
            "covers_off": {}})
        report = check_static_rules(instance, ship, call_index=instance.call_index)
        violations = [v for v in report.violations if v.severity == 'hard']
        if (not report.ran or report.error
                or any(v.rule_id.startswith('checker.') for v in violations)):
            output.status = 'static_oracle_unknown'
            break
        row["static_violations"] = sorted({v.rule_id for v in violations})
        if output.first_model_witness is None:
            output.first_model_actions = witness_actions(
                assignment, result.first_aboard_order, instance.load_list)
            raw_scored = problem.score(output.first_model_actions)
            output.first_model_witness = dict(assignment=assignment,
                order=result.first_aboard_order, static_violations=row["static_violations"],
                objective=result.relaxation_incumbent, target=target,
                replay_legal=raw_scored is not None,
                replay_lifts=raw_scored.objective if raw_scored is not None else None)
        if violations:
            output.cuts.extend(_static_cuts(context, assignment, ship, violations))
            continue
        cut, securing = add_securing_cut(context, assignment, ship)
        row["securing_status"] = securing.get("status")
        if cut is not None:
            output.cuts.append(cut)
            continue
        actions = witness_actions(assignment, result.first_aboard_order, instance.load_list)
        scored = problem.score(actions)
        row["replay_legal"] = scored is not None
        if scored is None:
            _exclude_ordered_witness(context, assignment, result.first_aboard_order)
            output.cuts.append(dict(kind="replay_assignment_and_order", size=len(actions)))
            continue
        output.actions = actions
        output.assignment = assignment
        output.first_aboard_order = result.first_aboard_order
        output.replay_lifts = scored.objective
        output.first_replay_witness = dict(assignment=assignment, order=result.first_aboard_order,
            model_objective=result.relaxation_incumbent, target=target,
            replay_lifts=scored.objective, elapsed_s=monotonic() - started)
        output.status = "replay_legal_needs_ballast_and_full_checker"
        break
    output.wall_time_s = monotonic() - started
    output.proof_work = (work.metadata(stop_reason=output.status) if work is not None
                         else dict(unit='wall seconds', limit=budget_s, work_complete=False))
    return output
