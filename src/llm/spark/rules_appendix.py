"""Plain-language rules-and-limits appendix for the Muse Spark baseline (D76).

StowLM's compact observation omits the static rule and physics tables because
its decoding mask enforces them. Spark has no mask, so D76 gives it the limits
it must satisfy, written from the *same* values the checker reads: vessel
stack/cell records, ``Vessel.dg_doc_areas``, the IMDG 42-24 tables, the active
rule-catalogue snapshot (pending contracts merged under rule-freeze-v1) with
instance overrides resolved by the checker's own ``_parameter`` lookup, the
checker's loading-policy and minimum-GM resolvers, the D55 visibility caps, the
D74 programme parameters and the port profile helpers. Nothing is re-typed
here except IMDG/IS Code wording and units.

Limits that are only reachable through the expensive physics (GZ curves,
weather criterion, longitudinal strength, lashing forces, actual sight lines,
in-port equilibrium) are stated as criteria, not computed: the checker
evaluates them on the finished plan. The text is deterministic (same instance
and catalogue snapshot -> identical bytes) and groups repeated per-stack data
into bay/row/tier ranges to keep input tokens down.

Sources: stowage.checker.rules (stack mass/height, weight order),
stowage.checker.rules_imdg (DoC areas, static DG stowage screen),
stowage.rules.imdg.tables (7.2.4/7.4.3), stowage.checker.stability
(loading policy, GM floor, in-port P-criteria), stowage.checker.lashing (D56),
stowage.checker.in_port_programme (D74), stowage.feasibility.visibility_bounds
(D55), stowage.ports (H and yard limits), stowage.moves._port_rules (C5).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable

from stowage.conventions import parse_slot
from stowage.model import Instance

APPENDIX_VERSION = "1.0"
HEADER = "Rules and limits (from the checker's catalogue):"


# ---------------------------------------------------------------- formatting

def _num(value: float, digits: int = 3) -> str:
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _kg(value: float) -> str:
    return f"{_num(value, 0)} kg"


def _deg(rad: float) -> str:
    return f"{_num(math.degrees(rad), 2)} deg"


def _runs(values: Iterable[int], width: int, step: int = 1) -> str:
    numbers = sorted(set(values))
    parts, start, end = [], None, None
    for value in numbers:
        if end is not None and value == end + step:
            end = value
            continue
        if start is not None:
            parts.append(f"{start:0{width}d}" if start == end else f"{start:0{width}d}-{end:0{width}d}")
        start = end = value
    if start is not None:
        parts.append(f"{start:0{width}d}" if start == end else f"{start:0{width}d}-{end:0{width}d}")
    return ",".join(parts)


def _bays(bays: Iterable[int]) -> str:
    bays = sorted(set(bays))
    parity = {bay % 2 for bay in bays}
    label = "bay" if len(bays) == 1 else "bays"
    if len(parity) == 1 and len(bays) > 1:
        label = ("odd " if parity == {1} else "even ") + label
        return f"{label} {_runs(bays, 3, 2)}"
    return f"{label} {_runs(bays, 3, 2 if len(parity) == 1 else 1)}"


def _positions(stacks: Iterable[tuple[bool, int, int]]) -> str:
    """(on_deck, bay, row) -> 'deck odd bays 001-005 rows 01-04; hold bay 007 rows 02-03'."""
    by_level: dict[bool, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for on_deck, bay, row in stacks:
        by_level[on_deck][bay].add(row)
    parts = []
    for on_deck in (False, True):
        if on_deck not in by_level:
            continue
        rows_to_bays: dict[tuple[int, ...], list[int]] = defaultdict(list)
        for bay, rows in by_level[on_deck].items():
            rows_to_bays[tuple(sorted(rows))].append(bay)
        chunks = [f"{_bays(bays)} rows {_runs(rows, 2)}"
                  for rows, bays in sorted(rows_to_bays.items(), key=lambda item: (min(item[1]), item[0]))]
        parts.append(("deck " if on_deck else "hold ") + ", ".join(chunks))
    return "; ".join(parts) if parts else "none"


def _slot_set(slots: Iterable[str]) -> str:
    """Ship slots/cells -> grouped stacks with their tier runs (tiers 02.. hold, 82.. deck)."""
    tiers: dict[tuple[bool, int, int], set[int]] = defaultdict(set)
    for slot in slots:
        address = parse_slot(slot)
        tiers[(address.on_deck, address.bay, address.row)].add(address.tier)
    groups: dict[tuple[bool, tuple[int, ...]], list[tuple[bool, int, int]]] = defaultdict(list)
    for key, values in tiers.items():
        groups[(key[0], tuple(sorted(values)))].append(key)
    out = []
    for (_, values), keys in sorted(groups.items(), key=lambda item: (item[0][0], min(item[1]), item[0][1])):
        out.append(f"{_positions(keys)} tiers {_runs(values, 2, 2)}")
    return " | ".join(out) if out else "none"


def _boxes(indices: Iterable[int]) -> str:
    indices = sorted(set(indices))
    return ("box " if len(indices) == 1 else "boxes ") + _runs(indices, 3)


# ---------------------------------------------------------------- lookups

def _parameter(instance: Instance, rule: str, name: str) -> Any:
    """The checker's own override-first lookup on the active catalogue snapshot."""
    from stowage.checker.rules import _parameter as checker_parameter

    return checker_parameter(instance, rule, name)


