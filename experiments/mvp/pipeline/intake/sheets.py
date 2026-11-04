"""CSV cells are parsed by software; a model may propose only their meaning.

The named no-model baseline is the Literal-header parser. It recognizes exact
canonical field names after punctuation/case normalization, plus the independent
freight tokens Container, Container Number, ISO, ISO Code, POL, POD and VGM.
It neither reads prose nor searches cells for an answer. A model header map
adds arbitrary headers without adding values, conversion rules or authority.
Coordinates are one-based physical CSV lines and columns, not array offsets.
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import io
from pathlib import Path
import re
import unicodedata


BASELINE = 'Literal-header parser'
SHEET_METADATA = {'declaration_method','declaration_date','signatory','voyage','booking_reference'}
_MASS = {'gross_mass_kg', 'tare_kg', 'max_gross_mass_kg'}
_BOOL = {'empty', 'reefer', 'direct_delivery', 'dg_declared','tank','equipment_reefer'}
_OPTIONAL_BLANK = {'special_stowage', 'oog', 'dg', 'reefer_setpoint_c',
                   'tare_kg', 'max_gross_mass_kg', *_BOOL}
_FIELDS = {'id', 'iso_type', 'pol', 'pod', 'booked_vessel', 'booking_status', 'seal', 'commodity',
           'reefer_setpoint_c', 'special_stowage', 'oog', 'dg', 'length_ft','height_m', *_MASS, *_BOOL, *SHEET_METADATA}
_DG = {'un_number', 'imdg_class', 'packing_group', 'limited_quantity',
       'marine_pollutant', 'packing_form', 'ctu_closed', 'flashpoint_c'}
_FACTORS = {'kg': Decimal(1), 'lb': Decimal('0.45359237'), 't': Decimal(1000)}
_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
_VALUE = re.compile(rf'^({_NUMBER}|[+-]?\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?)\s*([A-Za-z°℃℉]*)$')
_NULL = {'', '-', '—', 'n/a', 'na', 'unknown', '?', 'tba', 'pending'}


def _text(document):
    text = document.get('text', document.get('content'))
    if isinstance(text, str):
        return text
    if document.get('path'):
        return Path(document['path']).read_text(encoding='utf-8-sig')
    return ''


def _records(document):
    reader = csv.reader(io.StringIO(_text(document).lstrip('\ufeff')), strict=True)
    records = []
    try:
        while True:
            row = reader.line_num + 1
            records.append((row, next(reader)))
    except StopIteration:
        return records


def _name(value):
    return re.sub(r'[^a-z0-9]+', '_', value.strip().lower()).strip('_')


def _field(field):
    if not isinstance(field, str):
        return False
    if field in _FIELDS:
        return True
    return bool(re.fullmatch(r'dg\.(?:additional\.[0-8]\.)?(?:' + '|'.join(sorted(_DG)) + ')', str(field)))


def _unit(value):
    unit = str(value or '').strip().lower().replace('°', '')
    return {'kgs': 'kg', 'kilogram': 'kg', 'kilograms': 'kg', 'lbs': 'lb',
            'pounds': 'lb', 'pound': 'lb', 'mt': 't', 'tonne': 't',
            'tonnes': 't', 'ton': 't', 'tons': 't', 'celsius': 'C',
            'c': 'C', '℃': 'C', 'fahrenheit': 'F', 'f': 'F', '℉': 'F'}.get(unit, unit)


def _baseline_header(header):
    name = _name(header)
    if name in _FIELDS:
        return name, 'kg' if name in _MASS else 'C' if name == 'reefer_setpoint_c' else None
    tokens = name.split('_')
    # A fixed small vocabulary, independent of fixture formats. No substrings,
    # positional assumptions, language translation or document-specific aliases.
    if tokens in (['container'], ['container', 'number']):
        return 'id', None
    if tokens in (['iso'], ['iso', 'code']):
        return 'iso_type', None
    if tokens[:1] == ['vgm'] and (len(tokens) == 1 or len(tokens) == 2 and _unit(tokens[1]) in _FACTORS):
        return 'gross_mass_kg', _unit(tokens[1]) if len(tokens) == 2 else None
    return None, None


def baseline_mapping(document):
    """First header with literal id plus a second recognized field; no model."""
    for row, cells in _records(document):
        columns = []
        for index, header in enumerate(cells, 1):
            field, unit = _baseline_header(header)
            if field:
                item = {'column': index, 'header': header, 'field': field}
                if unit:
                    item['unit'] = unit
                columns.append(item)
        if sum(item['field'] == 'id' for item in columns) == 1 and len(columns) >= 2:
            return {'document': document['id'], 'header_row': row, 'columns': columns}
    return None


def _issue(reason, *, document, row_id=None, field='id', candidates=(), **extra):
    return {'row_id': row_id, 'field': field, 'reason': reason,
            'prompt': reason.replace('_', ' '), 'candidates': list(candidates),
            'document': document, **extra}


def validate_mapping(document, mapping):
    """Validate the physical binding, never a model-provided cell value."""
    issues = []
    document_id = document.get('id')
    if not isinstance(mapping, dict) or mapping.get('document') != document_id:
        return None, [_issue('invalid_header_map_document', document=document_id)]
    row = mapping.get('header_row')
    if type(row) is not int or row <= 0:
        return None, [_issue('invalid_header_row', document=document_id)]
    records = dict(_records(document))
    headers = records.get(row)
    if headers is None:
        return None, [_issue('missing_header_row', document=document_id)]
    columns = mapping.get('columns')
    if not isinstance(columns, list) or not columns:
        return None, [_issue('missing_column_maps', document=document_id)]
    checked, seen = [], set()
    for proposal in columns:
        if not isinstance(proposal, dict):
            issues.append(_issue('invalid_column_map', document=document_id))
            continue
        column, field = proposal.get('column'), proposal.get('field')
        if (type(column) is not int or column <= 0 or column > len(headers)
                or proposal.get('header') != headers[column - 1] or not _field(field)):
            issues.append(_issue('unbound_column_map', document=document_id, field=str(field), column=column))
            continue
        if column in seen:
            issues.append(_issue('ambiguous_column_map', document=document_id, field=field, column=column))
            continue
        literal,_=_baseline_header(headers[column-1])
        if literal and literal!=field:
            issues.append(_issue('header_field_conflict',document=document_id,field=field,column=column,
                                 candidates=[literal,field]))
            continue
        seen.add(column)
        item = {'column': column, 'header': headers[column - 1], 'field': field}
        if proposal.get('unit') is not None:
            item['unit'] = _unit(proposal['unit'])
            if item['unit'] not in {*_FACTORS, 'C', 'F','ft','m'}:
                issues.append(_issue('unsupported_unit', document=document_id, field=field, column=column))
                continue
            if proposal.get('unit_quote') is not None:
                quote = proposal['unit_quote']
                # Only an explicit global units declaration can supply missing
                # header/cell units. A model's chosen conversion is not evidence.
                declaration = re.fullmatch(r'(?:mass|weight|weights)\s+(?:unit|units|in)\s*[:=]?\s*([A-Za-z°℃℉]+)', str(quote).strip(), re.I)
                metadata_cells = [cell for physical_row, cells in records.items() if physical_row < row for cell in cells]
                if (not declaration or quote not in metadata_cells
                        or _unit(declaration[1]) != item['unit']):
                    issues.append(_issue('unbound_unit_declaration', document=document_id, field=field, column=column))
                    continue
                item['unit_quote'] = quote
        checked.append(item)
    if issues or sum(item['field'] == 'id' for item in checked) != 1:
        if not issues:
            issues.append(_issue('ambiguous_id_column', document=document_id))
        return None, issues
    return {'document': document_id, 'header_row': row, 'columns': checked}, []


def _numeric(cell, unit, *, temperature=False):
    match = _VALUE.fullmatch(cell.strip())
    if not match:
        raise ValueError('invalid_numeric_cell')
    source_unit = _unit(match[2])
    if source_unit and unit and source_unit != unit:
        raise ValueError('conflicting_cell_unit')
    actual = source_unit or unit
    if actual not in ({'C', 'F'} if temperature else _FACTORS):
        raise ValueError('missing_or_invalid_unit')
    try:
        number = Decimal(match[1].replace(',', ''))
    except InvalidOperation:
        raise ValueError('invalid_numeric_cell') from None
    if temperature:
        number = (number - 32) * Decimal(5) / Decimal(9) if actual == 'F' else number
    else:
        number *= _FACTORS[actual]
    return int(number) if number == number.to_integral_value() else float(number), actual


def _header_unit(header, *, temperature=False):
    tokens = re.findall(r'[A-Za-z]+|[℃℉]', header)
    allowed = {'C', 'F'} if temperature else set(_FACTORS)
    found = {_unit(token) for token in tokens if _unit(token) in allowed}
    return next(iter(found)) if len(found) == 1 else None


def _sourced_unit(item, *, temperature=False):
    header_unit = _header_unit(item['header'], temperature=temperature)
    supplied = item.get('unit')
    if supplied and header_unit and supplied != header_unit:
        raise ValueError('conflicting_header_unit')
    return header_unit or (supplied if item.get('unit_quote') else None)


def _parse(cell, item):
    field, unit = item['field'], item.get('unit')
    value = cell.strip()
    if value.lower() in _NULL:
        raise ValueError('missing_cell_value')
    if field in SHEET_METADATA:
        return _metadata(field,value), None
    if field in ('length_ft','height_m'):
        expected='ft' if field=='length_ft' else 'm'
        match=_VALUE.fullmatch(value)
        declared={t.lower() for t in re.findall(r'[A-Za-z]+',item['header']) if t.lower() in {'ft','m'}}
        actual=(match[2].lower() if match else '') or (next(iter(declared)) if len(declared)==1 else '')
        if (not match or actual!=expected or declared and declared!={expected}
                or unit and unit!=expected):
            raise ValueError('missing_or_invalid_unit')
        return float(Decimal(match[1].replace(',',''))),actual
    if field in _MASS:
        # A canonical unit suffix is source metadata, not a schema default.
        return _numeric(value, _sourced_unit(item))
    if field == 'reefer_setpoint_c' or field.endswith('.flashpoint_c'):
        return _numeric(value, _sourced_unit(item, temperature=True), temperature=True)
    leaf = field.rsplit('.', 1)[-1]
    if field in _BOOL or leaf in {'limited_quantity', 'marine_pollutant', 'ctu_closed'}:
        if field=='empty' and value.lower() in {'laden','loaded','full','empty'}:
            return value.lower()=='empty',None
        if value.lower() in {'yes', 'y', 'true', '1'}:
            return True, None
        if value.lower() in {'no', 'n', 'false', '0'}:
            return False, None
        raise ValueError('invalid_boolean_cell')
    if field in {'dg', 'oog'}:
        if value.lower() in {'none', 'nil', 'no', 'n'}:
            return None, None
        if field == 'dg' and value.lower() in {'yes', 'y'}:
            return True, None  # Emitted as dg_declared, never a DG object.
        raise ValueError('structured_cell_not_supported')
    if field == 'special_stowage':
        # Send these words to the reader; do not turn them into a rule here.
        return value, None
    if leaf == 'un_number':
        match = re.fullmatch(r'(?:UN\s*)?(\d{4})', value, re.I)
        if not match:
            raise ValueError('invalid_un_cell')
        return match[1], None
    if field in {'id', 'iso_type', 'pol', 'pod'} or leaf == 'packing_group':
        return value.upper(), None
    return value, None


def _metadata(field,value):
    """Noncargo cells still need narrow, independent value checks."""
    if field=='declaration_method' and value in {'1','2'}:
        return value
    if field=='declaration_date':
        iso=re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})',value)
        dmy=re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',value)
        if iso or dmy:
            year,month,day=map(int,iso.groups() if iso else reversed(dmy.groups()))
            year=year-543 if year>=2400 else year
            try:
                if not 1900<=year<=2200:
                    raise ValueError
                date(year,month,day)
                return value
            except ValueError:
                pass
    if field=='signatory':
        parts=value.split()
        letters=[part[:-1] if part.endswith('.') else part for part in parts]
        if (2<=len(parts)<=6 and all(part and any(c.isalpha() for c in part)
                and all(c.isalpha() or unicodedata.category(c).startswith('M') or c in "-'’" for c in part) for part in letters)
                and any(len(part)>=2 for part in letters)
                and not any(re.fullmatch(r'[A-Z]{5}',part) for part in letters)):
            return value
    if field in {'voyage','booking_reference'}:
        shape=r'[A-Za-z0-9][A-Za-z0-9./-]{2,23}'
        if (re.fullmatch(shape,value) and any(c.isdigit() for c in value)
                and any(c.isalpha() for c in value)
                and not re.fullmatch(r'[A-Za-z]{4}\d{7}|[2459][A-Za-z0-9][A-Za-z]\d',value)):
            if field=='voyage' or re.fullmatch(r'[A-Za-z]{2,8}[-/.][A-Za-z0-9./-]*\d[A-Za-z0-9./-]*',value):
                return value
    raise ValueError('invalid_metadata_cell')


def _a1(column, row):
    letters = ''
    while column:
        column, digit = divmod(column - 1, 26)
        letters = chr(65 + digit) + letters
    return f'{letters}{row}'


def parse_sheet(document, mapping=None):
    """Return strict cell observations and exceptions, never model cell values."""
    result = {'observations': [], 'issues': [], 'mapping': None,
              'baseline': BASELINE if mapping is None else None}
    try:
        mapping = baseline_mapping(document) if mapping is None else mapping
        if mapping is None:
            result['issues'].append(_issue('unmapped_sheet_headers', document=document.get('id')))
            return result
        checked, issues = validate_mapping(document, mapping)
        result['issues'].extend(issues)
        if checked is None:
            return result
        result['mapping'] = checked
        id_column = next(item['column'] for item in checked['columns'] if item['field'] == 'id')
        seen_ids = set()
        for row, cells in _records(document):
            if row <= checked['header_row'] or not any(cell.strip() for cell in cells):
                continue
            row_id = cells[id_column - 1].strip().upper() if len(cells) >= id_column else ''
            if not row_id:
                result['issues'].append(_issue('missing_row_id', document=document['id'], csv_row=row))
                continue
            if row_id in seen_ids:
                result['issues'].append(_issue('duplicate_id', document=document['id'], row_id=row_id, csv_row=row))
            seen_ids.add(row_id)
            by_field = {}
            for item in checked['columns']:
                column, field = item['column'], item['field']
                cell = cells[column - 1] if len(cells) >= column else ''
                if not cell.strip() and (field in _OPTIONAL_BLANK or field.startswith('dg.')):
                    continue
                try:
                    value, unit = _parse(cell, item)
                except ValueError as exc:
                    result['issues'].append(_issue(str(exc), document=document['id'], row_id=row_id,
                        field=field, csv_row=row, csv_column=column, cell=_a1(column, row), candidates=[cell]))
                    continue
                emitted_field = 'dg_declared' if field == 'dg' and value is True else field
                observation = {'row_id': row_id, 'field': emitted_field, 'value': value,
                    'document': document['id'], 'quote': cell, 'source_kind': 'sheet',
                    'csv_row': row, 'csv_column': column, 'row': row, 'column': column,
                    'cell': _a1(column, row), 'header': item['header'], 'cell_value': cell,
                    'normalized_value': value}
                if unit:
                    observation['unit'] = unit
                    if unit in {'C', 'F'}:
                        observation['temperature_unit'] = unit
                result['observations'].append(observation)
                by_field.setdefault(emitted_field, []).append(observation)
            for field, observations in by_field.items():
                values = []
                for observation in observations:
                    if observation['value'] not in values:
                        values.append(observation['value'])
                if len(values) > 1:
                    result['issues'].append(_issue('conflict', document=document['id'], row_id=row_id,
                        field=field, candidates=values, sources=[item['cell'] for item in observations]))
    except (csv.Error, OSError, UnicodeError, ValueError, TypeError) as exc:
        result['issues'].append(_issue('invalid_csv', document=document.get('id'), detail=str(exc)))
    return result


def verify_sheet_observation(document, mapping, claim):
    """Match identity, meaning, value and exact physical cell together."""
    if not isinstance(claim, dict):
        return False
    keys = ('row_id', 'field', 'value', 'document', 'csv_row', 'csv_column', 'quote')
    if any(key not in claim for key in keys):
        return False
    return any(all(type(claim[key]) is type(item[key]) and claim[key] == item[key] for key in keys)
               for item in parse_sheet(document, mapping)['observations'])
