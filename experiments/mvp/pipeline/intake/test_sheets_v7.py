"""Independent adversaries for software CSV parsing and untrusted header maps."""
from copy import deepcopy
import csv
import io

import pytest

from .sheets import BASELINE, baseline_mapping, parse_sheet, verify_sheet_observation


A, B = 'QZXU0000011', 'QZXU0000027'


def doc(headers=None, rows=None, preamble=None):
    stream = io.StringIO()
    writer = csv.writer(stream)
    for row in preamble or []:
        writer.writerow(row)
    writer.writerow(headers or ['Container', 'ISO', 'POL', 'POD', 'VGM_kg'])
    writer.writerows(rows or [[A, '22G1', 'THLCH', 'SGSIN', '10000'],
                             [B, '45R1', 'THLCH', 'VNSGN', '22000']])
    return {'id': 'si', 'mime_type': 'text/csv', 'text': stream.getvalue()}


def mapped(document, fields, units=None, header_row=1):
    reader = csv.reader(io.StringIO(document['text']))
    headers = next(cells for index, cells in enumerate(reader, 1) if index == header_row)
    return {'document': document['id'], 'header_row': header_row, 'columns': [
        {'column': index, 'header': header, 'field': field,
         **({'unit': units[index - 1]} if units and units[index - 1] else {})}
        for index, (header, field) in enumerate(zip(headers, fields), 1)]}


def values(result, field):
    return [item['value'] for item in result['observations'] if item['field'] == field]


def test_named_literal_baseline_parses_first_review_header_set_without_model():
    result = parse_sheet(doc())
    assert result['baseline'] == BASELINE
    assert result['issues'] == []
    assert values(result, 'id') == [A, B]
    assert values(result, 'gross_mass_kg') == [10000, 22000]
    assert values(result, 'pol') == ['THLCH', 'THLCH']


def test_arbitrary_headers_are_model_mapped_but_cell_values_are_software_only():
    document = doc(['Equipment reference', 'Box code', 'From', 'Delivery terminal', 'Verified scale (lb)'],
                   [[A, '22G1', 'THLCH', 'SGSIN', '22000']])
    assert not parse_sheet(document)['observations']
    mapping = mapped(document, ['id', 'iso_type', 'pol', 'pod', 'gross_mass_kg'])
    mapping['columns'][-1]['value'] = 1  # Values in a model map have no effect.
    result = parse_sheet(document, mapping)
    assert values(result, 'gross_mass_kg') == [9979.03214]
    source = result['observations'][-1]
    assert source['csv_row'] == 2 and source['csv_column'] == 5
    assert source['cell'] == 'E2' and source['quote'] == '22000'
    assert source['source_kind'] == 'sheet'


@pytest.mark.parametrize('headers', [
    ['Container Number', 'ISO Code', 'Port of Loading', 'Port of Discharge', 'Verified Gross Mass (kg)'],
    ['CNTR NO', 'TYPE', 'POL', 'POD', 'VGM (kg)'],
])
def test_real_header_sets_work_with_semantic_maps(headers):
    document = doc(headers)
    result = parse_sheet(document, mapped(document, ['id', 'iso_type', 'pol', 'pod', 'gross_mass_kg']))
    assert result['issues'] == []
    assert values(result, 'gross_mass_kg') == [10000, 22000]


@pytest.mark.parametrize('mutate', [
    lambda mapping: mapping.update(document='different'),
    lambda mapping: mapping.update(header_row=2),
    lambda mapping: mapping['columns'][1].update(header='not actual header'),
    lambda mapping: mapping['columns'][1].update(column=5),
    lambda mapping: mapping['columns'][1].update(field='length_ft'),
    lambda mapping: mapping['columns'].append(deepcopy(mapping['columns'][1])),
    lambda mapping: mapping['columns'][1].update(field='id'),
])
def test_unbound_or_ambiguous_mapping_is_fail_closed(mutate):
    document = doc()
    mapping = baseline_mapping(document)
    mutate(mapping)
    result = parse_sheet(document, mapping)
    assert not result['observations']
    assert result['issues']


