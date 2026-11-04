"""Optimistic static-domain Hall screens, never an open-slot-mask screen.

``analyze(instance, tables=problem.engine.T, assignment=prefix)`` returns plain
JSON data.  At the root, only required load-list boxes are demanded and all
other cargo is ignored.  A deficit therefore rules out the original map under
the native static-domain contract.  With an assignment, it rules out ONLY an
extension preserving those committed placements. Passing proves neither.

We deliberately let a long box split its two cell units between ANY cells in
the union of all its compatible footprints. This is a relaxation: the units
need not form a real footprint, use one tier, or have support.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from time import monotonic

import numpy as np
from ortools.graph.python import max_flow


def hall_flow(box_ids, resource_ids, domains, demands, capacities):
    """Integer capacitated Hall test with an independently checkable cut.

    A cut names a subset X of boxes and ALL its neighboring resources N(X),
    and checks sum(demand[X]) > sum(capacity[N(X)]). Edges carry total+1,
    so no minimum cut can cross a box-resource edge. No solver status alone
    is accepted as a certificate.
    """
    nb, nr = len(box_ids), len(resource_ids)
    total = sum(map(int, demands))
    if len(domains) != nb or len(demands) != nb or len(capacities) != nr:
        raise ValueError("Hall dimensions do not agree")
    if any(int(x) != x or x < 0 for x in list(demands) + list(capacities)):
        raise ValueError("Hall capacities and demands must be nonnegative integers")
    graph = max_flow.SimpleMaxFlow()
    source, sink = nb + nr, nb + nr + 1
    for bi, demand in enumerate(demands):
        graph.add_arc_with_capacity(source, bi, int(demand))
        for ri in sorted(set(domains[bi])):
            if not 0 <= ri < nr:
                raise ValueError("Hall domain resource outside its universe")
            graph.add_arc_with_capacity(bi, nb + ri, total + 1)
    for ri, cap in enumerate(capacities):
        graph.add_arc_with_capacity(nb + ri, sink, int(cap))
    status = graph.solve(source, sink)
    if status != graph.OPTIMAL:
        return dict(status="unknown", solver_status=int(status), demand=total)
    flow = int(graph.optimal_flow())
    result = dict(status="no_deficit" if flow == total else "deficit",
                  demand=total, flow=flow, shortfall=total-flow,
                  total_capacity=sum(map(int, capacities)))
    if flow < total:
        source_side = set(graph.get_source_side_min_cut())
        selected = [bi for bi in range(nb) if bi in source_side]
        neighbors = sorted({ri for bi in selected for ri in domains[bi]})
        need = sum(int(demands[bi]) for bi in selected)
        have = sum(int(capacities[ri]) for ri in neighbors)
        if not need > have:
            raise RuntimeError("Max-flow cut did not verify as a Hall deficit")
        result["certificate"] = dict(
            box_ids=[box_ids[bi] for bi in selected],
            resource_ids=[resource_ids[ri] for ri in neighbors],
            demand=need, capacity=have, deficit=need-have,
            checked_inequality=f"{need} > {have}",
        )
    return result


def _floor_demand(x):
    # Downward and upward rounding make floating resource screens weaker.
    return max(0, math.floor(float(x) - 1e-6))


def _ceil_capacity(x):
    return max(0, math.ceil(float(x) + 1e-6))


def analyze(instance, *, tables=None, assignment=None):
    """Return safe necessary static-domain bounds for load-list completion.

    ``assignment`` is a dict {box_id: slot}, interpreted as fixed placements,
    not a request to rearrange them. Other onboard/background cargo is ignored,
    making the screens optimistic. We use every tier of ``Tables.static``;
    no unsupported-slot calls to ``legal_actions`` or ``mask`` occur here.

    Stack mass limits use the MAXIMUM of mixed/20/40 ratings, independent of
    composition. Mass/height can flow to any statically compatible stack and
    split arbitrarily. Physical support, order, pairwise DG, OOG neighbors,
    securing, physics, instructions, and handling sequences are all relaxed.
    """
    started = monotonic()
    if tables is None:
        from stowage.feasibility.tables import build_tables
        tables = build_tables(instance)
    t = tables
    assignment = dict(assignment or {})
    if set(instance.load_list) - set(t.box_ids):
        raise ValueError("A required load-list box is absent from native Tables")
    occupied = set()
    used_mass = np.zeros(len(t.stack_ids))
    used_height = np.zeros(len(t.stack_ids))
    for cid, slot in assignment.items():
        if cid not in t.box_index or slot not in t.slot_index:
            raise ValueError(f"Unknown committed box or slot: {cid}, {slot}")
        bi, si = t.box_index[cid], t.slot_index[slot]
        footprint = [int(c) for c in t.slot_cells[si] if c >= 0]
        if len(footprint) != int(t.units[bi]):
            raise ValueError(f"Committed footprint has wrong size: {cid}, {slot}")
        if occupied.intersection(footprint):
            raise ValueError("Committed placements overlap")
        occupied.update(footprint)
        for ci in footprint:
            j = int(t.cell_stack[ci])
            used_mass[j] += float(t.mass[bi]) / int(t.units[bi])
            used_height[j] += float(t.height[bi])

    box_ids = sorted(set(instance.load_list) - assignment.keys())
    box_indices = [t.box_index[cid] for cid in box_ids]
    free_slot = np.array([not any(int(c) in occupied for c in row if c >= 0)
                          for row in t.slot_cells], dtype=bool)
    cell_domains, stack_domains, counts = [], [], []
    for bi in box_indices:
        eligible = np.flatnonzero(t.static[bi] & free_slot)
        cells = {int(c) for si in eligible for c in t.slot_cells[si] if c >= 0}
        cell_domains.append(sorted(cells))
        stack_domains.append(sorted({int(t.cell_stack[c]) for c in cells}))
        counts.append(len(eligible))

    cells = hall_flow(box_ids, t.cell_ids, cell_domains,
                      [int(t.units[bi]) for bi in box_indices],
                      [0 if ci in occupied else 1 for ci in range(len(t.cell_ids))])
    optimistic_mass = np.maximum.reduce([t.stack_lim_mixed, t.stack_lim_20, t.stack_lim_40])
    masses = hall_flow(box_ids, t.stack_ids, stack_domains,
                       [_floor_demand(t.mass[bi]) for bi in box_indices],
                       [_ceil_capacity(cap-used_mass[j]) for j, cap in enumerate(optimistic_mass)])
    heights = hall_flow(box_ids, t.stack_ids, stack_domains,
                        [_floor_demand(1000*t.units[bi]*t.height[bi]) for bi in box_indices],
                        [_ceil_capacity(1000*(cap-used_height[j])) for j, cap in enumerate(t.stack_hmax)])
    bounds = dict(cells=cells, mass_kg=masses, height_mm=heights)
    deficient = [name for name, row in bounds.items() if row["status"] == "deficit"]
    return dict(
        status="necessary_condition_failed" if deficient else "unclassified",
        scope="committed_prefix_only" if assignment else "original_load_manifest_relaxation",
        original_map_infeasible=bool(deficient and not assignment),
        infeasible_resources=deficient,
        required_boxes=len(box_ids), committed_boxes=len(assignment),
        slot_domain_min=min(counts, default=0),
        static_domain_sha256=hashlib.sha256(t.static.tobytes()).hexdigest(),
        rules="native Tables.static; all tiers, no open-slot mask",
        bounds=bounds, elapsed_s=monotonic()-started,
    )


def _self_test():
    # Every individual box has a destination, but their joint demand is too big.
    r = hall_flow(["a", "b", "c"], ["x", "y", "z"],
                  [[0, 1], [0, 1], [0, 1]], [1, 1, 1], [1, 1, 1])
    assert r["status"] == "deficit" and r["certificate"]["deficit"] == 1
    assert r["certificate"]["resource_ids"] == ["x", "y"]
    assert hall_flow(["long"], ["x", "y"], [[0, 1]], [2], [1, 1])["status"] == "no_deficit"
    assert hall_flow(["long"], ["x"], [[0]], [2], [1])["status"] == "deficit"
    assert hall_flow([], [], [], [], [])["status"] == "no_deficit"
    assert _floor_demand(2.5) <= 2.5 <= _ceil_capacity(2.5)
    return dict(status="passed", cases=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", type=Path, help="Explicit authorized TRAIN/validation instance JSON")
    parser.add_argument("--assignment", type=Path, help="Optional committed {box:slot} JSON")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(_self_test(), indent=2))
    elif args.instance:
        from stowage.model import Instance
        instance = Instance.model_validate_json(args.instance.read_text())
        assignment = json.loads(args.assignment.read_text()) if args.assignment else None
        result = analyze(instance, assignment=assignment)
        result["input_sha256"] = hashlib.sha256(args.instance.read_bytes()).hexdigest()
        print(json.dumps(result, indent=2))
    else:
        parser.error("provide --instance or --self-test")


if __name__ == "__main__":
    main()
