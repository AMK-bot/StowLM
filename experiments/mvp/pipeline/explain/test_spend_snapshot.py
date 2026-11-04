"""Portable spending evidence tests; every file is a temporary synthetic receipt."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.explain.record_spend_snapshot import build_snapshot, record, validate_snapshot
from experiments.mvp.pipeline.explain import verify_recording
from llm.spark.spend import Usage, calculate_cost


def call(rid, purpose='test-summary', status='measured'):
    usage = dict(input_tokens=100, output_tokens=100, cached_tokens=0)
    cost = str(calculate_cost(Usage(**usage)))
    row = dict(request_id=rid, purpose=purpose, status=status, cost_usd=cost, usage=usage,
               response_text='private raw prose omitted from snapshot', prompt_sha256='f' * 64)
    receipt = dict(request_id=rid, purpose=purpose, phase='settlement', cost_kind='measured',
                   cost_usd=cost, reservation_id='reservation-' + rid, **usage)
    return row, receipt


def fixture():
    first, one = call('one')
    second, two = call('two', status='measured_incomplete')
    state = dict(used_usd=str(Decimal(first['cost_usd']) + Decimal(second['cost_usd'])),
                 calls=[first, second], private_metadata='not published')
    return state, [one, two]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def rehash(value):
    value.pop('sha256', None)
    value['sha256'] = sha256_json(value)


def test_snapshot_strips_prose_and_contains_only_exact_matching_receipts():
    state, receipts = fixture()
    _, unrelated = call('unrelated')
    snapshot = build_snapshot(state, [dict(phase='reservation'), *receipts, unrelated,
                                      dict(phase='settlement', cost_kind='conservative_hold')])
    assert snapshot['source_state_sha256'] == sha256_json(state)
    assert snapshot['settlements'] == receipts
    assert snapshot['ledger_snapshot_sha256'] == sha256_json(receipts)
    stripped, actual = validate_snapshot(snapshot)
    assert actual == receipts and stripped['used_usd'] == state['used_usd']
    assert all(set(row) == {'request_id', 'purpose', 'status', 'cost_usd', 'usage'} for row in stripped['calls'])
    assert 'private raw prose' not in json.dumps(snapshot)


@pytest.mark.parametrize('change', ['hold', 'duplicate', 'cost', 'usage', 'cap'])
def test_invalid_or_unknown_spending_never_builds_snapshot(change):
    state, receipts = fixture()
    if change == 'hold':
        state['calls'][0]['status'] = 'held'
    elif change == 'duplicate':
        state['calls'][1]['request_id'] = 'one'
    elif change == 'cost':
        receipts[0]['cost_usd'] = '1'
    elif change == 'usage':
        receipts[0]['input_tokens'] += 1
    else:
        state['used_usd'] = '5.01'
    with pytest.raises(ValueError):
        build_snapshot(state, receipts)


def test_rehashed_receipt_tampering_still_fails_usage_and_cost_check():
    state, receipts = fixture()
    snapshot = build_snapshot(state, receipts)
    snapshot['settlements'][0]['input_tokens'] += 1
    snapshot['ledger_snapshot_sha256'] = sha256_json(snapshot['settlements'])
    rehash(snapshot)
    with pytest.raises(ValueError, match='cost or usage'):
        validate_snapshot(snapshot)


def test_record_only_writes_requested_snapshot_and_keeps_source_bytes(tmp_path):
    state, receipts = fixture()
    source, ledger, target = tmp_path / 'budget/.spend.json', tmp_path / 'calls.jsonl', tmp_path / 'fixtures/spend-snapshot.json'
    write(source, state)
    ledger.write_text('\n'.join(json.dumps(row) for row in receipts) + '\n')
    before = source.read_bytes(), ledger.read_bytes()
    result = record(target, state_path=source, ledger_path=ledger)
    assert validate_snapshot(json.loads(target.read_text()))[0] == result['state']
    assert (source.read_bytes(), ledger.read_bytes()) == before


def setup_verifier(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_recording, 'HERE', tmp_path)
    monkeypatch.setattr(verify_recording, 'FIXTURES', tmp_path / 'fixtures/development')
    monkeypatch.setattr(verify_recording, 'LEDGER', tmp_path / 'missing-live-ledger.jsonl')
    state, receipts = fixture()
    write(tmp_path / 'fixtures/spend-snapshot.json', build_snapshot(state, receipts))
    return state, receipts


def test_verifier_is_portable_when_live_budget_and_shared_ledger_are_missing(tmp_path, monkeypatch):
    state, _ = setup_verifier(tmp_path, monkeypatch)
    result = verify_recording._spend(tmp_path / 'cache')
    assert result['measured_cost_usd'] == state['used_usd']
    assert result['paid_calls'] == result['shared_ledger_calls_matched'] == 2
    assert result['spending_receipt_source'] == 'portable spend snapshot'
    assert result['spend_state'] == 'fixtures/spend-snapshot.json'


def test_snapshot_precedes_legacy_retired_state_and_checks_live_state_ids(tmp_path, monkeypatch):
    state, receipts = setup_verifier(tmp_path, monkeypatch)
    write(tmp_path / 'fixtures/development/retired-cache/.spend.json', dict(used_usd='0', calls=[]))
    assert verify_recording._spend(tmp_path / 'cache')['paid_calls'] == 2
    write(tmp_path / 'budget/.spend.json', state)
    assert verify_recording._spend(tmp_path / 'cache')['paid_calls'] == 2
    state['calls'][0]['request_id'] = 'different'
    write(tmp_path / 'budget/.spend.json', state)
    with pytest.raises(ValueError, match='matched request'):
        verify_recording._spend(tmp_path / 'cache')


def test_live_ledger_is_preferred_and_stale_snapshot_cannot_hide_new_call(tmp_path, monkeypatch):
    state, receipts = setup_verifier(tmp_path, monkeypatch)
    third, receipt = call('three')
    state['calls'].append(third)
    state['used_usd'] = str(sum(Decimal(row['cost_usd']) for row in state['calls']))
    write(tmp_path / 'budget/.spend.json', state)
    with pytest.raises(ValueError):
        verify_recording._spend(tmp_path / 'cache')
    verify_recording.LEDGER.write_text('\n'.join(json.dumps(row) for row in [*receipts, receipt]))
    result = verify_recording._spend(tmp_path / 'cache')
    assert result['paid_calls'] == 3 and result['spending_receipt_source'] == 'live shared ledger'


def test_local_hidden_state_is_checked_against_portable_receipts(tmp_path, monkeypatch):
    state, _ = setup_verifier(tmp_path, monkeypatch)
    local = dict(used_usd=state['calls'][0]['cost_usd'], calls=[state['calls'][0]])
    write(tmp_path / 'cache/.spend.json', local)
    assert verify_recording._spend(tmp_path / 'cache')['paid_calls'] == 2
    local['calls'][0]['usage']['output_tokens'] += 1
    write(tmp_path / 'cache/.spend.json', local)
    with pytest.raises(ValueError, match='cost or usage'):
        verify_recording._spend(tmp_path / 'cache')
