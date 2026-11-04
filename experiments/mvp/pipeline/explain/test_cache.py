"""Offline replay, stale-cache rejection, and spending guards."""
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import Mode
from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.explain import api, cache
from llm.spark.spend import SpendCapExceeded, Usage


@pytest.fixture
def facts(monkeypatch):
    def menu(instance, plan, report, box):
        return [{'id': 'weight', 'text': f'{box} weighs 20 kg.',
                 'th': f'{box} หนัก 20 kg.', 'evidence': [{'box_id': box}]},
                {'id': 'rank', 'text': 'Stack rank 2 of 6.',
                 'th': 'อันดับในกอง 2 จาก 6.', 'evidence': []}]
    monkeypatch.setattr(api, 'statement_menu', menu)
    monkeypatch.setattr(api, 'inputs', lambda *values: values)
    monkeypatch.setattr(cache, '_boxes', lambda instance, plan, report: sorted(row['id'] for row in instance['containers']))
    return ({'containers': [{'id': 'BOX01'}, {'id': 'BOX02'}]},
            {'id': 'plan'}, {'passed': True})


class FakeClient:
    def __init__(self):
        self.calls = []

    def complete(self, prompt, *, purpose, **kwargs):
        self.calls.append(purpose)
        if purpose in ('pipeline-box-summary-en', 'pipeline-box-summary-th'):
            rows = json.loads(prompt.split('\nSLOTS: ', 1)[1])
            before = 'ข้อมูลเปรียบเทียบคือ' if purpose.endswith('-th') else 'The stated comparison is'
            value = {'sentences': [dict(before=before, slot_id=row['id'], after='') for row in rows[:2]]}
        else:
            value = {'issues': []}
        return SimpleNamespace(text=json.dumps(value), cost_usd=Decimal('.001'),
                               request_id='fake', usage=Usage(100, 100))


def test_templates_replay_both_languages_with_explicit_provenance(facts, tmp_path):
    index = cache.prefetch(*facts, tmp_path)
    assert index['estimate']['label'] == 'Projection'
    assert index['estimate']['calls'] == 0
    for box in ('BOX01', 'BOX02'):
        for language in ('en', 'th'):
            answer = cache.load_cached(*facts, box, language=language, out_dir=tmp_path)
            assert answer.mode == Mode.live
            assert answer.text.startswith('Template placement facts (no model summary)')
            assert answer.statements[0]['evidence'] == [{'box_id': box}]
    assert index['records'][0]['cost_usd'] == 0
    assert not (tmp_path / '.spend.json').exists()


def test_recorded_model_audits_and_raw_calls(facts, tmp_path):
    client = FakeClient()
    index = cache.prefetch(*facts, tmp_path, client=client)
    assert len(client.calls) == 6
    assert index['records'][0]['cost_usd'] == pytest.approx(.006)
    row = index['entries']['BOX01']['th']
    payload = json.loads((tmp_path / row['path']).read_text())
    assert payload['audit']['checks']['thai_sense']['status'] == 'passed'
    assert payload['audit']['checks']['thai_sense']['is_proof'] is False
    assert len(payload['calls']) == 3
    assert all(call['response_text'] for call in payload['calls'])
    answer = cache.load_cached(*facts, 'BOX01', out_dir=tmp_path)
    assert answer.text.startswith('Recorded Spark summary\n')
    assert answer.statements[0]['id'] == 'summary'
    cache.prefetch(*facts, tmp_path, client=client)
    assert len(client.calls) == 6  # A valid recorded answer costs nothing to reuse.


def test_missing_answer_is_a_cache_miss(facts, tmp_path):
    assert cache.load_cached(*facts, 'BOX01', out_dir=tmp_path) is None
    cache.prefetch(*facts, tmp_path)
    assert cache.load_cached(*facts, 'MISSING', out_dir=tmp_path) is None


def test_partial_upgrade_keeps_later_recorded_entries(facts, tmp_path):
    client = FakeClient()
    cache.prefetch(*facts, tmp_path, client=client, model_box_ids=['BOX02'])
    assert len(client.calls) == 3
    cache.prefetch(*facts, tmp_path, client=client, model_box_ids=['BOX01', 'BOX02'])
    assert len(client.calls) == 6
    assert all(cache.load_cached(*facts, box, language=lang, out_dir=tmp_path).mode == Mode.recorded
               for box in ('BOX01', 'BOX02') for lang in ('en', 'th'))


def test_thai_replay_cannot_swap_english_summary_slots(facts, tmp_path):
    from experiments.mvp.pipeline.explain.summary import validate_summary, slots_for
    index = cache.prefetch(*facts, tmp_path, client=FakeClient())
    entry = index['entries']['BOX01']['th']
    payload = json.loads((tmp_path / entry['path']).read_text())
    spec = payload['summary']['spec']
    spec['sentences'].reverse()
    payload['summary'] = validate_summary(spec, slots_for(api.statement_menu(*facts, 'BOX01')), 'th')
    facts_rows = [s for s in payload['explanation']['statements'] if s['id'] != 'summary']
    body, rows = cache._body(facts_rows, payload['summary'], recorded=True)
    payload['explanation'].update(text=body, statements=rows)
    digest = sha256_json(payload)
    cache._write(tmp_path / (digest + '.json'), payload)
    entry.update(path=digest + '.json', sha256=digest)
    cache._save_index(tmp_path / 'index.json', index)
    with pytest.raises(ValueError, match='same English'):
        cache.load_cached(*facts, 'BOX01', language='th', out_dir=tmp_path)


