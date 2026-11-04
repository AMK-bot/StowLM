"""Abstract later-port lift counts for D15's fixed departure-stow scenario.

An overstow is a box that must be moved to reach a box beneath it. A hatch
cover also forces every box resting on its deck stacks to move before cargo
in its hold can be reached. The source of this counting model is decision D15
and the frozen ``stowage.moves.engine`` interface (plan task 2.3).

ASSUMPTION: no future loads are introduced. This is the departure-stow scenario
from D15, rather than a full-rotation operational forecast.
ASSUMPTION: each blocking box costs the configurable Q1 number of restow lifts
(default two), and each opened cover costs two lifts, as specified by D15.
APPROXIMATION: surviving boxes retain their departure coordinates for later
dependency counts. "Return to original positions" is an abstract counting
convention, not a physically executable policy: discharging a tier-02 support
can make returning a blocker to tier 04 illegal. No future actions, supported
replacement positions, or regulatory feasibility are constructed or certified.
Consequently these counts do not establish a minimum over arbitrary executable
future plans; callers requiring one must validate an explicit future policy.

With rule-freeze-v1 active, crossing blockers join the same abstract off/on
closure: enough top boxes are removed to bring each crossed stack below H.
This deliberately retains D15's configured off/on cost, rather than inventing
an unsupported shift destination and calling a one-lift shift executable.
Required future lifts also screen the resolved call's height and refusal rules.
Unknown environment-only crane limits are skipped (contract A3).
Fixed background cargo cannot join the restow count: a blocked crossing or
required hatch-cover removal raises a hard violation instead.
Direct-delivery nonproductive blockers also cannot use D15's off/on policy:
that scenario is explicitly unsupported. No legal future ship-shift policy
is constructed or certified; productive truck discharges remain allowed.

Physical cells, rather than reported BAPLIE slots, establish dependencies:
a 40'/45' box connects two stacks, so reaching a box below one half can also
require clearing boxes above the other half. Upward dependency closure finds
these indirect blockers and a set counts each blocking box only once.
"""

from __future__ import annotations

from collections import defaultdict

from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import FutureMoves, Instance, ShipState, Violation
from stowage.model.moves import Discharge, HatchCoverOff
from stowage.moves._paths import crossing_paths
from stowage.moves._port_rules import profile_resolution_violation, refused_primary_class
from stowage.ports import crane_height_at_call, resolve_port_call_profile
from stowage.rules.catalogue import rule_freeze_v1_enabled


