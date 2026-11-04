"""Optional TRAIN-only v2 proposal; NOT imported by the pinned active study.

Keep strict v1 whenever its aggregate TEU capacity screen succeeds. Otherwise
share the smallest adjacent boundary station that supplies aggregate TEU flow.
All permissions remain contiguous and fore-aft monotone; a shared station keeps
its complete port/starboard, hold/deck geometry. This is a capacity relaxation,
not a physical packing witness, finisher repair, or checker certificate.
"""
from __future__ import annotations

from collections import deque
from itertools import product

from frozen_frontier.strategy_rule import port_blocking_layout
from frozen_frontier.headroom_layout import BlockLayout


def teu_flow(capacities, demands, runs):
    """Integral max-flow over POD -> allowed station -> free-cell capacity.

TEU can split here. Real 40/45-foot boxes cannot; this deliberately weaker
screen must not be called a packing certificate. No stowage solve is run.
"""
    ports, stations = len(demands), len(capacities)
    source, sink = ports + stations, ports + stations + 1
    residual = [dict() for _ in range(sink + 1)]

    def edge(a, b, capacity):
        residual[a][b] = capacity
        residual[b][a] = 0

    for pod, demand in enumerate(demands):
        edge(source, pod, demand)
        for station in runs[pod]:
            edge(pod, ports + station, demand)
    for station, capacity in enumerate(capacities):
        edge(ports + station, sink, capacity)
    total = 0
    while True:
        parent = {source: None}
        queue = deque([source])
        while queue and sink not in parent:
            a = queue.popleft()
            for b, capacity in sorted(residual[a].items()):
                if capacity > 0 and b not in parent:
                    parent[b] = a
                    queue.append(b)
        if sink not in parent:
            break
        amount, b = sum(demands), sink
        while parent[b] is not None:
            a = parent[b]
            amount = min(amount, residual[a][b])
            b = a
        b = sink
        while parent[b] is not None:
            a = parent[b]
            residual[a][b] -= amount
            residual[b][a] += amount
            b = a
        total += amount
    allocation = [[residual[ports + station].get(pod, 0) for station in range(stations)]
                  for pod in range(ports)]
    return dict(feasible=total == sum(demands), assigned_teu=total,
                demand_teu=sum(demands), station_allocation_teu=allocation,
                certificate_kind="aggregate TEU flow only; not physical box packing")


def boundary_relaxation(capacities, demands, runs):
    """At most one shared whole station per adjacent POD boundary.

Return unchanged runs when already TEU-feasible; otherwise minimize shared
station count, then added permission cells, then the deterministic mask itself.
Each boundary chooses no change, earlier POD borrows later POD's first station,
or later POD borrows earlier POD's last station. No non-adjacent PODs may overlap.
"""
    original = tuple(tuple(run) for run in runs)
    baseline = teu_flow(capacities, demands, original)
    if baseline["feasible"]:
        return original, baseline
    if not original or any(not run for run in original):
        return None
    best = None
    for choices in product((0, 1, 2), repeat=len(original) - 1):
        candidate = [set(run) for run in original]
        for boundary, choice in enumerate(choices):
            if choice == 1:
                candidate[boundary].add(min(original[boundary + 1]))
            elif choice == 2:
                candidate[boundary + 1].add(max(original[boundary]))
        ordered = tuple(tuple(sorted(run)) for run in candidate)
        if any(run != tuple(range(min(run), max(run) + 1)) for run in ordered):
            continue
        if any(min(a) > min(b) or max(a) > max(b) for a, b in zip(ordered, ordered[1:])):
            continue
        if any(len(candidate[a] & candidate[b]) > (1 if b == a + 1 else 0)
               for a in range(len(candidate)) for b in range(a + 1, len(candidate))):
            continue
        flow = teu_flow(capacities, demands, ordered)
        if not flow["feasible"]:
            continue
        added = [candidate[i] - set(original[i]) for i in range(len(original))]
        score = (sum(len(stations) for stations in added),
                 sum(capacities[station] for stations in added for station in stations), ordered)
        if best is None or score < best[0]:
            best = score, ordered, flow
    return (best[1], best[2]) if best else None


def capacity_relaxed_layout(instance, model=None):
    """Proposed v2 entry point; leaves a TEU-feasible v1 layout exactly intact.

No current study imports this candidate. Adoption requires an explicitly new
train campaign/source epoch, followed by the single still-unread validation run.
"""
    base = port_blocking_layout(instance, model=model)
    if instance.size_class != "medium":
        return base
    meta = base.metadata
    capacities = [station["capacity_free_cells"] for station in meta["stations"]]
    assignments = meta["assignments"]
    demands = [assignment["demand_teu"] for assignment in assignments]
    original = [assignment["station_indices"] for assignment in assignments]
    if teu_flow(capacities, demands, original)["feasible"]:
        return base
    relaxed = boundary_relaxation(capacities, demands, original)
    if relaxed is None:
        return BlockLayout(base.allowed, base.name, dict(meta, capacity_candidate=dict(
            status="no adjacent-boundary TEU-feasible relaxation; retain v1/fallback",
            candidate_only=True)))
    runs, flow = relaxed
    updated = []
    pod_blocks = {}
    for old, run in zip(assignments, runs):
        blocks = sorted({block for station in run for block in meta["stations"][station]["blocks"]})
        pod_blocks[old["pod"]] = frozenset(blocks)
        updated.append(dict(old, station_indices=list(run), blocks=blocks,
                            capacity_free_cells=sum(capacities[station] for station in run)))
    shared = [station for station in range(len(capacities))
              if sum(station in run for run in runs) > 1]
    return BlockLayout({category: pod_blocks[category[0]] for category in base.allowed},
                       "port-blocking-boundary-shared-v2-candidate",
                       dict(meta, rule_version=2, assignments=updated,
                            capacity_candidate=dict(candidate_only=True, status="TEU-screened boundary relaxation",
                                strict_v1_assignments=assignments,
                                shared_station_indices=shared, aggregate_teu_flow=flow,
                                pure_one_port_per_station=False)))
