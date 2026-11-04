# Copied from experiments/mvp/frontier/lab/c4/c2-hatch/constructor_fast.py; engine integration changes are local.
"""Separate first-solution construction experiment; frozen constructor untouched.

The same restricted load-once model now gets one sustained feasibility search,
with soft first-aboard hints derived only from the current yard and cargo. This
module supplies candidates, never global lower bounds or checker-pass claims.
"""
from __future__ import annotations

import heapq
import math
from time import monotonic

from .constructor import (Construction, build_constructor_model, install_e9,
                         witness_actions, _exclude_ordered_witness, _static_cuts)
from stowage.model import Placement


def cold_yard_order(instance):
    """Merge top-to-bottom export chains, choosing heavy/late-POD cargo first.

    This order is only a CP hint: final support or hatch requirements can force
    a different order, with the ordinary model's charged export relocations.
    Non-export yard blockers are deliberately absent from the permutation.
    """
    required = set(instance.load_list)
    boxes = {box.id: box for box in instance.containers}
    first_call = {}
    for call in instance.rotation.calls[instance.call_index + 1:]:
        first_call.setdefault(call.locode, call.index)
    chains = [[bid for bid in reversed(stack.boxes) if bid in required]
              for stack in instance.yard.stacks]
    seen = {bid for chain in chains for bid in chain}
    chains += [[bid] for bid in sorted(required - seen)]
    available = []

    def offer(chain_index, offset):
        bid = chains[chain_index][offset]
        box = boxes[bid]
        heapq.heappush(available, (-float(box.gross_mass_kg),
            -first_call.get(box.pod, len(instance.rotation.calls)), bid,
            chain_index, offset))

    for index, chain in enumerate(chains):
        if chain:
            offer(index, 0)
    result = []
    while available:
        _, _, bid, index, offset = heapq.heappop(available)
        result.append(bid)
        if offset + 1 < len(chains[index]):
            offer(index, offset + 1)
    if len(result) != len(required) or set(result) != required:
        raise ValueError("load-list boxes must occur once in the current yard")
    return result


def add_cold_hints(context):
    """Soft hints cannot shrink or certify the construction model's domain."""
    model, T = context["model"], context["T"]
    order = cold_yard_order(context["instance"])
    for rank, bid in enumerate(order):
        model.add_hint(context["birth"][T.box_index[bid]], rank)
    for flag in context["relocated"].values():
        model.add_hint(flag, 0)
    return order


def construct(instance, *, budget_s=30., seed=0, problem=None, tables=None):
    """Produce the first static-screened replay candidate with sustained CP.

    The first raw model witness remains separately accessible for first-time
    legality measurements. Cold ballast and independent full-check admission
    remain external stages. A feasible first solution can cost more than an
    optimization-run witness; replay_lifts, never an internal model bound, is
    the actual candidate objective.
    """
    from alns import Problem
    from .bound_joint import solve_model
    from .geometry_cuts import add_all_oog_pairs
    from .securing_cuts import add_securing_cut
    from stowage.checker.rules import check_static_rules

    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError("budget_s must be positive and finite")
    started = monotonic()
    output = Construction()
    install_e9()
    context = build_constructor_model(instance, budget_s=budget_s, seed=seed, tables=tables)
    if not isinstance(context, dict):
        output.status = "unsupported_arrival_or_forecast"
        output.wall_time_s = monotonic() - started
        return output
    order = add_cold_hints(context)
    context["stop_after_first_solution"] = True
    output.log.append(dict(strategy="first_solution_sustained_cold_order_hints",
        hinted_birth_order=order, eager_oog_pairs=add_all_oog_pairs(context),
        safe_unary_domains=context["unary_info"]))
    problem = problem or Problem(instance)
    while budget_s - (monotonic() - started) > .01:
        context["solve_budget_s"] = budget_s - (monotonic() - started)
        result = solve_model(context)
        output.iterations += 1
        row = dict(status=result.status, objective=result.relaxation_incumbent,
                   elapsed_s=monotonic() - started)
        output.log.append(row)
        assignment = result.relaxation_assignment
        if assignment is None:
            output.status = ("load_once_model_infeasible" if result.status == "infeasible"
                             else "load_once_model_no_witness")
            break
        ship = instance.onboard.model_copy(update={"placements": [
            Placement(container_id=bid, slot=slot) for bid, slot in assignment.items()],
            "covers_off": {}})
        report = check_static_rules(instance, ship, call_index=instance.call_index)
        violations = [v for v in report.violations if v.severity == "hard"]
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
                objective=result.relaxation_incumbent, replay_legal=raw_scored is not None,
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
            model_objective=result.relaxation_incumbent, replay_lifts=scored.objective,
            elapsed_s=monotonic() - started)
        output.status = "replay_legal_needs_ballast_and_full_checker"
        break
    output.wall_time_s = monotonic() - started
    return output
