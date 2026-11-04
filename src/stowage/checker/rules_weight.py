"""Weight policy, with C1/C2 behind the shared rule-freeze activation switch.

The activated contract orders gross kg in physical columns and treats heavy
on-deck cargo solely as a soft metric. The following D25 fallback describes the
unchanged legacy regime only.

A heavy box may occupy the deck only when every compatible hold opportunity is
occupied. A forty-foot opportunity uses two physical cells, so occupying either
support removes that opportunity. Compatibility includes applicable hard static placement rules: footprint, stack
limits, support, weight order, reefer power, booking, OOG and DG restrictions.
E6 itself is deferred by the placement checker, so this query cannot recurse.
Source: leader decision D25, catalogue ``expert.e6.heavy_under_deck``. This is a
labelled policy assumption pending expert confirmation, not a stability surrogate.

A partial stow can fill its hold later. The completion helper therefore gives a
necessary condition only. New cargo can fill a hold cell or change whether an
empty cell remains compatible (for example by exhausting stack mass capacity).
True never certifies that a completion is feasible.
"""
from __future__ import annotations

import math
from collections import defaultdict

from stowage.conventions import format_slot
from stowage.checker.slots import cells_of_slot, parse_slot
from stowage.model import Container, Instance, ShipState, Violation
from stowage.rules.catalogue import rule_freeze_v1_enabled

E6 = 'expert.e6.heavy_under_deck'
MISSING = 'checker.missing_vessel_data'


def gross_weight_order_violations(instance, entries, emit, *, valid_mass, inherited_refs=None):
    """Emit C1's E1/E2 checks for an ordered physical column in O(N).

    Compare gross box mass, never mass allocated to a support. A spanning long
    box occurs in both physical columns, so it is compared with each support.
    The minimum lower gross mass proves the all-lower-boxes predicate; retaining
    a separate new-placement minimum prevents a tolerated inherited witness
    from hiding a hard breach involving a newly placed lower box.

    Call only in the activated regime. The single tolerance is resolved through
    E2 for both equivalent rule ids, including instance overrides.
    """
    from stowage.checker.rules import _parameter

    rule = 'expert.e2.weight_order'
    tolerance = _parameter(instance, rule, 'weight_order_tolerance_kg')
    placements = [entry[1] for entry in entries]
    e1_overrides = instance.rule_overrides.get('expert.e1.heavy_bottom', {})
    e2_overrides = instance.rule_overrides.get(rule, {})
    key = 'weight_order_tolerance_kg'
    if key in e1_overrides:
        e1_tolerance = _parameter(instance, 'expert.e1.heavy_bottom', key)
        if (key not in e2_overrides or isinstance(e1_tolerance, bool)
                or not isinstance(e1_tolerance, (int, float))
                or not math.isfinite(e1_tolerance) or e1_tolerance < 0
                or e1_tolerance != tolerance):
            emit(MISSING, 'E2 is authoritative for weight_order_tolerance_kg; '
                 'an E1 tolerance override requires a matching explicit E2 override.', placements)
            return
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance) or tolerance < 0):
        emit(MISSING, 'E1/E2 require a finite nonnegative weight_order_tolerance_kg.', placements)
        return
    if _parameter(instance, rule, 'weight_order_mode') != 'gross_mass_kg':
        emit(MISSING, 'E1/E2 require gross_mass_kg weight_order_mode.', placements)
        return
    if inherited_refs is None:
        from stowage.checker.rules_background import fixed_cargo_records

        inherited_refs = {(p.container_id, p.slot) for p in instance.onboard.placements}
        inherited_refs.update((p.container_id, p.slot) for p, _, _ in fixed_cargo_records(instance))
    arrival = inherited_refs
    lightest = lightest_new = None
    for _, upper, box, _ in entries:
        if not valid_mass(box):
            continue
        witness = lightest
        if lightest_new is not None and box.gross_mass_kg > lightest_new[1] + tolerance:
            witness = lightest_new
        if witness is not None and box.gross_mass_kg > witness[1] + tolerance:
            for rid in ('expert.e1.heavy_bottom', rule):
                emit(rid, 'Upper container gross mass exceeds lower container gross mass plus tolerance.',
                     [witness[0], upper], measured=box.gross_mass_kg,
                     limit=witness[1] + tolerance, unit='kg')
        if lightest is None or box.gross_mass_kg < lightest[1]:
            lightest = (upper, box.gross_mass_kg)
        if ((upper.container_id, upper.slot) not in arrival
                and (lightest_new is None or box.gross_mass_kg < lightest_new[1])):
            lightest_new = (upper, box.gross_mass_kg)


