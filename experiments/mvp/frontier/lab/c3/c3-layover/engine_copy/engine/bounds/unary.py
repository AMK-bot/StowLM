"""Optimistic DG eligibility for bounds only; native search tables stay intact.

Legacy DG unary assessment probes unsupported upper cells on an empty ship.
Its rejection can therefore depend on geometry that later supports change.
Rather than certify that probe, retain every existing physical slot with the
box's one/two-cell footprint. This drops reefer, heat, OOG, IMDG and all other
unary restrictions for DG cargo; admitting extra choices only weakens a bound.
"""
from dataclasses import replace

import numpy as np


def optimistic_dg_tables(tables):
    """Copy only ``static`` and widen DG rows; never mutate or narrow inputs.

    Production slot enumeration includes every physical singleton cell and
    every same-row/tier adjacent pair, exactly the legal 20/40/45 footprints.
    ``share``, ``height`` and stack/slot geometry arrays retain their physical
    meanings: the bound's remaining mass, support and height constraints are
    necessary conditions independently of the dropped DG unary restrictions.
    This is deliberately unsuitable for construction or acceptance gates.
    """
    rows = np.flatnonzero(tables.is_dg)
    if not len(rows):
        return tables
    static = tables.static.copy()
    for b in rows:
        footprint = 1 if tables.length[b] == 20 else 2
        static[b] |= tables.slot_n == footprint
    return replace(tables, static=static)
