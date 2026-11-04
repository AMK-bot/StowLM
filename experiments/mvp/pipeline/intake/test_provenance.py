"""Independent synthetic adversaries for strict CSV field/cell binding."""
import csv
from decimal import Decimal
import io
import json

import pytest

from .provenance import csv_evidence, is_csv_document


A = 'TEST0000001'
B = 'TEST0000002'


def document(rows=None, *, header=None):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['SYNTHETIC DOCUMENT / EXPORT', '', '', '', '', '', ''])
    writer.writerow(['Booking >>>', '', 'Mass units >>>'])
    writer.writerow(header or ['Container no.', 'ISO / size', 'POL', 'POD (blank = tally)',
                                'Gross as typed', 'EDI_VGM_kg', 'Equipment declaration JSON'])
    for row in rows or [[A, '22G1', 'THLCH', 'SGSIN', '22046.226 lb', '10000',
                         json.dumps({'length_ft': 20, 'tare_kg': 2300, 'dg': None,
                                     'oog': None, 'empty': False, 'category': 'VNSGN',
                                     'special_stowage': [], 'reefer_setpoint_c': None})],
                        [B, '45G1', 'THLCH', 'VNSGN', '19.500 MT', '19500',
                         json.dumps({'length_ft': 40, 'tare_kg': 3900, 'dg': {'un_number': '1203',
                                     'imdg_class': '3', 'packing_group': 'II',
                                     'additional': [{'un_number': '1263', 'packing_group': None}]}})]]:
        writer.writerow(row)
    return {'id': 'gate-sheet', 'mime_type': 'text/csv', 'text': stream.getvalue()}


def test_cell_coordinates_headers_and_exact_edi():
    evidence = csv_evidence(document(), A, 'gross_mass_kg', 10000)
    assert {item['cell'] for item in evidence} == {'E4', 'F4'}
    assert all(item['document'] == 'gate-sheet' and item['row'] == 4 for item in evidence)
    assert {item['column'] for item in evidence} == {5, 6}
    assert {item['header'] for item in evidence} == {'Gross as typed', 'EDI_VGM_kg'}
    assert all(item['normalized_value'] == 10000 for item in evidence)
    assert not any(item.get('source_conflict') for item in evidence)


def test_rounded_display_conversion_normalizes_only_to_corresponding_edi():
    center = float(Decimal('22046.226') * Decimal('0.45359237'))
    evidence = csv_evidence(document(), A, 'gross_mass_kg', center)
    assert [item['cell'] for item in evidence] == ['E4']
    assert evidence[0]['normalized_value'] == 10000
    assert csv_evidence(document(), A, 'gross_mass_kg', 10000.00001) == []
    assert csv_evidence(document(), B, 'gross_mass_kg', 10000) == []


def test_units_without_edi_require_exact_deterministic_conversion():
    doc = document([[A, '22G1', 'THLCH', 'SGSIN', '100.50 lb', '', '{}']])
    exact = float(Decimal('100.50') * Decimal('0.45359237'))
    evidence = csv_evidence(doc, A, 'gross_mass_kg', exact)
    assert len(evidence) == 1
    assert evidence[0]['normalized_value'] == exact
    assert csv_evidence(doc, A, 'gross_mass_kg', exact + 1e-9) == []


@pytest.mark.parametrize('text,exact', [('20 MT', 20000), ('1.250 tonnes', 1250), ('18.5 kg', 18.5)])
def test_explicit_weight_units(text, exact):
    doc = document([[A, '22G1', 'THLCH', 'SGSIN', text, '', '{}']])
    assert csv_evidence(doc, A, 'gross_mass_kg', exact)[0]['normalized_value'] == exact


def test_inconsistent_display_and_edi_are_not_reconciled():
    doc = document([[A, '22G1', 'THLCH', 'SGSIN', '10.000 MT', '10001', '{}']])
    exact = csv_evidence(doc, A, 'gross_mass_kg', 10001)
    displayed = csv_evidence(doc, A, 'gross_mass_kg', 10000)
    assert exact[0]['source_conflict'] is True
    assert displayed[0]['source_conflict'] is True
    assert displayed[0]['normalized_value'] == 10000
    assert exact[0]['normalized_value'] == 10001
    assert set(exact[0]['conflicting_values']) == {10000, 10001}


def test_precision_interval_is_based_on_last_display_digit():
    at_boundary = document([[A, '22G1', 'THLCH', 'SGSIN', '10.000 MT', '10000.5', '{}']])
    outside = document([[A, '22G1', 'THLCH', 'SGSIN', '10.000 MT', '10000.500001', '{}']])
    assert csv_evidence(at_boundary, A, 'gross_mass_kg', 10000)[0]['normalized_value'] == 10000.5
    assert csv_evidence(outside, A, 'gross_mass_kg', 10000)[0]['source_conflict'] is True


def test_wrong_row_column_and_same_value_elsewhere_rejected():
    doc = document()
    assert csv_evidence(doc, A, 'pod', 'VNSGN') == []  # category and other row do not establish POD
    assert csv_evidence(doc, A, 'iso_type', 'THLCH') == []
    assert csv_evidence(doc, 'TEST0000009', 'pod', 'SGSIN') == []
    assert csv_evidence(doc, B, 'tare_kg', 2300) == []
    assert csv_evidence(doc, A, 'height_m', 20) == []  # length value is not a height declaration
    assert csv_evidence(doc, A, 'id', B) == []