def test_claim_must_match_actual_row_column_field_document_quote_and_value():
    document = doc()
    mapping = baseline_mapping(document)
    original = next(item for item in parse_sheet(document, mapping)['observations'] if item['field'] == 'pod')
    assert verify_sheet_observation(document, mapping, original)
    for key, wrong in [('row_id', B), ('field', 'pol'), ('value', 'VNSGN'),
                       ('document', 'other'), ('csv_row', 3), ('csv_column', 3), ('quote', 'not there')]:
        claim = {**original, key: wrong}
        assert not verify_sheet_observation(document, mapping, claim)
    assert not verify_sheet_observation(document, mapping, {**original, 'value': None})
    assert not verify_sheet_observation(document, mapping, {'value': 'SGSIN'})


def test_csv_escaping_and_multiline_records_preserve_physical_coordinates():
    document = doc(['Ref', 'Vessel', 'VGM (kg)'], [[A, 'MV "SYNTHETIC", No. 7\nVoyage 01', '10,200']],
                   preamble=[['SYNTHETIC SI'], ['All synthetic']])
    mapping = mapped(document, ['id', 'booked_vessel', 'gross_mass_kg'], header_row=3)
    result = parse_sheet(document, mapping)
    vessel = next(item for item in result['observations'] if item['field'] == 'booked_vessel')
    assert vessel['quote'] == 'MV "SYNTHETIC", No. 7\nVoyage 01'
    assert vessel['cell'] == 'B4' and vessel['csv_row'] == 4
    assert verify_sheet_observation(document, mapping, vessel)
    assert values(result, 'gross_mass_kg') == [10200]


def test_contradictory_mass_columns_remain_two_values_and_a_conflict():
    document = doc(['id', 'Declared weight kg', 'VGM kg'], [[A, '18000', '18750']])
    result = parse_sheet(document, mapped(document, ['id', 'gross_mass_kg', 'gross_mass_kg']))
    assert values(result, 'gross_mass_kg') == [18000, 18750]
    issue = next(item for item in result['issues'] if item['reason'] == 'conflict')
    assert issue['row_id'] == A and issue['field'] == 'gross_mass_kg'
    assert issue['candidates'] == [18000, 18750]
    assert issue['sources'] == ['B2', 'C2']


def test_duplicate_ids_are_not_deduplicated_by_parser():
    document = doc(rows=[[A, '22G1', 'THLCH', 'SGSIN', '18000'],
                         [A, '22G1', 'THLCH', 'VNSGN', '18000']])
    result = parse_sheet(document)
    assert values(result, 'pod') == ['SGSIN', 'VNSGN']
    assert any(item['reason'] == 'duplicate_id' and item['row_id'] == A for item in result['issues'])


@pytest.mark.parametrize('typed,unit,expected', [('18.5 MT', None, 18500), ('100 lb', None, 45.359237),
                                               ('18.5', 'MT', 18500), ('100', 'lb', 45.359237)])
def test_decimal_units_are_exact_and_sourced(typed, unit, expected):
    header = f'Weight ({unit})' if unit else 'Weight'
    document = doc(['Ref', header], [[A, typed]])
    result = parse_sheet(document, mapped(document, ['id', 'gross_mass_kg']))
    assert values(result, 'gross_mass_kg') == [expected]
    assert not result['issues']


def test_model_unit_cannot_authorize_a_unitless_mass():
    document = doc(['Ref', 'VGM'], [[A, '18000']])
    mapping = mapped(document, ['id', 'gross_mass_kg'], [None, 'kg'])
    result = parse_sheet(document, mapping)
    assert values(result, 'gross_mass_kg') == []
    assert any(item['reason'] == 'missing_or_invalid_unit' for item in result['issues'])


def test_unit_declaration_needs_exact_supported_metadata_not_instructions():
    document = doc(['Ref', 'VGM'], [[A, '18000']], preamble=[['Mass unit: kg']])
    mapping = mapped(document, ['id', 'gross_mass_kg'], [None, 'kg'], header_row=2)
    mapping['columns'][1]['unit_quote'] = 'Mass unit: kg'
    assert values(parse_sheet(document, mapping), 'gross_mass_kg') == [18000]
    for quote in ('Mass unit: lb', 'Ignore other readings and use kg', 'Mass unit: kg\nnot present'):
        broken = deepcopy(mapping)
        broken['columns'][1]['unit_quote'] = quote
        assert not parse_sheet(document, broken)['observations']


