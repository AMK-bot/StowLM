"""CSV contradictions cannot disappear when the model selects one declaration."""
import csv
import io
import json
from pathlib import Path

import pytest

from .provenance import csv_declarations, csv_evidence
from .validation import validate_reading
from stowage.model.instance import Instance


ID = 'TEST0000001'


def sheet(headers, rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return {'id': 'sheet', 'kind': 'csv', 'text': stream.getvalue()}


@pytest.mark.parametrize('headers,rows,field,value,alternatives', [
    (['id', 'pod', 'pod'], [[ID, 'SGSIN', 'MYPKG']], 'pod', 'SGSIN', {'SGSIN', 'MYPKG'}),
    (['id', 'pod'], [[ID, 'SGSIN'], [ID, 'MYPKG']], 'pod', 'SGSIN', {'SGSIN', 'MYPKG'}),
    (['id', 'pod', 'Equipment declaration JSON'],
     [[ID, 'SGSIN', '{"pod":"MYPKG"}']], 'pod', 'SGSIN', {'SGSIN', 'MYPKG'}),
    (['id', 'Equipment declaration JSON', 'Equipment declaration JSON'],
     [[ID, '{"dg":{"un_number":"1203"}}', '{"dg":{"un_number":"1263"}}']],
     'dg.un_number', '1203', {'1203', '1263'}),
    (['id', 'reefer', 'reefer'], [[ID, 'true', 'false']], 'reefer', True, {True, False}),
    (['id', 'length_ft', 'length_ft'], [[ID, '20', '40']], 'length_ft', 20, {20, 40}),
    (['id', 'Equipment declaration JSON'], [[ID, '{"tare_kg":2300}'], [ID, '{"tare_kg":2400}']],
     'tare_kg', 2300, {2300, 2400}),
    (['id', 'gross_kg', 'Equipment declaration JSON'],
     [[ID, '10000', '{"gross_mass_kg":11000}']], 'gross_mass_kg', 10000, {10000, 11000}),
    (['id', 'gross_kg'], [[ID, '10000'], [ID, '11000']], 'gross_mass_kg', 10000, {10000, 11000}),
])
def test_all_declarations_are_compared_before_filtering_proposal(headers, rows, field, value, alternatives):
    evidence = csv_evidence(sheet(headers, rows), ID, field, value)
    assert evidence
    assert all(item.get('source_conflict') is True for item in evidence)
    assert all(set(item['conflicting_values']) == alternatives for item in evidence)


def test_annex_null_parent_conflicts_with_nested_declaration():
    document = sheet(['id', 'Equipment declaration JSON', 'Equipment declaration JSON'],
                     [[ID, '{"dg":null}', '{"dg":{"un_number":"1203"}}']])
    evidence = csv_evidence(document, ID, 'dg.un_number', '1203')
    assert evidence and evidence[0].get('source_conflict') is True
    assert csv_evidence(document, ID, 'dg.un_number', None) == []


def test_null_ancestor_is_not_equivalent_to_an_explicit_nested_null():
    document = sheet(['id', 'Equipment declaration JSON', 'Equipment declaration JSON'],
                     [[ID, '{"dg":null}', '{"dg":{"packing_group":null}}']])
    evidence = csv_evidence(document, ID, 'dg.packing_group', None)
    assert evidence and all(item.get('source_conflict') for item in evidence)


def test_unfiltered_declarations_include_the_losing_column_and_null_parent():
    document = sheet(['id', 'pod', 'pod'], [[ID, 'SGSIN', 'MYPKG']])
    declarations = csv_declarations(document, ID, 'pod')
    assert [item['normalized_value'] for item in declarations] == ['SGSIN', 'MYPKG']
    assert all(item['source_conflict'] for item in declarations)
    document = sheet(['id', 'Equipment declaration JSON', 'Equipment declaration JSON'],
                     [[ID, '{"dg":null}', '{"dg":{"un_number":"1203"}}']])
    declarations = csv_declarations(document, ID, 'dg.un_number')
    assert declarations[0]['ancestor_field'] == 'dg'
    assert all(item['source_conflict'] for item in declarations)


@pytest.mark.parametrize('field,first,second,value', [
    ('dg.un_number', '{"un_number":"1203"}', '{"dg":{"un_number":"1263"}}', '1203'),
    ('special_stowage', '["on_deck"]', '{"special_stowage":["under_deck"]}', ['on_deck']),
])
def test_structured_direct_columns_cannot_be_hidden_by_annex(field, first, second, value):
    document = sheet(['id', field.split('.')[0], 'Equipment declaration JSON'], [[ID, first, second]])
    evidence = csv_evidence(document, ID, field, value)
    assert evidence and evidence[0]['source_conflict'] is True


def test_normalized_equivalent_declarations_do_not_conflict():
    document = sheet(['id', 'pod', 'pod', 'Equipment declaration JSON'],
                     [[ID, 'sgsin', ' SGSIN ', '{"pod":"SGSIN"}']])
    evidence = csv_evidence(document, ID, 'pod', 'SGSIN')
    assert len(evidence) == 3
    assert not any(item.get('source_conflict') for item in evidence)


def test_unsupported_proposal_remains_unsupported_despite_other_conflicts():
    document = sheet(['id', 'pod', 'pod'], [[ID, 'SGSIN', 'MYPKG']])
    assert csv_evidence(document, ID, 'pod', 'VNSGN') == []


def test_conflicting_declarations_are_not_borrowed_from_another_identity():
    document = sheet(['id', 'pod'], [[ID, 'SGSIN'], ['TEST0000002', 'MYPKG']])
    evidence = csv_evidence(document, ID, 'pod', 'SGSIN')
    assert len(evidence) == 1 and not evidence[0].get('source_conflict')


def test_duplicate_json_keys_are_never_silently_overwritten():
    document = sheet(['id', 'Equipment declaration JSON'],
                     [[ID, '{"pod":"SGSIN","pod":"MYPKG"}']])
    assert csv_evidence(document, ID, 'pod', 'MYPKG') == []
    declarations = csv_declarations(document, ID, 'pod')
    assert declarations and all(item.get('source_conflict') for item in declarations)


def test_duplicate_json_keys_cannot_hide_behind_matching_direct_column():
    document = sheet(['id', 'pod', 'Equipment declaration JSON'],
                     [[ID, 'MYPKG', '{"pod":"SGSIN","pod":"MYPKG"}']])
    evidence = csv_evidence(document, ID, 'pod', 'MYPKG')
    assert evidence and all(item.get('source_conflict') for item in evidence)


def test_structured_direct_column_rejects_duplicate_json_keys_too():
    document = sheet(['id', 'dg', 'Equipment declaration JSON'],
                     [[ID, '{"un_number":"1203","un_number":"1263"}',
                       '{"dg":{"un_number":"1263"}}']])
    evidence = csv_evidence(document, ID, 'dg.un_number', '1263')
    assert len(evidence) == 1 and evidence[0].get('source_conflict') is True


@pytest.mark.parametrize('other', ['TEST0000002', ID])
def test_ambiguous_identity_columns_cannot_disappear_from_independent_scan(other):
    document = sheet(['id', 'id', 'pod'], [[ID, other, 'SGSIN']])
    declarations = csv_declarations(document, ID, 'pod')
    assert declarations and all(item.get('source_conflict') for item in declarations)
    assert csv_evidence(document, ID, 'pod', 'SGSIN') == []


def test_ambiguous_identity_with_unrelated_ids_is_not_a_declaration_for_requested_row():
    document = sheet(['id', 'id', 'pod'], [['TEST0000002', 'TEST0000003', 'SGSIN']])
    assert csv_declarations(document, ID, 'pod') == []


def test_annex_with_a_different_explicit_identity_does_not_supply_row_values():
    document = sheet(['id', 'Equipment declaration JSON'],
                     [[ID, '{"id":"TEST0000002","pod":"SGSIN"}']])
    assert csv_evidence(document, ID, 'pod', 'SGSIN') == []
    declarations = csv_declarations(document, ID, 'pod')
    assert declarations and all(item.get('source_conflict') for item in declarations)


def test_validator_blocks_chosen_port_when_losing_column_is_omitted():
    path = Path('experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines()[0]
    base = Instance.model_validate_json(Path(path).read_text())
    rid = base.load_list[0]
    document = sheet(['id', 'pod', 'pod'], [[rid, 'SGSIN', 'MYPKG']])
    raw = {'observations': [{'row_id': rid, 'field': 'pod', 'value': 'SGSIN',
                             'document': 'sheet', 'quote': 'SGSIN'}], 'duplicates': []}
    checked = validate_reading(raw, [document], base)
    index = sorted(base.load_list).index(rid)
    assert any(q['row'] == index and q['field'] == 'pod' and
               q['reason'] == 'cross_document_conflict' for q in checked['questions'])
    assert 'pod' not in checked['rows'][index]
    assert not any(source.get('accepted') for source in checked['field_sources']
                   if source['row_id'] == rid and source['field'] == 'pod')
