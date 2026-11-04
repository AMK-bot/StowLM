"""Migration tests use fabricated recordings in temporary directories only."""
from copy import deepcopy
import json

import pytest

from experiments.mvp.pipeline.contract.schema import StageRecord, chain_intact, sha256_json
from . import api, summary
from .cache import SCHEMA, _binding, _body, _save_index, _write, load_cached
from .normalize_summaries import _legacy_summary, normalize
from .render import render

EN = {'sentences': [dict(before='This comparison is summarized here', slot_id='mass', after=''),
                    dict(before='The placement context is shown here', slot_id='rank', after='')]}
TH = {'sentences': [dict(before='ข้อมูลเปรียบเทียบที่แสดงคือ', slot_id='mass', after=''),
                    dict(before='ข้อมูลตำแหน่งที่แสดงคือ', slot_id='rank', after='')]}


@pytest.fixture
def recording(tmp_path, monkeypatch):
    original = summary.validate_summary

    def punctuation(spec, slots, language, **kwargs):
        result = original(spec, slots, language, **kwargs)
        pieces = []
        for row in result['spec']['sentences']:
            before = row['before']
            if before and not before.endswith((':', ',', ';')):
                before += ':'
            pieces.append(' '.join(s for s in (before, slots[row['slot_id']][language], row['after']) if s) + '.')
        result['text'] = ' '.join(pieces)
        return result

    monkeypatch.setattr(summary, 'validate_summary', punctuation)
    models = ({'id': 'fake-instance'}, {'id': 'fake-plan'}, {'id': 'fake-report'})
    menu = [dict(id='mass', text='mass 12.5 kg; limit 20 kg.', th='มวล 12.5 kg; ขีดจำกัด 20 kg.',
                 evidence=[dict(source='instance', path='/mass', value=12.5, sha256='a'*64)]),
            dict(id='rank', text='weight rank 2 of 6 in BAY01.', th='อันดับน้ำหนัก 2 จาก 6 ใน BAY01.',
                 evidence=[dict(source='report', path='/rank', value=2, sha256='b'*64)])]
    fresh = deepcopy(menu)
    monkeypatch.setattr(api, 'inputs', lambda *args: args)
    monkeypatch.setattr(api, 'statement_menu', lambda *args: deepcopy(fresh))
    slots = summary.slots_for(menu)
    index = dict(schema=SCHEMA, binding=_binding(*models), entries={'BOX01': {}}, records=[])
    for language, spec in (('en', EN), ('th', TH)):
        rows, audit = render(menu, language)
        old = _legacy_summary(spec, slots, language)
        request = 'fabricated-test-request-' + language
        audit.update(source='spark-summary', model='fake-test-model',
                     summary=dict(source='spark-summary', accepted=True, model='fake-test-model',
                                  calls=[dict(status='received', request_id=request)]))
        calls = [dict(purpose='pipeline-box-summary-' + language, request_id=request,
                      status='measured', response_text=json.dumps(spec), cost_usd='0')]
        body, statements = _body(rows, old, recorded=True)
        payload = dict(binding=index['binding'], box_id='BOX01', language=language, audit=audit,
                       calls=calls, alternatives=None, summary=old,
                       explanation=dict(language=language, mode='recorded', text=body, statements=statements))
        digest = sha256_json(payload)
        _write(tmp_path / (digest + '.json'), payload)
        index['entries']['BOX01'][language] = dict(path=digest + '.json', sha256=digest, audit=audit)
    _save_index(tmp_path / 'index.json', index)
    return tmp_path, models, fresh, deepcopy(index)


def _payload(directory, language):
    index = json.loads((directory / 'index.json').read_text())
    entry = index['entries']['BOX01'][language]
    return json.loads((directory / entry['path']).read_text())


def _edit_payload(directory, language, mutate):
    index = json.loads((directory / 'index.json').read_text())
    payload = _payload(directory, language)
    mutate(payload)
    digest = sha256_json(payload)
    _write(directory / (digest + '.json'), payload)
    index['entries']['BOX01'][language] = dict(path=digest + '.json', sha256=digest, audit=payload['audit'])
    _save_index(directory / 'index.json', index)


def test_dry_run_has_no_writes_or_calls(recording):
    directory, models, _, _ = recording
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = normalize(directory, *models)
    assert result['dry_run'] and result['changed'] == 2
    assert result['cost_usd'] == 0 and result['model_called'] is False
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_apply_only_code_punctuation_preserves_provenance_and_history(recording):
    directory, models, _, old_index = recording
    old = _payload(directory, 'en')
    result = normalize(directory, *models, apply=True)
    new = _payload(directory, 'en')
    assert result['changed'] == 2
    assert 'summarized here: mass 12.5 kg; limit 20 kg' in new['summary']['text']
    assert new['summary']['spec'] == old['summary']['spec']
    assert new['calls'] == old['calls']
    assert new['audit']['model'] == old['audit']['model']
    assert new['audit']['summary']['accepted'] is True
    assert new['explanation']['mode'] == 'recorded'
    assert new['audit']['summary_render_history'][0]['previous_payload_sha256'] == old_index['entries']['BOX01']['en']['sha256']
    assert (directory / old_index['entries']['BOX01']['en']['path']).exists()
    records = [StageRecord.model_validate(r) for r in json.loads((directory / 'index.json').read_text())['records']]
    assert chain_intact(records) and records[-1].mode.value == 'live'
    assert records[-1].model is None and records[-1].cost_usd == 0
    assert load_cached(*models, 'BOX01', out_dir=directory).mode.value == 'recorded'
    assert normalize(directory, *models, apply=True)['changed'] == 0


