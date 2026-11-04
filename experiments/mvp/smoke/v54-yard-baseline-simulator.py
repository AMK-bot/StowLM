"""Event-driven synthetic port yard, with reproducible call and gate schedules.

An event advances time directly to the next gate arrival, ship or import exit;
no empty time steps are simulated. Placement follows the ordered clustering and
retrieval preferences in params/ports/README.md (task 1.5). Noise independently
ignores clustering and retrieval order, never legality. The move engine approves
every lift; these snapshots are planning inputs, never checker-approved plans.

APPROXIMATION: each berth serves a call for a caller-supplied fixed duration;
individual lifts occur at loading start without a crane travel/service model.
APPROXIMATION: other ships load in their explicit load order into the first legal
length-compatible slot, preferring intact long-box pairs and hold-before-deck
order, without solving global packing, weights or future port restows. Unserved loads/discharges are reported, never certified.
APPROXIMATION: workload volume is a negative-binomial synthetic demand model,
not a measured terminal distribution. Conditional volume caps are reported.
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

import numpy as np

from stowage.conventions import cells_of_slot, parse_slot
from stowage.generate.cargo import generate_load_list
from stowage.model import Instance
from stowage.model.moves import Discharge, GateIn, GateOut, HatchCoverOff, Load, Move
from stowage.model.ship import Placement, ShipState, WorldState
from stowage.model.yard import YardState
from stowage.moves.engine import IllegalMoveError, apply_move
from stowage.ports.profiles import SimulatorParameters, effective_yard_tiers
from stowage.yard.dig_out import DigOutPolicy, NearestStackDigOut, YardCapacityError, destinations, location

SIMULATOR_VERSION = "1.0.0"


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
    simulator_version: str = SIMULATOR_VERSION


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
    capacity = sum(b.n_bays * b.n_rows * effective_yard_tiers(instance.port, b)
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


def sample_ship_calls(template: Instance, parameters: SimulatorParameters, *, days: float,
                      seed: int, volume_scale: float = 1.0, service_s: float = 3600,
                      id_offset: int = 0) -> tuple[list[ShipCall], WorkloadReport]:
    """Sample weekly services, independent counts, gate bins and import cargo.

    Cargo is drawn by task 2.5. Export counts are conservatively capped by nominal
    vessel TEU, then each generated list is shortened to its TEU budget. Imports
    are conditioned on reachable first-tier deck slots and actual length pairs.
    These are explicit upper-capacity screens, not claims of true feasibility.
    The caller can sample warm-up weeks by shifting returned times together.
    """
    if days <= 0 or not math.isfinite(days) or volume_scale <= 0 or not math.isfinite(volume_scale):
        raise ValueError("days and volume_scale must be positive and finite")
    if parameters.archetype != template.port.archetype:
        raise ValueError("simulator parameters must match the port archetype")
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
        for ci in range(math.ceil(days * 86400 / interval) + 1):
            arrival = phase + ci * interval + rng.uniform(-service.arrival_jitter_s, service.arrival_jitter_s)
            if not 0 <= arrival < days * 86400:
                continue
            n = count(service.mean_load_boxes, service.call_volume_cv)
            nd = count(service.mean_discharge_boxes, service.call_volume_cv)
            report.raw_load_boxes += n
            report.raw_discharge_boxes += nd
            # Preserve the specified inbound transfer fraction without falsely
            # turning those boxes into local imports. A separate per-call RNG
            # leaves export/gate sampling streams unchanged.
            inbound_rng = np.random.default_rng(np.random.SeedSequence([seed, si, ci, 3405]))
            transfer_arrivals = int(inbound_rng.binomial(nd, parameters.traffic.transshipment_fraction))
            report.unserved_transfer_arrivals += transfer_arrivals
            local_imports = nd - transfer_arrivals
            if transfer_arrivals:
                report.conditioning.append(f"{service.id}/{ci}: {transfer_arrivals} inbound transfer arrivals unsupported by intermediate-discharge contract; retained as unserved flow demand")
            transfer = int(rng.binomial(n, parameters.traffic.transshipment_fraction))
            report.unserved_transfer_demand += transfer
            n -= transfer
            limit = min(n, template.vessel.nominal_teu)
            cargo = generate_load_list(template.rotation, template.call_index, limit,
                                       seed=int(rng.integers(2**31)), id_offset=serial)
            serial += len(cargo)
            selected, teu = [], 0
            for box in cargo:
                if teu + box.length_ft / 20 > template.vessel.nominal_teu:
                    break
                selected.append(box)
                teu += box.length_ft / 20
            imports = generate_load_list(template.rotation, template.call_index, min(local_imports, template.vessel.nominal_teu),
                                         seed=int(rng.integers(2**31)), id_offset=serial)
            serial += len(imports)
            imported, placements, occupied = [], [], set()
            slots = _ship_slots(template, deck_only=True, bottom_only=True)
            for box in imports:
                for slot in slots:
                    cells = set(cells_of_slot(slot))
                    if (box.length_ft == 20) != parse_slot(slot).is_20ft_bay or cells & occupied:
                        continue
                    if not all(getattr(next(c for c in template.vessel.cells if c.id == cid), f"accepts_{box.length_ft}") for cid in cells):
                        continue
                    # Create synthetic local imports from the task-2.5 cargo
                    # attribute draw. Destination changes invalidate the export
                    # category: never claim interchangeability across PODs.
                    imported.append(box.model_copy(update={"pod": template.port.locode,
                                                            "pol": box.pod, "category": None}))
                    placements.append(Placement(slot=slot, container_id=box.id))
                    occupied |= cells
                    break
            report.conditioned_load_boxes += len(selected)
            report.conditioned_discharge_boxes += len(imported)
            if n != len(selected) or local_imports != len(imported):
                report.conditioning.append(f"{service.id}/{ci}: local exports {n}->{len(selected)}; local imports {local_imports}->{len(imported)} (raw inbound {nd}, unsupported transfers {transfer_arrivals}); conservative vessel/accessible-slot cap")
            cutoff = arrival - parameters.gate_in.cutoff_before_arrival_s
            times = {}
            for box in selected:
                gate_bin = parameters.gate_in.bins[int(rng.choice(len(parameters.gate_in.bins), p=[b.share for b in parameters.gate_in.bins]))]
                times[box.id] = cutoff - float(rng.uniform(gate_bin.end_before_cutoff_s, gate_bin.start_before_cutoff_s))
            # An engine context is not a planning snapshot: exports may still be
            # outside the gate. Copying avoids falsely validating it as an Instance.
            inst = template.model_copy(update={"containers": selected + imported,
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
                  initial_yard: YardState | None = None, until_s: float | None = None) -> SimulationResult:
    """Run a finite event calendar and return loading-start yards plus every lift.

    Noise overrides both independent error probabilities, useful for sensitivity
    studies. Failed arrivals wait externally and retry after a successful exit;
    blocked/unplaceable loads remain explicitly unserved. Input objects are pure.
    Initial inventory must already be registered among call containers; its moves
    are historical and are not invented in this run. No implicit warm-up is used:
    callers must sample a sufficiently early period and discard warm-up snapshots.
    """
    if noise is not None and (not math.isfinite(noise) or not 0 <= noise <= 1):
        raise ValueError("noise must be in [0, 1]")
    if not calls:
        return SimulationResult(final_yard=initial_yard.model_copy(deep=True) if initial_yard else None, seed=seed)
    if len({call.id for call in calls}) != len(calls):
        raise ValueError("call ids must be unique")
    port = calls[0].instance.port
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
    contexts = {c.id: c.instance.model_copy(update={"containers": list(registry.values())}) for c in calls}
    ships = {c.id: c.instance.onboard.model_copy(deep=True) for c in calls}
    indexed = {c.id: c for c in calls}
    slots_by_call = {c.id: _ship_slots(c.instance) for c in calls}
    rng = Random(seed)
    result = SimulationResult(seed=seed)
    dig_out = dig_out or NearestStackDigOut()
    calendar, sequence = [], 0
    pending = []
    retained_exits = set()
    berths = [-math.inf] * parameters.berth_count
    pc = noise if noise is not None else parameters.placement.cluster_misplacement_probability
    po = noise if noise is not None else parameters.placement.retrieval_order_error_probability

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

    def choose(call_id, cid, now, *, discharge_src=None):
        candidates = destinations(contexts[call_id], world(call_id), cid)
        stacks = {s.key: s for s in yard.stacks}
        if not candidates:
            return False
        if rng.random() >= pc:
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
            best = max(map(prefix, candidates))
            candidates = [dst for dst in candidates if prefix(dst) == best]
        if rng.random() < po:
            rng.shuffle(candidates)
        else:
            def rank(dst):
                stack = stacks.get(dst.rsplit("-", 1)[0])
                # A later box on top delays an earlier one below it.
                inversion = bool(stack and deadlines[stack.boxes[-1]] < deadlines[cid])
                return inversion, len(stack.boxes) if stack else 0, dst
            candidates.sort(key=rank)
        for dst in candidates:
            move = (Discharge(call_index=contexts[call_id].call_index, container_id=cid, src=discharge_src, dst=dst)
                    if discharge_src else GateIn(call_index=contexts[call_id].call_index, container_id=cid, dst=dst))
            try:
                commit(call_id, move, now)
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

    for call in sorted(calls, key=lambda c: c.id):
        for cid, time in sorted(call.gate_in_times.items()):
            if location(world(call.id), cid) is None:
                event(time, 0, "gate", call.id, cid)
        event(call.cutoff_s, 1, "cutoff", call.id)
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
            # Retrieve imports in accessible order; through cargo is never
            # silently removed. Unreachable discharges remain aboard.
            for placement in sorted(list(ships[call_id].placements), key=lambda p: p.slot, reverse=True):
                if placement.container_id not in instance.discharge_list:
                    continue
                cid = placement.container_id
                # One stream per box: placement noise cannot change dwell or
                # cause later imports to receive different exogenous traffic.
                dwell_rng = Random(f"{seed}:import-dwell:{cid}")
                dwell = dwell_rng.gammavariate(1 / parameters.dwell.import_cv**2,
                                              parameters.dwell.import_mean_s * parameters.dwell.import_cv**2)
                deadlines[cid] = now + dwell
                try:
                    cover_for_slot(call_id, placement.slot, now)
                    ok = choose(call_id, cid, now, discharge_src=placement.slot)
                except IllegalMoveError:
                    ok = False
                if ok:
                    event(now + dwell, 4, "exit", call_id, cid)
                else:
                    result.unserved_discharges.append(cid)
            result.snapshots.append(LoadingSnapshot(call_id, now, yard.model_copy(deep=True), len(result.moves),
                                                   yard_statistics(instance, yard, instance.load_list)))
            for cid in call.load_order or tuple(instance.load_list):
                if location(world(call_id), cid) is None:
                    result.unserved_loads.append(cid)
                    continue
                try:
                    src = expose(call_id, cid, now)
                except YardCapacityError:
                    result.unserved_loads.append(cid)
                    continue
                box = registry[cid]
                slots = slots_by_call[call_id]
                if box.length_ft == 20:
                    occupied = {cell for p in ships[call_id].placements for cell in cells_of_slot(p.slot)}
                    cells = {cell.id: cell for cell in instance.vessel.cells}
                    later_lengths = {registry[b].length_ft for b in (call.load_order or tuple(instance.load_list))
                                     if registry[b].length_ft != 20 and location(world(call_id), b) is not None}
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
                for slot in slots:
                    if (box.length_ft == 20) != parse_slot(slot).is_20ft_bay:
                        continue
                    try:
                        cover_for_slot(call_id, slot, now)
                        commit(call_id, Load(call_index=instance.call_index, container_id=cid, src=src, dst=slot), now)
                        retry(now)
                        break
                    except IllegalMoveError:
                        continue
                else:
                    result.unserved_loads.append(cid)
            from stowage.model.moves import HatchCoverOn
            for cover_id in sorted(ships[call_id].covers_off):
                commit(call_id, HatchCoverOn(call_index=instance.call_index, cover_id=cover_id), now)
    result.final_yard = yard.model_copy(deep=True)
    result.external_queue = sorted(cid for _, cid in pending)
    result.missed_cutoffs.sort()
    result.unserved_loads.sort()
    result.unserved_discharges.sort()
    result.retained_import_exits = sorted(cid for _, cid in retained_exits)
    result.final_ships = {key: ship.model_copy(deep=True) for key, ship in sorted(ships.items())}
    return result
