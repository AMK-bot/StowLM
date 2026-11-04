"""Booking, OOG and port restrictions for a departure stow (catalogue E8/E9/E3).

A cargo envelope is its rectangular occupied region in ship coordinates. Two
rectangles intersect only if their interiors overlap on all three axes. This
uses ISO 668 dimensions from :mod:`stowage.conventions`, actual heights below
cargo, and the asymmetric overhangs recorded in ``OutOfGauge``. Sources and
provisional interpretations are in ``rules/catalogue.toml``. D32 defaults are
resolved through ``Catalogue.resolve_parameter``: instance overrides first,
then any port-specific values, then the catalogue. Heat clearance measures the
distance between the cargo envelope and the vessel's heated-structure geometry.
Source: IMDG 42-24 section 7.1.2, protected-from-heat definition; applying it to
E8 requests remains provisional pending expert Q12. ASSUMPTION: D32/Q13 uses no
extra OOG margin beyond the declared overhang; intersecting cargo envelopes
remain prohibited. Neither numeric default is duplicated in this module.
Under the activated C4 contract, immediate adjacent cells are reserved in
addition to envelope intersections. ``killed_slots`` counts unique physical
20-foot cells lost, including unsupported capacity above direct kills. Hold
guide clearance has no model field; the labelled guided-
hold approximation below follows the lashing checker's representation.

ASSUMPTION: cell centres describe aligned, axis-parallel container positions;
40/45-foot centres are the mean of their two physical cell centres. Stack
``max_height_m`` is the available vertical cargo envelope, including OOG.
The quay cap uses tier ordinal in the vessel stack, as specified by the frozen
``QuayCraneSpec`` contract; its metre lift height is informational only.

The complete check costs O(C + S + N² + NH + NC), where C/S are vessel cells/stacks,
N is occupied positions and H heat regions. The quadratic term is needed only
when there is OOG cargo; regular booking and quay checks are linear.
"""

from __future__ import annotations

import math
from typing import Any

from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M
from stowage.checker.slots import cells_of_slot, parse_slot
from stowage.model import Instance, ShipState, Violation
from stowage.rules.catalogue import rule_freeze_v1_enabled

Bounds = tuple[tuple[float, float], tuple[float, float], tuple[float, float]]


def _parameter(instance: Instance, rule: str, *names: str) -> Any:
    values = {}
    for ref in instance.port.port_rules:
        if ref.rule_id == rule:
            values.update(ref.params)
    values.update(instance.rule_overrides.get(rule, {}))
    for name in names:
        if name in values:
            value = values[name]
            return value.get("value") if isinstance(value, dict) and "value" in value else value
    return None


