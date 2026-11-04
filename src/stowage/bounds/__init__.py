"""Lower bounds on non-productive lifts (plan 3.6).

Counting bounds identify physical work that every solution must do; they
never impose a fixed retrieval order on interchangeable load boxes. Yard,
ship-clearance and cover lifts are disjoint types, so their bounds add.
Bounds on the SAME type (e.g. grouped LB1 and CPMP) must use their maximum.
See components for sources and the short validity arguments.
"""

from __future__ import annotations

import math

from stowage.frozen import frozen
from stowage.model import Instance, StowModel
from stowage.bounds.components import (
    cpmp_blocking_bound,
    current_hatch_cover_bound,
    current_restow_bound,
    future_hatch_cover_bound,
    future_restow_bound,
    grouped_blocking_bound,
    partial_order_blocking_bound,
    yard_rehandle_bound,
)


class MoveLowerBound(StowModel):
    """Valid lower bounds (never above the true optimum) on lifts for one instance.
    ``weighted`` uses ``instance.objective`` weights and is the bound on ``MoveCount.weighted_nonproductive``.
    ``method``: short description per component (e.g. "BRP LB1 with interchangeable categories")."""

    relocations: int
    restow_lifts: int
    hatch_cover_lifts: int
    future_restow_lifts: int
    future_hatch_cover_lifts: int
    weighted: float
    current_weighted: float = 0.0
    future_weighted: float = 0.0
    gap_informative: bool = False
    gap_note: str = "no certificate (L = 0)"
    current_gap_informative: bool = False
    current_gap_note: str = "no certificate (L_current = 0)"
    method: dict[str, str] = {}


@frozen("bounds.lower_bound", owner="3.6", implemented=True)
def lower_bound(instance: Instance, *, time_limit_s: float = 10.0) -> MoveLowerBound:
    """Lower bounds on every non-productive lift type for ``instance``. Must never exceed the brute-force optimum
    (3.6 done criterion; 4.10 checks against published BRP optima). Deterministic.

    Lift weights must be finite and nonnegative; otherwise raises ValueError.
    Simulator gate moves are outside the ship-call planning action space.
    """
    weights = instance.objective
    if any(not math.isfinite(value) or value < 0 for value in (
        weights.relocate, weights.restow, weights.hatch_cover,
        weights.future_restow, weights.future_hatch_cover,
    )):
        raise ValueError("lower bounds require finite nonnegative lift weights")
    # Counting uses the access graph plus sorting cover capacities; there is
    # no time-limited search, so zero budget returns the same deterministic bound.
    relocations = yard_rehandle_bound(instance)
    restows = current_restow_bound(instance)
    covers = current_hatch_cover_bound(instance)
    future_restows = future_restow_bound(instance)
    future_covers = future_hatch_cover_bound(instance)
    current_weighted = (
        weights.relocate * relocations
        + weights.restow * restows
        + weights.hatch_cover * covers
    )
    future_weighted = (
        weights.future_restow * future_restows
        + weights.future_hatch_cover * future_covers
    )
    weighted = current_weighted + future_weighted
    return MoveLowerBound(
        relocations=relocations,
        restow_lifts=restows,
        hatch_cover_lifts=covers,
        future_restow_lifts=future_restows,
        future_hatch_cover_lifts=future_covers,
        weighted=weighted,
        current_weighted=current_weighted,
        future_weighted=future_weighted,
        gap_informative=weighted > 0,
        gap_note=("positive lower bound; equality with a checked same-domain objective certifies optimality"
                  if weighted > 0 else "no certificate (L = 0)"),
        current_gap_informative=current_weighted > 0,
        current_gap_note=("positive current-work lower bound"
                          if current_weighted > 0 else "no certificate (L_current = 0)"),
        method={
            "relocations": "Grouped LB1/CPMP count: free order of all loads; non-load blockers only; no simulator gates",
            "restow_lifts": "Arrival cargo access closure; one lift per through-box; empty Stage A arrival has no blockers",
            "hatch_cover_lifts": "Mandatory discharge/forced-hold loading covers; credit shared openings and initially open covers",
            "future_restow_lifts": "Zero: free departure assignment/current restows relax all later obstructions",
            "future_hatch_cover_lifts": "Two lifts per hold cover forced by departure cargo exceeding raw deck capacity",
            "combination": "Add disjoint lift types; grouped LB1 and CPMP coincide and are never added",
        },
    )
