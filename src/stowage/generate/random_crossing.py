"""Labelled D62 random-yard stress buckets and move-engine observations.

APPROXIMATION: one quarter of activated random draws request a full-height dry
yard background stack across an export's truck-lane path. This is a controlled
stress mixture, not an observed terminal-traffic frequency. Yard limits and
cargo capabilities come from the selected port without a quay-H substitution.
Diagnostic probes deliberately test a blocked sequence; they are never plans
accepted by the checker or evidence that a complete instance is solvable.
"""
from __future__ import annotations

import math
from random import Random

from stowage.conventions import make_container_id, parse_slot, parse_yard
from stowage.generate.cargo_limits import MAX_GENERATED_GROSS_MASS_KG
from stowage.model import Instance, MovePlan
from stowage.model.moves import Load, Relocate
from stowage.model.yard import YardStack, YardState
from stowage.moves import apply_plan
from stowage.ports import effective_yard_tiers
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1_enabled


D62_BUCKET_SHARE = 0.25


def crossing_bucket_selected(seed: int, *, share: float = D62_BUCKET_SHARE) -> bool:
    """An independent deterministic stream leaves the cargo draw RNG untouched."""
    if not math.isfinite(share) or not 0 <= share <= 1:
        raise ValueError("crossing bucket share must lie in [0, 1]")
    return rule_freeze_v1_enabled() and Random(f"random-yard:d62:{seed}").random() < share


def condition_crossing_bucket(instance: Instance) -> tuple[Instance, dict]:
    """Move a complete export column behind a legal full-height yard tower.

    Existing later-cohort boxes above that target move with their column. This
    preserves the exact prescribed two-cohort blocking ratio, including a fully
    blocked yard, and every original shipment fact. The extra empty tower is
    later-cohort cargo, never load-list demand. Failure to find compatible free
    ground is explicit and changes no input; callers must reject/redraw selected
    bucket attempts which fail conditioning or real crossing observation.
    """
    if not rule_freeze_v1_enabled():
        return instance, {"applied": False, "reason": "switch_off"}
    boxes = {box.id: box for box in instance.containers}
    targets = set(instance.load_list)
    occupied = set()
    for stack in instance.yard.stacks:
        addr = parse_yard(stack.key + "-01")
        occupied.update((addr.block, addr.bay + offset, addr.row)
                        for offset in range(1 if stack.length_ft == 20 else 2))
    for source in instance.yard.stacks:
        if not source.boxes or source.boxes[0] not in targets:
            continue
        # A target's later-cohort blockers stay above it. Do not change which
        # exports are vertically blocked, or move another export into its cohort.
        if any(cid in targets for cid in source.boxes[1:]):
            continue
        column = [boxes[cid] for cid in source.boxes]
        box = column[0]
        if any(item.dg or item.reefer or item.oog or item.special_stowage or item.direct_delivery
               for item in column):
            continue
        width = 1 if box.length_ft == 20 else 2
        for block in instance.port.yard.blocks:
            if box.length_ft not in block.allowed_lengths_ft or block.n_rows < 2:
                continue
            limit = effective_yard_tiers(instance.port, block)
            if not limit or len(source.boxes) > limit:
                continue
            rows = list(range(1, block.n_rows + 1))
            if block.truck_lane_side == "high":
                rows.reverse()
            for bay in range(1, block.n_bays - width + 2):
                for near, far in zip(rows, rows[1:]):
                    footprints = {(block.id, bay + offset, row)
                                  for offset in range(width) for row in (near, far)}
                    # Two wholly free cells leave all other cargo in place.
                    if footprints & occupied:
                        continue
                    blocker_key = f"{block.id}-{bay:03d}-{near:02d}"
                    target_key = f"{block.id}-{bay:03d}-{far:02d}"
                    extra = []
                    serial = 100_000
                    tare = float(math.ceil(box.tare_kg))
                    if tare > MAX_GENERATED_GROSS_MASS_KG:
                        continue
                    for _ in range(limit):
                        cid = make_container_id("QZWU", serial)
                        while cid in boxes:
                            serial += 1
                            cid = make_container_id("QZWU", serial)
                        serial += 1
                        boxes[cid] = box
                        extra.append(box.model_copy(update={
                            "id": cid, "gross_mass_kg": tare, "empty": True,
                            "weight_class": load_catalogue().weight_classes.classify(tare),
                            "category": f"later:dry:{box.length_ft}:mass_kg={tare.hex()}:empty",
                        }))
                    stacks = [stack.model_copy(deep=True) for stack in instance.yard.stacks
                              if stack.key != source.key]
                    stacks.extend([
                        YardStack(key=target_key, length_ft=box.length_ft, boxes=source.boxes),
                        YardStack(key=blocker_key, length_ft=box.length_ft, boxes=[b.id for b in extra]),
                    ])
                    family = {**instance.family, "d62_crossing_bucket": "yard_limit_v1",
                              "d62_crossing_target": box.id, "d62_crossing_blocker": blocker_key,
                              "d62_crossing_yard_height": str(limit),
                              "d62_crossing_relocation_ground": source.key}
                    result = instance.model_copy(update={
                        "family": family, "containers": sorted(instance.containers + extra, key=lambda b: b.id),
                        "yard": YardState(yard_id=instance.yard.yard_id,
                                          time_s=instance.yard.time_s, stacks=stacks,
                                          quay_buffer=instance.yard.quay_buffer),
                    }, deep=True)
                    return result, {"applied": True, "target": box.id,
                                    "yard_blocker": blocker_key, "yard_height": limit,
                                    "added_background_boxes": len(extra),
                                    "preserved_column_height": len(source.boxes)}
    return instance, {"applied": False, "reason": "no_compatible_crossing_footprint"}


