"""Measure the graph of a map: yard digraph, conflict graph, treewidth estimate, cliques, components. Read-only."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]

from stowage.io import load_instance  # noqa: E402


def components(vertices, edges):
    parent = {v: v for v in vertices}

    def find(v):
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v
    for a, b in edges:
        parent[find(a)] = find(b)
    return Counter(find(v) for v in vertices)


def degeneracy_and_clique(vertices, adj):
    """Smallest-last order: degeneracy is a lower bound on treewidth; the greedy clique another."""
    degree = {v: len(adj[v]) for v in vertices}
    left, order, core = set(vertices), [], 0
    while left:
        v = min(left, key=lambda u: degree[u])
        core = max(core, degree[v])
        order.append(v)
        left.discard(v)
        for u in adj[v]:
            if u in left:
                degree[u] -= 1
    # Greedy clique from the densest tail of the order.
    clique = []
    for v in reversed(order):
        if all(v in adj[u] for u in clique):
            clique.append(v)
    return core, len(clique)


def min_fill_width(vertices, adj):
    """Upper bound on treewidth by min-degree elimination (fill-in added as we go)."""
    graph = {v: set(adj[v]) for v in vertices}
    width = 0
    while graph:
        v = min(graph, key=lambda u: len(graph[u]))
        nbrs = graph.pop(v)
        width = max(width, len(nbrs))
        for a in nbrs:
            graph[a].discard(v)
            graph[a] |= nbrs-{a}
    return width


def describe(path):
    i = load_instance(ROOT/path)
    boxes = {b.id: b for b in i.containers}
    load = list(i.load_list)
    loadset = set(load)
    order = {c.locode: c.index for c in i.rotation.calls}
    v = i.vessel
    out = dict(id=i.id, path=path, vessel=v.id if hasattr(v, 'id') else None, boxes=len(load),
               containers_total=len(boxes), call_index=i.call_index, onboard=len(i.onboard.placements),
               discharge_list=len(i.discharge_list))
    out['pods'] = dict(Counter(boxes[b].pod for b in load))
    out['lengths'] = dict(Counter(boxes[b].length_ft for b in load))
    out['reefers'] = sum(boxes[b].reefer for b in load)
    out['dg'] = sum(boxes[b].dg is not None for b in load)
    out['oog'] = sum(boxes[b].oog is not None for b in load)
    out['direct_delivery'] = sum(bool(boxes[b].direct_delivery) for b in load)
    masses = sorted(boxes[b].gross_mass_kg for b in load)
    out['mass_t'] = dict(min=masses[0]/1000, med=masses[len(masses)//2]/1000, max=masses[-1]/1000)
    # Ship
    stacks = list(v.stacks)
    out['ship'] = dict(cells=len(v.cells), stacks=len(stacks), covers=len(v.hatch_covers),
                       deck_stacks=sum(s.on_deck for s in stacks), hold_stacks=sum(not s.on_deck for s in stacks),
                       tiers=dict(Counter(len(s.cell_ids) for s in stacks)),
                       cells_20=sum(c.accepts_20 for c in v.cells), cells_40=sum(c.accepts_40 for c in v.cells),
                       cells_45=sum(c.accepts_45 for c in v.cells), plugs=sum(c.reefer_plug for c in v.cells))
    teu = sum(2 if boxes[b].length_ft > 20 else 1 for b in load)
    out['fill_teu_vs_cells'] = round(teu/len(v.cells), 3)
    # Yard digraph: arc top -> below for every pair in a yard stack (load-list and strangers).
    yard = [s for s in i.yard.stacks if s.boxes]
    out['yard'] = dict(stacks=len(yard), boxes=sum(len(s.boxes) for s in yard),
                       heights=dict(Counter(len(s.boxes) for s in yard)),
                       stacks_with_load=sum(any(b in loadset for b in s.boxes) for s in yard))
    strangers_above = 0
    pairs, cross_pod, cover = [], 0, []
    yard_deep = 0
    arcs_all = 0
    for s in yard:
        row = list(s.boxes)
        deepest = next((k for k, b in enumerate(row) if b in loadset), None)
        if deepest is None:
            continue
        strangers_above += sum(1 for b in row[deepest+1:] if b not in loadset)
        loaded = [b for b in row if b in loadset]
        yard_deep = max(yard_deep, len(loaded))
        for a in range(len(loaded)):
            for c in range(a+1, len(loaded)):
                pairs.append((loaded[c], loaded[a]))  # top, below
                cross_pod += boxes[loaded[c]].pod != boxes[loaded[a]].pod
        cover.append(len(loaded))
    out['yard_graph'] = dict(load_box_vertices=len(load), arcs_top_to_below=len(pairs),
                             density=round(2*len(pairs)/max(1, len(load)*(len(load)-1)), 4),
                             arcs_across_ports=cross_pod, strangers_forced_relocations=strangers_above,
                             largest_load_run_in_a_yard_stack=yard_deep,
                             load_stack_sizes=dict(Counter(cover)),
                             components=len(components(load, pairs)), isolated=len(load)-sum(cover))
    # Compatibility graph of ship stack order: a can sit below b (a < b).
    # Ship-order edges: a below b needs mass(a) >= mass(b) if rule on, POD(a) >= POD(b) to avoid restow.
    comparable = 0
    adj = defaultdict(set)
    for x, a in enumerate(load):
        for b in load[x+1:]:
            A, B = boxes[a], boxes[b]
            if A.length_ft == B.length_ft or True:
                ge = (order[A.pod] >= order[B.pod] and A.gross_mass_kg >= B.gross_mass_kg)
                le = (order[A.pod] <= order[B.pod] and A.gross_mass_kg <= B.gross_mass_kg)
                if not (ge or le):
                    adj[a].add(b)
                    adj[b].add(a)
                else:
                    comparable += 1
    n = len(load)
    out['poset'] = dict(pairs=n*(n-1)//2, comparable=comparable, conflict_edges=n*(n-1)//2-comparable,
                        density_conflict=round(1-comparable/max(1, n*(n-1)/2), 4))
    for key, (edges) in dict(yard=pairs).items():
        a2 = defaultdict(set)
        for p, q in edges:
            a2[p].add(q)
            a2[q].add(p)
        core, clique = degeneracy_and_clique(load, a2)
        out['yard_graph'] |= dict(treewidth_lb_degeneracy=core, clique=clique, treewidth_ub=min_fill_width(load, a2))
    core, clique = degeneracy_and_clique(load, adj)
    out['poset'] |= dict(treewidth_lb_degeneracy=core, clique_antichain=clique,
                         treewidth_ub=min_fill_width(load, adj) if n <= 600 else None,
                         components=len(components(load, [(a, b) for a in adj for b in adj[a]])))
    return out


if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(json.dumps(describe(p), indent=1, default=str))
