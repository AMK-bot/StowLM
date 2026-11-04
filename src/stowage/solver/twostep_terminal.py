"""Terminal box assignment, sequencing and bounded carrier-approved relocation.

Small/medium empty-ship calls first try two fast category-compatible replays:
yard-top boxes first, and the carrier's own representatives as a second candidate.
Both preserve the cargo-only slot pattern; actual expanded moves choose between
successful candidates. Tiny and nonempty-ship calls retain CP-SAT below.

CP-SAT assigns a real box to every compatible booking-category slot and orders
those slots. Lower cells precede upper cells; all hold loads under a hatch precede
its deck loads. Yard accessibility is a *soft* preference: a pending load box pays
once if a box originally below it is retrieved first. This is the classical
restricted blocks-relocation intuition (Caserta, Schwarze & Voss, EJOR 219, 2011).
The deterministic Expander chooses relocation destinations and counts real lifts.

APPROXIMATION: the CP objective counts first relocations from the original yard,
not repeated relocations after boxes land on other pending boxes. Non-load yard
blockers and discharge destinations can change the true count. Feasible candidates
are therefore ranked by the complete move engine's weighted objective, including
projected restows, rather than by that surrogate. A time-limited search is not an
optimality proof. Discharges use the oracle's deterministic first legal choice;
original quay restows keep their original slots. Discharge/relocation alternatives
are not exhaustively optimized in this baseline.

Every selected action passes the shared feasibility evaluator, including actual
box mass and whichever completion screens the caller configured. D79 demo runs
disable C2; ballast and the full checker own final physical acceptance. Failed
CP prefixes get no-good
cuts (a clause forbidding precisely that assignment and order prefix), so a dead
end triggers backtracking without changing the carrier's slot categories. CP-SAT
uses one worker. Ballast and full checker certification remain the finisher's job.
Source for CP-SAT modelling: OR-Tools CP-SAT documentation, developers.google.com/
optimization/cp/cp_solver; industry two-stage process: docs/domain.md E10 and D14.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from time import monotonic

from ortools.sat.python import cp_model

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.solver.securing_geometry import securing_geometry_violation, securing_state_violations
from stowage.solver.securing_geometry import level_four_tiebreak


class _TerminalDeadline(Exception):
    """An opaque oracle call exhausted the caller's remaining wall budget."""


def _precedences(instance, slots):
    """Necessary ship support ordering, plus no new deck blockers over hold loads."""
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    planned = {c: i for i, slot in enumerate(slots) for c in cells_of_slot(slot.slot)}
    edges = set()
    for stack in stacks.values():
        sequence = list(dict.fromkeys(planned[c] for c in stack.cell_ids if c in planned))
        edges.update(zip(sequence, sequence[1:]))
    holds, decks = defaultdict(set), defaultdict(set)
    for i, slot in enumerate(slots):
        for cell in cells_of_slot(slot.slot):
            stack = stacks[cells[cell].stack_id]
            if stack.hatch_cover_id is not None:
                (decks if stack.on_deck else holds)[stack.hatch_cover_id].add(i)
    for hatch in holds.keys() & decks.keys():
        edges.update((a, b) for a in holds[hatch] for b in decks[hatch] if a != b)
    if rule_freeze_v1_enabled():
        # D69 preserves far-to-near order within every physical sub-bay.
        endpoints = defaultdict(list)
        for i, slot in enumerate(slots):
            footprint = [cells[c] for c in cells_of_slot(slot.slot)]
            y = sum(c.tcg_m for c in footprint) / len(footprint)
            for bay in {stacks[c.stack_id].bay for c in footprint}:
                endpoints[bay].append((i, y))
        sign = 1 if instance.port.berthing_side == "port" else -1
        for same_bay in endpoints.values():
            for i, yi in same_bay:
                for j, yj in same_bay:
                    if (parse_slot(slots[j].slot).on_deck
                            and sign * yi < sign * yj - 1e-9):
                        edges.add((i, j))
    return edges


def _slot_adjustments(instance, slot, reserved_cells):
    """D73 local relocation proposals, retaining bay, tier and booked category.

    Only four neighboring rows are considered. This explicit search bound is
    a baseline policy, not a claim that other carrier-approved moves cannot
    help. Occupied or other planned category footprints are never overwritten;
    the shared mask decides whether a proposal is legal for the actual box.
    """
    from stowage.conventions import format_slot

    address = parse_slot(slot)
    cells = {cell.id for cell in instance.vessel.cells}
    rows = {parse_slot(cell).row for cell in cells}
    candidates = []
    for row in sorted(rows, key=lambda row: (abs(row-address.row), row)):
        target = format_slot(address.bay, row, address.tier)
        footprint = set(cells_of_slot(target))
        if target != slot and footprint <= cells and not footprint & reserved_cells:
            candidates.append(target)
        if len(candidates) >= 4:
            break
    return candidates


