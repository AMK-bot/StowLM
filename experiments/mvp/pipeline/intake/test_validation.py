"""Offline adversarial checks for evidence and cargo gates."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.intake.validation import validate_reading
from stowage.model.instance import Instance
from stowage.model.cargo import DangerousGoods
from stowage.rules.imdg.dgl import dangerous_goods_list, entries_for_un
from stowage.conventions import make_container_id


@pytest.fixture
def case():
    path = Path('experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines()[0]
    base = Instance.model_validate_json(Path(path).read_text())
    # Rule-bearing special requests have a separate reader gate; these tests
    # isolate declared cargo facts, with explicit no-request source rows.
    for box in base.containers:
        box.special_stowage = []
    boxes = {box.id: box.model_dump(mode='json') for box in base.containers}
    rows = [boxes[cid] for cid in base.load_list]
    text = '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows)
    documents = [{'id': 'booking', 'kind': 'text', 'text': text}]
    raw = {'observations': [{'row_id': row['id'], 'field': field, 'value': value,
                             'document': 'booking', 'quote': json.dumps(row, ensure_ascii=False)}
                            for row in rows for field, value in row.items()], 'duplicates': []}
    return base, documents, raw


def _codes(checked):
    return {q['reason'] for q in checked['questions']}


def _change(case, field, value):
    base, docs, raw = deepcopy(case)
    rid = base.load_list[0]
    item = next(o for o in raw['observations'] if o['row_id'] == rid and o['field'] == field)
    item['value'] = value
    item['quote'] = json.dumps({'row_id': rid, 'fields': {field: value}})
    docs[0]['text'] += '\n' + item['quote']
    return base, docs, raw, rid


def test_complete_rows_are_reconstructed_without_questions(case):
    base, docs, raw = case
    checked = validate_reading(raw, docs, base)
    assert not checked['questions'], checked['findings']
    originals = {box.id: box.model_dump(mode='json') for box in base.containers}
    assert checked['rows'] == [originals[cid] for cid in sorted(base.load_list)]
    assert all(source['quote'] in docs[0]['text'] for source in checked['field_sources'])


def test_missing_vgm_is_not_copied_from_ground_truth(case):
    base, docs, raw = case
    rid = base.load_list[0]
    raw['observations'] = [o for o in raw['observations'] if (o['row_id'], o['field']) != (rid, 'gross_mass_kg')]
    checked = validate_reading(raw, docs, base)
    assert 'missing_vgm' in _codes(checked)
    assert 'gross_mass_kg' not in checked['rows'][0]


def test_conflict_never_picks_a_source(case):
    base, docs, raw = case
    observation = deepcopy(next(o for o in raw['observations'] if o['field'] == 'pod'))
    observation.update(value='SGSIN', document='forwarded', quote=json.dumps({'id': observation['row_id'], 'pod': 'SGSIN'}))
    raw['observations'].append(observation)
    docs.append({'id': 'forwarded', 'text': observation['quote']})
    checked = validate_reading(raw, docs, base)
    original = next(box.pod for box in base.containers if box.id == observation['row_id'])
    if original != 'SGSIN':
        assert 'cross_document_conflict' in _codes(checked)
        row = checked['rows'][sorted(base.load_list).index(observation['row_id'])]
        assert 'pod' not in row
        assert not any(s['accepted'] for s in checked['field_sources']
                       if s['row_id'] == observation['row_id'] and s['field'] == 'pod')


@pytest.mark.parametrize('field,value,reason', [
    ('gross_mass_kg', -2, 'invalid_field'),
    ('gross_mass_kg', 1_000_000, 'container_validation'),
    ('pod', 'USLAX', 'pod_not_on_rotation'),
    ('reefer', True, 'missing_reefer_setpoint'),
    ('id', 'MSCU0000000', 'invalid_field'),
])
def test_bad_values_block(case, field, value, reason):
    base, docs, raw, rid = _change(case, field, value)
    checked = validate_reading(raw, docs, base)
    assert reason in _codes(checked), checked['findings']


def test_unknown_un_and_class_are_gated(case):
    base, docs, raw, rid = _change(case, 'dg', {
        'un_number': '9999', 'imdg_class': '3', 'stowage_category': 'A',
    })
    checked = validate_reading(raw, docs, base)
    assert 'unknown_un' in _codes(checked)
    entry = next(e for e in dangerous_goods_list() if e.hazard_class == '3')
    base, docs, raw, rid = _change(case, 'dg', {
        'un_number': f'{entry.un_number:04d}', 'imdg_class': '8',
        'packing_group': entry.packing_group, 'stowage_category': entry.stowage_category,
    })
    checked = validate_reading(raw, docs, base)
    assert 'imdg_class_mismatch' in _codes(checked)


def test_fabricated_quote_blocks_even_plausible_value(case):
    base, docs, raw = case
    raw['observations'][0]['quote'] = 'not actually in this document'
    checked = validate_reading(raw, docs, base)
    assert 'unsupported_source' in _codes(checked)


def test_duplicate_csv_is_found_even_if_model_omits_it(case):
    base, docs, raw = case
    rid = base.load_list[0]
    docs.append({'id': 'sheet', 'text': f'Synthetic document\nCONTAINER,WEIGHT\n{rid},12000\n{rid},12000\n'})
    checked = validate_reading(raw, docs, base)
    assert 'duplicate_id' in _codes(checked)
    raw['duplicate_resolutions'] = [{'row_id': rid, 'document': '*', 'action': 'keep_one'}]
    assert 'duplicate_id' in _codes(validate_reading(raw, docs, base))
    assert 'duplicate_id' not in _codes(validate_reading(raw, docs, base, duplicate_resolutions=raw['duplicate_resolutions']))


def test_missing_row_is_detected(case):
    base, docs, raw = case
    rid = base.load_list[0]
    raw['observations'] = [o for o in raw['observations'] if o['row_id'] != rid]
    checked = validate_reading(raw, docs, base)
    assert 'missing_row' in _codes(checked)
    assert checked['rows'][0] == {'id': rid}


def test_image_provenance_explicitly_unverified(case):
    base, docs, raw = case
    docs[0] = {'id': 'booking', 'kind': 'image', 'path': 'synthetic-tally.png'}
    checked = validate_reading(raw, docs, base)
    assert 'unverified_transcription' in _codes(checked)
    assert not any(s['accepted'] for s in checked['field_sources'])
    assert {s['evidence'] for s in checked['field_sources']} == {'unverified_transcription'}


def test_null_dg_conflicts_with_nested_declaration(case):
    base, docs, raw = case
    observation = deepcopy(next(o for o in raw['observations'] if o['field'] == 'dg' and o['value'] is None))
    observation.update(field='dg.un_number', value='1203', quote=json.dumps({'id': observation['row_id'], 'dg': {'un_number': '1203'}}))
    raw['observations'].append(observation)
    docs[0]['text'] += '\n' + observation['quote']
    assert 'cross_document_conflict' in _codes(validate_reading(raw, docs, base))


def test_nested_schema_defaults_are_not_sourced_facts(case):
    base, docs, raw, rid = _change(case, 'dg', {
        'un_number': '1090', 'imdg_class': '3', 'packing_group': 'II', 'stowage_category': 'E',
    })
    checked = validate_reading(raw, docs, base)
    assert any(q['field'] == 'dg.limited_quantity' for q in checked['questions'])
    assert 'limited_quantity' not in checked['rows'][0]['dg']


def test_unreadable_dg_is_not_a_no_dg_declaration(case):
    base, docs, raw = case
    item = next(o for o in raw['observations'] if o['field'] == 'dg' and o['value'] is None)
    item['quote'] = 'DG unreadable, ask shipper'
    docs[0]['text'] += '\n' + item['quote']
    checked = validate_reading(raw, docs, base)
    assert 'unsupported_source' in _codes(checked)


def test_imdg_diagnosis_survives_missing_vgm(case):
    base, docs, raw, rid = _change(case, 'dg', DangerousGoods(
        un_number='9999', imdg_class='3', stowage_category='A').model_dump(mode='json'))
    raw['observations'] = [o for o in raw['observations'] if (o['row_id'], o['field']) != (rid, 'gross_mass_kg')]
    checked = validate_reading(raw, docs, base)
    assert {'unknown_un', 'missing_vgm'} <= _codes(checked)


def test_uncertain_observation_is_blocking(case):
    base, docs, raw = case
    raw['observations'][0]['uncertain'] = True
    assert 'uncertain_reading' in _codes(validate_reading(raw, docs, base))


def test_nested_required_field_missing_still_asks(case):
    base, docs, raw, rid = _change(case, 'dg', {'un_number': '1090'})
    checked = validate_reading(raw, docs, base)
    assert any(q['field'] == 'dg.imdg_class' for q in checked['questions'])
    assert any(q['field'] == 'dg.stowage_category' for q in checked['questions'])


@pytest.mark.parametrize('field,value,reason', [
    ('gross_mass_kg', 1_000_000, 'container_validation'),
    ('pod', 'USLAX', 'pod_not_on_rotation'),
    ('reefer', True, 'missing_reefer_setpoint'),
    ('height_m', 2.8, 'container_validation'),
    ('length_ft', 45, 'container_validation'),
    ('gross_mass_kg', 10, 'container_validation'),
    ('max_gross_mass_kg', None, 'missing_csc_rating'),
])
def test_each_independent_gate_survives_missing_unrelated_field(case, field, value, reason):
    base, docs, raw, rid = _change(case, field, value)
    raw['observations'] = [o for o in raw['observations']
                           if (o['row_id'], o['field']) != (rid, 'category')]
    checked = validate_reading(raw, docs, base)
    assert reason in _codes(checked), checked['findings']
    assert any(q['field'] == 'category' for q in checked['questions'])


@pytest.mark.parametrize('dg,reason', [
    ({'un_number': '9999'}, 'unknown_un'),
    ({'un_number': '1090', 'imdg_class': '8'}, 'imdg_class_mismatch'),
])
def test_imdg_checks_do_not_require_other_dg_attributes(case, dg, reason):
    base, docs, raw, rid = _change(case, 'dg', dg)
    raw['observations'] = [o for o in raw['observations']
                           if (o['row_id'], o['field']) != (rid, 'category')]
    assert reason in _codes(validate_reading(raw, docs, base))


def test_declared_per_length_ceiling_is_a_sourced_policy_not_inferred_csc(case):
    base, docs, raw = case
    rid = base.load_list[0]
    box = next(box for box in base.containers if box.id == rid)
    base.rule_overrides['intake.limits'] = {'max_gross_mass_kg_by_length': {str(box.length_ft): 1000}}
    raw['observations'] = [o for o in raw['observations']
                           if (o['row_id'], o['field']) != (rid, 'category')]
    checked = validate_reading(raw, docs, base)
    assert 'intake_mass_limit' in _codes(checked)
    policy = next(f for f in checked['findings'] if f['code'] == 'declared_intake_mass_policy')
    assert policy['source'].startswith('base_instance.rule_overrides')
    assert policy['limit_kg'] == 1000


def test_ship_mass_screen_survives_missing_rating_and_other_fields(case):
    base, docs, raw, rid = _change(case, 'gross_mass_kg', 10_000_000)
    raw['observations'] = [o for o in raw['observations'] if o['row_id'] != rid or
                           o['field'] not in ('iso_type', 'height_m', 'max_gross_mass_kg', 'category')]
    assert 'ship_capability' in _codes(validate_reading(raw, docs, base))


def test_duplicate_and_conflict_survive_missing_unrelated_fields(case):
    base, docs, raw = case
    rid = base.load_list[0]
    raw['observations'] = [o for o in raw['observations']
                           if (o['row_id'], o['field']) != (rid, 'category')]
    other = deepcopy(next(o for o in raw['observations'] if o['row_id'] == rid and o['field'] == 'pod'))
    other.update(value='USLAX', document='other', quote=json.dumps({'id': rid, 'pod': 'USLAX'}))
    raw['observations'].append(other)
    docs.append({'id': 'other', 'text': other['quote']})
    raw['duplicates'] = [{'row_id': rid, 'document': 'booking', 'quote': rid}]
    assert {'duplicate_id', 'cross_document_conflict'} <= _codes(validate_reading(raw, docs, base))


def test_identity_and_missing_vgm_survive_missing_unrelated_field(case):
    base, docs, raw, rid = _change(case, 'id', make_container_id('ZZZU', 999999))
    raw['observations'] = [o for o in raw['observations'] if o['row_id'] != rid or
                           o['field'] not in ('category', 'gross_mass_kg')]
    assert {'unknown_yard_id', 'row_identity_mismatch', 'missing_vgm'} <= _codes(validate_reading(raw, docs, base))


def test_csv_quote_from_wrong_row_cannot_support_value(case):
    base, docs, raw, rid = _change(case, 'gross_mass_kg', 12345)
    other = base.load_list[1]
    docs.append({'id': 'sheet', 'mime_type': 'text/csv',
                 'text': f'Container no.,EDI_VGM_kg\n{rid},12000\n{other},12345\n'})
    observation = next(o for o in raw['observations'] if o['row_id'] == rid and o['field'] == 'gross_mass_kg')
    observation.update(document='sheet', quote=f'{other},12345')
    checked = validate_reading(raw, docs, base)
    assert any(q['row'] == 0 and q['field'] == 'gross_mass_kg' and q['reason'] == 'unsupported_source'
               for q in checked['questions'])
    assert 'gross_mass_kg' not in checked['rows'][0]


def test_csv_decoded_json_cell_is_evidence_without_raw_escape_quote(case):
    base, docs, raw = case
    rid = base.load_list[0]
    item = next(o for o in raw['observations'] if o['row_id'] == rid and o['field'] == 'empty')
    docs.append({'id': 'sheet', 'mime_type': 'text/csv',
                 'text': f'Container no.,Equipment declaration JSON\n{rid},"{{""empty"":false}}"\n'})
    item.update(document='sheet', quote='"empty":false')
    checked = validate_reading(raw, docs, base)
    assert not any(q['row'] == 0 and q['field'] == 'empty' for q in checked['questions'])
    source = next(s for s in checked['field_sources'] if s['document'] == 'sheet')
    assert source['evidence'] == 'csv_cell'
    assert source['cell'] == 'B2' and source['json_path'] == 'empty'
    assert source['csv_row'] == 2 and source['row'] == 0


def test_same_csv_rounded_mass_reconciles_to_declared_exact_edi(case):
    base, docs, raw, rid = _change(case, 'gross_mass_kg', 12000.12345)
    docs.append({'id': 'sheet', 'mime_type': 'text/csv',
                 'text': f'Container no.,Gross as typed,EDI_VGM_kg\n{rid},12000.123 kg,12000.12345\n'})
    item = next(o for o in raw['observations'] if o['row_id'] == rid and o['field'] == 'gross_mass_kg')
    item.update(document='sheet', quote='any model quote')
    raw['observations'].append({**item, 'value': 12000.123})
    checked = validate_reading(raw, docs, base)
    assert not any(q['row'] == 0 and q['field'] == 'gross_mass_kg' for q in checked['questions'])
    assert checked['rows'][0]['gross_mass_kg'] == 12000.12345
    assert all(s.get('cell') for s in checked['field_sources'] if s['document'] == 'sheet')


def test_same_csv_inconsistent_mass_cells_require_answer(case):
    base, docs, raw, rid = _change(case, 'gross_mass_kg', 12000)
    docs.append({'id': 'sheet', 'mime_type': 'text/csv',
                 'text': f'Container no.,Gross as typed,EDI_VGM_kg\n{rid},13000 kg,12000\n'})
    item = next(o for o in raw['observations'] if o['row_id'] == rid and o['field'] == 'gross_mass_kg')
    item.update(document='sheet', quote='12000')
    checked = validate_reading(raw, docs, base)
    assert any(q['row'] == 0 and q['field'] == 'gross_mass_kg' and q['reason'] == 'cross_document_conflict'
               for q in checked['questions'])


def test_mass_without_rating_or_declared_policy_is_not_marked_safety_checked(case):
    base, docs, raw, rid = _change(case, 'max_gross_mass_kg', None)
    checked = validate_reading(raw, docs, base)
    assert any(q['row'] == 0 and q['field'] == 'gross_mass_kg' and q['reason'] == 'missing_mass_limit'
               for q in checked['questions'])
    assert not any(source['accepted'] for source in checked['field_sources']
                   if source['row'] == 0 and source['field'] == 'gross_mass_kg')
    box = next(box for box in base.containers if box.id == rid)
    base.rule_overrides['intake.limits'] = {'max_gross_mass_kg_by_length': {str(box.length_ft): 100000}}
    assert not any(q['row'] == 0 and q['reason'] == 'missing_mass_limit'
                   for q in validate_reading(raw, docs, base)['questions'])


def test_unknown_un_blocks_complete_false_dg_declaration(case):
    # Synthetic development case: the selected source box is non-DG, but the
    # model proposes a false DG object with plausible class/category defaults.
    original = next(box for box in case[0].containers if box.id == case[0].load_list[0])
    assert original.dg is None
    proposed = DangerousGoods(un_number='9999', imdg_class='3', stowage_category='A').model_dump(mode='json')
    base, docs, raw, rid = _change(case, 'dg', proposed)
    checked = validate_reading(raw, docs, base)
    question = next(q for q in checked['questions'] if q['reason'] == 'unknown_un')
    assert question['field'] == 'dg'
    dg_sources = [s for s in checked['field_sources'] if s['row_id'] == rid and s['field'].startswith('dg.')]
    assert dg_sources and not any(s['accepted'] for s in dg_sources)
    assert any(s['field'] == 'dg.imdg_class' for s in dg_sources)
    assert any(s['field'] == 'dg.stowage_category' for s in dg_sources)


def test_unknown_additional_un_blocks_corresponding_entry(case):
    entry = next(e for e in dangerous_goods_list() if e.hazard_class == '3')
    proposed = DangerousGoods(un_number=f'{entry.un_number:04d}', imdg_class='3',
                              packing_group=entry.packing_group, stowage_category=entry.stowage_category,
                              additional=[DangerousGoods(un_number='9999', imdg_class='8', stowage_category='A')])
    base, docs, raw, rid = _change(case, 'dg', proposed.model_dump(mode='json'))
    checked = validate_reading(raw, docs, base)
    assert any(q['field'] == 'dg.additional.0' and q['reason'] == 'unknown_un' for q in checked['questions'])
    source = next(s for s in checked['field_sources'] if s['row_id'] == rid and s['field'] == 'dg.additional')
    assert not source['accepted']
    primary = next(s for s in checked['field_sources'] if s['row_id'] == rid and s['field'] == 'dg.imdg_class')
    assert primary['accepted']


@pytest.mark.parametrize('uncertain', [False, True])
def test_missing_or_uncertain_un_blocks_all_dependent_dg_facts(case, uncertain):
    proposed = DangerousGoods(un_number='1090', imdg_class='3', packing_group='II',
                              stowage_category='E').model_dump(mode='json')
    if not uncertain:
        proposed.pop('un_number')
    base, docs, raw, rid = _change(case, 'dg', proposed)
    if uncertain:
        item = next(o for o in raw['observations'] if o['row_id'] == rid and o['field'] == 'dg')
        raw['observations'].remove(item)
        raw['observations'].extend({**item, 'field': f'dg.{key}', 'value': value,
                                    'uncertain': key == 'un_number'} for key, value in proposed.items())
    checked = validate_reading(raw, docs, base)
    assert any(q['field'] == 'dg' and q['reason'] == 'missing_imdg_identity' for q in checked['questions'])
    assert not any(source['accepted'] for source in checked['field_sources']
                   if source['row_id'] == rid and source['field'].startswith('dg.'))


@pytest.mark.parametrize('packing_group', ['absent', None, 'III'])
def test_unresolved_required_packing_group_blocks_dependent_facts(case, packing_group):
    entry = next(e for e in dangerous_goods_list() if e.hazard_class == '3' and
                 e.packing_group == 'II' and len(entries_for_un(e.un_number)) == 1)
    proposed = DangerousGoods(un_number=f'{entry.un_number:04d}', imdg_class=entry.hazard_class,
                              packing_group=entry.packing_group,
                              stowage_category=entry.stowage_category).model_dump(mode='json')
    if packing_group == 'absent':
        proposed.pop('packing_group')
    else:
        proposed['packing_group'] = packing_group
    base, docs, raw, rid = _change(case, 'dg', proposed)
    checked = validate_reading(raw, docs, base)
    assert any(q['field'] == 'dg' and q['reason'] == 'unresolved_imdg_entry' for q in checked['questions'])
    assert not any(s['accepted'] for s in checked['field_sources']
                   if s['row_id'] == rid and s['field'].startswith('dg.'))


def test_explicit_none_packing_group_resolves_single_no_pg_entry(case):
    entry = next(e for e in dangerous_goods_list() if not e.hazard_class.startswith('1.') and
                 e.packing_group is None and len(entries_for_un(e.un_number)) == 1)
    proposed = DangerousGoods(un_number=f'{entry.un_number:04d}', imdg_class=entry.hazard_class,
                              packing_group=None, stowage_category=entry.stowage_category).model_dump(mode='json')
    base, docs, raw, rid = _change(case, 'dg', proposed)
    checked = validate_reading(raw, docs, base)
    assert not any(q['row'] == 0 and q['reason'] in ('unknown_un', 'missing_imdg_identity', 'unresolved_imdg_entry')
                   for q in checked['questions'])


def test_ambiguous_un_without_packing_group_blocks_entry(case):
    entry = next(e for e in dangerous_goods_list() if e.hazard_class == '3' and
                 len(entries_for_un(e.un_number)) > 1)
    proposed = {'un_number': f'{entry.un_number:04d}', 'imdg_class': entry.hazard_class,
                'stowage_category': entry.stowage_category}
    base, docs, raw, rid = _change(case, 'dg', proposed)
    checked = validate_reading(raw, docs, base)
    assert any(q['field'] == 'dg' and q['reason'] == 'unresolved_imdg_entry' for q in checked['questions'])
