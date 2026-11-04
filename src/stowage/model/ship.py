"""ShipState (what is aboard, where) and WorldState (yard + ship + covers at a moment) — plan 1.1."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from stowage.conventions import cells_of_slot, parse_slot
from stowage.model.base import CallIndex, ContainerId, HatchCoverId, Kg, Slug, SlotId, StowModel
from stowage.model.yard import YardState


class Placement(StowModel):
    """One box on board: its BAPLIE slot (even bay for 40'/45', odd for 20'; see ``conventions.cells_of_slot``)."""

    slot: SlotId
    container_id: ContainerId


    @model_validator(mode="after")
    def validate_slot(self) -> Placement:
        parse_slot(self.slot)
        return self


class ShipState(StowModel):
    """Contents of the ship at a moment.

    Fields:
        vessel_id: the Vessel this state belongs to.
        placements: boxes aboard, sorted by slot (deterministic JSON). Invariants (1.1 validates; 2.3 enforces on
            every move): each physical cell holds at most one box; stacks are gap-free bottom-up; a box's length fits
            its cells (``Cell.accepts_*``); no 20' box rests on a 40' box (catalogue physical rule, verify).
        tank_masses_kg: content mass per tank id, kg (all tanks, ballast included). Missing tank = empty.
        covers_off: hatch covers currently lifted off → where each rests: ``"quay"`` or the id of the closed cover it
            sits on (a cover with another cover on it can't be lifted or opened). Must be empty at departure
            (2.3 rejects a plan that ends with a cover off).
    """

    vessel_id: Slug
    placements: list[Placement] = Field(default_factory=list)
    tank_masses_kg: dict[str, Kg] = Field(default_factory=dict)
    covers_off: dict[HatchCoverId, HatchCoverId | Literal["quay"]] = Field(default_factory=dict)


    @model_validator(mode="after")
    def validate_occupancy(self) -> ShipState:
        boxes = [placement.container_id for placement in self.placements]
        if len(boxes) != len(set(boxes)):
            raise ValueError("ship state contains a box in two places")
        occupied = set()
        for placement in self.placements:
            cells = set(cells_of_slot(placement.slot))
            if occupied & cells:
                raise ValueError(f"ship placements overlap physical cells at slot {placement.slot}")
            occupied.update(cells)
        for cover, location in self.covers_off.items():
            if location in self.covers_off:
                raise ValueError(f"lifted cover {cover} cannot rest on an open cover {location}")
        self.placements = sorted(self.placements, key=lambda placement: placement.slot)
        return self


class WorldState(StowModel):
    """Everything the move engine tracks: the yard, the ship, and which call we're at. Pure data; engines may use
    faster internal structures but must convert to/from this at their boundaries."""

    call_index: CallIndex
    yard: YardState
    ship: ShipState