def test_json_csv_escapes_and_nested_paths_are_decoded():
    doc = document()
    assert '""un_number""' in doc['text']
    evidence = csv_evidence(doc, B, 'dg.un_number', '1203')
    assert len(evidence) == 1
    assert evidence[0]['cell'] == 'G5'
    assert evidence[0]['json_path'] == 'dg.un_number'
    assert evidence[0]['value'] == '1203'
    assert csv_evidence(doc, B, 'dg.additional.0.un_number', '1263')
    assert csv_evidence(doc, B, 'dg.additional.0.packing_group', None)
    assert csv_evidence(doc, B, 'dg.additional.1.un_number', '1263') == []
    assert csv_evidence(doc, B, 'dg.un_number', '1263') == []


def test_explicit_null_empty_list_false_and_missing_are_distinct():
    doc = document()
    assert csv_evidence(doc, A, 'dg', None)
    assert csv_evidence(doc, A, 'reefer_setpoint_c', None)
    assert csv_evidence(doc, A, 'special_stowage', [])
    assert csv_evidence(doc, A, 'empty', False)
    assert csv_evidence(doc, A, 'category', None) == []
    assert csv_evidence(doc, B, 'reefer_setpoint_c', None) == []
    assert csv_evidence(doc, B, 'dg', None) == []
    assert csv_evidence(doc, A, 'empty', 0) == []
    assert csv_evidence(doc, A, 'special_stowage', None) == []


def test_whole_object_requires_all_actual_keys_and_values():
    doc = document()
    assert csv_evidence(doc, B, 'dg', {'un_number': '1203'}) == []
    source = {'un_number': '1203', 'imdg_class': '3', 'packing_group': 'II',
              'additional': [{'un_number': '1263', 'packing_group': None}]}
    assert csv_evidence(doc, B, 'dg', source)
    source['imdg_class'] = '8'
    assert csv_evidence(doc, B, 'dg', source) == []


def test_blank_and_unknown_mass_are_not_null_or_zero_declarations():
    for cell in ('', 'VGM TBA', 'unknown', '???', '100 unknown-unit'):
        doc = document([[A, '22G1', 'THLCH', '', cell, '', '{}']])
        assert csv_evidence(doc, A, 'gross_mass_kg', None) == []
        assert csv_evidence(doc, A, 'gross_mass_kg', 0) == []
        assert csv_evidence(doc, A, 'pod', None) == []


def test_kg_header_does_not_silently_override_conflicting_cell_unit():
    doc = document([[A, '22G1', 'THLCH', 'SGSIN', '', '10 MT', '{}']])
    assert csv_evidence(doc, A, 'gross_mass_kg', 10000) == []
    assert csv_evidence(doc, A, 'gross_mass_kg', 10) == []


def test_document_id_required_and_non_csv_never_matches():
    doc = document()
    doc.pop('id')
    assert csv_evidence(doc, A, 'pod', 'SGSIN') == []
    prose = {'id': 'email', 'text': f'{A}: POD SGSIN; VGM 10000 kg'}
    assert not is_csv_document(prose)
    assert csv_evidence(prose, A, 'gross_mass_kg', 10000) == []
    assert is_csv_document(document())
    assert is_csv_document({'id': 'blank', 'kind': 'csv', 'text': ''})


def test_duplicate_rows_preserve_distinct_cell_locations():
    row = [A, '22G1', 'THLCH', 'SGSIN', '10.000 MT', '10000', '{}']
    evidence = csv_evidence(document([row, row]), A, 'pod', 'SGSIN')
    assert [item['cell'] for item in evidence] == ['D4', 'D5']


def test_direct_field_columns_normalize_declared_scalars():
    doc = document([[A, '20', 'true', '2300.00']], header=['id', 'length_ft', 'reefer', 'tare_kg'])
    assert csv_evidence(doc, A, 'length_ft', 20)
    assert csv_evidence(doc, A, 'reefer', True)
    assert csv_evidence(doc, A, 'tare_kg', 2300)
    assert csv_evidence(doc, A, 'reefer', False) == []


def test_large_integer_json_is_not_satisfied_by_rounded_float():
    integer = 123456789012345678901234567890123456789
    doc = document([[A, '22G1', 'THLCH', 'SGSIN', '', '', json.dumps({'dg': {'shipment_provenance': {'seed': integer}}})]])
    assert csv_evidence(doc, A, 'dg.shipment_provenance.seed', integer)
    assert csv_evidence(doc, A, 'dg.shipment_provenance.seed', float(integer)) == []


def test_large_integer_source_is_not_satisfied_by_adjacent_integer():
    integer = 2 ** 53 + 1
    doc = document([[A, '22G1', 'THLCH', 'SGSIN', '', str(integer),
                     json.dumps({'dg': {'shipment_provenance': {'seed': integer}}})]])
    assert csv_evidence(doc, A, 'dg.shipment_provenance.seed', integer - 1) == []
    assert csv_evidence(doc, A, 'gross_mass_kg', integer - 1) == []
    assert csv_evidence(doc, A, 'gross_mass_kg', integer)


def test_multiline_cells_keep_physical_line_coordinates():
    doc = document([[A, '22G1', 'THLCH', 'SGSIN', '10.000 MT', '10000', json.dumps({'category': 'line one\nline two'})]])
    assert csv_evidence(doc, A, 'category', 'line one\nline two')[0]['cell'] == 'G4'
