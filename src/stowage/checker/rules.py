"""Departure stowage constraints, excluding naval architecture (plan 2.4).

A physical stack is one odd-bay column; a forty-foot box loads two columns.
Mass is shared equally between its supports (ASSUMPTION from model.vessel.Stack);
height is counted in full in each column. Mixed 20/40-foot columns use their
20-foot column rating, with half the spanning box's mass on each support
(ASSUMPTION: convergence review section 5, item 7; confidence M, expert confirmation
pending). If no 20-foot rating is supplied, the general column rating applies.
Sources: catalogue.toml E1/E2/E6/E7, ISO 668 dimensions, and the vessel model.
With rule-freeze-v1 enabled, E1/E2 compare full gross kg in every physical
column (including each support of a spanning box), and E6 is a soft count.
The following D25 behavior applies only with the activation switch off.
D25 provisionally orders VGM-derived
classes L < M < H, with same-class boxes permitted in either mass order. Boundaries
come from instance overrides first, then assumption-labelled catalogue defaults
(pending expert Q2/Q4/Q11). Stored ``Container.weight_class`` is never trusted.
E1 retains its own heavy-above-nonheavy diagnostic. E6 allows deck H only when no
compatible hold footprint remains empty at departure; partial placements defer
this terminal condition to ``e6_completion_possible``.

Future overstows are permitted by the catalogue when their legal restows are counted;
that accounting requires a move plan and belongs to the move engine/full checker.
This state-only part validates POD and hatch references, not future move accounting.
Metrics: n_checked_boxes; activated soft metrics heavy_on_deck_count and
killed_slots; margins: minimum stack_mass_kg and stack_height_m.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import math

from stowage.conventions import stack_id
from stowage.checker.slots import cells_of_slot, parse_slot
from stowage.frozen import frozen
from stowage.model import Container, Instance, PartReport, Placement, ShipState, Violation
from stowage.rules.catalogue import catalogue_snapshot, clear_catalogue_snapshot_cache, load_catalogue, rule_freeze_v1_enabled


def _catalogue_data():
    return catalogue_snapshot().catalogue


def _catalogue():
    return catalogue_snapshot().rules


# Preserve the previous reset API for tests/tools; freshness never relies on it.
_catalogue_data.cache_clear = clear_catalogue_snapshot_cache
_catalogue.cache_clear = clear_catalogue_snapshot_cache


def _parameter(instance, rule, name):
    overrides = instance.rule_overrides.get(rule, {})
    if name in overrides:
        value = overrides[name]
        return value.get('value') if isinstance(value, dict) else value
    param = _catalogue()[rule].parameters.get(name)
    if param is None and rule == 'expert.e2.weight_order':
        # D25 stores VGM boundaries once, in the shared catalogue block. A
        # rule-scoped override uses that same parameter name for reproducibility.
        param = _catalogue_data().weight_classes.parameters.get(name)
    return param.value if param else None


def _geometry_index(instance):
    """Index reciprocal, ordered physical geometry without trusting validation.

    Mutable/model_construct inputs can bypass the vessel model's validators.
    An inconsistent stack cannot supply trustworthy floors or tier ordinals;
    exclude its cells from geometry-dependent helpers and retain hard evidence.
    Numeric tier gaps are allowed: ``Stack.cell_ids`` defines physical order.
    """
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    problems = {}
    members = {}

    def finite(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))

    for sid, stack in stacks.items():
        limits = (stack.max_mass_kg, stack.max_height_m, stack.base_z_m)
        optional = (stack.max_mass_20_kg, stack.max_mass_40_kg)
        if (not all(finite(v) for v in limits)
                or stack.max_mass_kg < 0 or stack.max_height_m <= 0
                or any(v is not None and (not finite(v) or v < 0) for v in optional)):
            problems[sid] = f'Stack {sid} has missing or invalid finite mass/height/base geometry.'
        ids = stack.cell_ids
        members[sid] = set(ids)
        if len(ids) != len(members[sid]) or ids != sorted(ids):
            problems[sid] = f'Stack {sid} requires unique, bottom-to-top cell ids.'
        for cid in ids:
            if cid not in cells or cells[cid].stack_id != sid:
                problems[sid] = f'Stack {sid} has absent or nonreciprocal cell data for {cid}.'
    for cid, cell in cells.items():
        sid = cell.stack_id
        stack = stacks.get(sid)
        if not all(finite(v) for v in (cell.lcg_m, cell.tcg_m, cell.vcg_base_m)):
            problems[sid] = f'Cell {cid} has missing or invalid finite coordinate geometry.'
        try:
            address = parse_slot(cid)
            canonical = stack_id(address.bay, address.row, address.on_deck)
        except (TypeError, ValueError):
            problems[sid] = f'Cell {cid} has an invalid physical-cell address.'
            continue
        if (stack is None or cid not in members[sid] or canonical != sid
                or (address.bay, address.row, address.on_deck)
                != (stack.bay, stack.row, stack.on_deck)):
            problems[sid] = f'Cell {cid} has missing or inconsistent reciprocal stack geometry.'
    usable = {cid for cid, cell in cells.items() if cell.stack_id not in problems}
    return cells, stacks, usable, problems


def _physical_geometry_index(instance):
    """Reciprocal geometry for the complete physical stow, including D42 cells.

    A split decision/fixed column can contain complementary cell lists. Merge
    those lists under its physical stack identity before checking order and
    OOG support space. Conflicting coordinates/stack facts remain unusable.
    This view never makes fixed cells available as decision positions.
    """
    vessel = instance.vessel
    cells, stacks, conflicts = {}, {}, {}
    for cell in [*vessel.cells, *vessel.fixed_load_cells]:
        if cell.id in cells and cells[cell.id] != cell:
            conflicts[cell.stack_id] = f'Conflicting decision/fixed physical cell {cell.id}.'
        cells.setdefault(cell.id, cell)
    for stack in [*vessel.stacks, *vessel.fixed_load_stacks]:
        if stack.id in stacks:
            previous = stacks[stack.id]
            if previous.model_dump(exclude={'cell_ids'}) != stack.model_dump(exclude={'cell_ids'}):
                conflicts[stack.id] = f'Conflicting decision/fixed physical stack {stack.id}.'
            stacks[stack.id] = previous.model_copy(update={
                'cell_ids': sorted(set(previous.cell_ids) | set(stack.cell_ids))})
        else:
            stacks[stack.id] = stack
    physical = instance.model_copy(update={'vessel': vessel.model_copy(update={
        'cells': list(cells.values()), 'stacks': list(stacks.values())})})
    cells, stacks, usable, problems = _geometry_index(physical)
    problems.update(conflicts)
    usable = {cid for cid in usable if cells[cid].stack_id not in problems}
    return cells, stacks, usable, problems


def _core(instance, ship, call_index, *, notes=None, background_ship=None):
    from stowage.checker.rules_background import d42_background_capacity_violations
    from stowage.checker.rules_evidence import background_capacity_violations, cargo_rating_violation

    violations = []
    boxes = {b.id: b for b in instance.containers}
    cells, stacks, usable_cells, geometry_problems = _geometry_index(instance)
    arrival = {(p.container_id, p.slot) for p in instance.onboard.placements}
    columns = defaultdict(list)
    occupied = {}
    margins = defaultdict(list)
    covers = {h.id for h in instance.vessel.hatch_covers}
    freeze_enabled = rule_freeze_v1_enabled()

    def valid_mass(box):
        return (isinstance(box.gross_mass_kg, (int, float))
                and math.isfinite(box.gross_mass_kg) and box.gross_mass_kg > 0
                and box.gross_mass_kg >= box.tare_kg)

    def emit(rule, message, placements=(), **kw):
        inherited = bool(placements) and instance.stage == 'B' and all(
            p.container_id.startswith('fixed:') or (p.container_id, p.slot) in arrival for p in placements)
        metadata = _catalogue().get(rule)
        severity = 'tolerated' if metadata and not metadata.is_hard(stage=instance.stage, inherited=inherited) else 'hard'
        violations.append(Violation(rule_id=rule, severity=severity, call_index=call_index,
            containers=[p.container_id for p in placements if not p.container_id.startswith('fixed:')],
            slots=[p.slot for p in placements if isinstance(p.slot, str) and len(p.slot) == 7 and p.slot.isdigit()],
            message=message, inherited=inherited, **kw))

    def numeric(rule, name, placements):
        value = _parameter(instance, rule, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            emit('checker.missing_vessel_data', f'{rule} requires a finite nonnegative {name} in instance rule overrides.', placements)
            return None
        return value

    def note(message):
        if notes is not None:
            notes.append(message)

    # Actual D42 boxes and attributable legacy aggregates are checked before
    # missing provenance or invalid aggregate evidence can stop the audit.
    evaluated_ship = ship if background_ship is None else background_ship
    actual_background_mass = {}
    checked_background = d42_background_capacity_violations(
        instance, evaluated_ship, emit, note=note,
        background_mass_by_stack=actual_background_mass) or set()
    planned_mass_by_stack = defaultdict(list)
    for placement in evaluated_ship.placements:
        box = boxes.get(placement.container_id)
        if box is None or not valid_mass(box):
            continue
        try:
            footprint = cells_of_slot(placement.slot)
            for cid in footprint:
                address = parse_slot(cid)
                sid = stack_id(address.bay, address.row, address.on_deck)
                planned_mass_by_stack[sid].append(box.gross_mass_kg / len(footprint))
        except (TypeError, ValueError):
            continue  # Existing core diagnostics reject malformed placements.
    planned_totals = {}
    for sid, masses in planned_mass_by_stack.items():
        try:
            planned_totals[sid] = math.fsum(masses)
        except OverflowError:
            emit('checker.missing_vessel_data', f'Planned stack {sid} has nonfinite total mass.')
    background_capacity_violations(instance.vessel, emit, note=note,
                                   checked_stacks=checked_background,
                                   planned_mass_by_stack=planned_totals,
                                   actual_mass_by_stack=actual_background_mass)
    if ship.vessel_id != instance.vessel.id:
        emit('checker.missing_vessel_data', 'Ship state refers to a different vessel.')
    for _, message in sorted(geometry_problems.items()):
        emit('checker.missing_vessel_data', message)
    for p in ship.placements:
        b = boxes.get(p.container_id)
        if b is None:
            emit('checker.missing_vessel_data', 'Placed container is absent from instance cargo.', [p])
            continue
        try:
            address = parse_slot(p.slot)
            footprint = cells_of_slot(p.slot)
        except (TypeError, ValueError):
            emit('checker.missing_vessel_data', 'Placement has an invalid ship slot address.', [p])
            continue
        if (b.length_ft == 20) != address.is_20ft_bay:
            emit('expert.physical.size_fit', 'Container length does not match slot footprint.', [p])
        if not valid_mass(b):
            emit('solas.vi2.verified_gross_mass', 'Container requires a positive verified gross mass including tare.', [p])
        cargo_rating_violation(b, p, emit, note=note)
        future = [c.index for c in instance.rotation.calls if c.index > call_index and c.locode == b.pod]
        if not future:
            emit('checker.missing_vessel_data', 'Container POD does not resolve to a future rotation call.', [p])
        for cid in footprint:
            if cid in occupied:
                emit('expert.physical.slot_occupancy', 'Two containers occupy the same physical cell.', [occupied[cid], p])
            occupied[cid] = p
            cell = cells.get(cid)
            if cell is None or cell.stack_id not in stacks:
                emit('checker.missing_vessel_data', f'Vessel has no cell or stack data for {cid}.', [p])
                continue
            if cid not in usable_cells:
                continue
            if not getattr(cell, f'accepts_{b.length_ft}'):
                emit('expert.physical.size_fit', 'Cell does not accept this container length.', [p])
            columns[cell.stack_id].append((cid, p, b, len(footprint)))
        if b.reefer and all(cid in cells for cid in footprint) and not any(cells[cid].reefer_plug for cid in footprint):
            emit('expert.physical.reefer_plug', 'Operating reefer has no plug in its footprint.', [p])
    for sid, entries in sorted(columns.items()):
        stack = stacks[sid]
        entries.sort(key=lambda e: e[0])
        ps = [e[1] for e in entries]
        mass = sum(b.gross_mass_kg / parts for _, _, b, parts in entries) if all(valid_mass(e[2]) for e in entries) else None
        heights = sum(b.height_m + (b.oog.over_height_m if b.oog else 0) for _, _, b, _ in entries)
        lengths = {b.length_ft for _, _, b, _ in entries}
        limit = stack.max_mass_kg
        # ASSUMPTION: a 40'/45' box above 20' boxes loads each supporting 20'
        # column against its 20' rating, not a minimum involving the 40' rating.
        # Convergence review section 5, item 7; confidence M. Expert must confirm.
        if 20 in lengths and stack.max_mass_20_kg is not None:
            limit = stack.max_mass_20_kg
        elif lengths <= {40, 45} and stack.max_mass_40_kg is not None:
            limit = stack.max_mass_40_kg
        if mass is not None:
            margins['stack_mass_kg'].append(limit - mass)
        margins['stack_height_m'].append(stack.max_height_m - heights)
        if mass is not None and mass > limit:
            emit('expert.physical.stack_weight', 'Stack exceeds its mass limit.', ps, measured=mass, limit=limit, unit='kg')
        if heights > stack.max_height_m:
            emit('expert.physical.tier_height', 'Stack exceeds its height limit.', ps, measured=heights, limit=stack.max_height_m, unit='m')
        actual = [e[0] for e in entries]
        if actual != stack.cell_ids[:len(actual)]:
            emit('expert.moves.bottom_up', 'Stack contains an unsupported tier or gap.', ps)
        if not stack.on_deck and stack.hatch_cover_id not in covers:
            emit('checker.missing_vessel_data', 'Hold stack has no hatch-cover mapping.', ps)
        if len(entries) > 1 and not freeze_enabled:
            threshold = numeric('expert.e1.heavy_bottom', 'heavy_threshold', ps)
            light_threshold = numeric('expert.e2.weight_order', 'medium_min_kg', ps)
            heavy_threshold = numeric('expert.e2.weight_order', 'heavy_min_kg', ps)
            tolerance = numeric('expert.e2.weight_order', 'tolerance', ps)
            order = _parameter(instance, 'expert.e2.weight_order', 'class_order')
            labels = _catalogue_data().weight_classes.parameters['labels'].value
            mode = _parameter(instance, 'expert.e2.weight_order', 'order_mode')
            class_limits = light_threshold is not None and heavy_threshold is not None and tolerance is not None
            if class_limits and (light_threshold <= 0 or light_threshold >= heavy_threshold):
                emit('checker.missing_vessel_data', 'E2 requires positive medium_min_kg < heavy_min_kg.', ps)
                class_limits = False
            if mode != 'weight_class' or not isinstance(order, list) or len(order) != len(labels) or set(map(str, order)) != set(labels):
                emit('checker.missing_vessel_data', 'E2 requires weight_class mode and a permutation of the catalogue class labels.', ps)
                class_limits = False
            if tolerance is not None and tolerance != int(tolerance):
                emit('checker.missing_vessel_data', 'E2 tolerance must be a nonnegative integer number of class ranks.', ps)
                class_limits = False
            # A running minimum class detects inversions in linear time.
            # VGM defines the class; same-class mass differences are unrestricted.
            # Retain a new-placement witness too, so inherited minima cannot hide
            # a new hard inversion beneath an unchanged arrival box.
            lightest = lightest_new = nonheavy = nonheavy_new = first_forty = None
            for _, upper, ub, _ in entries:
                if first_forty is not None and ub.length_ft == 20:
                    emit('expert.physical.no_20_on_40', 'Twenty-foot container cannot rest above a forty-foot container.', [first_forty, upper])
                if ub.length_ft != 20 and first_forty is None:
                    first_forty = upper
                if not valid_mass(ub):
                    continue
                is_new = (upper.container_id, upper.slot) not in arrival
                witness = nonheavy_new if nonheavy_new is not None else nonheavy
                if threshold is not None and witness is not None and ub.gross_mass_kg >= threshold:
                    emit('expert.e1.heavy_bottom', 'Heavy container is above a nonheavy container.', [witness, upper])
                if class_limits:
                    label = labels[int(ub.gross_mass_kg >= light_threshold) + int(ub.gross_mass_kg >= heavy_threshold)]
                    rank = order.index(label)
                    witness = lightest
                    if lightest_new is not None and rank > lightest_new[1] + tolerance:
                        witness = lightest_new
                    if witness is not None and rank > witness[1] + tolerance:
                        emit('expert.e2.weight_order', f'Upper container class {order[rank]} exceeds lower container class {order[witness[1]]} plus class-rank tolerance.', [witness[0], upper])
                    if lightest is None or rank < lightest[1]:
                        lightest = (upper, rank)
                    if is_new and (lightest_new is None or rank < lightest_new[1]):
                        lightest_new = (upper, rank)
                if threshold is not None and ub.gross_mass_kg < threshold:
                    nonheavy = upper
                    if is_new:
                        nonheavy_new = upper
    if freeze_enabled:
        from stowage.checker.rules_weight import fixed_weight_order_violations

        fixed_weight_order_violations(instance, evaluated_ship, emit, valid_mass=valid_mass)
        _later_call_refusals(instance, ship, call_index, emit)
    return violations, {k: min(v) for k, v in margins.items()}


def refused_later_handling_calls(rotation, call_index: int, pod: str,
                                primary_class: str | None, *, current_port=None) -> list[int]:
    """Shared C5/A3 predicate for the checker and feasibility mask.

    Returns refused later handling-call indices, empty with the switch off.
    Only the first future POD is handled on this recorded vessel leg, including
    a transshipment POD. Current-port handling and untouched transit are outside
    this predicate. D66 matches primary class only; D63 reclassification and
    unrecorded onward itineraries are not inferred. Unknown destination policy
    raises ValueError (or a resolver error), never a permissive False result.
    Mask callers must treat that missing evidence exactly as the checker does.
    """
    if not rule_freeze_v1_enabled() or primary_class is None:
        return []
    from stowage.ports import resolve_port_call_profile

    if not 0 <= call_index < len(rotation.calls):
        raise ValueError('call_index must resolve to a rotation call')
    destination = next((call for call in rotation.calls
                        if call.index > call_index and call.locode == pod), None)
    if destination is None:
        raise ValueError('POD must resolve to a future call')
    profile = resolve_port_call_profile(destination, current_port=current_port)
    if profile is None:
        raise ValueError(f'Cannot resolve destination port rules for {destination.locode}: unknown profile.')
    rule = next((ref for ref in profile.port_rules if ref.rule_id == 'port.dg.refused_class'), None)
    if rule is None:
        return []
    classes = rule.params.get('classes')
    if not isinstance(classes, list) or any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in classes):
        raise ValueError(f'port.dg.refused_class requires classes at destination {destination.locode}.')
    primary = primary_class.upper()
    identifiers = {primary, primary.rstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ'), primary.partition('.')[0]}
    return [destination.index] if identifiers.intersection(str(value).upper() for value in classes) else []


def _later_call_refusals(instance, ship, call_index, emit):
    """Adapt the shared later-handling predicate to checker evidence."""
    boxes = {box.id: box for box in instance.containers}
    destinations = {}
    for call in instance.rotation.calls:
        if call.index > call_index:
            destinations.setdefault(call.locode, call)
    results = {}
    for placement in ship.placements:
        box = boxes.get(placement.container_id)
        if box is None or box.dg is None or box.pod not in destinations:
            continue  # Core checks missing cargo and unresolved POD references.
        call = destinations[box.pod]
        key = (box.pod, box.dg.imdg_class)
        if key not in results:
            try:
                refused = refused_later_handling_calls(
                    instance.rotation, call_index, box.pod, box.dg.imdg_class,
                    current_port=instance.port)
                results[key] = (refused, None)
            except (ValueError, KeyError, OSError) as exc:
                results[key] = ([], str(exc))
        refused, error = results[key]
        if error is not None:
            emit('checker.missing_vessel_data',
                 f'Cannot resolve destination port rules for {call.locode}: {error}', [placement])
            continue
        if refused:
            emit('port.dg.refused_class',
                 f'Destination {call.locode} at later call {call.index} refuses primary DG class {box.dg.imdg_class}.', [placement])


@frozen('checker.check_static_rules', owner='2.4', implemented=True)
def check_static_rules(instance: Instance, ship: ShipState, *, call_index: int) -> PartReport:
    """Non-physics departure rules; move accounting remains delegated.

    Cost includes all D*(D-1)/2 DG pairs, even beyond segregation range, and
    O(O*N) cargo-envelope comparisons for O OOG boxes among N placements.
    E6 may cost O(E*(V+N+C*P)): E deck-heavy boxes, V vessel records, C hold
    candidates, P one incremental placement check. Geometry indexing,
    rotation/heat-region scans and deterministic stack/violation sorting add
    their own costs. Slices additionally recover original geometry and validate
    and sort fixed background loads against their cell/stack audit records.
    The <50 ms full-feeder target is a benchmark objective,
    not a worst-case latency guarantee; DG/E6-heavy inputs need measurement.

    Non-violation evidence notes use metrics keys ``note: <message>`` and numeric
    occurrence counts. This preserves notes in full-report JSON through the
    existing PartReport schema without misclassifying an assumption as a breach.
    """
    from stowage.checker.rules_imdg import imdg_violations
    from stowage.checker.rules_special import killed_slots, special_violations

    from stowage.checker.rules_weight import e6_departure_violations, weight_soft_metrics

    notes = []
    violations, margins = _core(instance, ship, call_index, notes=notes)
    violations.extend(e6_departure_violations(instance, ship, call_index=call_index))
    # Helpers require existing cargo/geometry. Retain core diagnostics for malformed stows.
    valid_ids = {b.id for b in instance.containers}
    _, _, valid_cells, _ = _geometry_index(instance)
    usable = _usable_ship(ship, valid_ids, valid_cells)
    violations.extend(special_violations(instance, usable, call_index=call_index))
    violations.extend(imdg_violations(instance, usable, call_index=call_index))
    oog_metrics = {'killed_slots': float(len(killed_slots(instance, usable)))} if rule_freeze_v1_enabled() else {}
    # One 40-foot pair can produce the same diagnostic from both supports.
    unique = {v.model_dump_json(): v for v in violations}
    ordered = sorted(unique.values(), key=lambda v: (v.rule_id, v.slots, v.containers, v.message))
    return PartReport(name='rules', violations=ordered,
        metrics={'n_checked_boxes': float(len(ship.placements)),
                 **weight_soft_metrics(instance, ship),
                 **oog_metrics,
                 **{f'note: {message}': float(count) for message, count in sorted(Counter(notes).items())}},
        margins=margins)


def _usable_ship(ship, valid_ids, valid_cells):
    placements = []
    for p in ship.placements:
        try:
            footprint = cells_of_slot(p.slot)
        except (TypeError, ValueError):
            continue
        if p.container_id in valid_ids and all(c in valid_cells for c in footprint):
            placements.append(p)
    return ship.model_copy(update={'placements': placements})


def placement_violations(instance: Instance, ship: ShipState, box: Container, slot: str, *, call_index: int | None = None) -> list[Violation]:
    """Check a candidate against a partial stow, without mutation.

    Checks candidate stacks and candidate/DG pairs, including existing OOG and
    top-stowage cargo affected by the new box. O(V + N log N + A*N + A*H)
    time and O(V+N) space; V vessel records, A boxes in affected stacks, H heat
    sources. For bounded stack depths this is linear apart from deterministic
    sorting. Unlike full checking it does not compare unrelated DG pairs.
    E6 is a departure/completion condition: an H deck candidate is not rejected
    merely because a compatible hold position is empty now. The engine must use
    ``e6_completion_possible`` and check the completed departure condition.
    Assumes the partial stow is already checked; existing violations in affected
    stacks may be returned. Candidate relocations replace their old placement.
    """
    from stowage.checker.rules_imdg import imdg_placement_violations
    from stowage.checker.rules_special import special_violations

    index = instance.call_index if call_index is None else call_index
    placements = [p for p in ship.placements if p.container_id != box.id]
    placements.append(Placement.model_construct(slot=slot, container_id=box.id))
    candidate = ship.model_copy(update={'placements': placements})
    try:
        footprint = cells_of_slot(slot)
    except (TypeError, ValueError):
        return _core(instance, candidate.model_copy(update={'placements': placements[-1:]}), index,
                     background_ship=candidate)[0]
    cells = {c.id: c for c in instance.vessel.cells}
    affected = {cells[c].stack_id for c in footprint if c in cells}
    local = []
    for p in placements:
        try:
            ids = cells_of_slot(p.slot)
        except (TypeError, ValueError):
            continue
        if p.container_id == box.id or any(c in cells and cells[c].stack_id in affected for c in ids):
            local.append(p)
    violations, _ = _core(instance, candidate.model_copy(update={'placements': local}), index,
                          background_ship=candidate)
    _, _, usable_cells, _ = _geometry_index(instance)
    usable = _usable_ship(candidate, {b.id for b in instance.containers}, usable_cells)
    violations.extend(special_violations(instance, usable, call_index=index, focus_container_id=box.id))
    if box.id in {p.container_id for p in usable.placements}:
        # Use instance cargo facts, exactly as the complete checker does.
        canonical = next(b for b in instance.containers if b.id == box.id)
        violations.extend(imdg_placement_violations(instance, usable, canonical, slot, call_index=index))
    unique = {v.model_dump_json(): v for v in violations}
    return sorted(unique.values(), key=lambda v: (v.rule_id, v.slots, v.containers, v.message))


# Public completion bound for feasibility masks; imports no checker entry points.
from stowage.checker.rules_weight import e6_completion_possible  # noqa: E402,F401