def fixed_weight_order_violations(instance, ship, emit, *, valid_mass):
    """Check C1 across decision and fixed boxes in every physical column.

    Fixed boxes remain real boxes for gross-order comparisons, including both
    supports of a long box. They are inherited cargo at stage B. They are never
    converted into move decisions or added again to physical aggregate masses.
    """
    from stowage.checker.rules import _physical_geometry_index
    from stowage.checker.rules_background import fixed_cargo_records

    if not rule_freeze_v1_enabled():
        return
    cells, _, usable_cells, _ = _physical_geometry_index(instance)
    fixed = fixed_cargo_records(instance)
    boxes = {box.id: box for box in instance.containers}
    records = [(p, boxes.get(p.container_id)) for p in ship.placements]
    records.extend((p, box) for p, box, _ in fixed)
    arrival = {(p.container_id, p.slot) for p in instance.onboard.placements}
    arrival.update((p.container_id, p.slot) for p, _, _ in fixed)
    columns = defaultdict(list)
    for placement, box in records:
        if box is None:
            continue
        try:
            footprint = cells_of_slot(placement.slot)
        except (TypeError, ValueError):
            continue
        if not all(cid in usable_cells for cid in footprint):
            continue
        for cid in footprint:
            columns[cells[cid].stack_id].append((cid, placement, box, len(footprint)))
    # Validate policy even for an empty stow; invalid explicit overrides must
    # never silently disappear merely because no column has two boxes yet.
    if not columns:
        gross_weight_order_violations(instance, [], emit, valid_mass=valid_mass,
                                      inherited_refs=arrival)
    for sid in sorted(columns):
        entries = sorted(columns[sid], key=lambda entry: entry[0])
        first_long = None
        for _, upper, box, _ in entries:
            if first_long is not None and box.length_ft == 20:
                emit('expert.physical.no_20_on_40',
                     'Twenty-foot container cannot rest above a forty-foot container.',
                     [first_long, upper])
            if box.length_ft != 20 and first_long is None:
                first_long = upper
        gross_weight_order_violations(instance, entries, emit, valid_mass=valid_mass,
                                      inherited_refs=arrival)


def weight_soft_metrics(instance: Instance, ship: ShipState) -> dict[str, float]:
    """C2 level-four preference; absent entirely in the legacy regime."""
    if not rule_freeze_v1_enabled():
        return {}
    from stowage.checker.rules import _parameter

    threshold = _parameter(instance, E6, 'heavy_on_deck_threshold_kg')
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold) or threshold < 0):
        # An invalid soft-policy parameter is not a hard stowage rejection.
        # Do not invent a threshold or claim a measured zero count.
        return {'note: heavy_on_deck_count unavailable: invalid heavy_on_deck_threshold_kg.': 1.0}
    boxes = {box.id: box for box in instance.containers}
    heavy = set()
    from stowage.checker.rules_background import fixed_cargo_records

    records = [(p, boxes.get(p.container_id)) for p in ship.placements]
    records.extend((p, box) for p, box, _ in fixed_cargo_records(instance))
    for placement, box in records:
        try:
            on_deck = parse_slot(placement.slot).on_deck
        except (TypeError, ValueError):
            continue
        if (box is not None and on_deck and math.isfinite(box.gross_mass_kg)
                and box.gross_mass_kg >= threshold):
            heavy.add(box.id)
    return {'heavy_on_deck_count': float(len(heavy))}


def _threshold(instance):
    # Import lazily: rules.py calls this module after defining its shared lookup.
    from stowage.checker.rules import _parameter
    value = _parameter(instance, E6, 'heavy_threshold')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return value


def _occupied(ship):
    occupied = set()
    for placement in ship.placements:
        try:
            occupied.update(cells_of_slot(placement.slot))
        except (ValueError, TypeError):
            pass  # The full core reports malformed placements independently.
    return occupied


def _compatible_slots(instance, box):
    """Fixed hold-space capability, ignoring occupancy; O(V + candidate DG checks)."""
    if 'on_deck' in box.special_stowage:
        return []
    from stowage.checker.rules import _geometry_index

    cells, stacks, usable_cells, _ = _geometry_index(instance)
    candidates = set()
    for cell in cells.values():
        if cell.id not in usable_cells:
            continue
        stack = stacks.get(cell.stack_id)
        if stack is None or stack.on_deck:
            continue
        address = parse_slot(cell.id)
        if box.length_ft == 20:
            candidates.add(cell.id)
        else:
            for bay in (address.bay - 1, address.bay + 1):
                if 0 < bay < 999:
                    candidates.add(format_slot(bay, address.row, address.tier))
    if box.dg is not None:
        from stowage.checker.rules_imdg import _doc_areas, _stowage
        areas = _doc_areas(instance)
    result = []
    for slot in sorted(candidates):
        footprint = cells_of_slot(slot)
        if any(cid not in usable_cells
               or stacks[cells[cid].stack_id].on_deck
               or not getattr(cells[cid], f'accepts_{box.length_ft}') for cid in footprint):
            continue
        if box.reefer and not any(cells[cid].reefer_plug for cid in footprint):
            continue
        if box.dg is not None and _stowage(instance, box, slot, instance.call_index, cells, stacks, areas):
            continue
        result.append(slot)
    return result