def _deck_destinations(instance, box):
    cells = {cell.id: cell for cell in instance.vessel.cells}
    bases = {stack.cell_ids[0] for stack in instance.vessel.stacks if stack.on_deck and stack.cell_ids}
    for cid in sorted(bases):
        cell = cells[cid]
        if not getattr(cell, f"accepts_{box.length_ft}"):
            continue
        if box.length_ft == 20:
            yield cid
        else:
            addr = parse_slot(cid)
            partner = f"{addr.bay + 2:03d}{addr.row:02d}{addr.tier:02d}"
            if addr.bay % 4 == 1 and partner in bases and getattr(cells[partner], f"accepts_{box.length_ft}"):
                yield f"{addr.bay + 1:03d}{addr.row:02d}{addr.tier:02d}"


def _diagnostic_observation(result, move):
    counters = result.d62_crossing
    observed = (counters.rejected_crossing_lifts > 0
                and (counters.yard_lifts_at_or_above_h > 0 or counters.ship_lifts_at_or_above_h > 0))
    return {**counters.model_dump(), "source": "random_yard_probe", "observed": observed,
            "probe_attempted": True,
            "probe_rule": result.violations[0].rule_id if result.violations else None,
            "probe_kind": move.kind, "probe_container_id": move.container_id,
            "probe_accepted_plan": False}


def crossing_probe(instance: Instance) -> dict:
    """Observe a required yard access lift using actual replay counters.

    A vertically blocked target first needs its later-cohort boxes relocated.
    Probe that actual dig-out prefix, then the export lift: a same-block yard
    relocation can bypass the lane while the subsequent load still crosses it.
    The original column's vacated footprint is a compatible empty destination.
    These diagnostic attempts never count as verified plan evidence.
    """
    cid = instance.family.get("d62_crossing_target")
    if cid is None:
        return {"source": "random_yard_probe", "observed": False, "reason": "no_conditioned_target"}
    box = next(box for box in instance.containers if box.id == cid)
    source = next(stack for stack in instance.yard.stacks if cid in stack.boxes)
    prefix = []
    if len(source.boxes) > 1:
        destination = instance.family.get("d62_crossing_relocation_ground")
        if destination is None:
            return {"source": "random_yard_probe", "observed": False,
                    "reason": "no_compatible_relocation_endpoint"}
        for level in range(len(source.boxes), 1, -1):
            prefix.append(Relocate(call_index=instance.call_index, container_id=source.boxes[level-1],
                src=f"{source.key}-{level:02d}", dst=f"{destination}-{len(prefix)+1:02d}"))
    last = None
    for dst in _deck_destinations(instance, box):
        move = Load(call_index=instance.call_index, container_id=cid,
                    src=source.key + "-01", dst=dst)
        moves = prefix+[move]
        result = apply_plan(instance, MovePlan(id="random.d62.diagnostic",
                            instance_id=instance.id, call_index=instance.call_index, moves=moves))
        index = result.violations[0].move_index if result.violations else None
        observed_move = moves[index] if index is not None and 0 <= index < len(moves) else move
        last = _diagnostic_observation(result, observed_move)
        last["probe_prefix_relocations"] = len(prefix)
        if result.d62_crossing.rejected_crossing_lifts:
            return last
    return last or {"source": "random_yard_probe", "observed": False,
                    "reason": "no_compatible_deck_endpoint"}


def ship_crossing_probe(instance: Instance) -> dict:
    """Load a near row to H, then try a farther row (D69's wrong order).

    All lifts use existing load-list boxes and the unmodified physical replay.
    Only counters actually observed before a rejection are returned. No ship
    occupancy or fixed background is fabricated for this diagnostic.
    """
    height = instance.port.max_crane_height_tiers
    if not rule_freeze_v1_enabled() or height != 4:
        return {"source": "random_yard_probe", "observed": False, "reason": "not_small_feeder_height_four"}
    boxes = {box.id: box for box in instance.containers}
    candidates = [(boxes[s.boxes[0]], s.key) for s in instance.yard.stacks
                  if len(s.boxes) == 1 and s.boxes[0] in instance.load_list
                  and boxes[s.boxes[0]].length_ft == 20
                  and not (boxes[s.boxes[0]].dg or boxes[s.boxes[0]].reefer
                           or boxes[s.boxes[0]].oog or boxes[s.boxes[0]].special_stowage)]
    if len(candidates) < height + 1:
        return {"source": "random_yard_probe", "observed": False, "reason": "insufficient_accessible_dry20_targets"}
    candidates.sort(key=lambda item: (-item[0].gross_mass_kg, item[0].id))
    cells = {cell.id: cell for cell in instance.vessel.cells}
    deck = [s for s in instance.vessel.stacks if s.on_deck and s.cell_ids]
    for near in deck:
        if len(near.cell_ids) < height:
            continue
        y = cells[near.cell_ids[0]].tcg_m
        for far in deck:
            fy = cells[far.cell_ids[0]].tcg_m
            if near.bay != far.bay or not (fy < y if instance.port.berthing_side == "port" else fy > y):
                continue
            moves = [Load(call_index=instance.call_index, container_id=box.id, src=key + "-01", dst=slot)
                     for (box, key), slot in zip(candidates[:height], near.cell_ids[:height])]
            box, key = candidates[height]
            moves.append(Load(call_index=instance.call_index, container_id=box.id,
                              src=key + "-01", dst=far.cell_ids[0]))
            result = apply_plan(instance, MovePlan(id="random.d62.ship-diagnostic",
                                instance_id=instance.id, call_index=instance.call_index, moves=moves))
            if result.d62_crossing.ship_lifts_at_or_above_h:
                return _diagnostic_observation(result, moves[-1])
    return {"source": "random_yard_probe", "observed": False, "reason": "no_observed_ship_crossing_sequence"}
