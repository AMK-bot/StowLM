"""Offline checks for the one permitted Spark-only sealed rerun."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
import urllib.error

import pytest

from llm.spark.client import SparkIncompleteError, SparkProtocolError, SparkResponse
from llm.spark.spend import SpendCapExceeded, SpendLedger, Usage

from . import spark_rerun as rerun
from .evaluate import arm_report, digest
from .schema import context_json_schema, prompt_messages


class SharedLedger:
    def __init__(self, records=()):
        self.records = list(records)
        self.reservations = []

    @contextmanager
    def locked(self):
        yield self

    def _records(self):
        return self.records

    _totals = staticmethod(SpendLedger._totals)

    def check_capacity(self, reservation):
        self.reservations.append(reservation)

    def append(self, record):
        self.records.append(dict(record))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unit tests must never send a network request')
    monkeypatch.setattr('urllib.request.urlopen', forbidden)


def rows(count=200):
    context = {'pods': ['SGSIN'], 'iso_types': ['22G1'], 'dg_classes': ['3']}
    return [{'id': f'synthetic-{i}', 'text': f'Synthetic incomplete booking {i}',
             'context': dict(context), 'expected': {'refusal': {'code': 'MISSING_FIELD'}}}
            for i in range(count)]


def inputs(tmp_path, *, payload=None, registration_count=200):
    data = rows()
    payload = payload if payload is not None else ''.join(json.dumps(row) + '\n' for row in data).encode()
    sealed = tmp_path / 'synthetic.jsonl'
    sealed.write_bytes(payload)
    freeze = tmp_path / 'freeze.json'
    frozen = {'sealed_registration': {'count': registration_count,
              'sha256': hashlib.sha256(payload).hexdigest()}}
    freeze.write_text(json.dumps(frozen))
    original = tmp_path / 'original.json'
    original.write_text(json.dumps({'prompt_sha256': digest(rerun.HERE / 'schema.py'),
                                   'schema_sha256': digest(rerun.HERE / 'schema.py')}))
    return freeze, original, sealed, data


def fake_client(records=()):
    return SimpleNamespace(ledger=SharedLedger(records), max_retries=0)


def response(text='{"refusal":{"code":"MISSING_FIELD"}}'):
    return SparkResponse(text, Usage(120, 20), Decimal('0.000235'), 'fake-request')


def test_verified_registered_bytes_and_unchanged_prompt_schema(tmp_path):
    freeze, original, sealed, expected = inputs(tmp_path)
    actual, frozen, old = rerun.verify_inputs(freeze, original, sealed)
    assert actual == expected
    assert frozen['sealed_registration']['sha256'] == hashlib.sha256(sealed.read_bytes()).hexdigest()
    assert old['schema_sha256'] == old['prompt_sha256'] == digest(rerun.HERE / 'schema.py')


def test_integrity_mismatch_aborts_before_decoding(tmp_path):
    freeze, original, sealed, _ = inputs(tmp_path)
    sealed.write_bytes(b'\xff not utf8 or json')
    with pytest.raises(ValueError, match='(?i)(sha|hash|registration|bytes)'):
        rerun.verify_inputs(freeze, original, sealed)


@pytest.mark.parametrize('key', ['prompt_sha256', 'schema_sha256'])
def test_prompt_and_schema_hashes_are_both_required(tmp_path, key):
    freeze, original, sealed, _ = inputs(tmp_path)
    old = json.loads(original.read_text())
    old[key] = '0' * 64
    original.write_text(json.dumps(old))
    with pytest.raises(ValueError, match='(?i)(prompt|schema|hash)'):
        rerun.verify_inputs(freeze, original, sealed)


@pytest.mark.parametrize('registration_count,population', [(199, 200), (200, 199)])
def test_registered_population_is_exactly_200(tmp_path, registration_count, population):
    payload = ''.join(json.dumps(row) + '\n' for row in rows(population)).encode()
    freeze, original, sealed, _ = inputs(tmp_path, payload=payload,
                                         registration_count=registration_count)
    with pytest.raises(ValueError, match='(?i)(count|200|population)'):
        rerun.verify_inputs(freeze, original, sealed)


def test_order_cap_counts_only_its_measured_cost_and_holds():
    shared = SharedLedger([
        {'purpose': rerun.PURPOSE, 'cost_usd': '0.40'},
        {'purpose': rerun.PURPOSE, 'phase': 'reservation', 'reservation_id': 'held', 'cost_usd': '0.30'},
        {'purpose': 'intake-lora-baseline', 'cost_usd': '99'},
    ])
    ledger = rerun.OrderLedger(shared)
    assert ledger.spent() == Decimal('0.70')
    ledger.check_capacity(Decimal('1.30'))
    with pytest.raises(SpendCapExceeded):
        ledger.check_capacity(Decimal('1.300001'))
    assert shared.reservations


def test_order_ledger_remaps_baseline_purpose_and_preserves_settlements():
    shared = SharedLedger()
    ledger = rerun.OrderLedger(shared)
    ledger.append({'purpose': rerun.baseline.PURPOSE, 'phase': 'reservation',
                   'reservation_id': 'one', 'cost_usd': '1.3'})
    ledger.append({'purpose': rerun.baseline.PURPOSE, 'phase': 'settlement',
                   'reservation_id': 'one', 'cost_usd': '0.02', 'cost_kind': 'measured'})
    assert {record['purpose'] for record in shared.records} == {rerun.PURPOSE}
    assert ledger.spent() == Decimal('0.02')
    assert rerun.baseline.PURPOSE == 'intake-lora-baseline'


def test_incomplete_is_a_miss_without_retry_and_next_row_runs(monkeypatch):
    data = rows(3)
    calls, persisted = [], []
    client = fake_client()
    def completion(client, messages, schema, *, max_output_tokens):
        calls.append((messages, schema, max_output_tokens))
        if len(calls) == 1:
            raise SparkIncompleteError('max_output_tokens', Usage(120, 2048),
                                       Decimal('0.008854'), 'incomplete-request')
        return response()
    monkeypatch.setattr(rerun.baseline, 'schema_completion', completion)
    result = rerun.run_spark(data, client=client, on_prediction=persisted.append)
    assert len(calls) == result['attempted_count'] == 3
    assert result['incomplete_count'] == 1
    assert result['transport_failure_count'] == 0
    assert persisted == result['predictions']
    assert persisted[0]['prediction'] == ''
    assert persisted[0]['decoding_error'] == 'incomplete'
    assert all(prediction['rerun'] is True for prediction in persisted)
    for row, (messages, schema, cap) in zip(data, calls):
        assert messages == prompt_messages(row['text'], row['context'])
        assert schema == context_json_schema(row['context'])
        assert cap == 2048
        assert 'expected' not in json.dumps(messages)
    report = arm_report(data, persisted, arm='spark', data_sha256='synthetic')
    assert report['exact_count'] == 2
    assert report['malformed_count'] == 1
    assert report['missing_count'] == 0
    assert report['silent_dg_errors'] == 0


def test_three_consecutive_transport_failures_stop_without_retry(monkeypatch):
    calls = []
    def completion(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError('offline transport failure')
    monkeypatch.setattr(rerun.baseline, 'schema_completion', completion)
    result = rerun.run_spark(rows(8), client=fake_client())
    assert len(calls) == result['attempted_count'] == 3
    assert result['transport_failure_count'] == 3
    assert len(result['predictions']) == 3
    assert result.get('execution_error')
    assert all(prediction.get('decoding_error') for prediction in result['predictions'])


def test_success_breaks_transport_failure_streak(monkeypatch):
    outcomes = [TimeoutError(), TimeoutError(), response(), TimeoutError(), TimeoutError(), response()]
    def completion(*args, **kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(rerun.baseline, 'schema_completion', completion)
    result = rerun.run_spark(rows(6), client=fake_client())
    assert result['attempted_count'] == 6
    assert result['transport_failure_count'] == 4
    assert not result.get('execution_error')


def test_cap_refusal_stops_before_another_prediction(monkeypatch):
    calls = []
    def completion(*args, **kwargs):
        calls.append(1)
        raise SpendCapExceeded('order cap')
    monkeypatch.setattr(rerun.baseline, 'schema_completion', completion)
    result = rerun.run_spark(rows(4), client=fake_client())
    assert len(calls) == 1
    assert result.get('execution_error')
    assert result['predictions'] == []
    assert Decimal(result['spend_usd']) == 0
    assert Decimal(result['held_usd']) == 0


def test_protocol_failure_does_not_falsely_count_as_transport(monkeypatch):
    def completion(*args, **kwargs):
        raise SparkProtocolError('Invalid Muse Spark response; billing unknown')
    monkeypatch.setattr(rerun.baseline, 'schema_completion', completion)
    result = rerun.run_spark(rows(5), client=fake_client())
    assert result['transport_failure_count'] == 0
    assert result.get('execution_error')
    assert len(result['predictions']) <= 1


def test_execute_scores_200_persists_rerun_metadata_and_refuses_second_run(tmp_path, monkeypatch):
    freeze, original, sealed, _ = inputs(tmp_path)
    output = tmp_path / 'rerun'
    calls = []
    def completion(*args, **kwargs):
        calls.append(1)
        return response()
    monkeypatch.setattr(rerun.baseline, 'schema_completion', completion)
    report = rerun.execute(freeze_path=freeze, original_path=original, sealed_path=sealed,
                           output=output, client=fake_client())
    assert len(calls) == 200
    saved = json.loads((output / 'spark.json').read_text())
    predictions = [json.loads(line) for line in (output / 'spark.predictions.jsonl').read_text().splitlines()]
    assert saved == report
    assert len(predictions) == report['attempted_count'] == report['exact_count'] == 200
    assert report['silent_dg_errors'] == report['incomplete_count'] == 0
    assert report['rerun'] is True
    assert report['max_output_tokens'] == 2048
    assert report['original_max_output_tokens'] == 256
    assert 'cap' in report['rerun_reason'].lower()
    assert report['prompt_sha256'] == report['schema_sha256'] == digest(rerun.HERE / 'schema.py')
    assert not (output / 'gates.json').exists()
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    sealed.unlink()
    with pytest.raises((ValueError, FileExistsError), match='(?i)(exist|one.shot|consum)'):
        rerun.execute(freeze_path=freeze, original_path=original, sealed_path=sealed,
                      output=output, client=fake_client())
    assert len(calls) == 200
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_shared_client_request_keeps_minimal_reasoning_and_measured_ledger(tmp_path, monkeypatch):
    from llm.spark.client import SparkClient
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    client = SparkClient('offline-fake-key', compute_path=compute,
                         ledger_path=tmp_path / 'calls.jsonl', credit_ready=True, max_retries=0)
    requests = []
    class Reply:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({'id': 'fake', 'model': 'muse-spark-1.3', 'status': 'completed',
                'usage': {'input_tokens': 120, 'output_tokens': 20},
                'output': [{'type': 'message', 'content': [{'type': 'output_text',
                    'text': '{"refusal":{"code":"MISSING_FIELD"}}'}]}]}).encode()
    def transport(request, **kwargs):
        requests.append(json.loads(request.data))
        return Reply()
    monkeypatch.setattr('urllib.request.urlopen', transport)
    data = rows(1)
    result = rerun.run_spark(data, client=client)
    assert len(requests) == result['attempted_count'] == 1
    assert requests[0]['max_output_tokens'] == 2048
    assert requests[0]['reasoning'] == {'effort': 'minimal'}
    assert requests[0]['input'] == prompt_messages(data[0]['text'], data[0]['context'])
    assert requests[0]['text']['format']['schema'] == context_json_schema(data[0]['context'])
    assert Decimal(result['spend_usd']) == response().cost_usd
    assert Decimal(result['held_usd']) == 0
    records = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    assert len(records) == 2
    assert {record['purpose'] for record in records} == {rerun.PURPOSE}
