"""Counting lower bounds, with no invented order between interchangeable boxes.

A lower bound counts work that every legal solution must do. These are the
counting/partial-order relaxations described in docs/domain.md, 'For 3.6'.
Sources are given at each component; project derivations are identified as
such. Gate moves belong to the yard simulator, not a ship-call loading plan
(model.moves); allowing free gate-out/gate-in would invalidate yard bounds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Hashable, TypeVar

from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import Instance

Box = TypeVar("Box", bound=Hashable)


def grouped_blocking_bound(
    stacks: Iterable[Iterable[Box]], priorities: Mapping[Box, int | float],
) -> int:
    """LB1 for genuine retrieval groups (smaller priority leaves first).

    Source: Borjian et al. (2015), §2.2.1,
    https://arxiv.org/html/1505.04229v2; grouped extension: project derivation.
    Every counted box lies above a strictly earlier required group, so must
    relocate at least once. Count boxes, not inversions. Equality is harmless:
    same-group identities may leave in any order. The caller must establish
    that these group precedences hold in EVERY allowed assignment/order;
    sorting container IDs or PODs does not establish ship-loading precedence.
    Valid for unrestricted relocations; no restricted look-ahead is used.
    """
    blockers: set[Box] = set()
    for stack in stacks:
        earliest = float("inf")
        for box in stack:
            priority = priorities[box]
            if priority > earliest:
                blockers.add(box)
            earliest = min(earliest, priority)
    return len(blockers)


def cpmp_blocking_bound(
    stacks: Iterable[Iterable[Box]], priorities: Mapping[Box, int | float],
) -> int:
    """CPMP blocking count B for genuine grouped retrieval priorities.

    Source: Tanaka–Tierney (2018), §5,
    https://doi.org/10.1016/j.ejor.2017.05.046; docs/domain.md's CPMP bound.
    In pre-marshalling all boxes stay in the bay until sorting is complete.
    Each box above a strictly earlier group must leave its stack at least
    once; equality never requires a move. This is the same counting formula
    as grouped LB1, so take their maximum, never add them. Its use for a ship
    call still requires actual retrieval precedences, not an arbitrary order.
    """
    return grouped_blocking_bound(stacks, priorities)


def partial_order_blocking_bound(
    stacks: Iterable[Iterable[Box]], precedes: Iterable[tuple[Box, Box]],
) -> int:
    """Count blockers of guaranteed precedence pairs (earlier, later).

    Project derivation in docs/domain.md, based on the BRPSP partial-order
    model of Tanaka–Voß (2019), https://doi.org/10.1016/j.ejor.2019.06.014.
    A later box above a guaranteed earlier box must move out of the way.
    Count it once even if several pairs witness this fact. Pairs must hold
    in every feasible category assignment/order. Supply transitive pairs too
    if desired; omitting them only weakens the bound. No pairs means zero.
    """
    required = set(precedes)
    blockers: set[Box] = set()
    for stack in stacks:
        below: list[Box] = []
        for box in stack:
            if any((lower, box) in required for lower in below):
                blockers.add(box)
            below.append(box)
    return len(blockers)


def yard_rehandle_bound(instance: Instance) -> int:
    """Grouped LB1 relaxed to EVERY ship assignment and load order.

    Source/proof: grouped_blocking_bound. Required load boxes may leave in
    any order (priority 0); other yard boxes cannot productively load at this
    call (priority 1). Thus each non-load box above any required load must
    relocate. This remains true with unrestricted preparation, freely chosen
    ship slots, and interchangeable boxes. We deliberately impose no order
    among load categories, because the instance does not freeze such an order.
    Simulator gate moves are excluded from a ship-call plan (model.moves).
    Only actual yard stacks are inspected: D66 truck exports have no yard
    position and incur no dig-out. This proof uses neither E6 nor any weight,
    OOG, crane-height or stability placement restriction, so relaxing E6 or
    adding the rule-freeze restrictions cannot invalidate it.
    """
    required = set(instance.load_list)
    stacks = [stack.boxes for stack in instance.yard.stacks]
    priorities = {box: int(box not in required) for stack in stacks for box in stack}
    return grouped_blocking_bound(stacks, priorities)


def _mandatory_clearance(instance: Instance) -> tuple[set[str], set[str]]:
    """Access closure over physical cells, including spanning 40'/45' boxes.

    Accessing a hold blocker may itself need another cover opened. Iterate
    upward dependencies and cover clearance to a fixed point; sets prevent
    double-counting boxes that obstruct several targets/covers.
    """
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    covers = {cover.id: cover for cover in instance.vessel.hatch_covers}
    members: dict[str, list[tuple[int, str]]] = defaultdict(list)
    box_stacks: dict[str, set[str]] = defaultdict(set)
    for placement in instance.onboard.placements:
        for cell in cells_of_slot(placement.slot):
            sid = cells[cell].stack_id
            members[sid].append((parse_slot(cell).tier, placement.container_id))
            box_stacks[placement.container_id].add(sid)
    above: dict[str, set[str]] = defaultdict(set)
    for stack_members in members.values():
        ordered = sorted(stack_members)
        for (_, lower), (_, upper) in zip(ordered, ordered[1:]):
            above[lower].add(upper)
    needed = set(instance.discharge_list)
    opened: set[str] = set()
    pending = list(sorted(needed))
    while pending:
        box = pending.pop()
        additional = set(above.get(box, ()))
        for sid in box_stacks[box]:
            stack = stacks[sid]
            cover_id = stack.hatch_cover_id
            if not stack.on_deck and cover_id not in opened:
                opened.add(cover_id)
                for deck_sid in covers[cover_id].deck_stack_ids:
                    additional.update(cid for _, cid in members.get(deck_sid, ()))
        for other in sorted(additional - needed):
            needed.add(other)
            pending.append(other)
    # Planning replay requires current discharge-list membership AND matching
    # POD. A through box for a later repeated call must use a counted restow.
    discharge_list = set(instance.discharge_list)
    productive = {box.id for box in instance.containers
                  if box.id in discharge_list and box.pod == instance.port.locode}
    return needed - productive, opened


def current_restow_bound(instance: Instance) -> int:
    """One lift per unavoidable through-box in the current access closure.

    Project derivation in docs/domain.md: Avriel et al. (1998),
    https://doi.org/10.1023/A:1018956823693 (stack shifts), and van Twiller
    et al. (2023), §2, https://arxiv.org/html/2307.07573 (hatch overstows).
    Each through-box above a mandatory discharge, or obstructing a cover
    needed to reach it, must move at least once. Direct ship shifts ARE legal,
    so charging two lifts per box would be unsafe for this general API.
    This counts arrival cargo only. Stage A's empty arrival has no such
    blockers; blockers created during loading are not counted here.
    Direct-delivery imports still require the same ship access, even though
    their productive discharge ends at a truck. Union/closure counts each
    physical box once, including 40'/45' boxes occupying two stacks.
    Forced below-deck loading is counted separately by the cover component.
    """
    return len(_mandatory_clearance(instance)[0])


def _cover_capacities(instance: Instance) -> dict[str, int]:
    """Raw decision-cell capacities overstate usable space, a safe relaxation."""
    capacities = {cover.id: 0 for cover in instance.vessel.hatch_covers}
    for stack in instance.vessel.stacks:
        if not stack.on_deck:
            capacities[stack.hatch_cover_id] += len(stack.cell_ids)
    return capacities


def _hold_excess(instance: Instance, cargo: set[str]) -> int:
    boxes = {box.id: box for box in instance.containers}
    demand = sum(1 if boxes[cid].length_ft == 20 else 2 for cid in cargo)
    deck = sum(len(stack.cell_ids) for stack in instance.vessel.stacks if stack.on_deck)
    return max(0, demand - deck)


def _extra_covers(excess: int, capacities: Mapping[str, int], available: set[str]) -> int:
    """Fewest additional covers even if the largest holds are freely usable."""
    remaining = excess - sum(capacities.get(cid, 0) for cid in available)
    extra = 0
    for capacity in sorted((size for cid, size in capacities.items()
                            if cid not in available and size > 0), reverse=True):
        if remaining <= 0:
            break
        remaining -= capacity
        extra += 1
    return extra


def forced_hold_cover_bounds(instance: Instance) -> tuple[int, int]:
    """Current and future cover lifts forced by raw deck-capacity shortfall.

    Project pigeonhole proof: a 20' box demands one cell, a 40'/45' box two.
    Raw cells ignore height, crossing, weight, OOG and stability restrictions,
    so they overstate usable capacity under either activation setting.

    Loads exceeding deck capacity must enter holds now. Already-required or
    initially open covers are credited their full hold capacity before any
    additional closed covers are charged two lifts. This avoids counting a
    discharge opening again as a loading opening; every initially open cover
    still needs its single departure closure.

    Retained arrival cargo plus loads must fit at departure. If their demand
    exceeds deck capacity, some must remain in holds regardless of current
    shifts. Every used hold cover opens at least once at a later POD in D15's
    projection, at two lifts. Charging the fewest largest holds is a relaxation
    of any feasible placement, without assuming the arrival stow survives.
    Current and future lifts are distinct costs and can safely be added.
    """
    capacities = _cover_capacities(instance)
    _, required = _mandatory_clearance(instance)
    initially_off = set(instance.onboard.covers_off)
    current = 2 * len(required - initially_off) + len(initially_off)
    current += 2 * _extra_covers(
        _hold_excess(instance, set(instance.load_list)), capacities, required | initially_off,
    )
    departure = ({p.container_id for p in instance.onboard.placements}
                 - set(instance.discharge_list)) | set(instance.load_list)
    later_ports = {call.locode for call in instance.rotation.calls[instance.call_index + 1:]}
    future_targets = {box.id for box in instance.containers
                      if box.id in departure and box.pod in later_ports}
    future = 2 * _extra_covers(_hold_excess(instance, future_targets), capacities, set())
    return current, future


def current_hatch_cover_bound(instance: Instance) -> int:
    """Mandatory access plus forced-hold loading; initially open covers close once.

    Project derivation in docs/domain.md; round-trip lift convention:
    https://www.ppiaf.org/sites/ppiaf.org/files/documents/toolkits/Portoolkit/Toolkit/module4/full.html.
    Mandatory discharge access (including access to its clearance blockers)
    forces every closed cover in the closure off and back on. Every cover
    initially off must at least return before departure, whether or not it
    serves mandatory work. Forced-hold loads can require additional covers;
    capacities of already-counted covers are credited before adding any.
    Distinct physical covers are counted once.
    """
    return forced_hold_cover_bounds(instance)[0]


def future_restow_bound(instance: Instance) -> int:
    """Zero: free current restows/placements relax all future obstructions.

    Project derivation, docs/domain.md 'Safe combination': a projection on
    arrival stow is not a bound when the planner may change that stow now.
    Current clearance and future clearance can substitute, even with unequal
    weights, so they must not both be charged for the same assumed geometry.
    This relaxation drops future obstructions and is valid for every category
    assignment and every continuation. It is a bound, not a future estimate.
    """
    return 0


def future_hatch_cover_bound(instance: Instance) -> int:
    """Two lifts per hold cover forced by departure cargo exceeding raw deck space.

    Below-deck cargo may move to deck now when space suffices, so arrival-stow
    projections are not bounds. The pigeonhole count instead minimizes over
    all departure assignments: capacity shortfall forces some hold targets
    in every one. See forced_hold_cover_bounds for the counting proof.
    """
    return forced_hold_cover_bounds(instance)[1]
