"""Dangerous goods clearance using the source-traced IMDG 42-24 tables.

Segregation keeps incompatible cargo apart; table 7.2.4 selects the requirement,
then 7.4.3 supplies geometry alternatives (OR), each containing conditions (AND).
Source: IMO MSC.556(108), 7.2.3, 7.2.4, 7.4.3 and sampled DGL column 16.

Shipment facts are read field by field from ``Container.dg`` (schema 1.2).
Non-None facts take precedence, including False and empty verification lists.
Ordinary declaration fields fall back to ``imdg.stowage.3_2`` overrides under
``containers[box.id]``. Legacy ``dry``, ``protected_from_heat`` and
``clear_living_quarters`` flags are honoured only for schema < 1.9 saved inputs.
New assessment keys retain explicit nulls as unknown. H1/SW19 and vessel-map
records must resolve in ``assessment_scenarios`` to the actual shipment/entry,
vessel, voyage leg, call and placement. Unknown never grants permission. CTU
classification uses ``ctu_closed`` and then the legacy ``imdg.segregation.7_4_3``
``slots[slot].closed`` fact. ISO type alone grants no CTU clearance.
For mixed loads, entry-specific SW/SG facts use each additional DG entry. Packing
form and CTU classification describe the shared physical box; additional entries
may inherit these two facts from the governing entry but cannot contradict it.
Facts come from the declared shipment/vessel scenario author, not the planner.
H1 operational precautions are emitted separately by
``imdg_handling_instructions``; they never assert future operational outcomes.

The segregation table is selected by declared ``Vessel.imdg_ship_type``
(closed_holds/hatchless), with the saved ``imdg.segregation.7_4_3.ship_type``
declaration retained when the native field is unknown. Conflicts fail closed.
Remaining segregation geometry uses explicit rule overrides:
``imdg.segregation.7_4_3`` accepts
``slots`` keyed by BAPLIE slot (``hold_id`` string), and ``pairs`` keyed by sorted
slots joined with ``|``. Pair facts are ``bulkheads``,
``barriers_resistant_to_fire_and_liquids``, ``segregated_by_deck``, and
``distance_from_intervening_bulkhead_m``. A hold_id also identifies the hold below
a deck slot when required by the table. Missing necessary facts fail closed.

SOLAS II-2/19-style space authorization uses ``Stack.dg_doc_area_id`` and
``Vessel.dg_doc_areas``. Every support of a 40/45-foot box must be documented
and allow all primary/subsidiary classes and the shipment packing form. Empty
permission lists grant nothing. Legacy stack class lists may tighten a documented
area but can never replace one. Packing form is an explicit shipment fact because
ISO container type alone cannot distinguish packaged cargo from bulk cargo.
Closed-freight-container claims must also agree with the CTU classification.
Authorization uses existing ``imdg.stowage.3_2``; absent area data additionally
carries the frozen contract's hard ``checker.missing_vessel_data`` diagnostic.

Legacy full checking is O(v + n + d²) time, O(v + n + d) auxiliary memory for v vessel
cells/stacks, n placements and d
DG boxes (times the number of declared hazards). Evidence-backed placement
adds per-DG support/geometry and registry resolution work; geometry is not
memoised across slots or assumed support heights. ``imdg_placement_violations``
checks only pairs involving the candidate in O(v + n + d), suitable for a partial
stow. Persistent vessel/box map caching can remove the O(v + n) setup cost.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

from stowage.checker.slots import cells_of_slot, parse_slot
from stowage.model import Instance, ShipState
from stowage.model.cargo import Container, DangerousGoods
from stowage.model.violation import Violation
from stowage.rules.imdg.dgl import lookup_dgl, stowage_handling_provision
from stowage.rules.imdg.labels import hazard_division
from stowage.rules.imdg.placement_assessments import (
    assess_sw1, assess_sw2, covered_under_deck, geometry_from_vessel, sunlight_from_vessel,
)
from stowage.rules.imdg.scenarios import context_for
from stowage.rules.imdg.segregation_support import (
    CLASS_SEPARATION, SEGREGATION_SUPPORT, classify_segregation_code,
)
from stowage.rules.imdg.shipment_assessments import assess_h1, assess_sw19, operational_precautions
from stowage.rules.imdg.ship_type import resolve_ship_type
from stowage.rules.imdg.tables import containership_segregation, general_segregation, segregation_codes

GENERAL = 'imdg.segregation.7_2_4'
SHIP = 'imdg.segregation.7_4_3'
STOWAGE = 'imdg.stowage.3_2'
MISSING = 'checker.missing_vessel_data'


def _v(rule: str, call: int, message: str, boxes=(), slots=()) -> Violation:
    return Violation(rule_id=rule, severity='hard', call_index=call,
                     containers=list(boxes), slots=list(slots), message=message)


_SHIPMENT_FIELDS = (
    'packing_form', 'ctu_closed', 'protected_from_heat', 'clear_living_quarters',
    'ventilation_compliant', 'shaded', 'air_circulation', 'dry',
    'handling_compliant', 'subsidiary_hazards_verified', 'special_provisions',
    'short_international_voyage', 'bulk_container_type', 'verified_provisions',
    'verified_segregation_codes', 'shipment_provenance',
    'h1', 'sw19', 'sunlight',
)
_SHARED_CTU_FIELDS = ('packing_form', 'ctu_closed')
_ASSESSMENT_FIELDS = ('h1', 'sw19', 'sunlight')
_LEGACY_PLACEMENT_FIELDS = ('dry', 'protected_from_heat', 'clear_living_quarters')


def _legacy_parity(instance):
    """Only pre-1.9 saved inputs retain placement-independent clearances."""
    try:
        version = tuple(int(part) for part in instance.schema_version.split('.'))
        return len(version) >= 2 and version < (1, 9)
    except (AttributeError, TypeError, ValueError):
        return False


def _entry_path(root, target, path='primary'):
    if root is target:
        return path
    for index, child in enumerate(root.additional):
        found = _entry_path(child, target, f'{path}.additional.{index}')
        if found is not None:
            return found
    return None


def _facts(instance: Instance, box: Container, dg: DangerousGoods | None = None, *, entry_path=None) -> dict[str, Any]:
    """Resolve non-None entry facts before legacy overrides; False/[] remain known.

    Unknown fields are omitted so older provision checks cannot mistake a model's
    default None key for evidence or iterate None as a verification list.
    """
    entry = box.dg if dg is None else dg
    legacy = instance.rule_overrides.get(STOWAGE, {}).get('containers', {}).get(box.id, {})
    result = {key: value for key, value in legacy.items()
              if value is not None or key in _ASSESSMENT_FIELDS}
    if not _legacy_parity(instance):
        for key in _LEGACY_PLACEMENT_FIELDS:
            result.pop(key, None)
    # New evidence is entry-specific, even when two consignments share a UN.
    # Legacy fields retain their historical resolution for saved-input parity.
    if entry is not box.dg:
        for key in _ASSESSMENT_FIELDS:
            result.pop(key, None)
    entries = legacy.get('entry_assessments')
    path = _entry_path(box.dg, entry) if entry_path is None else entry_path
    scoped = entries.get(path, {}) if isinstance(entries, dict) else {}
    if isinstance(scoped, dict):
        result.update({key: scoped[key] for key in _ASSESSMENT_FIELDS if key in scoped})
    for field in _SHIPMENT_FIELDS:
        if field in _LEGACY_PLACEMENT_FIELDS and not _legacy_parity(instance):
            continue
        value = getattr(entry, field, None)
        if value is None and entry is not box.dg and field in _SHARED_CTU_FIELDS:
            value = getattr(box.dg, field, None)
        if value is not None:
            result[field] = value
        elif field in _ASSESSMENT_FIELDS and field in getattr(entry, 'model_fields_set', ()):
            result[field] = None
    return result


def _closed(instance: Instance, box: Container, slot: str) -> bool | None:
    facts = _facts(instance, box)
    if 'ctu_closed' in facts:
        fact = facts['ctu_closed']
        return fact if isinstance(fact, bool) else None
    fact = instance.rule_overrides.get(SHIP, {}).get('slots', {}).get(slot, {}).get('closed')
    return fact if isinstance(fact, bool) else None


def _hazards(dg: DangerousGoods):
    yield dg
    for additional in dg.additional:
        yield from _hazards(additional)


def _hazard_entries(dg, path='primary'):
    """Use traversal scope, including when in-memory entries share an object."""
    yield path, dg
    for index, child in enumerate(dg.additional):
        yield from _hazard_entries(child, f'{path}.additional.{index}')


def _doc_areas(instance):
    return {area.id: (area, set(area.stack_ids)) for area in instance.vessel.dg_doc_areas}


def _sunlight_declaration(facts):
    declaration = facts.get('sunlight')
    if not isinstance(declaration, dict):
        return None
    # Packing/CTU declarations are physical shared facts. A contrary sunlight
    # label cannot turn CTU cargo into an uncontained-package exemption.
    packing = facts.get('packing_form')
    known_ctu = isinstance(facts.get('ctu_closed'), bool) or isinstance(packing, str) and packing in {
        'packaged_goods_in_closed_freight_containers', 'open_freight_container', 'tank_container'}
    if known_ctu and declaration.get('package_inside_ctu') is False:
        return None
    return declaration


def _doc_permissions(instance, box, slot, call, cells, stacks, areas):
    """SOLAS II-2/19-style authorization; O(hazards + supporting cells)."""
    out = []
    packing = _facts(instance, box).get('packing_form')
    if not isinstance(packing, str) or not packing:
        out.append(_v(MISSING, call, 'DG packing_form shipment fact is missing.', [box.id], [slot]))
        packing = None
    elif packing == 'packaged_goods_in_closed_freight_containers':
        closed = _closed(instance, box, slot)
        if closed is None:
            out.append(_v(MISSING, call, 'DG closed freight container packing form requires a known CTU closed/open classification.', [box.id], [slot]))
        elif closed is False:
            out.append(_v(STOWAGE, call, 'DG packing form claims a closed freight container without matching CTU classification.', [box.id], [slot]))
    shared_values = {}
    for dg in _hazards(box.dg):
        entry_facts = _facts(instance, box, dg)
        for field in _SHARED_CTU_FIELDS:
            value = entry_facts.get(field)
            if value is None:
                continue
            if field in shared_values and shared_values[field] != value:
                out.append(_v(STOWAGE, call, f'Mixed DG entries disagree on shared {field} shipment fact.', [box.id], [slot]))
            else:
                shared_values[field] = value
    hazards = {risk for dg in _hazards(box.dg) for risk in [dg.imdg_class, *dg.subsidiary_risks]}
    for cid in cells_of_slot(slot):
        stack = stacks.get(cells[cid].stack_id) if cid in cells else None
        if stack is None:
            out.append(_v(MISSING, call, 'DG slot has no vessel cell/stack data.', [box.id], [slot]))
            continue
        documented = areas.get(stack.dg_doc_area_id)
        if documented is None or stack.id not in documented[1]:
            out.append(_v(MISSING, call, f'Stack {stack.id} has no matching Document of Compliance area documentation.', [box.id], [slot]))
            out.append(_v(STOWAGE, call, f'DG stowage is not permitted: stack {stack.id} has no Document of Compliance area.', [box.id], [slot]))
            continue
        area, _ = documented
        if not hazards <= set(area.classes_allowed):
            out.append(_v(STOWAGE, call, f'DG class or subsidiary risk is not permitted by Document of Compliance area {area.id}.', [box.id], [slot]))
        if packing is not None and packing not in area.packing_forms_allowed:
            out.append(_v(STOWAGE, call, f'DG packing form {packing} is not permitted by Document of Compliance area {area.id}.', [box.id], [slot]))
        if stack.dg_classes_allowed is not None and not hazards <= set(stack.dg_classes_allowed):
            out.append(_v(STOWAGE, call, 'DG class or subsidiary risk is not permitted by the stack Document of Compliance restriction.', [box.id], [slot]))
    return out


def _stowage(instance: Instance, box: Container, slot: str, call: int, cells, stacks, areas, ship=None):
    """Actual stowage assessment, or the existing static necessary screen.

    The no-ship caller in rules_weight asks which slots could be compatible.
    Upper-tier geometry is undecidable before support heights are known, so that
    caller defers unknown geometry to its subsequent actual placement check.
    Both public IMDG verdict functions always supply the actual ship state.
    """
    out = _doc_permissions(instance, box, slot, call, cells, stacks, areas)
    config = instance.rule_overrides.get(STOWAGE, {})
    geometry_facts = getattr(instance.vessel, 'imdg_placement_geometry', None)
    geometry_present = geometry_facts is not None or 'placement_geometry' in config
    if geometry_facts is None:
        geometry_facts = config.get('placement_geometry')
    envelope = (_placement_envelope(instance, ShipState(vessel_id=instance.vessel.id) if ship is None else ship,
                                    box, slot, cells, stacks)
                if geometry_present else None)
    for entry_path, dg in _hazard_entries(box.dg):
        facts = _facts(instance, box, dg, entry_path=entry_path)
        context = context_for(instance, box, entry_path, slot, call)
        registry = config.get('assessment_scenarios')
        try:
            entry = lookup_dgl(int(dg.un_number), dg.packing_group)
        except (KeyError, ValueError):
            out.append(_v(STOWAGE, call, f'UN {dg.un_number} has no unambiguous sampled DGL entry.', [box.id], [slot]))
            continue
        codes = set(entry.stowage_codes) | set(dg.stowage_codes) | set(dg.handling_codes)
        if (hazard_division(dg.imdg_class) != entry.hazard_class or dg.stowage_category != entry.stowage_category
                or not set(entry.subsidiary_risks) <= set(dg.subsidiary_risks)):
            out.append(_v(STOWAGE, call, 'Declared DG class, category or subsidiary risks disagree with the DGL.', [box.id], [slot]))
        if entry.subsidiary_hazard_note and not facts.get('subsidiary_hazards_verified', False):
            out.append(_v(MISSING, call, 'DGL conditional subsidiary hazards need shipment verification.', [box.id], [slot]))
        conditional_sg = {'SG27', 'SG31', 'SG34', 'SG50', 'SG53', 'SG72'}
        for code in sorted((set(entry.segregation_codes) | set(dg.segregation_codes)) & conditional_sg):
            if code not in facts.get('verified_segregation_codes', []):
                out.append(_v(GENERAL, call, f'{code} requires external conditional segregation verification.', [box.id], [slot]))
        category = entry.stowage_category
        if 'SW16' in codes:
            if _closed(instance, box, slot) is None:
                out.append(_v(MISSING, call, 'SW16 requires the CTU closed/open classification.', [box.id], [slot]))
            elif _closed(instance, box, slot) is False:
                category = 'B'
        if 'SW19' in codes:
            if 'sw19' in facts:
                assessment = assess_sw19(facts, un_number=dg.un_number, context=context, registry=registry)
                if assessment.compliant is True:
                    category = assessment.category
                else:
                    out.append(_v(MISSING if assessment.compliant is None else STOWAGE, call,
                                  'SW19 battery assessment ' + ('requires evidence.' if assessment.compliant is None else 'is not satisfied.'),
                                  [box.id], [slot]))
            elif not _legacy_parity(instance) or 'special_provisions' not in facts:
                out.append(_v(MISSING, call, 'SW19 requires shipment special-provision facts.', [box.id], [slot]))
            elif set(map(str, facts['special_provisions'])) & {'376', '377'}:
                if 'short_international_voyage' not in facts:
                    out.append(_v(MISSING, call, 'SW19 requires the voyage classification.', [box.id], [slot]))
                elif not facts['short_international_voyage']:
                    category = 'C'
        if category in {'C', 'D'} and not parse_slot(slot).on_deck:
            out.append(_v(STOWAGE, call, f'IMDG cargo-ship category {category} requires on-deck stowage.', [box.id], [slot]))
        elif category not in {'A', 'B', 'C', 'D', 'E'}:
            out.append(_v(STOWAGE, call, 'Class-1 stowage requires verified explosives arrangements not represented by the model.', [box.id], [slot]))
        conditions = {'SW1': ('protected_from_heat',), 'SW2': ('clear_living_quarters',),
                      'SW6': () if parse_slot(slot).on_deck else ('ventilation_compliant',),
                      'SW11': ('shaded', 'air_circulation'), 'H1': ('dry',), 'H5': ('handling_compliant',)}
        for code in sorted(codes):
            try:
                stowage_handling_provision(code)
            except KeyError:
                out.append(_v(STOWAGE, call, f'Unknown DGL provision {code} cannot be cleared.', [box.id], [slot]))
                continue
            if code in {'SW16', 'SW19'}:
                continue
            if code in {'SW1', 'SW2'} and (geometry_present or code == 'SW1' and 'sunlight' in facts):
                geometry = geometry_from_vessel(instance.vessel, geometry_facts,
                                                context=context, registry=registry, provision=code)
                if envelope is None:
                    result = None
                elif code == 'SW2':
                    result = assess_sw2(envelope, geometry, context=context, registry=registry)
                else:
                    sunlight = sunlight_from_vessel(
                        instance.vessel, slot, _sunlight_declaration(facts),
                        covers_off=() if ship is None else ship.covers_off,
                        context=context, registry=registry)
                    result = assess_sw1(envelope, geometry, on_deck=parse_slot(slot).on_deck,
                                        sunlight=sunlight, context=context, registry=registry)
                if result is None and ship is None:
                    continue  # Necessary static screen only; actual verdict stays fail-closed.
                if result is not True:
                    out.append(_v(MISSING if result is None else STOWAGE, call,
                                  f'{code} placement assessment ' + ('requires evidence.' if result is None else 'is not satisfied.'),
                                  [box.id], [slot]))
                continue
            if code == 'H1' and 'h1' in facts:
                result = assess_h1(facts, on_deck=parse_slot(slot).on_deck, slot=slot,
                                   covered_under_deck=covered_under_deck(
                                       instance.vessel, slot, () if ship is None else ship.covers_off),
                                   context=context, registry=registry)
                if result is not True:
                    out.append(_v(MISSING if result is None else STOWAGE, call,
                                  'H1 dryness assessment ' + ('requires evidence.' if result is None else 'is not satisfied.'),
                                  [box.id], [slot]))
                continue
            needed = conditions.get(code)
            if needed is None:
                if code == 'SW23' and facts.get('bulk_container_type') not in {None, 'BK3'}:
                    continue
                if code not in facts.get('verified_provisions', []):
                    out.append(_v(MISSING, call, f'{code} needs externally verified shipment conditions.', [box.id], [slot]))
                continue
            for field in needed:
                if field not in facts:
                    out.append(_v(MISSING, call, f'{code} requires {field} data.', [box.id], [slot]))
                elif facts[field] is not True:
                    out.append(_v(STOWAGE, call, f'{code} condition {field} is not met.', [box.id], [slot]))
    return out


def imdg_handling_instructions(instance: Instance) -> list[dict[str, Any]]:
    """Emit H1 terminal/crew precautions separately from plan verdicts.

    Instructions apply even when shipment declarations are unknown or adverse.
    Their presence never records an inspection or an accomplished operation.
    Consumers can carry this sidecar alongside the checked plan; it is not a
    Violation and cannot clear a hard stowage condition.
    """
    out = []
    for box in sorted(instance.containers, key=lambda box: box.id):
        if box.dg is None:
            continue
        for entry_path, dg in _hazard_entries(box.dg):
            try:
                entry_codes = lookup_dgl(int(dg.un_number), dg.packing_group).stowage_codes
            except (KeyError, ValueError):
                entry_codes = ()
            if 'H1' not in {*entry_codes, *dg.stowage_codes, *dg.handling_codes}:
                continue
            out.append({'container_id': box.id, 'entry_path': entry_path,
                        'code': 'H1', 'source': 'IMO MSC.556(108), 7.1.6 H1; 7.3.3.3',
                        'instructions': list(operational_precautions({'h1': _facts(instance, box, dg).get('h1')}))})
    return out


def _envelope(box, slot, cells):
    occupied = [cells[cid] for cid in cells_of_slot(slot)]
    x = sum(c.lcg_m for c in occupied) / len(occupied)
    y = sum(c.tcg_m for c in occupied) / len(occupied)
    # ISO external lengths (20 feet is nominal); IMDG space minima are 6 m/2.4 m.
    return x, y, {20: 6.058, 40: 12.192, 45: 13.716}[box.length_ft] / 2, 2.438 / 2


def _placement_envelope(instance, ship, box, slot, cells, stacks):
    """Actual box bounds, including OOG and every actual supporting box height.

    An empty static-cell probe cannot establish an upper-tier floor. Return
    unknown there; the mask owner must use an optimistic geometry screen rather
    than memoising an assumed standard-height floor as final clearance.
    """
    from stowage.rules.imdg.placement_assessments import BoxEnvelope

    boxes = {b.id: b for b in instance.containers}
    occupied = {cid: boxes.get(p.container_id) for p in ship.placements
                if p.container_id != box.id for cid in cells_of_slot(p.slot)}
    try:
        ids = cells_of_slot(slot)
        floors = []
        for cid in ids:
            stack = stacks[cells[cid].stack_id]
            index = stack.cell_ids.index(cid)
            below = [occupied.get(lower) for lower in stack.cell_ids[:index]]
            if any(b is None for b in below):
                return None
            floors.append(stack.base_z_m + sum(
                b.height_m + (b.oog.over_height_m if b.oog else 0) for b in below))
        if max(floors) - min(floors) > 1e-6:
            return None
        x, y, length, width = _envelope(box, slot, cells)
        oog = box.oog
        return BoxEnvelope(
            x-length-(oog.over_length_aft_m if oog else 0),
            x+length+(oog.over_length_fore_m if oog else 0),
            y-width-(oog.over_width_starboard_m if oog else 0),
            y+width+(oog.over_width_port_m if oog else 0),
            floors[0], floors[0]+box.height_m+(oog.over_height_m if oog else 0))
    except (KeyError, ValueError, TypeError):
        return None


def _geometry(instance, a, sa, b, sb, level, cells):
    """Return True/False, or None when a necessary geometry fact is unavailable."""
    config = instance.rule_overrides.get(SHIP, {})
    ship_type = resolve_ship_type(instance.vessel, config)
    if ship_type is None:
        return None
    ca, cb = _closed(instance, a, sa), _closed(instance, b, sb)
    if ca is None or cb is None:
        return None
    pair = 'closed_closed' if ca and cb else 'open_open' if not ca and not cb else 'closed_open'
    try:
        xa, ya, la, wa = _envelope(a, sa, cells)
        xb, yb, lb, wb = _envelope(b, sb, cells)
    except KeyError:
        return None
    dx, dy = abs(xa - xb) - la - lb, abs(ya - yb) - wa - wb
    vertical = dx < 0 and dy < 0
    ad, bd = parse_slot(sa).on_deck, parse_slot(sb).on_deck
    slotfacts = config.get('slots', {})
    af, bf = slotfacts.get(sa, {}), slotfacts.get(sb, {})
    pf = config.get('pairs', {}).get('|'.join(sorted((sa, sb))), {})
    orientations = [('vertical', None, 0)] if vertical else []
    if not vertical:
        deck = 'on' if ad and bd else 'under'
        if dx >= 0:
            orientations.append(('fore_aft', deck, max(dx, 0)))
        if dy >= 0:
            orientations.append(('athwartships', deck, max(dy, 0)))
    unknown = False
    for orientation, deck, distance in orientations:
        cell = containership_segregation(level, ship_type=ship_type, orientation=orientation,
                                         container_pair=pair, deck=deck)
        for alt in cell.alternatives:
            if alt.prohibited:
                continue
            checks = []
            if alt.min_container_spaces:
                checks.append(distance + 1e-9 >= alt.min_container_spaces * (6 if orientation == 'fore_aft' else 2.4))
            if alt.min_horizontal_m:
                checks.append(distance + 1e-9 >= alt.min_horizontal_m)
            if alt.not_same_vertical_line:
                checks.append(not vertical)
            if alt.segregated_by_deck:
                checks.append(False if ad == bd else pf.get('segregated_by_deck'))
            if alt.open_above_closed:
                checks.append((not ca and parse_slot(sa).tier > parse_slot(sb).tier) or
                              (not cb and parse_slot(sb).tier > parse_slot(sa).tier))
            if alt.barriers_resistant_to_fire_and_liquids:
                checks.append(pf.get('barriers_resistant_to_fire_and_liquids'))
            if alt.min_bulkheads:
                same_hold = af.get('hold_id') is not None and af.get('hold_id') == bf.get('hold_id')
                checks.append(False if same_hold else None if 'bulkheads' not in pf else pf['bulkheads'] >= alt.min_bulkheads)
            if alt.not_in_or_above_same_hold:
                checks.append(None if 'hold_id' not in af or 'hold_id' not in bf else af['hold_id'] != bf['hold_id'])
            if alt.min_distance_from_intervening_bulkhead_m:
                checks.append(None if 'distance_from_intervening_bulkhead_m' not in pf else
                              pf['distance_from_intervening_bulkhead_m'] >= alt.min_distance_from_intervening_bulkhead_m)
            if any(c is False for c in checks):
                continue
            if any(c is None for c in checks):
                unknown = True
            else:
                return True
    return None if unknown else False


def _pair(instance, a, sa, b, sb, call, cells):
    out, level = [], 0
    for da, db in ((x, y) for x in _hazards(a.dg) for y in _hazards(b.dg)):
        # No automatic LQ exemption: packaging conditions are absent from the model.
        for left in [da.imdg_class, *da.subsidiary_risks]:
            for right in [db.imdg_class, *db.subsidiary_risks]:
                try:
                    value = general_segregation(left, right).value
                except KeyError:
                    value = '*'
                if value == '*':
                    out.append(_v(GENERAL, call, 'Explosives compatibility or unknown hazard class cannot be cleared by the general table.', [a.id, b.id], [sa, sb]))
                elif value != 'X':
                    level = max(level, int(value))
        for dg, other, box in ((da, db, a), (db, da, b)):
            try:
                entry_codes = lookup_dgl(int(dg.un_number), dg.packing_group).segregation_codes
            except (KeyError, ValueError):
                entry_codes = ()
            codes = set(dg.segregation_codes) | set(entry_codes)
            try:
                othercodes = set(other.segregation_codes) | set(lookup_dgl(int(other.un_number), other.packing_group).segregation_codes)
            except (KeyError, ValueError):
                othercodes = set(other.segregation_codes)
            groups = {'SG35': 'SGG1', 'SG36': 'SGG18', 'SG38': 'SGG2', 'SG49': 'SGG6', 'SG60': 'SGG16'}
            for code in sorted(codes):
                support = classify_segregation_code(code)
                if code.startswith('SGG'):
                    if code not in SEGREGATION_SUPPORT:
                        out.append(_v(GENERAL, call, f'{code} is an unclassified segregation-group marker; {support.reason}', [a.id, b.id], [sa, sb]))
                    continue
                if code in groups:
                    if groups[code] in othercodes:
                        level = max(level, 2)
                elif code in {'SG5', 'SG6'}:
                    surrogate = '3' if code == 'SG5' else '5.1'
                    try:
                        val = general_segregation(surrogate, other.imdg_class).value
                        if val.isdigit():
                            level = max(level, int(val))
                        elif val == '*':
                            raise KeyError(code)
                    except KeyError:
                        out.append(_v(GENERAL, call, f'{code} compatibility is unresolved.', [a.id, b.id], [sa, sb]))
                elif code == 'SG14':
                    if other.imdg_class.startswith('1') and other.imdg_class != '1.4S':
                        level = max(level, 2)
                elif code in CLASS_SEPARATION:
                    if CLASS_SEPARATION[code].intersection((other.imdg_class, *other.subsidiary_risks)):
                        level = max(level, 2)
                elif code == 'SG46':
                    if other.un_number == '1017':
                        level = max(level, 2)
                elif code == 'SG26':
                    if other.imdg_class in {'2.1', '3'} and parse_slot(sa).on_deck and parse_slot(sb).on_deck:
                        try:
                            _, ya, _, wa = _envelope(a, sa, cells)
                            _, yb, _, wb = _envelope(b, sb, cells)
                            if abs(ya-yb)-wa-wb < 4.8:
                                out.append(_v(SHIP, call, 'SG26 requires two container spaces athwartships.', [a.id, b.id], [sa, sb]))
                        except KeyError:
                            out.append(_v(MISSING, call, 'SG26 needs vessel cell geometry.', [a.id, b.id], [sa, sb]))
                elif code not in _facts(instance, box, dg).get('verified_segregation_codes', []):
                    meaning = segregation_codes().get(code)
                    out.append(_v(GENERAL, call, f'{code} requires external conditional segregation verification' + (f': {meaning.meaning}' if meaning else '.'), [a.id, b.id], [sa, sb]))
    if level:
        result = _geometry(instance, a, sa, b, sb, level, cells)
        if result is not True:
            out.append(_v(MISSING if result is None else SHIP, call,
                          f'IMDG segregation level {level} ' + ('requires missing ship/compartment data.' if result is None else 'is not satisfied.'), [a.id, b.id], [sa, sb]))
    return out


def imdg_violations(instance: Instance, ship: ShipState, *, call_index: int) -> list[Violation]:
    """Check all DG boxes and pairs; O(v + n + d²), deterministic placement order."""
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    areas = _doc_areas(instance)
    dg = [(boxes[p.container_id], p.slot) for p in sorted(ship.placements, key=lambda p: p.slot)
          if p.container_id in boxes and boxes[p.container_id].dg is not None]
    out = [v for box, slot in dg for v in _stowage(instance, box, slot, call_index, cells, stacks, areas, ship)]
    for (a, sa), (b, sb) in combinations(dg, 2):
        out.extend(_pair(instance, a, sa, b, sb, call_index, cells))
    return out


def imdg_placement_violations(instance: Instance, ship: ShipState, box: Container, slot: str, *, call_index: int | None = None) -> list[Violation]:
    """Check just the candidate DG box and its DG pairs; O(v + n + d), no mutation."""
    if box.dg is None:
        return []
    call = instance.call_index if call_index is None else call_index
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    boxes = {b.id: b for b in instance.containers}
    out = _stowage(instance, box, slot, call, cells, stacks, _doc_areas(instance), ship)
    for placement in sorted(ship.placements, key=lambda p: p.slot):
        other = boxes.get(placement.container_id)
        if other is not None and other.id != box.id and other.dg is not None:
            out.extend(_pair(instance, box, slot, other, placement.slot, call, cells))
    return out