def _iter_compatible_empty_hold_slots(instance, ship, box, *, call_index=None):
    """Yield legal hold opportunities in deterministic slot order.

    Departure E6 needs one witness, while the public list query needs them all.
    Keep the hard placement gate identical for both queries.
    """
    from stowage.checker.rules import placement_violations
    occupied = _occupied(ship)
    index = instance.call_index if call_index is None else call_index
    for slot in _compatible_slots(instance, box):
        if (not occupied.intersection(cells_of_slot(slot))
                and not any(v.severity == 'hard' for v in
                            placement_violations(instance, ship, box, slot, call_index=index))):
            yield slot


def compatible_empty_hold_slots(instance: Instance, ship: ShipState, box: Container, *, call_index: int | None = None) -> list[str]:
    """Return empty hold slots permitting this box under hard static rules.

    Cost O(V+N+C*P), where P is one incremental static-placement check and C the
    fixed-capability candidates. Forty/forty-five-foot opportunities need both
    supports empty. The box is relocated hypothetically, without changing state.
    Physics, lashing and visibility remain other checker parts' responsibility.
    """
    return list(_iter_compatible_empty_hold_slots(instance, ship, box, call_index=call_index))


def e6_departure_violations(instance: Instance, ship: ShipState, *, call_index: int) -> list[Violation]:
    """Check completed departure fallback; O(H*(V + N + C*P)), H deck boxes.

    C2 returns no violations when activated. Legacy uses the catalogue heavy
    threshold unless overridden by the instance. No
    threshold is invented when catalogue data is missing. Per D25, new deck H
    boxes are legal only after all their compatible hold opportunities are blocked.
    P is one incremental placement check; stop at the first compatible witness.
    """
    if rule_freeze_v1_enabled():
        return []
    boxes = {box.id: box for box in instance.containers}
    threshold = _threshold(instance)
    from stowage.checker.rules import _parameter
    fallback = _parameter(instance, E6, 'allow_on_deck_if_no_compatible_hold_cell')
    out = []
    for placement in ship.placements:
        box = boxes.get(placement.container_id)
        try:
            on_deck = parse_slot(placement.slot).on_deck
        except (ValueError, TypeError):
            continue
        if box is None or not on_deck:
            continue
        if threshold is None:
            out.append(Violation(rule_id=MISSING, severity='hard', call_index=call_index,
                containers=[box.id], slots=[placement.slot],
                message=f'{E6} requires a finite nonnegative catalogue/override heavy_threshold.'))
        elif not isinstance(fallback, bool):
            out.append(Violation(rule_id=MISSING, severity='hard', call_index=call_index,
                containers=[box.id], slots=[placement.slot],
                message=f'{E6} requires boolean allow_on_deck_if_no_compatible_hold_cell.'))
        elif math.isfinite(box.gross_mass_kg) and box.gross_mass_kg >= threshold:
            empty = next(_iter_compatible_empty_hold_slots(instance, ship, box, call_index=call_index), None) if fallback else None
            if not fallback or empty is not None:
                out.append(Violation(rule_id=E6, severity='hard', call_index=call_index,
                    containers=[box.id], slots=[placement.slot],
                    message=(f'Deck class-H cargo has a compatible empty hold opportunity at departure: {empty}.'
                             if fallback else 'Deck class-H cargo is prohibited by the E6 fallback override.'),
                    measured=box.gross_mass_kg, limit=threshold, unit='kg'))
    return out


def e6_completion_possible(instance: Instance, ship: ShipState, remaining_boxes,
                           *, call_index: int | None = None) -> bool:
    """Necessary condition for completion without relocating current placements.

    C2 always returns True when activated. In legacy, with unplaced cargo,
    return True optimistically: adding cargo can fill a cell
    or make another empty cell incompatible through stack mass, support or DG
    constraints. An empty-footprint counting bound would therefore be unsound.
    With no remaining boxes, test the departure condition exactly. A disabled
    fallback proves completion impossible whenever a fixed deck H remains.
    Missing policy data returns True; the full checker reports hard diagnostics.

    Cost O(N+R) when unplaced cargo remains; otherwise O(H*(V+N+C*P)) for H deck
    boxes and C candidate slots, with P one incremental placement check. Memory
    O(N+R+V). No recursive call to the full departure checker.
    """
    if rule_freeze_v1_enabled():
        return True
    from stowage.checker.rules import _parameter
    threshold = _threshold(instance)
    if threshold is None:
        return True
    boxes = {box.id: box for box in instance.containers}
    placed = {placement.container_id for placement in ship.placements}
    deck_heavy = False
    for placement in ship.placements:
        box = boxes.get(placement.container_id)
        try:
            on_deck = parse_slot(placement.slot).on_deck
        except (ValueError, TypeError):
            continue
        if box is not None and on_deck and math.isfinite(box.gross_mass_kg) and box.gross_mass_kg >= threshold:
            deck_heavy = True
            break
    if not deck_heavy:
        return True
    fallback = _parameter(instance, E6, 'allow_on_deck_if_no_compatible_hold_cell')
    if fallback is False:
        return False
    if fallback is not True:
        return True
    if any(box.id not in placed for box in remaining_boxes):
        return True
    index = instance.call_index if call_index is None else call_index
    return not any(v.rule_id == E6 for v in e6_departure_violations(instance, ship, call_index=index))
