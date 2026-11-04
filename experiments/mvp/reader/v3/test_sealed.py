"""Synthetic transport/isolation fixtures; never reads a real sealed sentence."""
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch
import urllib.request

import pytest

from experiments.mvp.reader.v3 import admission, sealed
from experiments.mvp.reader.v3.spark_client import SchemaSparkClient
from llm.spark.spend import SpendLedger


CONTEXT = {'bays': [1, 3], 'max_tiers': {'hold': 3, 'deck': 4},
           'max_tiers_by_bay': {}, 'has_accommodation': True,
           'hatch_covers': ['fixture'], 'ports': ['SYNTH']}


@pytest.mark.parametrize('updates', [{'mask': 127}, {'load1': 46}, {'t': 880}, {'t': 1001}])
def test_admission_rejects_invalid_state(tmp_path, updates):
    state = {'mask': 255, 'load1': 45, 't': 999, **updates}
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(state))
    with pytest.raises(RuntimeError):
        admission.admit(path, now=1000)


def test_waiting_never_bypasses_admission(monkeypatch):
    calls = []
    def current():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('denied')
        return {'admitted': True}
    monkeypatch.setattr(admission, 'admit', current)
    monkeypatch.setattr(admission.time, 'sleep', lambda _seconds: None)
    assert admission.wait_admit(10) == {'admitted': True}
    assert len(calls) == 2


def test_schema_spark_injects_mask_and_preserves_shared_client_accounting(tmp_path):
    ledger = tmp_path / 'calls.jsonl'
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    client = SchemaSparkClient(api_key='fixture-secret', ledger_path=ledger,
        compute_path=compute, credit_ready=True, max_retries=0)
    request_class = urllib.request.Request
    observed = []

    class Response:
        headers = {'x-request-id': 'fixture-request'}
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def read(self):
            return json.dumps({'id': 'fixture-request', 'model': 'muse-spark-1.3',
                'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 40},
                'output': [{'type': 'message', 'content': [{'type': 'output_text',
                'text': '{"schema_version":"stowlm.reader/2","constraint":{"type":"close_bay","bay":1}}'}]}]}).encode()

    def transport(request, timeout):
        observed.append(json.loads(request.data))
        assert request.headers['Authorization'] == 'Bearer fixture-secret'
        return Response()

    with patch('urllib.request.urlopen', transport):
        result = client.complete_schema('synthetic input', context=CONTEXT,
                                       purpose='synthetic-fixture')
    assert urllib.request.Request is request_class
    assert observed[0]['text']['format']['type'] == 'json_schema'
    assert observed[0]['text']['format']['schema']
    assert observed[0]['store'] is False
    assert result.request_id == 'fixture-request'
    records = client.ledger._records()
    assert [r['phase'] for r in records] == ['reservation', 'settlement']
    actual, held = SpendLedger._totals(records)
    assert actual == Decimal('0.000295') and held == 0
    assert 'fixture-secret' not in ledger.read_text()
    assert 'synthetic input' not in ledger.read_text()


def test_independent_packet_uses_only_public_contract_world(tmp_path, monkeypatch):
    monkeypatch.setattr(sealed, 'OUT', tmp_path / 'sealed')
    sealed.packet()
    sealed.verify_packet()
    packet = (sealed.OUT / 'reviewer_prompt.txt').read_text()
    assert (sealed.READER / 'contract.json').read_text() in packet
    assert (sealed.READER / 'vocabulary.md').read_text() in packet
    assert '48 loosening' in packet
    with pytest.raises(ValueError, match='already frozen'):
        sealed.packet()


def test_ingest_preserves_independent_text_and_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(sealed, 'OUT', tmp_path / 'sealed')
    sealed.packet()
    labels = {'schema_version': 'stowlm.reader/2', 'refusal': {
        'refused': True, 'code': 'loosening', 'reason': 'Independently authored reason.'}}
    rows = [{'id': f'sealed-v3-{i:03}', 'sentence': f'Synthetic independent relaxation fixture {i}.',
             'expected': labels, 'kind': 'loosening', 'family': 'synthetic', 'loosening': True}
            for i in range(1, 121)]
    (sealed.OUT / 'claude_response.json').write_text(json.dumps({'result': '\n'.join(map(json.dumps, rows))}))
    (sealed.OUT / 'reviewer-process.json').write_text('{}')
    sealed.ingest()
    result = sealed.rows_and_verify()
    assert [(r['sentence'], r['expected']) for r in result] == [(r['sentence'], r['expected']) for r in rows]
    assert json.loads((sealed.OUT / 'manifest.json').read_text())['loosening_count'] == 120
    with pytest.raises(ValueError, match='Never overwrite'):
        sealed.ingest()
