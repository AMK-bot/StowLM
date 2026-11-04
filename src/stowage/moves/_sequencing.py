"""Geometric pending-lift obligations for the C6/D69 sequencing screen.

This helper does not reject actions. In particular, a later hatch opening can
cause the expander to clear deck cargo through additional restows. Consumers
must apply A7's stage/action policy before treating a reported row as a reason
not to build a stack to H; these rows are not proof of universal infeasibility.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping

from stowage.conventions import cells_of_slot, odd_bays_of, parse_slot, stack_id
from stowage.model import Instance
from stowage.model.moves import Discharge, HatchCoverOff, Load, RestowOn
from stowage.model.ship import WorldState
from stowage.moves._paths import _CrossingGeometry, _indexed_crossing_paths
from stowage.rules.catalogue import rule_freeze_v1_enabled


def pending_lift_rows(
    instance: Instance,
    state: WorldState,
    bay: int,
    blocking_row: int,
    *,
    pending_load_targets: Mapping[str, Collection[str]] | None = None,
    placing_container_id: str | None = None,
) -> tuple[int, ...]:
    """Report pending endpoint rows whose mandatory options cross a footprint.

    ``bay`` is an addressed ship bay: an even bay queries the UNION of its two
    20-foot blocking stacks. The caller determines whether its proposed action
    brings that footprint to H; this function does not require a current height.
    This is ship-side sequencing only: A8's independent effective yard height
    does not change the queried ship footprint or its quay-H semantics.
    Discharges still aboard, hatch openings required to reach them, and closures
    of covers already off are inferred from the current world. Other unexecuted
    loads/restow returns require caller-supplied EXHAUSTIVE eventually-viable
    target domains, including targets temporarily unavailable now. Missing or
    empty domains are unknown and are skipped; no vacuous ``all`` establishes an
    obligation. A single target alternative that avoids the footprint suppresses
    that box's report. Target-specific required cover off/on paths are grouped
    with that target, rather than treated as independent mandatory obligations.

    Results label cargo target/source rows, and the quay-nearest edge rows for
    independent cover closures. All decisions use the same prepared evaluator
    as ``crossing_paths``; geometry is indexed once per invocation and endpoint
    results are cached locally. There is no replay, target search, solver, or
    shared/global cache here. Inputs remain
    untouched. Unknown H and the legacy switch return an empty tuple.
    """
    if not rule_freeze_v1_enabled() or instance.port.max_crane_height_tiers is None:
        return ()
    queried = {stack_id(subbay, blocking_row, True) for subbay in odd_bays_of(bay)}
    geometry = _CrossingGeometry(instance, instance.port)
    stacks, cells, covers = geometry.stacks, geometry.cells, geometry.covers
    aboard = {placement.container_id: placement.slot for placement in state.ship.placements}
    path_cache: dict[tuple[str, str], bool] = {}
    cover_rows_cache: dict[str, tuple[int, ...]] = {}

    def crosses(key, move):
        if key not in path_cache:
            path_cache[key] = not queried.isdisjoint(_indexed_crossing_paths(
                move, port=instance.port, geometry=geometry).ship_stack_ids)
        return path_cache[key]

    def cover_crosses(cover_id):
        # Off and on share exactly the same A2 geometry; one local cache entry.
        return crosses(("cover", cover_id), HatchCoverOff(call_index=state.call_index, cover_id=cover_id))

    def cover_rows(cover_id):
        if cover_id not in cover_rows_cache:
            members = [stacks[sid] for cover in covers[cover_id]
                       for sid in cover.deck_stack_ids + cover.hold_stack_ids]
            rows = set()
            # Labels only: the actual crossing decision above always uses the
            # shared path API. Empty geometry has already been diagnosed there.
            for subbay in {stack.bay for stack in members}:
                candidates = [stack for stack in members if stack.bay == subbay and stack.on_deck and stack.cell_ids]
                candidates = candidates or [stack for stack in members if stack.bay == subbay and stack.cell_ids]
                nearest = (max if instance.port.berthing_side == "port" else min)(
                    candidates, key=lambda stack: cells[stack.cell_ids[0]].tcg_m)
                rows.add(nearest.row)
            cover_rows_cache[cover_id] = tuple(sorted(rows))
        return cover_rows_cache[cover_id]

    def required_covers(slot):
        result = set()
        for cell_id in cells_of_slot(slot):
            stack = stacks[cells[cell_id].stack_id]
            if stack.hatch_cover_id is not None and (
                    not stack.on_deck or stack.hatch_cover_id in state.ship.covers_off):
                result.add(stack.hatch_cover_id)
        return result

    def target_crosses(cid, slot, *, discharge=False):
        key = ("discharge" if discharge else "target", slot)
        if discharge:
            move = Discharge(call_index=state.call_index, container_id=cid, src=slot, dst="truck")
        elif cid in state.yard.quay_buffer:
            move = RestowOn(call_index=state.call_index, container_id=cid, dst=slot)
        else:
            # This is a geometry-only load: a truck endpoint omits yard paths,
            # without claiming that the box is eligible for direct delivery.
            move = Load(call_index=state.call_index, container_id=cid, src="truck", dst=slot)
        return crosses(key, move) or any(cover_crosses(cover_id) for cover_id in required_covers(slot))

    pending_rows = set()
    for cid in instance.discharge_list:
        if cid != placing_container_id and cid in aboard and target_crosses(cid, aboard[cid], discharge=True):
            pending_rows.add(parse_slot(aboard[cid]).row)
    # Already-open covers must eventually close regardless of any target domain.
    for cover_id in state.ship.covers_off:
        if cover_crosses(cover_id):
            pending_rows.update(cover_rows(cover_id))
    if pending_load_targets is not None:
        registered = {box.id for box in instance.containers}
        pending_ids = (set(instance.load_list) | set(state.yard.quay_buffer)) - set(aboard)
        for cid in sorted(pending_ids & registered):
            if cid == placing_container_id:
                continue
            targets = pending_load_targets.get(cid)
            if not targets:
                continue
            # Consume the alternatives once and stop at the first escape. The
            # domain is exhaustive by caller contract, never generated here.
            rows = set()
            for target in targets:
                if not target_crosses(cid, target):
                    break
                rows.add(parse_slot(target).row)
            else:
                pending_rows.update(rows)
    return tuple(sorted(pending_rows))
