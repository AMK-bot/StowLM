"""Terminal box assignment and loading sequence with the carrier's slots frozen.

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

Every selected action passes the common frozen feasibility API, including actual
box mass and linear stability/strength margins. Failed prefixes get CP no-good
cuts (a clause forbidding precisely that assignment and order prefix), so a dead
end triggers backtracking without changing the carrier's slot categories. CP-SAT
uses one worker. Ballast and full checker certification remain the finisher's job.
Source for CP-SAT modelling: OR-Tools CP-SAT documentation, developers.google.com/
optimization/cp/cp_solver; industry two-stage process: docs/domain.md E10 and D14.
"""
from __future__ import annotations

from collections import defaultdict
from time import monotonic

from ortools.sat.python import cp_model

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot


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
    return edges


def terminal_plan(instance, prestow, *, deadline, seed, engine_factory):
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
    best, best_cost = None, float("inf")
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
        engine = engine_factory(instance, space)
        ex = Expander(instance)
        actions = []
        legal = None

        def refresh():
            nonlocal legal
            legal = engine.legal_actions()
            metrics["terminal_max_legal_choices"] = max(metrics["terminal_max_legal_choices"], len(legal))
            return legal

        def push(action):
            nonlocal legal
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
                    failed_at = k
                    break
                push(action)
            if failed_at is None and service_actions() and engine.is_complete():
                plan = space.expand(instance, actions)
                replay = apply_plan(instance, plan)
                if replay.legal:
                    metrics["terminal_candidates"] += 1
                    cost = replay.count.weighted_nonproductive
                    if cost < best_cost:
                        best, best_cost = plan, cost
                        metrics["objective"] = cost
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