def _fast_terminal(instance, prestow, categories, *, deadline, engine_factory,
                   feasibility_config, metrics):
    """Try yard-first category substitution, then the carrier's original boxes.

    APPROXIMATION: small/medium Stage A uses two greedy replays instead of
    optimizing all category assignments with CP-SAT. The cargo-only category
    pattern is retained, allowing the existing bounded D73 row adjustment.
    Actual complete move counts rank successful replays;
    no optimality is claimed. A failed replay remains a measured search failure.
    """
    from stowage.moves import apply_plan

    space = get_action_space("boxslot")
    best, best_cost = None, None
    # SlotCategory records are sorted for auditability, rather than necessarily
    # in crane order. Follow the actual carrier trace, or support/hatch topology
    # for manually supplied pre-stows that do not include that trace.
    slot_index = {slot.slot: i for i, slot in enumerate(prestow.slots)}
    traced = {slot_index[a.slot]: k for k, a in enumerate(prestow.actions)
              if isinstance(a, Place) and a.slot in slot_index}
    edges = _precedences(instance, prestow.slots)
    pending = set(range(len(prestow.slots)))
    order = []
    while pending:
        ready = [i for i in pending if not any(b == i and a in pending for a, b in edges)]
        if not ready:
            break
        selected = min(ready, key=lambda i: (traced.get(i, len(traced)+i), i))
        order.append(selected)
        pending.remove(selected)
    order.extend(sorted(pending))  # A cyclic/malformed pre-stow is rejected by the oracle.
    order_position = {i: k for k, i in enumerate(order)}
    metrics.update(terminal_method="two greedy category replays",
                   terminal_objective_model="complete measured weighted nonproductive moves",
                   terminal_fast_attempts=0, terminal_fast_failures=0,
                   terminal_category_substitutions=0,
                   carrier_approval_adjustments=0, carrier_approval_adjustment_limit=4)
    for policy in ("yard_first", "carrier_representatives"):
        if monotonic() >= deadline:
            break
        kwargs = {"config": feasibility_config} if feasibility_config is not None else {}
        engine = engine_factory(instance, space, **kwargs)
        ex = Expander(instance)
        remaining = {category: set(ids) for category, ids in categories.items()}
        actions, substitutions, adjustments = [], 0, 0
        adjusted_slots = list(prestow.slots)
        reserved_cells = {cell for slot in prestow.slots for cell in cells_of_slot(slot.slot)}
        metrics["terminal_fast_attempts"] += 1

        def yard_key(cid):
            location = ex.location(cid)
            if location is None or location[0] != "yard":
                return (0, cid)
            stack = ex.yard[location[1]]
            return (len(stack) - stack.index(cid) - 1, cid)

        try:
            for i in order:
                slot = adjusted_slots[i]
                if monotonic() >= deadline:
                    raise _TerminalDeadline
                ids = (sorted(remaining[slot.category], key=yard_key)
                       if policy == "yard_first" else [slot.representative_box])
                selected = None
                for cid in ids:
                    if monotonic() >= deadline:
                        raise _TerminalDeadline
                    action = Place(cid, slot.slot)
                    if engine.is_legal(action) and securing_geometry_violation(instance, ex, action) is None:
                        selected = action
                        break
                if selected is None:
                    # D73 carrier approval: retain the booked category, bay and
                    # tier, proposing at most four neighboring free rows. This
                    # is a bounded terminal search policy, never a rule bypass.
                    if adjustments < 4:
                        for target in _slot_adjustments(instance, slot.slot, reserved_cells):
                            proposed = list(adjusted_slots)
                            proposed[i] = replace(slot, slot=target)
                            if any(order_position[lower] >= order_position[upper]
                                   for lower, upper in _precedences(instance, proposed)):
                                continue
                            for cid in ids:
                                if monotonic() >= deadline:
                                    raise _TerminalDeadline
                                action = Place(cid, target)
                                if (engine.is_legal(action)
                                        and securing_geometry_violation(instance, ex, action) is None):
                                    selected = action
                                    adjusted_slots = proposed
                                    adjustments += 1
                                    reserved_cells.update(cells_of_slot(target))
                                    break
                            if selected is not None:
                                break
                    if selected is None:
                        raise ValueError("No legal same-category box or bounded slot adjustment")
                # A membership query can overrun; do not start a mutation after it.
                if monotonic() >= deadline:
                    raise _TerminalDeadline
                engine.push(selected)
                ex.step(selected)
                actions.append(selected)
                remaining[slot.category].remove(selected.box)
                substitutions += selected.box != slot.representative_box
                metrics["terminal_expansions"] += 1
            if not engine.is_complete() or securing_state_violations(instance, ex):
                raise ValueError("Greedy terminal replay is incomplete or violates securing geometry")
            plan = space.expand(instance, actions)
            replay = apply_plan(instance, plan)
            if not replay.legal:
                raise ValueError("Expanded greedy terminal candidate rejected by move engine")
            metrics["terminal_candidates"] += 1
            cost = replay.count.weighted_nonproductive
            if best_cost is None or cost < best_cost:
                best, best_cost = plan, cost
                metrics.update(objective=cost, terminal_fast_selected_policy=policy,
                               terminal_category_substitutions=substitutions,
                               carrier_approval_adjustments=adjustments)
        except _TerminalDeadline:
            metrics["terminal_fast_failures"] += 1
            break
        except ValueError as exc:
            metrics["terminal_fast_failures"] += 1
            metrics["terminal_fast_last_failure"] = str(exc)
    return best


