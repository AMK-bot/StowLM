"""Departure-aware greedy dig-out for the scalable planner and port simulator.

The container relocation problem asks which stacks should receive blockers so
that later retrievals need few extra lifts. A useful rule is to put a blocker
above boxes that depart later: this creates no new departure-order inversion.
Among such destinations we use the closest later departure rank (best fit),
reserving empty stacks for harder blockers. If every destination creates an
inversion, defer its consequences by covering the latest departing stack first.
This is a greedy relocation rule, related to the good/bad move classification
in Forster & Bortfeldt, "A tree search procedure for the container relocation
problem" (2012), https://doi.org/10.1016/j.cor.2011.04.004, and departure-order
heuristics in https://arxiv.org/abs/1505.04229. It is not an exact optimizer.

Ranks refer to *yard pickup order*, not the ship's POD order. Lower ranks depart
first; equal ranks permit either order. Explicit ranks can be arrival times from
a simulator. APPROXIMATION: absent pickup forecasts, current load-list boxes
share rank zero and other inventory has infinite rank. This assumes current
loads leave before other inventory and gives no order within either group;
it can create future rehandles if a non-load box is picked up sooner. Known
per-box forecasts override this fallback, including in partial mappings. POD
order is not a substitute for pickup forecasts. Each returned relocation has passed the
public move engine; no input state is mutated. Detailed yard DG pair segregation
and OOG clearances have the same certification limits as yard.destinations.
The scalable search itself uses ``Expander`` for every action and shares its
crossing-clearing relocation policy with the mask. This separately exported
simulator policy uses optional pickup forecasts and departure-aware best-fit
targets, so its target choices can differ; it is never substituted into search
or feasibility replay.
Relocations stay within the source block, matching the action expander's
single yard-crane lift convention; inter-block transfers need a transport model.
"""
from __future__ import annotations

from collections.abc import Mapping
import math

from stowage.conventions import parse_yard
from stowage.model import Instance
from stowage.model.moves import Relocate
from stowage.model.ship import WorldState
from stowage.moves.engine import IllegalMoveError, apply_move
from stowage.yard.dig_out import Retrieval, YardCapacityError, destinations, location


class ScalableDigOutPolicy:
    """Pure deterministic DigOutPolicy with optional externally known pickup ranks.

    APPROXIMATION: one-step priority ranking is a search heuristic; it does not
    guarantee globally minimum rehandles or finding a route in a congested yard.
    APPROXIMATION: missing ranks assume current loads leave before other yard
    inventory, with no within-group order. Supply actual pickup ranks when
    known; the fallback cannot certify freedom from future rehandles.
    """

    def __init__(self, departure_ranks: Mapping[str, float] | None = None):
        self.departure_ranks = dict(departure_ranks or {})
        if any(math.isnan(value) for value in self.departure_ranks.values()):
            raise ValueError("departure ranks must not contain NaN")

    def retrieve(self, instance: Instance, state: WorldState, container_id: str) -> Retrieval:
        source = location(state, container_id)
        if source is None:
            raise YardCapacityError(f"expert.moves.top_access: {container_id} is absent from the yard")
        key = source.rsplit("-", 1)[0]
        current_loads = set(instance.load_list)

        def rank(cid: str) -> float:
            # APPROXIMATION: absent forecasts assume current-load pickup first
            # (rank zero), then other inventory (infinity). This may hide future
            # inversions; explicit ranks always take precedence for named boxes.
            return self.departure_ranks.get(cid, 0.0 if cid in current_loads else math.inf)

        moves: list[Relocate] = []
        while True:
            stacks = {stack.key: stack for stack in state.yard.stacks}
            stack = stacks[key]
            if stack.boxes[-1] == container_id:
                return Retrieval(state, tuple(moves), source)
            blocker = stack.boxes[-1]
            src = f"{key}-{len(stack.boxes):02d}"
            priority = rank(blocker)
            origin = parse_yard(src)

            def preference(dst: str) -> tuple:
                address = parse_yard(dst)
                below = stacks.get(dst.rsplit("-", 1)[0])
                priorities = [rank(cid) for cid in below.boxes] if below else []
                earliest = min(priorities, default=math.inf)
                unsafe = earliest < priority
                # APPROXIMATION: Manhattan grid distance only breaks objective
                # ties; actual crane routes/travel times are not modelled.
                distance = (address.block != origin.block,
                            abs(address.bay - origin.bay) + abs(address.row - origin.row))
                return (unsafe, -earliest if unsafe else earliest,
                        sum(value < priority for value in priorities),
                        len(priorities), distance, dst)

            candidates = (dst for dst in destinations(instance, state, blocker, exclude=key)
                          if parse_yard(dst).block == origin.block)
            for dst in sorted(candidates, key=preference):
                move = Relocate(call_index=state.call_index, container_id=blocker, src=src, dst=dst)
                try:
                    updated = apply_move(instance, state, move)
                except IllegalMoveError:
                    continue
                state = updated
                moves.append(move)
                break
            else:
                raise YardCapacityError("expert.moves.top_access: no legal blocker relocation destination")


def dig_out(instance: Instance, state: WorldState, container_id: str, *,
            departure_ranks: Mapping[str, float] | None = None) -> Retrieval:
    """Expose one box without removing it; returned moves replay via apply_move."""
    return ScalableDigOutPolicy(departure_ranks).retrieve(instance, state, container_id)