def _rule(rule: str):
    from stowage.checker.rules import _catalogue

    return _catalogue().get(rule)


def _box_index(instance: Instance) -> dict[str, int]:
    from llm.stowlm.format import BoxIndex

    index = BoxIndex([box.id for box in instance.containers])
    return dict(index.by_id)


def _relevant_boxes(instance: Instance):
    """Boxes this plan handles or carries: load list, discharges and onboard cargo."""
    ids = set(instance.load_list) | set(instance.discharge_list)
    ids |= {p.container_id for p in instance.onboard.placements}
    return [box for box in instance.containers if box.id in ids]


def _hazards(dg) -> list:
    out = [dg]
    for extra in dg.additional:
        out.extend(_hazards(extra))
    return out


# ---------------------------------------------------------------- sections

def _intro(instance: Instance) -> list[str]:
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    calls = instance.rotation.calls
    route = " > ".join(f"{call.index}:{call.locode}" for call in calls)
    return [
        f"Ship {instance.vessel.id}; port {instance.port.locode} ({instance.port.archetype}), call "
        f"{instance.call_index} of rotation {route}. Stage {instance.stage}. Rule set: "
        f"{'rule-freeze-v1 active' if rule_freeze_v1_enabled() else 'legacy (rule-freeze-v1 off)'}.",
        "Every limit below is hard (a better move count never excuses a breach) unless marked preference. "
        "Box numbers are the action indices; masses are verified gross mass (VGM); slots are BBBRRTT "
        "(odd bay = 20ft cell, even bay = 40/45ft slot over the two odd bays; hold tiers 02.., deck tiers 82..).",
    ]


def _stack_section(instance: Instance) -> list[str]:
    vessel = instance.vessel
    cells = {cell.id: cell for cell in vessel.cells}
    groups: dict[tuple, list[tuple[bool, int, int]]] = defaultdict(list)
    for stack in vessel.stacks:
        tiers = tuple(parse_slot(cid).tier for cid in stack.cell_ids)
        key = (stack.on_deck, tiers, stack.max_mass_kg, stack.max_mass_20_kg, stack.max_mass_40_kg,
               round(stack.max_height_m, 3))
        groups[key].append((stack.on_deck, stack.bay, stack.row))
    lines = ["1 Stacks (expert.physical.stack_weight, expert.physical.tier_height; vessel stack records):"]
    for key, members in sorted(groups.items(), key=lambda item: (item[0][0], min(item[1]))):
        on_deck, tiers, general, m20, m40, height = key
        ratings = [f"max stack mass {_kg(general)}"]
        if m20 is not None:
            ratings.append(f"{_kg(m20)} if any 20ft box is in the stack")
        if m40 is not None:
            ratings.append(f"{_kg(m40)} if only 40/45ft boxes")
        lines.append(f"- {_positions(members)}: tiers {_runs(tiers, 2, 2)} ({len(tiers)} tier{'s' if len(tiers) != 1 else ''}), "
                     f"max height {_num(height)} m above stack base; " + ", ".join(ratings) + ".")
    lines.append("  Stack mass = sum of VGM resting on the stack base, boxes already aboard included; a 40/45ft box "
                 "puts half its mass on each of its two 20ft stacks. Stack height = sum of box heights plus "
                 "over-height, using each box's declared height.")
    no40 = sorted(cid for cid, cell in cells.items() if not cell.accepts_40)
    no20 = sorted(cid for cid, cell in cells.items() if not cell.accepts_20)
    yes45 = sorted(cid for cid, cell in cells.items() if cell.accepts_45)
    plugs = sorted(cid for cid, cell in cells.items() if cell.reefer_plug)
    for label, ids in (("Cells refusing 20ft", no20), ("Cells refusing 40ft", no40)):
        if ids:
            lines.append(f"  {label}: {_slot_set(ids)}.")
    if any(box.length_ft == 45 for box in instance.containers):
        lines.append(f"  45ft boxes only where both cells accept 45ft: {_slot_set(yes45)}.")
    if any(box.reefer for box in _relevant_boxes(instance)):
        lines.append(f"  Reefer plugs (an operating reefer needs one in its footprint, expert.physical.reefer_plug): "
                     f"{_slot_set(plugs)}.")
    lines.extend(_visibility_caps(instance))
    return lines


