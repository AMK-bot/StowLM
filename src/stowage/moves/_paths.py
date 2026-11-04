"""Shared C6 crossing geometry (contract Addendum 1 A2).

This is the single path definition for replay, masks, expanders and solvers. Paths
describe stacks to screen against the *current* occupancy, never future occupancy.
Geometry alone does not certify top access, support, height or DG legality.
"""
from __future__ import annotations

from dataclasses import dataclass

from stowage.conventions import cells_of_slot, odd_bays_of, parse_slot, parse_yard
from stowage.model import Instance, Move, Violation
from stowage.model.port import PortProfile
from stowage.rules.catalogue import rule_freeze_v1_enabled


@dataclass(frozen=True)
class CrossingPath:
    """Physical deck stack ids and yard ground cells crossed by a single lift.

    Ship ids include non-decision ``fixed_load_stacks`` as well as decision
    stacks; fixed occupancy comes from ``fixed_loads[*].cargo_boxes[*].cell_ids``.
    Yard ground cells deliberately retain both halves of long footprints: callers
    resolve each cell to its occupying stack before testing its current height.
    """

    ship_stack_ids: tuple[str, ...] = ()
    yard_ground_slots: tuple[tuple[str, int, int], ...] = ()


class _CrossingGeometry:
    """Invocation-local indexes reused by a batch of pure path queries."""

    def __init__(self, instance: Instance, port: PortProfile):
        self.cells = {cell.id: cell for cell in instance.vessel.cells + instance.vessel.fixed_load_cells}
        self.stacks = {stack.id: stack for stack in instance.vessel.stacks + instance.vessel.fixed_load_stacks}
        self.deck_by_bay = {}
        for stack in self.stacks.values():
            if stack.on_deck and stack.cell_ids:
                self.deck_by_bay.setdefault(stack.bay, []).append(stack)
        self.ys = {stack.id: self.cells[stack.cell_ids[0]].tcg_m
                   for stack in self.stacks.values() if stack.cell_ids}
        self.covers = {}
        for cover in instance.vessel.hatch_covers + instance.vessel.fixed_load_hatch_covers:
            self.covers.setdefault(cover.id, []).append(cover)
        self.blocks = {block.id: block for block in port.yard.blocks}
        self.boxes = {box.id: box for box in instance.containers}


def crossing_paths(instance: Instance, move: Move, *, port: PortProfile | None = None) -> CrossingPath:
    """Return the binding C6/A2 crossing path, excluding every endpoint stack.

    The selected profile defaults to the current port. Future-call callers pass
    their resolved profile explicitly. Unknown H disables only ship paths;
    operational yard paths remain active under A8's independent yard limits.
    Consumers screen yard heights with ``ports.yard_blocking_tiers(port, block)``.
    Environment-only profiles and rule-freeze-v1 off return empty paths.
    Ship y coordinates are positive to port; neither numeric row order nor hold
    occupancy determines quay crossing. Output order is deterministic.
    Missing cover-edge geometry raises ``IllegalMoveError`` tagged
    ``expert.moves.hatch_clear``; callers must not interpret unknown reach as an
    empty, unobstructed path.
    """
    port = instance.port if port is None else port
    if not rule_freeze_v1_enabled() or port.profile_kind == "next_call_environment":
        return CrossingPath()
    return _indexed_crossing_paths(move, port=port, geometry=_CrossingGeometry(instance, port))


