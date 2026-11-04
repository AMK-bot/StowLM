import json
import os
from pathlib import Path
import struct
import wave

import pytest

from experiments.mvp.voice.asr import MuseTranscriber, TranscriptionError, PURPOSE
from llm.spark.spend import SpendCapExceeded, SpendLedger


def fixture(tmp_path, monkeypatch, response):
    monkeypatch.setenv('MODEL_API_KEY', 'fake-secret-for-test-only')
    wav = tmp_path / 'clip.wav'
    with wave.open(str(wav), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(struct.pack('<h', 1000) * 24000)
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    ledger = SpendLedger(tmp_path / 'calls.jsonl', compute_path=compute)
    def transport(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response
    client = MuseTranscriber(ledger=ledger, transport=transport, load_key=lambda: None)
    return wav, ledger, client


class Response:
    status_code = 200
    def __init__(self, transcript='Close bay 1.', duration=1000):
        self.transcript, self.duration = transcript, duration
    def json(self):
        return {'transcript': self.transcript, 'audioDurationMs': self.duration, 'sessionId': 'session'}


def test_success_shared_settlement(tmp_path, monkeypatch):
    wav, ledger, client = fixture(tmp_path, monkeypatch, Response())
    result = client.transcribe(wav)
    assert result['transcript'] == 'Close bay 1.'
    actual, held = ledger._totals(ledger._records())
    assert str(actual) == '0.00005' and held == 0
    assert 'fake-secret' not in ledger.path.read_text()
    assert '$0.00005' in ledger.compute_path.read_text()


@pytest.mark.parametrize('response', [RuntimeError('fake-secret-for-test-only'), Response('', 1000),
                                    Response('fake-secret-for-test-only'), Response(duration=float('nan'))])
def test_safe_errors_keep_unknown_holds(tmp_path, monkeypatch, response):
    wav, ledger, client = fixture(tmp_path, monkeypatch, response)
    with pytest.raises(TranscriptionError) as error:
        client.transcribe(wav)
    assert 'fake-secret' not in str(error.value) + ledger.path.read_text()
    actual, held = ledger._totals(ledger._records())
    assert actual + held > 0


def test_restart_subcap(tmp_path, monkeypatch):
    wav, ledger, client = fixture(tmp_path, monkeypatch, Response())
    ledger.append({'purpose': PURPOSE, 'cost_usd': '0.50'})
    with pytest.raises(SpendCapExceeded):
        client.transcribe(wav)
    assert len(ledger._records()) == 1
