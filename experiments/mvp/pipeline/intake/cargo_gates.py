"""Deterministic v7 cargo screens after source authentication.

Input facts are declarations already authenticated by the intake evidence layer.
Missing shipping facts remain questions; derived classification never supplies a
shipment permission. Tare/temperature/empty screens are labelled approximate
intake policies, not equipment certification or placement checks.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from pydantic import ValidationError
from stowage.model.cargo import Container, DangerousGoods
from stowage.model.instance import Instance
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1_enabled
from stowage.rules.imdg.dgl import entries_for_un
from .validation import _check_capability

# Broad plausibility screens, not plate ratings; tank/OT equipment is referred.
TARE_BANDS_KG = {20: (1500, 4000), 40: (2500, 6500), 45: (3500, 7500)}
# Explicit synthetic representatives, not inferred individual equipment masses.
REPRESENTATIVE_TARE_KG = {'22G1': 2200, '42G1': 3800, '45G1': 3900,
                          '22R1': 3000, '42R1': 4300, '45R1': 4500,
                          'L5G1': 4800, 'L5R1': 5500}
EMPTY_TOLERANCE_KG = 150
_REQUIRED = ('id', 'iso_type', 'pol', 'pod', 'gross_mass_kg', 'empty', 'reefer', 'dg')
_EQUIPMENT_FIELDS = ('iso_type', 'length_ft', 'height_m', 'reefer', 'tank', 'equipment_reefer')
_REJECTED_EVIDENCE = {'unsupported_paper', 'unsupported_source', 'unknown_contextual_assessment',
                      'rejected_model', 'unverified_transcription', 'wrong_booking_scope'}
_GROUNDED_KINDS = {'sheet', 'parser_text', 'paper', 'image', 'planner', 'closed_world'}
_GROUNDED_EVIDENCE = {'verified_cell', 'corroborated_sources', 'authoritative_declaration',
                      'deterministic_label', 'planner_confirmation', 'closed_world_declaration'}


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _equipment_declarations(fact, sources, rid, index):
    """Keep grounded but unresolved declarations; discard rejected model claims."""
    declared={field:[] for field in _EQUIPMENT_FIELDS}
    for field in declared:
        if field in fact and fact[field] is not None:
            declared[field].append((fact[field], 'resolved row'))
    for source in sources:
        source_rid=source.get('row_id')
        if source_rid != rid and not (source_rid is None and source.get('row') == index):
            continue
        field=source.get('field')
        if field not in declared or 'value' not in source or source['value'] is None:
            continue
        if source.get('uncertain') or source.get('superseded_by_planner') or source.get('evidence') in _REJECTED_EVIDENCE:
            continue
        if source.get('derived') and source.get('evidence') != 'closed_world_declaration':
            continue
        if source.get('source_kind') not in _GROUNDED_KINDS and source.get('evidence') not in _GROUNDED_EVIDENCE:
            continue
        declared[field].append((source['value'], str(source.get('document', 'source'))))
    return declared


def _audit_equipment(fact, sources, rid, index, issue):
    """Compare every explicit physical assertion even if another field stopped."""
    declared=_equipment_declarations(fact, sources, rid, index)
    isos=[]
    for value,origin in declared['iso_type']:
        if isinstance(value,str) and len(value)==4 and value.isascii() and value.isalnum():
            isos.append((value.upper(),origin))
    if len({iso for iso,_ in isos})>1:
        issue(rid,'iso_type','cross_document_conflict',f'{rid}: ISO declarations disagree.',
              [f'{origin}: {value}' for value,origin in isos])
    for iso,iso_origin in isos:
        expectations={'length_ft':{'2':20,'4':40,'L':45}.get(iso[0]),
                      'height_m':{'2':2.591,'5':2.896}.get(iso[1]),
                      'tank':iso[2]=='T', 'equipment_reefer':iso[2]=='R',
                      'reefer':iso[2]=='R'}
        for field,expected in expectations.items():
            if expected is None:
                continue
            for actual,origin in declared[field]:
                if field in ('tank','equipment_reefer','reefer'):
                    valid=type(actual) is bool
                    # A non-operating reefer is explicitly permitted by Container.
                    agrees=valid and (actual==expected or field=='reefer' and actual is False)
                else:
                    valid=_number(actual)
                    agrees=valid and (actual==expected if field=='length_ft' else math.isclose(actual,expected,rel_tol=0.,abs_tol=.001))
                if agrees:
                    continue
                candidates=[f'{iso_origin}: ISO {iso} implies {field}={expected}', f'{origin}: {field}={actual}']
                prompt=f'{rid}: ISO {iso} disagrees with explicit {field}={actual}; resolve both declarations.'
                issue(rid,'iso_type','iso_declaration_conflict',prompt,candidates)
                issue(rid,field,'iso_declaration_conflict',prompt,candidates)


def validate_cargo_rows(facts, base_instance, *, field_sources=None):
    """Return JSON-able partial rows, independent gate questions and derivations.

    A question blocks that row from completion. This function never reads
    base-instance cargo attributes as extraction hints; only membership, yard
    geometry, vessel capability, rotation and explicitly declared policy.
    """
    base = base_instance if isinstance(base_instance, Instance) else Instance.model_validate(base_instance)
    entries = [dict(value, id=value.get('id', key)) for key, value in facts.items()] if isinstance(facts, dict) else deepcopy(facts)
    policy = base.rule_overrides.get('intake.policy', {})
    rows, row_ids, questions, findings = [], [], [], []
    sources = deepcopy(field_sources or [])
    seen = set()
    yard = {cid: stack for stack in base.yard.stacks for cid in stack.boxes}
    yard_ids = set(yard) | set(base.yard.quay_buffer)

    for index, fact in enumerate(entries):
        rid = str(fact.get('id', f'row-{index + 1}'))
        row_ids.append(rid)
        box = {key: deepcopy(value) for key, value in fact.items() if key in Container.model_fields}
        rows.append(box)

        def issue(row_id, field, reason, prompt, candidates=None):
            item = {'row_id': row_id, 'row': index, 'field': field, 'reason': reason,
                    'prompt': prompt, 'candidates': list(candidates or [])}
            if not any(q['row_id'] == row_id and q['field'] == field and q['reason'] == reason for q in questions):
                questions.append(item)
                findings.append({'row_id': row_id, 'row': index, 'field': field, 'code': reason,
                                 'severity': 'question', 'message': prompt})

        def derived(field, value, inputs, rule):
            box[field] = value
            sources.append({'row_id': rid, 'row': index, 'field': field, 'document': 'software:derivation',
                            'quote': rule, 'value': value, 'inputs': inputs, 'accepted': True,
                            'derived': True})

        for field in _REQUIRED:
            if field not in fact or (fact[field] is None and field != 'dg'):
                issue(rid, field, 'missing_vgm' if field == 'gross_mass_kg' else 'missing_declaration',
                      f'{rid}: supply the {field} declaration.')
        if rid in seen:
            issue(rid, 'id', 'duplicate_id', f'{rid}: repeated physical identity; confirm or shut out the duplicate.')
        seen.add(rid)
        if 'id' in fact and fact['id'] not in base.load_list:
            issue(rid, 'id', 'not_booked', f'{rid}: box is not booked in this call\'s load list.')
        if 'id' in fact and fact['id'] not in yard_ids:
            issue(rid, 'id', 'unknown_yard_id', f'{rid}: box is absent from this yard snapshot.')
        if fact.get('pol') is not None and fact['pol'] != base.port.locode:
            issue(rid, 'pol', 'wrong_pol', f'{rid}: POL {fact["pol"]} differs from current port {base.port.locode}.', [str(fact['pol']), base.port.locode])
        if policy.get('this_port') and policy['this_port'] != base.port.locode:
            issue(rid, 'pol', 'policy_port_mismatch', f'{rid}: intake policy and instance current port disagree.')
        if fact.get('pod') is not None and fact['pod'] not in {c.locode for c in base.rotation.calls[base.call_index + 1:]}:
            issue(rid, 'pod', 'pod_not_on_rotation', f'{rid}: POD {fact["pod"]} is not a later rotation call.')
        vessel = policy.get('vessel', base.vessel.id)
        if fact.get('booked_vessel') != vessel:
            issue(rid, 'booked_vessel', 'wrong_vessel', f'{rid}: booking vessel {fact.get("booked_vessel")} does not confirm {vessel}.', [str(fact.get('booked_vessel')), vessel])
        status = str(fact.get('booking_status', '')).strip().lower()
        if status not in ('confirmed', 'booked', 'accepted'):
            issue(rid, 'booking_status', 'booking_not_confirmed', f'{rid}: booking status {status or "unknown"} requires confirmation or shut_out.')
        if 'booked_voyage' in fact and policy.get('voyage') and fact['booked_voyage'] != policy['voyage']:
            issue(rid, 'booked_voyage', 'wrong_voyage', f'{rid}: booked voyage differs from this call.')

        _audit_equipment(fact, sources, rid, index, issue)
        iso = fact.get('iso_type')
        if isinstance(iso, str) and len(iso) == 4:
            length = {'2': 20, '4': 40, 'L': 45}.get(iso[0])
            height = {'2': 2.591, '5': 2.896}.get(iso[1])
            if length:
                derived('length_ft', length, ['iso_type'], 'ISO 6346 nominal length: 2=20 ft, 4=40 ft, L=45 ft.')
                if rid in yard and yard[rid].length_ft != length:
                    issue(rid, 'iso_type', 'yard_length_mismatch', f'{rid}: ISO length differs from the yard stack geometry.')
            else:
                issue(rid, 'iso_type', 'unsupported_iso', f'{rid}: unsupported ISO length code.')
            if height:
                derived('height_m', height, ['iso_type'], 'ISO 6346 supported height: 2=2.591 m, 5=2.896 m.')
            else:
                issue(rid, 'iso_type', 'unsupported_iso', f'{rid}: unsupported ISO height code.')
            if iso[2] not in ('G', 'R'):
                issue(rid, 'iso_type', 'unsupported_equipment', f'{rid}: {iso} needs equipment review; this intake supports general purpose and reefer groups.')
            if fact.get('reefer') is True and iso[2] != 'R':
                issue(rid, 'reefer', 'reefer_iso_mismatch', f'{rid}: operating reefer declaration conflicts with ISO {iso}.')
        elif iso is not None:
            issue(rid, 'iso_type', 'invalid_iso', f'{rid}: ISO size/type must have four characters.')

        if 'tare_kg' not in fact:
            representative = REPRESENTATIVE_TARE_KG.get(iso) if isinstance(iso, str) else None
            if representative is None:
                issue(rid, 'tare_kg', 'missing_tare_policy', f'{rid}: unstated tare has no supported ISO representative; supply the equipment tare.')
            else:
                band = TARE_BANDS_KG[box['length_ft']]
                derived('tare_kg', representative, ['iso_type', 'length_ft'],
                        f'Synthetic approximate tare representative for {iso}: {representative} kg; size screening band {band[0]}–{band[1]} kg. Not an individual equipment measurement.')
                sources[-1].update(approximate=True, band_kg=list(band),
                                   evidence='derived_iso_tare',
                                   policy_source='cargo_gates.REPRESENTATIVE_TARE_KG',
                                   band_source='cargo_gates.TARE_BANDS_KG')
        elif fact['tare_kg'] is None:
            issue(rid, 'tare_kg', 'missing_declaration', f'{rid}: an explicitly unknown tare needs equipment confirmation; no representative replaces it.')

        for name in ('empty', 'reefer'):
            if name in fact and type(fact[name]) is not bool:
                issue(rid, name, 'invalid_boolean', f'{rid}: {name} requires an explicit boolean declaration.')
        for name in ('gross_mass_kg', 'tare_kg', 'max_gross_mass_kg'):
            value = fact.get(name)
            if value is not None and (not _number(value) or value <= 0):
                issue(rid, name, 'invalid_mass', f'{rid}: {name} must be finite positive kg.')
                box.pop(name, None)
        gross, tare, rating = (box.get(k) for k in ('gross_mass_kg', 'tare_kg', 'max_gross_mass_kg'))
        length = box.get('length_ft')
        if _number(gross):
            derived('weight_class', load_catalogue().weight_classes.classify(gross), ['gross_mass_kg'], 'Shared rule catalogue weight_classes.classify(VGM).')
            if _number(tare) and gross < tare:
                issue(rid, 'gross_mass_kg', 'gross_below_tare', f'{rid}: gross {gross} kg is below tare {tare} kg.')
            if _number(rating) and gross > rating:
                issue(rid, 'gross_mass_kg', 'container_mass_limit', f'{rid}: gross {gross} kg exceeds supplied plate rating {rating} kg.')
            ceilings = policy.get('max_gross_mass_kg_by_length', {})
            ceiling = ceilings.get(str(length)) if isinstance(ceilings, dict) else None
            if _number(ceiling) and ceiling > 0:
                if gross > ceiling:
                    issue(rid, 'gross_mass_kg', 'intake_mass_limit', f'{rid}: gross {gross} kg exceeds declared intake ceiling {ceiling} kg.')
            elif not _number(rating):
                issue(rid, 'gross_mass_kg', 'missing_mass_limit', f'{rid}: no plate rating or declared intake mass ceiling is available.')
        if _number(tare) and length in TARE_BANDS_KG:
            low, high = TARE_BANDS_KG[length]
            if not low <= tare <= high:
                issue(rid, 'tare_kg', 'implausible_tare', f'{rid}: tare {tare} kg lies outside approximate {length}-ft screening band {low}–{high} kg.')
        if _number(gross) and _number(tare) and fact.get('empty') is True and abs(gross - tare) > EMPTY_TOLERANCE_KG:
            issue(rid, 'empty', 'empty_mass_mismatch', f'{rid}: empty gross {gross} kg differs from tare {tare} kg by more than approximate {EMPTY_TOLERANCE_KG}-kg tolerance.')
        if _number(gross) and _number(tare) and fact.get('empty') is False and gross <= tare + EMPTY_TOLERANCE_KG:
            issue(rid, 'empty', 'loaded_mass_mismatch', f'{rid}: declared loaded gross {gross} kg is within the approximate {EMPTY_TOLERANCE_KG}-kg empty tolerance of tare {tare} kg.')

        point = fact.get('reefer_setpoint_c')
        unit = str(fact.get('reefer_setpoint_unit', fact.get('temperature_unit', 'C'))).strip().upper()
        if unit in ('F', '°F', 'FAHRENHEIT') or fact.get('fahrenheit') is True:
            issue(rid, 'reefer_setpoint_c', 'fahrenheit_setpoint', f'{rid}: Fahrenheit source requires a planner-confirmed Celsius conversion.')
        if fact.get('reefer') is True:
            bounds = policy.get('reefer_setpoint_c', {'min': -35, 'max': 30})
            low, high = bounds.get('min', -35), bounds.get('max', 30)
            if point is None:
                issue(rid, 'reefer_setpoint_c', 'missing_reefer_setpoint', f'{rid}: operating reefer has no declared set point.')
            elif not _number(point) or not low <= point <= high:
                issue(rid, 'reefer_setpoint_c', 'reefer_setpoint_range', f'{rid}: set point {point} °C is outside declared intake range [{low}, {high}] °C.')
        elif point is not None:
            issue(rid, 'reefer_setpoint_c', 'nonreefer_setpoint', f'{rid}: a set point requires operating reefer=true.')
        reefer_blocked = type(fact.get('reefer')) is not bool or any(
            q['row_id'] == rid and q['field'] in ('reefer', 'iso_type') for q in questions)
        if 'reefer_setpoint_c' in fact and reefer_blocked:
            issue(rid, 'reefer_setpoint_c', 'reefer_dependency', f'{rid}: set point remains unresolved until the reefer declaration and ISO equipment group agree.')
        for source in sources:
            if source.get('row_id') == rid and source.get('field') == 'reefer_setpoint_c':
                source['inputs'] = sorted(set(source.get('inputs', [])) | {'reefer', 'iso_type'})

        if isinstance(fact.get('dg'), dict):
            box['dg'] = _derive_dg(fact['dg'], rid, 'dg', issue, sources, index)
        elif 'dg' in fact and fact['dg'] is not None:
            issue(rid, 'dg', 'invalid_dg', f'{rid}: DG must be an explicit declaration or explicit non-DG null.')
        if fact.get('special_stowage'):
            issue(rid, 'special_stowage', 'reader_referral', f'{rid}: send special-stowage wording to the authenticated instructions reader; a planner must confirm its reading.')
            box['special_stowage'] = []
        else:
            box['special_stowage'] = []
        # Direct delivery follows the known current-port policy, not the model.
        dg = box.get('dg')
        primary = dg.get('imdg_class') if isinstance(dg, dict) else None
        derived('direct_delivery', bool(base.port.requires_direct_delivery(primary)) if rule_freeze_v1_enabled() else False,
                ['dg.imdg_class', 'base_instance.port'], 'Current-port direct-delivery policy; no shipment permission is inferred.')
        if box['direct_delivery'] and rid in yard_ids:
            issue(rid, 'direct_delivery', 'direct_delivery_in_yard', f'{rid}: port requires direct delivery but the box has a yard position.')
        category = None
        if all(k in box for k in ('pod', 'iso_type', 'gross_mass_kg', 'empty', 'dg', 'reefer')) and _number(gross) and not (dg or fact.get('reefer') or fact.get('special_stowage') or box.get('oog')):
            masskey = f'mass_kg={float(gross).hex()}' if rule_freeze_v1_enabled() else box['weight_class']
            category = f'{box["pod"]}:{box["iso_type"]}:{masskey}:{"empty" if box["empty"] else "loaded"}'
        derived('category', category, ['pod', 'iso_type', 'gross_mass_kg', 'empty', 'dg', 'reefer', 'special_stowage', 'oog'], 'Generator interchangeability category; DG, reefer, OOG and special requests have category=null.')
        try:
            _check_capability(box, rid, base, issue)
        except (TypeError, ValueError, KeyError):
            issue(rid, None, 'invalid_partial_row', f'{rid}: invalid declarations prevent vessel capability screening.')
        # Model validation adds schema findings after every independent screen.
        try:
            normalized = Container.model_validate(box).model_dump(mode='json')
            # Defaults are serialization conveniences, not evidence. A partial
            # row must not grow null setpoints or false DG shipment flags.
            if not any(q['row_id'] == rid for q in questions):
                rows[index] = normalized
        except ValidationError as exc:
            for error in exc.errors():
                field = '.'.join(map(str, error['loc'])) or None
                issue(rid, field, 'container_validation', f'{rid}: {error["msg"]}')
    def overlaps(left, right):
        return left is None or right is None or left == right or left.startswith(right + '.') or right.startswith(left + '.')

    # A computed label is not accepted evidence when one of its inputs stopped.
    for source in sources:
        rid = source.get('row_id')
        if rid is None and isinstance(source.get('row'), int) and 0 <= source['row'] < len(row_ids):
            rid = row_ids[source['row']]
        dependencies = [source.get('field'), *source.get('inputs', [])]
        if any(q['row_id'] == rid and any(overlaps(q['field'], field) for field in dependencies) for q in questions):
            source['accepted'] = False
        if source.get('derived'):
            for field in source.get('inputs', []):
                input_sources = [s for s in sources if s is not source and s.get('row_id') == rid and overlaps(s.get('field'), field)]
                if input_sources and not any(s.get('accepted') for s in input_sources):
                    source['accepted'] = False
    return {'row_ids': row_ids, 'rows': rows, 'questions': questions, 'findings': findings, 'field_sources': sources}


def _derive_dg(fact, rid, prefix, issue, sources, index):
    out = deepcopy(fact)
    number, pg = fact.get('un_number'), fact.get('packing_group')
    incomplete = False
    for field in ('un_number', 'imdg_class', 'packing_group', 'marine_pollutant', 'limited_quantity'):
        if field not in fact:
            incomplete = True
            issue(rid, f'{prefix}.{field}', 'missing_dg_declaration', f'{rid}: {prefix}.{field} needs an explicit DG declaration.')
    for field in ('marine_pollutant', 'limited_quantity'):
        if field in fact and type(fact[field]) is not bool:
            incomplete = True
            issue(rid, f'{prefix}.{field}', 'invalid_dg_boolean', f'{rid}: {prefix}.{field} requires an explicit boolean declaration.')
    matches = []
    if isinstance(number, str) and len(number) == 4 and number.isascii() and number.isdecimal():
        entries = entries_for_un(int(number))
        matches = [entry for entry in entries if entry.packing_group == pg]
        if not entries:
            issue(rid, prefix, 'unknown_un', f'{rid}: UN {number} is not in the curated local DGL; refer the DG declaration.')
    else:
        issue(rid, prefix, 'invalid_un', f'{rid}: UN identity must be a four-digit declaration.')
    if len(matches) != 1:
        incomplete = True
        issue(rid, prefix, 'unresolved_imdg_entry', f'{rid}: UN {number} / packing group {pg} does not resolve one local DGL row.')
    else:
        entry = matches[0]
        klass = entry.hazard_class + (entry.compatibility_group or '')
        if fact.get('imdg_class') != klass:
            incomplete = True
            issue(rid, f'{prefix}.imdg_class', 'imdg_class_mismatch', f'{rid}: declared class {fact.get("imdg_class")} differs from UN {number} class {klass}.', [str(fact.get('imdg_class')), klass])
        for field, value in {'subsidiary_risks': list(entry.subsidiary_risks), 'stowage_category': entry.stowage_category,
                             'stowage_codes': [c for c in entry.stowage_codes if not c.startswith('H')],
                             'handling_codes': [c for c in entry.stowage_codes if c.startswith('H')],
                             'segregation_codes': list(entry.segregation_codes)}.items():
            if field in fact and fact[field] != value:
                incomplete = True
                issue(rid, f'{prefix}.{field}', 'imdg_classification_conflict', f'{rid}: declared {prefix}.{field} differs from the local DGL.')
            out[field] = value
            sources.append({'row_id': rid, 'row': index, 'field': f'{prefix}.{field}', 'document': 'software:DGL',
                            'quote': f'{entry.source.document}, PDF page {entry.source.pdf_page}, row {entry.source.row}',
                            'value': value, 'inputs': [f'{prefix}.un_number', f'{prefix}.packing_group'], 'accepted': True, 'derived': True})
    additional = fact.get('additional', [])
    if not isinstance(additional, list) or any(not isinstance(item, dict) for item in additional):
        incomplete = True
        issue(rid, f'{prefix}.additional', 'invalid_additional_dg', f'{rid}: additional DG entries require a list of declarations.')
    else:
        child_questions = []
        def child_issue(*args, **kwargs):
            child_questions.append(True)
            issue(*args, **kwargs)
        out['additional'] = [_derive_dg(item, rid, f'{prefix}.additional.{i}', child_issue, sources, index) for i, item in enumerate(additional)]
        incomplete = incomplete or bool(child_questions)
    if incomplete:
        issue(rid, prefix, 'incomplete_dg_entry', f'{rid}: the complete {prefix} identity and shipment flags must be resolved before any dependent DG declaration is accepted.')
        return out
    # Complete entries may serialize unknown permissions as null; partial
    # entries retain only declared and independently derived fields.
    try:
        return DangerousGoods.model_validate(out).model_dump(mode='json')
    except ValidationError:
        issue(rid, prefix, 'invalid_dg_entry', f'{rid}: the {prefix} declaration does not validate; no default shipment facts are accepted.')
        return out


derive_cargo_rows = validate_cargo_rows
