# Copied from experiments/mvp/frontier/lab/c1/fx-exact/bound.py; engine integration changes are local.
"""Certified lift lower bounds from an exact, optimistic hatch-packing model.

Every legal empty-arrival stow maps to a feasible model solution: retain the
production unary eligibility mask, cell exclusion, bottom-up support and relaxed
stack envelopes; drop all other placement interactions and all load ordering.
Each used hold cover costs its opening/closure now, and each (cover, first later
POD call) pair costs two later cover lifts. Future restows are free. Add only the
unavoidable non-load yard blockers from ``stowage.bounds``. This is an exact
*relaxation*, not an exact operational stowage solver.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from time import monotonic

from ortools.sat.python import cp_model

from stowage.bounds import lower_bound as counting_lower_bound
from stowage.feasibility.tables import build_tables
from stowage.rules.catalogue import rule_freeze_v1_enabled


def _floor_scaled(value, scale=1000):
    numerator, denominator = float(value).as_integer_ratio()
    return numerator * scale // denominator


def _ceil_scaled(value, scale=1000):
    numerator, denominator = float(value).as_integer_ratio()
    return -(-numerator * scale // denominator)


@dataclass
class BoundResult:
    lower_bound: float
    hatch_lower_bound: float
    relocations: int
    status: str
    relaxation_assignment: dict[str, str] | None
    wall_time_s: float
    counting_lower_bound: float
    relaxation_incumbent: float | None = None
    variables: int = 0
    constraints: int = 0
    note: str = ""
    solver_deterministic_time: float = 0.


def lower_bound(instance, *, budget_s: float = 10.0, seed: int = 0,
                tables=None, work_budget=None, deterministic_cap=None) -> BoundResult:
    """Return a certified bound, including on time limit (never the incumbent).

    ``tables`` may reuse ``problem.engine.T`` where available. Unsupported
    arrival/discharge or joint-forecast cases retain the existing counting bound.
    Coefficients are rounded DOWN to 0.001 lift-weight units; stack sizes and
    limits are rounded outward. These choices only enlarge the relaxation.
    """
    started = monotonic()
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError("budget_s must be positive and finite")
    base = counting_lower_bound(instance)
    weights = instance.objective
    baseline_hatch = (base.hatch_cover_lifts * weights.hatch_cover
                      + base.future_hatch_cover_lifts * weights.future_hatch_cover)
    common = dict(relocations=base.relocations, counting_lower_bound=base.weighted)
    if (instance.onboard.placements or instance.discharge_list
            or instance.plan_calls or instance.forecasts):
        return BoundResult(base.weighted, baseline_hatch, status="counting_only",
            relaxation_assignment=None, wall_time_s=monotonic() - started,
            note="Packing relaxation supports empty arrival without forecast loads.", **common)

    # In the inactive legacy branch the production unary DG table probes an
    # empty ship. For evidence-backed SW1/SW2 geometry, an upper-tier floor is
    # unknown until supports exist, so that probe may reject a slot that a real
    # supported stow permits. Such a table is not a relaxation. Conservatively
    # retain the counting bound for every inactive DG case, even when the caller
    # supplies prebuilt tables; active tables use optimistic vertical domains.
    if not rule_freeze_v1_enabled() and any(box.dg is not None for box in instance.containers):
        return BoundResult(base.weighted, baseline_hatch, status="counting_only",
            relaxation_assignment=None, wall_time_s=monotonic() - started,
            note=("Inactive DG tables may reject supported upper-tier geometry; "
                  "retaining the map-wide counting bound."), **common)

    T = build_tables(instance) if tables is None else tables
    model = cp_model.CpModel()
    chosen = {}
    at_cell = defaultdict(list)
    at_stack = defaultdict(list)
    hold_cover = defaultdict(list)
    cover_call = defaultdict(list)
    first_call = {}
    for index in range(instance.call_index + 1, len(instance.rotation.calls)):
        first_call.setdefault(instance.rotation.calls[index].locode, index)
    boxes = {box.id: box for box in instance.containers}
    for bi, box_id in enumerate(T.box_ids):
        row = []
        for si, slot in enumerate(T.slot_ids):
            if not T.static[bi, si]:
                continue
            var = model.new_bool_var(f"x_{bi}_{si}")
            chosen[bi, si] = var
            row.append(var)
            for ci in T.slot_cells[si]:
                if ci >= 0:
                    at_cell[int(ci)].append(var)
            for sj in T.slot_stacks[si]:
                if sj >= 0:
                    at_stack[int(sj)].append((bi, var))
            covers = {T.stack_cover[int(sj)] for sj in T.slot_stacks[si]
                      if sj >= 0 and not T.stack_deck[int(sj)]}
            for cover in covers:
                hold_cover[cover].append(var)
                call = first_call.get(boxes[box_id].pod)
                if call is not None:
                    cover_call[cover, call].append(var)
        model.add_exactly_one(row)

    occupied = {}
    for ci in range(len(T.cell_ids)):
        occupied[ci] = model.new_bool_var(f"occ_{ci}")
        model.add(occupied[ci] == sum(at_cell[ci]))
    for sj, stack_cells in enumerate(T.stack_cells):
        for lower, upper in zip(stack_cells, stack_cells[1:]):
            model.add(occupied[upper] <= occupied[lower])
        # Shared mass per support, with outward rounding. Use the largest
        # applicable rating, so the length-dependent rating is safely relaxed.
        cap = max(T.stack_lim_mixed[sj], T.stack_lim_20[sj], T.stack_lim_40[sj])
        if math.isfinite(float(cap)):
            model.add(sum(_floor_scaled(T.share[bi]) * var
                          for bi, var in at_stack[sj]) <= _ceil_scaled(cap))
        cap_h = float(T.stack_hmax[sj])
        if math.isfinite(cap_h):
            model.add(sum(_floor_scaled(T.height[bi]) * var
                          for bi, var in at_stack[sj]) <= _ceil_scaled(cap_h))

    # A restricted, rigorous consequence of legacy E6. For an ordinary heavy
    # box on deck, an entirely empty compatible pair of hold columns is a
    # witness against E6. Its bottom cells cannot BOTH be empty. Production
    # placement_violations verifies each unary witness. Other DG cargo cannot
    # interact with a non-DG witness; lateral OOG can, so disable these cuts
    # whenever it exists. Do not assume all heavy cargo must be below deck.
    from stowage.checker.rules import placement_violations
    empty_ship = instance.onboard.model_copy(update={"placements": [], "covers_off": {}})
    lateral_oog = any(box.oog and any((box.oog.over_width_port_m,
        box.oog.over_width_starboard_m, box.oog.over_length_fore_m,
        box.oog.over_length_aft_m)) for box in boxes.values())
    e6_cuts = 0
    if (not rule_freeze_v1_enabled() and T.e6_fallback is True
            and T.e6_threshold is not None and not lateral_oog):
        for bi, box_id in enumerate(T.box_ids):
            if work_budget is None and monotonic() - started >= 0.3 * budget_s:
                break
            box = boxes[box_id]
            if (box.gross_mass_kg < T.e6_threshold or box.dg is not None
                    or box.oog is not None or box.special_stowage):
                continue
            deck_vars = [var for (b, si), var in chosen.items()
                         if b == bi and T.slot_deck[si]]
            if not deck_vars:
                continue
            for si, slot in enumerate(T.slot_ids):
                if work_budget is None and monotonic() - started >= 0.3 * budget_s:
                    break
                if not T.static[bi, si] or T.slot_deck[si]:
                    continue
                physical = [int(ci) for ci in T.slot_cells[si] if ci >= 0]
                if any(T.cell_k[ci] != 0 for ci in physical):
                    continue
                if any(v.severity == "hard" for v in placement_violations(
                        instance, empty_ship, box, slot, call_index=instance.call_index)):
                    continue
                model.add(sum(deck_vars) <= sum(occupied[ci] for ci in physical))
                e6_cuts += 1

    scale = 1000
    now = _floor_scaled(weights.hatch_cover, 2 * scale)
    later = _floor_scaled(weights.future_hatch_cover, 2 * scale)
    initially_off = set(instance.onboard.covers_off)
    constant = _floor_scaled(weights.hatch_cover, len(initially_off) * scale)
    terms = []
    for cover, variables in hold_cover.items():
        used = model.new_bool_var(f"cover_{cover}")
        model.add_max_equality(used, variables)
        if cover not in initially_off:
            terms.append(now * used)
    for (cover, call), variables in cover_call.items():
        used = model.new_bool_var(f"cover_{cover}_call_{call}")
        model.add_max_equality(used, variables)
        terms.append(later * used)
    model.minimize(sum(terms) + constant)

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = max(0.001, budget_s - (monotonic() - started))
    if work_budget is not None:
        work_budget.configure(solver, cap=deterministic_cap)
    status = solver.solve(model)
    deterministic_used = work_budget.record(solver) if work_budget is not None else float(solver.deterministic_time)
    status_name = solver.status_name(status).lower()
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(solver.solution_info())
    # BestObjectiveBound certifies the minimization lower bound; a feasible
    # objective value alone is an upper bound on the relaxation and is unsafe.
    packed_bound = max(0.0, math.floor(float(solver.best_objective_bound)) / scale)
    hatch_bound = max(baseline_hatch, packed_bound)
    total = max(base.weighted, base.relocations * weights.relocate + hatch_bound)
    assignment = None
    incumbent = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        assignment = {T.box_ids[bi]: T.slot_ids[si]
                      for (bi, si), var in chosen.items() if solver.value(var)}
        incumbent = float(solver.objective_value) / scale
    return BoundResult(total, hatch_bound, status=status_name,
        relaxation_assignment=assignment, wall_time_s=monotonic() - started,
        relaxation_incumbent=incumbent, solver_deterministic_time=deterministic_used,
        variables=len(model.proto.variables), constraints=len(model.proto.constraints),
        note=("Exact optimistic packing; ignores load order, yard load-load blocking, "
              f"DG pairs, weight order, restows and physics; {e6_cuts} safe bottom-slot E6 cuts. "
              "No full-domain optimality claim without equality to a checker-passed plan."), **common)
