"""Exhaustive tiny-instance oracle for the D11 level-3 move objective.

Dijkstra's algorithm (Dijkstra, 1959, *A note on two problems in connexion
with graphs*) visits physical world states in increasing cost of lifts already
made. A completed departure has an additional terminal cost from the move
engine's later-port projection. We keep searching after the first departure:
one more shift now can save several projected lifts later.

ASSUMPTION: the planning action space excludes simulator-only gate_in/gate_out.
Allowing a yard blocker to leave through the gate for free changes the problem.
All eight planning move kinds, every geometric destination, and every box
identity are enumerated; the move engine alone decides physical legality.
No fixed retrieval or same-category order is imposed.
With rule freeze v1 active, productive direct-delivery lifts use a truck
endpoint and never require a yard position. Direct shifts still cost one lift;
off/on restows cost two. The action graph includes shifts, so its optimum is a
relaxation of D69's Stage A planners that only clear ship blockers via off/on.

The default omits departure stowage rules, providing an exact *relaxed* optimum
that is no larger than the rule-constrained optimum. Set check_static_rules=True
to check the implemented 2.4 rules at departure. Neither mode checks ballast,
stability, strength, visibility or lashing, and returned plans are oracle
witnesses, never checker-approved operational plans. Search caps return 'limit',
never an unproved exact result. Intended only for a few boxes and cells.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
import math
from time import perf_counter
from typing import Iterator, Literal

from stowage.conventions import format_slot, parse_slot
from stowage.model import Instance, Move, MovePlan
from stowage.model.moves import (
    Discharge, HatchCoverOff, HatchCoverOn, Load, Relocate,
    RestowOff, RestowOn, RestowShift,
)
from stowage.model.ship import WorldState
from stowage.moves.engine import IllegalMoveError, apply_move, initial_state, project_future_moves
from stowage.rules.catalogue import rule_freeze_v1_enabled


@dataclass(frozen=True)
class BruteForceResult:
    """Only status='optimal' certifies objective; on 'limit' it is an incumbent.

    status='infeasible' means the entire relaxed state graph was exhausted.
    expanded_nodes counts popped, non-stale states; wall_time_s is measured.
    """

    status: Literal["optimal", "infeasible", "limit"]
    objective: float | None
    plan: MovePlan | None
    expanded_nodes: int
    wall_time_s: float


def _slots(instance: Instance) -> dict[int, tuple[str, ...]]:
    cells = {cell.id for cell in instance.vessel.cells}
    twenty = tuple(sorted(cells))
    forty = []
    for cell in twenty:
        p = parse_slot(cell)
        if p.bay < 998 and format_slot(p.bay + 2, p.row, p.tier) in cells:
            forty.append(format_slot(p.bay + 1, p.row, p.tier))
    return {20: twenty, 40: tuple(forty), 45: tuple(forty)}


def _candidates(instance: Instance, state: WorldState, slots, keys) -> Iterator[Move]:
    call = instance.call_index
    boxes = {box.id: box for box in instance.containers}
    load_list = set(instance.load_list)
    stacks = {stack.key: stack for stack in state.yard.stacks}
    direct = ({cid for cid, box in boxes.items() if box.direct_delivery}
              if rule_freeze_v1_enabled() else set())

    def yard_destinations():
        for key in keys:
            tier = len(stacks[key].boxes) + 1 if key in stacks else 1
            if tier <= 99:
                yield key, f"{key}-{tier:02d}"

    for stack in state.yard.stacks:
        cid = stack.boxes[-1]
        src = f"{stack.key}-{len(stack.boxes):02d}"
        for key, dst in yard_destinations():
            if key != stack.key:
                yield Relocate(call_index=call, container_id=cid, src=src, dst=dst)
        if cid in load_list:
            for dst in slots[boxes[cid].length_ft]:
                yield Load(call_index=call, container_id=cid, src=src, dst=dst)
    aboard = {placement.container_id for placement in state.ship.placements}
    for cid in sorted((load_list & direct) - aboard):
        for dst in slots[boxes[cid].length_ft]:
            yield Load(call_index=call, container_id=cid, src="truck", dst=dst)
    for placement in state.ship.placements:
        cid, src = placement.container_id, placement.slot
        yield RestowOff(call_index=call, container_id=cid, src=src)
        for dst in slots[boxes[cid].length_ft]:
            if dst != src:
                yield RestowShift(call_index=call, container_id=cid, src=src, dst=dst)
        if boxes[cid].pod == instance.port.locode:
            if cid in direct:
                yield Discharge(call_index=call, container_id=cid, src=src, dst="truck")
            else:
                for _, dst in yard_destinations():
                    yield Discharge(call_index=call, container_id=cid, src=src, dst=dst)
    for cid in state.yard.quay_buffer:
        for dst in slots[boxes[cid].length_ft]:
            yield RestowOn(call_index=call, container_id=cid, dst=dst)
    covers = tuple(cover.id for cover in instance.vessel.hatch_covers)
    for cover in covers:
        if cover in state.ship.covers_off:
            yield HatchCoverOn(call_index=call, cover_id=cover)
        else:
            for dst in ("quay", *covers):
                if dst != cover:
                    yield HatchCoverOff(call_index=call, cover_id=cover, dst=dst)


def brute_force_optimum(
    instance: Instance, *, max_nodes: int = 10_000, time_limit_s: float | None = 5.0,
    check_static_rules: bool = False,
) -> BruteForceResult:
    """Exactly minimize current nonproductive lifts plus projected future lifts.

    Caps bound work rather than silently limiting the set of legal moves.
    ``time_limit_s=None`` disables the wall cap for deterministic node-bounded
    correctness checks; the default retains its five-second safety cap. Weights
    must be finite and nonnegative (Dijkstra requires nonnegative edge costs).
    Productive moves cost zero; cycles are eliminated by best-cost state labels.
    Stage-D joint forecasts are unsupported: this is the one-call stage-A/B
    objective with the move engine's no-new-loads future projection.
    """
    if instance.plan_calls or instance.forecasts:
        raise ValueError("the tiny oracle supports one call without forecast loads")
    if max_nodes < 1 or (time_limit_s is not None
                         and (not math.isfinite(time_limit_s) or time_limit_s <= 0)):
        raise ValueError("search limits must be positive and finite")
    w = instance.objective
    weights = (w.relocate, w.restow, w.hatch_cover, w.future_restow, w.future_hatch_cover)
    if any(not math.isfinite(value) or value < 0 for value in weights):
        raise ValueError("the tiny oracle requires finite nonnegative objective weights")
    costs = {"relocate": w.relocate, "load": 0.0, "discharge": 0.0,
             "restow_shift": w.restow, "restow_off": w.restow, "restow_on": w.restow,
             "hatch_cover_off": w.hatch_cover, "hatch_cover_on": w.hatch_cover}
    started = perf_counter()
    slots = _slots(instance)
    keys = tuple(f"{block.id}-{bay:03d}-{row:02d}"
                 for block in instance.port.yard.blocks
                 for bay in range(1, block.n_bays + 1)
                 for row in range(1, block.n_rows + 1))

    def key(state):
        # Quay order affects no legal move: all buffered boxes are independently accessible.
        # D62 crossing depends on this geometry, not a move-history obligation.
        # D69's planner sequencing screen is deliberately not imposed here:
        # this exhaustive action graph also allows corrective direct shifts.
        return (tuple((s.key, s.length_ft, tuple(s.boxes)) for s in state.yard.stacks),
                tuple(sorted(state.yard.quay_buffer)),
                tuple((p.slot, p.container_id) for p in state.ship.placements),
                tuple(sorted(state.ship.covers_off.items())))

    initial = initial_state(instance)
    initial_key = key(initial)
    best = {initial_key: 0.0}
    parents = {initial_key: None}
    queue = [(0.0, 0, initial_key, initial)]
    serial = count(1)
    incumbent = math.inf
    goal_key = None
    expanded = 0

    def result(status):
        moves = []
        cursor = goal_key
        while cursor is not None and parents[cursor] is not None:
            cursor, move = parents[cursor]
            moves.append(move)
        plan = None if goal_key is None else MovePlan(
            id=f"{instance.id}.tiny-oracle", instance_id=instance.id,
            call_index=instance.call_index, moves=list(reversed(moves)),
            provenance=instance.provenance.model_copy(deep=True))
        return BruteForceResult(status, None if goal_key is None else incumbent,
                                plan, expanded, perf_counter() - started)

    while queue:
        cost, _, state_key, state = heappop(queue)
        if cost != best[state_key]:
            continue
        if cost >= incumbent:
            return result("optimal")
        if expanded >= max_nodes or (time_limit_s is not None
                                     and perf_counter() - started >= time_limit_s):
            return result("limit")
        expanded += 1
        aboard = {placement.container_id for placement in state.ship.placements}
        complete = (set(instance.load_list) <= aboard
                    and not (set(instance.discharge_list) & aboard)
                    and not state.yard.quay_buffer and not state.ship.covers_off)
        if complete and check_static_rules:
            from stowage.checker.rules import check_static_rules as check_rules

            report = check_rules(instance, state.ship, call_index=instance.call_index)
            complete = report.ran and not any(v.severity == "hard" for v in report.violations)
        if complete:
            try:
                future = project_future_moves(instance, state.ship, from_call=instance.call_index)
            except IllegalMoveError:
                # Activated later-call rules can invalidate this departure's
                # D15 scenario. Another current shift may repair the stow;
                # reject only this terminal candidate and keep searching.
                pass
            else:
                total = cost + sum(w.future_restow * f.restow_lifts
                                   + w.future_hatch_cover * f.hatch_cover_lifts for f in future)
                if total < incumbent:
                    incumbent, goal_key = total, state_key
                if cost >= incumbent:
                    continue
        for move in _candidates(instance, state, slots, keys):
            next_cost = cost + costs[move.kind]
            if next_cost >= incumbent:
                continue
            try:
                child = apply_move(instance, state, move)
            except IllegalMoveError:
                continue
            child_key = key(child)
            if next_cost >= best.get(child_key, math.inf):
                continue
            best[child_key] = next_cost
            parents[child_key] = (state_key, move)
            heappush(queue, (next_cost, next(serial), child_key, child))
    return result("optimal" if goal_key is not None else "infeasible")