def test_replay_rejects_swapped_measurement_limit_even_with_valid_hashes(facts, tmp_path, monkeypatch):
    def menu(instance, plan, report, box):
        return [{'id': 'weight', 'text': f'{box}: mass 12.5 kg; limit 20 kg.',
                 'th': f'{box}: น้ำหนัก 12.5 kg; ขีดจำกัด 20 kg.', 'evidence': []}]
    monkeypatch.setattr(api, 'statement_menu', menu)
    index = cache.prefetch(*facts, tmp_path)
    entry = index['entries']['BOX01']['th']
    payload = json.loads((tmp_path / entry['path']).read_text())
    swapped = 'BOX01: น้ำหนัก 20 kg; ขีดจำกัด 12.5 kg.'
    payload['explanation']['statements'][0]['text'] = swapped
    payload['explanation']['text'] = 'Recorded answer\n' + swapped
    digest = sha256_json(payload)
    cache._write(tmp_path / (digest + '.json'), payload)
    entry.update(path=digest + '.json', sha256=digest)
    cache._save_index(tmp_path / 'index.json', index)
    with pytest.raises(ValueError, match='fact roles'):
        cache.load_cached(*facts, 'BOX01', language='th', out_dir=tmp_path)


def test_load_rejects_stale_or_edited_payload(facts, tmp_path):
    index = cache.prefetch(*facts, tmp_path)
    instance, plan, report = facts
    for changed in [(dict(instance, other=1), plan, report),
                    (instance, dict(plan, id='changed'), report),
                    (instance, plan, dict(report, passed=False))]:
        with pytest.raises(ValueError, match='different instance, plan, or report'):
            cache.load_cached(*changed, 'BOX01', out_dir=tmp_path)
    entry = index['entries']['BOX01']['en']
    path = tmp_path / entry['path']
    value = json.loads(path.read_text())
    value['explanation']['text'] += ' edited'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='payload integrity mismatch'):
        cache.load_cached(*facts, 'BOX01', out_dir=tmp_path)


def test_budget_reserves_before_call_and_persists(tmp_path):
    client = FakeClient()
    proxy = cache._BudgetClient(client, tmp_path, 3.)
    reserve = cache._reserve()
    # A previous unknown-billing request consumes a full reservation.
    (tmp_path / '.spend.json').write_text(json.dumps({'used_usd': str(2 * reserve), 'calls': []}))
    with pytest.raises(SpendCapExceeded):
        proxy.complete('prompt', purpose='test', max_output_tokens=4096)
    assert client.calls == []
    with pytest.raises(ValueError, match='at most'):
        cache._BudgetClient(client, tmp_path, 5.01)


def test_projection_over_cap_stops_before_spending(facts, tmp_path):
    client = FakeClient()
    with pytest.raises(SpendCapExceeded, match='projection'):
        cache.prefetch(*facts, tmp_path, client=client, spend_cap_usd=.001)
    assert client.calls == []
    assert (tmp_path / 'estimate.json').exists()
    assert not (tmp_path / '.spend.json').exists()


def test_unknown_billing_retains_hold(tmp_path):
    class TimeoutClient:
        def complete(self, *args, **kwargs):
            raise OSError('timeout')
    proxy = cache._BudgetClient(TimeoutClient(), tmp_path, 3.)
    with pytest.raises(OSError):
        proxy.complete('prompt', purpose='test', max_output_tokens=4096)
    state = json.loads((tmp_path / '.spend.json').read_text())
    assert Decimal(state['used_usd']) == cache._reserve()
    assert state['calls'][0]['status'] == 'held'


def test_incomplete_billing_keeps_shared_ledger_identity(tmp_path):
    from llm.spark.client import SparkIncompleteError
    from llm.spark.spend import calculate_cost
    usage = Usage(100, 100)
    class IncompleteClient:
        def complete(self, *args, **kwargs):
            raise SparkIncompleteError('max_output_tokens', usage, calculate_cost(usage), 'partial-request')
    proxy = cache._BudgetClient(IncompleteClient(), tmp_path, 5.)
    with pytest.raises(SparkIncompleteError):
        proxy.complete('prompt', purpose='test', max_output_tokens=4096)
    state = json.loads((tmp_path / '.spend.json').read_text())
    assert state['calls'][0]['status'] == 'measured_incomplete'
    assert state['calls'][0]['request_id'] == 'partial-request'
    assert state['calls'][0]['usage']['input_tokens'] == 100
    assert Decimal(state['used_usd']) == calculate_cost(usage)


def test_invalid_index_and_language_rejected(facts, tmp_path):
    cache.prefetch(*facts, tmp_path)
    with pytest.raises(ValueError, match='language'):
        cache.load_cached(*facts, 'BOX01', language='xx', out_dir=tmp_path)
    path = tmp_path / 'index.json'
    value = json.loads(path.read_text())
    value['entries']['BOX01']['en']['path'] = '../escape.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='index integrity'):
        cache.load_cached(*facts, 'BOX01', out_dir=tmp_path)
    value['entries']['BOX01']['en'] = {'path': '../escape.json', 'sha256': '../escape'}
    value.pop('sha256')
    value['sha256'] = sha256_json(value)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='cache path'):
        cache.load_cached(*facts, 'BOX01', out_dir=tmp_path)
