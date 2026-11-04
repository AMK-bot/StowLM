"""Deterministic gates for proposed readings; no load-list values are extraction hints.

Every Container top-level field needs an explicit declaration, including nulls and
flags. Pydantic defaults are serialization conveniences, not evidence of cargo facts.
The local DGL is a curated subset: an absent UN is unresolved, not a claim that no
such UN exists internationally. An unknown or unresolved UN/packing group blocks its complete DG entry,
including dependent class/category declarations, until an explicit correction.
Slot screening is necessary capability checking;
placement, stability and aggregate constraints remain the full checker's job.

Mass policy: never infer a CSC plate rating from nominal size. Independently gate
the declared plate rating and actual vessel support ratings even on partial rows.
An optional explicitly supplied policy is read only from
rule_overrides['intake.limits']['max_gross_mass_kg_by_length'] (kg keyed by feet).
Its source path is recorded in findings; there is no built-in per-length ceiling.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import csv
import io
import json
import math
import re
from typing import Annotated, Any, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError

from experiments.mvp.pipeline.contract.schema import Question
from stowage.model.cargo import Container, DangerousGoods, OutOfGauge
from stowage.model.instance import Instance
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.rules.imdg.dgl import entries_for_un
from stowage.conventions import parse_slot
from .provenance import csv_evidence, csv_declarations, is_csv_document, is_image_document
from .text_provenance import text_evidence, confirmed_value


def _leaves(field: str, value: Any):
    if field in ('dg', 'oog') and isinstance(value, dict) and value:
        for key, item in value.items():
            yield from _leaves(f'{field}.{key}', item)
    else:
        yield field, value


def _put(row: dict, path: str, value: Any) -> None:
    parts = path.split('.')
    target = row
    for i, part in enumerate(parts):
        key = int(part) if isinstance(target, list) and part.isdigit() else part
        if isinstance(target, list):
            if not isinstance(key, int):
                raise ValueError('list paths require numeric indexes')
            while len(target) <= key:
                target.append(None)
        if i == len(parts) - 1:
            target[key] = value
        else:
            child = target[key] if isinstance(target, list) else target.get(key)
            if not isinstance(child, (dict, list)):
                child = [] if parts[i + 1].isdigit() else {}
                target[key] = child
            target = child


@lru_cache(maxsize=512)
def _field_adapter(path: str):
    parts = path.split('.')
    annotation = Container
    metadata = []
    for part in parts:
        if get_origin(annotation) is list:
            if not part.isdigit() or int(part) > 100:
                raise ValueError('nested cargo index must be 0 to 100')
            annotation, metadata = get_args(annotation)[0], []
            continue
        options = get_args(annotation) or (annotation,)
        model = next((t for t in options if isinstance(t, type) and issubclass(t, BaseModel)), None)
        if model is not None:
            if part not in model.model_fields:
                raise ValueError('unknown Container field')
            info = model.model_fields[part]
            annotation, metadata = info.annotation, info.metadata
        else:
            raise ValueError('unsupported nested field')
    annotation = Annotated[annotation, *metadata] if metadata else annotation
    return TypeAdapter(annotation)


def _normalize(path: str, value: Any):
    """Normalize schema scalars only; never infer units or cargo attributes."""
    adapter = _field_adapter(path)
    name = path.split('.')[-1]
    if isinstance(value, str):
        value = value.strip()
        if name in ('id', 'iso_type', 'pol', 'pod', 'packing_group'):
            value = value.upper()
    if isinstance(value, bool) and name not in ('empty', 'reefer', 'direct_delivery', 'marine_pollutant',
                                              'limited_quantity', 'ctu_closed', 'protected_from_heat',
                                              'clear_living_quarters', 'ventilation_compliant', 'shaded',
                                              'air_circulation', 'dry', 'handling_compliant',
                                              'subsidiary_hazards_verified', 'short_international_voyage'):
        raise ValueError('boolean supplied for non-boolean field')
    normalized = adapter.validate_python(value)
    dumped = adapter.dump_python(normalized, mode='json')
    dumped = _declared_only(dumped, value)
    json.dumps(dumped, allow_nan=False)
    return dumped


def _declared_only(normalized, original):
    if isinstance(normalized, dict) and isinstance(original, dict):
        return {key: _declared_only(normalized[key], item) for key, item in original.items() if key in normalized}
    if isinstance(normalized, list) and isinstance(original, list):
        return [_declared_only(item, source) for item, source in zip(normalized, original)]
    return normalized


def _missing_declarations(value, model, prefix, rid, issue):
    for name, info in model.model_fields.items():
        field = prefix + '.' + name
        if name not in value:
            issue(rid, field, 'missing_field', f'{rid}: explicitly declare {field}; schema defaults are not cargo evidence.')
            continue
        item = value[name]
        annotation = info.annotation
        if get_origin(annotation) is list:
            nested = get_args(annotation)[0]
            if isinstance(item, list) and isinstance(nested, type) and issubclass(nested, BaseModel):
                for i, entry in enumerate(item):
                    if isinstance(entry, dict):
                        _missing_declarations(entry, nested, f'{field}.{i}', rid, issue)
        elif isinstance(item, dict):
            candidates = get_args(annotation) or (annotation,)
            nested = next((t for t in candidates if isinstance(t, type) and issubclass(t, BaseModel)), None)
            if nested is not None:
                _missing_declarations(item, nested, field, rid, issue)


def _declares_null(field, quote):
    text = quote.lower().replace('""', '"')
    if re.search(rf'(?:"|\b){field}["\s]*[:=]\s*(null|none)\b', text):
        return True
    if field == 'dg':
        return bool(re.search(r'\b(non[ -]?dg|no\s+(dg|dangerous\s+goods)|dg\s*[:=/-]?\s*(nil|none))\b', text))
    return bool(re.search(r'\b(in[ -]gauge|no\s+oog|oog\s*[:=/-]?\s*(nil|none))\b', text))


def _token(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def validate_reading(raw: dict, documents: list[dict], base_instance: Instance | dict, *,
                     planner_confirmations=None, duplicate_resolutions=()) -> dict:
    planner_confirmations = planner_confirmations or {}
    base = base_instance if isinstance(base_instance, Instance) else Instance.model_validate(base_instance)
    docs = {str(d.get('id', d.get('name', ''))): d for d in documents}
    grouped = defaultdict(lambda: defaultdict(list))
    sources, findings, questions = [], [], []
    confirmed_fields = defaultdict(set)
    observations = raw.get('observations', [])
    if not isinstance(observations, list):
        observations = []
    row_ids = {str(o.get('row_id', '')) for o in observations if isinstance(o, dict) and o.get('row_id')}
    row_ids.update(base.load_list)  # Expected identities are a completeness gate, never field values.
    row_ids.update(str(d['row_id']) for d in raw.get('duplicates', []) if isinstance(d, dict) and d.get('row_id'))
    ids = sorted(row_ids)
    indexes = {rid: i for i, rid in enumerate(ids)}
    seen_questions = set()

    def issue(rid, field, reason, message, candidates=()):
        key = (rid, field, reason)
        if key in seen_questions:
            return
        seen_questions.add(key)
        question = Question(id=f'{rid or "document"}:{field or "row"}:{reason}',
                            row=indexes.get(rid), field=field, prompt=message,
                            candidates=[str(c) for c in candidates], reason=reason)
        questions.append(question.model_dump(mode='json'))
        findings.append({'row': indexes.get(rid), 'row_id': rid, 'field': field,
                         'code': reason, 'severity': 'blocking', 'message': message})

    if not row_ids:
        issue(None, None, 'empty_reading', 'No cargo rows were read. Supply a load list.')
    for observation in observations:
        if not isinstance(observation, dict):
            issue(None, None, 'invalid_observation', 'A proposed reading is not an object.')
            continue
        rid, field = str(observation.get('row_id', '')), observation.get('field')
        doc_id, quote = str(observation.get('document', '')), observation.get('quote')
        if not rid or not isinstance(field, str) or not field:
            issue(None, None, 'invalid_observation', 'A proposed reading lacks its row identity or field.')
            continue
        document = docs.get(doc_id)
        csv_document = document is not None and is_csv_document(document)
        image = document is not None and is_image_document(document)
        text = '' if document is None else str(document.get('text', document.get('content', document.get('transcription', ''))))
        verified = document is not None and isinstance(quote, str) and bool(quote.strip()) and quote in text
        unverified_image = image and not text and isinstance(quote, str) and bool(quote.strip())
        if not csv_document and not verified and not unverified_image:
            issue(rid, field, 'unsupported_source', f'{rid}: provide an exact source quote for {field}.')
            continue
        if observation.get('uncertain') is True:
            issue(rid, field, 'uncertain_reading', f'{rid}: source reading for {field} is uncertain. Confirm it explicitly.')
            continue
        for path, value in _leaves(field, observation.get('value')):
            if (path == 'special_stowage' or path.startswith('special_stowage.')) and value:
                issue(rid, path, 'instruction_requires_reader', f'{rid}: proposed stowage instruction requires the reader and planner confirmation gate; intake cannot create a rule.')
                sources.append({'row': indexes[rid], 'row_id': rid, 'field': path, 'document': doc_id,
                                'quote': quote, 'evidence': 'proposed_instruction', 'accepted': False,
                                'reader_request': {'text': quote, 'origin': 'intake', 'requires_confirmation': True}})
                continue
            confirmed = confirmed_value(planner_confirmations, doc_id, rid, path, value)
            if confirmed:
                confirmed_fields[rid].add(path)
            if not csv_document and not confirmed and (image or not text_evidence(document or {}, rid, path, value, quote)):
                reason = 'unverified_transcription' if unverified_image else 'unsupported_source'
                issue(rid, path, reason, f'{rid}: source does not authenticate {path} and its declared value. Confirm it explicitly.')
                sources.append({'row': indexes[rid], 'row_id': rid, 'field': path, 'document': doc_id,
                                'quote': quote, 'evidence': reason, 'accepted': False})
                continue
            cells = csv_evidence(document, rid, path, value) if csv_document else []
            if csv_document and not cells:
                issue(rid, path, 'unsupported_source', f'{rid}: CSV row and field do not declare the proposed {path}. Provide a source or correct the value.')
                sources.append({'row': indexes[rid], 'row_id': rid, 'field': path, 'document': doc_id,
                                'quote': quote, 'evidence': 'unsupported_csv_claim', 'accepted': False})
                continue
            field_sources = []
            for cell in cells or [None]:
                source = {'row': indexes[rid], 'row_id': rid, 'field': path, 'document': doc_id,
                          'quote': quote, 'evidence': 'planner_confirmation' if confirmed else 'exact_quote'}
                if cell is not None:
                    source.update({key: item for key, item in cell.items() if key != 'row'})
                    source['csv_row'] = cell['row']
                    source['quote'] = cell['cell_value']
                sources.append(source)
                field_sources.append(source)
            if path in ('dg', 'oog') and value is None and not csv_document and not _declares_null(path, quote):
                issue(rid, path, 'unsupported_null_declaration', f'{rid}: explicitly confirm whether {path} is absent; unknown is not a no declaration.')
                for source in field_sources:
                    source['accepted'] = False
                continue
            if any(cell.get('source_conflict') for cell in cells):
                candidates = sorted({number for cell in cells for number in cell.get('conflicting_values', [])})
                issue(rid, path, 'cross_document_conflict', f'{rid}: CSV cells disagree on {path}. Confirm the correct value.', candidates)
                for source in field_sources:
                    source['accepted'] = False
                continue
            try:
                normalized_values = [_normalize(path, cell['normalized_value']) for cell in cells] if cells else [_normalize(path, value)]
            except (ValueError, TypeError, ValidationError) as exc:
                issue(rid, path, 'invalid_field', f'{rid}: invalid {path}: {str(exc).splitlines()[0]}')
                for source in field_sources:
                    source['accepted'] = False
                continue
            for source in field_sources:
                source['accepted'] = True
            grouped[rid][path].extend(normalized_values)

    # Audit source declarations independently of the assertions Spark retained.
    for rid in ids:
        fields = set(Container.model_fields) | set(grouped[rid])
        for field in fields:
            if any(field == name or field.startswith(name + '.') for name in confirmed_fields[rid]):
                continue
            declarations = [cell for document in docs.values() if is_csv_document(document)
                            for cell in csv_declarations(document, rid, field)]
            values = set()
            for cell in declarations:
                try:
                    declared = _normalize(field, cell['normalized_value'])
                except (ValueError, TypeError, ValidationError):
                    declared = cell['normalized_value']
                values.add(_token(declared))
            if len(values) > 1 or any(cell.get('source_conflict') for cell in declarations):
                issue(rid, field, 'cross_document_conflict', f'{rid}: CSV declarations disagree on {field}. Confirm the complete field.', sorted(values))

    rows = []
    for rid in ids:
        row = {}
        overlaps = {field for field in grouped[rid]
                    if any(other.startswith(field + '.') for other in grouped[rid])}
        for field in sorted(overlaps):
            issue(rid, field, 'cross_document_conflict', f'{rid}: overlapping whole-field and nested declarations for {field}. Confirm the complete field.')
        for field, values in sorted(grouped[rid].items()):
            if any(field == parent or field.startswith(parent + '.') for parent in overlaps):
                continue
            unique = {_token(v): v for v in values}
            if len(unique) != 1:
                issue(rid, field, 'cross_document_conflict', f'{rid}: sources disagree on {field}. Confirm the value.', unique)
            else:
                _put(row, field, next(iter(unique.values())))
        # A null parent and a nested declaration conflict regardless of iteration order.
        for parent in ('dg', 'oog'):
            if parent in grouped[rid] and any(k.startswith(parent + '.') for k in grouped[rid]):
                row.pop(parent, None)
                issue(rid, parent, 'cross_document_conflict', f'{rid}: sources disagree whether {parent} applies.')
        if not grouped[rid]:
            issue(rid, None, 'missing_row', f'{rid}: expected load-list row has no supported reading. Supply the full row.')
            row['id'] = rid  # Identity placeholder only; blocked until explicit source declarations arrive.
        for field in Container.model_fields:
            if field not in row and not any(q['row'] == indexes[rid] and q['field'] == field for q in questions):
                reason = ('missing_vgm' if field == 'gross_mass_kg' else
                          'missing_reefer_setpoint' if field == 'reefer_setpoint_c' and row.get('reefer') is True else
                          'missing_field')
                issue(rid, field, reason, f'{rid}: supply {field}; no supported declaration was found.')
        if row.get('id') is not None and row['id'] != rid:
            issue(rid, 'id', 'row_identity_mismatch', f'{rid}: row identity disagrees with declared id {row["id"]}.')
        if row.get('reefer') is True and row.get('reefer_setpoint_c') is None:
            issue(rid, 'reefer_setpoint_c', 'missing_reefer_setpoint', f'{rid}: reefer set point is missing. Confirm degrees Celsius.')
        for field, model in (('dg', DangerousGoods), ('oog', OutOfGauge)):
            if isinstance(row.get(field), dict):
                _missing_declarations(row[field], model, field, rid, issue)
        _check_row(row, rid, base, issue, findings, indexes[rid])
        try:
            candidate = Container.model_validate(row)
        except ValidationError as exc:
            for error in exc.errors(include_url=False):
                field = '.'.join(str(p) for p in error['loc'])
                if not field:
                    field = ('length_ft' if 'iso_type length' in error['msg'] else
                             'height_m' if 'iso_type height' in error['msg'] else
                             'reefer_setpoint_c' if 'reefer_setpoint_c' in error['msg'] else 'gross_mass_kg')
                if error['type'] == 'missing':
                    if '.' in field:
                        issue(rid, field, 'missing_field', f'{rid}: supply {field}; no supported declaration was found.')
                    continue
                issue(rid, field, 'container_validation', f'{rid}: {error["msg"]}')
            candidate = None
        if candidate is not None:
            if all(field in row for field in Container.model_fields) and not any(
                    q['row'] == indexes[rid] and q['reason'] == 'missing_field' for q in questions):
                row = candidate.model_dump(mode='json')
        rows.append(row)

    for duplicate in raw.get('duplicates', []):
        if isinstance(duplicate, dict) and duplicate.get('row_id'):
            rid = str(duplicate['row_id'])
            issue(rid, 'id', 'duplicate_id', f'{rid}: the same id occurs twice in a source. Confirm one physical box or correct the id.', ['keep_one'])
    for doc_id, document in docs.items():
        # CSV data lines are distinct physical declarations. References in prose,
        # forwarded emails and the equipment JSON annex are not duplicate boxes.
        lines = str(document.get('text', '')).splitlines()
        header = None
        counts = defaultdict(int)
        for line in lines:
            if not line.strip() or line.lstrip().startswith(('#', '{', '[')):
                continue
            try:
                cells = next(csv.reader(io.StringIO(line)))
            except csv.Error:
                continue
            lowered = [re.sub(r'[^a-z0-9]+', '_', cell.strip().lower()).strip('_') for cell in cells]
            id_names = ('id', 'container_id', 'container', 'container_no', 'cntr', 'box_id', 'box')
            if header is None:
                header = next((i for i, name in enumerate(lowered) if name in id_names), None)
                continue
            if header < len(cells):
                cid = cells[header].strip().upper()
                if cid in indexes:
                    counts[cid] += 1
        for cid, count in counts.items():
            if count > 1 and not _duplicate_confirmed(duplicate_resolutions, cid, doc_id):
                issue(cid, 'id', 'duplicate_id', f'{cid}: occurs {count} times in CSV {doc_id}. Confirm one physical box or correct the id.', ['keep_one'])
    # Different extraction groups must not smuggle through one physical identity twice.
    actual = defaultdict(list)
    for i, row in enumerate(rows):
        if row.get('id'):
            actual[row['id']].append(ids[i])
    for cid, groups in actual.items():
        if len(groups) > 1:
            for rid in groups:
                issue(rid, 'id', 'duplicate_id', f'{cid}: duplicate identity across cargo rows.')
    for source in sources:
        if any(q['row'] == source['row'] and (q['field'] is None or
               source['field'] == q['field'] or source['field'].startswith(q['field'] + '.') or
               q['field'].startswith(source['field'] + '.')) for q in questions):
            source['accepted'] = False
    return {'row_ids': ids, 'rows': rows, 'questions': questions, 'field_sources': sources, 'findings': findings}


def _duplicate_confirmed(resolutions, row_id, document):
    return any(isinstance(item, dict) and item.get('row_id') == row_id and
               item.get('document') in (document, '*') and item.get('action') == 'keep_one'
               for item in resolutions)


def _check_row(box: dict, rid: str, base: Instance, issue, findings, index):
    """Check each relation with only its own prerequisites; no complete-row gate."""
    yard = {cid: stack for stack in base.yard.stacks for cid in stack.boxes}
    yard_ids = set(yard) | set(base.yard.quay_buffer)
    cid, length = box.get('id'), box.get('length_ft')
    mass, rating = box.get('gross_mass_kg'), box.get('max_gross_mass_kg')
    if cid is not None and cid not in yard_ids:
        issue(rid, 'id', 'unknown_yard_id', f'{rid}: id is not present in the supplied yard. Confirm the id or revise the yard.')
    elif cid in yard and length is not None and length != yard[cid].length_ft:
        issue(rid, 'length_ft', 'yard_length_mismatch', f'{rid}: length disagrees with its yard stack geometry.')
    if box.get('pod') is not None and box['pod'] not in {call.locode for call in base.rotation.calls[base.call_index + 1:]}:
        issue(rid, 'pod', 'pod_not_on_rotation', f'{rid}: POD {box["pod"]} is not a later call on this rotation.')
    if 'max_gross_mass_kg' in box and rating is None:
        issue(rid, 'max_gross_mass_kg', 'missing_csc_rating', f'{rid}: CSC maximum gross mass is unknown. Supply the plate rating.')
    if mass is not None and rating is not None and mass > rating:
        issue(rid, 'gross_mass_kg', 'container_validation', f'{rid}: gross_mass_kg must not exceed the supplied CSC max_gross_mass_kg.')
    if mass is not None and box.get('tare_kg') is not None and mass < box['tare_kg']:
        issue(rid, 'gross_mass_kg', 'container_validation', f'{rid}: gross_mass_kg must include tare_kg.')
    iso = box.get('iso_type')
    if iso:
        expected_length = {'2': 20, '4': 40, 'L': 45}.get(iso[0])
        expected_height = {'2': 2.591, '5': 2.896}.get(iso[1])
        if length is not None and expected_length != length:
            issue(rid, 'length_ft', 'container_validation', f'{rid}: iso_type length code must agree with length_ft.')
        if box.get('height_m') is not None and expected_height is not None and abs(box['height_m'] - expected_height) > .001:
            issue(rid, 'height_m', 'container_validation', f'{rid}: iso_type height code must agree with height_m.')
    if box.get('reefer_setpoint_c') is not None and box.get('reefer') is False:
        issue(rid, 'reefer_setpoint_c', 'container_validation', f'{rid}: reefer_setpoint_c requires reefer=True.')
    overrides = base.rule_overrides.get('intake.limits', {})
    ceilings = overrides.get('max_gross_mass_kg_by_length', {})
    declared_ceiling = None
    if isinstance(ceilings, dict) and length is not None and str(length) in ceilings:
        ceiling = ceilings[str(length)]
        if not isinstance(ceiling, bool) and isinstance(ceiling, (int, float)) and math.isfinite(ceiling) and ceiling > 0:
            declared_ceiling = ceiling
            source = f'base_instance.rule_overrides.intake.limits.max_gross_mass_kg_by_length.{length}'
            findings.append({'row': index, 'row_id': rid, 'field': 'gross_mass_kg',
                             'code': 'declared_intake_mass_policy', 'severity': 'info',
                             'source': source, 'limit_kg': ceiling,
                             'message': 'Explicit intake screening ceiling; not an inferred CSC plate rating.'})
            if mass is not None and mass > ceiling:
                issue(rid, 'gross_mass_kg', 'intake_mass_limit', f'{rid}: mass {mass} kg exceeds the explicitly declared {length}-foot intake ceiling {ceiling} kg ({source}).')
    if mass is not None and rating is None and declared_ceiling is None:
        issue(rid, 'gross_mass_kg', 'missing_mass_limit', f'{rid}: gross mass is sourced but cannot pass the mass screen without a supplied CSC rating or explicit intake ceiling.')
    if box.get('direct_delivery') is True and cid in yard_ids:
        issue(rid, 'direct_delivery', 'direct_delivery_in_yard', f'{rid}: direct-delivery flag conflicts with the yard position.')
    dg = box.get('dg')
    primary = dg.get('imdg_class') if isinstance(dg, dict) else None
    if rule_freeze_v1_enabled() and 'direct_delivery' in box and 'dg' in box and (dg is None or primary is not None) and box['direct_delivery'] != base.port.requires_direct_delivery(primary):
        issue(rid, 'direct_delivery', 'port_direct_delivery', f'{rid}: direct-delivery declaration conflicts with this port\'s DG policy.')
    if isinstance(dg, dict):
        _check_dg(dg, rid, 'dg', issue)
    _check_capability(box, rid, base, issue)


def _check_dg(dg, rid, prefix, issue):
    number = dg.get('un_number')
    if number is None:
        issue(rid, prefix, 'missing_imdg_identity', f'{rid}: UN identity is missing or unresolved. Confirm the complete {prefix} declaration before its dependent DG fields can be verified.')
    else:
        entries = entries_for_un(int(number))
        if not entries:
            issue(rid, prefix, 'unknown_un', f'{rid}: UN {number} is absent from the local curated IMDG table. Confirm the complete {prefix} declaration; its class, category and other dependent fields cannot be verified.')
        else:
            if 'packing_group' in dg:
                matched = [e for e in entries if e.packing_group == dg['packing_group']]
            else:
                matched = list(entries) if len(entries) == 1 and entries[0].packing_group is None else []
            if len(matched) != 1:
                issue(rid, prefix, 'unresolved_imdg_entry', f'{rid}: the supplied UN and packing group do not resolve one local IMDG row. Confirm the complete {prefix} declaration; dependent DG fields remain unverified.')
            if dg.get('imdg_class') is not None and dg['imdg_class'] not in {e.hazard_class + (e.compatibility_group or '') if e.hazard_class.startswith('1.') else e.hazard_class for e in matched or entries}:
                issue(rid, f'{prefix}.imdg_class', 'imdg_class_mismatch', f'{rid}: declared class {dg["imdg_class"]} disagrees with UN {number} in the IMDG table.')
            if matched and dg.get('stowage_category') is not None and dg['stowage_category'] not in {e.stowage_category for e in matched}:
                issue(rid, f'{prefix}.stowage_category', 'imdg_stowage_category', f'{rid}: declared stowage category disagrees with the local IMDG row.')
    for i, additional in enumerate(dg.get('additional', [])):
        if isinstance(additional, dict):
            _check_dg(additional, rid, f'{prefix}.additional.{i}', issue)


def _check_capability(box: dict, rid: str, base: Instance, issue):
    cells = {cell.id: cell for cell in base.vessel.cells}
    stacks = {stack.id: stack for stack in base.vessel.stacks}
    geometry = []

    def mass_limit(stack, length):
        rating = stack.max_mass_20_kg if length == 20 else stack.max_mass_40_kg
        return stack.max_mass_kg if rating is None else rating

    length_options = [box['length_ft']] if box.get('length_ft') is not None else [20, 40, 45]
    for length in length_options:
        for cell in cells.values():
            if not getattr(cell, f'accepts_{length}'):
                continue
            supports = [[cell]]
            if length != 20:
                address = parse_slot(cell.id)
                supports = []
                for other_bay in (address.bay - 2, address.bay + 2):
                    other = cells.get(f'{other_bay:03d}{address.row:02d}{address.tier:02d}')
                    if other is not None and getattr(other, f'accepts_{length}'):
                        supports.append([cell, other])
            for support in supports:
                if any(stacks[c.stack_id].on_deck and stacks[c.stack_id].cell_ids.index(c.id) + 1 >
                       base.port.quay_crane.max_on_deck_tiers for c in support):
                    continue
                geometry.append((length, support))
    if not geometry:
        issue(rid, 'length_ft', 'ship_capability', f'{rid}: no ship cell supports the declared length within current-port crane limits.')
        return

    tests = {
        'reefer': lambda length, support: box.get('reefer') is not True or any(c.reefer_plug for c in support),
        'height_m': lambda length, support: box.get('height_m') is None or all(
            box['height_m'] <= stacks[c.stack_id].max_height_m + 1e-6 for c in support),
        'gross_mass_kg': lambda length, support: box.get('gross_mass_kg') is None or all(
            box['gross_mass_kg'] / len(support) <= mass_limit(stacks[c.stack_id], length) + 1e-6 for c in support),
    }
    failures = [field for field, test in tests.items() if not any(test(*option) for option in geometry)]
    for field in failures:
        issue(rid, field, 'ship_capability', f'{rid}: declared {field} exceeds every compatible ship support or plug capability.')
    if not failures and not any(all(test(*option) for test in tests.values()) for option in geometry):
        field = next((name for name in ('gross_mass_kg', 'height_m', 'reefer') if name in box), 'length_ft')
        issue(rid, field, 'ship_capability', f'{rid}: no single ship position satisfies the combined declared length, height, mass and reefer requirements.')