def project(instance: Instance, ship: ShipState, *, from_call: int) -> list[FutureMoves]:
    """Project each later call without mutating the departure state or cargo.

    Each box resolves to the first subsequent call matching its POD; this
    matters when the rotation visits the same port more than once. Dependencies
    are rebuilt from the remaining boxes at each call because a discharged
    spanning box must no longer connect its two former stacks.

    APPROXIMATION: rebuilding removes discharged boxes but leaves every survivor
    at its departure coordinates, even if support is now absent. The returned
    counts preserve D15's scenario and do not certify future physical replay.
    """
    calls = instance.rotation.calls
    if not 0 <= from_call < len(calls):
        raise ValueError("from_call is outside rotation")

    containers = {box.id: box for box in instance.containers}
    cells = {cell.id: cell for cell in instance.vessel.cells + instance.vessel.fixed_load_cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks + instance.vessel.fixed_load_stacks}
    covers = {cover.id: cover for cover in instance.vessel.hatch_covers}
    fixed_occupied = {cell_id for load in instance.vessel.fixed_loads
                      for box in load.cargo_boxes or () for cell_id in box.cell_ids}
    fixed_heights = {
        stack.id: max((index + 1 for index, cell_id in enumerate(stack.cell_ids)
                       if cell_id in fixed_occupied), default=0)
        for stack in instance.vessel.fixed_load_stacks if stack.on_deck
    }
    placements = {placement.container_id: placement.slot for placement in ship.placements}
    freeze = rule_freeze_v1_enabled()
    first_call: dict[str, int] = {}
    for index in range(from_call + 1, len(calls)):
        first_call.setdefault(calls[index].locode, index)

    discharges: dict[int, set[str]] = defaultdict(set)
    stack_boxes: dict[str, list[tuple[int, str]]] = defaultdict(list)
    box_stacks: dict[str, list[str]] = {}
    active = {placement.container_id for placement in ship.placements}
    for placement in ship.placements:
        cid = placement.container_id
        destination = first_call.get(containers[cid].pod)
        if destination is not None:
            discharges[destination].add(cid)
        occupied_stacks = [cells[cell].stack_id for cell in cells_of_slot(placement.slot)]
        box_stacks[cid] = occupied_stacks
        tier = parse_slot(placement.slot).tier
        for sid in occupied_stacks:
            stack_boxes[sid].append((tier, cid))
    for members in stack_boxes.values():
        members.sort()

    projected = []
    lifts_per_box = instance.objective.future_restow_lifts_per_box
    for call_index in range(from_call + 1, len(calls)):
        targets = discharges[call_index] & active
        if not targets:
            projected.append(FutureMoves(call_index=call_index))
            continue

        # Adjacent occupied cells suffice: transitive closure reaches every
        # higher box, including boxes on the far half of a spanning blocker.
        above: dict[str, set[str]] = defaultdict(set)
        remaining: dict[str, list[str]] = {}
        for sid, members in stack_boxes.items():
            ordered = [cid for _, cid in members if cid in active]
            remaining[sid] = ordered
            for lower, upper in zip(ordered, ordered[1:]):
                above[lower].add(upper)

        opened = {
            stacks[sid].hatch_cover_id
            for cid in targets
            for sid in box_stacks[cid]
            if not stacks[sid].on_deck
        }
        required = set(targets)
        for cover_id in opened:
            for sid in covers[cover_id].deck_stack_ids:
                required.update(remaining.get(sid, ()))

        pending = list(required)
        while pending:
            cid = pending.pop()
            for blocker in above.get(cid, ()):
                if blocker not in required:
                    required.add(blocker)
                    pending.append(blocker)

        if freeze:
            from stowage.moves.engine import IllegalMoveError

            try:
                profile = resolve_port_call_profile(calls[call_index], current_port=instance.port)
                height = crane_height_at_call(calls[call_index], current_port=instance.port)
            except (ValueError, OSError) as error:
                raise IllegalMoveError(profile_resolution_violation(
                    calls[call_index], error, containers=sorted(targets))) from error
            fixed_covers = {cover.id: cover for cover in instance.vessel.fixed_load_hatch_covers}
            for cover_id in sorted(opened):
                fixed_cover = fixed_covers.get(cover_id)
                fixed_deck = [] if fixed_cover is None else fixed_cover.deck_stack_ids
                # Membership may be split across decision/fixed cover catalogs.
                # Every fixed stack physically carried by this cover counts.
                fixed_deck = set(fixed_deck) | {
                    stack.id for stack in instance.vessel.fixed_load_stacks
                    if stack.on_deck and stack.hatch_cover_id == cover_id
                }
                if any(fixed_heights.get(sid, 0) for sid in fixed_deck):
                    raise IllegalMoveError(Violation(
                        rule_id="expert.moves.hatch_clear", severity="hard", call_index=call_index,
                        message="Fixed background deck cargo cannot be restowed to open a required hatch cover."))
            if height is not None:
                # APPROXIMATION: legacy calls with an explicit H but no resolvable
                # profile use the model's synthetic port-side berthing default.
                geometry = profile if profile is not None else instance.port.model_copy(
                    update={"berthing_side": "port"})
                geometry = geometry.model_copy(update={"max_crane_height_tiers": height})
                cover_paths = [crossing_paths(instance, HatchCoverOff(
                    call_index=call_index, cover_id=cover_id), port=geometry)
                    for cover_id in sorted(opened)]
                # Monotone closure: first account for top access, then crossing
                # access to every cargo and cover lift. Already-required boxes
                # can leave first, so they never add a second restow charge.
                while True:
                    expanded = set(required)
                    for cid in required:
                        expanded.update(above.get(cid, ()))
                    paths = cover_paths + [crossing_paths(instance, Discharge(
                        call_index=call_index, container_id=cid, src=placements[cid], dst="truck"),
                        port=geometry) for cid in sorted(required)]
                    for path in paths:
                        for sid in path.ship_stack_ids:
                            stack = stacks[sid]
                            if fixed_heights.get(sid, 0) >= height:
                                raise IllegalMoveError(Violation(
                                    rule_id="port.e3.quay_crossing", severity="hard", call_index=call_index,
                                    measured=fixed_heights[sid], limit=height, unit="tiers",
                                    message="Unmovable fixed background cargo blocks a required later-call crane path."))
                            for cid in remaining.get(sid, ()):
                                if cid in required:
                                    continue
                                occupied = next(cell for cell in cells_of_slot(placements[cid])
                                                if cells[cell].stack_id == sid)
                                if stack.cell_ids.index(occupied) + 1 >= height:
                                    expanded.add(cid)
                    if expanded == required:
                        break
                    required = expanded

            # No restriction on a refused box merely remaining aboard. Refusal
            # applies only when this scenario requires discharge or restow (A4).
            for cid in sorted(required):
                box = containers[cid]
                if profile is not None and refused_primary_class(
                    profile, box.dg.imdg_class if box.dg else None
                ):
                    raise IllegalMoveError(Violation(
                        rule_id="port.dg.refused_class", severity="hard", call_index=call_index,
                        containers=[cid], slots=[placements[cid]],
                        message="A required later-call discharge or restow has a refused primary DG class."))
                if cid not in targets and profile is not None and profile.requires_direct_delivery(
                    box.dg.imdg_class if box.dg else None
                ):
                    # The marker belongs to the current call; future direct
                    # delivery comes only from this call's resolved policy.
                    # Do not price prohibited off/on buffering or silently
                    # assert that an unconstructed permanent shift is legal.
                    raise IllegalMoveError(Violation(
                        rule_id="port.dg.direct_delivery", severity="hard", call_index=call_index,
                        containers=[cid], slots=[placements[cid]],
                        message="D15's off/on projection is unsupported for a direct-delivery blocker; "
                                "no legal future ship-shift policy is constructed or certified."))
                if height is not None:
                    for cell_id in cells_of_slot(placements[cid]):
                        stack = stacks[cells[cell_id].stack_id]
                        tier = stack.cell_ids.index(cell_id) + 1
                        if stack.on_deck and tier > height:
                            raise IllegalMoveError(Violation(
                                rule_id="port.e3.quay_height", severity="hard", call_index=call_index,
                                containers=[cid], slots=[placements[cid]], measured=tier, limit=height,
                                unit="tiers", message="The later-call crane cannot reach a required deck lift."))

        restowed = len(required - targets)
        projected.append(
            FutureMoves(
                call_index=call_index,
                restowed_boxes=restowed,
                restow_lifts=restowed * lifts_per_box,
                hatch_cover_lifts=2 * len(opened),
            )
        )
        # APPROXIMATION: preserve surviving coordinates under D15's abstract
        # fixed-slot convention; no unsupported return is physically replayed.
        active.difference_update(targets)

    return projected
