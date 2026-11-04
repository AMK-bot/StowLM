"""Bounded operational realization of final-stow/first-aboard witnesses.

Only this candidate generator is restricted: at most a few flagged cargo may
use temporary ship slots, followed by repeated Place actions. Failure to find
a realization is UNKNOWN, never a map infeasibility or global lift bound.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
from time import monotonic

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot
from stowage.model import MovePlan, Placement
from stowage.moves import apply_move, apply_plan, initial_state


@dataclass
class Repair:
    actions: tuple = ()
    plan: MovePlan | None = None
    replay_lifts: float | None = None
    status: str = "unknown"
    wall_time_s: float = 0.
    nodes: int = 0
    replayed_moves: int = 0
    flagged_boxes: list = field(default_factory=list)
    rejected: dict = field(default_factory=dict)
    family: str = "bounded_temporary_slot_realization_not_a_global_bound"
    full_checker_pass: bool = False


def _flags(current_order_flags):
    if current_order_flags is None:
        return set()
    if isinstance(current_order_flags, dict):
        return set(current_order_flags.get("ship_restowed", []))
    return set(current_order_flags)


def _temporary_domains(instance, assignment, flagged, limit, tables=None):
    from strengthening import safe_unary_mask
    from stowage.feasibility.tables import build_tables
    T = tables if tables is not None else build_tables(instance)
    mask, _ = safe_unary_mask(instance, T)
    final_cells = {cell for slot in assignment.values() for cell in cells_of_slot(slot)}
    domains = {}
    for bid in sorted(flagged):
        bi = T.box_index[bid]
        slots = [slot for si, slot in enumerate(T.slot_ids)
                 if mask[bi, si] and slot != assignment[bid]]
        slots.sort(key=lambda slot: (
            bool(final_cells.intersection(cells_of_slot(slot))),
            not bool(T.slot_deck[T.slot_index[slot]]),
            min(int(T.cell_k[ci]) for ci in T.slot_cells[T.slot_index[slot]] if ci >= 0),
            slot))
        domains[bid] = slots[:limit]
    return domains


def repair(instance, assignment, first_aboard_order, current_order_flags=None, *,
           budget_s=10., max_flagged=3, max_nodes=2000, max_replayed_moves=10000,
           max_temporary_slots=24, max_extra_places=None, tables=None):
    """Realize a witness with physical replay, then screen its departure state.

    First appearances obey the supplied permutation. A flagged box can load
    directly to its final slot or to a temporary unary-eligible ship slot.
    Between loads, DFS may finalize any already-loaded flagged box; Place from
    another ship slot creates a one-lift direct shift, and Place from the quay
    restores a box automatically restowed off during hatch/source clearing.
    Unflagged cargo cannot be restowed. Extra-place, node, move and time caps
    make this a deliberately incomplete repair search.

    Every generated lift passes the production apply_move physical rules.
    Departure static rules are checked only at the completed leaf, matching
    the checker's distinction between move legality and departure stowage.
    Ballast and independent full-check admission remain mandatory externally.
    """
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError("budget_s must be positive and finite")
    if any(value < 0 for value in (max_flagged, max_nodes, max_replayed_moves,
                                    max_temporary_slots)):
        raise ValueError("repair work limits must be nonnegative")
    started = monotonic()
    output = Repair()
    failures = Counter()
    order = list(first_aboard_order or [])
    required = set(instance.load_list)
    assignment = dict(assignment)
    flagged = _flags(current_order_flags)
    if (set(assignment) != required or len(order) != len(required)
            or set(order) != required or not flagged <= required):
        raise ValueError("repair requires a complete assignment/order and known flagged boxes")
    output.flagged_boxes = sorted(flagged)
    if len(flagged) > max_flagged:
        output.status = "unknown_flag_limit"
        output.wall_time_s = monotonic() - started
        return output
    if instance.onboard.placements or instance.yard.quay_buffer:
        output.status = "unsupported_nonempty_arrival"
        output.wall_time_s = monotonic() - started
        return output
    # Order cannot repair a forbidden final stow. Screen it once before DFS,
    # while retaining the independent completed-plan screen below.
    from stowage.checker.rules import check_static_rules
    final_ship = instance.onboard.model_copy(update={"placements": [
        Placement(container_id=bid, slot=slot) for bid, slot in assignment.items()],
        "covers_off": {}})
    static_hard = [violation for violation in check_static_rules(instance, final_ship,
        call_index=instance.call_index).violations if violation.severity == "hard"]
    if static_hard:
        output.status = "unknown_forbidden_final_stow"
        output.rejected = dict(Counter(f"departure:{v.rule_id}" for v in static_hard))
        output.wall_time_s = monotonic() - started
        return output
    if max_extra_places is None:
        max_extra_places = 2 * len(flagged) + 1
    if max_extra_places < 0:
        raise ValueError("max_extra_places must be nonnegative")
    domains = (_temporary_domains(instance, assignment, flagged,
               max_temporary_slots, tables=tables) if flagged else {})
    seen = set()
    stopped = False

    def exhausted():
        nonlocal stopped
        hit = (monotonic() - started >= budget_s or output.nodes >= max_nodes
               or output.replayed_moves >= max_replayed_moves)
        stopped = stopped or hit
        return hit

    def transition(ex, state, action):
        if exhausted():
            return None
        trial = ex.copy()
        try:
            added = trial.step(action)
            # An unflagged departure box may never leave a placement; the
            # supplied witness priced no current restow for that box.
            if any(move.kind.startswith("restow") and move.container_id not in flagged
                   for move in added):
                failures["unflagged_restow"] += 1
                return None
            successor = state
            for move in added:
                if exhausted():
                    return None
                output.replayed_moves += 1
                successor = apply_move(instance, successor, move)
        except (ValueError, KeyError, TypeError):
            failures["physical_transition"] += 1
            return None
        return trial, successor

    def finish(ex, actions):
        nonlocal stopped
        from stowage.checker.rules import check_static_rules
        if any(ex.where.get(bid) != ("ship", slot) for bid, slot in assignment.items()):
            failures["final_assignment"] += 1
            return False
        try:
            trial = ex.copy()
            trial.finish()
            # Expand the actual repeated-Place sequence again through the
            # public API; this is the MovePlan handed to the full finisher.
            plan = get_action_space("boxslot").expand(instance, actions)
            if output.replayed_moves + len(plan.moves) > max_replayed_moves:
                stopped = True
                return False
            output.replayed_moves += len(plan.moves)
            replay = apply_plan(instance, plan)
        except (ValueError, KeyError, TypeError):
            failures["complete_expansion"] += 1
            return False
        if not replay.legal:
            failures["complete_replay"] += 1
            return False
        final = {placement.container_id: placement.slot for placement in replay.final_state.ship.placements}
        if final != assignment:
            failures["final_assignment"] += 1
            return False
        hard = [v for v in check_static_rules(instance, replay.final_state.ship,
            call_index=instance.call_index).violations if v.severity == "hard"]
        if hard:
            failures.update(f"departure:{v.rule_id}" for v in hard)
            return False
        output.actions = tuple(actions)
        output.plan = plan
        output.replay_lifts = float(replay.count.weighted_nonproductive)
        output.status = "replay_and_static_legal_needs_ballast_and_full_checker"
        return True

    def dfs(ex, state, index, actions):
        if exhausted():
            return False
        output.nodes += 1
        extra = len(actions) - index
        key = (index, extra, tuple(sorted(ex.where.items())),
               tuple((key, tuple(boxes)) for key, boxes in sorted(ex.yard.items())),
               tuple(sorted(ex.covers_off.items())))
        if key in seen:
            return False
        seen.add(key)
        if index == len(order) and finish(ex, actions):
            return True
        # Final shifts are optional before the next first load: both branches
        # are searched so an early shift need not obstruct later hatch access.
        if extra < max_extra_places:
            for bid in order[:index]:
                if bid not in flagged or ex.where.get(bid) == ("ship", assignment[bid]):
                    continue
                action = Place(bid, assignment[bid])
                step = transition(ex, state, action)
                if step is not None and dfs(*step, index, actions + (action,)):
                    return True
        if index < len(order):
            bid = order[index]
            destinations = [assignment[bid]] + domains.get(bid, [])
            for slot in destinations:
                action = Place(bid, slot)
                step = transition(ex, state, action)
                if step is not None and dfs(*step, index + 1, actions + (action,)):
                    return True
        return False

    dfs(Expander(instance), initial_state(instance), 0, ())
    if output.plan is None:
        output.status = "unknown_work_limit" if stopped else "unknown_no_realization_in_neighbourhood"
    output.rejected = dict(failures)
    output.wall_time_s = monotonic() - started
    return output