def _indexed_crossing_paths(move: Move, *, port: PortProfile, geometry: _CrossingGeometry) -> CrossingPath:
    """The shared A2 algorithm; callers gate once before preparing geometry."""
    if port.profile_kind == "next_call_environment":
        return CrossingPath()
    cells, stacks = geometry.cells, geometry.stacks
    # Every stack's cells share a transverse coordinate. Hold endpoints use their
    # own geometry, but only deck stacks are candidates for crossing obstructions.
    ys = geometry.ys
    ship_path: set[str] = set()
    ship_endpoints: set[tuple[int, int]] = set()

    def ship_leg(slot: str, other: str | None = None):
        target = parse_slot(slot)
        other_cells = cells_of_slot(other) if other is not None else ()
        for index, target_cell in enumerate(cells_of_slot(slot)):
            bay = parse_slot(target_cell).bay
            ship_endpoints.add((bay, target.row))
            target_y = cells[target_cell].tcg_m
            source_y = None if other is None else cells[other_cells[index]].tcg_m
            for stack in geometry.deck_by_bay.get(bay, ()):
                y = ys[stack.id]
                if source_y is not None:
                    crossed = min(source_y, target_y) < y < max(source_y, target_y)
                else:
                    crossed = y > target_y if port.berthing_side == "port" else y < target_y
                if crossed:
                    ship_path.add(stack.id)

    kind = move.kind
    ship_kind = kind if port.max_crane_height_tiers is not None else None
    if ship_kind == "restow_shift":
        source, target = parse_slot(move.src), parse_slot(move.dst)
        if source.bay == target.bay:
            ship_leg(move.dst, move.src)
            ship_endpoints.update((bay, source.row) for bay in odd_bays_of(source.bay))
        else:
            # APPROXIMATION: cranes do not gantry with a box over the ship;
            # some terminals do. D69/A2 models cross-bay shifts via the quay.
            ship_leg(move.src)
            ship_leg(move.dst)
    elif ship_kind in ("load", "restow_on"):
        ship_leg(move.dst)
    elif ship_kind in ("discharge", "restow_off"):
        ship_leg(move.src)
    elif ship_kind in ("hatch_cover_off", "hatch_cover_on"):
        def missing_cover_geometry(reason: str):
            # Import locally: engine's public exception also wraps path failures
            # for mask/solver callers that screen covers before physical replay.
            from stowage.moves.engine import IllegalMoveError

            raise IllegalMoveError(Violation(
                rule_id="expert.moves.hatch_clear", severity="hard", call_index=move.call_index,
                message=f"Hatch cover {move.cover_id} has insufficient geometry for quay crossing: {reason}.",
            ))

        covers = geometry.covers.get(move.cover_id, ())
        members = [stacks[sid] for cover in covers for sid in cover.deck_stack_ids + cover.hold_stack_ids]
        if not members:
            missing_cover_geometry("no member stacks define its edge")
        for bay in sorted({stack.bay for stack in members}):
            # Covers land on the quay (A2), so their nearest edge defines reach.
            # A model Stack may legally contain no cells: prefer nonempty deck
            # geometry, and use nonempty hold geometry only when deck is absent.
            candidates = [stack for stack in members if stack.bay == bay and stack.on_deck and stack.cell_ids]
            candidates = candidates or [stack for stack in members if stack.bay == bay and stack.cell_ids]
            if not candidates:
                missing_cover_geometry(f"bay {bay:03d} has no nonempty deck or hold stack")
            member = (max if port.berthing_side == "port" else min)(candidates, key=lambda stack: ys[stack.id])
            ship_leg(member.cell_ids[0])
        ship_endpoints.update((stack.bay, stack.row) for stack in members)
    ship_path = {sid for sid in ship_path if (stacks[sid].bay, stacks[sid].row) not in ship_endpoints}

    # A ship-side truck has no yard endpoint. Preserve its ship crossing path,
    # without requiring yard geometry (also true for ship/quay-only lifts).
    yard_lift = (kind in ("relocate", "gate_in", "gate_out")
                 or (kind == "load" and move.src != "truck")
                 or (kind == "discharge" and move.dst != "truck"))
    if not yard_lift:
        return CrossingPath(tuple(sorted(ship_path)))

    # APPROXIMATION: a stack at its effective yard limit blocks the machine,
    # stricter than equipment that passes over its own maximum stack height.
    # D62/A8 user rule, expert Q25: this yard limit is independent of quay H.
    yard_path: set[tuple[str, int, int]] = set()
    yard_endpoints: set[tuple[str, int, int]] = set()
    blocks = geometry.blocks
    box = geometry.boxes.get(getattr(move, "container_id", None))
    width = 1 if box is None or box.length_ft == 20 else 2

    def endpoint(address):
        addr = parse_yard(address)
        yard_endpoints.update((addr.block, addr.bay + k, addr.row) for k in range(width))
        return addr

    def yard_lane(addr):
        block = blocks[addr.block]
        rows = range(1, addr.row) if block.truck_lane_side == "low" else range(addr.row + 1, block.n_rows + 1)
        yard_path.update((addr.block, addr.bay + k, row) for k in range(width) for row in rows)

    if kind == "relocate":
        source, target = endpoint(move.src), endpoint(move.dst)
        if source.block != target.block:
            yard_lane(source)
            yard_lane(target)
        else:
            # APPROXIMATION: C6 gantry path first traverses rows in the source
            # bay, then intervening bays in the target row; long lifts use the
            # union of the two parallel 20-foot footprint paths (A2).
            for k in range(width):
                yard_path.update((source.block, source.bay + k, row)
                                 for row in range(min(source.row, target.row) + 1, max(source.row, target.row)))
                yard_path.update((source.block, bay, target.row)
                                 for bay in range(min(source.bay + k, target.bay + k) + 1,
                                                  max(source.bay + k, target.bay + k)))
    elif kind in ("load", "gate_out") and move.src != "truck":
        yard_lane(endpoint(move.src))
    elif kind in ("discharge", "gate_in") and move.dst != "truck":
        yard_lane(endpoint(move.dst))
    yard_path.difference_update(yard_endpoints)
    return CrossingPath(tuple(sorted(ship_path)), tuple(sorted(yard_path)))
