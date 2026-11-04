"""Joint physical-slot packing proposals with unchanged native-mask replay.

This experiment groups bookings only to reduce the proposal model. Every real
container is subsequently placed through the frozen production feasibility
engine and securing geometry. Neither CP feasibility nor CP infeasibility is a
shipping certificate. Full terminal replay, ballast and checking remain owned
by the caller. Group maximum masses and millimetre rounding can exclude legal
layouts; an unsuccessful proposal falls back to the original constructor.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import time

import numpy as np


VERSION = "scale-joint-packing-3-scoped-e6-restricted-presolve"
PRESOLVE_MODE = "one_iteration_no_substitution_no_probing_no_symmetry"


def add_e6_full_hold_condition(model, *, deck_heavy, hold_occupied, fixed_deck_heavy=False):
    """Conservative proposal restriction: deck H implies every hold cell used.

    Full hold occupancy is sufficient to remove compatible *empty* hold slots
    from E6. It can exclude valid stows with incompatible empty hold space, so
    this is deliberately a search restriction, never an E6 rule replacement.
    The unchanged native predicate is still evaluated on the real departure.
    """
    if not deck_heavy and not fixed_deck_heavy:
        return None
    any_deck_heavy = model.NewBoolVar("e6_any_deck_heavy")
    model.AddMaxEquality(any_deck_heavy, [*deck_heavy, int(fixed_deck_heavy)])
    for occupied in hold_occupied:
        model.Add(occupied >= any_deck_heavy)
    return any_deck_heavy


def _solve_with_owner_log(solver, model, *, attempt, deadline, search_mode):
    """Persist CP setup/progress when the owner supplies a log directory.

    Logging is diagnostic only and changes no model row or acceptance check.
    A pre-Solve record survives even if one indivisible native call overruns.
    """
    directory = os.environ.get("SCALE_PACKING_LOG")
    if not directory:
        return solver.Solve(model)
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"cp-owner-{os.getpid()}.jsonl"
    solver.parameters.log_search_progress = True
    solver.parameters.log_to_stdout = False
    proto = model.Proto()
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        def record(kind, **payload):
            stream.write(json.dumps(dict(kind=kind, pid=os.getpid(), attempt=attempt,
                wall_time=time.time(), monotonic=time.monotonic(), **payload), sort_keys=True) + "\n")
            stream.flush()

        record("before_solve", deadline=deadline, search_mode=search_mode, presolve_mode=PRESOLVE_MODE,
               remaining_s=max(0., deadline - time.monotonic()),
               variables=len(proto.variables), constraints=len(proto.constraints),
               model_stats=model.ModelStats(), parameters=str(solver.parameters))
        solver.log_callback = lambda message: record("cp_progress", message=message)
        try:
            status = solver.Solve(model)
            record("after_solve", status=solver.StatusName(status),
                   response_stats=solver.ResponseStats())
            return status
        finally:
            solver.log_callback = None


@dataclass
class BookingGroup:
    boxes: list[str]
    representative: int
    height_mm: int
    share_kg: int


def booking_groups(instance, tables):
    """Keep all booking facts and static eligibility; coarsen actual kg only.

    Gross mass is represented conservatively by the group's maximum, while
    exact actual kg still belongs to native replay. Category includes POD,
    equipment, special requests, DG and OOG. Different rank/heavy flags or
    static domains never share a group.
    """
    from stowage.solver.twostep import category_of

    boxes = {b.id: b for b in instance.containers}
    retained = {p.container_id for p in instance.onboard.placements}
    groups = {}
    for cid in sorted(set(instance.load_list) - retained):
        bi = tables.box_index[cid]
        key = (category_of(boxes[cid], instance), int(tables.rank[bi]),
               bool(tables.e1_threshold is not None and tables.mass[bi] >= tables.e1_threshold),
               bool(tables.closes_stack[bi]), float(tables.height[bi]),
               tables.static[bi].tobytes())
        if key not in groups:
            groups[key] = BookingGroup([], bi, int(math.ceil(tables.height[bi] * 1000 - 1e-7)), 0)
        group = groups[key]
        group.boxes.append(cid)
        group.share_kg = max(group.share_kg, int(math.ceil(tables.share[bi] - 1e-7)))
    return list(groups.values())


def build_model(instance, tables, *, deadline, seed=0):
    """Build a grouped slot model; missing native interactions are replayed.

    Explicit cell occupancy and equal support roofs couple adjacent bay slots.
    A long placement therefore cannot consume an imaginary independent slot,
    and hold columns cannot silently switch from twenty-foot to long cargo.
    """
    from ortools.sat.python import cp_model
    from stowage.conventions import cells_of_slot

    def clock():
        if time.monotonic() >= deadline:
            raise TimeoutError("joint packing model build exhausted deadline")

    clock()
    groups = booking_groups(instance, tables)
    model = cp_model.CpModel()
    fixed = {}
    boxes = {b.id: b for b in instance.containers}
    for placement in instance.onboard.placements:
        for cell in cells_of_slot(placement.slot):
            if cell in fixed:
                raise ValueError("overlapping retained cargo")
            fixed[cell] = (boxes[placement.container_id], len(cells_of_slot(placement.slot)))
    variables = {}
    by_cell = defaultdict(list)
    for gi, group in enumerate(groups):
        clock()
        bi = group.representative
        eligible = []
        for si in np.flatnonzero(tables.static[bi]):
            si = int(si)
            cells = cells_of_slot(tables.slot_ids[si])
            if any(c in fixed for c in cells):
                continue
            var = model.NewBoolVar(f"g{gi}s{si}")
            variables[gi, si] = var
            eligible.append(var)
            for cell in cells:
                by_cell[cell].append((gi, si, var))
        model.Add(sum(eligible) == len(group.boxes))

    occupancy, heights, ranks, heavy, long, closed, mass = {}, {}, {}, {}, {}, {}, {}
    maxheight = max((g.height_mm for g in groups), default=4000)
    for cell in tables.cell_ids:
        records = by_cell[cell]
        fb, units = fixed.get(cell, (None, 1))
        fbi = tables.box_index[fb.id] if fb is not None else None
        occupancy[cell] = model.NewBoolVar(f"o{len(occupancy)}")
        model.Add(occupancy[cell] == sum(v for _, _, v in records) + int(fb is not None))
        heights[cell] = sum(groups[g].height_mm * v for g, _, v in records)
        ranks[cell] = sum(int(tables.rank[groups[g].representative]) * v for g, _, v in records)
        heavy[cell] = sum(int(tables.e1_threshold is not None and
                              tables.mass[groups[g].representative] >= tables.e1_threshold) * v
                          for g, _, v in records)
        long[cell] = sum(int(tables.length[groups[g].representative] != 20) * v for g, _, v in records)
        closed[cell] = sum(int(tables.closes_stack[groups[g].representative]) * v for g, _, v in records)
        mass[cell] = sum(groups[g].share_kg * v for g, _, v in records)
        if fb is not None:
            fh = int(math.ceil(tables.height[fbi] * 1000 - 1e-7))
            maxheight = max(maxheight, fh)
            heights[cell] += fh
            ranks[cell] += int(tables.rank[fbi])
            heavy[cell] += int(tables.e1_threshold is not None and tables.mass[fbi] >= tables.e1_threshold)
            long[cell] += int(tables.length[fbi] != 20)
            closed[cell] += int(tables.closes_stack[fbi])
            mass[cell] += int(math.ceil(tables.mass[fbi] / units - 1e-7))

    e6_threshold = getattr(tables, "e6_threshold", None)
    if e6_threshold is not None:
        # Use every real kg value rather than only the representative. A
        # group spanning the E6 threshold is conservatively treated as H.
        heavy_groups = {gi for gi, group in enumerate(groups)
                        if any(float(boxes[cid].gross_mass_kg) >= e6_threshold for cid in group.boxes)}
        heavy_deck = [var for (gi, si), var in variables.items()
                      if gi in heavy_groups and tables.slot_deck[si]]
        fixed_heavy_deck = any(
            boxes[p.container_id].gross_mass_kg >= e6_threshold and
            any(tables.stack_deck[int(tables.cell_stack[tables.cell_index[c]])]
                for c in cells_of_slot(p.slot))
            for p in instance.onboard.placements)
        hold_occupied = [occupancy[cell] for j, ids in enumerate(tables.stack_cells)
                         if not tables.stack_deck[j] for ci in ids
                         for cell in [tables.cell_ids[int(ci)]]]
        add_e6_full_hold_condition(model, deck_heavy=heavy_deck,
                                  hold_occupied=hold_occupied, fixed_deck_heavy=fixed_heavy_deck)

    floors = {}
    for j, stack in enumerate(tables.stack_ids):
        clock()
        ids = [tables.cell_ids[int(ci)] for ci in tables.stack_cells[j]]
        for k, cell in enumerate(ids):
            floors[cell] = int(round(tables.stack_base_z[j] * 1000)) + sum(heights[c] for c in ids[:k])
            if not k:
                continue
            below = ids[k - 1]
            model.Add(occupancy[cell] <= occupancy[below])
            for support in (ids[:k] if tables.e2_tol else [below]):
                model.Add(ranks[cell] <= ranks[support] + int(tables.e2_tol) + 4 * (1 - occupancy[cell]))
            if tables.e1_threshold is not None:
                model.Add(heavy[cell] <= heavy[below] + 1 - occupancy[cell])
            model.Add(long[cell] >= long[below] - (1 - occupancy[cell]))
            model.Add(occupancy[cell] + closed[below] <= 1)
            if not tables.stack_deck[j]:
                model.Add(long[cell] <= long[below] + 1 - occupancy[cell])
        total_mass = sum(mass[c] for c in ids)
        total_height = sum(heights[c] for c in ids)
        has20 = model.NewBoolVar(f"twenty{j}")
        haslong = model.NewBoolVar(f"long{j}")
        mixed = model.NewBoolVar(f"mixed{j}")
        for cell in ids:
            model.Add(has20 >= occupancy[cell] - long[cell])
            model.Add(haslong >= long[cell])
        model.Add(has20 <= sum(occupancy[c] - long[c] for c in ids))
        model.Add(haslong <= sum(long[c] for c in ids))
        model.Add(mixed <= has20)
        model.Add(mixed <= haslong)
        model.Add(mixed >= has20 + haslong - 1)
        lim20, lim40, limmix = (int(math.floor(a[j] + 1e-7)) for a in
                                (tables.stack_lim_20, tables.stack_lim_40, tables.stack_lim_mixed))
        model.Add(total_mass <= lim40 + (lim20 - lim40) * has20)
        model.Add(total_mass <= lim40 + (lim20 - lim40) * has20 + (limmix - lim20) * mixed)
        height_limit = float(tables.stack_hmax[j])
        visibility = float(tables.extra.get("stack_visibility_top", np.full(len(tables.stack_ids), np.inf))[j])
        if math.isfinite(visibility):
            height_limit = min(height_limit, visibility - float(tables.stack_base_z[j]))
        model.Add(total_height <= max(0, int(math.floor(height_limit * 1000 + 1e-7))))

    # Equal cumulative support height is shared by all boxes using one long
    # slot. Aggregating by selected slot avoids one big-M row per booking.
    by_slot = defaultdict(list)
    for (gi, si), var in variables.items():
        if tables.length[groups[gi].representative] != 20:
            by_slot[si].append(var)
    roof_bound = int(maxheight * max((len(x) for x in tables.stack_cells), default=1)
                     + 1000 * (max(tables.stack_base_z, default=0) - min(tables.stack_base_z, default=0)) + 10)
    for si, choices in by_slot.items():
        cell1, cell2 = cells_of_slot(tables.slot_ids[si])
        used = sum(choices)
        model.Add(floors[cell1] - floors[cell2] <= roof_bound * (1 - used))
        model.Add(floors[cell2] - floors[cell1] <= roof_bound * (1 - used))

    # Fill low holds first while retaining an incentive for balanced moments.
    # Objective coefficients are proposal preferences, never checker margins.
    cells = {c.id: c for c in instance.vessel.cells}
    objective = []
    transverse, longitudinal = [], []
    center = instance.vessel.hull.lpp_m / 2 if instance.vessel.hull is not None else 0.
    maxmoment = 1
    for (gi, si), var in variables.items():
        if len(objective) % 4096 == 0:
            clock()
        bi = groups[gi].representative
        footprint = [cells[c] for c in cells_of_slot(tables.slot_ids[si])]
        tonnes = max(1, int(round(groups[gi].share_kg * len(footprint) / 1000)))
        z = sum(c.vcg_base_m for c in footprint) / len(footprint)
        y = int(round(tonnes * sum(c.tcg_m for c in footprint) / len(footprint)))
        x = int(round(tonnes * (sum(c.lcg_m for c in footprint) / len(footprint) - center)))
        objective.append((int(tables.slot_deck[si]) * 10000 + int(round(tonnes * z * 10))) * var)
        transverse.append(y * var)
        longitudinal.append(x * var)
        maxmoment += abs(y) + abs(x)
    abs_y = model.NewIntVar(0, maxmoment, "abs_y")
    abs_x = model.NewIntVar(0, maxmoment, "abs_x")
    fixed_y = fixed_x = 0
    for placement in instance.onboard.placements:
        footprint = [cells[c] for c in cells_of_slot(placement.slot)]
        tonnes = boxes[placement.container_id].gross_mass_kg / 1000
        fixed_y += int(round(tonnes * sum(c.tcg_m for c in footprint) / len(footprint)))
        fixed_x += int(round(tonnes * (sum(c.lcg_m for c in footprint) / len(footprint) - center)))
    model.AddAbsEquality(abs_y, sum(transverse) + fixed_y)
    model.AddAbsEquality(abs_x, sum(longitudinal) + fixed_x)
    model.Minimize(sum(objective) + 10 * abs_y + abs_x)
    return model, groups, variables


def replay_failure_diagnostics(canonical, ex, tables, boxes, samples, *, deadline, limit=4):
    """Explain bounded rejected prescribed choices using unchanged local rules.

    Absence of a public local violation does not identify or invalidate a
    native completion/access screen. This diagnostic never changes search.
    """
    from stowage.checker.rules import placement_violations
    from stowage.model import Placement

    rows = []
    diagnostic_deadline = min(deadline, time.monotonic() + 2.)
    try:
        ship = canonical.onboard.model_copy(update={"placements": [
            Placement(container_id=cid, slot=loc[1])
            for cid, loc in ex.where.items() if loc[0] == "ship"]})
    except Exception as exc:
        return [{"classification": "diagnostic_setup_error", "error": f"{type(exc).__name__}: {exc}"}]
    for gi, si, cid, stage, detail in samples[:limit]:
        if time.monotonic() >= diagnostic_deadline:
            break
        row = {"group_index": int(gi), "slot_index": int(si), "container_id": cid,
               "slot": tables.slot_ids[si], "rejection_stage": stage, "rejection_detail": detail}
        started = time.monotonic()
        try:
            violations = placement_violations(canonical, ship, boxes[cid], tables.slot_ids[si],
                                              call_index=canonical.call_index)
            row["violations"] = [dict(rule_id=v.rule_id, severity=v.severity, message=v.message,
                                      containers=list(v.containers), slots=list(v.slots))
                                 for v in violations]
            hard = [v for v in violations if v.severity == "hard"]
            row["rule_ids"] = sorted({v.rule_id for v in violations})
            row["classification"] = (
                "public_local_hard_rule" if hard else
                "native_mask_rejected_without_public_local_hard_rule" if stage == "native_mask" else
                "search_filter_without_public_local_hard_rule")
            if stage == "native_mask" and not hard:
                row["interpretation"] = "Completion/access/other native screens remain possible; no cause proved."
        except Exception as exc:
            row.update(classification="diagnostic_rule_error", error=f"{type(exc).__name__}: {exc}")
        row["diagnostic_time_s"] = time.monotonic() - started
        rows.append(row)
    return rows


def replay_assignment(canonical, tables, groups, selected, *, deadline, config=None, initial_engine=None):
    """Bind actual IDs, then push every action through the native mask.

    Hold-first and bottom-up ordering preserves supports/hatch access. Failure
    to find a real booking for a selected slot rejects the proposal, never
    changes its category or silently relocates cargo.
    """
    from stowage.actions import get_action_space
    from stowage.actions.expander import Expander
    from stowage.actions.space import Place
    from stowage.checker.rules import e6_completion_possible
    from stowage.model import Placement
    from stowage.solver.securing_geometry import securing_geometry_violation, securing_state_violations
    from stowage.solver.twostep import PreStowPlan, SlotCategory, category_of
    from stowage.solver.twostep_adapter import make_planning_engine

    if time.monotonic() >= deadline:
        return None, {"packing_replay_status": "timeout", "packing_replay_depth": 0}
    engine = (initial_engine.copy() if initial_engine is not None else
              make_planning_engine(canonical, get_action_space("boxslot"), config=config))
    if time.monotonic() >= deadline:
        return None, {"packing_replay_status": "timeout:engine_setup", "packing_replay_depth": 0}
    ex = Expander(canonical)
    boxes = {b.id: b for b in canonical.containers}
    remaining = {gi: sorted(group.boxes, key=lambda c: (-boxes[c].gross_mass_kg, c))
                 for gi, group in enumerate(groups)}
    pending = sorted(selected, key=lambda item: (bool(tables.slot_deck[item[1]]),
                     int(max(tables.slot_k[item[1]])), tables.slot_ids[item[1]], item[0]))
    actions = []
    scalar_queries = 0
    while pending:
        if time.monotonic() >= deadline:
            return None, {"packing_replay_status": "timeout", "packing_replay_depth": len(actions)}
        progress = False
        rejected_samples = {}

        def rejected(gi, si, cid, stage, detail=None):
            # One real booking per prescribed group/slot, at most four slots.
            if (gi, si) not in rejected_samples and len(rejected_samples) < 4:
                rejected_samples[gi, si] = (gi, si, cid, stage, detail)
        # Prescribed placements need only exact scoped pair membership.
        # A whole legal_index() expands every alternative destination and can
        # spend the remaining deadline on an unrelated final C1f/E6 screen.
        hold_pending = any(not tables.slot_deck[s] for _, s in pending)
        for index, (gi, si) in enumerate(pending):
            if time.monotonic() >= deadline:
                return None, {"packing_replay_status": "timeout:scan", "packing_replay_depth": len(actions)}
            # Never load deck cargo while a prescribed hold slot is pending.
            if tables.slot_deck[si] and hold_pending:
                continue
            slot = tables.slot_ids[si]
            for cid in remaining[gi]:
                if time.monotonic() >= deadline:
                    return None, {"packing_replay_status": "timeout:scan", "packing_replay_depth": len(actions)}
                action = Place(cid, slot)
                scalar_queries += 1
                if not engine.is_legal(action):
                    rejected(gi, si, cid, "native_mask")
                    continue
                if time.monotonic() >= deadline:
                    return None, {"packing_replay_status": "timeout:membership", "packing_replay_depth": len(actions)}
                if callable(getattr(engine, "forces_restow", None)) and engine.forces_restow(action):
                    rejected(gi, si, cid, "forced_restow_search_filter")
                    continue
                reason = securing_geometry_violation(canonical, ex, action, include_existing=True)
                if reason:
                    rejected(gi, si, cid, "securing_geometry", reason)
                    continue
                if time.monotonic() >= deadline:
                    return None, {"packing_replay_status": "timeout:geometry", "packing_replay_depth": len(actions)}
                engine.push(action)
                ex.step(action)
                remaining[gi].remove(cid)
                pending.pop(index)
                actions.append(action)
                progress = True
                break
            if progress:
                break
        if not progress:
            diagnostics = replay_failure_diagnostics(canonical, ex, tables, boxes,
                list(rejected_samples.values()), deadline=deadline)
            return None, {"packing_replay_status": "native_rejected", "packing_replay_depth": len(actions),
                          "packing_replay_pending": len(pending),
                          "packing_replay_rejections": diagnostics}
    if not engine.is_complete():
        return None, {"packing_replay_status": "incomplete", "packing_replay_depth": len(actions)}
    if time.monotonic() >= deadline:
        return None, {"packing_replay_status": "timeout:completion", "packing_replay_depth": len(actions)}
    violations = securing_state_violations(canonical, ex)
    ship = canonical.onboard.model_copy(update={"placements": [
        Placement(container_id=cid, slot=loc[1]) for cid, loc in ex.where.items() if loc[0] == "ship"]})
    if violations or not e6_completion_possible(canonical, ship, (), call_index=canonical.call_index):
        return None, {"packing_replay_status": "departure_geometry_rejected", "packing_replay_depth": len(actions),
                      "packing_replay_violations": violations}
    slots = tuple(SlotCategory(action.slot, category_of(boxes[action.box], canonical), action.box)
                  for action in actions)
    return PreStowPlan(slots=slots, actions=tuple(actions)), {
        "packing_replay_status": "ok", "packing_replay_depth": len(actions),
        "packing_replay_mask_queries": 0, "packing_replay_scalar_queries": scalar_queries,
        "packing_e6_proposal_restriction": "deck_H_implies_full_hold_cells",
        "packing_full_checker_run": False, "packing_repair_used": False}


def joint_preplan(canonical, *, deadline, seed=0, feasibility_config=None, max_proposals=3):
    """Return a native-replayed proposal and stage telemetry, or ``None``."""
    from ortools.sat.python import cp_model
    from stowage.feasibility import FeasibilityConfig
    from stowage.feasibility.tables import build_tables
    from stowage.actions import get_action_space
    from stowage.solver.twostep_adapter import make_planning_engine

    started = time.monotonic()
    metrics = {"packing_version": VERSION, "packing_cp_workers": 1,
               "packing_e6_proposal_restriction": "deck_H_implies_full_hold_cells",
               "packing_presolve_mode": PRESOLVE_MODE,
               "packing_status": "not_started", "packing_attempts": []}
    if canonical.discharge_list:
        metrics["packing_status"] = "unsupported_discharge"
        return None, metrics
    config = feasibility_config or FeasibilityConfig(extra={"global_screen": 0})
    try:
        if time.monotonic() >= deadline:
            raise TimeoutError("joint packing expired before native setup")
        # Native engine setup owns the exact same immutable table object used
        # by proposal and replay. Copies preserve its original empty prefix.
        initial_engine = make_planning_engine(canonical, get_action_space("boxslot"), config=config)
        tables = getattr(initial_engine, "T", None)
        if tables is None:
            tables = build_tables(canonical)
        metrics["packing_native_setup_time_s"] = time.monotonic() - started
        if time.monotonic() >= deadline:
            raise TimeoutError("joint packing native setup exhausted deadline")
        model, groups, variables = build_model(canonical, tables, deadline=deadline, seed=seed)
        metrics.update(packing_model_time_s=time.monotonic() - started,
                       packing_groups=len(groups), packing_variables=len(variables))
        for attempt in range(max_proposals):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            solver = cp_model.CpSolver()
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = int(seed + attempt)
            solver.parameters.max_time_in_seconds = max(.001, remaining * .65)
            # Retain one bounded simplification pass without the probing,
            # symmetry and substitution work implicated in earlier overruns.
            # Model constraints and both search effort limits stay unchanged.
            solver.parameters.cp_model_presolve = True
            solver.parameters.cp_model_probing_level = 0
            solver.parameters.symmetry_level = 0
            solver.parameters.max_presolve_iterations = 1
            solver.parameters.presolve_substitution_level = 0
            solver.parameters.max_deterministic_time = max(.001, min(10., remaining * .065))
            solver.parameters.stop_after_first_solution = True
            search_model = model
            search_mode = "original_objective"
            if attempt == 0:
                # The first task is to find a joint packing. Optimizing the
                # soft moment/deck surrogate before any incumbent spends LP
                # effort without producing a native-replayable proposal.
                # Clone retains every original constraint and no-good row.
                search_model = model.Clone()
                search_model.ClearObjective()
                ordered = sorted(variables, key=lambda item: (
                    bool(tables.slot_deck[item[1]]), int(max(tables.slot_k[item[1]])),
                    -int(tables.rank[groups[item[0]].representative]),
                    -groups[item[0]].share_kg, tables.slot_ids[item[1]], item[0]))
                decisions = [search_model.GetBoolVarFromProtoIndex(variables[key].Index()) for key in ordered]
                search_model.AddDecisionStrategy(decisions, cp_model.CHOOSE_FIRST, cp_model.SELECT_MAX_VALUE)
                solver.parameters.search_branching = cp_model.FIXED_SEARCH
                solver.parameters.linearization_level = 0
                search_mode = "satisfaction_hold_first_bottom_up_heavy_first"
            solve_started = time.monotonic()
            status = _solve_with_owner_log(solver, search_model, attempt=attempt, deadline=deadline,
                                          search_mode=search_mode)
            row = {"attempt": attempt, "cp_status": solver.StatusName(status),
                   "cp_time_s": time.monotonic() - solve_started, "search_mode": search_mode,
                   "presolve_mode": PRESOLVE_MODE}
            metrics["packing_attempts"].append(row)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                metrics["packing_status"] = "proposal_" + solver.StatusName(status).lower()
                break
            selected = [key for key, var in variables.items() if solver.BooleanValue(var)]
            replay_started = time.monotonic()
            proposal, detail = replay_assignment(canonical, tables, groups, selected,
                deadline=deadline, config=config, initial_engine=initial_engine)
            row.update(detail)
            row["replay_time_s"] = time.monotonic() - replay_started
            if proposal is not None and time.monotonic() < deadline:
                metrics["packing_status"] = "ok"
                metrics["packing_total_time_s"] = time.monotonic() - started
                return proposal, metrics
            model.Add(sum(variables[key] for key in selected) <= len(selected) - 1)
        metrics.setdefault("packing_status", "exhausted")
    except Exception as exc:
        metrics.update(packing_status="error", packing_error=f"{type(exc).__name__}: {exc}")
    metrics["packing_total_time_s"] = time.monotonic() - started
    return None, metrics


class Installation:
    def __init__(self, module, original, replacement):
        self.module, self.original, self.replacement = module, original, replacement

    def restore(self):
        if self.module._preplan is self.replacement:
            self.module._preplan = self.original

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.restore()


def install(*, cp_budget_s=60., fallback_fraction=.5, max_proposals=3):
    """Patch only the loaded runtime's carrier search; return restorable handle."""
    from dataclasses import replace
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.solver import twostep

    if cp_budget_s < 0 or not 0 <= fallback_fraction <= 1 or max_proposals < 1:
        raise ValueError("invalid packing budget")
    original = twostep._preplan

    def preplan(instance, *, beam_width, deadline, feasibility_config=None, seed=0):
        if rule_freeze_v1_enabled() or not twostep._use_fast_prestow(instance):
            return original(instance, beam_width=beam_width, deadline=deadline,
                            feasibility_config=feasibility_config, seed=seed)
        start = time.monotonic()
        canonical = twostep._canonical_instance(instance)
        cp_deadline = min(deadline, start + min(cp_budget_s, max(0., deadline - start) * (1 - fallback_fraction)))
        proposal, metrics = joint_preplan(canonical, deadline=cp_deadline, seed=seed,
            feasibility_config=feasibility_config, max_proposals=max_proposals)
        if proposal is not None:
            metrics.update(preplan_method=VERSION, preplan_search_method=VERSION,
                           preplan_constructor_fallback_used=False)
            return replace(proposal, search_metrics=metrics)
        fallback = original(instance, beam_width=beam_width, deadline=deadline,
                            feasibility_config=feasibility_config, seed=seed)
        detail = dict(fallback.search_metrics or {})
        detail.update(metrics)
        detail["packing_original_fallback_used"] = True
        return replace(fallback, search_metrics=detail)

    twostep._preplan = preplan
    return Installation(twostep, original, preplan)