def _visibility_caps(instance: Instance) -> list[str]:
    """D55 necessary caps, listed only where they can bind at this call.

    A cap is omitted when the stack's own height limit, or H on-deck tiers of the
    tallest box in this call's cargo, already stays below it. Truncated (not
    rounded) to cm so the stated cap is never looser than the checker's.
    """
    try:
        from stowage.feasibility.visibility_bounds import compute_visibility_caps

        caps = compute_visibility_caps(instance).top_by_stack
    except Exception as exc:  # noqa: BLE001 - missing evidence disables this extra screen only
        return [f"  Visibility caps unavailable ({type(exc).__name__}); the checker still applies SOLAS V/22."]
    port = instance.port
    height = port.max_crane_height_tiers if port.max_crane_height_tiers is not None else (
        port.quay_crane.max_on_deck_tiers if port.quay_crane is not None else None)
    tallest = max((box.height_m + (box.oog.over_height_m if box.oog else 0.0)
                   for box in _relevant_boxes(instance)), default=0.0)
    by_bay: dict[tuple[bool, int], dict[int, float]] = defaultdict(dict)
    for stack in instance.vessel.stacks:
        cap = caps.get(stack.id)
        if cap is None or not math.isfinite(cap):
            continue
        allowed = math.floor((cap - stack.base_z_m) * 100 + 1e-9) / 100
        reach = stack.max_height_m
        if stack.on_deck and height is not None:
            reach = min(reach, height * tallest)
        if allowed < reach - 1e-9:
            by_bay[(stack.on_deck, stack.bay)][stack.row] = allowed
    if not by_bay:
        return []
    chunks = []
    for (on_deck, bay), rows in sorted(by_bay.items()):
        values: dict[float, list[int]] = defaultdict(list)
        for row, allowed in rows.items():
            values[allowed].append(row)
        detail = ", ".join(f"rows {_runs(r, 2)} {_num(v, 2)}" for v, r in sorted(values.items()))
        chunks.append(f"{'deck' if on_deck else 'hold'} bay {bay:03d} {detail}")
    return ["  Bridge visibility cap (D55, necessary only), max box-top height above stack base in m: "
            + "; ".join(chunks) + "."]


def _weight_section(instance: Instance) -> list[str]:
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    lines = ["2 Stacking order (expert.moves.bottom_up, expert.physical.no_20_on_40, expert E1/E2/E6):",
             "- Fill each stack bottom-up with no gaps; one box per cell; no 20ft box above a 40/45ft box."]
    if rule_freeze_v1_enabled() and _parameter(instance, "expert.e2.weight_order", "weight_order_mode") == "gross_mass_kg":
        tolerance = _parameter(instance, "expert.e2.weight_order", "weight_order_tolerance_kg")
        lines.append(f"- Heavy low, no heavier on lighter: every box must weigh at most {_kg(tolerance)} more than "
                     "each box below it in the same stack (equal masses allowed; a 40ft box over two 20ft boxes "
                     "is compared with both).")
    else:
        medium = _parameter(instance, "expert.e2.weight_order", "medium_min_kg")
        heavy = _parameter(instance, "expert.e2.weight_order", "heavy_min_kg")
        tolerance = _parameter(instance, "expert.e2.weight_order", "tolerance")
        threshold = _parameter(instance, "expert.e1.heavy_bottom", "heavy_threshold")
        lines.append(f"- Weight classes L < {_kg(medium)} <= M < {_kg(heavy)} <= H. No box may be more than "
                     f"{_num(tolerance, 0)} class(es) heavier than any box below it in the same stack; no box of "
                     f"{_kg(threshold)} or more above a box lighter than that.")
    e6 = _rule("expert.e6.heavy_under_deck")
    if e6 is not None and e6.hardness == "soft":
        threshold = _parameter(instance, "expert.e6.heavy_under_deck", "heavy_on_deck_threshold_kg")
        lines.append(f"- Preference only (after legality and fewest lifts): boxes of {_kg(threshold)} or more "
                     "under deck rather than on deck.")
    elif e6 is not None:
        threshold = _parameter(instance, "expert.e6.heavy_under_deck", "heavy_threshold")
        lines.append(f"- Boxes of {_kg(threshold)} or more must go under deck unless no compatible hold slot "
                     "remains empty at departure.")
    if instance.stage == "B":
        lines.append("- Stage B: order breaches already aboard on arrival are tolerated; new placements must obey.")
    return lines


