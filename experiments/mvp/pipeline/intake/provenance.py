"""Bind CSV assertions to the correct physical row, column and JSON key.

Decimal arithmetic governs mass conversion. Display rounding is reconciled only
against the same row's exact EDI value; no absolute/relative error tolerance is
used to accept a proposed value.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import io
import json
import mimetypes
from pathlib import Path
import re

from stowage.model.cargo import Container

_MISSING = object()
_UPPER = {'id', 'iso_type', 'pol', 'pod', 'packing_group'}
_UNITS = {'kg': Decimal(1), 'kgs': Decimal(1), 'kilograms': Decimal(1),
          'lb': Decimal('0.45359237'), 'lbs': Decimal('0.45359237'),
          'mt': Decimal(1000), 't': Decimal(1000), 'tonne': Decimal(1000),
          'tonnes': Decimal(1000)}
_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


class _DuplicateJSONKey(ValueError):
    pass


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _text(document):
    text = document.get('text', document.get('content'))
    if isinstance(text, str):
        return text
    if document.get('path'):
        try:
            return Path(document['path']).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            pass
    return ''


def _header(text):
    name = re.sub(r'[^a-z0-9]+', '_', text.strip().lower()).strip('_')
    aliases = {'container': 'id', 'container_no': 'id', 'container_id': 'id',
               'cntr': 'id', 'box': 'id', 'box_id': 'id', 'iso_size': 'iso_type',
               'iso': 'iso_type', 'pod_blank_tally': 'pod', 'pod_blank_is_tally': 'pod',
               'gross_as_typed': '_display_mass', 'edi_vgm_kg': '_exact_mass',
               'vgm_kg': '_exact_mass', 'gross_kg': '_exact_mass',
               'equipment_declaration_json': '_annex'}
    if name in aliases:
        return aliases[name]
    return name if name in Container.model_fields else None


def _records(document):
    """Yield CSV records with their starting physical line and active headers."""
    stream = csv.reader(io.StringIO(_text(document)), strict=True)
    headers = None
    header_names = None
    try:
        while True:
            start = stream.line_num + 1
            cells = next(stream)
            candidate = [_header(cell) for cell in cells]
            if 'id' in candidate and sum(field is not None for field in candidate) >= 2:
                headers = candidate
                header_names = cells
                continue
            if headers is not None and cells:
                yield start, cells, headers, header_names
    except (StopIteration, csv.Error):
        return


def is_image_document(document):
    mime = document.get('mime_type') or document.get('mime')
    if not mime and document.get('path'):
        mime = mimetypes.guess_type(str(document['path']))[0]
    return (str(mime).startswith('image/') or document.get('kind') == 'image'
            or document.get('type') in ('image', 'photo'))


def is_csv_document(document):
    mime = document.get('mime_type', document.get('mime', ''))
    if is_image_document(document):
        return False
    if (mime in ('text/csv', 'application/csv') or document.get('kind') == 'csv'
            or str(document.get('path', '')).lower().endswith('.csv')):
        return True
    return next(_records(document), None) is not None


def _decimal(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _mass(text, *, require_unit=False):
    match = re.fullmatch(rf'\s*({_NUMBER})\s*([a-zA-Z]*)\s*', text)
    if match is None or (require_unit and not match[2]):
        return None
    factor = _UNITS.get(match[2].lower()) if match[2] else Decimal(1)
    if factor is None:
        return None
    number = _decimal(match[1])
    if number is None:
        return None
    return number * factor, Decimal(1).scaleb(number.as_tuple().exponent) * factor / 2


def _kg_mass(text):
    if re.fullmatch(rf'\s*{_NUMBER}\s*(?:kg|kgs|kilograms)?\s*', text, re.IGNORECASE) is None:
        return None
    return _mass(text)


def _matches(source, proposal, field):
    if source is None or proposal is None:
        return source is None and proposal is None
    if isinstance(source, bool) or isinstance(proposal, bool):
        return type(source) is bool and type(proposal) is bool and source == proposal
    if isinstance(source, dict) or isinstance(proposal, dict):
        return (isinstance(source, dict) and isinstance(proposal, dict)
                and source.keys() == proposal.keys()
                and all(_matches(source[k], proposal[k], k) for k in source))
    if isinstance(source, list) or isinstance(proposal, list):
        return (isinstance(source, list) and isinstance(proposal, list) and len(source) == len(proposal)
                and all(_matches(a, b, field) for a, b in zip(source, proposal)))
    if isinstance(source, (int, float, Decimal)):
        left, right = _decimal(source), _decimal(proposal)
        return left is not None and right is not None and (
            left == right or isinstance(source, Decimal) and isinstance(proposal, float)
            and float(left) == proposal)
    if not isinstance(source, str) or not isinstance(proposal, str):
        return False
    source, proposal = source.strip(), proposal.strip()
    if field.split('.')[-1] in _UPPER:
        source, proposal = source.upper(), proposal.upper()
    return source == proposal


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _path(value, field):
    for name in field.split('.'):
        if isinstance(value, dict) and name in value:
            value = value[name]
        elif isinstance(value, list) and name.isdigit() and int(name) < len(value):
            value = value[int(name)]
        else:
            return _MISSING
    return value


def _column_name(index):
    out = ''
    while index:
        index, digit = divmod(index - 1, 26)
        out = chr(65 + digit) + out
    return out


def _scalar(cell, field):
    text = cell.strip()
    if field in ('dg', 'oog', 'special_stowage'):
        try:
            return json.loads(text, parse_float=Decimal, object_pairs_hook=_strict_object)
        except _DuplicateJSONKey:
            raise
        except (ValueError, TypeError):
            return _MISSING
    if field in ('empty', 'reefer', 'direct_delivery'):
        return {'true': True, 'false': False}.get(text.lower(), _MISSING)
    if field in ('length_ft', 'height_m', 'tare_kg', 'max_gross_mass_kg', 'reefer_setpoint_c'):
        return _decimal(text) if re.fullmatch(_NUMBER, text) else _MISSING
    return text


def _normalized(value, field):
    if isinstance(value, dict):
        return {key: _normalized(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized(item, field) for item in value]
    if isinstance(value, str):
        value = value.strip()
        return value.upper() if field.split('.')[-1] in _UPPER else value
    return value


def _ancestor_null(value, field):
    parts = field.split('.')
    for length in range(1, len(parts)):
        ancestor = '.'.join(parts[:length])
        if _path(value, ancestor) is None:
            return ancestor
    return None


def _declarations(document, row_id, field):
    """Collect declarations before comparing anything with the model's choice."""
    document_id = document.get('id', document.get('name'))
    if not isinstance(document_id, str) or not document_id or not isinstance(row_id, str) or not isinstance(field, str):
        return []
    if field.split('.')[0] not in Container.model_fields:
        return []
    evidence = []
    for row, cells, headers, header_names in _records(document):
        id_columns = [i for i, name in enumerate(headers) if name == 'id']
        matching_ids = [index for index in id_columns if index < len(cells)
                        and cells[index].strip().upper() == row_id.strip().upper()]
        if not matching_ids:
            continue
        if len(id_columns) != 1:
            index = matching_ids[0]
            identities = [cells[index].strip().upper() if index < len(cells) else ''
                          for index in id_columns]
            item = {'document': document_id, 'row_id': row_id, 'field': field,
                    'row': row, 'column': index + 1, 'cell': f'{_column_name(index + 1)}{row}',
                    'header': header_names[index], 'value': cells[index], 'cell_value': cells[index],
                    'normalized_value': {'ambiguous_csv_identity': identities}, 'evidence': 'csv_cell',
                    'source_conflict': True, 'conflicting_values': sorted(set(identities)),
                    'invalid_declaration': 'ambiguous_csv_identity'}
            evidence.append((item, {'ambiguous_csv_identity': identities}, []))
            continue
        exacts, displays = [], []
        for index, name in enumerate(headers):
            if index >= len(cells):
                continue
            if name in ('_exact_mass', 'gross_mass_kg'):
                mass = _kg_mass(cells[index])
                if mass is not None:
                    exacts.append((index, mass[0]))
            elif name == '_display_mass':
                mass = _mass(cells[index], require_unit=True)
                if mass is not None:
                    displays.append((index, *mass))
        exact_values = {amount for _, amount in exacts}
        exact = next(iter(exact_values)) if len(exact_values) == 1 else None
        compatible = exact is not None and all(center - half <= exact <= center + half
                                               for _, center, half in displays)
        for index, name in enumerate(headers):
            if index >= len(cells):
                continue
            cell = cells[index]
            source, normalized, json_path = _MISSING, _MISSING, None
            ancestor = None
            invalid = None
            matches = []
            if name == '_annex':
                try:
                    annex = json.loads(cell, parse_float=Decimal, object_pairs_hook=_strict_object)
                except _DuplicateJSONKey as exc:
                    source, normalized = cell, {'invalid_csv_annex': str(exc)}
                    json_path, invalid = field, 'duplicate_json_key'
                except (ValueError, TypeError):
                    continue
                else:
                    declared_id = _path(annex, 'id')
                    if declared_id is not _MISSING and not _matches(declared_id, row_id, 'id'):
                        source, normalized = cell, {'csv_annex_identity_mismatch': _json_value(declared_id)}
                        json_path, invalid = 'id', 'annex_row_identity_mismatch'
                    else:
                        source = _path(annex, field)
                        if source is _MISSING:
                            ancestor = _ancestor_null(annex, field)
                            if ancestor is None:
                                continue
                            source = None
                        else:
                            matches.append(source)
                        normalized, json_path = _normalized(source, field), ancestor or field
            elif field == 'gross_mass_kg' and name in ('_exact_mass', 'gross_mass_kg', '_display_mass'):
                mass = _mass(cell, require_unit=True) if name == '_display_mass' else _kg_mass(cell)
                if mass is None:
                    continue
                center = mass[0]
                matches.append(center)
                if name == '_display_mass' and compatible:
                    matches.append(exact)
                source = cell
                normalized = exact if compatible else center
            elif name == field.split('.')[0]:
                if not cell.strip():
                    continue
                try:
                    source = _scalar(cell, name)
                except _DuplicateJSONKey as exc:
                    source, normalized = cell, {'invalid_csv_cell': str(exc)}
                    invalid = 'duplicate_json_key'
                else:
                    if source is _MISSING:
                        continue
                    if '.' in field:
                        container = {name: source}
                        source = _path(container, field)
                        if source is _MISSING:
                            ancestor = _ancestor_null(container, field)
                            if ancestor is None:
                                continue
                            source = None
                    normalized = _normalized(source, field)
                    if ancestor is None:
                        matches.append(source)
            else:
                continue
            item = {'document': document_id, 'row_id': row_id, 'field': field,
                    'row': row, 'column': index + 1, 'cell': f'{_column_name(index + 1)}{row}',
                    'header': header_names[index], 'value': _json_value(source), 'cell_value': cell,
                    'normalized_value': _json_value(normalized), 'evidence': 'csv_cell'}
            if json_path is not None:
                item['json_path'] = json_path
            if ancestor is not None:
                item['ancestor_field'] = ancestor
            if invalid:
                item['source_conflict'] = True
                item['conflicting_values'] = [cell]
                item['invalid_declaration'] = invalid
            evidence.append((item, normalized, matches))
    values = []
    for _, normalized, _ in evidence:
        if not any(_matches(normalized, previous, field) for previous in values):
            values.append(normalized)
    absent_and_present = (any(item.get('ancestor_field') for item, _, _ in evidence)
                          and any(not item.get('ancestor_field') for item, _, _ in evidence))
    if len(values) > 1 or absent_and_present:
        # Strings keep mixed-type and structured candidates sortable in callers.
        candidates = [_json_value(value) for value in values]
        numeric = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                      for value in candidates)
        if not numeric and (not all(isinstance(value, (str, bool)) for value in candidates)
                            or len({type(value) for value in candidates}) > 1):
            candidates = [json.dumps(value, sort_keys=True, ensure_ascii=False) for value in candidates]
        candidates.sort()
        for item, _, _ in evidence:
            item['source_conflict'] = True
            item['conflicting_values'] = candidates
    return evidence


def csv_declarations(document, row_id, field):
    """All decoded declarations, including conflicts the model did not retain.

    Ancestor nulls block contradictory nested declarations but do not establish
    a value for that nested field. Locations refer to physical CSV cells.
    """
    return [item for item, _, _ in _declarations(document, row_id, field)]


def csv_evidence(document, row_id, field, value):
    """Matching source cells, marked against *all* declarations for this field.

    Only explicit unit conversion and same-row display/EDI reconciliation are
    allowed. A conflict never turns an unsupported model value into evidence.
    """
    return [item for item, _, matches in _declarations(document, row_id, field)
            if any(_matches(source, value, field) for source in matches)]
