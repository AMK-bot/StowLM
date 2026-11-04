"""Instance: one planning problem (plan 1.1; produced by 3.5 / 4.3 / 10.3 / 19.1, read by everything).

Stage coverage: A = empty ship (``onboard.placements`` empty, no discharges); B = layover (``onboard`` holds boxes,
``discharge_list`` non-empty, inherited boxes may carry tolerated violations); C = yard policy (uses the same
Instance per ship call plus the simulator); D = two load ports (``forecasts`` for later load calls).
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from stowage.conventions import cells_of_slot, parse_slot, parse_yard

from stowage.model.base import CallIndex, ContainerId, StowModel
from stowage.model.cargo import Container
from stowage.model.port import PortProfile, Rotation
from stowage.model.provenance import Provenance
from stowage.model.ship import ShipState
from stowage.model.vessel import Vessel, _validate_stack_occupancy
from stowage.model.voyage import VoyageSpec, validate_voyage_spec
from stowage.model.yard import YardState
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.versions import SCHEMA_VERSION

SizeClass = Literal["tiny", "small", "medium", "large", "full"]  # the 3.5 size ladder; 3.5 fixes the bounds


class ObjectiveWeights(StowModel):
    """D11 level-3 weights per lift type. All 1 until the expert answers Q1. Projected later-port lifts are counted
    by the move engine (``stowage.moves.project_future_moves``) and weighted here."""

    relocate: float = 1.0
    restow: float = 1.0  # per restow lift (shift = 1 lift; off + on = 2 lifts)
    hatch_cover: float = 1.0  # per cover lift
    future_restow: float = 1.0  # per projected restow lift at later calls
    future_hatch_cover: float = 1.0  # per projected cover lift at later calls
    # ASSUMPTION: D15, pending expert Q1: projected restow = off + on.
    future_restow_lifts_per_box: int = Field(default=2, ge=1)


class ForecastScenario(StowModel):
    """Stage D: one scenario of the boxes expected to load at a later call, with its probability."""

    probability: float = Field(ge=0.0, le=1.0)
    containers: list[Container]

    @model_validator(mode="after")
    def canonical_containers(self) -> ForecastScenario:
        if len({box.id for box in self.containers}) != len(self.containers):
            raise ValueError("duplicate container id in forecast scenario")
        self.containers.sort(key=lambda box: box.id)
        return self


class CallForecast(StowModel):
    """Stage D: forecast for a later load call (scenario probabilities sum to 1)."""

    call_index: CallIndex
    scenarios: list[ForecastScenario]

    @model_validator(mode="after")
    def probability_total(self) -> CallForecast:
        if not math.isclose(sum(s.probability for s in self.scenarios), 1.0, abs_tol=1e-9):
            raise ValueError("forecast scenario probabilities must sum to 1")
        return self


class PlanConstraint(StowModel):
    """Planner instruction from the instructions reader (19.2), e.g. "keep the reefers in bay 14". The schema of
    ``kind``/``params`` is owned by 19.2; the checker reports a broken one as rule id ``instructions.<kind>``."""

    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    hardness: Literal["hard", "soft"] = "hard"
    source_text: str | None = None


class Instance(StowModel):
    """One planning problem for one call (``call_index``) of ``rotation``.

    Fields:
        schema_version, id: as MovePlan. stage: "A".."D". size_class: ladder rung. family: split tags
            (``{"vessel": ..., "port": ..., "noise": ...}``, 4.3/5.3). seed: generator seed.
        vessel, rotation, port (the port at ``call_index``).
        call_index: which call we plan.
        plan_calls: stage D: further load calls planned jointly (``PlannerResult.later_plans``); empty otherwise. ``rotation.calls[call_index].locode == port.locode``.
        yard: yard snapshot when loading starts (3.4 or 3.5).
        onboard: ship state on arrival (empty placements in stage A; fixed tanks' contents always).
        containers: every box referenced anywhere (yard, onboard, load list, forecasts excluded), sorted by id.
        load_list: boxes that must be loaded at this call (in the yard at start, or marked direct_delivery with no
            yard position). D11 level 2: all must load.
        discharge_list: boxes aboard whose POD is this call (stage B; empty in A). Must all be discharged.
        objective: lift weights. forecasts: stage D. plan_constraints: instructions reader.
        rule_overrides: catalogue id → parameter overrides for this instance (e.g. a tighter GM for an experiment);
            empty for normal instances. Recorded so results are reproducible.
        provenance: generator, seed, versions, stubs.
        voyage: optional D50 identities and consumption declaration for departure to the immediate next call.
            Arrival liquids are derived from the checked departure, never supplied as an independent state.
    """

    schema_version: str = SCHEMA_VERSION
    id: str
    stage: Literal["A", "B", "C", "D"] = "A"
    size_class: SizeClass
    family: dict[str, str] = Field(default_factory=dict)
    seed: int | None = None
    vessel: Vessel
    rotation: Rotation
    port: PortProfile
    call_index: CallIndex
    plan_calls: list[CallIndex] = Field(default_factory=list)
    yard: YardState
    onboard: ShipState
    containers: list[Container]
    load_list: list[ContainerId]
    discharge_list: list[ContainerId] = Field(default_factory=list)
    objective: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    forecasts: list[CallForecast] = Field(default_factory=list)
    plan_constraints: list[PlanConstraint] = Field(default_factory=list)
    rule_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    voyage: VoyageSpec | None = None

    @field_validator("load_list", "discharge_list", "plan_calls")
    @classmethod
    def canonical_set(cls, value: list) -> list:
        if len(set(value)) != len(value):
            raise ValueError("duplicate entry in set-like list")
        return sorted(value)

    @model_validator(mode="after")
    def consistent_references(self) -> Instance:
        if self.port.profile_kind != "operational":
            raise ValueError("current port must be an operational profile; next-call environments declare density only")
        calls = self.rotation.calls
        if self.call_index >= len(calls):
            raise ValueError("call_index is outside rotation")
        if calls[self.call_index].locode != self.port.locode:
            raise ValueError("port.locode must match rotation at call_index")
        if self.voyage is not None:
            validate_voyage_spec(self.vessel, self.voyage)
            departure, arrival = self.voyage.departure, self.voyage.arrival
            if departure.call_index != self.call_index or departure.locode != self.port.locode:
                raise ValueError("voyage.departure call_index/locode must match the current call")
            if self.call_index + 1 >= len(calls):
                raise ValueError("voyage.arrival requires a next call in rotation")
            if arrival.call_index != self.call_index + 1 or arrival.locode != calls[self.call_index + 1].locode:
                raise ValueError("voyage.arrival call_index/locode must match the immediate next call")
            if not math.isclose(departure.water_density_kg_m3, self.port.water_density_kg_m3,
                                rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("voyage.departure.water_density_kg_m3 must match port.water_density_kg_m3")
        if self.yard.yard_id != self.port.yard.id:
            raise ValueError("yard_id does not match port yard")
        if self.onboard.vessel_id != self.vessel.id:
            raise ValueError("onboard vessel_id does not match vessel")
        boxes = {box.id: box for box in self.containers}
        if len(boxes) != len(self.containers):
            raise ValueError("duplicate container id in containers")
        self.containers.sort(key=lambda box: box.id)
        if rule_freeze_v1_enabled():
            for box in self.containers:
                required = self.port.requires_direct_delivery(box.dg.imdg_class if box.dg else None)
                if box.direct_delivery != required:
                    raise ValueError(f"direct_delivery must match current-port primary DG class: {box.id}")
        blocks = {block.id: block for block in self.port.yard.blocks}
        yard_boxes = []
        for stack in self.yard.stacks:
            addr = parse_yard(stack.key + "-01")
            block = blocks.get(addr.block)
            if block is None:
                raise ValueError(f"unknown yard block: {addr.block}")
            last_bay = addr.bay + (stack.length_ft != 20)
            if last_bay > block.n_bays or addr.row > block.n_rows:
                raise ValueError(f"yard stack outside block geometry: {stack.key}")
            for cid in stack.boxes:
                if cid not in boxes:
                    raise ValueError(f"unknown container in yard: {cid}")
                if boxes[cid].length_ft != stack.length_ft:
                    raise ValueError(f"container length disagrees with yard stack: {cid}")
            yard_boxes.extend(stack.boxes)
        yard_boxes.extend(self.yard.quay_buffer)
        onboard_ids = [p.container_id for p in self.onboard.placements]
        located = yard_boxes + onboard_ids
        if len(set(located)) != len(located):
            raise ValueError("container appears in two places (yard, quay or onboard)")
        for cid in located + self.load_list + self.discharge_list:
            if cid not in boxes:
                raise ValueError(f"unknown container reference: {cid}")
        direct_delivery_ids = {box.id for box in self.containers if box.direct_delivery}
        if direct_delivery_ids & set(yard_boxes):
            raise ValueError("direct-delivery container must not appear in the yard snapshot")
        if not set(self.load_list) - direct_delivery_ids <= set(yard_boxes):
            raise ValueError("every load_list container must be in the yard unless marked direct_delivery")
        if set(self.load_list) & direct_delivery_ids & set(onboard_ids):
            raise ValueError("direct-delivery load_list container must not already be onboard")
        if not set(self.discharge_list) <= set(onboard_ids):
            raise ValueError("every discharge_list container must be onboard")
        if set(self.load_list) & set(self.discharge_list):
            raise ValueError("load_list and discharge_list must be disjoint")
        for box in self.containers:
            if box.id in self.discharge_list:
                if box.pod != self.port.locode:
                    raise ValueError(f"discharge container POD must be current port: {box.id}")
            elif not any(call.locode == box.pod for call in calls[self.call_index + 1:]):
                raise ValueError(f"unresolved POD {box.pod} after call_index for {box.id}")
        for cid in onboard_ids:
            if boxes[cid].pod == self.port.locode and cid not in self.discharge_list:
                # A later repeated call can still be this box's destination; the contract resolves it later.
                if not any(c.locode == boxes[cid].pod for c in calls[self.call_index + 1:]):
                    raise ValueError(f"onboard current-port cargo missing from discharge_list: {cid}")
        cells = {cell.id: cell for cell in self.vessel.cells}
        occupied_cells = set()
        for placement in self.onboard.placements:
            addr = parse_slot(placement.slot)
            box = boxes[placement.container_id]
            if (box.length_ft == 20) != addr.is_20ft_bay:
                raise ValueError(f"container length disagrees with slot: {placement.slot}")
            for cell_id in cells_of_slot(placement.slot):
                if cell_id not in cells:
                    raise ValueError(f"unknown slot/cell: {placement.slot} ({cell_id})")
                capability = f"accepts_{box.length_ft}"
                if not getattr(cells[cell_id], capability):
                    raise ValueError(f"onboard.placements requires {capability} for cell {cell_id} "
                                     f"at slot {placement.slot}")
                occupied_cells.add(cell_id)
        # Use each stack's declared geometry rather than tier arithmetic: hold and
        # deck have separate bases, and long boxes require support in both columns.
        _validate_stack_occupancy(self.vessel.stacks, occupied_cells, field="onboard.placements")
        tank_ids = {tank.id for tank in self.vessel.tanks}
        for tank_id in self.onboard.tank_masses_kg:
            if tank_id not in tank_ids:
                raise ValueError(f"unknown tank: {tank_id}")
        cover_ids = {cover.id for cover in self.vessel.hatch_covers}
        for cover, destination in self.onboard.covers_off.items():
            if cover not in cover_ids or (destination != "quay" and destination not in cover_ids):
                raise ValueError(f"unknown hatch cover reference: {cover} -> {destination}")
        for index in self.plan_calls:
            if not self.call_index < index < len(calls):
                raise ValueError("plan_calls must refer to later calls in rotation")
        if len({f.call_index for f in self.forecasts}) != len(self.forecasts):
            raise ValueError("duplicate forecast call_index")
        self.forecasts.sort(key=lambda f: f.call_index)
        for forecast in self.forecasts:
            if not self.call_index < forecast.call_index < len(calls):
                raise ValueError("forecast call_index must refer to a later call")
            for scenario in forecast.scenarios:
                for box in scenario.containers:
                    if not any(c.locode == box.pod for c in calls[forecast.call_index + 1:]):
                        raise ValueError(f"unresolved forecast POD {box.pod} for {box.id}")
        # D15: nested shipment and vessel evidence is input lineage too. Keep it
        # when consumers derive a standalone report from instance.provenance.
        self.provenance = self.provenance.merge_nested(self)
        return self
