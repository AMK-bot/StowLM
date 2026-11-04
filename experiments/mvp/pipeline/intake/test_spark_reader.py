from contextlib import contextmanager
from decimal import Decimal
import json
import urllib.error

import pytest

from . import spark_reader as reader
from llm.spark.client import SparkCreditNotReady, SparkIncompleteError, SparkProtocolError
from llm.spark.spend import SpendCapExceeded, SpendLedger


def proposal(document='email'):
    return {'observations': [
        {'row_id': 'MSCU1234567', 'field': 'gross_mass_kg', 'value': 18000,
         'document': document, 'quote': 'VGM 18 tonnes'},
    ], 'duplicates': [], 'notes': []}


def client(tmp_path, *, credit_ready=True):
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    return reader.MultimodalSparkClient(api_key='test-key', ledger_path=tmp_path / 'calls.jsonl',
                                        compute_path=compute, credit_ready=credit_ready)


def response_payload(**updates):
    data = {'model': reader.MODEL, 'id': 'recorded-request', 'status': 'completed',
            'usage': {'input_tokens': 100, 'output_tokens': 40,
                      'input_tokens_details': {'cached_tokens': 20}},
            'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(proposal())}]}]}
    data.update(updates)
    return data


@contextmanager
def http_response(data):
    class Response:
        headers = {'x-request-id': 'test-header-request'}

        def read(self):
            return json.dumps(data).encode()
    yield Response()


def test_multimodal_bytes_and_schema(tmp_path):
    picture = tmp_path / 'tally.png'
    picture.write_bytes(b'\x89PNG\r\n\x1a\nactual image bytes')
    content = reader.document_content([
        {'id': 'booking', 'text': 'Synthetic document\nVGM 18 tonnes'},
        {'id': 'tally', 'path': str(picture), 'text': 'HIDDEN ANSWER NOT SENT'},
    ])
    assert content[-1]['type'] == 'input_image'
    assert content[-1]['image_url'].startswith('data:image/png;base64,')
    wire = json.dumps(content)
    assert 'HIDDEN ANSWER NOT SENT' not in wire
    for field in reader.Container.model_fields:
        assert field in content[0]['text']
    assert 'NOT a duplicate' in content[0]['text']


def test_live_accounting_and_payload(monkeypatch, tmp_path):
    sent = []

    def transport(request, timeout):
        sent.append(json.loads(request.data))
        assert request.get_header('Authorization') == 'Bearer test-key'
        return http_response(response_payload())

    monkeypatch.setattr(reader.urllib.request, 'urlopen', transport)
    live = client(tmp_path)
    out = reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], client=live)
    assert out['observations'] == proposal()['observations']
    assert out['metadata']['mode'] == 'live'
    assert out['metadata']['cost_usd'] == pytest.approx(0.000273)
    assert out['metadata']['usage'] == {'input_tokens': 100, 'output_tokens': 40, 'cached_tokens': 20}
    assert sent[0]['input'][0]['content'][1]['text'] == '\nSOURCE DOCUMENT ID: email'
    assert sent[0]['store'] is False
    records = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    assert [record['phase'] for record in records] == ['reservation', 'settlement']
    assert all(record['purpose'] == reader.PURPOSE for record in records)
    assert records[-1]['cost_kind'] == 'measured'
    assert 'test-key' not in (tmp_path / 'calls.jsonl').read_text()
    assert 'VGM 18 tonnes' not in (tmp_path / 'calls.jsonl').read_text()


def test_recorded_replay_preserves_original_cost_without_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('Recorded replay used network')
    monkeypatch.setattr(reader.urllib.request, 'urlopen', no_network)
    raw = proposal()
    raw['metadata'] = {'wall_s': 12.5, 'cost_usd': 0.0123, 'model': reader.MODEL, 'mode': 'live'}
    out = reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], reader.RecordedClient(raw))
    assert out['metadata']['mode'] == 'recorded'
    assert out['metadata']['wall_s'] == 12.5
    assert out['metadata']['cost_usd'] == 0.0123
    assert out['metadata']['replay_cost_usd'] == 0
    assert raw['metadata']['mode'] == 'live'


def test_credit_gate_sends_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(reader.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('Sent request'))
    with pytest.raises(SparkCreditNotReady):
        reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], client(tmp_path, credit_ready=False))
    assert not (tmp_path / 'calls.jsonl').exists()


def test_incomplete_usage_is_settled_but_not_returned(monkeypatch, tmp_path):
    monkeypatch.setattr(reader.urllib.request, 'urlopen', lambda *a, **kw: http_response(
        response_payload(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})))
    with pytest.raises(SparkIncompleteError):
        reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], client(tmp_path))
    records = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    assert records[-1]['status'] == 'incomplete'
    assert records[-1]['cost_kind'] == 'measured'


def test_invalid_provider_usage_retains_hold(monkeypatch, tmp_path):
    monkeypatch.setattr(reader.urllib.request, 'urlopen', lambda *a, **kw: http_response(
        response_payload(usage={'input_tokens': -1, 'output_tokens': 2})))
    with pytest.raises(SparkProtocolError):
        reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], client(tmp_path))
    records = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    assert records[-1]['cost_kind'] == 'conservative_hold'


def test_task_cap_is_separate_from_project_cap(monkeypatch, tmp_path):
    live = client(tmp_path)
    live.ledger.append({'purpose': reader.PURPOSE, 'cost_usd': '5'})
    monkeypatch.setattr(reader.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('Sent request'))
    with pytest.raises(SpendCapExceeded, match='Intake task'):
        reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], live)
    assert live.ledger.cap_usd == Decimal('150')


def test_other_task_spend_does_not_consume_intake_cap(monkeypatch, tmp_path):
    live = client(tmp_path)
    live.ledger.append({'purpose': 'other-executive', 'cost_usd': '20'})
    monkeypatch.setattr(reader.urllib.request, 'urlopen', lambda *a, **kw: http_response(response_payload()))
    assert reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], live)['metadata']['mode'] == 'live'


@pytest.mark.parametrize('mutation', [
    lambda raw: raw['observations'][0].update(document='unknown-document'),
    lambda raw: raw['observations'][0].update(field='instruction.allow_unsafe'),
    lambda raw: raw['observations'][0].update(quote=None),
    lambda raw: raw.update(observations='wrong-shape'),
    lambda raw: raw.update(duplicates=[{'row_id': 'MSCU1234567', 'document': 'email'}]),
])
def test_bad_proposals_fail_closed(mutation):
    raw = proposal()
    mutation(raw)
    with pytest.raises(ValueError):
        reader.read([{'id': 'email', 'text': 'VGM 18 tonnes'}], reader.RecordedClient(raw))


def test_text_file_and_unique_ids(tmp_path):
    sheet = tmp_path / 'sheet.csv'
    sheet.write_text('\ufeffcontainer,kg\nMSCU1234567,18000')
    content = reader.document_content([{'id': 'sheet', 'path': str(sheet)}])
    assert content[-1]['text'].startswith('container,kg')
    with pytest.raises(ValueError, match='unique'):
        reader.document_content([{'id': 'same', 'text': 'a'}, {'id': 'same', 'text': 'b'}])
