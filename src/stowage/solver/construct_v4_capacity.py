"""Cheap necessary capacity counts for monotone construction branches.

A pooled count asks whether a group of boxes collectively has enough space:
ten tall boxes cannot fit into nine available destinations, even if each box
individually has several choices. This is the pigeonhole principle behind
assignment matching (https://developers.google.com/optimization/assignment).
Here counts are only upper bounds; passing never proves a feasible packing.

APPROXIMATION: examine a bounded selection of height/class pools, rather than
every subset of cargo. Ignore support, mass and segregation interactions not
already represented by the caller's optimistic domains. These omissions make
the screen weaker; the unchanged oracle/checker still decides acceptance.
"""
from __future__ import annotations

import numpy as np

from stowage.conventions import cells_of_slot, parse_slot


class CapacityPools:
    """Count cell unions and disjoint long slots without a solver worker.

    Call only when placed cargo stays fixed. A long slot joins adjacent odd
    bays in a single row/tier, so each component is a path. Taking the first
    available edge along a path gives its maximum number of disjoint pairs.
    Counting every selectable tier independently remains optimistic about
    future supports and equal roof heights.
    """

    def __init__(self, tables, parts=None):
        self.t = tables
        parts = parts if parts is not None else [cells_of_slot(s) for s in tables.slot_ids]
        self.parts = [tuple(tables.cell_index[c] for c in p) for p in parts]
        self.long_order = []
        self.path_geometry = True
        for si, footprint in enumerate(parts):
            if len(footprint) != 2:
                continue
            a, b = map(parse_slot, footprint)
            if (a.row, a.tier) != (b.row, b.tier) or abs(a.bay-b.bay) != 2:
                self.path_geometry = False
            self.long_order.append(((a.row, a.tier, min(a.bay, b.bay)), si))
        self.long_order.sort()

    def long_capacity(self, allowed):
        if not self.path_geometry:
            # Unknown geometry: counting all edges is a safe, weaker bound.
            return sum(bool(allowed[si]) for _, si in self.long_order)
        used = set()
        count = 0
        for _, si in self.long_order:
            if not allowed[si]:
                continue
            a, b = self.parts[si]
            if a not in used and b not in used:
                used.update((a, b))
                count += 1
        return count

    def evaluate(self, indices, domains):
        """Return necessary deficits; each row is one remaining box's domain."""
        if not len(indices):
            return []
        t = self.t
        lengths, heights, ranks = t.length[indices], t.height[indices], t.rank[indices]
        long = lengths != 20
        candidates = [('all', np.ones(len(indices), dtype=bool)),
                      ('long', long), ('20ft', ~long), ('45ft', lengths == 45)]
        for rank in (1, 2):
            selected = ranks >= rank
            candidates.extend([(f'rank>={rank}', selected),
                               (f'long/rank>={rank}', selected & long)])
        levels = np.unique(heights)
        # Every chosen threshold is valid; sampling many distinct OOG heights
        # only limits strength and work, never changes a threshold's validity.
        if len(levels) > 4:
            levels = levels[np.linspace(0, len(levels)-1, 4, dtype=int)]
        for height in levels[1:]:
            selected = heights >= height
            candidates.extend([(f'height>={height:g}', selected),
                               (f'long/height>={height:g}', selected & long)])
        seen = set()
        deficits = []
        for name, selected in candidates:
            key = selected.tobytes()
            if key in seen or not np.any(selected):
                continue
            seen.add(key)
            allowed = np.any(domains[selected], axis=0)
            cells = {cell for si in np.flatnonzero(allowed) for cell in self.parts[int(si)]}
            units = int(np.sum(np.where(long[selected], 2, 1)))
            if units > len(cells):
                deficits.append(dict(pool=name, boxes=int(selected.sum()),
                                     demanded_cell_units=units, available_cell_units=len(cells)))
            if np.all(long[selected]):
                capacity = self.long_capacity(allowed)
                if int(selected.sum()) > capacity:
                    deficits.append(dict(pool=name, boxes=int(selected.sum()),
                                         disjoint_long_slots=capacity))
            if len(deficits) >= 8:
                break
        return deficits