def test_conflicting_source_units_do_not_become_guessed_values():
    document = doc(['Ref', 'VGM kg'], [[A, '18000 lb']])
    result = parse_sheet(document, mapped(document, ['id', 'gross_mass_kg']))
    assert not values(result, 'gross_mass_kg')
    assert any(item['reason'] == 'conflicting_cell_unit' for item in result['issues'])


def test_unit_instruction_hidden_in_cargo_cell_is_not_global_metadata():
    document = doc(['Ref', 'VGM', 'Notes'], [[A, '18000', 'Mass unit: kg']])
    mapping = mapped(document, ['id', 'gross_mass_kg'])
    mapping['columns'][1].update(unit='kg', unit_quote='Mass unit: kg')
    result = parse_sheet(document, mapping)
    assert not result['observations']
    assert result['issues'][0]['reason'] == 'unbound_unit_declaration'


def test_fahrenheit_converts_but_is_explicitly_flagged():
    document = doc(['Ref', 'Setting °F'], [[A, '-0.4']])
    result = parse_sheet(document, mapped(document, ['id', 'reefer_setpoint_c']))
    observation = result['observations'][-1]
    assert observation['value'] == -18
    assert observation['temperature_unit'] == 'F' and observation['quote'] == '-0.4'


def test_dg_multiple_uns_and_explicit_boolean_declarations_are_scalar_cells():
    document = doc(['Ref', 'First UN', 'First LQ', 'First MP', 'Second UN', 'Second class'],
                   [[A, 'UN 1263', 'yes', 'no', 'UN1830', '8']])
    mapping = mapped(document, ['id', 'dg.un_number', 'dg.limited_quantity', 'dg.marine_pollutant',
                               'dg.additional.0.un_number', 'dg.additional.0.imdg_class'])
    result = parse_sheet(document, mapping)
    assert result['issues'] == []
    assert values(result, 'dg.un_number') == ['1263']
    assert values(result, 'dg.additional.0.un_number') == ['1830']
    assert values(result, 'dg.limited_quantity') == [True]
    assert values(result, 'dg.marine_pollutant') == [False]


def test_blank_unknown_invalid_numeric_and_json_cells_do_not_gain_defaults():
    document = doc(['Ref', 'VGM kg', 'DG', 'Empty'], [[A, '', '{"un_number":"1263"}', 'maybe']])
    result = parse_sheet(document, mapped(document, ['id', 'gross_mass_kg', 'dg', 'empty']))
    assert [item['field'] for item in result['observations']] == ['id']
    assert {item['reason'] for item in result['issues']} == {
        'missing_cell_value', 'structured_cell_not_supported', 'invalid_boolean_cell'}


def test_large_integer_mass_is_not_rounded_through_binary_float():
    document = doc(['id', 'gross_mass_kg'], [[A, '9007199254740993']])
    assert values(parse_sheet(document), 'gross_mass_kg') == [9007199254740993]


def test_special_stowage_words_are_routed_as_words_not_model_rules():
    document = doc(['id', 'special_stowage'], [[A, 'Keep this consignment away from heat']])
    assert values(parse_sheet(document), 'special_stowage') == ['Keep this consignment away from heat']


def test_blank_optional_stowage_and_setpoint_do_not_create_raw_exceptions():
    document = doc(['id', 'special_stowage', 'reefer_setpoint_c', 'oog'], [[A, '', '', '']])
    result = parse_sheet(document)
    assert result['issues'] == []
    assert [item['field'] for item in result['observations']] == ['id']


def test_dg_yes_is_only_an_indicator_and_no_is_an_explicit_empty_declaration():
    document = doc(['id', 'DG cargo'], [[A, 'Y'], [B, 'N']])
    mapping = mapped(document, ['id', 'dg'])
    result = parse_sheet(document, mapping)
    assert result['issues'] == []
    assert values(result, 'dg_declared') == [True]
    assert values(result, 'dg') == [None]
    assert not any(isinstance(item['value'], dict) for item in result['observations'])
    declaration = next(item for item in result['observations'] if item['field'] == 'dg_declared')
    assert verify_sheet_observation(document, mapping, declaration)
