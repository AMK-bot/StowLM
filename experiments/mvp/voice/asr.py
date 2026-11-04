"""Buffered Muse transcription, no retries, shared durable spend accounting.

The ASR purpose has a $0.50 lifetime subcap; the reader purpose has $2.50.
Credentials are loaded into this process environment only. Never log exceptions
from HTTP transports, headers, or provider error bodies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4
import wave

import requests

from llm.spark.spend import SpendCapExceeded, SpendLedger

ENDPOINT = 'https://api.meta.ai/v1/asr/transcribe'
MODEL = 'muse-voice-transcribe-1.0'
PURPOSE = 'voice-v5-1.3-asr'
ALLOCATION = Decimal('0.50')
RATE = Decimal('0.18') / Decimal(3600)


class TranscriptionError(RuntimeError):
    def __init__(self):
        super().__init__('Voice transcription unavailable; type the instruction.')


def audio_info(path):
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Audio exceeds upload limit')
    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() not in (16000, 24000):
            raise ValueError('Use mono PCM16 WAV at 16 or 24 kHz')
        seconds = wav.getnframes() / wav.getframerate()
        if not 0 < seconds <= 60:
            raise ValueError('Voice brief must contain 0–60 seconds of audio')
        frames = wav.readframes(wav.getnframes())
        if len(frames) != wav.getnframes() * wav.getsampwidth() * wav.getnchannels():
            raise ValueError('Audio WAV is truncated')
        if not any(frames):
            raise ValueError('Audio is silent')
    return {'audio_seconds': seconds, 'audio_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class MuseTranscriber:
    def __init__(self, *, ledger=None, transport=None, load_key=None):
        self.ledger = ledger if ledger is not None else SpendLedger()
        self.transport = transport if transport is not None else requests.post
        if load_key is None:
            from experiments.mvp.reader.baselines import load_credentials
            load_key = load_credentials
        self.load_key = load_key

    def transcribe(self, path):
        path = Path(path)
        info = audio_info(path)
        self.load_key()
        key = os.environ.get('MODEL_API_KEY')
        if not key:
            raise TranscriptionError()
        reserve = Decimal(math.ceil(info['audio_seconds'] + 1)) * RATE
        reservation = uuid4().hex
        payload = {'model': MODEL, 'audioEncoding': 'WAV', 'mode': 'PUSH_TO_TALK'}
        common = {'reservation_id': reservation, 'purpose': PURPOSE, 'model': MODEL,
                  'endpoint': ENDPOINT, 'timestamp': datetime.now(timezone.utc).isoformat()}
        with self.ledger.locked():
            actual, held = self.ledger._totals([r for r in self.ledger._records() if r.get('purpose') == PURPOSE])
            if actual + held + reserve > ALLOCATION:
                raise SpendCapExceeded('Voice ASR allocation exhausted')
            self.ledger.append({**common, 'phase': 'reservation', 'cost_usd': str(reserve),
                                'cost_kind': 'conservative_hold', **info})
            start = time.perf_counter()
            measured = None
            outcome = 'unknown'
            result = None
            try:
                with path.open('rb') as audio:
                    response = self.transport(ENDPOINT,
                        headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'},
                        files={'request': (None, json.dumps(payload), 'application/json'),
                               'audio': ('brief.wav', audio, 'audio/wav')}, timeout=(15, 60))
                if 400 <= response.status_code < 500:
                    # Explicit rejection, no processed audio; never retry automatically.
                    measured, outcome = Decimal(0), 'rejected'
                elif response.status_code == 200:
                    data = response.json()
                    duration = data.get('audioDurationMs') if isinstance(data, dict) else None
                    transcript = data.get('transcript') if isinstance(data, dict) else None
                    if (isinstance(duration, (int, float)) and not isinstance(duration, bool)
                            and math.isfinite(duration) and 0 <= duration <= (math.ceil(info['audio_seconds'] + 1) * 1000)):
                        measured = Decimal(math.floor(duration / 1000)) * RATE
                        outcome = 'completed'
                        if isinstance(transcript, str) and transcript.strip() and key not in transcript:
                            session = data.get('sessionId')
                            session = session if isinstance(session, str) and key not in session else None
                            result = {'transcript': transcript, 'latency_s': time.perf_counter() - start,
                                'evidence': {**info, 'model': MODEL, 'session_id': session,
                                    'audio_duration_ms': duration, 'cost_usd': str(measured),
                                    'reservation_id': reservation, 'purpose': PURPOSE,
                                    'cost_basis': 'provider whole audio seconds × $0.18/hour; invoice unverified'}}
            except Exception:
                # The reservation is durable even if this process is interrupted.
                # Transport diagnostics and provider bodies are never saved.
                pass
            finally:
                self.ledger.append({**common, 'phase': 'settlement',
                    'cost_usd': str(reserve if measured is None else measured),
                    'cost_kind': 'conservative_hold' if measured is None else 'rate_derived',
                    'outcome': outcome})
            if result is None:
                raise TranscriptionError() from None
            return result
