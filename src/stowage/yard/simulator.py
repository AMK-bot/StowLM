"""Event-driven synthetic port yard, with reproducible call and gate schedules.

An event advances time directly to the next gate arrival, ship or import exit;
no empty time steps are simulated. Placement follows the ordered clustering and
retrieval preferences in params/ports/README.md (task 1.5). Activated D72 selects
other-box placements above pending exports with a separate synthetic mixing
share (default 25 percent whenever noise is positive), independent of clustering
noise. Unmixed placements retain D25 clustering with clustering-error noise.
Noise zero is an idealized separated yard: capacity pressure queues/refuses
arrivals rather than granting permission to mix. Legacy noise ignores clustering;
independent retrieval-order errors remain. The move engine approves every lift;
these snapshots are planning inputs, never checker-approved plans.

APPROXIMATION: each berth serves a call for a caller-supplied fixed duration;
individual lifts occur at loading start without a crane travel/service model.
APPROXIMATION: other ships try their explicit load order into the first legal
length-compatible slot; activated D69 defers near-row loads for farther lifts.
Placement prefers intact long-box pairs and hold-before-deck order, without
solving global packing, weights or future port restows. Unserved loads/discharges
are reported, never certified.
APPROXIMATION: workload volume is a negative-binomial synthetic demand model,
not a measured terminal distribution. Conditional volume caps are reported.
APPROXIMATION: local-import equipment, DG mix and POD-dependent payloads are
drawn from export lanes and then reversed; these are not inbound-trade estimates.
APPROXIMATION: every port, including hubs, has gateway-style gate-in only;
there is no vessel-discharge transshipment inflow to its export yard stacks.
Transshipment is not manufactured: the current move contract cannot discharge
an intermediate-POD box into the yard (Discharge requires final POD=current
port). The sampler records unserved transfer demand pending that model extension.
Source: params/ports/README.md and model/moves.py; numpy.random.Generator
negative_binomial and gamma definitions describe the sampling parameterisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from random import Random
import re

import numpy as np
from pydantic import Field as ModelField

from stowage.conventions import canonical_vessel_id, cells_of_slot, parse_slot, parse_yard
from stowage.generate import cargo as cargo_generator
from stowage.generate import cargo_shipments
from stowage.generate.cargo import generate_load_list
from stowage.model import Instance, Vessel
from stowage.model.moves import Discharge, GateIn, GateOut, HatchCoverOff, Load, Move
from stowage.model.port import Rotation
from stowage.model.provenance import Provenance
from stowage.model.ship import Placement, ShipState, WorldState
from stowage.model.yard import YardState
from stowage.moves.engine import IllegalMoveError, apply_move
from stowage.ports.profiles import SimulatorParameters
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.versions import YARD_SIMULATOR_VERSION as SIMULATOR_VERSION, component_versions
from stowage.yard.dig_out import (
    DigOutPolicy, NearestStackDigOut, YardCapacityError, destinations, location,
    preserves_export_separation, validate_relocation, _yard_tiers,
)


def effective_yard_simulator_version() -> str:
    """Resolve the staged yard epoch at operation time, independently of registry timing."""
    return "5.0.0" if rule_freeze_v1_enabled() else "3.0.0"


@dataclass(frozen=True)
class ShipCall:
    """One independently identified ship call and its registered cargo.

    The instance supplies vessel/rotation/cargo contracts. Yard placement is
    generated afresh. Explicit gate times must cover every export exactly once,
    precede cut-off, and may be negative relative to the simulation epoch.
    ``load_order`` is a permutation of the load list; empty means stable ID order.
    """
    id: str
    instance: Instance
    arrival_s: float
    cutoff_s: float
    gate_in_times: dict[str, float]
    service_s: float = 3600.0
    load_order: tuple[str, ...] = ()

    def __post_init__(self):
        if not all(math.isfinite(t) for t in (self.arrival_s, self.cutoff_s, self.service_s)) or self.service_s <= 0:
            raise ValueError("ship times must be finite and service_s positive")
        if self.cutoff_s > self.arrival_s:
            raise ValueError("cutoff must not be after arrival")
        if set(self.gate_in_times) != set(self.instance.load_list):
            raise ValueError("gate-in times must cover exactly the export load list")
        if any(not math.isfinite(t) or t > self.cutoff_s for t in self.gate_in_times.values()):
            raise ValueError("export gate-in must occur by cut-off")
        if self.load_order and (len(self.load_order) != len(self.instance.load_list)
                                or set(self.load_order) != set(self.instance.load_list)):
            raise ValueError("load_order must be a permutation of load_list")


@dataclass(frozen=True)
class TimedMove:
    time_s: float
    call_id: str
    move: Move


@dataclass(frozen=True)
class LoadingSnapshot:
    call_id: str
    time_s: float
    yard: YardState
    moves_before: int
    statistics: dict[str, float]


@dataclass
class SimulationResult:
    snapshots: list[LoadingSnapshot] = field(default_factory=list)
    moves: list[TimedMove] = field(default_factory=list)
    final_yard: YardState | None = None
    external_queue: list[str] = field(default_factory=list)
    missed_cutoffs: list[str] = field(default_factory=list)
    unserved_loads: list[str] = field(default_factory=list)
    unserved_discharges: list[str] = field(default_factory=list)
    retained_import_exits: list[str] = field(default_factory=list)
    final_ships: dict[str, ShipState] = field(default_factory=dict)
    queued_gate_events: int = 0
    berth_wait_s: dict[str, float] = field(default_factory=dict)
    seed: int = 0
    simulator_version: str = field(default_factory=lambda: effective_yard_simulator_version())

    @property
    def cover_diagnostics(self) -> list[str]:
        """Closure failures, kept outside the legacy dataclass wire fields.

        Inspect alongside ``final_ships`` when a simulated call cannot close a
        cover. Successful legacy results retain their exact serialized shape.
        """
        return self.__dict__.setdefault("_cover_diagnostics", [])


class YardSimulationProvenance(Provenance):
    """Active simulation settings and observed D72 counts, distinct from cargo provenance."""

    congestion_mixing: dict[str, str | float | int | bool] = ModelField(default_factory=dict)


@dataclass
class CongestedSimulationResult(SimulationResult):
    """D72 adds serialized provenance only to active results; legacy wire fields stay fixed."""

    provenance: YardSimulationProvenance = field(default_factory=YardSimulationProvenance)


def _simulation_result(calls: list[ShipCall], parameters: SimulatorParameters, *,
                       seed: int, noise: float | None,
                       congestion_mix_share: float) -> SimulationResult:
    if not rule_freeze_v1_enabled():
        return SimulationResult(seed=seed)
    level = noise if noise is not None else parameters.placement.cluster_misplacement_probability
    # APPROXIMATION: a synthetic scenario share, not an observed terminal rate.
    # The default is 25 percent at noise .2/.5/.8; noise zero always disables it.
    # Legal destination capacity can lower the achieved share; count both.
    mixing = {
        "synthetic": True, "policy": "synthetic-independent-v2", "noise_level": level,
        "congestion_mix_share": congestion_mix_share,
        "configured_share": congestion_mix_share if level > 0 else 0.0,
        "noise_zero_capacity_policy": "idealized separated yard: queue arrivals or report unserved retrievals rather than mix under capacity pressure",
        "achieved_share": 0.0,
        "share_definition": "selection probability per unique box at its first eligible placement attempt; eligible means a compatible destination above pending exports exists; actual engine legality may prevent mixing",
        "achieved_share_definition": "mixed_boxes / eligible_boxes (zero when no eligible boxes); counters are distinct from repeated placement attempts",
        **dict.fromkeys(("placement_attempts", "eligible_placements", "mixing_attempts",
                         "mixed_placements", "eligible_boxes", "mixing_selected_boxes", "mixed_boxes"), 0),
    }
    base = Provenance(seed=seed, created_by="yard.simulate_port",
                      versions={**component_versions(),
                                "yard_simulator": effective_yard_simulator_version()}).merge_nested(
                          *(call.instance for call in calls), parent_ids=[call.id for call in calls])
    provenance = YardSimulationProvenance(**base.model_dump(), congestion_mixing=mixing)
    result = CongestedSimulationResult(seed=seed, provenance=provenance)
    # Policy counters update the same serialized mapping, never caller inputs.
    result.__dict__["_congestion_mixing"] = provenance.congestion_mixing
    return result


@dataclass
class WorkloadReport:
    raw_load_boxes: int = 0
    conditioned_load_boxes: int = 0
    raw_discharge_boxes: int = 0
    conditioned_discharge_boxes: int = 0
    unserved_transfer_demand: int = 0
    unserved_transfer_arrivals: int = 0
    conditioning: list[str] = field(default_factory=list)


def yard_statistics(instance: Instance, yard: YardState, targets: list[str] | tuple[str, ...]) -> dict[str, float]:
    """TEU occupancy, nonempty-stack height, and physically blocked target share.

    Any box above a target blocks physical access, even if it is interchangeable:
    one still needs a lift to retrieve that particular target. This differs from
    random-generator *out-of-order* blocking, which discounts equal categories.
    """
    capacity = sum(b.n_bays * b.n_rows * _yard_tiers(instance.port, b)
                   for b in instance.port.yard.blocks)
    occupied = sum(len(s.boxes) * (1 if s.length_ft == 20 else 2) for s in yard.stacks)
    heights = [len(s.boxes) for s in yard.stacks]
    wanted = set(targets)
    present = sum(cid in wanted for s in yard.stacks for cid in s.boxes)
    blocked = sum(cid in wanted for s in yard.stacks for cid in s.boxes[:-1])
    ground = sum(b.n_bays * b.n_rows for b in instance.port.yard.blocks)
    return {"occupancy_teu_fraction": occupied / capacity, "mean_nonempty_stack_height":
            sum(heights) / len(heights) if heights else 0.0,
            "mean_height_per_teu_ground_position": occupied / ground,
            "max_stack_height": float(max(heights, default=0)),
            "blocked_target_share": blocked / present if present else 0.0,
            "target_presence_fraction": present / len(wanted) if wanted else 1.0,
            "boxes": float(sum(heights)), "teu": float(occupied)}


def _workload_teu_cap(vessel: Vessel) -> int:
    """Apply current fleet/slice nominal TEU caps, independently of cell count.

    Stored vessels keep their historical labels and nominal values. Resolve
    only the declared lineage for this new workload; unrelated vessels continue
    to use their own nominal capacity. Reading parameters constructs no physics.
    """
    from stowage.vessel.fleet import list_fleet, load_ship_params

    identities = [value for value in (vessel.slice_of, vessel.id) if value is not None]
    for source in vessel.sources:
        declared = re.search(r"(?:^|/)params/fleet/([^/\s]+)\.toml(?:\s|$)", source)
        if declared:
            identities.append(declared.group(1))
    known = set(list_fleet())
    nominal_cap = vessel.nominal_teu
    for identity in identities:
        name = canonical_vessel_id(identity).partition(".")[0]
        if name in known:
            nominal_cap = min(nominal_cap, load_ship_params(name)["nominal_teu"])
            break
    # A 45' box is 2.25 nominal TEU but occupies two physical 20' positions.
    # Physical-cell screening therefore belongs alongside, not inside, this cap.
    return nominal_cap


def sample_ship_calls(template: Instance, parameters: SimulatorParameters, *, days: float,
                      seed: int, volume_scale: float = 1.0, service_s: float = 3600,
                      id_offset: int = 0) -> tuple[list[ShipCall], WorkloadReport]:
    """Sample weekly services, independent counts, gate bins and import cargo.

    Cargo is drawn by task 2.5. Export counts are conservatively capped by current
    fleet nominal TEU (or a smaller declared slice cap), then each generated list
    is shortened to its TEU budget. With rule-freeze-v1 active, a separate physical
    20' cell budget counts 20' boxes as one, 40'/45' as two. Foreign vessels retain
    their own nominal cap. Imports have the same accepted nominal TEU budget and
    are conditioned on reachable first-tier deck slots and actual length pairs.
    These are explicit upper-capacity screens, not claims of true feasibility.
    The cell budget counts declared geometry; unusable cells and complete C1
    placement feasibility still require the downstream generator/checker screen.
    The caller can sample warm-up weeks by shifting returned times together.
    Service IDs retain their signed cycle relative to the original phase. Each
    cycle owns its RNG stream, including pre-origin cycles, so extending the
    horizon or examining additional jitter candidates never shifts call times.
    """
    if days <= 0 or not math.isfinite(days) or volume_scale <= 0 or not math.isfinite(volume_scale):
        raise ValueError("days and volume_scale must be positive and finite")
    if parameters.archetype != template.port.archetype:
        raise ValueError("simulator parameters must match the port archetype")
    nominal_cap = _workload_teu_cap(template.vessel)
    freeze = rule_freeze_v1_enabled()
    physical_cap = len(template.vessel.cells)
    # Candidate count is bounded by each budget because every box costs at
    # least one TEU and one cell; acceptance below charges their actual units.
    candidate_cap = min(nominal_cap, physical_cap) if freeze else nominal_cap
    rng = np.random.default_rng(seed)
    report = WorkloadReport()
    calls = []
    serial = id_offset

    def count(mean, cv):
        mean *= volume_scale
        variance = max((cv * mean) ** 2, mean + 1e-6)
        return int(rng.negative_binomial(mean * mean / (variance - mean), mean / variance))

    for si, service in enumerate(parameters.services):
        interval = 604800 / service.calls_per_week
        phase = si * 604800 / len(parameters.services)
        horizon = days * 86400
        jitter = service.arrival_jitter_s
        # Services repeat on both sides of the phase origin. Include every
        # nominal cycle whose jitter range intersects [0, horizon), even when
        # jitter spans multiple intervals (params/ports/README.md, Calls).
        first_cycle = math.ceil((-jitter - phase) / interval)
        last_cycle = math.ceil((horizon + jitter - phase) / interval) - 1
        for ci in range(first_cycle, last_cycle + 1):
            # Zig-zag encoding is injective for signed cycles and legal input
            # to SeedSequence; abs(ci) would alias the preceding/following calls.
            cycle_identity = 2 * ci if ci >= 0 else -2 * ci - 1
            rng = np.random.default_rng(np.random.SeedSequence([seed, si, cycle_identity, 3404]))
            arrival = phase + ci * interval + rng.uniform(-service.arrival_jitter_s, service.arrival_jitter_s)
            if not 0 <= arrival < horizon:
                continue
            n = count(service.mean_load_boxes, service.call_volume_cv)
            nd = count(service.mean_discharge_boxes, service.call_volume_cv)
            report.raw_load_boxes += n
            report.raw_discharge_boxes += nd
            # Preserve the specified inbound transfer fraction without falsely
            # turning those boxes into local imports. A separate per-call RNG
            # leaves export/gate sampling streams unchanged.
            inbound_rng = np.random.default_rng(np.random.SeedSequence([seed, si, cycle_identity, 3405]))
            transfer_arrivals = int(inbound_rng.binomial(nd, parameters.traffic.transshipment_fraction))
            report.unserved_transfer_arrivals += transfer_arrivals
            local_imports = nd - transfer_arrivals
            if transfer_arrivals:
                report.conditioning.append(f"{service.id}/{ci}: {transfer_arrivals} inbound transfer arrivals unsupported by intermediate-discharge contract; retained as unserved flow demand")
            transfer = int(rng.binomial(n, parameters.traffic.transshipment_fraction))
            report.unserved_transfer_demand += transfer
            n -= transfer
            limit = min(n, candidate_cap)
            cargo = generate_load_list(template.rotation, template.call_index, limit,
                                       seed=int(rng.integers(2**31)), id_offset=serial)
            serial += len(cargo)
            selected, teu, physical_cells = [], 0, 0
            for box in cargo:
                footprint = 1 if box.length_ft == 20 else 2
                if (teu + box.length_ft / 20 > nominal_cap
                        or (freeze and physical_cells + footprint > physical_cap)):
                    break
                selected.append(box)
                teu += box.length_ft / 20
                physical_cells += footprint
            # APPROXIMATION: import attributes retain the export generator's
            # conditioned draw; actual reversed handling policies are screened
            # below. No onward transshipment itinerary is supplied or inferred.
            imports = generate_load_list(template.rotation, template.call_index, min(local_imports, candidate_cap),
                                         seed=int(rng.integers(2**31)), id_offset=serial)
            serial += len(imports)
            imported, placements, occupied = [], [], set()
            imported_teu = 0
            slots = _ship_slots(template, deck_only=True, bottom_only=True)
            for box in imports:
                converted = box.model_copy(update={"pod": template.port.locode,
                                                   "pol": box.pod, "category": None})
                if freeze:
                    primary = box.dg.imdg_class if box.dg else None
                    if primary is not None:
                        origin = next((call for call in template.rotation.calls
                                       if call.index > template.call_index and call.locode == box.pod), None)
                        if origin is None:
                            raise ValueError("import POL must resolve to the drawn export's future POD call")
                        # Reverse the attribute draw's export leg for local
                        # imports, retaining the actual origin-call metadata.
                        # current_port supplies the actual destination policy;
                        # no intermediate transfer/onward itinerary is invented.
                        import_leg = Rotation(service=template.rotation.service, calls=[
                            origin.model_copy(update={"index": 0}),
                            template.rotation.calls[template.call_index].model_copy(update={
                                "index": 1, "locode": template.port.locode, "archetype": None}),
                        ])
                        refused = cargo_shipments.refused_handling_calls(
                            import_leg, 0, template.port.locode, primary, current_port=template.port)
                        if refused:
                            refused_ports = [import_leg.calls[index].locode for index in refused]
                            report.conditioning.append(
                                f"{service.id}/{ci}: refused import {box.id}: UN{box.dg.un_number}, "
                                f"primary {primary}, {box.iso_type}, {box.pod}->{template.port.locode}; "
                                f"refused handling ports {refused_ports}")
                            continue
                    converted = converted.model_copy(update={
                        "direct_delivery": template.port.requires_direct_delivery(primary)})
                    # Charge only imports that were accepted and placed. A 45'
                    # box uses 2.25 nominal TEU, while its slot uses two cells.
                    if imported_teu + box.length_ft / 20 > nominal_cap:
                        continue
                for slot in slots:
                    cells = set(cells_of_slot(slot))
                    if (box.length_ft == 20) != parse_slot(slot).is_20ft_bay or cells & occupied:
                        continue
                    if not all(getattr(next(c for c in template.vessel.cells if c.id == cid), f"accepts_{box.length_ft}") for cid in cells):
                        continue
                    # Create synthetic local imports from the task-2.5 cargo
                    # attribute draw. Destination changes invalidate the export
                    # category: never claim interchangeability across PODs.
                    imported.append(converted)
                    imported_teu += box.length_ft / 20
                    placements.append(Placement(slot=slot, container_id=box.id))
                    occupied |= cells
                    break
            report.conditioned_load_boxes += len(selected)
            report.conditioned_discharge_boxes += len(imported)
            if freeze and local_imports:
                report.conditioning.append(
                    f"{service.id}/{ci}: APPROXIMATION: local imports requested {local_imports}, "
                    f"emitted {len(imported)}; export-lane equipment, DG mix and POD-dependent "
                    "payload draws are reversed, not calibrated to inbound trade; export-leg "
                    "conditioning remains before actual import-leg policy screening")
            if n != len(selected) or local_imports != len(imported):
                report.conditioning.append(f"{service.id}/{ci}: local exports {n}->{len(selected)}; local imports {local_imports}->{len(imported)} (raw inbound {nd}, unsupported transfers {transfer_arrivals}); conservative vessel/accessible-slot cap")
            cutoff = arrival - parameters.gate_in.cutoff_before_arrival_s
            times = {}
            for box in selected:
                gate_bin = parameters.gate_in.bins[int(rng.choice(len(parameters.gate_in.bins), p=[b.share for b in parameters.gate_in.bins]))]
                times[box.id] = cutoff - float(rng.uniform(gate_bin.end_before_cutoff_s, gate_bin.start_before_cutoff_s))
            # An engine context is not a planning snapshot: exports may still be
            # outside the gate. Copying avoids falsely validating it as an Instance.
            provenance = template.provenance.model_copy(deep=True)
            if rule_freeze_v1_enabled():
                provenance.versions.update(
                    cargo_generator=cargo_generator.effective_cargo_generator_version(),
                    yard_simulator=effective_yard_simulator_version())
            inst = template.model_copy(update={"containers": selected + imported,
                     "provenance": provenance,
                     "load_list": sorted(b.id for b in selected), "discharge_list": sorted(b.id for b in imported),
                     "onboard": ShipState(vessel_id=template.vessel.id, placements=placements,
                                          tank_masses_kg=dict(template.onboard.tank_masses_kg)),
                     "yard": YardState(yard_id=template.port.yard.id)})
            calls.append(ShipCall(f"{service.id}-{ci}", inst, arrival, cutoff, times, service_s))
    return sorted(calls, key=lambda c: (c.arrival_s, c.id)), report


def _ship_slots(instance, *, deck_only=False, bottom_only=False):
    cells = {c.id: c for c in instance.vessel.cells}
    slots = set()
    for stack in instance.vessel.stacks:
        if deck_only and not stack.on_deck:
            continue
        for cell_id in stack.cell_ids[:1] if bottom_only else stack.cell_ids:
            slots.add(cell_id)
            address = parse_slot(cell_id)
            other = f"{address.bay + 2:03d}{address.row:02d}{address.tier:02d}"
            if other in cells:
                slots.add(f"{address.bay + 1:03d}{address.row:02d}{address.tier:02d}")
    positions = {cid: stack.on_deck for stack in instance.vessel.stacks for cid in stack.cell_ids}
    def slot_order(slot):
        address = parse_slot(slot)
        first = cells_of_slot(slot)[0]
        return positions[first], address.tier, address.bay, address.row
    return sorted(slots, key=slot_order)


def simulate_port(calls: list[ShipCall], parameters: SimulatorParameters, *, seed: int,
                  noise: float | None = None, dig_out: DigOutPolicy | None = None,
                  initial_yard: YardState | None = None, until_s: float | None = None,
                  congestion_mix_share: float = .25) -> SimulationResult:
    """Run a finite event calendar and return loading-start yards plus every lift.

    Noise overrides both independent error probabilities, useful for sensitivity
    studies. The synthetic congestion_mix_share is independent of those errors;
    noise zero disables mixing even with an explicit positive share. Its idealized
    separated-yard policy queues arrivals or reports unserved retrievals instead
    of mixing under capacity pressure. Failed arrivals wait externally and retry after a successful exit;
    blocked/unplaceable loads remain explicitly unserved. Input objects are pure.
    Initial inventory must already be registered among call containers; its moves
    are historical and are not invented in this run. No implicit warm-up is used:
    callers must sample a sufficiently early period and discard warm-up snapshots.
    """
    if noise is not None and (not math.isfinite(noise) or not 0 <= noise <= 1):
        raise ValueError("noise must be in [0, 1]")
    if not math.isfinite(congestion_mix_share) or not 0 <= congestion_mix_share <= 1:
        raise ValueError("congestion_mix_share must be finite and in [0, 1]")
    if not calls:
        result = _simulation_result(calls, parameters, seed=seed, noise=noise,
                                    congestion_mix_share=congestion_mix_share)
        result.final_yard = initial_yard.model_copy(deep=True) if initial_yard else None
        return result
    if len({call.id for call in calls}) != len(calls):
        raise ValueError("call ids must be unique")
    port = calls[0].instance.port
    freeze = rule_freeze_v1_enabled()
    if parameters.archetype != port.archetype or any(c.instance.port != port for c in calls):
        raise ValueError("all calls must share the parameterised port layout")
    registry, owners, deadlines = {}, {}, {}
    for call in calls:
        for box in call.instance.containers:
            if box.id in registry:
                raise ValueError("container ids must be disjoint between calls")
            registry[box.id] = box
            owners[box.id] = call.id
            deadlines[box.id] = call.arrival_s if box.id in call.instance.load_list else math.inf
    yard = initial_yard.model_copy(deep=True) if initial_yard else YardState(yard_id=port.yard.id)
    if yard.yard_id != port.yard.id or any(cid not in registry for s in yard.stacks for cid in s.boxes):
        raise ValueError("initial yard must match layout and registered inventory")
    if freeze:
        for stack in yard.stacks:
            block = parse_yard(stack.key + "-01").block
            if len(stack.boxes) > _yard_tiers(port, block):
                raise ValueError("initial yard stack exceeds the effective height limit")
            if any(registry[cid].direct_delivery for cid in stack.boxes):
                raise ValueError("initial yard cannot contain a direct-delivery box")
    contexts = {c.id: c.instance.model_copy(update={"containers": list(registry.values())}) for c in calls}
    ships = {c.id: c.instance.onboard.model_copy(deep=True) for c in calls}
    indexed = {c.id: c for c in calls}
    slots_by_call = {c.id: _ship_slots(c.instance) for c in calls}
    rng = Random(seed)
    result = _simulation_result(calls, parameters, seed=seed, noise=noise,
                                congestion_mix_share=congestion_mix_share)
    # Labelled independent stream: selecting D72 boxes consumes no clustering
    # or retrieval-order draws, preserving the same exogenous sorting noise in
    # a mix-share-zero control. Changed destinations can still change retries.
    mixing_rng = Random(f"{seed}:congestion-mixing:independent-v2") if freeze else None
    calendar, sequence = [], 0
    pending = []
    retained_exits = set()
    berths = [-math.inf] * parameters.berth_count
    pc = noise if noise is not None else parameters.placement.cluster_misplacement_probability
    po = noise if noise is not None else parameters.placement.retrieval_order_error_probability
    export_owners = {cid: call.id for call in calls for cid in call.instance.load_list}

    def separated_destination(state, cid, destination):
        return preserves_export_separation(state, cid, destination,
                                           export_owners=export_owners)

    # APPROXIMATION: idealized noise zero queues/refuses demand rather than
    # mixing under capacity pressure, including retrieval rehandles. A sorting
    # noise override of zero controls this promise independently of mix share.
    retrieval_destination = separated_destination if freeze and pc == 0 else None
    dig_out = dig_out or NearestStackDigOut(destination_predicate=retrieval_destination)
    mix_share = congestion_mix_share if pc > 0 else 0.0

    def event(time, priority, kind, call_id, cid=""):
        nonlocal sequence
        heapq.heappush(calendar, (time, priority, sequence, kind, call_id, cid))
        sequence += 1

    def world(call_id):
        return WorldState(call_index=contexts[call_id].call_index, yard=yard, ship=ships[call_id])

    def commit(call_id, move, now):
        nonlocal yard
        state = apply_move(contexts[call_id], world(call_id), move, move_index=len(result.moves))
        yard, ships[call_id] = state.yard, state.ship
        yard.time_s = now
        result.moves.append(TimedMove(now, call_id, move))

    def cluster(cid):
        box = registry[cid]
        values = {"vessel_call_id": owners[cid], "vessel_call": owners[cid], "vessel": owners[cid], "pod": box.pod,
                  "size_type": box.iso_type, "weight_class": box.weight_class,
                  "size/type": box.iso_type, "POD": box.pod}
        return tuple(values.get(key, getattr(box, key, None)) for key in parameters.placement.cluster_keys)

    export_ids = {cid for call in calls for cid in call.instance.load_list}
    mixing_selected: dict[str, bool] = {}
    mixed_boxes: set[str] = set()

    def choose(call_id, cid, now, *, discharge_src=None):
        candidates = destinations(contexts[call_id], world(call_id), cid)
        stacks = {s.key: s for s in yard.stacks}
        if not candidates:
            return False
        cluster_draw = rng.random()

        def prefix(dst):
            stack = stacks.get(dst.rsplit("-", 1)[0])
            if not stack:
                return 0
            old, new = cluster(stack.boxes[-1]), cluster(cid)
            count = 0
            for a, b in zip(old, new):
                if a != b:
                    break
                count += 1
            return count

        def clustered(options):
            if not options:
                return []
            best = max(map(prefix, options))
            return [dst for dst in options if prefix(dst) == best]

        mixed = []
        if freeze:
            # APPROXIMATION: D72's independent synthetic scenario share is
            # not a measured terminal rate. One labelled RNG stream selects
            # boxes without consuming clustering or retrieval-order draws.
            # Imports can bury their own call's exports. An export can bury
            # another call's exports, never count its own cluster as mixing.
            # Inspect the entire stack: an import may already hide the export.
            def foreign_export_stack(dst):
                if pc == 0:
                    # An unserved export still occupies its call's yard
                    # space. Idealized zero-noise separation must survive a
                    # failed load, just as it does during retrieval replay.
                    return not separated_destination(world(call_id), cid, dst)
                stack = stacks.get(dst.rsplit("-", 1)[0])
                return bool(stack and any(box in export_ids and box not in result.unserved_loads
                    and (cid not in export_ids or owners[box] != call_id)
                    for box in stack.boxes))

            mixed = [dst for dst in candidates if foreign_export_stack(dst)]
            unmixed = [dst for dst in candidates if not foreign_export_stack(dst)]
            counters = result.__dict__.setdefault("_congestion_mixing", {})
            counters["placement_attempts"] = counters.get("placement_attempts", 0) + 1
            if mixed:
                counters["eligible_placements"] = counters.get("eligible_placements", 0) + 1
                if cid not in mixing_selected:
                    # One synthetic coin per eligible box. Gate retries and
                    # repeated discharge passes cannot inflate its chance of
                    # being selected; keep consuming the normal cluster draw.
                    mixing_selected[cid] = mixing_rng.random() < mix_share
                    counters["eligible_boxes"] = counters.get("eligible_boxes", 0) + 1
                    if mixing_selected[cid]:
                        counters["mixing_selected_boxes"] = counters.get("mixing_selected_boxes", 0) + 1
            if mixed and mixing_selected[cid]:
                counters["mixing_attempts"] = counters.get("mixing_attempts", 0) + 1
            else:
                mixed = []
            # No unselected box may bury pending exports, including when
            # ordinary clustering would prefer the same call's export stack.
            # Lack of separate legal capacity queues/refuses the arrival;
            # synthetic congestion never grants a fallback mixing exception.
            # D25 keys govern unmixed placement unless the existing sorting
            # noise draw requests a clustering error, just as in the control.
            candidates = clustered(unmixed) if cluster_draw >= pc else unmixed
        elif cluster_draw >= pc:
            candidates = clustered(candidates)

        order_error = rng.random() < po

        def ordered(options):
            if order_error:
                rng.shuffle(options)
            else:
                def rank(dst):
                    stack = stacks.get(dst.rsplit("-", 1)[0])
                    # A later box on top delays an earlier one below it.
                    inversion = bool(stack and deadlines[stack.boxes[-1]] < deadlines[cid])
                    return inversion, len(stack.boxes) if stack else 0, dst
                options.sort(key=rank)
            return options

        # Every deliberate mixed placement must pass exactly the same storage
        # enumeration and engine replay as normal placement, including A8.
        # If every mixed destination is inaccessible, retain ordinary D25
        # storage rather than refusing a box solely for synthetic congestion.
        attempted = set()
        groups = [(True, mixed), (False, candidates)] if mixed else [(False, candidates)]
        for is_mixed, options in groups:
            for dst in ordered(options):
                if dst in attempted:
                    continue
                attempted.add(dst)
                move = (Discharge(call_index=contexts[call_id].call_index, container_id=cid, src=discharge_src, dst=dst)
                        if discharge_src else GateIn(call_index=contexts[call_id].call_index, container_id=cid, dst=dst))
                try:
                    commit(call_id, move, now)
                    if freeze and is_mixed:
                        counters["mixed_placements"] = counters.get("mixed_placements", 0) + 1
                        if cid not in mixed_boxes:
                            mixed_boxes.add(cid)
                            counters["mixed_boxes"] = len(mixed_boxes)
                    return True
                except IllegalMoveError:
                    continue
        return False

    def expose(call_id, cid, now):
        nonlocal yard
        retrieval = dig_out.retrieve(contexts[call_id], world(call_id), cid)
        # Hook output is always replayed by the public engine, even if a custom
        # policy returned an invalid state or invented an illegal relocation.
        for move in retrieval.moves:
            validate_relocation(contexts[call_id], world(call_id), move,
                                destination_predicate=retrieval_destination)
            commit(call_id, move, now)
        src = location(world(call_id), cid)
        if src is None:
            raise YardCapacityError("expert.moves.top_access: retrieved box absent")
        return src

    def retry(now):
        for entry in list(pending):
            call_id, cid = entry
            if now <= indexed[call_id].cutoff_s and choose(call_id, cid, now):
                pending.remove(entry)
        # Retry previously blocked import exits only after a lift freed capacity.
        # Removing before enqueue prevents duplicate events at the same timestamp.
        for owner, box in sorted(retained_exits):
            retained_exits.remove((owner, box))
            event(now, 4, "exit", owner, box)

    def cover_for_slot(call_id, slot, now):
        from stowage.model.moves import HatchCoverOn
        first = cells_of_slot(slot)[0]
        stack = next(s for s in contexts[call_id].vessel.stacks if first in s.cell_ids)
        cover = stack.hatch_cover_id
        if not cover:
            return
        if stack.on_deck and cover in ships[call_id].covers_off:
            commit(call_id, HatchCoverOn(call_index=contexts[call_id].call_index, cover_id=cover), now)
        elif not stack.on_deck and cover not in ships[call_id].covers_off:
            commit(call_id, HatchCoverOff(call_index=contexts[call_id].call_index, cover_id=cover), now)

    def prepared_load(call_id, cid, src, slot):
        """Preflight the complete cover/load transaction without side effects.

        The shared sequencing screen describes pending lifts; candidate slots
        may wait for another load, or for reachable covers to close, before a
        nearer deck stack is allowed to reach H (D69).
        """
        from stowage.model.moves import HatchCoverOn
        from stowage.moves import pending_lift_rows

        instance = contexts[call_id]
        state = world(call_id)
        cells = {cell.id: cell for cell in instance.vessel.cells}
        stacks = {stack.id: stack for stack in instance.vessel.stacks}
        footprint = cells_of_slot(slot)
        box = registry[cid]
        if not all(getattr(cells[cell], f"accepts_{box.length_ft}") for cell in footprint):
            return None, False
        members = {cells[cell].stack_id for cell in footprint}
        moves = []

        def stage(move):
            nonlocal state
            state = apply_move(instance, state, move)
            moves.append(move)

        # A rejected candidate must never leave a newly opened cover behind.
        for sid in sorted(members):
            stack = stacks[sid]
            cover = stack.hatch_cover_id
            if cover is None:
                continue
            if stack.on_deck and cover in state.ship.covers_off:
                stage(HatchCoverOn(call_index=instance.call_index, cover_id=cover))
            elif not stack.on_deck and cover not in state.ship.covers_off:
                stage(HatchCoverOff(call_index=instance.call_index, cover_id=cover))

        address = parse_slot(slot)
        height = instance.port.max_crane_height_tiers
        reaches_height = height is not None and any(
            stacks[cells[cell].stack_id].on_deck and
            stacks[cells[cell].stack_id].cell_ids.index(cell) + 1 >= height
            for cell in footprint)
        if reaches_height:
            # Future alternatives must be eventually usable after this load.
            # Retain temporary support/cover constraints and pending discharge
            # occupancies, but exclude staying cargo, this proposed footprint,
            # and deck positions above the crane's permanent height ceiling.
            permanent = set(footprint) | {cell for placement in state.ship.placements
                if placement.container_id not in instance.discharge_list
                for cell in cells_of_slot(placement.slot)}
            deck_limit = height  # Activated engine height replaces the compatibility field.
            eventual_slots = tuple(target for target in slots_by_call[call_id]
                if not permanent.intersection(cells_of_slot(target))
                and all(not stacks[cells[cell].stack_id].on_deck or
                        stacks[cells[cell].stack_id].cell_ids.index(cell) + 1 <= deck_limit
                        for cell in cells_of_slot(target)))
            targets = {box_id: tuple(target for target in eventual_slots
                if (registry[box_id].length_ft == 20) == parse_slot(target).is_20ft_bay
                and all(getattr(cells[cell], f"accepts_{registry[box_id].length_ft}")
                        for cell in cells_of_slot(target))) for box_id in instance.load_list
                if box_id not in result.unserved_loads}

            def pending_rows():
                return pending_lift_rows(instance, state, address.bay, address.row,
                                         pending_load_targets=targets, placing_container_id=cid)

            if pending_rows():
                # Independent open-cover obligations can be finished now.
                # Use engine replay for legality; no copied crossing geometry.
                for cover in sorted(state.ship.covers_off):
                    try:
                        stage(HatchCoverOn(call_index=instance.call_index, cover_id=cover))
                    except IllegalMoveError:
                        continue
                if pending_rows():
                    return None, True
        stage(Load(call_index=instance.call_index, container_id=cid, src=src, dst=slot))
        return moves, False

    for call in sorted(calls, key=lambda c: c.id):
        for cid, time in sorted(call.gate_in_times.items()):
            # APPROXIMATION: direct-delivery trucks are available at their lift;
            # their synthetic gate times never create a yard placement (A4).
            if freeze and registry[cid].direct_delivery:
                continue
            if location(world(call.id), cid) is None:
                event(time, 0, "gate", call.id, cid)
        # Gate acceptance is inclusive: settle every same-time release/retry
        # (including import exits) before declaring a pending box late.
        event(call.cutoff_s, 9, "cutoff", call.id)
        event(call.arrival_s, 2, "arrive", call.id)
    while calendar:
        now, _, _, kind, call_id, cid = heapq.heappop(calendar)
        if until_s is not None and now > until_s:
            break
        yard.time_s = now
        call, instance = indexed[call_id], contexts[call_id]
        if kind == "gate":
            if not choose(call_id, cid, now):
                pending.append((call_id, cid))
                result.queued_gate_events += 1
        elif kind == "cutoff":
            result.missed_cutoffs.extend(box for owner, box in pending if owner == call_id)
        elif kind == "arrive":
            berth = min(range(len(berths)), key=lambda i: (berths[i], i))
            start = max(now, berths[berth])
            berths[berth] = start + call.service_s
            result.berth_wait_s[call_id] = start - now
            event(start, 3, "start", call_id)
        elif kind == "exit":
            retained_exits.discard((call_id, cid))
            try:
                src = expose(call_id, cid, now)
                commit(call_id, GateOut(call_index=instance.call_index, container_id=cid, src=src), now)
                retry(now)
            except YardCapacityError:
                # No fictitious lift or hourly busy-loop: retain explicitly.
                retained_exits.add((call_id, cid))
        elif kind == "start":
            # Bay-string order is not an access order: a spanning upper box
            # or a deck box over a shared hatch can block another bay. Revisit
            # remaining imports after every productive pass; only a complete
            # pass without progress establishes that they are unserved.
            remaining = [p for p in sorted(ships[call_id].placements, key=lambda p: p.slot, reverse=True)
                         if p.container_id in instance.discharge_list]
            while remaining:
                progress = False
                for placement in list(remaining):
                    cid = placement.container_id
                    # One stream per box: retries and placement noise cannot
                    # change exogenous dwell or another import's traffic.
                    dwell_rng = Random(f"{seed}:import-dwell:{cid}")
                    dwell = dwell_rng.gammavariate(1 / parameters.dwell.import_cv**2,
                                                  parameters.dwell.import_mean_s * parameters.dwell.import_cv**2)
                    deadlines[cid] = now + dwell
                    try:
                        cover_for_slot(call_id, placement.slot, now)
                        if freeze and registry[cid].direct_delivery:
                            commit(call_id, Discharge(call_index=instance.call_index, container_id=cid,
                                                      src=placement.slot, dst="truck"), now)
                            ok = True
                        else:
                            ok = choose(call_id, cid, now, discharge_src=placement.slot)
                    except IllegalMoveError:
                        ok = False
                    if ok:
                        if not (freeze and registry[cid].direct_delivery):
                            event(now + dwell, 4, "exit", call_id, cid)
                        remaining.remove(placement)
                        progress = True
                if not progress:
                    result.unserved_discharges.extend(p.container_id for p in remaining)
                    break
            result.snapshots.append(LoadingSnapshot(call_id, now, yard.model_copy(deep=True), len(result.moves),
                                                   yard_statistics(instance, yard, instance.load_list)))
            load_order = list(call.load_order or tuple(instance.load_list))
            deferred_at, completed_loads = {}, 0
            for cid in load_order:
                box = registry[cid]
                if freeze and box.direct_delivery:
                    src = "truck"
                else:
                    if location(world(call_id), cid) is None:
                        result.unserved_loads.append(cid)
                        continue
                    try:
                        src = expose(call_id, cid, now)
                    except YardCapacityError:
                        result.unserved_loads.append(cid)
                        continue
                slots = slots_by_call[call_id]
                if box.length_ft == 20:
                    occupied = {cell for p in ships[call_id].placements for cell in cells_of_slot(p.slot)}
                    cells = {cell.id: cell for cell in instance.vessel.cells}
                    later_lengths = {registry[b].length_ft for b in (call.load_order or tuple(instance.load_list))
                                     if registry[b].length_ft != 20 and
                                     (location(world(call_id), b) is not None or
                                      (freeze and registry[b].direct_delivery))}
                    intact = []
                    for long_slot in slots:
                        if parse_slot(long_slot).is_20ft_bay:
                            continue
                        footprint = set(cells_of_slot(long_slot))
                        if not footprint & occupied and any(all(getattr(cells[c], f"accepts_{length}")
                                                                       for c in footprint) for length in later_lengths):
                            intact.append(footprint)
                    # Best fit: fill a partially occupied long pair before
                    # destroying another intact pair needed by pending long boxes.
                    base_order = {slot: i for i, slot in enumerate(slots)}
                    slots = sorted(slots, key=lambda slot: (sum(bool(set(cells_of_slot(slot)) & pair)
                                                                for pair in intact), base_order[slot]))
                deferred = False
                for slot in slots:
                    if (box.length_ft == 20) != parse_slot(slot).is_20ft_bay:
                        continue
                    try:
                        if freeze:
                            moves, waits = prepared_load(call_id, cid, src, slot)
                            deferred |= waits
                            if moves is None:
                                continue
                            for move in moves:
                                commit(call_id, move, now)
                        else:
                            cover_for_slot(call_id, slot, now)
                            commit(call_id, Load(call_index=instance.call_index, container_id=cid, src=src, dst=slot), now)
                        completed_loads += 1
                        retry(now)
                        break
                    except IllegalMoveError:
                        continue
                else:
                    if freeze and deferred and deferred_at.get(cid) != completed_loads:
                        # Farther obligations receive their turn first. Retry
                        # only after progress, so inaccessible cargo terminates.
                        deferred_at[cid] = completed_loads
                        load_order.append(cid)
                    else:
                        result.unserved_loads.append(cid)
            from stowage.model.moves import HatchCoverOn
            for cover_id in sorted(ships[call_id].covers_off):
                try:
                    commit(call_id, HatchCoverOn(call_index=instance.call_index, cover_id=cover_id), now)
                except IllegalMoveError as error:
                    if not freeze:
                        raise
                    result.cover_diagnostics.append(
                        f"{call_id}/{cover_id}: {error.violation.rule_id}: {error.violation.message}")
    result.final_yard = yard.model_copy(deep=True)
    result.external_queue = sorted(cid for _, cid in pending)
    result.missed_cutoffs.sort()
    result.unserved_loads.sort()
    result.unserved_discharges.sort()
    result.retained_import_exits = sorted(cid for _, cid in retained_exits)
    result.final_ships = {key: ship.model_copy(deep=True) for key, ship in sorted(ships.items())}
    if freeze:
        mixing = result.provenance.congestion_mixing
        mixing["achieved_share"] = (mixing["mixed_boxes"] / mixing["eligible_boxes"]
                                    if mixing["eligible_boxes"] else 0.0)
    return result
