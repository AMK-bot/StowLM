from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.explain.render import _translations, render, validate_protected_tokens
from llm.spark.client import SparkIncompleteError
from llm.spark.spend import Usage


FACTS = [
    {'id': 'where', 'text': 'Box B01 is above B02 at THLCH; POD rank 2.',
     'th': 'ตู้ B01 อยู่เหนือ B02 ที่ THLCH; ลำดับ POD 2.',
     'evidence': [{'box_id': 'B01', 'boxes': ['B01', 'B02'], 'locode': 'THLCH'}]},
    {'id': 'weight', 'text': 'Stack mass is 12.5 kg; limit 20 kg (rules.stack_weight).',
     'th': 'น้ำหนักกอง 12.5 kg; ขีดจำกัด 20 kg (rules.stack_weight).',
     'evidence': [{'rule_id': 'rules.stack_weight', 'measured': 12.5, 'limit': 20}]},
]


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(text=json.dumps(result, ensure_ascii=False), cost_usd=Decimal('0.001'),
                               request_id='fake-request', usage=SimpleNamespace(
                                   input_tokens=100, output_tokens=50, cached_tokens=0))


def translations():
    return {'statements': [{'id': s['id'], 'text': s['th']} for s in FACTS]}


def test_offline_templates_and_metadata_are_unchanged():
    before = deepcopy(FACTS)
    english, en = render(FACTS)
    thai, th = render(FACTS, 'th')
    assert FACTS == before
    assert english == FACTS
    assert [s['text'] for s in thai] == [s['th'] for s in FACTS]
    assert thai[0]['evidence'] == FACTS[0]['evidence']
    assert en['source'] == th['source'] == 'template'
    assert th['cost_usd'] == 0
    assert th['checks']['thai_sense']['status'] == 'not_run'
    assert not th['checks']['thai_sense']['native_reader_reviewed']


def test_spark_only_orders_english_fact_ids_and_preserves_omitted_facts():
    client = FakeClient([{'statement_ids': ['weight']}])
    rendered, audit = render(FACTS, client=client)
    assert [s['id'] for s in rendered] == ['weight', 'where']
    assert rendered[0]['text'] == FACTS[1]['text']
    assert audit['source'] == 'spark-selected'
    assert len(client.calls) == 1
    assert client.calls[0][1]['purpose'] == 'pipeline-box-explain-selection'


@pytest.mark.parametrize('selection', [
    {'statement_ids': ['invented']}, {'statement_ids': ['where', 'where']},
    {'statement_ids': []}, {'statement_ids': ['where'], 'text': 'made up'},
    {'statement_ids': [True]}, {'statement_ids': 'where'},
])
def test_invalid_selection_falls_back_without_model_prose(selection):
    result, audit = render(FACTS, client=FakeClient([selection]))
    assert result == FACTS
    assert audit['checks']['selection'] == 'rejected'
    assert audit['source'] == 'template'


def test_thai_translation_then_separate_sense_check_and_cost():
    client = FakeClient([{'statement_ids': ['where', 'weight']}, translations(),
                         {'issues': [{'id': 'where', 'reason': 'Phrasing is stiff.'}]}])
    rendered, audit = render(FACTS, 'th', client=client)
    assert rendered[0]['text'] == FACTS[0]['th']
    assert audit['source'] == 'spark-selected'
    assert audit['cost_usd'] == 0.003
    assert audit['checks']['protected_tokens'] == 'passed'
    assert audit['checks']['thai_render'] == 'passed'
    assert audit['template'] is True
    assert 'thai_sense_flagged_template_fallback' in audit['fallbacks']
    assert len(audit['calls']) == 3
    assert audit['calls'][1]['request_id'] == 'fake-request'
    assert audit['calls'][1]['usage']['input_tokens'] == 100
    assert len(audit['calls'][1]['prompt_sha256']) == 64
    assert audit['calls'][1]['cost_usd'] == 0.001
    assert 'text' not in audit['calls'][1]
    sense = audit['checks']['thai_sense']
    assert sense['status'] == 'flagged'
    assert sense['issues'][0]['id'] == 'where'
    assert sense['is_proof'] is False
    assert [c[1]['purpose'] for c in client.calls] == [
        'pipeline-box-explain-selection', 'pipeline-box-explain-thai-render',
        'pipeline-box-explain-thai-sense']