def terminal_plan(instance, prestow, *, deadline, seed, engine_factory, feasibility_config=None):
    """Return ``(MovePlan | None, metrics, status, message)`` within a wall deadline.

    ``engine_factory(instance, space)`` supplies the frozen feasibility protocol;
    no implementation details of either the public engine are used.
    """
    # Lazy import avoids a module cycle while keeping a single category definition.
    from stowage.solver.twostep import category_of
    from stowage.moves import apply_plan

    metrics = {"num_workers": 1, "terminal_cp_solves": 0, "terminal_no_goods": 0,
               "terminal_expansions": 0, "terminal_max_legal_choices": 0,
               "terminal_candidates": 0, "fixed_slot_failure": 0,
               "terminal_objective_model": "original-yard first relocation surrogate"}
    metrics.update(carrier_approval_adjustments=0, carrier_approval_adjustment_limit=4)
    slots = prestow.slots
    n = len(slots)
    box_by_id = {b.id: b for b in instance.containers}
    categories = defaultdict(list)
    for cid in sorted(instance.load_list):
        categories[category_of(box_by_id[cid], instance)].append(cid)
    slot_categories = defaultdict(list)
    for i, slot in enumerate(slots):
        slot_categories[slot.category].append(i)
    if {k: len(v) for k, v in categories.items()} != {k: len(v) for k, v in slot_categories.items()}:
        metrics["fixed_slot_failure"] = 1
        metrics["terminal_status"] = "dead_end"
        return None, metrics, "dead_end", "Fixed-slot categories do not match the load manifest"

    if (instance.size_class in {"small", "medium"} and not rule_freeze_v1_enabled()
            and not instance.onboard.placements and not instance.discharge_list):
        fast_start = monotonic()
        candidate = _fast_terminal(instance, prestow, categories, deadline=deadline,
            engine_factory=engine_factory, feasibility_config=feasibility_config, metrics=metrics)
        metrics["terminal_fast_seconds"] = monotonic() - fast_start
        if candidate is not None:
            metrics["terminal_status"] = "ok"
            return candidate, metrics, "ok", None
        metrics["terminal_cp_fallback"] = 1
        metrics["terminal_method"] += " then CP-SAT"
        metrics["terminal_objective_model"] = "original-yard first relocation surrogate"
        if monotonic() >= deadline:
            metrics.update(terminal_status="timeout", fixed_slot_failure=1)
            return None, metrics, "timeout", "Greedy terminal replays exhausted their wall budget"

    model = cp_model.CpModel()
    rank = [model.new_int_var(0, max(0, n - 1), f"slot_order_{i}") for i in range(n)]
    if rank:
        model.add_all_different(rank)
    assignment = {}
    box_rank = {}
    for category, ids in categories.items():
        indices = slot_categories[category]
        for cid in ids:
            box_rank[cid] = model.new_int_var(0, n - 1, f"box_order_{cid}")
            row = []
            for i in indices:
                x = model.new_bool_var(f"assign_{cid}_{i}")
                assignment[cid, i] = x
                model.add(box_rank[cid] == rank[i]).only_enforce_if(x)
                row.append(x)
            model.add_exactly_one(row)
        for i in indices:
            model.add_exactly_one(assignment[cid, i] for cid in ids)
    for lower, upper in sorted(_precedences(instance, slots)):
        model.add(rank[lower] < rank[upper])
    penalties = []
    for stack in instance.yard.stacks:
        for at, cid in enumerate(stack.boxes):
            if cid not in box_rank:
                continue
            inversions = []
            for below in stack.boxes[:at]:
                if below not in box_rank:
                    continue
                inversion = model.new_bool_var(f"earlier_{below}_{cid}")
                model.add(box_rank[below] < box_rank[cid]).only_enforce_if(inversion)
                model.add(box_rank[below] > box_rank[cid]).only_enforce_if(inversion.negated())
                inversions.append(inversion)
            if inversions:
                moved = model.new_bool_var(f"relocated_{cid}")
                model.add_max_equality(moved, inversions)
                penalties.append(moved)
    model.minimize(sum(penalties))
    space = get_action_space("boxslot")
    original = {p.container_id: p.slot for p in instance.onboard.placements
                if p.container_id not in instance.discharge_list}
    best, best_cost = None, None
    order_literals = {}
    failure_message = "No legal terminal completion found within the fixed slot categories"
    cp_slice_s = 2.0

    def forbid_prefix(selected):
        literals = []
        for k, (cid, i) in enumerate(selected):
            literals.append(assignment[cid, i].negated())
            if (i, k) not in order_literals:
                equal = model.new_bool_var(f"slot_{i}_at_{k}")
                model.add(rank[i] == k).only_enforce_if(equal)
                model.add(rank[i] != k).only_enforce_if(equal.negated())
                order_literals[i, k] = equal
            literals.append(order_literals[i, k].negated())
        model.add_bool_or(literals)
        metrics["terminal_no_goods"] += 1

    while monotonic() < deadline:
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = int(seed) % 2147483647
        # Reserve time for oracle replay and no-good re-solves. CP optimizes its
        # surrogate during each slice; FEASIBLE does not claim a proof of optimum.
        solver.parameters.max_time_in_seconds = max(0.001, min(cp_slice_s, (deadline - monotonic()) * 0.5))
        status = solver.solve(model)
        metrics["terminal_cp_solves"] += 1
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if status == cp_model.INFEASIBLE:
                failure_message = "CP search exhausted fixed-slot assignments and loading orders"
            elif status == cp_model.UNKNOWN and monotonic() < deadline:
                cp_slice_s *= 2
                continue
            break
        cp_slice_s = 2.0
        selected = sorted(((cid, i) for (cid, i), x in assignment.items() if solver.value(x)),
                          key=lambda item: solver.value(rank[item[1]]))
        metrics["terminal_last_surrogate"] = solver.objective_value
        kwargs = {"config": feasibility_config} if feasibility_config is not None else {}
        engine = engine_factory(instance, space, **kwargs)
        ex = Expander(instance)
        actions = []
        adjustments = 0
        adjusted_slots = list(slots)
        order_position = {i: k for k, (_, i) in enumerate(selected)}
        reserved_cells = {cell for item in slots for cell in cells_of_slot(item.slot)}
        reserved_cells.update(cell for placement in instance.onboard.placements
                              if placement.container_id not in instance.discharge_list
                              for cell in cells_of_slot(placement.slot))
        legal = None

        def refresh():
            nonlocal legal
            legal = engine.legal_actions()
            metrics["terminal_max_legal_choices"] = max(metrics["terminal_max_legal_choices"], len(legal))
            return legal

        def push(action):
            nonlocal legal
            # Membership may itself consume the remainder of the budget. Never
            # start another oracle mutation or expansion after that call ends.
            if monotonic() >= deadline:
                raise _TerminalDeadline
            engine.push(action)
            ex.step(action)
            actions.append(action)
            legal = None
            metrics["terminal_expansions"] += 1

        def service_actions(desired=None):
            """Discharge first; restore original quay slots when required/possible."""
            for _ in range(len(original) + len(instance.discharge_list) + 1):
                if monotonic() >= deadline:
                    return False
                if callable(getattr(engine, "is_legal", None)):
                    # Avoid enumerating every manifest-box/vessel-slot pair.
                    pending_discharge = [Discharge(cid) for cid in sorted(instance.discharge_list)
                        if ex.location(cid) is not None and ex.location(cid)[0] == "ship"]
                    discharge = next((a for a in pending_discharge if engine.is_legal(a)), None)
                    if discharge is not None:
                        push(discharge)
                        continue
                    if desired is not None and not pending_discharge and engine.is_legal(desired):
                        return True
                    restore = [Place(cid, slot) for cid, slot in sorted(original.items())
                               if ex.location(cid) == ("quay", "quay")]
                    restoration = next((a for a in restore if engine.is_legal(a)), None)
                    if restoration is not None:
                        push(restoration)
                        continue
                    if not pending_discharge:
                        return desired is None
                current = refresh() if legal is None else legal
                discharge = sorted((a for a in current if isinstance(a, Discharge)), key=space.sort_key)
                if discharge:
                    push(discharge[0])
                    continue
                if desired is not None and desired in current:
                    return True
                restore = sorted((a for a in current if isinstance(a, Place)
                    and original.get(a.box) == a.slot and ex.location(a.box) == ("quay", "quay")),
                    key=space.sort_key)
                if restore:
                    push(restore[0])
                    continue
                return desired is None
            return False

        failed_at = None
        try:
            for k, (cid, i) in enumerate(selected):
                action = Place(cid, slots[i].slot)
                if not service_actions(action):
                    replacement = None
                    # D73 carrier approval belongs to both terminal paths,
                    # independently of the optional frozen-rule catalogue.
                    if adjustments < 4:
                        for target in _slot_adjustments(instance, action.slot, reserved_cells):
                            trial = Place(cid, target)
                            proposed_slots = list(adjusted_slots)
                            proposed_slots[i] = replace(slots[i], slot=target)
                            if any(order_position[lower] >= order_position[upper]
                                   for lower, upper in _precedences(instance, proposed_slots)):
                                continue
                            if service_actions(trial) and securing_geometry_violation(instance, ex, trial) is None:
                                replacement = trial
                                adjusted_slots = proposed_slots
                                break
                    if replacement is None:
                        failed_at = k
                        break
                    action = replacement
                    adjustments += 1
                    reserved_cells.update(cells_of_slot(action.slot))
                reason = securing_geometry_violation(instance, ex, action)
                if reason is not None:
                    metrics["securing_geometry_rejections"] = metrics.get("securing_geometry_rejections", 0) + 1
                    failure_message = reason
                    failed_at = k
                    break
                push(action)
            if failed_at is None and service_actions() and engine.is_complete():
                reasons = securing_state_violations(instance, ex)
                if reasons:
                    metrics["securing_geometry_rejections"] = metrics.get("securing_geometry_rejections", 0) + 1
                    failure_message = "; ".join(reasons)
                    forbid_prefix(selected)
                    continue
                plan = space.expand(instance, actions)
                replay = apply_plan(instance, plan)
                if replay.legal:
                    metrics["terminal_candidates"] += 1
                    moves = replay.count.weighted_nonproductive
                    soft = level_four_tiebreak(instance, replay.final_state.ship)
                    cost = (moves, *soft) if rule_freeze_v1_enabled() else (moves,)
                    if best_cost is None or cost < best_cost:
                        best, best_cost = plan, cost
                        metrics["objective"] = moves
                        metrics["carrier_approval_adjustments"] = adjustments
                        if rule_freeze_v1_enabled():
                            metrics.update(heavy_on_deck_count=soft[0], killed_slots=soft[1],
                                           objective_order=["moves", "heavy_on_deck_count", "killed_slots"])
                        metrics["terminal_surrogate"] = solver.objective_value
                else:
                    failure_message = "Expanded candidate rejected by the move engine"
            elif failed_at is None:
                failure_message = "Fixed-slot loads complete but discharge/quay completion unavailable"
            if failed_at is not None:
                # A rejected *fixed-slot* action need not be a global dead end.
                # Do not enumerate the entire vessel/manifest to diagnose it.
                metrics["terminal_failed_prefix_length"] = failed_at + 1
                metrics["terminal_dead_end_reason"] = "Selected fixed-slot prefix rejected by oracle"
                # The public API reports global dead ends, not the reason for
                # rejecting one action. Do not enumerate all ship actions here.
                metrics["terminal_dead_end_rules"] = []
        except _TerminalDeadline:
            break
        except ValueError as exc:
            failure_message = f"Fixed-slot replay rejected: {exc}"
        # For an exact failure prefix, changing future actions cannot rescue the
        # same physical state/action. If service/expansion failed, conservatively
        # exclude the entire candidate instead (never infer a stronger cut).
        forbid_prefix(selected[:failed_at + 1] if failed_at is not None else selected)
        if not n or metrics["terminal_candidates"] >= 8:
            break
    if best is not None:
        metrics["terminal_status"] = "ok"
        return best, metrics, "ok", None
    status = "timeout" if monotonic() >= deadline else "dead_end"
    metrics.update(terminal_status=status, fixed_slot_failure=1)
    return None, metrics, status, failure_message
