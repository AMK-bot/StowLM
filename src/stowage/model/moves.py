"""Move: one lift. The objective (D11) counts these (plan 1.1 declares; 2.3 applies and counts).

Every move is exactly **one lift** by one machine (quay crane or yard equipment). A restow via the quay is two moves
(``restow_off`` + ``restow_on``); a direct shift within the ship is one (``restow_shift``). A hatch cover lifted off
and later put back is two moves (``hatch_cover_off`` + ``hatch_cover_on``). Whether cover lifts count, and the
relative weights, are expert Q1 → ``ObjectiveWeights``.

Locations: yard positions are ``BLOCK-BBB-RR-TT`` strings, ship positions ``BBBRRTT`` slots, ``"quay"`` is the quay
apron buffer (``YardState.quay_buffer``), ``"gate"`` is outside the terminal (trucks/rail; simulator only).
For direct delivery (D61 C5), ``Load.src="truck"`` / ``Discharge.dst="truck"`` represents the truck at ship side, with
no yard endpoint. Only a container marked ``direct_delivery`` may use it; state-aware enforcement belongs to the
move engine and checker, since a standalone move has no container registry.
A move's ``dst`` yard address/slot must be the exact position the box ends up in (top of the target stack), so a plan
replays without interpretation.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from stowage.model.base import CallIndex, ContainerId, HatchCoverId, SlotId, StowModel, YardAddr


class _MoveBase(StowModel):
    call_index: CallIndex  # the port call at which this lift happens


class Relocate(_MoveBase):
    """Yard → yard (a rehandle when it serves loading; non-productive)."""

    kind: Literal["relocate"] = "relocate"
    container_id: ContainerId
    src: YardAddr
    dst: YardAddr


class Load(_MoveBase):
    """Yard → ship, or truck → ship for direct delivery when src is "truck". Productive (one lift).

    A truck source is reserved for marked direct-delivery containers; move-engine/checker validation uses the
    instance to verify that marker. Restowed boxes come back from the quay buffer by ``RestowOn``.
    """

    kind: Literal["load"] = "load"
    container_id: ContainerId
    src: YardAddr | Literal["truck"]
    dst: SlotId


class Discharge(_MoveBase):
    """Ship → yard, or ship → truck for direct delivery when dst is "truck". Productive (one lift).

    Only for boxes whose POD is this call. A truck destination is reserved for marked direct-delivery containers;
    move-engine/checker validation uses the instance to verify that marker. A box taken off *temporarily* must
    use ``RestowOff`` (to the quay buffer) so counting stays unambiguous; the quay buffer holds only restowed boxes.
    """

    kind: Literal["discharge"] = "discharge"
    container_id: ContainerId
    src: SlotId
    dst: YardAddr | Literal["truck"]


class RestowShift(_MoveBase):
    """Ship → ship in one lift (shift within reach of the crane). Non-productive (1 lift)."""

    kind: Literal["restow_shift"] = "restow_shift"
    container_id: ContainerId
    src: SlotId
    dst: SlotId


class RestowOff(_MoveBase):
    """Ship → quay buffer, to be put back later at the same call. Non-productive (1 lift)."""

    kind: Literal["restow_off"] = "restow_off"
    container_id: ContainerId
    src: SlotId


class RestowOn(_MoveBase):
    """Quay buffer → ship, for a box that came off by ``RestowOff``. Non-productive (1 lift)."""

    kind: Literal["restow_on"] = "restow_on"
    container_id: ContainerId
    dst: SlotId


class HatchCoverOff(_MoveBase):
    """Lift a hatch cover off (onto the quay, or onto another closed cover: ``dst`` = that cover id). All deck stacks
    on it must be empty. Non-productive (1 lift)."""

    kind: Literal["hatch_cover_off"] = "hatch_cover_off"
    cover_id: HatchCoverId
    dst: HatchCoverId | Literal["quay"] = "quay"  # another cover must be closed, with no deck boxes and no cover on it


class HatchCoverOn(_MoveBase):
    """Put a lifted hatch cover back. Non-productive (1 lift)."""

    kind: Literal["hatch_cover_on"] = "hatch_cover_on"
    cover_id: HatchCoverId  # must not have another cover resting on it


class GateIn(_MoveBase):
    """Gate → yard (simulator 3.4: an export box arrives, or an import is placed after discharge staging).
    Counted separately; not part of the loading objective."""

    kind: Literal["gate_in"] = "gate_in"
    container_id: ContainerId
    dst: YardAddr


class GateOut(_MoveBase):
    """Yard → gate (simulator 3.4: an import leaves). Counted separately."""

    kind: Literal["gate_out"] = "gate_out"
    container_id: ContainerId
    src: YardAddr


Move = Annotated[
    Relocate | Load | Discharge | RestowShift | RestowOff | RestowOn | HatchCoverOff | HatchCoverOn | GateIn | GateOut,
    Field(discriminator="kind"),
]
MoveKind = Literal[
    "relocate", "load", "discharge", "restow_shift", "restow_off", "restow_on",
    "hatch_cover_off", "hatch_cover_on", "gate_in", "gate_out",
]
NON_PRODUCTIVE_KINDS: tuple[str, ...] = (
    "relocate", "restow_shift", "restow_off", "restow_on", "hatch_cover_off", "hatch_cover_on",
)