def _special_section(instance: Instance) -> list[str]:
    index = _box_index(instance)
    requests: dict[str, list[int]] = defaultdict(list)
    oog = []
    for box in _relevant_boxes(instance):
        for request in box.special_stowage:
            requests[request].append(index[box.id])
        if box.oog is not None:
            oog.append(index[box.id])
    if not requests and not oog:
        return []
    lines = ["2b Booking requests (expert E8/E9, hard):"]
    meaning = {
        "on_deck": "must be on deck",
        "under_deck": "must be under deck",
        "top_stowage": "must be the top box of its stack at departure",
        "away_from_heat": (f"must stay at least {_num(_parameter(instance, 'expert.e8.away_from_heat', 'minimum_clearance'))}"
                           " m from heated structures (checker geometry)"),
    }
    for request, boxes in sorted(requests.items()):
        lines.append(f"- {_boxes(boxes)}: {meaning.get(request, request)}.")
    if oog:
        lines.append(f"- {_boxes(oog)} out of gauge: over-height cargo kills the cells above it, over-width/length "
                     "kills the neighbouring cells; killed cells must stay empty; under deck the cell guides refuse "
                     "over-width/length.")
    return lines


def _dg_section(instance: Instance) -> list[str]:
    from stowage.checker.rules import _geometry_index
    from stowage.checker.rules_imdg import SHIP, _closed, _doc_areas, _stowage
    from stowage.rules.imdg.tables import _read, containership_segregation, segregation_codes

    vessel = instance.vessel
    lines = ["3 Dangerous goods (SOLAS II-2/19 DoC, IMDG 7.2.4 / 7.4.3, imdg.stowage.3_2):"]
    stacks = {stack.id: stack for stack in vessel.stacks}
    for area in sorted(vessel.dg_doc_areas, key=lambda area: area.id):
        members = [(s.on_deck, s.bay, s.row) for sid in area.stack_ids if (s := stacks.get(sid)) is not None]
        if not members:
            continue
        lines.append(f"- DoC area {area.id} ({_positions(members)}): classes {', '.join(area.classes_allowed) or 'none'}; "
                     f"packing {', '.join(area.packing_forms_allowed) or 'none'}.")
    undocumented = [(s.on_deck, s.bay, s.row) for s in vessel.stacks
                    if s.dg_doc_area_id is None or s.dg_doc_area_id not in {a.id for a in vessel.dg_doc_areas}]
    if undocumented:
        lines.append(f"- No DoC area (no DG at all): {_positions(undocumented)}.")
    index = _box_index(instance)
    dg_boxes = [box for box in _relevant_boxes(instance) if box.dg is not None]
    if not dg_boxes:
        lines.append("- No dangerous goods in this call's cargo.")
        return lines + _port_dg_lines(instance, dg_boxes)
    cells, stack_map, usable, _ = _geometry_index(instance)
    areas = _doc_areas(instance)
    call = instance.call_index
    codes_meaning = segregation_codes()
    for box in sorted(dg_boxes, key=lambda b: index[b.id]):
        entries = _hazards(box.dg)
        label = "; ".join(
            f"UN{dg.un_number} class {dg.imdg_class}" + (f" (sub {','.join(dg.subsidiary_risks)})" if dg.subsidiary_risks else "")
            + (f" PG {dg.packing_group}" if dg.packing_group else "") for dg in entries)
        if box.id in instance.load_list:
            # Same static screen the checker's E6 capability query uses (ship=None):
            # DoC area, packing form, category on/under deck, SW/H provisions and
            # declared assessment scenarios. Upper-tier geometry that needs actual
            # support heights is deferred to the full check (necessary, not sufficient).
            candidates = _candidate_slots(box, cells, usable)
            permitted = [slot for slot in candidates
                         if not _stowage(instance, box, slot, call, cells, stack_map, areas)]
            where = f"static DG screen passes only at {_slot_set(permitted)}" if permitted else \
                "no ship slot passes the static DG screen (cannot be legally loaded)"
        else:
            where = "already aboard or discharging"
        sg = sorted({code for dg in entries for code in dg.segregation_codes})
        sg_text = ("; SG codes: " + " ".join(f"{code} ({codes_meaning[code].meaning.rstrip('.')})"
                                            if code in codes_meaning else code for code in sg)) if sg else ""
        lines.append(f"- box {index[box.id]:03d} {label}: {where}{sg_text}.")
    meanings = _read("segregation_7_2_4.json")["meanings"]
    ship_type = instance.rule_overrides.get(SHIP, {}).get("ship_type")
    by_level: dict[tuple[int, str | None], list[str]] = defaultdict(list)
    uncleared: dict[str, list[str]] = defaultdict(list)
    for a_pos, a in enumerate(dg_boxes):
        for b in dg_boxes[a_pos + 1:]:
            level, problems = _pair_requirement(instance, a, b, cells)
            pair = "-".join(f"{i:03d}" for i in sorted((index[a.id], index[b.id])))
            for message in problems:
                uncleared[message].append(pair)
            if level:
                kind = _pair_kind(instance, [a, b], _closed) if ship_type in {"closed_holds", "hatchless"} else None
                by_level[(level, kind)].append(pair)
    if len(dg_boxes) > 1 and not by_level and not uncleared:
        lines.append("- Segregation (7.2.4 plus DGL SG codes, as the checker computes it): none required between "
                     "these DG boxes.")
    for (level, kind), pairs in sorted(by_level.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        lines.append(f"- Segregation level {level} \"{meanings.get(str(level), level)}\" (7.2.4 with DGL SG codes) "
                     f"for box pairs {', '.join(sorted(pairs))}.")
        if ship_type not in {"closed_holds", "hatchless"}:
            lines.append("  This instance declares no 7.4.3 ship type (closed holds/hatchless), so the checker "
                         "cannot verify any separation for these pairs and rejects them wherever stowed (fail closed).")
        elif kind is None:
            lines.append("  CTU closed/open status unknown: the checker cannot clear these pairs (fail closed).")
        else:
            lines.append("  " + _ship_cells(level, ship_type, kind, containership_segregation))
    for message, pairs in sorted(uncleared.items()):
        lines.append(f"- Box pairs {', '.join(sorted(pairs))}: {message.rstrip('.')} (fail closed).")
    return lines + _port_dg_lines(instance, dg_boxes)


def _pair_requirement(instance: Instance, a, b, cells) -> tuple[int, list[str]]:
    """Run the checker's own pair rule (7.2.4 + SG codes) and read its required level.

    Probe with the 7.4.3 geometry facts removed: ``_pair`` then stops before any
    distance test and reports the numeric level it needs in its fail-closed
    diagnostic. Slot-dependent SG26 is probed in hold cells so it does not fire;
    it is described by its SG meaning instead. Other diagnostics (unresolved
    codes, class-1 compatibility) are returned verbatim.
    """
    import re

    from stowage.checker.rules_imdg import SHIP, _pair

    probe = instance.model_copy(update={"rule_overrides": {
        key: value for key, value in instance.rule_overrides.items() if key != SHIP}})
    hold = sorted(cid for cid, cell in cells.items() if not parse_slot(cid).on_deck) or sorted(cells)
    slot_a = slot_b = hold[0]
    level, problems = 0, []
    for violation in _pair(probe, a, slot_a, b, slot_b, instance.call_index, cells):
        match = re.fullmatch(r"IMDG segregation level (\d) requires missing ship/compartment data\.",
                             violation.message)
        if match:
            level = max(level, int(match[1]))
        else:
            problems.append(violation.message)
    return level, sorted(set(problems))


def _pair_kind(instance, boxes, closed_fact) -> str | None:
    states = set()
    for box in boxes:
        state = closed_fact(instance, box, "")
        if state is None:
            return None
        states.add(state)
    return "closed_closed" if states == {True} else "open_open" if states == {False} else "closed_open"


def _ship_cells(level: int, ship_type: str, pair: str, lookup) -> str:
    parts = []
    for orientation, deck, label in (("vertical", None, "vertical"), ("fore_aft", "on", "fore-aft on deck"),
                                     ("fore_aft", "under", "fore-aft under deck"),
                                     ("athwartships", "on", "athwartships on deck"),
                                     ("athwartships", "under", "athwartships under deck")):
        cell = lookup(level, ship_type=ship_type, orientation=orientation, container_pair=pair, deck=deck)
        parts.append(f"{label}: {cell.text}")
    return (f"7.4.3 {ship_type.replace('_', ' ')}, {pair.replace('_', '/')} CTUs (container space = 6 m fore-aft, "
            "2.4 m athwartships): " + "; ".join(parts) + ".")


def _candidate_slots(box, cells, usable) -> list[str]:
    from stowage.conventions import format_slot

    out = set()
    for cell in cells.values():
        if cell.id not in usable:
            continue
        address = parse_slot(cell.id)
        if box.length_ft == 20:
            if cell.accepts_20:
                out.add(cell.id)
            continue
        for bay in (address.bay - 1, address.bay + 1):
            if 0 < bay < 999:
                slot = format_slot(bay, address.row, address.tier)
                try:
                    from stowage.conventions import cells_of_slot

                    footprint = cells_of_slot(slot)
                except ValueError:
                    continue
                if all(cid in usable and getattr(cells[cid], f"accepts_{box.length_ft}") for cid in footprint):
                    out.add(slot)
    return sorted(out)


def _port_dg_lines(instance: Instance, dg_boxes) -> list[str]:
    from stowage.ports import resolve_port_call_profile
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if not rule_freeze_v1_enabled():
        return []
    lines = []
    for call in instance.rotation.calls:
        if call.index < instance.call_index:
            continue
        current = call.index == instance.call_index
        try:
            profile = instance.port if current else resolve_port_call_profile(call, current_port=instance.port)
        except (ValueError, OSError) as exc:
            lines.append(f"- call {call.index} {call.locode}: port rules unresolved ({type(exc).__name__}); "
                         "boxes for it fail closed (port.call.profile_resolution).")
            continue
        if profile is None:
            continue
        refused = sorted({str(v) for ref in profile.port_rules if ref.rule_id == "port.dg.refused_class"
                          for v in ref.params.get("classes", [])})
        if refused:
            scope = ("no load, discharge or restow here of that class" if current else
                     f"boxes of that class bound for {call.locode} may not be loaded")
            lines.append(f"- call {call.index} {call.locode} refuses primary class {', '.join(refused)} "
                         f"(port.dg.refused_class): {scope}; untouched transit aboard is allowed.")
        if current:
            direct = next((ref for ref in profile.port_rules if ref.rule_id == "port.dg.direct_delivery"), None)
            if direct is not None:
                classes = ", ".join(str(v) for v in direct.params.get("classes", []))
                lines.append(f"- {call.locode} direct delivery for primary class {classes} (port.dg.direct_delivery): "
                             "loaded from / discharged to a ship-side truck in one lift; never yard or quay buffer.")
    return lines


def _port_section(instance: Instance) -> list[str]:
    from stowage.ports import crane_height_at_call, resolve_port_call_profile, yard_blocking_tiers
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    port = instance.port
    lines = ["4 Cranes and yard (port.e3.*, D62):"]
    height = port.max_crane_height_tiers if port.max_crane_height_tiers is not None else (
        port.quay_crane.max_on_deck_tiers if port.quay_crane is not None else None)
    later = []
    for call in instance.rotation.calls:
        if call.index <= instance.call_index:
            continue
        try:
            value = crane_height_at_call(call, current_port=port)
        except (ValueError, OSError):
            value = None
        later.append(f"{call.index}:{call.locode} {value if value is not None else 'unknown'}")
    lines.append(f"- H = {height if height is not None else 'unknown'} on-deck tiers at {port.locode} (counted from "
                 "the deck or hatch-cover base, lowest tier = 1): no on-deck stack taller than H."
                 + (f" Later calls H: {'; '.join(later)}." if later else ""))
    if rule_freeze_v1_enabled():
        lines.append(f"- Crane crossing: a lift may not pass over an on-deck stack already at H or more tiers between "
                     f"the {port.berthing_side}-side quay edge (berthing side) and the target row, source and target "
                     "stacks excluded; holds never block." + (" Stage A: load far-side rows first."
                                                             if instance.stage == "A" else ""))
    blocks: dict[tuple, list[str]] = defaultdict(list)
    for block in port.yard.blocks:
        try:
            limit = yard_blocking_tiers(port, block)
        except ValueError:
            limit = None
        key = (limit, block.truck_lane_side, tuple(sorted(block.reefer_rows)), block.dg_block,
               tuple(sorted(block.allowed_lengths_ft)))
        blocks[key].append(block.id)
    for (limit, lane, reefer_rows, dg_block, lengths), ids in sorted(blocks.items(), key=lambda item: min(item[1])):
        extra = [f"truck lane {lane}-row side"]
        if reefer_rows:
            extra.append(f"reefer rows {_runs(reefer_rows, 2)}")
        extra.append("DG allowed" if dg_block else "no DG")
        extra.append("lengths " + "/".join(str(v) for v in lengths) + "ft")
        lines.append(f"- yard {'blocks' if len(ids) > 1 else 'block'} {', '.join(sorted(ids))}: max "
                     f"{limit if limit is not None else 'unknown'} tiers "
                     f"({'storage and crossing limit' if rule_freeze_v1_enabled() else 'storage limit'}); " + ", ".join(extra) + ".")
    if port.max_draught_m is not None:
        lines.append(f"- Port/berth max draught {_num(port.max_draught_m)} m (port.call.draft).")
    return lines


def _stability_section(instance: Instance) -> list[str]:
    from stowage.checker.stability import _loading_policy, _minimum_loading_gm
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    hull = instance.vessel.hull
    lines = ["5 Stability and strength (IS Code, IACS; checked by the full physics at departure):"]
    if hull is None:
        return lines + ["- No hull data in this instance: the checker reports checker.missing_vessel_data."]
    try:
        min_trim, max_trim, min_mass, max_mass = _loading_policy(instance)
        trim = f"trim between {_num(min_trim)} and {_num(max_trim)} m"
        mass = f"; displacement {_num(min_mass / 1000, 1)}-{_num(max_mass / 1000, 1)} t"
    except (ValueError, TypeError) as exc:
        trim, mass = f"trim/displacement policy unavailable ({type(exc).__name__}; the checker fails closed)", ""
    try:
        gm = f"GM after free-surface correction >= {_num(_minimum_loading_gm(instance))} m"
    except (ValueError, TypeError) as exc:
        gm = f"GM floor unavailable ({type(exc).__name__}; fails closed)"
    lashing_gm = (f" and <= {_num(hull.max_gm_lashing_m)} m (D56 lashing design GM; above it lashing is invalid)"
                  if hull.max_gm_lashing_m is not None else "")
    draught = f"draught fore and aft <= {_num(hull.max_draught_m)} m"
    if instance.port.max_draught_m is not None:
        draught += f" and <= port {_num(instance.port.max_draught_m)} m"
    lines.append(f"- Departure: {gm}{lashing_gm}; |list| <= {_deg(hull.max_list_rad)}; {trim}; {draught}; "
                 f"forward draught >= {_num(hull.min_draught_fwd_m)} m{mass}.")
    crit = []
    for rule, name, unit in (("iscode.a2_2.gz_area_0_30", "minimum", "m.rad to 30 deg"),
                             ("iscode.a2_2.gz_area_0_40", "minimum", "m.rad to 40 deg/flooding"),
                             ("iscode.a2_2.gz_area_30_40", "minimum", "m.rad 30-40 deg"),
                             ("iscode.a2_2.gz_at_30", "minimum", "m GZ at >= 30 deg")):
        crit.append(f"{_num(_parameter(instance, rule, name), 3)} {unit}")
    peak = _parameter(instance, "iscode.a2_2.max_gz_angle", "minimum")
    heel = _parameter(instance, "iscode.a2_3.weather", "max_steady_heel")
    # Expensive physics: GZ curve, weather criterion, strength envelopes and the
    # SOLAS V/22 sight line are criteria here; the checker computes them.
    lines.append(f"- Intact stability criteria (computed by the checker from the GZ curve): {', '.join(crit)}; "
                 f"max GZ at >= {_deg(peak)}; weather criterion (steady wind heel <= {_deg(heel)}); "
                 "IS Code B2.3 containership check.")
    lines.append("- Longitudinal strength (iacs.s11a): still-water shear/bending inside each station envelope, "
                 "plus wave loads. Large mass concentrations at the ends or middle raise them.")
    blind = _parameter(instance, "solas.v22.visibility", "max_blind_distance")
    factor = _parameter(instance, "solas.v22.visibility", "ship_length_multiplier")
    lines.append(f"- Bridge visibility (solas.v22.visibility): sea surface hidden ahead of the bow <= min({_num(factor)}"
                 f" x ship length, {_num(blind)} m); blind sectors limited. Tall deck stacks forward of the bridge "
                 "are the usual cause.")
    if rule_freeze_v1_enabled():
        angle = _parameter(instance, "port.stability.cargo_operations", "maximum_trim_angle_rad")
        fluid = _parameter(instance, "port.stability.cargo_operations", "minimum_fluid_gm_m")
        lines.append(f"- In port after every lift (port.stability.cargo_operations, D64): GM >= {_num(fluid)} m; "
                     f"|list| <= {_deg(hull.max_list_rad)}; |trim| <= Lpp x tan({_deg(angle)}) = "
                     f"{_num(hull.lpp_m * math.tan(angle), 2)} m; draughts as at departure; an open hatch's "
                     "coaming must stay above water. Alternate port/starboard placements to limit list.")
    return lines


def _ballast_section(instance: Instance) -> list[str]:
    from stowage.checker.in_port_programme import programme_parameters
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    lines = ["6 Ballast: you do not write ballast; the shared ballast finisher sets it after your plan. "
             "Your stow must leave a ballast solution that meets section 5."]
    if not rule_freeze_v1_enabled():
        return lines
    try:
        p = programme_parameters(instance)
    except (ValueError, TypeError, AttributeError) as exc:
        lines.append(f"- Pump-rate check (port.ballast.transfer_rate, D74): operating rates not declared in this "
                     f"instance ({type(exc).__name__}); the checker fails closed when this check is active.")
        return lines
    cranes = min(p.port_cranes, p.max_cranes)
    t_lift = 3600 / (p.gross_moves_per_hour * cranes)
    lines.append(f"- Pump-rate check (port.ballast.transfer_rate, D74): ballast {_num(p.ballast_pump_m3_h, 0)} m3/h, "
                 f"anti-heel transfer {_num(p.heel_transfer_m3_h, 0)} m3/h; {cranes} cranes x "
                 f"{_num(p.gross_moves_per_hour, 1)} moves/h = {_num(t_lift, 1)} s per lift; "
                 f"{_num(p.pre_operation_allowance_h, 2)} h before the first lift. Ballast changes must be pumpable in "
                 "the lifts before the changeover, so very short plans cannot absorb large ballast changes.")
    return lines


def _lashing_section(instance: Instance) -> list[str]:
    rule = "iacs.member_irs.container_securing"
    # Lashing forces need the IRS motion model at the departure GM (expensive
    # physics); state the limits it checks against instead of computing them.
    values = {name: _parameter(instance, rule, name) for name in (
        "transverse_racking", "corner_post_compression", "corner_casting_tension",
        "vertical_lashing_pull", "fitting_swl_fraction")}
    kn = {k: _num(v / 1000, 0) for k, v in values.items() if k != "fitting_swl_fraction"}
    return ["7 Lashing (iacs.member_irs.container_securing; APPROXIMATION: synthetic IRS screening model, checked "
            f"by the checker at departure GM): transverse racking <= {kn['transverse_racking']} kN, corner post "
            f"compression <= {kn['corner_post_compression']} kN, corner tension <= {kn['corner_casting_tension']} kN, "
            f"lashing pull <= {kn['vertical_lashing_pull']} kN, fittings at {_num(values['fitting_swl_fraction'] * 100, 0)}% "
            "of SWL. Heavy boxes high in deck stacks and high GM raise these loads."]


def _moves_section(instance: Instance) -> list[str]:
    return ["8 Moves (expert.moves.*, expert.rotation.*): only the top box of a stack can be lifted; a hold stack "
            "needs its hatch cover off, which needs the deck stacks on that cover empty. A box under a box for a "
            "later port is reachable only by restowing (each extra lift counts). Load every load-list box; the "
            "objective after legality is fewest total lifts (yard rehandles, restows now and later, hatch covers)."]


def rules_appendix(instance: Instance) -> str:
    """Deterministic plain-language limits for one instance (D76); see module docstring."""
    lines = [HEADER]
    for section in (_intro, _stack_section, _weight_section, _special_section, _dg_section, _port_section,
                    _stability_section, _ballast_section, _lashing_section, _moves_section):
        lines.extend(section(instance))
    return "\n".join(lines) + "\n"