def _nonnegative(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _interiors_overlap(a, b) -> bool:
    # Metre coordinates can differ by roundoff at a shared face. Use the same
    # nanometre numerical tolerance as the distance checks, not an OOG margin.
    return all(x[0] < y[1] - 1e-9 and y[0] < x[1] - 1e-9 for x, y in zip(a, b))


def _clearance_parameter(instance: Instance, rule: str, name: str, *aliases: str) -> Any:
    """Resolve D32 values without treating zero or an invalid override as absent.

    Legacy parameter aliases and port values remain supported. Instance values
    win even when they use an alias and the port supplies the canonical name.
    """
    from stowage.checker.rules import _catalogue_data

    catalogue = _catalogue_data()
    port_values = {}
    for ref in instance.port.port_rules:
        if ref.rule_id == rule:
            port_values.update(ref.params)
    for values in (instance.rule_overrides.get(rule, {}), port_values):
        for key in (name, *aliases):
            if key in values:
                return catalogue.resolve_parameter(rule, name, {rule: {name: values[key]}})
    return catalogue.resolve_parameter(rule, name, instance.rule_overrides)


def _killed_cells(instance, ship, cells, stacks, usable_cells):
    """Map OOG owners to E9 rule IDs and killed physical 20-foot cells.

    Neighbours are selected by physical coordinates, never row-number parity.
    A long footprint kills neighbours of both supporting cells. A killed cell
    disables every 20/40/45-foot position containing it; count its capacity once.
    E8 requests deliberately do not participate.
    """
    boxes = {box.id: box for box in instance.containers}
    killed = {}
    for p in ship.placements:
        box = boxes.get(p.container_id)
        ids = cells_of_slot(p.slot)
        if box is None or box.oog is None or any(cid not in usable_cells for cid in ids):
            continue
        oog = box.oog
        physical = [cells[cid] for cid in ids]
        rules = {"expert.e9.over_height": set(), "expert.e9.over_width_length": set()}
        if oog.over_height_m > 0:
            for cell in physical:
                column = stacks[cell.stack_id].cell_ids
                above = column[column.index(cell.id) + 1:]
                if above:
                    rules["expert.e9.over_height"].add(above[0])
        horizontal = rules["expert.e9.over_width_length"]
        has_horizontal = any((oog.over_width_port_m, oog.over_width_starboard_m,
                              oog.over_length_fore_m, oog.over_length_aft_m))
        for cell in physical if has_horizontal else []:
            address = parse_slot(cell.id)
            same_level = [c for cid, c in cells.items() if cid in usable_cells
                          and parse_slot(cid).tier == address.tier
                          and parse_slot(cid).on_deck == address.on_deck]
            for amount, direction in ((oog.over_width_port_m, 1), (oog.over_width_starboard_m, -1)):
                neighbours = [c for c in same_level if parse_slot(c.id).bay == address.bay
                              and direction * (c.tcg_m - cell.tcg_m) > 1e-9]
                if amount > 0 and neighbours:
                    nearest = min(neighbours, key=lambda c: abs(c.tcg_m - cell.tcg_m))
                    horizontal.add(nearest.id)
            for amount, direction in ((oog.over_length_fore_m, 1), (oog.over_length_aft_m, -1)):
                edge = (max if direction > 0 else min)(c.lcg_m for c in physical)
                neighbours = [c for c in same_level if parse_slot(c.id).row == address.row
                              and direction * (c.lcg_m - edge) > 1e-9]
                if amount > 0 and neighbours:
                    nearest = min(neighbours, key=lambda c: abs(c.lcg_m - edge))
                    horizontal.add(nearest.id)
        if has_horizontal:
            # Large or combined overhangs can reach beyond the immediate
            # neighbour, including diagonals. Reserve that capacity too, even
            # before a container is placed there. This is the D32 no-margin
            # envelope test against the nominal empty-cell ISO footprint.
            x = sum(c.lcg_m for c in physical) / len(physical)
            y = sum(c.tcg_m for c in physical) / len(physical)
            length = CONTAINER_LENGTH_M[box.length_ft]
            envelope = ((x - length / 2 - oog.over_length_aft_m, x + length / 2 + oog.over_length_fore_m),
                        (y - CONTAINER_WIDTH_M / 2 - oog.over_width_starboard_m,
                         y + CONTAINER_WIDTH_M / 2 + oog.over_width_port_m))
            for cell in same_level:
                nominal = ((cell.lcg_m - CONTAINER_LENGTH_M[20] / 2, cell.lcg_m + CONTAINER_LENGTH_M[20] / 2),
                           (cell.tcg_m - CONTAINER_WIDTH_M / 2, cell.tcg_m + CONTAINER_WIDTH_M / 2))
                if cell.id not in ids and _interiors_overlap(envelope, nominal):
                    horizontal.add(cell.id)
        killed[p.container_id] = rules
    return killed


def killed_slots(instance: Instance, ship: ShipState) -> set[str]:
    """Unique lost TEU capacity, closing direct kills upward (empty when off).

    The upward closure affects only the tie-break metric. Hard E9 violations
    continue to use direct killed cells from ``_killed_cells``.
    """
    if not rule_freeze_v1_enabled():
        return set()
    from stowage.checker.rules import _physical_geometry_index

    cells, stacks, usable, _ = _physical_geometry_index(instance)
    direct = {cid for rules in _killed_cells(instance, ship, cells, stacks, usable).values()
              for ids in rules.values() for cid in ids}
    return {above for cid in direct for column in [stacks[cells[cid].stack_id].cell_ids]
            for above in column[column.index(cid):] if above in usable}


def special_violations(
    instance: Instance, ship: ShipState, *, call_index: int, focus_container_id: str | None = None
) -> list[Violation]:
    """Return hard booking/OOG/port violations; never mutate the input state.

    With ``focus_container_id``, inspect booking checks only in its supporting
    stacks and OOG pairs involving those stacks. Legacy cost is
    O(C + S + N + AN + AH), with A affected boxes; activated killed-cell
    reservation adds O(NC). Unrelated pre-existing violations are excluded.

    Missing geometry or required per-instance policies fails closed with
    ``checker.missing_vessel_data``. Yard height and port draught are checked
    by the move engine and stability checker respectively, because this input
    contains neither departure yard state nor a computed hydrostatic condition.
    """
    result = []
    boxes = {box.id: box for box in instance.containers}
    from stowage.checker.rules import _geometry_index

    frozen_rules = rule_freeze_v1_enabled()
    placements = list(ship.placements)
    fixed_boxes = {}
    if frozen_rules:
        from stowage.checker.rules import _physical_geometry_index
        from stowage.checker.rules_background import fixed_cargo_records

        cells, stacks, usable_cells, geometry_problems = _physical_geometry_index(instance)
        for placement, box, original in fixed_cargo_records(instance):
            placements.append(placement)
            boxes[box.id] = box
            fixed_boxes[box.id] = original
    else:
        cells, stacks, usable_cells, geometry_problems = _geometry_index(instance)
    occupied = {}
    valid = []

    def add(rule, message, placements=(), measured=None, limit=None, unit=None):
        result.append(Violation(rule_id=rule, severity="hard", call_index=call_index,
                                containers=[p.container_id for p in placements if p.container_id not in fixed_boxes],
                                slots=[p.slot for p in placements], message=message,
                                measured=measured, limit=limit, unit=unit))

    def missing(rule, message, placements=()):
        add("checker.missing_vessel_data", f"{rule}: {message}", placements)

    for _, message in sorted(geometry_problems.items()):
        missing("special stowage", message)

    for p in sorted(placements, key=lambda p: p.slot):
        ids = cells_of_slot(p.slot)
        if p.container_id not in boxes or any(cid not in usable_cells for cid in ids):
            missing("special stowage", "container, cell or stack data is absent", [p])
            continue
        valid.append(p)
        for cid in ids:
            occupied[cid] = p

    # Compute actual floors from the heights below, rather than standard-height cell z.
    floors = {}
    for stack in stacks.values():
        if stack.id in geometry_problems:
            continue
        z = stack.base_z_m
        for cid in stack.cell_ids:
            if cid in occupied:
                floors[cid] = z
                z += boxes[occupied[cid].container_id].height_m

    affected_stacks = {
        cells[cid].stack_id for p in valid if p.container_id == focus_container_id
        for cid in cells_of_slot(p.slot)
    }
    affected_ids = {
        p.container_id for p in valid
        if focus_container_id is None or any(cells[cid].stack_id in affected_stacks for cid in cells_of_slot(p.slot))
    }
    envelopes = {}
    for p in valid:
        box = boxes[p.container_id]
        ids = cells_of_slot(p.slot)
        physical = [cells[cid] for cid in ids]
        x = sum(c.lcg_m for c in physical) / len(physical)
        y = sum(c.tcg_m for c in physical) / len(physical)
        z = max(floors[cid] for cid in ids)
        oog = box.oog
        length, width = CONTAINER_LENGTH_M[box.length_ft], CONTAINER_WIDTH_M
        envelopes[p.slot] = ((x-length/2-(oog.over_length_aft_m if oog else 0),
                              x+length/2+(oog.over_length_fore_m if oog else 0)),
                             (y-width/2-(oog.over_width_starboard_m if oog else 0),
                              y+width/2+(oog.over_width_port_m if oog else 0)),
                             (z, z+box.height_m+(oog.over_height_m if oog else 0)))
        if box.id not in affected_ids or box.id in fixed_boxes:
            continue
        requests = box.special_stowage
        deck = parse_slot(p.slot).on_deck
        for name, wrong in (("under_deck", deck), ("on_deck", not deck)):
            if name in requests and wrong:
                add(f"expert.e8.{name}", f"Booking requires {name.replace('_', ' ')} stowage.", [p])
        above = sorted({occupied[cid].slot for cell in physical
                        for cid in stacks[cell.stack_id].cell_ids
                        if cid in occupied and parse_slot(cid).tier > parse_slot(p.slot).tier})
        if "top_stowage" in requests and above:
            add("expert.e8.top_stowage", "Booking requires the top occupied position in every supporting stack.", [p])
        if oog and oog.over_height_m:
            if above and not frozen_rules:
                add("expert.e9.over_height", "Over-height cargo must be the top box in every supporting stack.", [p])
            available = min(stacks[c.stack_id].base_z_m + stacks[c.stack_id].max_height_m for c in physical)
            clearance = _parameter(instance, "expert.e9.over_height", "vertical_clearance", "available_clearance_m")
            if isinstance(clearance, dict):
                clearance = clearance.get(p.slot)
            if clearance is not None:
                parsed = _nonnegative(clearance)
                if parsed is None:
                    missing("expert.e9.over_height", "invalid vertical_clearance", [p])
                else:
                    available = min(available, z + parsed)
            if envelopes[p.slot][2][1] > available + 1e-9:
                add("expert.e9.over_height", "Over-height cargo exceeds the available vertical envelope.", [p],
                    envelopes[p.slot][2][1]-z, available-z, "m")
        if frozen_rules and oog and not deck and any((oog.over_width_port_m, oog.over_width_starboard_m,
                                                     oog.over_length_fore_m, oog.over_length_aft_m)):
            # APPROXIMATION: all represented holds are celled, matching the
            # lashing model. There is no non-celled-hold flag or guide clearance
            # geometry; treat nominal lateral/longitudinal clearance as zero.
            add("expert.e9.over_width_length", "Hold cell guides constrain lateral and longitudinal OOG overhang.", [p])
        if "away_from_heat" in requests:
            clearance = _nonnegative(_clearance_parameter(instance, "expert.e8.away_from_heat", "minimum_clearance", "heat_clearance_m"))
            if clearance is None:
                missing("expert.e8.away_from_heat", "minimum_clearance must be finite and nonnegative", [p])
            else:
                # Tank IDs and source IDs are separate namespaces. Legacy
                # same-ID records have no link; never ignore an explicit link.
                heat_ids = {h.tank_id if h.tank_id is not None else h.id
                            for h in instance.vessel.heat_sources
                            if h.kind == "heated_tank"}
                if any(t.heated and t.id not in heat_ids for t in instance.vessel.tanks):
                    missing("expert.e8.away_from_heat", "a heated tank has no heat-source geometry", [p])
                for heat in instance.vessel.heat_sources:
                    hb = ((heat.x_min_m, heat.x_max_m), (heat.y_min_m, heat.y_max_m), (heat.z_min_m, heat.z_max_m))
                    distance = math.sqrt(sum(max(a[0]-b[1], b[0]-a[1], 0)**2 for a, b in zip(envelopes[p.slot], hb)))
                    overlap = _interiors_overlap(envelopes[p.slot], hb)
                    if overlap or distance + 1e-9 < clearance:
                        add("expert.e8.away_from_heat", f"Cargo is too close to heat source {heat.id}.", [p], distance, clearance, "m")
        if deck:
            limit = instance.port.quay_crane.max_on_deck_tiers if call_index == instance.call_index else (
                instance.rotation.calls[call_index].max_on_deck_tiers if 0 <= call_index < len(instance.rotation.calls) else None)
            override = _parameter(instance, "port.e3.quay_height", "max_on_deck_tiers", "max_tiers")
            if call_index == instance.call_index and override is not None:
                limit = _nonnegative(override)
            tier = max(stacks[c.stack_id].cell_ids.index(c.id)+1 for c in physical)
            if limit is None:
                missing("port.e3.quay_height", "no crane tier limit is recorded for this call", [p])
            elif tier > limit:
                add("port.e3.quay_height", "On-deck position exceeds the call's crane tier limit.", [p], tier, limit, "1")

    if frozen_rules:
        owners = {p.container_id: p for p in valid}
        for owner, rules in _killed_cells(instance, ship, cells, stacks, usable_cells).items():
            p = owners[owner]
            for rule, ids in rules.items():
                blocked = {occupied[cid].container_id: occupied[cid] for cid in ids if cid in occupied}
                for q in blocked.values():
                    if {owner, q.container_id} & affected_ids:
                        add(rule, "OOG cargo kills an occupied neighbouring physical cell.", [p, q])

    affected_placements = [p for p in valid if p.container_id in affected_ids]
    seen_pairs = set()
    for p in valid:
        oog = boxes[p.container_id].oog
        if not oog or not any((oog.over_width_port_m, oog.over_width_starboard_m, oog.over_length_fore_m, oog.over_length_aft_m)):
            continue
        if frozen_rules:
            # C4 always prohibits envelope intersection. The activated contract
            # has no additional-margin parameter, but retain explicit margins
            # already recorded in an instance or port profile.
            configured = _parameter(instance, "expert.e9.over_width_length", "oog_clearance", "minimum_clearance_m")
            clearance = 0.0 if configured is None else _nonnegative(configured)
        else:
            clearance = _nonnegative(_clearance_parameter(instance, "expert.e9.over_width_length", "oog_clearance", "minimum_clearance_m"))
        if clearance is None:
            if p.container_id not in affected_ids:
                continue
            missing("expert.e9.over_width_length", "oog_clearance must be finite and nonnegative", [p])
            continue
        neighbours = valid if p.container_id in affected_ids else affected_placements
        for q in neighbours:
            key = tuple(sorted((p.slot, q.slot)))
            if p == q or key in seen_pairs or not ({p.container_id, q.container_id} & affected_ids):
                continue
            a, b = envelopes[p.slot], envelopes[q.slot]
            horizontal_gap = math.hypot(*(max(a[i][0]-b[i][1], b[i][0]-a[i][1], 0) for i in (0, 1)))
            horizontal_overlap = _interiors_overlap(a[:2], b[:2])
            if _interiors_overlap(a[2:], b[2:]) and (horizontal_overlap or horizontal_gap + 1e-9 < clearance):
                seen_pairs.add(key)
                add("expert.e9.over_width_length", "OOG cargo or its required clearance blocks an occupied neighbouring position.", [p, q])

    rule = "port.call.length"
    maximum = _parameter(instance, rule, "max_loa", "max_loa_m")
    configured = any(ref.rule_id == rule for ref in instance.port.port_rules) or rule in instance.rule_overrides
    if configured and focus_container_id is None:
        maximum = _nonnegative(maximum)
        if maximum is None or instance.vessel.hull is None:
            missing(rule, "maximum length and vessel LOA must be supplied")
        elif instance.vessel.hull.loa_m > maximum:
            add(rule, "Vessel overall length exceeds the port allowance.", measured=instance.vessel.hull.loa_m, limit=maximum, unit="m")
    return result
