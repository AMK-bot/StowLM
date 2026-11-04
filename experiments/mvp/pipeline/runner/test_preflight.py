"""Voice failure happens before VoyagePacket; no rules are inferred here."""
from types import SimpleNamespace

from experiments.mvp.pipeline.runner.preflight import prepare_voice


def test_network_down_during_voice_needs_typed_answer():
    def offline(path):
        raise ConnectionError('synthetic voice network unavailable')
    result = prepare_voice('synthetic.wav', transcriber=SimpleNamespace(transcribe=offline))
    assert result['status'] == 'needs_answers'
    assert 'Type the instruction' in result['message']
    assert not result['transcript'] and result['plan'] is result['package'] is None


def test_voice_draft_needs_confirmation_before_reader():
    result = prepare_voice('synthetic.wav', transcriber=SimpleNamespace(
        transcribe=lambda _: {'transcript': 'Keep bay 1 clear.'}))
    assert result['status'] == 'needs_confirmation'
    assert result['transcript'] == 'Keep bay 1 clear.'
    assert result['plan'] is None
