from collections import Counter
import json
import math
from pathlib import Path

import pytest

from experiments.mvp.voice import audio, measure
from experiments.mvp.reader.schema import validate_answer


def test_frozen_validation_cohort_unique_and_stratified(tmp_path):
    cohort = measure.freeze_cohort(tmp_path)
    assert len(cohort["english"]) == len({row["sentence"] for row in cohort["english"]}) == 40
    assert Counter(row["category"] for row in cohort["english"]) == dict.fromkeys(measure.CATEGORIES, 10)
    assert all(row["split"] == "valid" for row in cohort["english"])
    assert len(cohort["thai"]["sentences"]) == 20
    assert measure.freeze_cohort(tmp_path) == measure.load_cohort(tmp_path)
    path = tmp_path / "cohort.json"
    value = json.loads(path.read_text())
    value["english"][0]["sentence"] = "altered"
    measure.save(path, value)
    with pytest.raises(ValueError, match="hash"):
        measure.load_cohort(tmp_path)
    with pytest.raises(ValueError, match="differs"):
        measure.freeze_cohort(tmp_path)


def test_radio_deterministic_and_correct_snr(tmp_path):
    clean = tmp_path / "clean.wav"
    audio.write_wav(clean, (7000 * math.sin(2 * math.pi * 1000 * i / audio.RATE) for i in range(2400)))
    first = audio.radio(clean, tmp_path / "radio-a.wav", seed=42)
    second = audio.radio(clean, tmp_path / "radio-b.wav", seed=42)
    third = audio.radio(clean, tmp_path / "radio-c.wav", seed=43)
    assert first["sha256"] == second["sha256"] != third["sha256"]
    assert first["duration_s"] == audio.wav_info(clean)["duration_s"]
    assert first["measured_pre_quantization_snr_db"] == pytest.approx(12)
    assert first["sha256"] != audio.wav_info(clean)["sha256"]


def test_silent_audio_rejected(tmp_path):
    with pytest.raises(ValueError, match="nonzero"):
        audio.write_wav(tmp_path / "silent.wav", [0] * 100)


def fixture_measurement(tmp_path):
    cohort = measure.freeze_cohort(tmp_path)
    sources = {row["id"]: row for row in cohort["english"]}
    sentences = {row["sentence"]: row for row in cohort["english"]}
    thai = {row["source_id"]: row["thai"] for row in cohort["thai"]["sentences"]}
    sentences.update({text: sources[source_id] for source_id, text in thai.items()})
    manifest = {"cohort_sha256": cohort["cohort_sha256"], "clips": {}}
    (tmp_path / "audio").mkdir()
    for language, ids, conditions in (("en", sources, ("clean", "radio")), ("th", thai, ("clean",))):
        for source_id in ids:
            for condition in conditions:
                path = tmp_path / "audio" / f"{language}-{source_id}-{condition}.wav"
                manifest["clips"][f"{language}/{source_id}/{condition}"] = audio.write_wav(path, [300, -300] * 24)
    measure.save(tmp_path / "audio-manifest.json", manifest)

    class Parser:
        calls = 0

        def parse(self, sentence, context):
            self.calls += 1
            row = sentences[sentence]
            assert context == row["context"]
            return validate_answer(json.dumps(row["expected"]), context), {"parser": "Fake", "raw": "do not persist this"}

    class Transcriber:
        calls = 0

        def transcribe(self, path):
            self.calls += 1
            language, source_id, condition = Path(path).stem.split("-", 1)[0], None, None
            source_id = "reader-" + Path(path).stem.split("reader-", 1)[1].rsplit("-", 1)[0]
            text = sources[source_id]["sentence"] if language == "en" else thai[source_id]
            return {"transcript": text, "latency_s": .1, "evidence": {"request_id": "fake", "headers": "private"}}

    return cohort, Parser(), Transcriber()


def test_full_pipeline_pairing_latency_and_restart_guard(tmp_path):
    _, parser, transcriber = fixture_measurement(tmp_path)
    report = measure.run_measurement(tmp_path, parser=parser, transcriber=transcriber)
    assert parser.calls == 160
    assert transcriber.calls == 100
    for group in ("en/clean", "en/radio", "th/clean"):
        data = report["groups"][group]
        n = data["planned_n"]
        assert data["completed_n"] == data["transcript_strict_exact_n"] == data["parse_gold_exact_n"] == n
        assert data["parse_vs_en_typed"]["exact_n"] == n
        assert data["latency"]["n"] == n
    assert report["groups"]["th/clean"]["parse_vs_th_typed"]["exact_n"] == 20
    assert report["groups"]["en/clean"]["wer"] == 0
    assert report["groups"]["th/clean"]["cer"] == 0
    measure.run_measurement(tmp_path, parser=parser, transcriber=transcriber)
    assert parser.calls == 160 and transcriber.calls == 100
    contents = "".join(path.read_text() for path in (tmp_path / "checkpoints").glob("*.json"))
    assert "do not persist this" not in contents and "private" not in contents


def test_unknown_outcome_not_retried_and_missing_pair_not_success(tmp_path):
    cohort, parser, transcriber = fixture_measurement(tmp_path)
    source_id = cohort["english"][0]["id"]
    measure.save(tmp_path / "checkpoints" / f"en-{source_id}-typed.json",
                 {"cohort_sha256": cohort["cohort_sha256"], "language": "en", "condition": "typed",
                  "source_id": source_id, "status": "pending"})
    report = measure.run_measurement(tmp_path, parser=parser, transcriber=transcriber)
    assert parser.calls == 159
    assert report["groups"]["en/clean"]["parse_vs_en_typed"]["paired_n"] == 39
    assert report["groups"]["en/clean"]["parse_vs_en_typed"]["exact_rate_all_planned"] == 39 / 40
    assert report["groups"]["en/typed"]["status_counts"]["pending"] == 1


def test_transcriber_failure_is_safe_and_counted(tmp_path):
    _, parser, _ = fixture_measurement(tmp_path)

    class Broken:
        def transcribe(self, path):
            raise RuntimeError("SECRET provider response")

    report = measure.run_measurement(tmp_path, parser=parser, transcriber=Broken())
    assert report["groups"]["en/clean"]["completed_n"] == 0
    assert report["groups"]["en/clean"]["parse_vs_en_typed"]["exact_rate_all_planned"] == 0
    assert report["groups"]["en/clean"]["status_counts"] == {"error": 40}
    contents = "".join(path.read_text() for path in (tmp_path / "checkpoints").glob("*.json"))
    assert "SECRET" not in contents


def test_quality_metrics_preserve_strict_metric():
    assert measure.normalized("Keep BAY ten.") == measure.normalized("keep bay ten")
    assert "Keep BAY ten." != "keep bay ten"
    assert measure.edit_distance("abc", "axc") == 1
    assert measure.edit_distance(["bay", "ten"], ["bay", "two"]) == 1
    assert measure.latency_summary([1, 3, 2])["p95_s"] == 3
