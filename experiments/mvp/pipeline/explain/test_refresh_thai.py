from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json, chain_intact, StageRecord
from experiments.mvp.pipeline.explain import api, cache
from experiments.mvp.pipeline.explain.refresh_thai import refresh
from llm.spark.spend import Usage


class Client:
    max_retries = 0

    def __init__(self, issues=None):
        self.calls, self.issues = [], issues or []

    def complete(self, prompt, *, purpose, **kwargs):
        self.calls.append((purpose, kwargs))
        assert kwargs['max_output_tokens'] == 4096
        if purpose.endswith(('-en', '-th')):
            rows = json.loads(prompt.split('\nSLOTS: ', 1)[1])
            value = {'sentences': [dict(before='ข้อมูลเปรียบเทียบคือ' if purpose.endswith('-th') else 'The stated comparison is',
                                       slot_id=row['id'], after='') for row in rows[:2]]}
        else:
            value = {'issues': self.issues}
        return SimpleNamespace(text=json.dumps(value), request_id=f'fake-{len(self.calls)}',
                               cost_usd=Decimal('.001'), usage=Usage(100, 100))


@pytest.fixture
def recording(tmp_path, monkeypatch):
    def menu(instance, plan, report, box):
        return [dict(id='weight', text=f'{box}: mass 12 kg; limit 20 kg.',
                     th=f'{box}: น้ำหนัก 12 kg; ขีดจำกัด 20 kg.', evidence=[dict(box_id=box)]),
                dict(id='rank', text='Stack rank 2 of 6.', th='อันดับในกอง 2 จาก 6.', evidence=[])]
    monkeypatch.setattr(api, 'inputs', lambda *values: values)
    monkeypatch.setattr(api, 'statement_menu', menu)
    monkeypatch.setattr(cache, '_boxes', lambda *args: ['BOX01'])
    values = ({'containers': [{'id': 'BOX01'}]}, {'id': 'plan'}, {'passed': True})
    cache.prefetch(*values, tmp_path, client=Client())
    return tmp_path, values


def read(directory, language='th'):
    index = json.loads((directory / 'index.json').read_text())
    entry = index['entries']['BOX01'][language]
    return index, json.loads((directory / entry['path']).read_text())


def save(directory, index, payload):
    digest = sha256_json(payload)
    cache._write(directory / (digest + '.json'), payload)
    index['entries']['BOX01']['th'] = dict(path=digest + '.json', sha256=digest, audit=deepcopy(payload['audit']))
    cache._save_index(directory / 'index.json', index)
    return digest


def remove_summary(directory):
    index, payload = read(directory)
    payload['summary'] = None
    payload['audit'].update(source='template', model=None)
    payload['audit']['summary']['accepted'] = False
    rows = [r for r in payload['explanation']['statements'] if r['id'] != 'summary']
    text, rows = cache._body(rows)
    payload['explanation'].update(text=text, statements=rows, mode='live')
    return save(directory, index, payload)


def test_dry_run_estimates_without_writing(recording):
    directory, values = recording
    remove_summary(directory)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = refresh(directory, *values)
    assert result['dry_run'] and result['estimate']['calls_bound'] == 2
    assert result['estimate']['output_tokens_bound'] == 8192
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_accepted_thai_refresh_keeps_english_facts_prior_flags_and_hashes(recording):
    directory, values = recording
    old_hash = remove_summary(directory)
    _, original = read(directory)
    client = Client()
    result = refresh(directory, *values, client=client)
    index, updated = read(directory)
    assert len(client.calls) == 2 and result['refreshed'][0]['summary_accepted']
    assert updated['summary'] is not None and updated['audit']['summary']['accepted']
    assert updated['explanation']['mode'] == 'recorded'
    assert updated['explanation']['text'].startswith('Recorded Spark summary')
    assert updated['explanation']['statements'][1:] == original['explanation']['statements']
    assert updated['audit']['prior_language_checks'][-1]['previous_payload_sha256'] == old_hash
    assert (directory / (old_hash + '.json')).exists()
    assert chain_intact([StageRecord.model_validate(row) for row in index['records']])
    assert index['records'][-1]['input_sha256'] == sha256_json(index['binding'])
    assert len(updated['calls']) == len(original['calls']) + 2
    cache.load_cached(*values, 'BOX01', language='th', out_dir=directory)


def test_summary_sense_flag_suppresses_refreshed_summary(recording):
    directory, values = recording
    remove_summary(directory)
    issue = dict(id='summary', reason='Awkward framing')
    result = refresh(directory, *values, client=Client([issue]))
    _, payload = read(directory)
    assert not result['refreshed'][0]['summary_accepted']
    assert payload['summary'] is None and payload['audit']['model'] is None
    assert payload['explanation']['mode'] == 'live'
    assert payload['audit']['checks']['thai_sense']['issues'] == [issue]
    cache.load_cached(*values, 'BOX01', language='th', out_dir=directory)


def test_old_stray_english_thai_summary_is_archived_without_loading_it(recording):
    directory, values = recording
    index, payload = read(directory)
    payload['summary']['spec']['sentences'][0]['before'] = 'The ข้อมูลเปรียบเทียบคือ'
    old_hash = save(directory, index, payload)
    with pytest.raises(ValueError, match='English lead-ins'):
        cache.load_cached(*values, 'BOX01', language='th', out_dir=directory)
    result = refresh(directory, *values, client=Client())
    _, updated = read(directory)
    assert result['refreshed'][0]['summary_accepted']
    assert updated['audit']['thai_summary_refresh_history'][-1]['previous_payload_sha256'] == old_hash
    assert json.loads((directory / (old_hash + '.json')).read_text())['summary']['spec']['sentences'][0]['before'].startswith('The ')


def test_rehashed_fact_role_swap_fails_before_calls_or_estimate_write(recording):
    directory, values = recording
    index, payload = read(directory)
    fact = next(row for row in payload['explanation']['statements'] if row['id'] == 'weight')
    fact['text'] = 'BOX01: น้ำหนัก 20 kg; ขีดจำกัด 12 kg.'
    save(directory, index, payload)
    client = Client()
    with pytest.raises(ValueError, match='fact roles'):
        refresh(directory, *values, client=client, box_ids=['BOX01'])
    assert not client.calls and not (directory / 'refresh-thai-estimate.json').exists()


def test_valid_admitted_thai_is_not_refreshed_by_default(recording):
    directory, values = recording
    result = refresh(directory, *values)
    assert result['requested_box_ids'] == [] and result['estimate']['calls_bound'] == 0
