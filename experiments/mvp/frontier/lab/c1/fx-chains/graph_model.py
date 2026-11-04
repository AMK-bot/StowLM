"""Exact matching bound for a deliberately restricted, zero-overstow poset.

This is NOT a bound for native legal stows: port order is an objective and
native E2 may allow mass/class tolerances. Physical stack capacities are absent.
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching


def chain_cover(problem, boxes=None, *, by_class=False):
    """Return matching, chain and independently checkable antichain witnesses.

    Lower precedes upper if its port is no earlier and its mass/rank no lower.
    Equal attributes are ordered by id so the relation is antisymmetric.
    """
    p, T = problem, problem.engine.T
    ids = sorted(p.instance.load_list if boxes is None else boxes)
    attrs = [(p.pod(b), int(T.rank[T.box_index[b]]) if by_class
              else float(T.mass[T.box_index[b]])) for b in ids]
    rows, cols = [], []
    adjacent = [[] for _ in ids]
    for i, a in enumerate(attrs):
        for j, b in enumerate(attrs):
            if i != j and a[0] >= b[0] and a[1] >= b[1] and (a != b or i < j):
                rows.append(i)
                cols.append(j)
                adjacent[i].append(j)
    graph = csr_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)),
                       shape=(len(ids), len(ids)))
    mate = maximum_bipartite_matching(graph, perm_type='column')
    inverse = {int(j): i for i, j in enumerate(mate) if j >= 0}
    chains = []
    for i in range(len(ids)):
        if i in inverse:
            continue
        chain = []
        while i >= 0:
            chain.append(ids[i])
            i = int(mate[i])
        chains.append(chain)
    # Alternating reachability from unmatched left vertices gives a minimum
    # vertex cover (L minus ZL) union ZR. Its complement on both copies yields
    # a maximum antichain of the original poset.
    left = {i for i, j in enumerate(mate) if j < 0}
    right, todo = set(), list(left)
    while todo:
        i = todo.pop()
        for j in adjacent[i]:
            if mate[i] == j or j in right:
                continue
            right.add(j)
            k = inverse.get(j)
            if k is not None and k not in left:
                left.add(k)
                todo.append(k)
    anti = sorted(left - right)
    assert len(anti) == len(chains)
    aset = set(anti)
    assert not any(i in aset and j in aset for i, j in zip(rows, cols))
    assert sorted(b for chain in chains for b in chain) == ids
    return dict(boxes=len(ids), order='port+class' if by_class else 'port+exact_mass',
                width=len(chains), edges=len(rows), chains=chains,
                antichain=[ids[i] for i in anti],
                scope='restricted zero-overstow zero-tolerance model; not map infeasibility')
