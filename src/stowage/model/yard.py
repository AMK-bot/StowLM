"""Yard layout (static) and yard state (which box is where) — plan 1.1; built by 2.6, evolved by 3.4 and 2.3.

Addressing: ``BLOCK-BBB-RR-TT`` (``stowage.conventions.YardAddress``). A yard *stack* is one ground slot
``BLOCK-BBB-RR``; boxes in it are listed bottom → top. Only the top box of a stack is reachable (catalogue
``moves.*`` rule, enforced by 2.3). A 40'/45' stack occupies ground bays ``bay`` and ``bay+1`` (addressed by
``bay``; a 45' overhangs within the same 2 ground bays — assumption); a 20' stack must not overlap a 40' footprint
and stacks are single-length (assumption, conventions).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from stowage.conventions import parse_yard
from stowage.model.base import ContainerId, Metres, StowModel, YardStackKey


class YardBlock(StowModel):
    """One yard block.

    Fields:
        id: block id (``[A-Z0-9]{1,4}``). n_bays: ground bays (20' positions) along the block, numbered 1..n_bays.
        n_rows: rows across, 1..n_rows. max_tiers: stack-height limit of the equipment serving it (E3, "1-over-N"
            → N), the move engine rejects a move that would exceed it. Port-level overrides (PortProfile.yard_max_tiers)
            take the minimum.
        equipment_id: id of the PortProfile.equipment entry serving the block (``Equipment.id``, e.g. ``"rtg"``).
        truck_lane_side: truck lane at the low-row (row 1) or high-row (row n_rows) edge of each bay, for crane
            crossing geometry (D62). Default low is synthetic.
        reefer_rows: rows with reefer plugs (reefers only there; a port rule, 1.5/2.6).
        dg_block: True if DG boxes may be stored here (port rule; assumption until Q9).
        allowed_lengths_ft: which box lengths may be stacked here.
        x_m, y_m: block origin in the terminal plane (for travel distance; informational in stage A).
    """

    id: str = Field(pattern=r"^[A-Z0-9]{1,4}$")
    n_bays: int = Field(ge=1)
    n_rows: int = Field(ge=1)
    max_tiers: int = Field(ge=1)
    equipment_id: str
    truck_lane_side: Literal["low", "high"] = "low"
    reefer_rows: list[int] = Field(default_factory=list)
    dg_block: bool = False
    allowed_lengths_ft: list[Literal[20, 40, 45]] = Field(default_factory=lambda: [20, 40, 45])
    x_m: Metres = 0.0
    y_m: Metres = 0.0


    @model_validator(mode="after")
    def normalize_rows(self) -> YardBlock:
        if any(not 1 <= row <= self.n_rows for row in self.reefer_rows):
            raise ValueError(f"block {self.id} has an out-of-range reefer row")
        self.reefer_rows = sorted(set(self.reefer_rows))
        self.allowed_lengths_ft = sorted(set(self.allowed_lengths_ft))
        return self


class Yard(StowModel):
    """Static layout: the blocks. ``id`` is a slug of the port's yard (e.g. ``"laem_chabang_like.berth_group"``)."""

    id: str
    blocks: list[YardBlock]


    @model_validator(mode="after")
    def normalize_blocks(self) -> Yard:
        if len(self.blocks) != len({block.id for block in self.blocks}):
            raise ValueError("yard has duplicate block ids")
        self.blocks = sorted(self.blocks, key=lambda block: block.id)
        return self


class YardStack(StowModel):
    """Contents of one yard stack. ``boxes`` bottom → top, no gaps; ``length_ft`` is the stack's length class."""

    key: YardStackKey
    length_ft: Literal[20, 40, 45]
    boxes: list[ContainerId]


    @model_validator(mode="after")
    def validate_stack(self) -> YardStack:
        parse_yard(self.key + "-01")
        if not self.boxes:
            raise ValueError("yard state stores non-empty stacks only")
        if len(self.boxes) != len(set(self.boxes)):
            raise ValueError(f"yard stack {self.key} contains a box in two places")
        return self


class YardState(StowModel):
    """Which box is where in the yard at a moment.

    Fields:
        yard_id: the Yard this state belongs to.
        time_s: simulation time of the snapshot, s from the simulator epoch (3.4); 0 for random yards (3.5).
        stacks: non-empty stacks only, sorted by key (deterministic JSON). A box appears at most once in the whole
            yard and never also on the ship (1.1 validates within an Instance).
        quay_buffer: boxes temporarily on the quay apron (restow-off, hatch-cover staging is separate), in the order
            they were put down. Empty at the start and end of a valid plan for stage A.
    """

    yard_id: str
    time_s: float = 0.0
    stacks: list[YardStack] = Field(default_factory=list)
    quay_buffer: list[ContainerId] = Field(default_factory=list)


    @model_validator(mode="after")
    def validate_locations(self) -> YardState:
        keys = [stack.key for stack in self.stacks]
        if len(keys) != len(set(keys)):
            raise ValueError("yard state has duplicate stack keys")
        boxes = [box for stack in self.stacks for box in stack.boxes] + self.quay_buffer
        if len(boxes) != len(set(boxes)):
            raise ValueError("yard state contains a box in two places")
        footprints = set()
        for stack in self.stacks:
            address = parse_yard(stack.key + "-01")
            for offset in range(1 if stack.length_ft == 20 else 2):
                position = (address.block, address.bay + offset, address.row)
                if position in footprints:
                    raise ValueError(f"yard stack {stack.key} overlaps another stack footprint")
                footprints.add(position)
        self.stacks = sorted(self.stacks, key=lambda stack: stack.key)
        return self
