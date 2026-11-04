"""Replay is explicit and cannot fall through to model or engine calls."""
from __future__ import annotations

import json
from pathlib import Path
import socket

import pytest

from experiments.mvp.pipeline.runner.__main__ import main
from experiments.mvp.pipeline.runner.offline import run_recorded_packet, stored_plan

HERE = Path(__file__).resolve().parent


def test_offline_replay_uses_only_recorded_reader_and_stored_plan(tmp_path, monkeypatch):
    import experiments.mvp.pipeline.runner as runner
    seen = []

    def no_network(*args, **kwargs):
        raise AssertionError('Offline replay attempted network access')

    monkeypatch.setattr(socket, 'create_connection', no_network)

    def run(packet, **kwargs):
        seen.append(kwargs)
        assert kwargs['parser'].recorded_offline is True
        assert kwargs['stored']['plan']
        assert kwargs['confirm'] is None
        answer, evidence = kwargs['parser'].parse('No containers on deck in bay 2.',
            json.loads((HERE / 'fixtures/readings.json').read_text())['readings'][1]['context'])
        assert answer.type == 'max_tiers'
        assert evidence['recorded'] is True
        return {'status': 'needs_confirmation', 'execution_mode': 'recorded_offline'}

    monkeypatch.setattr(runner, 'run_packet', run)
    value = run_recorded_packet({}, out_dir=tmp_path)
    assert value['execution_mode'] == 'recorded_offline'
    assert len(seen) == 1


@pytest.mark.parametrize('value', [None, [], {}, {'plan': None}])
def test_offline_rejects_missing_stored_plan_before_runner(tmp_path, monkeypatch, value):
    import experiments.mvp.pipeline.runner as runner
    fixture = tmp_path / 'stored.json'
    fixture.write_text(json.dumps(value))
    monkeypatch.setattr(runner, 'run_packet', lambda *args, **kwargs: pytest.fail('Runner must not start'))
    outcome = run_recorded_packet({}, out_dir=tmp_path, stored_path=fixture)
    assert outcome['status'] == 'error'
    assert outcome['execution_mode'] == 'recorded_offline'
    assert 'valid reader and stored-plan JSON' in outcome['message']
    assert not (tmp_path / 'package').exists()


def test_offline_cli_is_explicit_and_confirms_only_when_requested(tmp_path, monkeypatch, capsys):
    import experiments.mvp.pipeline.runner.offline as replay
    packet = tmp_path / 'packet.json'
    packet.write_text('{}')
    calls = []

    def run(value, **kwargs):
        calls.append(kwargs)
        assert kwargs['confirm']({}) is True
        return {'status': 'checked_plan', 'label': 'stored', 'execution_mode': 'recorded_offline'}

    monkeypatch.setattr(replay, 'run_recorded_packet', run)
    assert main([str(packet), '--out', str(tmp_path / 'out'), '--offline', '--confirm-all']) == 0
    assert json.loads(capsys.readouterr().out)['execution_mode'] == 'recorded_offline'
    assert len(calls) == 1


@pytest.mark.parametrize('kind', ['missing', 'malformed', 'stored_without_offline'])
def test_cli_bad_files_return_actionable_json_without_traceback(tmp_path, capsys, kind):
    packet = tmp_path / 'packet.json'
    if kind != 'missing':
        packet.write_text('{' if kind == 'malformed' else '{}')
    args = [str(packet), '--out', str(tmp_path / 'out')]
    if kind == 'stored_without_offline':
        args += ['--stored', str(tmp_path / 'stored.json')]
    assert main(args) == 1
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert value['status'] == 'error'
    assert any(word in value['message'].lower() for word in ['correct', 'check', 'requires'])
    assert 'Traceback' not in captured.out + captured.err


def test_bundled_stored_plan_has_required_identity():
    value = stored_plan()
    assert len(value['instance_sha256']) == 64
    assert value['plan']['instance_id']


@pytest.mark.slow
def test_bundled_offline_replay_rechecks_without_network_gpu_or_search(tmp_path, monkeypatch):
    import experiments.mvp.pipeline.runner.core as core
    from types import SimpleNamespace
    import experiments.mvp.reader.pipeline as reader

    def unavailable(*args, **kwargs):
        raise AssertionError('Offline replay attempted network, GPU or live search')

    monkeypatch.setattr(socket, 'create_connection', unavailable)
    monkeypatch.setattr(socket, 'getaddrinfo', unavailable)
    monkeypatch.setattr(socket.socket, 'connect', unavailable)
    from experiments.mvp.pipeline.binding import confirm_brief
    monkeypatch.setattr(core, 'binding', SimpleNamespace(execute_brief=unavailable, confirm_brief=confirm_brief))
    monkeypatch.setattr(reader.GlimmerParser, '__init__', unavailable)
    packet = json.loads((HERE / 'fixtures/packet.json').read_text())
    outcome = run_recorded_packet(packet, out_dir=tmp_path / 'offline', confirm=lambda row: True)
    assert outcome['status'] == 'checked_plan', outcome['message']
    assert outcome['passed'] is True and outcome['label'] == 'stored'
    assert outcome['execution_mode'] == 'recorded_offline'
    assert outcome['checker']['passed'] is True
    assert all(row['parser']['recorded'] for row in outcome['reading']['sentences'])
    persisted = json.loads((tmp_path / 'offline/outcome.json').read_text())
    assert persisted['execution_mode'] == 'recorded_offline'
