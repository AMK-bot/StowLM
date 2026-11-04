"""MovePlan: the ordered moves for one call plus the final ballast (plan 1.1).

Every planner returns one (planner protocol, ``stowage.planner``). Gold plans (6.1) also store the structured
action sequence (``actions``) in the 3.8 action space so move counts reproduce exactly; tokens are never stored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from stowage.model.base import CallIndex, Kg, StowModel
from stowage.model.moves import Move
from stowage.model.provenance import Provenance
from stowage.versions import SCHEMA_VERSION


class BallastCondition(StowModel):
    """Departure ballast: content mass per *adjustable* tank, kg (absent adjustable tank = empty). Fixed tanks keep
    the instance's ``onboard.tank_masses_kg``. ``source`` = who set it: the planner itself (e.g. the exact solver) or
    the ballast finisher (4.2)."""

    tank_masses_kg: dict[str, Kg]
    source: Literal["planner", "finisher"]


class MovePlan(StowModel):
    """A plan for one port call.

    Fields:
        schema_version: ``stowage.versions.SCHEMA_VERSION`` at write time.
        id: unique plan id (e.g. ``"<instance_id>.<planner>.<seed>"``).
        instance_id: the Instance it plans.
        call_index: the call it plans (= ``Instance.call_index``; stage D multi-call plans: one MovePlan per call).
        moves: ordered lifts. Every move's ``call_index`` equals this ``call_index``.
        ballast: departure ballast, or None = not set yet (the finisher adds it; ``stowage.planner``). A plan with
            ``ballast=None`` cannot pass the full checker when the vessel has adjustable tanks.
        action_space: ``"<name>@<version>"`` of the 3.8 action space the planner spoke, or None (planner wrote moves).
        actions: the structured action sequence (JSON-able dicts from ``ActionSpace.encode``), or None.
        provenance: versions, stubs, seed, producer.
    """

    schema_version: str = SCHEMA_VERSION
    id: str
    instance_id: str
    call_index: CallIndex
    moves: list[Move] = Field(default_factory=list)
    ballast: BallastCondition | None = None
    action_space: str | None = None
    actions: list[dict[str, Any]] | None = None
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def consistent_calls(self) -> MovePlan:
        if any(move.call_index != self.call_index for move in self.moves):
            raise ValueError("every move call_index must equal the plan call_index")
        return self
