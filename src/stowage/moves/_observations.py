"""Current-call D62 observations emitted by physical plan replay, not estimates."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from stowage.model import ApplyResult
from stowage.model.base import StowModel
from stowage.model.moves import GateOut
from stowage.moves._paths import CrossingPath, _CrossingGeometry, _indexed_crossing_paths
from stowage.rules.catalogue import rule_freeze_v1_enabled


class CrossingObservations(StowModel):
    """One count per processed lift/domain at the pre-commit crossing check.

    Earlier source/DG/height failures, unprocessed suffixes and abstract future
    lifts are excluded. A lift can contribute to both ship and yard exposure,
    but rejection is counted once. Ship uses quay H; yard uses the obstructing
    block's effective yard limit (A8), independently of H. The historical
    ``yard_*_h`` field names are retained for G1 API compatibility.
    A yard rehandle counts local clearance of a remaining export's lane path;
    this is not proof of necessary extra cost or overall planning improvement.
    """

    source: Literal["migrated_move_engine"] = "migrated_move_engine"
    status: Literal["applicable", "not_applicable"]
    not_applicable_reason: Literal["switch_off", "unknown_height"] | None = None
    ship_lifts_at_or_above_h: int = Field(default=0, ge=0)
    yard_lifts_at_or_above_h: int = Field(default=0, ge=0)
    rejected_crossing_lifts: int = Field(default=0, ge=0)
    yard_h_blocking_rehandles: int = Field(default=0, ge=0)


class ObservedApplyResult(ApplyResult):
    """Additive move-owned result; serialize observations with model_dump/json.

    Use this type for a full JSON round trip. The original strict ApplyResult
    schema does not accept its new d62_crossing field; result.count remains the
    unchanged MoveCount model. No Instance/model schema is changed.
    """

    d62_crossing: CrossingObservations


class CrossingObserver:
    """Replay-local accumulator, with no mutation of instance, plan or world."""

    def __init__(self, replay):
        enabled = rule_freeze_v1_enabled()
        applicable = enabled and (replay.crane_height is not None or bool(replay.yard_blocking_limits))
        self.metric = CrossingObservations(
            status="applicable" if applicable else "not_applicable",
            not_applicable_reason=None if applicable else "unknown_height" if enabled else "switch_off",
        )
        self.geometry = _CrossingGeometry(replay.instance, replay.instance.port) if applicable else None

    def path(self, replay, move):
        # Unknown ship H and no yard blocks make the observer not applicable.
        # Truck transfers still replay, with no crossing geometry to evaluate.
        if self.geometry is None:
            return CrossingPath()
        return _indexed_crossing_paths(move, port=replay.instance.port, geometry=self.geometry)

    def exposure(self, *, ship: bool, yard: bool):
        self.metric.ship_lifts_at_or_above_h += int(ship)
        self.metric.yard_lifts_at_or_above_h += int(yard)

    def rejected(self, rule_id: str):
        if self.geometry is not None and rule_id in ("port.e3.quay_crossing", "port.e3.yard_crossing"):
            self.metric.rejected_crossing_lifts += 1

    def rehandle_candidate(self, replay, move) -> bool:
        """Before relocation, does its source obstruct a pending export lane?

        Exclude the export's own start stack, direct delivery, and unrelated
        full stacks. The threshold is the source block's effective yard limit,
        not quay H. Paths use current yard addresses, including both halves
        of long boxes. Count only after replay commits the candidate relocation.
        """
        if self.geometry is None or move.kind != "relocate":
            return False
        if replay.locations.get(move.container_id) != ("yard", move.src):
            return False
        source = move.src.rsplit("-", 1)[0]
        source_limit = replay.yard_blocking_limits[source.split("-", 1)[0]]
        if source == move.dst.rsplit("-", 1)[0] or len(replay.yard[source]) != source_limit:
            return False
        seen = {source}
        for cid in sorted(replay.load_list):
            location = replay.locations.get(cid)
            if location is None or location[0] != "yard" or replay.boxes[cid].direct_delivery:
                continue
            key = location[1].rsplit("-", 1)[0]
            if key in seen:
                continue
            seen.add(key)
            path = self.path(replay, GateOut(call_index=replay.call_index, container_id=cid, src=location[1]))
            if any(replay.footprints.get(ground) == source for ground in path.yard_ground_slots):
                return True
        return False

    def committed(self, rehandle: bool):
        self.metric.yard_h_blocking_rehandles += int(rehandle)
