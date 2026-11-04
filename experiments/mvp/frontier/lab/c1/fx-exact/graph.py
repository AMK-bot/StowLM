"""Structural diagnostics, not a solver or a lower bound on lift count.

Domains deliberately relax all unary restrictions except cell length capability.
The incidence graph coarsens each box's slot domain to its supporting stacks.
The box primal graph retains pairwise occupancy conflicts of those domains.
No external graph package is needed. See STRUCTURE.md for interpretation.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from time import perf_counter

from stowage.conventions import cells_of_slot, format_slot, parse_slot


def _edge(graph, left, right):
    graph.setdefault(left, set())
    graph.setdefault(right, set())
    if left != right:
        graph[left].add(right)
        graph[right].add(left)


def min_fill(graph):
    """Return a deterministic elimination order and its induced-width upper bound.

    Each eliminated vertex's remaining neighbors are made a clique. Minimize
    number of missing neighbor edges, then degree, then the vertex name. Bitsets
    count fill edges without a quadratic Python pair loop. On a remaining clique
    the rest of the order is immediate. An edgeless graph has width zero.
    """
    names = sorted(graph)
    ids = {name: i for i, name in enumerate(names)}
    adjacency = [0] * len(names)
    for name, neighbors in graph.items():
        i = ids[name]
        for other in neighbors:
            if other not in ids or name not in graph[other]:
                raise ValueError("expected a symmetric graph containing every vertex")
            if other != name:
                adjacency[i] |= 1 << ids[other]
    active = (1 << len(names)) - 1
    width, fill_edges = 0, 0
    order, bags = [], []
    while active:
        candidates = []
        remaining = active
        while remaining:
            bit = remaining & -remaining
            i = bit.bit_length() - 1
            neighbors = adjacency[i] & active
            candidates.append((neighbors.bit_count(), i, neighbors))
            remaining ^= bit
        if all(degree == len(candidates) - 1 for degree, _, _ in candidates):
            # This also handles the final singleton.
            for _, i, _ in candidates:
                neighbors = adjacency[i] & active
                width = max(width, neighbors.bit_count())
                order.append(names[i])
                bags.append(tuple(names[j] for j in range(len(names))
                                  if (neighbors | (1 << i)) & (1 << j)))
                active ^= 1 << i
            break
        best = None
        for degree, i, neighbors in candidates:
            missing_twice, cursor = 0, neighbors
            while cursor:
                bit = cursor & -cursor
                j = bit.bit_length() - 1
                missing_twice += (neighbors & ~adjacency[j] & ~bit).bit_count()
                cursor ^= bit
                # Candidate cannot beat the current fill score even before
                # counting its remaining missing pairs.
                if best is not None and missing_twice > 2 * best[0]:
                    missing_twice = 2 * (best[0] + 1)
                    break
            key = (missing_twice // 2, degree, i)
            if best is None or key < best[:3]:
                best = (*key, neighbors)
        missing, degree, i, neighbors = best
        fill_edges += missing
        width = max(width, degree)
        order.append(names[i])
        bags.append(tuple(names[j] for j in range(len(names))
                          if (neighbors | (1 << i)) & (1 << j)))
        cursor = neighbors
        while cursor:
            bit = cursor & -cursor
            j = bit.bit_length() - 1
            adjacency[j] |= neighbors & ~bit
            cursor ^= bit
        active ^= 1 << i
    return {"width_upper_bound": width, "fill_edges": fill_edges,
            "order": order, "bags": bags}


def _domains(instance):
    cells = {cell.id: cell for cell in instance.vessel.cells}
    by_length = {20: {}, 40: {}, 45: {}}
    for cell in cells.values():
        if cell.accepts_20:
            by_length[20][cell.id] = (cell.id,)
        address = parse_slot(cell.id)
        partner = format_slot(address.bay + 2, address.row, address.tier)
        if partner not in cells:
            continue
        slot = format_slot(address.bay + 1, address.row, address.tier)
        for length in (40, 45):
            if (getattr(cell, f"accepts_{length}")
                    and getattr(cells[partner], f"accepts_{length}")):
                by_length[length][slot] = (cell.id, partner)
    return by_length


def build_graphs(instance):
    """Return (coarsened physical incidence, occupancy primal, domain counts).

    Vertices are strings prefixed box:, slot:, ship:, yard:, or hatch:.
    The primal has one vertex per load box, joined when some assignments could
    overlap a physical cell. Its domains are a superset of real legal domains.
    """
    incidence, primal = {}, {}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {box.id: box for box in instance.containers}
    by_length = _domains(instance)
    for box in instance.containers:
        incidence[f"box:{box.id}"] = set()
    for stack in instance.vessel.stacks:
        name = f"ship:{stack.id}"
        incidence.setdefault(name, set())
        if stack.hatch_cover_id is not None:
            _edge(incidence, name, f"hatch:{stack.hatch_cover_id}")
    for cover in instance.vessel.hatch_covers:
        incidence.setdefault(f"hatch:{cover.id}", set())
    for domains in by_length.values():
        for slot, footprint in domains.items():
            for cell in footprint:
                _edge(incidence, f"slot:{slot}", f"ship:{cells[cell].stack_id}")
    for stack in instance.yard.stacks:
        incidence.setdefault(f"yard:{stack.key}", set())
        for box in stack.boxes:
            _edge(incidence, f"box:{box}", f"yard:{stack.key}")
    for placement in instance.onboard.placements:
        for cell in cells_of_slot(placement.slot):
            _edge(incidence, f"box:{placement.container_id}",
                  f"ship:{cells[cell].stack_id}")
    # Connect boxes to stack neighborhoods of their full geometric slot domains.
    cell_users, domain_counts = {}, {}
    for box in instance.load_list:
        primal[box] = set()
        domains = by_length[boxes[box].length_ft]
        domain_counts[box] = len(domains)
        supports = {cell for footprint in domains.values() for cell in footprint}
        for cell in supports:
            cell_users.setdefault(cell, set()).add(box)
            _edge(incidence, f"box:{box}", f"ship:{cells[cell].stack_id}")
    for users in cell_users.values():
        for left, right in combinations(sorted(users), 2):
            _edge(primal, left, right)
    return incidence, primal, domain_counts


def _stats(graph):
    started = perf_counter()
    result = min_fill(graph)
    return {"vertices": len(graph),
            "edges": sum(map(len, graph.values())) // 2,
            "vertex_kinds": dict(sorted(Counter(v.split(":", 1)[0] if ":" in v else "box" for v in graph).items())),
            "min_fill_width_upper_bound": result["width_upper_bound"],
            "fill_edges": result["fill_edges"],
            "elapsed_s": perf_counter() - started}


def summarize(instance):
    """JSON-serializable summary of a real instance; no planning or finishing."""
    incidence, primal, domain_counts = build_graphs(instance)
    return {"instance_id": instance.id, "size_class": instance.size_class,
            "load_boxes": len(instance.load_list),
            "hatches": len(instance.vessel.hatch_covers),
            "candidate_slots_min": min(domain_counts.values(), default=0),
            "candidate_slots_max": max(domain_counts.values(), default=0),
            "physical_incidence": _stats(incidence),
            "occupancy_primal": _stats(primal),
            "global_factor_primal_width": max(0, len(instance.load_list) - 1),
            "interpretation": "Heuristic graph-width upper bounds; not lift lower bounds. "
            "Incidence coarsens box-slot domains to stacks and omits global rules. "
            "Occupancy domains relax all unary rules except length capability. "
            "A single unreformulated all-box physics factor makes the box primal a clique."}
