from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import Mode, sha256_json
from experiments.mvp.pipeline.explain import api, cache
from experiments.mvp.pipeline.explain.review_recording import review_recording
from llm.spark.spend import Usage


class InitialClient:
    max_retries = 0

    def complete(self, prompt, *, purpose, **kwargs):
        if purpose.startswith('pipeline-box-summary-') and purpose.endswith(('-en', '-th')):
            rows = json.loads(prompt.split('\nSLOTS: ', 1)[1])
            framing = 'ข้อมูลเปรียบเทียบคือ' if purpose.endswith('-th') else 'The stated comparison is'
            value = {'sentences': [dict(before=framing, slot_id=r['id'], after='') for r in rows[:2]]}
        else:
            value = {'issues': []}
        return SimpleNamespace(text=json.dumps(value), cost_usd=Decimal('.001'),
                               request_id='fake-initial', usage=Usage(100, 100))


class Client:
    max_retries = 0

    def __init__(self, directory, response=None):
        self.directory, self.response, self.calls = directory, response or {'issues': []}, []

    def complete(self, prompt, **kwargs):
        assert (self.directory / 'review-estimate.json').exists()
        assert kwargs['max_output_tokens'] == 4096
        self.calls.append((prompt, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return SimpleNamespace(text=json.dumps(self.response), cost_usd=Decimal('.001'),
                               request_id='fake-review', usage=Usage(100, 100))


@pytest.fixture
def recording(tmp_path, monkeypatch):
    def menu(instance, plan, report, box):
        return [{'id': 'weight', 'text': f'{box}: mass 12 kg; limit 20 kg.',
                 'th': f'{box}: น้ำหนัก 12 kg; ขีดจำกัด 20 kg.', 'evidence': [{'box_id': box}]},
                {'id': 'rank', 'text': 'Stack rank 2 of 6.',
                 'th': 'อันดับในกอง 2 จาก 6.', 'evidence': []}]
    monkeypatch.setattr(api, 'inputs', lambda *values: values)
    monkeypatch.setattr(api, 'statement_menu', menu)
    monkeypatch.setattr(cache, '_boxes', lambda *args: ['BOX01', 'BOX02'])
    values = ({'containers': [{'id': 'BOX01'}, {'id': 'BOX02'}]}, {'id': 'plan'}, {'passed': True})
    cache.prefetch(*values, tmp_path, client=InitialClient())
    return tmp_path, values


def read_payload(directory, box='BOX01', language='th'):
    index = json.loads((directory / 'index.json').read_text())
    digest = index.pop('sha256')
    assert digest == sha256_json(index)
    entry = index['entries'][box][language]
    payload = json.loads((directory / entry['path']).read_text())
    assert sha256_json(payload) == entry['sha256']
    assert payload['audit'] == entry['audit']
    return index, payload


def save_payload(directory, index, payload, box='BOX01', language='th'):
    digest = sha256_json(payload)
    cache._write(directory / (digest + '.json'), payload)
    index['entries'][box][language] = {'sha256': digest, 'path': digest + '.json', 'audit': payload['audit']}
    cache._save_index(directory / 'index.json', index)
    return digest


def test_dry_run_has_no_mutation_and_projects_all_requested_passed_rows(recording):
    directory, values = recording
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = review_recording(directory, *values)
    assert result['dry_run'] and result['estimate']['calls_bound'] == 2
    assert result['estimate']['output_tokens_bound'] == 8192
    assert result['estimate']['input_tokens_projection'] > 1024
    assert result['estimate']['projected_cost_usd'] > 0
    assert result['native_reader_reviewed'] is False and result['is_proof'] is False
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_reviews_every_requested_row_without_changing_facts(recording):
    directory, values = recording
    _, before = read_payload(directory)
    client = Client(directory)
    result = review_recording(directory, *values, client=client)
    index, after = read_payload(directory)
    assert len(client.calls) == 2 and result['requested_box_ids'] == ['BOX01', 'BOX02']
    assert after['explanation'] == before['explanation']
    assert after['summary'] == before['summary']
    assert after['audit']['checks']['thai_sense']['status'] == 'passed'
    assert after['audit']['model'] == before['audit']['model']
    history = after['audit']['thai_review_history'][-1]
    assert history['previous_payload_sha256'] == sha256_json(before)
    assert history['previous_summary_sha256'] == sha256_json(before['summary'])
    assert (directory / (sha256_json(before) + '.json')).exists()
    assert result['cost_usd'] == pytest.approx(.002)
    assert index['records'][-1]['output_sha256'] == sha256_json(index['entries'])
    assert after['audit']['calls'][-1]['request_id'] == 'fake-review'
    cache.load_cached(*values, 'BOX01', language='th', out_dir=directory)


def test_previous_flags_and_payload_hashes_survive_every_repeat(recording):
    directory, values = recording
    index, payload = read_payload(directory)
    prior = {'status': 'flagged', 'issues': [{'id': 'weight', 'reason': 'Prior wording flag'}],
             'is_proof': False, 'native_reader_reviewed': False, 'previous_payload_sha256': 'a' * 64}
    payload['audit']['prior_language_checks'] = [deepcopy(prior)]
    current = {'status': 'flagged', 'issues': [{'id': 'rank', 'reason': 'Current wording flag'}],
               'is_proof': False, 'native_reader_reviewed': False}
    payload['audit']['checks']['thai_sense'] = deepcopy(current)
    first_hash = save_payload(directory, index, payload)
    review_recording(directory, *values, client=Client(directory), box_ids=['BOX01'])
    _, first = read_payload(directory)
    first_new_hash = sha256_json(first)
    review_recording(directory, *values, client=Client(directory), box_ids=['BOX01'])
    _, final = read_payload(directory)
    history = final['audit']['prior_language_checks']
    assert history[0] == prior
    assert history[1]['issues'] == current['issues']
    assert history[1]['previous_payload_sha256'] == first_hash
    assert history[2]['previous_payload_sha256'] == first_new_hash
    assert len(final['audit']['thai_review_history']) == 2


def test_summary_flag_removes_only_model_summary_and_truthfully_labels_facts(recording):
    directory, values = recording
    _, before = read_payload(directory)
    response = {'issues': [{'id': 'summary', 'reason': 'Awkward summary'}]}
    result = review_recording(directory, *values, client=Client(directory, response), box_ids=['BOX01'])
    _, after = read_payload(directory)
    assert after['summary'] is None and after['audit']['model'] is None
    assert after['audit']['source'] == 'template'
    assert after['audit']['summary']['accepted'] is False
    assert after['explanation']['mode'] == 'live'
    assert after['explanation']['text'].startswith('Template placement facts (no model summary)')
    assert after['explanation']['statements'] == before['explanation']['statements'][1:]
    assert after['audit']['checks']['thai_sense']['issues'] == response['issues']
    assert result['reviewed'][0]['summary_removed']
    assert after['audit']['thai_review_model'] is not None
    cache.load_cached(*values, 'BOX01', language='th', out_dir=directory)


@pytest.mark.parametrize('response', [OSError('timeout'), {'issues': [{'id': 'unknown', 'reason': 'No'}]}])
def test_unavailable_review_drops_summary_and_preserves_previous_flag(recording, response):
    directory, values = recording
    result = review_recording(directory, *values, client=Client(directory, response), box_ids=['BOX01'])
    _, payload = read_payload(directory)
    assert payload['summary'] is None
    assert payload['audit']['checks']['thai_sense']['status'] == 'unavailable'
    assert payload['audit']['prior_language_checks'][-1]['status'] == 'passed'
    assert result['reviewed'][0]['summary_removed']


def test_fact_flag_does_not_change_facts_or_unflagged_summary(recording):
    directory, values = recording
    _, before = read_payload(directory)
    response = {'issues': [{'id': 'weight', 'reason': 'Unnatural wording'}]}
    review_recording(directory, *values, client=Client(directory, response), box_ids=['BOX01'])
    _, after = read_payload(directory)
    assert after['explanation'] == before['explanation']
    assert after['audit']['checks']['thai_sense']['status'] == 'flagged'
    assert after['audit']['checks']['thai_sense']['is_proof'] is False


def test_template_facts_are_reviewed_and_never_called_recorded_model_answers(recording):
    directory, values = recording
    index, payload = read_payload(directory)
    payload['summary'] = None
    payload['audit'].update(source='template', model=None)
    payload['audit']['summary']['accepted'] = False
    rows = payload['explanation']['statements'][1:]
    body, statements = cache._body(rows)
    payload['explanation'].update(text=body, statements=statements, mode='live')
    save_payload(directory, index, payload)
    client = Client(directory)
    review_recording(directory, *values, client=client, box_ids=['BOX01'])
    _, final = read_payload(directory)
    assert len(client.calls) == 1
    assert final['audit']['model'] is None and final['explanation']['mode'] == 'live'
    assert final['explanation']['text'].startswith('Template placement facts')
    assert cache.load_cached(*values, 'BOX01', language='th', out_dir=directory).mode == Mode.live


def test_all_pairs_rederive_before_any_spending_or_estimate_write(recording):
    directory, values = recording
    index, payload = read_payload(directory, 'BOX02')
    payload['explanation']['statements'][1]['text'] = 'BOX02: น้ำหนัก 20 kg; ขีดจำกัด 12 kg.'
    save_payload(directory, index, payload, box='BOX02')
    client = Client(directory)
    with pytest.raises(ValueError, match='fact roles'):
        review_recording(directory, *values, client=client)
    assert client.calls == [] and not (directory / 'review-estimate.json').exists()


def test_different_original_inputs_are_refused_before_spending(recording):
    directory, values = recording
    client = Client(directory)
    with pytest.raises(ValueError, match='different instance'):
        review_recording(directory, values[0], {'id': 'changed'}, values[2], client=client)
    assert client.calls == [] and not (directory / 'review-estimate.json').exists()


def test_shared_local_five_dollar_budget_stops_before_request(recording):
    directory, values = recording
    cache._write(directory / '.spend.json', {'used_usd': '4.99', 'calls': []})
    client = Client(directory)
    result = review_recording(directory, *values, client=client, box_ids=['BOX01'])
    _, payload = read_payload(directory)
    assert client.calls == [] and result['cost_usd'] == 0
    assert payload['audit']['checks']['thai_sense']['status'] == 'unavailable'
    assert payload['summary'] is None


def test_requested_box_ids_reject_missing_and_duplicate_entries(recording):
    directory, values = recording
    for boxes in (['missing'], ['BOX01', 'BOX01']):
        with pytest.raises(ValueError, match='distinct cache entries'):
            review_recording(directory, *values, box_ids=boxes)
