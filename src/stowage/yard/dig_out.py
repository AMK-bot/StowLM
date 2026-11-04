"""Replaceable yard retrieval policy, expressed entirely as crane moves.

A blocking container is above the requested box and must first be lifted away.
This is the block relocation problem (Tanaka & Voss, 2019,
doi:10.1016/j.ejor.2019.06.014). The policy below supplies a reproducible baseline,
not an optimal solution: it picks the nearest legal stack and never trades a
physical constraint for a shorter distance. Distances use block origins and
nominal ISO container pitches; they are synthetic travel proxies.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Protocol

from stowage.conventions import parse_yard
from stowage.model import Instance
from stowage.model.moves import GateOut, Relocate
from stowage.model.port import PortProfile
from stowage.model.ship import WorldState
from stowage.model.yard import YardBlock
from stowage.moves import crossing_paths
from stowage.moves.engine import IllegalMoveError, apply_move
from stowage.ports.profiles import effective_yard_tiers
from stowage.rules.catalogue import rule_freeze_v1_enabled


class YardCapacityError(ValueError):
    """No legal storage/retrieval destination exists; never bypass the engine."""


def _yard_tiers(port: PortProfile, block: YardBlock | str) -> int:
    """Resolve the shared yard storage limit in the physical replay regime."""
    if not rule_freeze_v1_enabled() and port.max_crane_height_tiers is not None:
        port = port.model_copy(update={"max_crane_height_tiers": None})
    return effective_yard_tiers(port, block)


def location(state: WorldState, container_id: str) -> str | None:
    for stack in state.yard.stacks:
        if container_id in stack.boxes:
            return f"{stack.key}-{stack.boxes.index(container_id) + 1:02d}"
    return None


def destinations(instance: Instance, state: WorldState, container_id: str,
                 *, exclude: str | None = None) -> list[str]:
    """Enumerate storage candidates; the move engine remains the legality gate.

    Also respect the profile's explicit DG zoning and powered reefer rows, which
    are stowage restrictions outside the move engine's physical-access remit.
    APPROXIMATION: detailed yard DG pair segregation/OOG clearances are omitted;
    this synthetic placement policy does not certify those clearances. The
    departure checker must still certify every departure's full hard rules.
    """
    box = next(box for box in instance.containers if box.id == container_id)
    stacks = {stack.key: stack for stack in state.yard.stacks}
    footprint = {}
    for stack in state.yard.stacks:
        addr = parse_yard(stack.key + "-01")
        for offset in range(1 if stack.length_ft == 20 else 2):
            footprint[addr.block, addr.bay + offset, addr.row] = stack.key
    result = []
    width = 1 if box.length_ft == 20 else 2
    for block in instance.port.yard.blocks:
        if box.length_ft not in block.allowed_lengths_ft or (box.dg and not block.dg_block):
            continue
        for bay in range(1, block.n_bays - width + 2):
            for row in range(1, block.n_rows + 1):
                if box.reefer and row not in block.reefer_rows:
                    continue
                key = f"{block.id}-{bay:03d}-{row:02d}"
                stack = stacks.get(key)
                if key == exclude or (stack and stack.length_ft != box.length_ft):
                    continue
                if any(footprint.get((block.id, bay + off, row), key) != key for off in range(width)):
                    continue
                tier = len(stack.boxes) + 1 if stack else 1
                if tier <= _yard_tiers(instance.port, block):
                    result.append(f"{key}-{tier:02d}")
    return sorted(result)


DestinationPredicate = Callable[[WorldState, str, str], bool]


def preserves_export_separation(state: WorldState, container_id: str, destination: str,
                               *, export_owners: Mapping[str, str]) -> bool:
    """No import/foreign export may bury a still-present registered export.

    Keep unserved exports registered: failure to load does not make their yard
    space available for another call's box. Inspect every tier, since historical
    inventory can already have a foreign box above a pending export.
    """
    stack = next((stack for stack in state.yard.stacks
                  if stack.key == destination.rsplit("-", 1)[0]), None)
    return stack is None or all(
        below not in export_owners or export_owners.get(container_id) == export_owners[below]
        for below in stack.boxes)


def validate_relocation(instance: Instance, state: WorldState, move: Relocate,
                        *, destination_predicate: DestinationPredicate | None = None) -> None:
    """Check one hook lift against the current yard's storage restrictions.

    The caller must invoke this immediately before physical replay, then repeat
    with the updated world for the next move. A hook's returned state is not
    evidence that the relocation respected DG zoning or powered reefer rows.
    """
    if not isinstance(move, Relocate):
        raise YardCapacityError("expert.moves.top_access: retrieval hooks may only relocate yard boxes")
    if not any(box.id == move.container_id for box in instance.containers):
        raise YardCapacityError("expert.moves.top_access: hook relocation references an unknown container")
    if move.dst not in destinations(instance, state, move.container_id,
                                    exclude=move.src.rsplit("-", 1)[0]):
        raise YardCapacityError(
            "expert.moves.top_access: hook relocation destination violates yard storage restrictions")
    if destination_predicate is not None and not destination_predicate(
            state, move.container_id, move.dst):
        raise YardCapacityError(
            "expert.moves.top_access: zero-noise retrieval cannot bury a pending foreign export")


@dataclass(frozen=True)
class Retrieval:
    state: WorldState
    moves: tuple[Relocate, ...]
    source: str


class DigOutPolicy(Protocol):
    """Expose a requested box; do not remove it or mutate the input world.

    Implementations return only successfully engine-applied relocation moves
    that respect ``destinations`` at each evolving state,
    the resulting world and the box's accessible yard address. Raise
    YardCapacityError if there is no legal relocation. Stage C/solver 4.7 can
    replace this hook without changing event scheduling or snapshot semantics.
    """

    def retrieve(self, instance: Instance, state: WorldState, container_id: str) -> Retrieval: ...


class NearestStackDigOut:
    """APPROXIMATION: greedy nearest-stack dig-out, without future lookahead."""

    def __init__(self, *, destination_predicate: DestinationPredicate | None = None):
        self.destination_predicate = destination_predicate

    def retrieve(self, instance: Instance, state: WorldState, container_id: str) -> Retrieval:
        source = location(state, container_id)
        if source is None:
            raise YardCapacityError(f"expert.moves.top_access: {container_id} is absent from the yard")
        key = source.rsplit("-", 1)[0]
        moves = []
        # Load and gate-out share exactly the same yard-to-truck-lane leg.
        # The retrieval hook has no ship target; ship-side paths remain the
        # consuming load's responsibility. Geometry comes only from the engine.
        lane = set(crossing_paths(instance, GateOut(
            call_index=instance.call_index, container_id=container_id, src=source,
        )).yard_ground_slots)

        def footprint(stack):
            address = parse_yard(stack.key + "-01")
            return {(address.block, address.bay + offset, address.row)
                    for offset in range(1 if stack.length_ft == 20 else 2)}

        while True:
            stack = next(stack for stack in state.yard.stacks if stack.key == key)
            crossing_blockers = [candidate for candidate in state.yard.stacks
                if lane and lane.intersection(footprint(candidate))
                and len(candidate.boxes) >= _yard_tiers(
                    instance.port, parse_yard(candidate.key + "-01").block)]
            if not crossing_blockers and stack.boxes[-1] == container_id:
                return Retrieval(state, tuple(moves), location(state, container_id))
            # A far blocker may itself be screened by another full yard stack.
            # Try every crossing blocker through physical replay, rather than
            # inferring accessibility from lane direction or copied geometry.
            for stack in crossing_blockers or [stack]:
                blocker = stack.boxes[-1]
                src = f"{stack.key}-{len(stack.boxes):02d}"
                origin = parse_yard(src)
                blocks = {block.id: block for block in instance.port.yard.blocks}

                def distance(dst):
                    address = parse_yard(dst)
                    # APPROXIMATION: ISO pitches, no lanes or crane route model.
                    x = blocks[address.block].x_m + address.bay * 6.058
                    y = blocks[address.block].y_m + address.row * 2.438
                    ox = blocks[origin.block].x_m + origin.bay * 6.058
                    oy = blocks[origin.block].y_m + origin.row * 2.438
                    return ((x - ox) ** 2 + (y - oy) ** 2, dst)

                for dst in sorted(destinations(instance, state, blocker, exclude=stack.key), key=distance):
                    if self.destination_predicate is not None and not self.destination_predicate(
                            state, blocker, dst):
                        continue
                    address = parse_yard(dst)
                    # Keep the requested source exposed and never rebuild a
                    # lane obstruction while clearing another one. This makes
                    # the number of blockers decrease after each rehandle.
                    if crossing_blockers and dst.rsplit("-", 1)[0] == key:
                        continue
                    width = 1 if stack.length_ft == 20 else 2
                    if lane and address.tier >= _yard_tiers(instance.port, address.block) and any(
                        (address.block, address.bay + offset, address.row) in lane
                        for offset in range(width)
                    ):
                        continue
                    move = Relocate(call_index=instance.call_index, container_id=blocker, src=src, dst=dst)
                    try:
                        updated = apply_move(instance, state, move)
                    except IllegalMoveError:
                        continue
                    state = updated
                    moves.append(move)
                    break
                else:
                    continue
                break
            else:
                raise YardCapacityError("expert.moves.top_access: no legal blocker relocation destination")
