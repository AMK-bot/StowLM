import json
import urllib.request

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.copilot.replay import FIXTURES, replay_conversation, replay_real_conversation


@pytest.mark.parametrize('name', ['plain', 'negotiation', 'attack'])
def test_recorded_conversation_replays_without_network(name, monkeypatch):
    def offline(*args, **kwargs):
        raise AssertionError('Replay attempted network access')
    monkeypatch.setattr(urllib.request, 'urlopen', offline)
    replay = replay_conversation(name)
    assert replay['mode'] == 'recorded'
    assert 'synthetic' in replay['label'].lower()
    assert replay['conversation']['turns']
    for row in replay['conversation']['turns']:
        assert row['turn']['mode'] == 'recorded'
        for call in row['turn']['tool_calls']:
            assert call['result_sha256'] == sha256_json(call['result'])


def test_live_scenario_record_reports_all_failures():
    value = json.loads((FIXTURES / 'live-scenarios.json').read_text())
    assert value['scenario_count'] >= 30
    assert value['scenario_count'] == len(value['results'])
    assert value['passed'] == sum(row['passed'] for row in value['results'])
    assert value['failures'] == [row['id'] for row in value['results'] if not row['passed']]
    for name, count in value['metrics'].items():
        assert count == {'passed': sum(row['scores'][name] for row in value['results']),
                         'total': len(value['results'])}


def test_replay_name_cannot_access_arbitrary_file():
    with pytest.raises(ValueError):
        replay_conversation('../scenarios')


@pytest.mark.parametrize('name', ['plain', 'negotiation', 'attack'])
def test_real_tool_conversation_replays_without_network(name, monkeypatch):
    def offline(*args, **kwargs):
        raise AssertionError('Replay attempted network access')
    monkeypatch.setattr(urllib.request, 'urlopen', offline)
    value = replay_real_conversation(name)
    assert value['mode'] == 'recorded'
    assert 'not the adopted fine-tuned reader' in value['reader_label']
    assert value['source']['envelope']['in_envelope']
    assert value['turns']
    for row in value['turns']:
        assert row['turn']['mode'] == 'recorded'
        digest = row['turn']['plan_sha256']
        if digest:
            assert any(call['result'].get('passed') is True
                       and call['result'].get('checker', {}).get('screen') == 'full'
                       and call['result'].get('plan_sha256') == digest
                       for call in row['turn']['tool_calls'])


def test_review_recordings_bind_current_guards_and_document_attack_has_no_tools():
    from experiments.mvp.pipeline.copilot.review_revalidate import runtime_fingerprint
    expected = runtime_fingerprint()['sha256']
    for name in ('plain', 'negotiation', 'attack'):
        value = replay_conversation(name)
        assert value['guard_replayed'] is True
        assert value['runtime_sha256'] == expected
        assert 'offline' in value['label'].lower()
    attack = replay_conversation('attack')
    assert attack['document_attack_zero_tools'] is True
    assert attack['conversation']['actual_tool_sequence'] == []
    assert all(not row['turn']['tool_calls'] for row in attack['conversation']['turns'])
