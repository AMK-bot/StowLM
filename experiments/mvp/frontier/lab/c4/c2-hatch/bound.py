"""Certified lift lower bounds from an exact, optimistic hatch-packing model.

Every legal empty-arrival stow maps to a feasible model solution: retain the
production unary eligibility mask, cell exclusion, bottom-up support and relaxed
stack envelopes; drop all other placement interactions and all load ordering.
Each used hold cover costs its opening/closure now, and each (cover, first later
POD call) pair costs two later cover lifts. Port-wise required-box closure prices projected future restows exactly under legacy D15; rule-freeze crossing requirements remain relaxed. Add only the
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
    proof_family: str = "arbitrary_current_moves_final_stow_relaxation"
    support_flags: dict | None = None
    witness_hatch: float | None = None
    witness_restow: float | None = None
    projected_required: dict | None = None


def build_model(instance, *, budget_s: float = 10.0, seed: int = 0, tables=None):
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

    T = build_tables(instance) if tables is None else tables
    from strengthening import safe_unary_mask
    static, unary_info = safe_unary_mask(instance, T)
    model = cp_model.CpModel()
    chosen = {}
    at_cell = defaultdict(list)
    cell_placements = defaultdict(list)
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
            if not static[bi, si]:
                continue
            var = model.new_bool_var(f"x_{bi}_{si}")
            chosen[bi, si] = var
            row.append(var)
            for ci in T.slot_cells[si]:
                if ci >= 0:
                    at_cell[int(ci)].append(var)
                    cell_placements[int(ci)].append((bi, var))
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

    # Exact length-dependent mass envelopes: any twenty-foot cargo selects
    # the twenty rating, otherwise the long rating applies (production rule).
    for sj, members in at_stack.items():
        twenty_vars = [var for bi,var in members if T.length[bi] == 20]
        has_twenty = model.new_bool_var(f"has_twenty_{sj}")
        if twenty_vars:
            model.add_max_equality(has_twenty, twenty_vars)
        else:
            model.add(has_twenty == 0)
        demand = sum(_floor_scaled(T.share[bi])*var for bi,var in members)
        if math.isfinite(float(T.stack_lim_20[sj])):
            model.add(demand <= _ceil_scaled(T.stack_lim_20[sj])).only_enforce_if(has_twenty)
        if math.isfinite(float(T.stack_lim_40[sj])):
            model.add(demand <= _ceil_scaled(T.stack_lim_40[sj])).only_enforce_if(has_twenty.Not())

    # Full checker lashing._load_graph fails closed for every mixed 20/long
    # hold column (IRS special consideration is not an enabled override).
    # This is a necessary full-checker condition under both rule-freeze modes.
    for sj, members in at_stack.items():
        if T.stack_deck[sj]:
            continue
        long_hold = model.new_bool_var(f"long_hold_{sj}")
        twenty = [var for bi, var in members if T.length[bi] == 20]
        long = [var for bi, var in members if T.length[bi] != 20]
        model.add(sum(twenty) <= len(T.box_ids) * (1 - long_hold))
        model.add(sum(long) <= len(T.box_ids) * long_hold)

    # Necessary departure ordering rules. Every legal stow obeys these
    # pairwise physical-column implications. All lower/upper pairs are needed
    # for nonzero class tolerance: adjacent inequalities alone are too weak.
    weight_order_cuts = 0
    if not rule_freeze_v1_enabled():
        max_rank = max([int(rank) for rank in T.rank] + [0])
        for stack_cells in T.stack_cells:
            rank_at = {ci: sum(int(T.rank[bi]) * var for bi, var in cell_placements[ci])
                       for ci in stack_cells}
            heavy_at = {ci: sum(var for bi, var in cell_placements[ci]
                               if T.e1_threshold is not None and T.mass[bi] >= T.e1_threshold)
                        for ci in stack_cells}
            long_at = {ci: sum(var for bi, var in cell_placements[ci] if T.length[bi] != 20)
                       for ci in stack_cells}
            closed_at = {ci: sum(var for bi, var in cell_placements[ci] if T.closes_stack[bi])
                         for ci in stack_cells}
            for lo, lower in enumerate(stack_cells):
                for upper in stack_cells[lo + 1:]:
                    if T.e2_valid:
                        model.add(rank_at[upper] <= rank_at[lower] + T.e2_tol
                                  + max_rank * (1 - occupied[upper]))
                        weight_order_cuts += 1
                    if T.e1_threshold is not None:
                        model.add(heavy_at[upper] <= heavy_at[lower])
                        weight_order_cuts += 1
                    model.add(long_at[upper] >= long_at[lower] + occupied[upper] - 1)
                    model.add(occupied[upper] + closed_at[lower] <= 1)
                    weight_order_cuts += 2

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
            if monotonic() - started >= 0.3 * budget_s:
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
                if monotonic() - started >= 0.3 * budget_s:
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

    from strengthening import add_e6_bottom_oog
    e6_oog_cuts = add_e6_bottom_oog(locals())

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
    cover_used = {}
    hatch_terms = list(terms)
    for (cover, call), variables in cover_call.items():
        used = model.new_bool_var(f"cover_{cover}_call_{call}")
        model.add_max_equality(used, variables)
        cover_used[cover, call] = used
        terms.append(later * used)
        hatch_terms.append(later * used)

    # At a later port, required boxes are the least upward-closed set
    # containing its discharge targets and all active deck cargo on opened
    # covers. A long box belongs to both columns, transmitting requirements
    # across them. One Boolean per (box, call) prevents double charging.
    # Every actual D15 closure satisfies these implications, so optimizing
    # them is an admissible relaxation even if extra frozen crossing paths
    # are present. With positive restow weights their least solution matches
    # the complete legacy D15 closure exactly.
    restow_coefficient = _floor_scaled(weights.future_restow,
        weights.future_restow_lifts_per_box * scale)
    destinations = {bi: first_call.get(boxes[bid].pod, len(instance.rotation.calls))
                    for bi, bid in enumerate(T.box_ids)}
    required = {}
    restow_terms = []
    for call in range(instance.call_index + 1, len(instance.rotation.calls)):
        active = {bi for bi in range(len(T.box_ids)) if destinations[bi] >= call}
        targets = {bi for bi in active if destinations[bi] == call}
        if not targets:
            continue
        for bi in active:
            required[bi, call] = model.new_bool_var(f"required_{bi}_{call}")
            if bi in targets:
                model.add(required[bi, call] == 1)
            else:
                restow_terms.append(restow_coefficient * required[bi, call])
        for (bi, si), var in chosen.items():
            if bi not in active:
                continue
            if T.slot_deck[si]:
                stack_indices = {int(sj) for sj in T.slot_stacks[si] if sj >= 0}
                for cover, decks in T.cover_deck_stacks.items():
                    used = cover_used.get((cover, call))
                    if used is not None and stack_indices.intersection(decks):
                        model.add(required[bi, call] >= var + used - 1)
        for sj, stack_cells in enumerate(T.stack_cells):
            below = model.new_bool_var(f"below_{sj}_{call}_0")
            model.add(below == 0)
            for ki, ci in enumerate(stack_cells):
                next_below = model.new_bool_var(f"below_{sj}_{call}_{ki+1}")
                model.add(next_below >= below)
                for bi, var in cell_placements[ci]:
                    if bi not in active:
                        continue
                    r = required[bi, call]
                    model.add(r >= var + below - 1)
                    model.add(next_below >= var + r - 1)
                below = next_below
    terms.extend(restow_terms)
    model.minimize(sum(terms) + constant)

    return dict(locals())


def lower_bound(instance, *, budget_s: float = 10.0, seed: int = 0, tables=None):
    context = build_model(instance, budget_s=budget_s, seed=seed, tables=tables)
    if isinstance(context, BoundResult):
        return context
    return solve_model(context)


def solve_model(context, *, budget_s=None):
    started = context["started"]
    budget_s = context["budget_s"]
    seed = context["seed"]
    model = context["model"]
    T = context["T"]
    chosen = context["chosen"]
    base = context["base"]
    weights = context["weights"]
    baseline_hatch = context["baseline_hatch"]
    common = context["common"]
    scale = context["scale"]
    hatch_terms = context["hatch_terms"]
    restow_terms = context["restow_terms"]
    constant = context["constant"]
    required = context["required"]
    instance = context["instance"]
    e6_cuts = context["e6_cuts"]
    weight_order_cuts = context["weight_order_cuts"]
    unary_info = context["unary_info"]
    e6_oog_cuts = context["e6_oog_cuts"]
    solve_budget = context.get("solve_budget_s", budget_s)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.stop_after_first_solution = context.get("stop_after_first_solution", False)
    solver.parameters.max_time_in_seconds = max(0.001, min(solve_budget, budget_s - (monotonic() - started)))
    status = solver.solve(model)
    status_name = solver.status_name(status).lower()
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(solver.solution_info())
    # BestObjectiveBound certifies the minimization lower bound; a feasible
    # objective value alone is an upper bound on the relaxation and is unsafe.
    packed_bound = max(0.0, math.floor(float(solver.best_objective_bound)) / scale)
    hatch_bound = max(baseline_hatch, packed_bound)  # includes projected restows in C2
    total = max(base.weighted, base.relocations * weights.relocate + hatch_bound)
    assignment = None
    incumbent = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        assignment = {T.box_ids[bi]: T.slot_ids[si]
                      for (bi, si), var in chosen.items() if solver.value(var)}
        incumbent = float(solver.objective_value) / scale
    witness_hatch = witness_restow = projected_required = None
    if assignment is not None:
        witness_hatch = (solver.value(sum(hatch_terms)) + constant) / scale
        witness_restow = solver.value(sum(restow_terms)) / scale
        projected_required = {str(call): [T.box_ids[bi] for bi in range(len(T.box_ids))
                              if (bi, call) in required and solver.value(required[bi, call])]
                              for call in range(instance.call_index + 1, len(instance.rotation.calls))}
    return BoundResult(total, hatch_bound, status=status_name,
        relaxation_assignment=assignment, wall_time_s=monotonic() - started,
        relaxation_incumbent=incumbent,
        variables=len(model.proto.variables), constraints=len(model.proto.constraints),
        witness_hatch=witness_hatch, witness_restow=witness_restow,
        projected_required=projected_required,
        support_flags={"empty_arrival": True, "forecasts": False, "rule_freeze_v1": rule_freeze_v1_enabled(),
                       "final_state_only_cuts": True, "heat_domains": unary_info},
        note=("Exact optimistic packing and legacy D15 restow closure; ignores load order, yard load-load blocking, "
              f"DG pairs, frozen crossing rules and physics; {e6_cuts} safe bottom-slot E6 cuts; "
              f"{weight_order_cuts} necessary departure ordering cuts; "
              f"{e6_oog_cuts} conservative OOG-aware bottom E6 cuts; {unary_info}. "
              "No full-domain optimality claim without equality to a checker-passed plan."), **common)