@pytest.mark.parametrize('text', [
    'ตู้ B01 อยู่เหนือ B02 ที่ THLCH; ลำดับ POD 3.',
    'ตู้ B03 อยู่เหนือ B02 ที่ THLCH; ลำดับ POD 2.',
    'ตู้ B01 อยู่เหนือ B02 ที่ SGSIN; ลำดับ POD 2.',
    'ตู้ B01 อยู่เหนือ B02 ที่ THLCH; ลำดับ 2.',
    'Box B01 is above B02 at THLCH; POD rank 2.',
])
def test_invalid_translation_uses_template_but_still_runs_sense(text):
    response = translations()
    response['statements'][0]['text'] = text
    client = FakeClient([{'statement_ids': ['where']}, response, {'issues': []}])
    rendered, audit = render(FACTS, 'th', client=client)
    assert rendered[0]['text'] == FACTS[0]['th']
    assert 'thai_render_rejected' in audit['fallbacks']
    assert audit['checks']['thai_render'] == 'rejected_template_fallback'
    assert audit['source'] != 'spark-thai'
    assert audit['checks']['thai_sense']['status'] == 'passed'
    assert len(client.calls) == 3


def test_token_checks_keep_counts_and_explicit_nonnumeric_box_ids():
    assert validate_protected_tokens('B01 2 2 THLCH rules.dg', 'กฎ rules.dg THLCH B01 2 2')
    assert not validate_protected_tokens('B01 2 2', 'ตู้ B01 2')
    assert not validate_protected_tokens('mass -2', 'น้ำหนัก 2')
    assert not validate_protected_tokens('Box blue', 'ตู้ red', identifiers=['blue'])
    assert not validate_protected_tokens('12.50', '12.5')
    assert not validate_protected_tokens('20%', '20')


def test_measurement_limit_swap_is_rejected_despite_matching_tokens():
    response = translations()
    response['statements'][1]['text'] = 'น้ำหนักกอง 20 kg; ขีดจำกัด 12.5 kg (rules.stack_weight).'
    assert validate_protected_tokens(FACTS[1]['text'], response['statements'][1]['text'])
    with pytest.raises(ValueError, match='fact roles'):
        _translations(response, FACTS)


@pytest.mark.parametrize('issues', [[], [{'id': 'weight', 'reason': 'Measurement and limit reversed.'}]])
def test_renderer_never_displays_swapped_roles_even_if_sense_passes(issues):
    response = translations()
    response['statements'][1]['text'] = 'น้ำหนักกอง 20 kg; ขีดจำกัด 12.5 kg (rules.stack_weight).'
    rows, audit = render(FACTS, 'th', client=FakeClient([
        {'statement_ids': ['weight']}, response, {'issues': issues}]))
    mass = next(s for s in rows if s['id'] == 'weight')
    assert mass['text'] == FACTS[1]['th']
    assert audit['checks']['thai_render'] == 'rejected_template_fallback'
    assert audit['checks']['fact_roles'] == 'code-template-v1'


def test_missing_template_is_audited_and_not_called_thai():
    facts = deepcopy(FACTS)
    del facts[0]['th']
    rendered, audit = render(facts, 'th')
    assert rendered[0]['text'] == facts[0]['text']
    assert 'thai_template_unavailable:where' in audit['fallbacks']


def test_bad_sense_output_is_unavailable_not_passed():
    client = FakeClient([{'statement_ids': ['where']}, translations(),
                         {'issues': [{'id': 'not-allowed', 'reason': 'Wrong'}]}])
    _, audit = render(FACTS, 'th', client=client)
    assert audit['checks']['thai_sense']['status'] == 'unavailable'


def test_client_failure_falls_back_safely():
    client = FakeClient([OSError('provider detail'), OSError('provider detail'), OSError('provider detail')])
    rendered, audit = render(FACTS, 'th', client=client)
    assert rendered[0]['text'] == FACTS[0]['th']
    assert 'provider detail' not in json.dumps(audit)
    assert audit['cost_usd'] == 0
    assert audit['checks']['thai_sense']['status'] == 'unavailable'


def test_incomplete_response_retains_measured_cost_and_request_provenance():
    incomplete = SparkIncompleteError('max_output_tokens', Usage(100, 50),
                                      Decimal('0.002'), 'incomplete-request')
    _, audit = render(FACTS, client=FakeClient([incomplete]))
    assert audit['cost_usd'] == 0.002
    call = audit['calls'][0]
    assert call['status'] == 'failed'
    assert call['request_id'] == 'incomplete-request'
    assert call['usage'] == {'input_tokens': 100, 'output_tokens': 50, 'cached_tokens': 0}
    assert call['cost_usd'] == 0.002
    assert call['error_type'] == 'SparkIncompleteError'


@pytest.mark.parametrize('facts', [[FACTS[0], FACTS[0]], [{'id': 'bad', 'text': ''}], [{}]])
def test_invalid_fact_menu_is_rejected(facts):
    with pytest.raises(ValueError):
        render(facts)


def test_invalid_language_is_rejected():
    with pytest.raises(ValueError):
        render(FACTS, 'fr')
