"""Deterministic D185 port blocking, outside the pinned planner implementation.

Medium cargo uses whole transverse hatch stations: port and starboard covers
with the same physical bay span always have identical permissions. Stations are
ordered from fore to aft (x is forward of AP), and contiguous station runs are
assigned in future discharge order. Small slices have just one station and two
side covers, so their original one-port-per-cover rule is retained explicitly.

Allocation uses manifest TEU and free geometric cells only. It is a necessary
capacity heuristic, not a packing witness or a feasibility/checker certificate.
Headroom's scoped placement screens and independent audits remain mandatory.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from experiments.mvp.strategy.headroom.layout import BlockLayout, BlockModel, booking_category
from stowage.conventions import cells_of_slot


def _ordered_ports(instance, boxes):
    calls = sorted(instance.rotation.calls, key=lambda call: call.index)
    rank = {}
    for call in calls:
        if call.index > instance.call_index:
            rank.setdefault(str(call.locode), call.index)
    return sorted({str(box.pod) for box in boxes}, key=lambda pod: (rank.get(pod, float("inf")), pod))


def _stations(instance, model, *, pair_sides):
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    by_cover = defaultdict(list)
    for block in model.blocks:
        by_cover[block.hatch_group].append(block)
    grouped = defaultdict(list)
    for cover, blocks in by_cover.items():
        bays = tuple(sorted({stacks[sid].bay for block in blocks for sid in block.stack_ids}))
        # Use geometry rather than parsing cover IDs (which are free text).
        key = ("span", bays) if pair_sides else ("cover", cover)
        grouped[key].extend(blocks)
    result = []
    for blocks in grouped.values():
        ids = sorted({cid for block in blocks for cid in block.cell_ids})
        x = sum(float(cells[cid].lcg_m) for cid in ids) / len(ids)
        result.append(dict(covers=sorted({block.hatch_group for block in blocks}),
                           blocks=sorted(block.id for block in blocks), cells=ids,
                           lcg_m=x))
    return sorted(result, key=lambda station: (-station["lcg_m"], station["covers"]))


def _partition(capacities, demands):
    """Contiguous nonempty runs, minimizing shortages then capacity mismatch.

The tiny dynamic program examines every boundary, without solving or looking at
outcomes. Lexicographic shortage priority avoids sacrificing a feasible TEU split
for prettier proportional balance. Every station receives exactly one POD.
"""
    count, ports = len(capacities), len(demands)
    if ports > count:
        return None
    total_capacity = max(sum(capacities), 1)
    total_demand = max(sum(demands), 1)
    prefix = [0]
    for capacity in capacities:
        prefix.append(prefix[-1] + capacity)

    @lru_cache(None)
    def solve(start, port):
        if port == ports:
            return ((0, 0.), ()) if start == count else None
        best = None
        for stop in range(start + 1, count - (ports - port - 1) + 1):
            tail = solve(stop, port + 1)
            if tail is None:
                continue
            capacity = prefix[stop] - prefix[start]
            shortfall = max(0, demands[port] - capacity)
            mismatch = (capacity / total_capacity - demands[port] / total_demand) ** 2
            candidate = ((shortfall + tail[0][0], mismatch + tail[0][1]),
                         (stop,) + tail[1])
            if best is None or candidate < best:
                best = candidate
        return best

    value = solve(0, 0)
    start, runs = 0, []
    for stop in value[1]:
        runs.append(tuple(range(start, stop)))
        start = stop
    return runs


def port_blocking_layout(instance, model=None):
    """Return a headroom-compatible layout; inspect metadata for rule limitations.

No optimization feedback, model output, validation data or source mutation is
used. If there are fewer longitudinal stations than PODs, adjacent PODs share a
station in discharge order; this explicitly recorded softening preserves the
no-side-split property on medium instead of inventing unavailable separation.
"""
    model = model or BlockModel.from_instance(instance)
    load_ids = set(instance.load_list)
    boxes = [box for box in instance.containers if box.id in load_ids]
    ports = _ordered_ports(instance, boxes)
    pair_sides = instance.size_class == "medium"
    stations = _stations(instance, model, pair_sides=pair_sides)
    retained_cells = set()
    discharges = set(instance.discharge_list)
    for placement in instance.onboard.placements:
        if placement.container_id not in discharges:
            retained_cells.update(cells_of_slot(placement.slot))
    capacities = [sum(cid not in retained_cells for cid in station["cells"]) for station in stations]
    demand = {pod: sum(1 if box.length_ft == 20 else 2 for box in boxes if str(box.pod) == pod)
              for pod in ports}
    runs = _partition(capacities, [demand[pod] for pod in ports]) if ports else []
    shared = runs is None
    if shared:
        # Monotone proportional bins: multiple PODs may share the same station,
        # but every permission still spans the complete ship width at that x.
        runs = [(min(len(stations) - 1, i * len(stations) // len(ports)),)
                for i in range(len(ports))] if stations else [() for _ in ports]
    pod_blocks = {pod: frozenset(bid for i in run for bid in stations[i]["blocks"])
                  for pod, run in zip(ports, runs)}
    allowed = {booking_category(box, instance): pod_blocks[str(box.pod)] for box in boxes}
    assignments = [dict(pod=pod, demand_teu=demand[pod], station_indices=list(run),
                        capacity_free_cells=sum(capacities[i] for i in run),
                        blocks=sorted(pod_blocks[pod])) for pod, run in zip(ports, runs)]
    return BlockLayout(allowed, "port-blocking-fore-aft-v1" if pair_sides else "port-blocking-cover-v1",
                       dict(rule_version=1, source="D185 fixed manifest/geometry rule, train-only tuning",
                            direction="fore-to-aft; decreasing x forward of AP",
                            port_order=ports, paired_transverse_covers=pair_sides,
                            lateral_split_possible=not pair_sides,
                            shared_station_due_to_short_slice=shared,
                            static_packing_witness=False,
                            stations=[{**station, "capacity_free_cells": capacity}
                                      for station, capacity in zip(stations, capacities)],
                            assignments=assignments))


# Short alias for callers that already label their arm "rule".
rule_layout = port_blocking_layout
