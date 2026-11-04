"""Offline coverage for clip accounting, resume and the queued reader."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.voice.audio import wav_info, write_wav
from experiments.mvp.pipeline.radio import make_clips
from experiments.mvp.pipeline.radio.read_transcripts import read_transcripts
from experiments.mvp.pipeline.reader_final.reader import LABEL, MODEL_NAME, UNKNOWN
from experiments.mvp.reader.pipeline import ParserOutputError
from experiments.mvp.reader.schema import validate_answer


RULE = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 9}}
SENTENCE = "Leave bay 9 completely empty."
CANDIDATES = [{"sentence": SENTENCE, "rule": RULE}]
CONTEXT = {"bays": [7, 9]}


class Audio:
    def __init__(self):
        self.synthesized = []
        self.simulated = []

    def synthesize(self, sentence, language, destination):
        self.synthesized.append((sentence, language, Path(destination)))
        return write_wav(destination, [1000, -1000] * 12000)

    def radio(self, source, destination, *, seed):
        self.simulated.append((Path(source), Path(destination), seed))
        return write_wav(destination, [900, -1100] * 12000)


class Transcriber:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def transcribe(self, path):
        self.calls.append(Path(path))
        if self.fail:
            raise RuntimeError("fake transport failure")
        return {"transcript": SENTENCE, "latency_s": .125,
                "evidence": {"cost_usd": "0.00005", "audio_duration_ms": 1000,
                             "model": "muse-voice-transcribe-1.0"}}


def generate(directory, audio, transcriber, **kwargs):
    return make_clips.generate(directory, candidates=CANDIDATES, transcriber=transcriber,
                               synthesize_fn=audio.synthesize, radio_fn=audio.radio, **kwargs)


def test_resume_skips_completed_audio_and_transcripts(tmp_path):
    audio, transcriber = Audio(), Transcriber()
    first = generate(tmp_path, audio, transcriber)
    second = generate(tmp_path, audio, transcriber)

    assert len(first["clips"]) == 2
    assert second == first
    assert len(transcriber.calls) == 2
    assert len(audio.synthesized) == len(audio.simulated) == 1
    assert audio.simulated[0][2] == int(hashlib.sha256(SENTENCE.encode()).hexdigest()[:8], 16)
    assert Decimal(first["spend_usd"]) == Decimal("0.0001")
    for row in first["clips"]:
        path = Path(row["wav_path"])
        if not path.is_absolute():
            path = tmp_path / path
        info = wav_info(path)
        assert row["wav_sha256"] == info["sha256"]
        assert row["duration_s"] == info["duration_s"] == 1
        assert row["transcript"] == SENTENCE
        assert row["expected_rule"] == RULE
    assert json.loads((tmp_path / "manifest.json").read_text()) == first


def test_cost_cap_checked_before_call(tmp_path):
    audio, transcriber = Audio(), Transcriber()
    with pytest.raises(make_clips.CostCapExceeded):
        generate(tmp_path, audio, transcriber, cap_usd="0.00009")
    assert transcriber.calls == []


def test_cost_cap_includes_completed_calls_on_resume(tmp_path):
    audio, transcriber = Audio(), Transcriber()
    with pytest.raises(make_clips.CostCapExceeded):
        generate(tmp_path, audio, transcriber, cap_usd="0.0001")
    assert len(transcriber.calls) == 1
    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert Decimal(saved["spend_usd"]) == Decimal("0.00005")
    with pytest.raises(make_clips.CostCapExceeded):
        generate(tmp_path, audio, transcriber, cap_usd="0.0001")
    assert len(transcriber.calls) == 1


def test_two_failed_attempts_stop_and_keep_durable_spend(tmp_path):
    audio, transcriber = Audio(), Transcriber(fail=True)
    with pytest.raises(make_clips.ClipError):
        generate(tmp_path, audio, transcriber)
    assert len(transcriber.calls) == 2
    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert Decimal(saved["spend_usd"]) == Decimal("0.0002")
    assert [row["status"] for row in saved["attempts"]] == ["failed", "failed"]
    with pytest.raises(make_clips.ClipError):
        generate(tmp_path, audio, transcriber)
    assert len(transcriber.calls) == 2
    assert Decimal(json.loads((tmp_path / "manifest.json").read_text())["spend_usd"]) == Decimal("0.0002")


class Reader:
    known = {"adapter_sha256": "a" * 64, "decoder_sha256": "b" * 64,
             "domain_words_sha256": "c" * 64, "decoding_policy": "fake masked greedy"}

    def __init__(self, *, malformed=False, unknown=False):
        self.warmed = 0
        self.calls = []
        self.malformed, self.unknown = malformed, unknown

    def warm(self):
        self.warmed += 1

    def parse(self, sentence, context):
        self.calls.append((sentence, context))
        rule = RULE if sentence == "correct" else {**RULE, "constraint": {"type": "close_bay", "bay": 7}}
        evidence = {**self.known, "parser": "GlimmerParser", "model": MODEL_NAME,
                    "masked": True, "prompt_sha256": "d" * 64, "raw": json.dumps(rule)}
        if self.unknown:
            evidence.pop("adapter_sha256")
        if self.malformed:
            evidence["raw"] = "invalid JSON"
            raise ParserOutputError(evidence)
        return validate_answer(evidence["raw"], context), evidence


def manifest():
    return {"clips": [{"sentence": SENTENCE, "transcript": transcript, "expected_rule": RULE,
                       "condition": condition} for condition, transcript in
                      [("clean", "correct"), ("radio", "misread")]]}


def test_reader_scores_misread_and_warms_once():
    reader = Reader()
    result = read_transcripts(manifest(), reader=reader, context=CONTEXT)
    assert reader.warmed == 1
    assert reader.calls == [("correct", CONTEXT), ("misread", CONTEXT)]
    assert [row["binds"] for row in result["rows"]] == [True, False]
    for row in result["rows"]:
        assert row["label"] == LABEL
        assert row["evidence"]["adapter_sha256"] == reader.known["adapter_sha256"]
    assert result["summary"]["clean"]["binds"] == 1
    assert result["summary"]["radio"]["binds"] == 0


def test_malformed_reading_does_not_bind():
    reader = Reader(malformed=True)
    result = read_transcripts(manifest(), reader=reader, context=CONTEXT)
    assert reader.warmed == 1
    assert all(row["binds"] is False for row in result["rows"])
    assert all(row["evidence"]["raw"] == "invalid JSON" for row in result["rows"])


def test_reader_label_requires_adapter_evidence():
    result = read_transcripts(manifest(), reader=Reader(unknown=True), context=CONTEXT)
    assert all(row["label"] == UNKNOWN for row in result["rows"])