def test_thai_template_only_change_rebuilds_slots_and_keeps_english_facts(recording):
    directory, models, fresh, _ = recording
    old = _payload(directory, 'en')
    _edit_payload(directory, 'th', lambda p: p['audit']['checks']['thai_sense'].update(status='passed'))
    fresh[0]['th'] = 'น้ำหนัก 12.5 kg; ขีดจำกัด 20 kg.'
    result = normalize(directory, *models, apply=True)
    assert result['changed'] == 2
    en, th = _payload(directory, 'en'), _payload(directory, 'th')
    assert en['summary']['spec'] == old['summary']['spec']
    assert en['summary']['slots_sha256'] != old['summary']['slots_sha256']
    assert 'น้ำหนัก 12.5 kg' in th['explanation']['text']
    assert en['explanation']['statements'][1]['text'] == old['explanation']['statements'][1]['text']
    assert load_cached(*models, 'BOX01', language='th', out_dir=directory).mode.value == 'recorded'
    assert th['audit']['checks']['thai_sense']['status'] == 'not_run'
    assert th['audit']['prior_language_checks'][-1]['status'] == 'passed'


def test_summary_clause_can_include_more_already_sourced_values(recording):
    directory, models, fresh, _ = recording
    fresh[0].update(summary_en='mass 12.5 kg; limit 20 kg', summary_th='มวล 12.5 kg; ขีดจำกัด 20 kg')
    assert normalize(directory, *models, apply=True)['changed'] == 2
    assert load_cached(*models, 'BOX01', out_dir=directory).mode.value == 'recorded'


def test_summary_clause_cannot_add_an_unsourced_value(recording):
    directory, models, fresh, _ = recording
    fresh[0].update(summary_en='mass 999 kg', summary_th='มวล 999 kg')
    with pytest.raises(ValueError, match='absent from its full source fact'):
        normalize(directory, *models, apply=True)


@pytest.mark.parametrize('change', ['number', 'evidence'])
def test_changed_english_value_or_evidence_is_rejected_before_writes(recording, change):
    directory, models, fresh, _ = recording
    if change == 'number':
        fresh[0]['text'] = 'mass 20 kg; limit 12.5 kg.'
    else:
        fresh[0]['evidence'][0]['value'] = 99
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(ValueError, match='English facts'):
        normalize(directory, *models, apply=True)
    after = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != '.prefetch.lock'}
    assert after == before


def test_invalid_english_model_spec_aborts_even_when_rehashed(recording):
    directory, models, _, _ = recording
    def mutate(payload):
        payload['summary']['spec']['sentences'][0]['before'] = 'mass 20 kg; limit 12.5 kg'
        body, rows = _body([s for s in payload['explanation']['statements'] if s['id'] != 'summary'], payload['summary'], recorded=True)
        payload['explanation'].update(text=body, statements=rows)
    _edit_payload(directory, 'en', mutate)
    with pytest.raises(ValueError, match='recorded model response'):
        normalize(directory, *models)


def test_non_thai_lead_in_is_removed_with_original_summary_and_flags_retained(recording):
    directory, models, _, _ = recording
    def mutate(payload):
        old_menu = [s for s in _payload(directory, 'en')['explanation']['statements'] if s['id'] != 'summary']
        spec = deepcopy(TH)
        spec['sentences'][0]['before'] = 'This comparison is summarized here ข้อมูลที่แสดงคือ'
        payload['summary'] = _legacy_summary(spec, summary.slots_for(old_menu), 'th')
        payload['calls'][0]['response_text'] = json.dumps(spec)
        payload['audit']['checks']['thai_sense'] = dict(status='flagged', issues=[dict(id='summary', reason='English lead-in')])
        rows = [s for s in payload['explanation']['statements'] if s['id'] != 'summary']
        body, statements = _body(rows, payload['summary'], recorded=True)
        payload['explanation'].update(text=body, statements=statements)
    _edit_payload(directory, 'th', mutate)
    old = _payload(directory, 'th')
    assert normalize(directory, *models, apply=True)['changed'] == 2
    new = _payload(directory, 'th')
    assert new['summary'] is None and new['explanation']['mode'] == 'live'
    assert new['audit']['summary_render_history'][0]['previous_summary'] == old['summary']
    assert new['audit']['checks']['thai_sense']['status'] == 'not_run'
    assert new['audit']['prior_language_checks'][-1]['issues'] == old['audit']['checks']['thai_sense']['issues']
    assert new['calls'] == old['calls'] and new['audit']['model'] is None


def test_tampered_index_fails_without_mutation(recording):
    directory, models, _, _ = recording
    index = json.loads((directory / 'index.json').read_text())
    index['binding']['plan_sha256'] = '0'*64
    (directory / 'index.json').write_text(json.dumps(index))
    with pytest.raises(ValueError, match='index integrity'):
        normalize(directory, *models)
