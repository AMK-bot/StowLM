"""Live stage adapters, exercised only with fresh synthetic data and injected calls."""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import wave

import pytest

from app import functional_models as models


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch, tmp_path):
    """A missing injection must fail before any network or MLX operation."""
    import requests
    import socket
    import urllib.request
    from llm.spark.spend import SpendLedger

    def managed_forbidden(*args, **kwargs):
        raise AssertionError("Tests must never touch the managed project spend ledger")

    monkeypatch.setattr(SpendLedger, "_compute_text", managed_forbidden)
    monkeypatch.setattr(SpendLedger, "_sync_compute", managed_forbidden)

    def forbidden(*args, **kwargs):
        raise AssertionError("A test attempted a real network call")

    monkeypatch.setattr(requests, "post", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)
    monkeypatch.delenv("STOWLM_ACCEPT_PAID_CALLS", raising=False)
    monkeypatch.setenv("STOWLM_FUNCTIONAL_LAUNCH_ID", "synthetic-test-launch")
    monkeypatch.setenv("STOWLM_FUNCTIONAL_CAP_USD", "10.0")
    monkeypatch.setattr(models, "_FUNCTIONAL_SPEND_ROOT", tmp_path / "functional-spend", raising=False)
    grants = []
    monkeypatch.setattr(models, "_test_grants", grants, raising=False)
    if hasattr(models, "grant_call"):
        real_grant_call = models.grant_call

        def capture_grant(*args, **kwargs):
            grant = real_grant_call(*args, **kwargs)
            grants.append(grant)
            return grant

        monkeypatch.setattr(models, "grant_call", capture_grant)


def request_records():
    """Read completed fake request ledgers; grants retain their own immutable paths."""
    return [json.loads(line) for grant in models._test_grants
            for line in Path(grant.ledger_path).read_text().splitlines() if line]


def request_ledger_text():
    return "".join(Path(grant.ledger_path).read_text() for grant in models._test_grants)


@pytest.fixture
def installed(monkeypatch, tmp_path):
    model = tmp_path / "model"
    adapter = tmp_path / "adapter"
    model.mkdir()
    adapter.mkdir()
    (model / "config.json").write_text('{}')
    (model / "tokenizer_config.json").write_text('{}')
    (model / "tokenizer.json").write_text('{}')
    (model / "model.safetensors").write_bytes(b"synthetic base weights, never loaded")
    (adapter / "adapter_config.json").write_text('{}')
    weights = adapter / "adapters.safetensors"
    weights.write_bytes(b"synthetic adapter, never loaded")
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    adoption = tmp_path / "adoption.json"
    adoption.write_text(json.dumps({"adopted": {"path": str(adapter), "adapter_sha256": digest}}))
    monkeypatch.setattr(models, "_ADOPTION_RECORD", adoption)
    monkeypatch.setattr(models, "_DEFAULT_MODEL_DIR", model)
    monkeypatch.setattr(models.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-test-token-never-return")
    monkeypatch.setenv("SPARK_CREDIT_READY", "1")
    return model, adapter, digest


def status(installed):
    model, adapter, _ = installed
    return models.availability(adapter_dir=adapter, model_dir=model)


def test_availability_uses_presence_and_metadata_only(installed):
    view = status(installed)
    assert all(view[stage]["available"] for stage in ("spark", "voice", "reader"))
    assert "synthetic-test-token" not in json.dumps(view)
    assert view["spark"]["model"] == "muse-spark-1.3"
    assert view["voice"]["formats"] == ["wav"]
    assert view["reader"]["adapter_sha256"] == installed[2]
    assert view["reader"]["adopted"] is True
    assert view["reader"]["decode"] == "schema mask (v3 decoder)"
    assert view["reader"]["loaded"] is False


@pytest.mark.parametrize("stage", ["spark", "voice"])
def test_missing_key_is_plain_unavailable(installed, monkeypatch, stage):
    monkeypatch.delenv("MODEL_API_KEY")
    value = status(installed)[stage]
    assert value["available"] is False
    assert value["reason"] == "No Meta Model API key: enter yours in the app, or put MODEL_API_KEY in .env (see .env.example)."


@pytest.mark.parametrize("stage", ["spark", "voice"])
def test_credit_gate_is_required(installed, monkeypatch, stage):
    monkeypatch.setenv("SPARK_CREDIT_READY", "0")
    value = status(installed)[stage]
    assert value["available"] is False
    assert "paid calls" in value["reason"].lower()


def test_missing_model_weights_is_plain_unavailable(installed):
    (installed[0] / "model.safetensors").unlink()
    view = status(installed)["reader"]
    assert view["available"] is False
    assert "model" in view["reason"].lower() or "weights" in view["reason"].lower()


def test_missing_adapter_weights_is_plain_unavailable(installed):
    (installed[1] / "adapters.safetensors").unlink()
    value = status(installed)["reader"]
    assert value["available"] is False
    assert "no adapters.safetensors" in value["reason"]


def test_missing_mlx_is_plain_unavailable(installed, monkeypatch):
    monkeypatch.setattr(models.importlib.util, "find_spec", lambda name: None)
    value = status(installed)["reader"]
    assert value["available"] is False
    assert "mlx_lm isn't installed" in value["reason"]


def test_adopted_adapter_hash_must_match(installed):
    (installed[1] / "adapters.safetensors").write_bytes(b"changed synthetic adapter")
    value = models.availability(model_dir=installed[0])["reader"]
    assert value["available"] is False
    assert "sha" in value["reason"].lower() or "hash" in value["reason"].lower()
    assert value["adopted"] is False


def test_missing_adoption_record_is_plain_unavailable(installed, monkeypatch, tmp_path):
    monkeypatch.setattr(models, "_ADOPTION_RECORD", tmp_path / "absent.json")
    value = models.availability(model_dir=installed[0])["reader"]
    assert value["available"] is False
    assert "adopt" in value["reason"].lower()


def test_custom_adapter_carries_hash_without_claiming_adoption(installed, tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "adapters.safetensors").write_bytes(b"other synthetic adapter")
    (custom / "adapter_config.json").write_text('{}')
    value = models.availability(adapter_dir=custom, model_dir=installed[0])["reader"]
    assert value["available"] is True
    assert value["adopted"] is False
    assert value["adapter_sha256"] == hashlib.sha256((custom / "adapters.safetensors").read_bytes()).hexdigest()


def test_unavailable_stage_raises_before_intake_or_voice_calls(tmp_path):
    with pytest.raises(models.Unavailable) as intake:
        models.read_papers([], tmp_path / "missing.json")
    assert intake.value.stage == "spark"
    assert intake.value.reason == str(intake.value)
    assert "MODEL_API_KEY" in intake.value.reason
    with pytest.raises(models.Unavailable) as voice:
        models.transcribe(tmp_path / "missing.wav")
    assert voice.value.stage == "voice"
    assert "MODEL_API_KEY" in voice.value.reason


class FakeSparkClient:
    def __init__(self):
        self.calls = []

    def complete_documents(self, wire, **kwargs):
        from llm.spark.client import SparkResponse
        from llm.spark.spend import Usage
        self.calls.append((deepcopy(wire), kwargs))
        raw = {"schema_version": "stowlm.intake-interpretation/7", "header_maps": [],
               "observations": [], "exceptions": [], "notes": []}
        return SparkResponse(json.dumps(raw), Usage(100, 20), Decimal("0.0123"), "synthetic-request")


def test_intake_and_answers_roundtrip_through_real_seam(installed, tmp_path):
    from stowage.model.fixtures import build_instance
    base = build_instance()
    base_path = tmp_path / "base.json"
    base_path.write_text(base.model_dump_json())
    paper = tmp_path / "cargo.txt"
    paper.write_text("SYNTHETIC cargo papers: all details need planner review.")
    client = FakeSparkClient()
    initial = models.read_papers([{"name": paper.name, "media_type": "text/plain", "path": str(paper)}],
                                 base_path, client=client)
    assert len(client.calls) == 1
    assert "SYNTHETIC cargo papers" in json.dumps(client.calls[0][0])
    assert initial["mode"] == "live" and initial["model"] == "muse-spark-1.3"
    assert initial["boxes"] == len(base.load_list)
    assert initial["complete"] is False and initial["questions"]
    assert float(initial["cost_usd"]) == pytest.approx(0.0123)
    assert len(initial["receipt_sha256"]) == 64
    assert all({"id", "text", "field", "box"} <= q.keys() for q in initial["questions"])
    # A new worker can reconstruct the pipeline's complete intake receipt from JSON.
    restored = json.loads(json.dumps(initial))
    question = next(q for q in restored["raw"]["questions"] if q["row"] is not None and q["field"] is not None)
    row_id = restored["rows"][question["row"]]["id"]
    final = models.answer_papers(restored, {question["id"]: "shut_out"})
    assert final["mode"] == "live"
    assert final["boxes"] == len(base.load_list) - 1
    assert row_id not in {row["id"] for row in final["rows"]}
    assert len(final["raw"]["records"]) == len(initial["raw"]["records"]) + 1
    assert float(final["cost_usd"]) == pytest.approx(0.0123)
    assert len(client.calls) == 1
    json.dumps(final)


def test_answers_reject_unknown_question(installed, tmp_path):
    from stowage.model.fixtures import build_instance
    path = tmp_path / "base.json"
    path.write_text(build_instance().model_dump_json())
    document = tmp_path / "synthetic.txt"
    document.write_text("SYNTHETIC document")
    view = models.read_papers([{"name": document.name, "media_type": "text/plain", "path": document}],
                              path, client=FakeSparkClient())
    with pytest.raises(ValueError):
        models.answer_papers(json.loads(json.dumps(view)), {"unknown-question": "yes"})


class FakeLedger:
    def __init__(self):
        self.records = []

    @contextmanager
    def locked(self):
        yield self

    def _records(self):
        return self.records

    def _totals(self, records):
        return Decimal(0), Decimal(0)

    def append(self, record):
        self.records.append(deepcopy(record))


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 16000)
    return path


def test_voice_uses_real_transcriber_with_fake_transport_and_ledger(installed, audio):
    from experiments.mvp.voice.asr import MuseTranscriber
    ledger = FakeLedger()
    calls = []

    class Response:
        status_code = 200
        def json(self):
            return {"transcript": "Close bay one.", "audioDurationMs": 1000, "sessionId": "synthetic-session"}

    def transport(url, **kwargs):
        calls.append((url, kwargs["files"]["audio"][0], json.loads(kwargs["files"]["request"][1])))
        return Response()

    value = models.transcribe(audio, transcriber=MuseTranscriber(ledger=ledger, transport=transport,
                                                                 load_key=lambda: None))
    assert len(calls) == 1 and calls[0][2]["audioEncoding"] == "WAV"
    assert value["mode"] == "live" and value["transcript"] == "Close bay one."
    assert value["model"] == "muse-voice-transcribe-1.0"
    assert value["audio_seconds"] == 1
    assert value["audio_sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert float(value["cost_usd"]) == pytest.approx(0.00005)
    assert value["wall_s"] >= 0
    assert [record["phase"] for record in ledger.records] == ["reservation", "settlement"]
    assert ledger.records[-1]["outcome"] == "completed"
    assert "synthetic-test-token" not in json.dumps(value)


def test_voice_failure_has_safe_plain_reason(installed, audio):
    from experiments.mvp.voice.asr import MuseTranscriber

    def failure(*args, **kwargs):
        raise RuntimeError("private provider body synthetic-test-token")

    transcriber = MuseTranscriber(ledger=FakeLedger(), transport=failure, load_key=lambda: None)
    with pytest.raises(models.Unavailable) as failure:
        models.transcribe(audio, transcriber=transcriber)
    assert failure.value.stage == "voice"
    assert "private provider body" not in failure.value.reason
    assert "synthetic-test-token" not in failure.value.reason


CONTEXT = {"id": "synthetic-call", "bays": [1, 2, 3], "max_tiers": {"hold": 2, "deck": 2},
           "has_accommodation": False, "hatch_covers": ["synthetic-hatch"], "ports": ["THBKK"]}


@pytest.mark.parametrize("answer, expected", [
    ({"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}, "constraint_ready"),
    ({"schema_version": "stowlm.reader/2", "refusal": {"refused": True, "code": "unsupported", "reason": "Unsupported sentence."}}, "refused"),
    ("garbage and private raw model output", "invalid"),
    ({"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 999}}, "invalid"),
])
def test_reader_validates_injected_generation(installed, answer, expected):
    calls = []
    raw = json.dumps(answer) if isinstance(answer, dict) else answer

    def generate(sentence, context):
        calls.append((sentence, deepcopy(context)))
        return raw

    value = models.Reader(adapter_dir=installed[1], model_dir=installed[0], generate=generate).read(
        "Close bay one.", deepcopy(CONTEXT))
    assert calls == [("Close bay one.", CONTEXT)]
    assert value["mode"] == "live" and value["status"] == expected
    assert value["adopted"] is True
    assert value["sentence"] == "Close bay one."
    assert value["evidence"]["adapter_sha256"] == installed[2]
    assert value["evidence"]["model"] == "muse-glimmer-30b"
    assert value["decode"] == "schema mask (v3 decoder)"
    assert value["wall_s"] >= 0
    assert value["cold_load_s"] is None
    assert "raw" not in value["evidence"]
    if expected == "constraint_ready":
        assert value["answer"] == answer
        assert value["rule_text"] == "Nothing loads into bay 01."
        assert value["refusal"] is None
    elif expected == "refused":
        assert value["answer"] == answer
        assert value["refusal"] == answer["refusal"]
        assert value["rule_text"] is None
    else:
        assert value["answer"] is None
        assert value["rule_text"] is None and value["refusal"] is None
        assert "private raw model output" not in json.dumps(value)


def test_reader_unavailable_never_calls_generator(installed):
    (installed[1] / "adapters.safetensors").unlink()
    with pytest.raises(models.Unavailable) as failure:
        models.Reader(adapter_dir=installed[1], model_dir=installed[0]).read("Close bay one.", CONTEXT)
    assert failure.value.stage == "reader"
    assert "no adapters.safetensors" in failure.value.reason


def test_module_has_no_replay_import_or_input_path():
    source = Path(models.__file__).read_text()
    for prohibited in ("RecordedClient", "RecordedParser", "fixtures/", "fixtures\\", "ui/cache"):
        assert prohibited not in source


@pytest.mark.parametrize("directory, filename, reason", [
    (0, "config.json", "config.json"),
    (1, "adapter_config.json", "adapter_config.json"),
])
def test_reader_requires_configuration_files(installed, directory, filename, reason):
    (installed[directory] / filename).unlink()
    view = status(installed)["reader"]
    assert view["available"] is False
    assert reason in view["reason"]


def test_reader_requires_every_indexed_weight_shard(installed):
    model = installed[0]
    (model / "model.safetensors.index.json").write_text(json.dumps({
        "weight_map": {"first": "model.safetensors", "second": "missing-00002.safetensors"}}))
    view = status(installed)["reader"]
    assert view["available"] is False
    assert "shard" in view["reason"].lower()


def test_default_voice_uses_environment_without_credential_loader(installed, audio, monkeypatch):
    from experiments.mvp.voice import asr
    calls = []

    class Transcriber:
        def __init__(self, *, load_key, ledger):
            assert ledger.path == models._test_grants[-1].ledger_path
            calls.append(load_key())

        def transcribe(self, path):
            assert path == audio
            return {"transcript": "Synthetic transcript.", "evidence": {
                "model": "muse-voice-transcribe-1.0", "cost_usd": "0.00005"}}

    monkeypatch.setattr(asr, "MuseTranscriber", Transcriber)
    view = models.transcribe(audio)
    assert calls == [None]
    assert view["transcript"] == "Synthetic transcript."


def test_default_reader_loads_once_serializes_decode_and_separates_cold_time(installed, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import ModuleType
    import sys
    import threading
    import time
    from app import brief

    monkeypatch.setattr(models, "_READER_STATES", {})
    loads, decodes = [], []
    guard = threading.Lock()
    ready = threading.Barrier(2)
    clock = {"elapsed": 0.0, "active": 0, "max_active": 0}
    marker_model, marker_tokenizer = object(), object()

    def load(model_dir, *, adapter_path):
        loads.append((model_dir, adapter_path))
        time.sleep(0.01)
        with guard:
            clock["elapsed"] += 10.0
        return marker_model, marker_tokenizer

    def masked(model, tokenizer, sentence, context):
        assert model is marker_model and tokenizer is marker_tokenizer
        assert context == CONTEXT
        with guard:
            clock["active"] += 1
            clock["max_active"] = max(clock["active"], clock["max_active"])
        time.sleep(0.02)
        with guard:
            clock["elapsed"] += 2.0
            clock["active"] -= 1
        decodes.append(sentence)
        return json.dumps({"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}})

    module = ModuleType("mlx_lm")
    module.load = load
    monkeypatch.setitem(sys.modules, "mlx_lm", module)
    monkeypatch.setattr(brief, "masked_answer", masked)
    monkeypatch.setattr(models, "perf_counter", lambda: clock["elapsed"])

    def read(sentence):
        reader = models.Reader(adapter_dir=installed[1], model_dir=installed[0])
        ready.wait(timeout=5)
        return reader.read(sentence, deepcopy(CONTEXT))

    assert not status(installed)["reader"]["loaded"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read, "Close bay one.")
        second = pool.submit(read, "Keep bay one empty.")
        views = [first.result(timeout=5), second.result(timeout=5)]
    assert len(loads) == 1
    assert sorted(decodes) == ["Close bay one.", "Keep bay one empty."]
    assert clock["max_active"] == 1
    assert all(view["status"] == "constraint_ready" and view["wall_s"] == 2.0 for view in views)
    assert sorted(view["cold_load_s"] for view in views if view["cold_load_s"] is not None) == [10.0]
    assert status(installed)["reader"]["loaded"] is True


def test_intake_rejects_generated_mode_from_injected_client(installed, tmp_path):
    from stowage.model.fixtures import build_instance
    base = tmp_path / "base.json"
    base.write_text(build_instance().model_dump_json())
    document = tmp_path / "synthetic.txt"
    document.write_text("SYNTHETIC paper")

    class Client:
        def complete_documents(self, *args, **kwargs):
            return {"schema_version": "stowlm.intake-interpretation/7", "header_maps": [],
                    "observations": [], "exceptions": [], "notes": []}

    with pytest.raises(models.ModelError) as failure:
        models.read_papers([{"name": document.name, "media_type": "text/plain", "path": document}],
                           base, client=Client())
    assert failure.value.stage == "spark"
    assert "live reading" in failure.value.reason


@pytest.mark.parametrize("filename", ["tokenizer.json", "tokenizer_config.json"])
def test_reader_requires_tokenizer_files(installed, filename):
    (installed[0] / filename).unlink()
    view = status(installed)["reader"]
    assert view["available"] is False
    assert "tokenizer" in view["reason"].lower()


def test_reader_invalid_weight_map_is_plain_unavailable(installed):
    (installed[0] / "model.safetensors.index.json").write_text(json.dumps({"weight_map": []}))
    view = status(installed)["reader"]
    assert view["available"] is False
    assert "index" in view["reason"].lower()


def test_voice_invalid_wav_is_a_plain_input_error(installed, tmp_path):
    path = tmp_path / "invalid.wav"
    path.write_bytes(b"synthetic invalid WAV")
    with pytest.raises(ValueError) as failure:
        models.transcribe(path)
    assert "WAV" in str(failure.value)


def test_intake_preserves_host_metadata_at_pipeline_seam(installed, tmp_path, monkeypatch):
    from experiments.mvp.pipeline import intake
    from stowage.model.fixtures import build_instance
    base = tmp_path / "base.json"
    base.write_text(build_instance().model_dump_json())
    paper = tmp_path / "booking-confirmation.txt"
    paper.write_text("SYNTHETIC DOCUMENT\nBooking reference SYNTHETIC-BOOKING.")
    metadata = {"id": "host-booking-id", "role": "booking", "booking_ref": "SYNTHETIC-BOOKING",
                "complete_booking_list": False, "synthetic": True}
    document = {"name": "booking-confirmation.txt", "media_type": "text/plain", "path": paper, **metadata}
    original = dict(document)
    received = []
    real_read = intake.read_documents

    def read_documents(documents, *, base_instance, client):
        received.extend(deepcopy(documents))
        return real_read(documents, base_instance=base_instance, client=client)

    monkeypatch.setattr(intake, "read_documents", read_documents)
    view = models.read_papers([document], base, client=FakeSparkClient())
    assert len(received) == 1
    assert {key: received[0][key] for key in metadata} == metadata
    assert received[0]["path"] == str(paper)
    assert received[0]["mime_type"] == "text/plain"
    assert document == original
    context = json.loads(view["raw"]["records"][-1]["notes"])["intake_context"]
    assert {key: context["documents"][0][key] for key in metadata} == metadata


@pytest.mark.parametrize("role", [None, "not_a_host_role"])
def test_missing_or_unknown_document_role_stays_unspecified(installed, tmp_path, role):
    from stowage.model.fixtures import build_instance
    base = tmp_path / "base.json"
    base.write_text(build_instance().model_dump_json())
    paper = tmp_path / "booking.txt"
    paper.write_text("SYNTHETIC DOCUMENT\nThis filename cannot grant booking authority.")
    item = {"name": paper.name, "media_type": "text/plain", "path": paper,
            "booking_ref": None, "complete_booking_list": False, "synthetic": False}
    if role is not None:
        item["role"] = role
    view = models.read_papers([item], base, client=FakeSparkClient())
    context = json.loads(view["raw"]["records"][-1]["notes"])["intake_context"]
    actual = context["documents"][0]
    assert actual["role"] == "unspecified"
    assert actual["booking_ref"] is None
    assert actual["complete_booking_list"] is False
    assert actual["synthetic"] is False
    assert any(q["field"] == "document:booking" and q["reason"] == "missing_document"
               for q in view["raw"]["questions"])


def test_default_document_ids_follow_file_stems_and_survive_reordering(installed, tmp_path):
    from stowage.model.fixtures import build_instance
    base = tmp_path / "base.json"
    base.write_text(build_instance().model_dump_json())
    documents = []
    for stem in ("synthetic-booking", "synthetic-vgm", "synthetic-gate"):
        paper = tmp_path / f"{stem}.txt"
        paper.write_text("SYNTHETIC DOCUMENT\nNo declared cargo facts.")
        documents.append({"name": paper.name, "media_type": "text/plain", "path": paper})
    documents[-1]["id"] = "supplied-gate-id"
    documents[0]["name"] = "display-name-does-not-control-id.txt"

    def ids(items):
        view = models.read_papers(items, base, client=FakeSparkClient())
        context = json.loads(view["raw"]["records"][-1]["notes"])["intake_context"]
        return {item["path"]: item["id"] for item in context["documents"]}

    expected = {str(item["path"]): item.get("id", item["path"].stem) for item in documents}
    assert ids(documents) == expected
    assert ids(list(reversed(documents))) == expected


@pytest.mark.parametrize("suffix", [".txt", ".md", ""])
def test_all_host_authority_roles_avoid_missing_document_questions(installed, tmp_path, suffix):
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    base = tmp_path / "base.json"
    base.write_text(instance.model_dump_json())
    booking_ref = "SYNTHETIC-BOOKING-001"
    roles = ("booking", "shipping_instructions", "vgm", "dg_declaration", "gate_slip", "reefer_list", "oog_list")
    members = {
        "booking": list(instance.load_list),
        "reefer_list": [row.id for row in instance.containers if row.reefer],
        "dg_declaration": [row.id for row in instance.containers if row.dg is not None],
        "oog_list": [row.id for row in instance.containers if row.oog is not None],
    }
    documents = []
    for role in roles:
        paper = tmp_path / f"{role}{suffix}"
        paper.write_text("SYNTHETIC DOCUMENT\nBooking reference " + booking_ref + "\n" +
                         "\n".join(members.get(role, list(instance.load_list))))
        documents.append({"name": paper.name, "media_type": "text/plain", "path": paper,
                          "role": role, "booking_ref": booking_ref,
                          "complete_booking_list": role in members, "synthetic": True})
    view = models.read_papers(documents, base, client=FakeSparkClient())
    reasons = {question["reason"] for question in view["raw"]["questions"]}
    assert not reasons & {"missing_document", "incomplete_document_scope", "document_scope_mismatch",
                          "unverified_booking_scope", "list_membership_conflict"}
    # Having all forms does not turn absent model readings into accepted cargo facts.
    assert view["complete"] is False
    assert view["questions"]
    assert view["mode"] == "live"


@pytest.mark.parametrize("merge", [False, True])
def test_intake_answers_resume_from_json_in_a_fresh_guarded_process(installed, tmp_path, booking_views, merge):
    import os
    import subprocess
    from stowage.model.fixtures import build_instance
    base = tmp_path / "base.json"
    base.write_text(build_instance().model_dump_json())
    paper = tmp_path / "synthetic-paper.txt"
    paper.write_text("SYNTHETIC DOCUMENT\nCargo declarations need review.")
    initial = models.read_papers([{"name": paper.name, "media_type": "text/plain", "path": paper}],
                                 base, client=FakeSparkClient())
    if merge:
        initial = models.merge_intake_views(booking_views)
        second_booking = {row["id"] for row in booking_views[1]["rows"]}
        question = next(q for q in initial["questions"] if q["box"] in second_booking)
        removed = question["box"]
    else:
        question = next(q for q in initial["raw"]["questions"] if q["row"] is not None and q["field"] is not None)
        removed = initial["rows"][question["row"]]["id"]
    receipt = tmp_path / "intake.json"
    receipt.write_text(json.dumps(initial))
    answers = {question["id"]: "shut_out"}
    child = '''
import importlib.abc
import json
import os
from pathlib import Path
import socket
import sys
import urllib.request
import requests

def forbidden(*args, **kwargs):
    raise AssertionError("Fresh answer worker attempted a network call")
socket.create_connection = forbidden
socket.socket.connect = forbidden
urllib.request.urlopen = forbidden
requests.post = forbidden

class NoModelImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mlx', 'mlx_lm'}:
            raise AssertionError("Fresh answer worker attempted a model import")
sys.meta_path.insert(0, NoModelImports())
assert 'MODEL_API_KEY' not in os.environ
assert 'SPARK_CREDIT_READY' not in os.environ
from app.functional_models import answer_papers
intake_view = json.loads(Path(sys.argv[1]).read_text())
result = answer_papers(intake_view, json.loads(sys.stdin.read()))
print(json.dumps({'pid': os.getpid(), 'result': result}))
'''
    env = os.environ.copy()
    env.pop("MODEL_API_KEY", None)
    env.pop("SPARK_CREDIT_READY", None)
    env.update(UV_OFFLINE="1", UV_CACHE_DIR="/private/tmp/fa-models-uv-cache")
    completed = subprocess.run(["uv", "run", "--offline", "python", "-c", child, str(receipt)],
                               input=json.dumps(answers), text=True, capture_output=True,
                               cwd=Path(__file__).resolve().parents[2], env=env, timeout=20)
    assert completed.returncode == 0, completed.stderr
    output = json.loads(completed.stdout)
    assert output["pid"] != os.getpid()
    final = output["result"]
    assert final["boxes"] == initial["boxes"] - 1
    assert removed not in {row["id"] for row in final["rows"]}
    if merge:
        before = initial["raw"]["bookings"]
        after = final["raw"]["bookings"]
        assert before[0]["view"] == after[0]["view"]
        assert len(after[1]["view"]["raw"]["records"]) == len(before[1]["view"]["raw"]["records"]) + 1
    else:
        assert len(final["raw"]["records"]) == len(initial["raw"]["records"]) + 1
    assert final["mode"] == "live"
    assert float(final["cost_usd"]) == pytest.approx(float(initial["cost_usd"]))
    assert receipt.read_text() == json.dumps(initial)


@pytest.mark.parametrize("identifier", [None, "", "  ", 17])
def test_document_id_must_be_a_nonempty_string(installed, tmp_path, identifier):
    paper = tmp_path / "synthetic.txt"
    paper.write_text("SYNTHETIC DOCUMENT")
    with pytest.raises(ValueError, match="id"):
        models.read_papers([{"name": paper.name, "media_type": "text/plain", "path": paper,
                            "id": identifier}], tmp_path / "unused-base.json", client=FakeSparkClient())


def test_duplicate_file_stems_require_explicit_ids(installed, tmp_path):
    documents = []
    for folder in ("one", "two"):
        directory = tmp_path / folder
        directory.mkdir()
        paper = directory / "synthetic.txt"
        paper.write_text("SYNTHETIC DOCUMENT")
        documents.append({"name": paper.name, "media_type": "text/plain", "path": paper})
    with pytest.raises(ValueError) as failure:
        models.read_papers(documents, tmp_path / "unused-base.json", client=FakeSparkClient())
    assert "id" in str(failure.value).lower()
    assert "explicit" in str(failure.value).lower()


@pytest.fixture
def intake_input(tmp_path):
    from stowage.model.fixtures import build_instance
    base = tmp_path / "base.json"
    base.write_text(build_instance().model_dump_json())
    paper = tmp_path / "synthetic.txt"
    paper.write_text("SYNTHETIC DOCUMENT\nCargo facts need planner review.")
    return [{"name": paper.name, "media_type": "text/plain", "path": paper}], base


@pytest.mark.parametrize("failure_kind, fragments", [
    ("output_limit", ("output limit", "24576", "nothing")),
    ("content_filter", ("content", "filter", "nothing")),
    ("http", ("http", "429")),
    ("timeout", ("timed out",)),
    ("protocol", ("protocol",)),
    ("transport", ("transport",)),
    ("spend_cap", ("spend", "cap")),
    ("ledger", ("ledger",)),
])
def test_spark_call_failures_are_safe_model_errors(intake_input, failure_kind, fragments):
    from llm.spark.client import SparkHTTPError, SparkIncompleteError, SparkProtocolError
    from llm.spark.spend import SpendCapExceeded, SpendError, Usage
    failures = {
        "output_limit": SparkIncompleteError("max_output_tokens", Usage(100, 24576), Decimal("0.115"), "synthetic-request"),
        "content_filter": SparkIncompleteError("content_filter", Usage(100, 20), Decimal("0.001"), "synthetic-request"),
        "http": SparkHTTPError(429),
        "timeout": TimeoutError("private provider diagnostic"),
        "protocol": SparkProtocolError("private provider diagnostic"),
        "transport": RuntimeError("private provider diagnostic"),
        "spend_cap": SpendCapExceeded("private spend details"),
        "ledger": SpendError("private ledger details"),
    }
    calls = []

    class Client:
        def complete_documents(self, *args, **kwargs):
            calls.append(True)
            raise failures[failure_kind]

    with pytest.raises(models.ModelError) as failure:
        models.read_papers(*intake_input, client=Client())
    assert calls == [True]
    assert failure.value.stage == "spark"
    assert failure.value.reason == str(failure.value)
    assert not isinstance(failure.value, models.Unavailable)
    reason = failure.value.reason.lower().replace(",", "")
    for fragment in fragments:
        assert fragment in reason
    assert "private" not in reason


def test_spark_schema_rejected_result_is_a_model_error(intake_input, monkeypatch):
    from experiments.mvp.pipeline import intake
    from llm.spark.client import SparkResponse
    from llm.spark.spend import Usage
    calls, statuses = [], []
    real_read = intake.read_documents

    class Client:
        def complete_documents(self, *args, **kwargs):
            calls.append(True)
            return SparkResponse("private invalid JSON", Usage(100, 20), Decimal("0.001"), "synthetic-request")

    def read_documents(*args, **kwargs):
        result = real_read(*args, **kwargs)
        statuses.append(result.status)
        return result

    monkeypatch.setattr(intake, "read_documents", read_documents)
    with pytest.raises(models.ModelError) as failure:
        models.read_papers(*intake_input, client=Client())
    assert statuses == ["rejected"]
    assert len(calls) == 2
    assert failure.value.stage == "spark"
    assert "schema" in failure.value.reason.lower()
    assert "private" not in failure.value.reason.lower()
    assert not isinstance(failure.value, models.Unavailable)


def test_spark_schema_rejection_exception_is_a_model_error(intake_input, monkeypatch):
    from experiments.mvp.pipeline import intake
    from experiments.mvp.pipeline.intake.interpretation import SchemaRejection
    error = SchemaRejection([{
        "validation_error": "private schema diagnostic", "wall_s": 0.01, "cost_usd": 0.001,
        "request_id": "synthetic-request", "usage": {}, "raw_text": "private invalid JSON",
        "prompt_sha256": "a" * 64, "documents_sha256": "b" * 64,
    }])

    def read_documents(*args, **kwargs):
        raise error

    monkeypatch.setattr(intake, "read_documents", read_documents)
    with pytest.raises(models.ModelError) as failure:
        models.read_papers(*intake_input, client=FakeSparkClient())
    assert failure.value.stage == "spark"
    assert "schema" in failure.value.reason.lower()
    assert "private" not in failure.value.reason.lower()


@pytest.mark.parametrize("missing", ["key", "credit_gate"])
def test_only_missing_environment_gates_are_spark_unavailable(intake_input, monkeypatch, missing):
    if missing == "credit_gate":
        monkeypatch.setenv("MODEL_API_KEY", "synthetic-key")
    with pytest.raises(models.Unavailable) as failure:
        models.read_papers(*intake_input)
    assert failure.value.stage == "spark"
    if missing == "key":
        assert "MODEL_API_KEY" in failure.value.reason
    else:
        assert "paid calls" in failure.value.reason.lower()


@pytest.fixture
def booking_views(tmp_path):
    from stowage.model.fixtures import build_instance
    original = build_instance().model_dump(mode="json")
    views = []
    for index, members in enumerate((original["load_list"][:2], original["load_list"][2:]), 1):
        base = deepcopy(original)
        base["id"] = f"synthetic-booking-{index}"
        base["load_list"] = members
        path = tmp_path / f"base-{index}.json"
        path.write_text(json.dumps(base))
        booking = tmp_path / f"booking-{index}.txt"
        booking.write_text("SYNTHETIC DOCUMENT\nBooking roster\n" + "\n".join(members))
        view = models.read_papers([{"name": booking.name, "media_type": "text/plain", "path": booking,
                                   "role": "booking", "booking_ref": f"SYNTHETIC-BOOKING-{index}",
                                   "synthetic": True}], path, client=FakeSparkClient())
        views.append(view)
    return views


def test_merge_intake_requires_nonempty_views():
    with pytest.raises(ValueError):
        models.merge_intake_views([])


def test_merge_intake_preserves_rows_bookings_receipts_and_totals(booking_views):
    originals = deepcopy(booking_views)
    booking_views[0]["wall_s"], booking_views[1]["wall_s"] = 2.5, 4.25
    merged = models.merge_intake_views(booking_views)
    assert merged["mode"] == "live"
    assert merged["rows"] == booking_views[0]["rows"] + booking_views[1]["rows"]
    assert merged["boxes"] == 4
    assert merged["complete"] is False
    assert merged["wall_s"] == pytest.approx(6.75)
    assert float(merged["cost_usd"]) == pytest.approx(sum(float(view["cost_usd"]) for view in booking_views))
    assert len(merged["receipt_sha256"]) == 64
    assert merged["raw"]["schema"] == "stowlm.functional-intake-merge/1"
    assert [booking["view"] for booking in merged["raw"]["bookings"]] == booking_views
    assert [booking["booking_ref"] for booking in merged["bookings"]] == ["SYNTHETIC-BOOKING-1", "SYNTHETIC-BOOKING-2"]
    assert [booking["id"] for booking in merged["bookings"]] == ["b0001", "b0002"]
    assert [booking["receipt_sha256"] for booking in merged["bookings"]] == [view["receipt_sha256"] for view in booking_views]
    assert all(originals[index]["raw"] == view["raw"] for index, view in enumerate(booking_views))
    json.dumps(merged)


@pytest.mark.parametrize("flags", [(True, True), (True, False), (False, True), (False, False)])
def test_merge_intake_complete_requires_every_booking_complete(booking_views, flags):
    views = deepcopy(booking_views)
    for view, complete in zip(views, flags):
        if complete:
            view.update(complete=True, questions=[])
            view["raw"].update(status="complete", questions=[])
    merged = models.merge_intake_views(views)
    assert merged["complete"] is all(flags)


def test_merge_intake_namespaces_colliding_question_ids(booking_views):
    local_ids = [{question["id"] for question in view["questions"]} for view in booking_views]
    assert local_ids[0] & local_ids[1], "Synthetic bookings must exercise real document-question collisions"
    merged = models.merge_intake_views(booking_views)
    ids = [question["id"] for question in merged["questions"]]
    assert len(ids) == len(set(ids))
    assert len(ids) == sum(len(view["questions"]) for view in booking_views)
    assert not set(ids) & set.union(*local_ids)
    routes = merged["raw"]["question_routes"]
    assert set(routes) == set(ids)
    for index, child in enumerate(booking_views, 1):
        assert {route["question"] for route in routes.values() if route["booking"] == f"b{index:04d}"} == local_ids[index - 1]
    assert [q["id"] for q in models.merge_intake_views(deepcopy(booking_views))["questions"]] == ids


def test_merge_intake_withholds_every_duplicate_row_and_blocks_answers(booking_views):
    merged = models.merge_intake_views([booking_views[0], deepcopy(booking_views[0])])
    duplicated = {row["id"] for row in booking_views[0]["rows"]}
    assert not duplicated & {row["id"] for row in merged["rows"]}
    assert merged["boxes"] == 0
    assert merged["complete"] is False
    assert merged["conflicts"]
    questions = [question for question in merged["questions"]
                 if question["box"] in duplicated and question["id"] not in merged["raw"]["question_routes"]]
    assert {question["box"] for question in questions} == duplicated
    with pytest.raises(ValueError) as failure:
        models.answer_papers(json.loads(json.dumps(merged)), {questions[0]["id"]: "keep_one"})
    assert "correct" in str(failure.value).lower()


@pytest.mark.parametrize("tamper", [False, True])
def test_merged_json_answers_route_only_to_the_selected_booking(booking_views, monkeypatch, tamper):
    from experiments.mvp.pipeline import intake
    merged = json.loads(json.dumps(models.merge_intake_views(booking_views)))
    target = next(question for question in merged["questions"]
                  if question["box"] in {row["id"] for row in booking_views[1]["rows"]})
    route = deepcopy(merged["raw"]["question_routes"][target["id"]])
    assert route["booking"] == "b0002"
    if tamper:
        other_box = next(q for q in merged["questions"]
                         if q["box"] in {row["id"] for row in booking_views[0]["rows"]})
        merged["raw"]["question_routes"][target["id"]] = deepcopy(merged["raw"]["question_routes"][other_box["id"]])
    calls = []
    answer_questions = intake.answer_questions

    def answer(result, answers):
        calls.append(deepcopy(answers))
        return answer_questions(result, answers)

    monkeypatch.setattr(intake, "answer_questions", answer)
    final = models.answer_papers(merged, {target["id"]: "shut_out"})
    assert calls == [{route["question"]: "shut_out"}]
    before_children = merged["raw"]["bookings"]
    children = final["raw"]["bookings"]
    assert children[0]["view"] == before_children[0]["view"]
    assert len(children[1]["view"]["raw"]["records"]) == len(before_children[1]["view"]["raw"]["records"]) + 1
    assert target["box"] not in {row["id"] for row in final["rows"]}
    assert final["boxes"] == merged["boxes"] - 1
    assert final["wall_s"] >= merged["wall_s"]
    assert final["cost_usd"] == merged["cost_usd"]
    assert [booking["booking_ref"] for booking in final["bookings"]] == ["SYNTHETIC-BOOKING-1", "SYNTHETIC-BOOKING-2"]
    json.dumps(final)


def test_merged_answers_prevalidate_all_ids_before_dispatch(booking_views, monkeypatch):
    from experiments.mvp.pipeline import intake
    merged = models.merge_intake_views(booking_views)
    calls = []

    def answer(*args, **kwargs):
        calls.append(True)
        raise AssertionError("An unknown question must reject the entire answer request before dispatch")

    monkeypatch.setattr(intake, "answer_questions", answer)
    known = merged["questions"][0]["id"]
    with pytest.raises(ValueError):
        models.answer_papers(merged, {known: "shut_out", "unknown-question": "yes"})
    assert calls == []


def test_merge_keeps_scope_questions_for_mis_scoped_supporting_papers(intake_input, tmp_path):
    documents, base = intake_input
    documents = [{**documents[0], "role": "booking", "booking_ref": "BOOKING-A"}]
    paper = tmp_path / "oog-list.txt"
    paper.write_text("SYNTHETIC DOCUMENT\nComplete OOG list: none.")
    documents.append({"name": paper.name, "media_type": "text/plain", "path": paper,
                      "role": "oog_list", "booking_ref": "BOOKING-B", "complete_booking_list": True})
    view = models.read_papers(documents, base, client=FakeSparkClient())
    scope_questions = [q for q in view["raw"]["questions"] if q["reason"] == "document_scope_mismatch"]
    assert scope_questions
    merged = models.merge_intake_views([view])
    assert merged["bookings"][0]["booking_ref"] == "BOOKING-A"
    assert merged["raw"]["bookings"][0]["view"]["raw"] == view["raw"]
    routes = merged["raw"]["question_routes"]
    assert {q["id"] for q in scope_questions} <= {route["question"] for route in routes.values()}
    assert merged["complete"] is False


@pytest.mark.parametrize("mode", ["generated", "recorded"])
def test_answer_papers_rejects_nonlive_receipt_as_model_error(booking_views, mode):
    view = deepcopy(booking_views[0])
    view["raw"]["records"][0]["mode"] = mode
    with pytest.raises(models.ModelError) as failure:
        models.answer_papers(view, {})
    assert failure.value.stage == "spark"
    assert "live reading" in failure.value.reason
    assert not isinstance(failure.value, models.Unavailable)


@pytest.mark.parametrize("present, accepted, expected", [
    (False, False, False), (True, False, False), (True, True, True),
])
def test_availability_reads_provider_presence_and_consent_only(installed, present, accepted, expected):
    class Provider:
        @property
        def key(self):
            raise AssertionError("Availability must not read the key value")

        def summary(self):
            return {"present": present, "paid_calls_accepted": accepted,
                    "source": "entered_in_app", "private": "synthetic-secret-must-not-leak"}

    view = models.availability(adapter_dir=installed[1], model_dir=installed[0], key_provider=Provider())
    for stage in ("spark", "voice"):
        assert view[stage]["available"] is expected
        if not present:
            assert view[stage]["reason"] == "No Meta Model API key: enter yours in the app, or put MODEL_API_KEY in .env (see .env.example)."
        elif not accepted:
            assert "paid calls" in view[stage]["reason"].lower()
    assert "synthetic-secret-must-not-leak" not in json.dumps(view)
    assert all("private" not in value for value in view.values())


def test_environment_paid_call_consent_supports_new_name(installed, monkeypatch):
    monkeypatch.delenv("SPARK_CREDIT_READY")
    monkeypatch.setenv("STOWLM_ACCEPT_PAID_CALLS", "1")
    assert status(installed)["spark"]["available"] is True
    assert status(installed)["voice"]["available"] is True


@pytest.mark.parametrize("missing", ["base", "adapter"])
def test_missing_reader_weights_give_local_setup_guidance(installed, missing):
    if missing == "base":
        (installed[0] / "model.safetensors").unlink()
    else:
        (installed[1] / "adapters.safetensors").unlink()
    reason = status(installed)["reader"]["reason"].lower()
    assert ("weights" if missing == "base" else "adapters.safetensors") in reason
    assert any(action in reason for action in ("download", "get ", "install", "setup", "set up"))


def test_functional_ledger_default_cap_and_status_do_not_touch_project_docs(monkeypatch):
    from llm.spark.spend import SpendLedger

    def forbidden(*args, **kwargs):
        raise AssertionError("Functional accounting touched the managed project docs ledger")

    monkeypatch.setattr(SpendLedger, "_compute_text", forbidden)
    monkeypatch.setattr(SpendLedger, "_sync_compute", forbidden)
    ledger = models.functional_ledger()
    with ledger.locked():
        ledger.append({"phase": "reservation", "reservation_id": "synthetic-spend", "cost_usd": "0.25",
                       "purpose": "synthetic-spark", "cost_kind": "conservative_hold"})
        ledger.append({"phase": "settlement", "reservation_id": "synthetic-spend", "cost_usd": "0.10",
                       "purpose": "synthetic-spark", "cost_kind": "measured"})
        ledger.append({"phase": "reservation", "reservation_id": "synthetic-hold", "cost_usd": "0.20",
                       "purpose": "synthetic-voice", "cost_kind": "conservative_hold"})
    view = models.spend_status()
    assert view["available"] is True and view["reason"] is None
    assert view["cap_usd"] == 10.0
    assert view["spent_usd"] == pytest.approx(0.10)
    assert view["held_usd"] == pytest.approx(0.20)
    assert view["committed_usd"] == pytest.approx(0.30)
    assert view["remaining_usd"] == pytest.approx(9.70)
    assert ledger.path.is_relative_to(models._FUNCTIONAL_SPEND_ROOT)


def test_launch_cap_is_persisted_and_only_explicit_config_changes_it():
    models.configure_spend(launch_id="synthetic-cap", cap_usd=0.5)
    ledger = models.functional_ledger(launch_id="synthetic-cap", cap_usd=2.0)
    assert ledger.cap_usd == Decimal("0.5")
    models.configure_spend(launch_id="synthetic-cap", cap_usd=0.75)
    assert models.spend_status(launch_id="synthetic-cap")["cap_usd"] == 0.75
    models.configure_spend(launch_id="synthetic-cap", cap_usd=0.25)
    assert models.spend_status(launch_id="synthetic-cap")["cap_usd"] == 0.25


def test_functional_spend_cap_blocks_spark_and_voice_before_transport(installed, intake_input, audio, monkeypatch):
    import requests
    import urllib.request
    models.configure_spend(cap_usd=2.0)
    ledger = models.functional_ledger()
    ledger.append({"phase": "reservation", "reservation_id": "synthetic-near-cap", "cost_usd": "1.99995",
                   "purpose": "synthetic-prior-request", "cost_kind": "conservative_hold"})
    transports = []

    def forbidden(*args, **kwargs):
        transports.append(True)
        raise AssertionError("A request crossed the Functional launch cap")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(requests, "post", forbidden)
    with pytest.raises(models.ModelError) as spark:
        models.read_papers(*intake_input)
    assert "cap" in spark.value.reason.lower()
    with pytest.raises((models.ModelError, models.Unavailable)) as voice:
        models.transcribe(audio)
    assert "cap" in voice.value.reason.lower() or "ledger" in voice.value.reason.lower()
    assert transports == []
    view = models.spend_status()
    assert view["held_usd"] == pytest.approx(1.99995)
    assert view["committed_usd"] <= view["cap_usd"]


def test_default_spark_and_voice_share_launch_accounting_with_redacted_outputs(installed, intake_input, audio, monkeypatch, capsys):
    import requests
    import urllib.request
    from llm.spark.spend import SpendLedger, Usage, calculate_cost
    sentinel = "synthetic-test-token-never-return"

    def managed_forbidden(*args, **kwargs):
        raise AssertionError("Managed project ledger was used")

    monkeypatch.setattr(SpendLedger, "_compute_text", managed_forbidden)
    monkeypatch.setattr(SpendLedger, "_sync_compute", managed_forbidden)

    class SparkResponse:
        headers = {"x-request-id": sentinel + "-request"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            interpretation = {"schema_version": "stowlm.intake-interpretation/7", "header_maps": [],
                              "observations": [], "exceptions": [], "notes": []}
            return json.dumps({"model": "muse-spark-1.3", "id": sentinel + "-response", "status": "completed",
                               "usage": {"input_tokens": 100, "output_tokens": 20},
                               "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(interpretation)}]}]}).encode()

    class VoiceResponse:
        status_code = 200
        def json(self):
            return {"transcript": "Synthetic spoken instruction.", "audioDurationMs": 1000,
                    "sessionId": sentinel + "-session"}

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: SparkResponse())
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: VoiceResponse())
    intake = models.read_papers(*intake_input)
    voice = models.transcribe(audio)
    view = models.spend_status()
    expected = float(calculate_cost(Usage(100, 20))) + 0.00005
    assert view["spent_usd"] == pytest.approx(expected)
    assert view["held_usd"] == 0
    assert view["committed_usd"] == pytest.approx(expected)
    records = request_records()
    assert {row["model"] for row in records} == {"muse-spark-1.3", "muse-voice-transcribe-1.0"}
    assert [row["phase"] for row in records] == ["reservation", "settlement", "reservation", "settlement"]
    captured = capsys.readouterr()
    exposed = json.dumps({"intake": intake, "voice": voice, "spend": view, "records": records}) + captured.out + captured.err
    assert sentinel not in exposed
    assert sentinel not in request_ledger_text()


def test_failed_transports_keep_conservative_holds_and_redact_diagnostics(installed, intake_input, audio, monkeypatch, capsys):
    import requests
    import urllib.request
    sentinel = "synthetic-test-token-never-return"

    def failure(*args, **kwargs):
        raise TimeoutError("private transport diagnostic " + sentinel)

    monkeypatch.setattr(urllib.request, "urlopen", failure)
    monkeypatch.setattr(requests, "post", failure)
    with pytest.raises(models.ModelError) as spark:
        models.read_papers(*intake_input)
    with pytest.raises((models.ModelError, models.Unavailable)) as voice:
        models.transcribe(audio)
    records = request_records()
    settlements = [row for row in records if row["phase"] == "settlement"]
    assert len(settlements) == 2
    assert all(row["cost_kind"] == "conservative_hold" for row in settlements)
    view = models.spend_status()
    assert view["spent_usd"] == 0
    assert view["held_usd"] == pytest.approx(sum(float(row["cost_usd"]) for row in settlements))
    assert view["held_usd"] > 0
    assert view["remaining_usd"] == pytest.approx(view["cap_usd"] - view["held_usd"])
    captured = capsys.readouterr()
    exposed = str(spark.value) + str(voice.value) + json.dumps(view) + request_ledger_text() + captured.out + captured.err
    assert sentinel not in exposed
    assert "private transport diagnostic" not in exposed


def test_functional_ledger_same_launch_is_shared_with_a_fresh_guarded_process(tmp_path):
    import os
    import subprocess
    launch = "synthetic-cross-process"
    models.configure_spend(launch_id=launch, cap_usd=0.5)
    ledger = models.functional_ledger(launch_id=launch)
    ledger.append({"phase": "reservation", "reservation_id": "parent-hold", "cost_usd": "0.4",
                   "purpose": "synthetic-parent", "cost_kind": "conservative_hold"})
    child = '''
import json
from pathlib import Path
import socket
import sys
import urllib.request

def forbidden(*args, **kwargs):
    raise AssertionError("Ledger test attempted a network call")
socket.create_connection = forbidden
socket.socket.connect = forbidden
urllib.request.urlopen = forbidden
from app import functional_models as models
from llm.spark.spend import SpendCapExceeded
models._FUNCTIONAL_SPEND_ROOT = Path(sys.argv[1])
ledger = models.functional_ledger(launch_id=sys.argv[2], cap_usd=2.0)
blocked = False
try:
    ledger.append({'phase': 'reservation', 'reservation_id': 'child-hold', 'cost_usd': '0.2',
                   'purpose': 'synthetic-child', 'cost_kind': 'conservative_hold'})
except SpendCapExceeded:
    blocked = True
print(json.dumps({'blocked': blocked, 'status': models.spend_status(launch_id=sys.argv[2]), 'path': str(ledger.path)}))
'''
    env = os.environ.copy()
    env.pop("MODEL_API_KEY", None)
    env.pop("SPARK_CREDIT_READY", None)
    env.pop("STOWLM_ACCEPT_PAID_CALLS", None)
    env.update(UV_OFFLINE="1", UV_CACHE_DIR="/private/tmp/fa-models-uv-cache")
    result = subprocess.run(["uv", "run", "--offline", "python", "-c", child,
                             str(models._FUNCTIONAL_SPEND_ROOT), launch],
                            text=True, capture_output=True, cwd=Path(__file__).resolve().parents[2],
                            env=env, timeout=20)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["blocked"] is True
    assert output["status"]["cap_usd"] == 0.5
    assert output["status"]["held_usd"] == pytest.approx(0.4)
    assert output["path"] == str(ledger.path)
    assert len(ledger._records()) == 1


def test_spark_credential_echo_is_refused_before_returning_a_view(installed, intake_input, capsys):
    from llm.spark.client import SparkResponse
    from llm.spark.spend import Usage
    sentinel = "synthetic-test-token-never-return"

    class Client:
        def complete_documents(self, *args, **kwargs):
            response = {"schema_version": "stowlm.intake-interpretation/7", "header_maps": [],
                        "observations": [], "exceptions": [], "notes": ["Private echo " + sentinel]}
            return SparkResponse(json.dumps(response), Usage(100, 20), Decimal("0.001"), "synthetic-request")

    with pytest.raises(models.ModelError) as failure:
        models.read_papers(*intake_input, client=Client())
    assert failure.value.stage == "spark"
    captured = capsys.readouterr()
    assert sentinel not in str(failure.value) + captured.out + captured.err
    assert "Private echo" not in str(failure.value)


def test_default_voice_refuses_credential_echo_without_logging_it(installed, audio, monkeypatch, capsys):
    import requests
    sentinel = "synthetic-test-token-never-return"

    class Response:
        status_code = 200
        def json(self):
            return {"transcript": "Private echo " + sentinel, "audioDurationMs": 1000,
                    "sessionId": sentinel + "-session"}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    with pytest.raises((models.ModelError, models.Unavailable)) as failure:
        models.transcribe(audio)
    captured = capsys.readouterr()
    assert sentinel not in str(failure.value) + captured.out + captured.err
    assert sentinel not in request_ledger_text()


def test_key_provider_diagnostic_never_reaches_exception_text():
    sentinel = "synthetic-provider-secret-never-return"

    class Provider:
        def summary(self):
            raise RuntimeError("private provider diagnostic " + sentinel)

    with pytest.raises(ValueError) as failure:
        models.availability(key_provider=Provider())
    assert sentinel not in str(failure.value)
    assert "private provider diagnostic" not in str(failure.value)


def test_reader_refuses_credential_echo_before_returning_a_view(installed, capsys):
    sentinel = "synthetic-test-token-never-return"
    answer = {"schema_version": "stowlm.reader/2", "refusal": {
        "refused": True, "code": "unsupported", "reason": "Private echo " + sentinel}}
    reader = models.Reader(adapter_dir=installed[1], model_dir=installed[0],
                           generate=lambda sentence, context: json.dumps(answer))
    with pytest.raises(models.ModelError) as failure:
        reader.read("Synthetic unsupported instruction.", CONTEXT)
    assert failure.value.stage == "reader"
    captured = capsys.readouterr()
    assert sentinel not in str(failure.value) + captured.out + captured.err


@pytest.mark.parametrize("echo", [None, "stdout", "stderr"])
def test_provider_voice_uses_worker_environment_without_changing_server_environment(installed, audio, monkeypatch, echo):
    import os
    import subprocess
    from types import SimpleNamespace
    key = "synthetic-entered-key-only-in-worker"
    before = os.environ.get("MODEL_API_KEY")
    calls = []

    class Provider:
        def summary(self):
            return {"present": True, "paid_calls_accepted": True}

        def worker_env(self):
            return {"MODEL_API_KEY": key, "SPARK_CREDIT_READY": "1", "STOWLM_ACCEPT_PAID_CALLS": "1",
                    "STOWLM_FUNCTIONAL_LAUNCH_ID": "synthetic-provider-launch", "STOWLM_FUNCTIONAL_CAP_USD": "0.75"}

    def run(command, **kwargs):
        calls.append(True)
        assert key not in json.dumps(command)
        assert kwargs["env"]["MODEL_API_KEY"] == key
        assert kwargs["env"]["STOWLM_FUNCTIONAL_LAUNCH_ID"] == "synthetic-provider-launch"
        assert kwargs["env"]["STOWLM_FUNCTIONAL_CAP_USD"] == "0.75"
        assert os.environ.get("MODEL_API_KEY") == before
        view = {"mode": "live", "transcript": "Synthetic instruction.", "model": "muse-voice-transcribe-1.0",
                "audio_seconds": 1, "audio_sha256": "a" * 64, "wall_s": 0.1, "cost_usd": 0.00005}
        return SimpleNamespace(returncode=0,
                               stdout=json.dumps(view) if echo != "stdout" else key,
                               stderr=key if echo == "stderr" else "")

    monkeypatch.setattr(subprocess, "run", run)
    if echo:
        with pytest.raises(models.ModelError) as failure:
            models.transcribe(audio, key_provider=Provider())
        assert key not in str(failure.value)
    else:
        view = models.transcribe(audio, key_provider=Provider())
        assert view["transcript"] == "Synthetic instruction."
        assert key not in json.dumps(view)
    assert calls == [True]
    assert os.environ.get("MODEL_API_KEY") == before


def test_provider_voice_requires_consent_before_requesting_worker_key(audio):
    class Provider:
        def summary(self):
            return {"present": True, "paid_calls_accepted": False}

        def worker_env(self):
            raise AssertionError("A key must not be read without consent")

    with pytest.raises(models.Unavailable) as failure:
        models.transcribe(audio, key_provider=Provider())
    assert "Paid calls aren't accepted yet" in failure.value.reason


@pytest.fixture
def observed_intake_hold(installed, intake_input, monkeypatch):
    """Take the reservation from the unchanged client's actual fake request path."""
    import urllib.error
    import urllib.request

    def rejected(*args, **kwargs):
        raise urllib.error.HTTPError("https://synthetic.invalid", 400, "synthetic rejection", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", rejected)
    with pytest.raises(models.ModelError):
        models.read_papers(*intake_input)
    records = request_records()
    reservations = [row for row in records if row["phase"] == "reservation"]
    assert len(reservations) == 1
    hold = Decimal(reservations[0]["cost_usd"])
    assert hold > 0
    assert models.spend_status()["committed_usd"] == 0
    return hold


def test_default_launch_cap_is_ten_without_environment_override(monkeypatch):
    monkeypatch.delenv("STOWLM_FUNCTIONAL_CAP_USD")
    fresh = models.spend_status(launch_id="synthetic-default-ten")
    assert fresh["cap_usd"] == 10.0
    configured = models.configure_spend(launch_id="synthetic-configured-default-ten")
    assert configured["cap_usd"] == 10.0


def test_room_for_calls_uses_actual_frozen_intake_reservation(observed_intake_hold):
    view = models.spend_status()
    assert view["hold_usd_per_call"] == pytest.approx(float(observed_intake_hold))
    assert view["holds_usd"] == view["held_usd"] == 0
    assert view["room_for_calls"] == int(Decimal("10") // observed_intake_hold)
    assert view["room_for_calls"] == 7


@pytest.mark.parametrize("offset, expected", [("-0.000001", 2), ("0", 3), ("0.000001", 3)])
def test_room_for_calls_floors_exact_cap_boundaries(observed_intake_hold, offset, expected):
    cap = observed_intake_hold * 3 + Decimal(offset)
    view = models.configure_spend(launch_id="synthetic-floor-boundary", cap_usd=cap)
    assert view["room_for_calls"] == expected
    assert view["hold_usd_per_call"] == pytest.approx(float(observed_intake_hold))


def test_room_for_calls_counts_pending_and_conservative_holds_and_releases_settled_slots(observed_intake_hold):
    hold = observed_intake_hold
    launch = "synthetic-room-holds"
    models.configure_spend(launch_id=launch, cap_usd=hold * 4)
    ledger = models.functional_ledger(launch_id=launch)
    ledger.append({"cost_usd": str(hold / 4), "purpose": "synthetic-known-spend"})
    ledger.append({"phase": "reservation", "reservation_id": "pending", "cost_usd": str(hold),
                   "purpose": "synthetic-pending", "cost_kind": "conservative_hold"})
    ledger.append({"phase": "reservation", "reservation_id": "unknown", "cost_usd": str(hold),
                   "purpose": "synthetic-unknown", "cost_kind": "conservative_hold"})
    ledger.append({"phase": "settlement", "reservation_id": "unknown", "cost_usd": str(hold),
                   "purpose": "synthetic-unknown", "cost_kind": "conservative_hold"})
    before = models.spend_status(launch_id=launch)
    assert before["spent_usd"] == pytest.approx(float(hold / 4))
    assert before["holds_usd"] == before["held_usd"] == pytest.approx(float(hold * 2))
    assert before["room_for_calls"] == 1
    ledger.append({"phase": "settlement", "reservation_id": "pending", "cost_usd": "0",
                   "purpose": "synthetic-pending", "cost_kind": "measured"})
    after = models.spend_status(launch_id=launch)
    assert after["spent_usd"] == before["spent_usd"]
    assert after["holds_usd"] == after["held_usd"] == pytest.approx(float(hold))
    assert after["room_for_calls"] == 2


def test_room_for_calls_is_zero_for_exhausted_and_lowered_caps(observed_intake_hold):
    hold = observed_intake_hold
    launch = "synthetic-room-exhausted"
    models.configure_spend(launch_id=launch, cap_usd=hold)
    ledger = models.functional_ledger(launch_id=launch)
    ledger.append({"phase": "reservation", "reservation_id": "all-budget", "cost_usd": str(hold),
                   "purpose": "synthetic-exhausted", "cost_kind": "conservative_hold"})
    assert models.spend_status(launch_id=launch)["room_for_calls"] == 0
    view = models.configure_spend(launch_id=launch, cap_usd=hold / 2)
    assert view["remaining_usd"] == 0
    assert view["room_for_calls"] == 0


def test_corrupt_spend_status_returns_null_reservation_and_room_fields():
    ledger = models.functional_ledger()
    ledger.path.write_text("not JSON\n")
    view = models.spend_status()
    assert view["available"] is False
    for key in ("spent_usd", "held_usd", "holds_usd", "grants_usd", "hold_usd_per_call", "room_for_calls",
                "committed_usd", "remaining_usd", "cap_usd"):
        assert view[key] is None


def test_default_intake_requests_overlap_and_status_does_not_wait_for_transport(installed, intake_input, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import urllib.request
    from llm.spark.spend import Usage, calculate_cost
    release = threading.Event()
    both_entered = threading.Event()
    guard = threading.Lock()
    entered = []

    class Response:
        headers = {"x-request-id": "synthetic-concurrent-request"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            interpretation = {"schema_version": "stowlm.intake-interpretation/7", "header_maps": [],
                              "observations": [], "exceptions": [], "notes": []}
            return json.dumps({"model": "muse-spark-1.3", "id": "synthetic-concurrent-response", "status": "completed",
                               "usage": {"input_tokens": 100, "output_tokens": 20},
                               "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(interpretation)}]}]}).encode()

    def transport(*args, **kwargs):
        with guard:
            entered.append(True)
            if len(entered) == 2:
                both_entered.set()
        assert release.wait(5), "The test did not release its fake transports"
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(models.read_papers, *intake_input)
        second = pool.submit(models.read_papers, *intake_input)
        try:
            assert both_entered.wait(3), "The second booking could not enter its fake transport concurrently"
            current = pool.submit(models.spend_status).result(timeout=0.75)
            assert current["available"] is True
            assert current["spent_usd"] == 0
            assert current["committed_usd"] == pytest.approx(current["held_usd"] + current["grants_usd"])
            assert current["committed_usd"] == pytest.approx(sum(float(grant.cap_usd) for grant in models._test_grants))
            assert current["held_usd"] > 0
            assert current["committed_usd"] <= current["cap_usd"]
        finally:
            release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert all(view["mode"] == "live" for view in results)
    final = models.spend_status()
    assert final["held_usd"] == 0
    assert final["spent_usd"] == pytest.approx(2 * float(calculate_cost(Usage(100, 20))))


def test_grant_admission_is_atomic_across_threads_and_local_caps_match_allocations():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from llm.spark.spend import SpendCapExceeded
    launch = "synthetic-thread-grants"
    models.configure_spend(launch_id=launch, cap_usd=10)
    start = threading.Barrier(8)

    def acquire(index):
        start.wait(timeout=5)
        try:
            return models.grant_call(cap_usd=10, launch_id=launch,
                                     call_id=f"synthetic-call-{index}", booking_ref=f"synthetic-booking-{index}")
        except SpendCapExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(acquire, range(8)))
    grants = [grant for grant in results if grant is not None]
    try:
        assert len(grants) == 7
        assert len({grant.id for grant in grants}) == 7
        assert len({str(grant.ledger_path) for grant in grants}) == 7
        for grant in grants:
            ledger = models.functional_ledger(grant=grant)
            assert ledger.cap_usd == grant.cap_usd
        current = models.spend_status(launch_id=launch)
        assert current["committed_usd"] == pytest.approx(sum(float(grant.cap_usd) for grant in grants))
        assert current["committed_usd"] <= 10
    finally:
        for grant in grants:
            models.release(grant)
    assert models.spend_status(launch_id=launch)["committed_usd"] == 0


def test_exact_multiple_of_intake_hold_admits_every_promised_grant(observed_intake_hold):
    from llm.spark.spend import SpendCapExceeded
    cap = observed_intake_hold * 3
    launch = "synthetic-exact-grants"
    models.configure_spend(launch_id=launch, cap_usd=cap)
    assert models.spend_status(launch_id=launch)["room_for_calls"] == 3
    grants = []
    try:
        for index in range(3):
            grants.append(models.grant_call(cap_usd=cap, launch_id=launch, booking_ref=f"synthetic-{index}"))
        assert models.grant_call(cap_usd=cap, launch_id=launch) is None
        assert sum(Decimal(str(grant.cap_usd)) for grant in grants) == cap
        assert models.spend_status(launch_id=launch)["room_for_calls"] == 0
    finally:
        for grant in grants:
            models.release(grant)


@pytest.mark.parametrize("kind", ["measured", "conservative_hold"])
def test_release_keeps_used_or_unknown_cost_and_releases_only_unused_grant(kind):
    from llm.spark.spend import SpendError
    grant = models.grant_call(cap_usd=10, booking_ref="synthetic-release")
    ledger = models.functional_ledger(grant=grant)
    ledger.append({"phase": "reservation", "reservation_id": "synthetic-request", "cost_usd": "0.5",
                   "purpose": "synthetic-used", "cost_kind": "conservative_hold"})
    ledger.append({"phase": "settlement", "reservation_id": "synthetic-request", "cost_usd": "0.25",
                   "purpose": "synthetic-used", "cost_kind": kind})
    active = models.spend_status()
    assert active["committed_usd"] == pytest.approx(float(grant.cap_usd))
    models.release(grant)
    released = models.spend_status()
    assert released["committed_usd"] == pytest.approx(0.25)
    assert released["spent_usd"] == (pytest.approx(0.25) if kind == "measured" else 0)
    assert released["held_usd"] == (pytest.approx(0.25) if kind == "conservative_hold" else 0)
    models.release(grant)
    assert models.spend_status() == released
    with pytest.raises(SpendError):
        ledger.append({"cost_usd": "0.01", "purpose": "synthetic-stale-use"})
    assert models.spend_status() == released


def test_status_snapshot_ignores_partial_tail_but_fails_closed_for_malformed_earlier_record():
    grant = models.grant_call(cap_usd=10, booking_ref="synthetic-snapshot")
    ledger = models.functional_ledger(grant=grant)
    ledger.append({"cost_usd": "0.1", "purpose": "synthetic-complete-record"})
    complete = ledger.path.read_bytes()
    with ledger.path.open("ab") as handle:
        handle.write(b'{"phase":"reservation"')
    partial = models.spend_status()
    assert partial["available"] is True
    assert partial["spent_usd"] == pytest.approx(0.1)
    assert partial["committed_usd"] == pytest.approx(float(grant.cap_usd))
    ledger.path.write_bytes(b"not JSON\n" + complete)
    corrupted = models.spend_status()
    assert corrupted["available"] is False
    assert corrupted["spent_usd"] is None
    assert corrupted["held_usd"] is None
    assert corrupted["room_for_calls"] is None


def test_grant_admission_is_atomic_across_fresh_guarded_processes():
    from concurrent.futures import ThreadPoolExecutor
    import os
    import subprocess
    launch = "synthetic-process-grants"
    models.configure_spend(launch_id=launch, cap_usd=2)
    child = '''
import json
from pathlib import Path
import socket
import sys
import urllib.request
from llm.spark.spend import SpendLedger, SpendCapExceeded

def forbidden(*args, **kwargs):
    raise AssertionError("A grant-only child attempted network or managed docs accounting")
socket.create_connection = forbidden
socket.socket.connect = forbidden
urllib.request.urlopen = forbidden
SpendLedger._compute_text = forbidden
SpendLedger._sync_compute = forbidden
from app import functional_models as models
models._FUNCTIONAL_SPEND_ROOT = Path(sys.argv[1])
try:
    grant = models.grant_call(cap_usd=2, launch_id=sys.argv[2], booking_ref='synthetic-child')
    result = ({'admitted': True, 'id': grant.id, 'cap_usd': float(grant.cap_usd)}
              if grant is not None else {'admitted': False})
except SpendCapExceeded:
    result = {'admitted': False}
print(json.dumps(result))
'''
    env = os.environ.copy()
    for key in ("MODEL_API_KEY", "SPARK_CREDIT_READY", "STOWLM_ACCEPT_PAID_CALLS"):
        env.pop(key, None)
    env.update(UV_OFFLINE="1", UV_CACHE_DIR="/private/tmp/fa-models-uv-cache")

    def run_child():
        result = subprocess.run(["uv", "run", "--offline", "python", "-c", child,
                                 str(models._FUNCTIONAL_SPEND_ROOT), launch],
                                text=True, capture_output=True, cwd=Path(__file__).resolve().parents[2],
                                env=env, timeout=20)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda unused: run_child(), range(2)))
    admitted = [result for result in results if result["admitted"]]
    assert len(admitted) == 1
    current = models.spend_status(launch_id=launch)
    assert current["held_usd"] == 0
    assert current["grants_usd"] == pytest.approx(admitted[0]["cap_usd"])
    assert current["committed_usd"] <= 2


def test_status_snapshot_does_not_acquire_the_legacy_client_lock():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    ledger = models.functional_ledger()
    entered, release = threading.Event(), threading.Event()

    def hold_client_lock():
        with ledger.locked():
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_client_lock)
        try:
            assert entered.wait(2)
            view = pool.submit(models.spend_status).result(timeout=0.75)
            assert view["available"] is True
            assert view["spent_usd"] == 0
        finally:
            release.set()
        holder.result(timeout=5)


def test_status_tolerates_empty_grant_creation_window_but_not_unregistered_spend():
    ledger = models.functional_ledger()
    directory = ledger.path.parent / "bookings"
    directory.mkdir(exist_ok=True)
    unpublished = directory / "synthetic-not-published-yet.jsonl"
    unpublished.touch()
    view = models.spend_status()
    assert view["available"] is True
    assert view["committed_usd"] == 0
    unpublished.write_text(json.dumps({"cost_usd": "0.1"}) + "\n")
    view = models.spend_status()
    assert view["available"] is False
    assert view["committed_usd"] is None


@pytest.mark.parametrize("scheduler_handshake", [False, True])
def test_explicit_booking_grant_stays_active_until_its_caller_releases_it(installed, intake_input, monkeypatch, scheduler_handshake):
    from io import BytesIO
    import urllib.request
    from llm.spark.spend import Usage, calculate_cost
    raw = {"schema_version": "stowlm.intake-interpretation/7", "header_maps": [],
           "observations": [], "exceptions": [], "notes": []}
    wire = {"model": "muse-spark-1.3", "id": "synthetic-explicit-grant", "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(raw)}]}]}
    response = BytesIO(json.dumps(wire).encode())
    response.headers = {"x-request-id": "synthetic-explicit-grant"}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: response)
    if scheduler_handshake:
        grant = models.grant_call(cap=models._intake_hold_usd(), call_id="synthetic-call", booking_ref="synthetic-booking")
        assert isinstance(grant, dict)
        call_kwargs = {"ledger_path": grant.get("ledger_path"), "cap_usd": grant.get("cap_usd")}
    else:
        grant = models.grant_call(cap_usd=10, call_id="synthetic-call", booking_ref="synthetic-booking")
        call_kwargs = {"grant": grant}
    try:
        result = models.read_papers(*intake_input, **call_kwargs)
        assert result["mode"] == "live"
        active = models.spend_status()
        measured = float(calculate_cost(Usage(100, 20)))
        assert active["spent_usd"] == pytest.approx(measured)
        assert active["committed_usd"] == pytest.approx(float(grant.cap_usd))
    finally:
        models.release(grant)
    final = models.spend_status()
    assert final["spent_usd"] == pytest.approx(measured)
    assert final["held_usd"] == 0
    assert final["committed_usd"] == pytest.approx(measured)


def test_cap_failure_after_a_paid_schema_attempt_does_not_claim_zero_charge(installed, intake_input, monkeypatch):
    from io import BytesIO
    import urllib.request
    from llm.spark.spend import Usage, calculate_cost
    transports = []

    def invalid_schema_response(*args, **kwargs):
        transports.append(True)
        wire = {"model": "muse-spark-1.3", "id": "synthetic-paid-attempt", "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}]}
        response = BytesIO(json.dumps(wire).encode())
        response.headers = {"x-request-id": "synthetic-paid-attempt"}
        return response

    monkeypatch.setattr(urllib.request, "urlopen", invalid_schema_response)
    grant = models.grant_call(cap_usd=10, booking_ref="SYNTHETIC-B10")
    try:
        with pytest.raises(models.ModelError) as failure:
            models.read_papers(*intake_input, grant=grant)
        assert transports == [True]
        reason = failure.value.reason.lower()
        assert "cap" in reason
        assert "api key panel" in reason
        for false_claim in ("nothing was charged", "booking wasn't sent", "no api call", "no request was sent"):
            assert false_claim not in reason
        assert models.spend_status()["spent_usd"] == pytest.approx(float(calculate_cost(Usage(100, 20))))
    finally:
        models.release(grant)


@pytest.mark.parametrize("kind", ["output_limit", "filter", "http", "protocol", "timeout", "transport", "cap", "ledger"])
def test_public_spark_failure_guidance_points_to_the_api_key_panel(kind):
    from llm.spark.client import SparkHTTPError, SparkIncompleteError, SparkProtocolError
    from llm.spark.spend import SpendCapExceeded, SpendError, Usage
    errors = {
        "output_limit": SparkIncompleteError("max_output_tokens", Usage(100, 24576), Decimal("0.1"), "synthetic-request"),
        "filter": SparkIncompleteError("content_filter", Usage(100, 20), Decimal("0.01"), "synthetic-request"),
        "http": SparkHTTPError(429), "protocol": SparkProtocolError("synthetic diagnostic"),
        "timeout": TimeoutError("synthetic diagnostic"), "transport": RuntimeError("synthetic diagnostic"),
        "cap": SpendCapExceeded("synthetic diagnostic"), "ledger": SpendError("synthetic diagnostic"),
    }
    reason = models._spark_error(errors[kind]).reason
    assert "see spent and held in the api key panel" in reason.lower()
    assert "check the spend ledger" not in reason.lower()


@pytest.mark.parametrize("cost", [None, "not a price", -1, "NaN", "0.10"])
def test_public_schema_failure_billing_guidance_points_to_the_api_key_panel(cost):
    reason = models._schema_error({"cost_usd": cost}).reason
    assert "see spent and held in the api key panel" in reason.lower()
    assert "check the spend ledger" not in reason.lower()


@pytest.mark.parametrize("cap_failure", [True, False])
def test_public_voice_spend_errors_point_to_the_api_key_panel(audio, cap_failure):
    from llm.spark.spend import SpendCapExceeded, SpendError

    class Transcriber:
        def transcribe(self, path):
            raise (SpendCapExceeded("private details") if cap_failure else SpendError("private details"))

    with pytest.raises(models.ModelError) as failure:
        models.transcribe(audio, transcriber=Transcriber())
    assert "see spent and held in the api key panel" in failure.value.reason.lower()
    assert "private details" not in failure.value.reason
    assert "check the spend ledger" not in failure.value.reason.lower()


def test_model_source_never_directs_the_planner_to_an_invisible_spend_ledger():
    assert "check the spend ledger" not in Path(models.__file__).read_text().lower()


def test_contextual_cap_refusal_matches_reviewed_booking_facts_exactly():
    error = models.intake_cap_error("B10", spent_usd=Decimal("0.665976"), hold_usd=Decimal("1.384448"),
                                    cap_usd=Decimal("2"), other_bookings_read=10)
    assert error.stage == "spark"
    assert error.reason == (
        "Booking B10 wasn't sent: $0.67 spent plus this call's worst-case hold of $1.384448 would pass your $2.00 cap. "
        "Nothing was charged for it. 10 other bookings were read and are kept for a retry; "
        "nothing has been planned from a partial reading. Raise the cap in the API key panel to read the rest."
    )


def test_cap_refusal_without_booking_context_does_not_invent_prior_reading_counts():
    reason = models.intake_cap_error("B10", spent_usd=Decimal("0.665976"), hold_usd=Decimal("1.384448"),
                                     cap_usd=2).reason
    assert reason.startswith("Booking B10 wasn't sent: $0.67 spent")
    assert "other bookings" not in reason
    assert "partial reading" not in reason
    assert "kept for a retry" not in reason
    assert "API key panel" in reason


def test_cap_refusal_arithmetic_names_pending_holds_and_reserved_grants():
    reason = models.intake_cap_error("B10", spent_usd=Decimal("0.10"), holds_usd=Decimal("0.75"),
                                     grants_usd=Decimal("0.20"), hold_usd=Decimal("1.384448"),
                                     cap_usd=2, other_bookings_read=0).reason
    assert "$0.75 held" in reason
    assert "$0.20" in reason and "reserved for other calls" in reason
    assert "$0.10 spent plus this call" not in reason
    assert "$1.384448" in reason and "$2.00 cap" in reason


def test_cap_refusal_uses_the_atomic_admission_snapshot():
    models.configure_spend(cap_usd=2)
    ledger = models.functional_ledger()
    ledger.append({"cost_usd": "0.665976", "purpose": "synthetic-earlier-bookings"})
    refusal = {}
    grant = models.grant_call(cap_usd=2, booking_ref="B10", refusal=refusal)
    assert grant is None
    assert Decimal(str(refusal["spent_usd"])) == Decimal("0.665976")
    assert Decimal(str(refusal["hold_usd"])) == models._intake_hold_usd()
    assert Decimal(str(refusal["cap_usd"])) == Decimal("2")
    # A later explicit cap change cannot rewrite the decision's captured facts.
    models.configure_spend(cap_usd=10)
    reason = models.intake_cap_error("B10", **refusal).reason
    assert "$2.00 cap" in reason
    assert "$10.00 cap" not in reason


def test_custom_reader_stamps_adoption_status_at_the_time_of_reading(installed, tmp_path):
    custom = tmp_path / "custom-reader"
    custom.mkdir()
    weights = custom / "adapters.safetensors"
    weights.write_bytes(b"synthetic custom reader weights, never loaded")
    (custom / "adapter_config.json").write_text('{}')
    answer = json.dumps({"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}})
    view = models.Reader(adapter_dir=custom, model_dir=installed[0], generate=lambda *args: answer).read(
        "Close bay one.", deepcopy(CONTEXT))
    assert view["status"] == "constraint_ready"
    assert view["adopted"] is False
    assert view["evidence"]["adapter_sha256"] == hashlib.sha256(weights.read_bytes()).hexdigest()
    models._ADOPTION_RECORD.write_text(json.dumps({"adopted": {
        "path": str(custom), "adapter_sha256": hashlib.sha256(weights.read_bytes()).hexdigest()}}))
    assert models.availability(adapter_dir=custom, model_dir=installed[0])["reader"]["adopted"] is True
    assert json.loads(json.dumps(view))["adopted"] is False


@pytest.fixture(params=["c5c4b49fb95c293c", "fd88061f0f769900"])
def saved_generator_merge_case(request):
    """Local regression on the explicitly requested synthetic run; never replayed as a live call."""
    call = Path(__file__).resolve().parents[2] / "experiments/mvp/ui/functional/calls/c20261103-212438-199686"
    path = call / f"intake/bookings/SYN-149c026dbad8683a-B2-{request.param}.json"
    base = call / "base-instance.json"
    if not path.is_file() or not base.is_file():
        pytest.skip("The requested local synthetic regression receipt is not present in this clone")
    view = json.loads(path.read_text())
    assert len(view["questions"]) == 1
    assert view["raw"]["questions"][0]["reason"] == "unsupported_equipment"
    return view, base, "QZXU0000117"


def test_generator_record_choice_preserves_spark_receipt_and_other_rows(saved_generator_merge_case, monkeypatch):
    from experiments.mvp.pipeline import intake
    view, base_path, box = saved_generator_merge_case
    original = deepcopy(view)
    generator = {row["id"]: row for row in json.loads(base_path.read_text())["containers"]}

    def forbidden(*args, **kwargs):
        raise AssertionError("Choosing a generator record must not read papers or call a model")

    monkeypatch.setattr(intake, "read_documents", forbidden)
    monkeypatch.setattr(intake, "answer_questions", forbidden)
    result = models.merge_generator_records(view, base_path, [box])
    assert view == original
    assert result["raw"]["schema"] == "stowlm.functional-generator-merge/1"
    assert result["raw"]["source_intake"] == original
    assert result["cost_usd"] == original["cost_usd"]
    assert result["boxes"] == original["boxes"]
    assert result["complete"] is True
    assert result["questions"] == []
    for before, after in zip(original["rows"], result["rows"]):
        expected = generator[box] if before["id"] == box else before
        assert after == {**expected, "origin": "generator" if before["id"] == box else "spark"}
    assert result["generator_choices"] == [{
        "box": box, "origin": "generator", "reason": "planner chose the generator's record (not read by a model)",
        "closed_questions": original["questions"],
    }]
    assert json.loads(json.dumps(result))["raw"]["source_intake"] == original


@pytest.mark.parametrize("reason, field", [("missing_declaration", "pod"), ("cross_document_conflict", "gross_mass_kg"),
                                           ("reader_referral", "special_stowage")])
def test_generator_record_choice_refuses_any_other_open_box_question(saved_generator_merge_case, reason, field):
    view, base_path, box = saved_generator_merge_case
    changed = deepcopy(view)
    index = next(i for i, row in enumerate(view["rows"]) if row["id"] == box)
    question = {"id": f"{box}:{field}:{reason}", "field": field, "row": index,
                "reason": reason, "prompt": "Synthetic additional open question.", "candidates": []}
    changed["raw"]["questions"].append(question)
    changed["questions"].append({"id": question["id"], "field": field, "box": box, "text": question["prompt"]})
    with pytest.raises(ValueError):
        models.merge_generator_records(changed, base_path, [box])


@pytest.mark.parametrize("change", ["reason", "field", "prompt"])
def test_generator_record_choice_requires_exact_frozen_policy_question(saved_generator_merge_case, change):
    view, base_path, box = saved_generator_merge_case
    changed = deepcopy(view)
    question = changed["raw"]["questions"][0]
    question[{"reason": "reason", "field": "field", "prompt": "prompt"}[change]] = {
        "reason": "cross_document_conflict", "field": "pod", "prompt": "A model requested equipment review, rather than frozen policy."}[change]
    with pytest.raises(ValueError):
        models.merge_generator_records(changed, base_path, [box])


def test_generator_choice_requires_an_open_question_and_a_known_selected_box(saved_generator_merge_case):
    view, base_path, box = saved_generator_merge_case
    complete_box = next(row["id"] for row in view["rows"] if row["id"] != box)
    with pytest.raises(ValueError):
        models.merge_generator_records(view, base_path, [complete_box])
    with pytest.raises(ValueError):
        models.merge_generator_records(view, base_path, ["UNKNOWN-SYNTHETIC-BOX"])
    resolved = models.merge_generator_records(view, base_path, [box])
    with pytest.raises(ValueError):
        models.merge_generator_records(resolved, base_path, [box])


@pytest.fixture
def synthetic_oog_generator_choice(booking_views, tmp_path):
    """A by-design OOG review with other unresolved synthetic cargo left intact."""
    from experiments.mvp.pipeline.contract.schema import sha256_json
    view = deepcopy(booking_views[1])
    context = json.loads(view["raw"]["records"][-1]["notes"])["intake_context"]
    base = context["base_instance"]
    selected = next(row["id"] for row in base["containers"] if row.get("oog") and row["id"] in base["load_list"])
    index = next(index for index, row in enumerate(view["rows"]) if row["id"] == selected)
    view["raw"]["questions"] = [question for question in view["raw"]["questions"] if question["row"] != index]
    view["questions"] = [question for question in view["questions"] if question["box"] != selected]
    text = f"{selected}: listed OOG cargo requires verified dimensions; an absent model reading cannot declare it in gauge."
    raw_question = {"id": f"{selected}:oog:missing_oog_declaration", "row": index, "field": "oog",
                    "reason": "missing_oog_declaration", "prompt": text, "candidates": []}
    view["raw"]["questions"].append(raw_question)
    view["questions"].append({"id": raw_question["id"], "box": selected, "field": "oog", "text": text})
    view["receipt_sha256"] = sha256_json(view["raw"])
    path = tmp_path / "synthetic-oog-base.json"
    path.write_text(json.dumps(base))
    return view, path, selected


def test_generator_oog_choice_is_pure_keeps_other_questions_and_has_explicit_audit(synthetic_oog_generator_choice):
    from experiments.mvp.pipeline.contract.schema import sha256_json
    view, base_path, selected = synthetic_oog_generator_choice
    before = deepcopy(view)
    base = json.loads(base_path.read_text())
    expected = next(row for row in base["containers"] if row["id"] == selected)
    result = models.merge_generator_records(view, base_path, [selected])
    assert view == before
    assert result["cost_usd"] == view["cost_usd"]
    assert result["questions"] == [question for question in view["questions"] if question["box"] != selected]
    assert result["complete"] is False
    assert result["raw"]["schema"] == "stowlm.functional-generator-merge/1"
    assert result["raw"]["source_intake"] == before
    assert result["raw"]["generator_rows"] == [expected]
    assert result["raw"]["cost_usd"] == 0.0
    assert result["raw"]["reason"] == "planner chose the generator's record (not read by a model)"
    assert result["raw"]["base_instance_sha256"] == sha256_json(base)
    assert result["raw"]["base_file_sha256"] == hashlib.sha256(base_path.read_bytes()).hexdigest()
    assert next(row for row in result["rows"] if row["id"] == selected) == {**expected, "origin": "generator"}
    assert all(row["origin"] == "spark" for row in result["rows"] if row["id"] != selected)
    assert result["generator_choices"][0]["closed_questions"] == [question for question in view["questions"] if question["box"] == selected]


@pytest.mark.parametrize("invalid", ["missing_dimension", "zero_dimensions", "null_oog"])
def test_generator_oog_choice_requires_explicit_positive_verified_dimensions(synthetic_oog_generator_choice, invalid):
    view, base_path, selected = synthetic_oog_generator_choice
    base = json.loads(base_path.read_text())
    row = next(row for row in base["containers"] if row["id"] == selected)
    if invalid == "missing_dimension":
        row["oog"].pop("over_width_port_m")
    elif invalid == "zero_dimensions":
        row["oog"] = {key: 0 for key in row["oog"]}
    else:
        row["oog"] = None
    base_path.write_text(json.dumps(base))
    with pytest.raises(ValueError):
        models.merge_generator_records(view, base_path, [selected])


def test_generator_choice_persists_and_routes_other_cargo_answers(synthetic_oog_generator_choice, monkeypatch):
    view, base_path, selected = synthetic_oog_generator_choice
    result = json.loads(json.dumps(models.merge_generator_records(view, base_path, [selected])))
    other = next(question for question in result["questions"] if question["box"] not in (None, selected))
    generated = next(row for row in result["rows"] if row["id"] == selected)
    from experiments.mvp.pipeline import intake
    calls = []

    def answer_questions(source, answers):
        # This policy fixture intentionally supplies an OOG gate in isolation.
        # Keep that state stable while observing dispatch of an unrelated answer.
        calls.append(deepcopy(answers))
        removed = next(index for index, row in enumerate(source.rows) if row["id"] == other["box"])
        rows = [row for index, row in enumerate(source.rows) if index != removed]
        questions = [question.model_copy(update={"row": question.row - 1})
                     if question.row is not None and question.row > removed else question
                     for question in source.questions if question.row != removed]
        record = source.records[-1].model_copy(update={"cost_usd": 0.0, "wall_s": 0.01})
        return source.model_copy(update={"rows": rows, "questions": questions, "records": [*source.records, record]}, deep=True)

    monkeypatch.setattr(intake, "answer_questions", answer_questions)
    final = models.answer_papers(result, {other["id"]: "shut_out"})
    assert calls == [{other["id"]: "shut_out"}]
    assert next(row for row in final["rows"] if row["id"] == selected) == generated
    assert other["box"] not in {row["id"] for row in final["rows"]}
    assert final["boxes"] == result["boxes"] - 1
    assert final["cost_usd"] == result["cost_usd"]
    assert final["generator_choices"] == result["generator_choices"]
    assert final["raw"]["source_intake"]["raw"]["records"][:-1] == result["raw"]["source_intake"]["raw"]["records"]
    json.dumps(final)


def test_booking_merge_preserves_generator_choices_and_routes_unselected_booking_answers(synthetic_oog_generator_choice, booking_views):
    view, base_path, selected = synthetic_oog_generator_choice
    overlaid = models.merge_generator_records(view, base_path, [selected])
    merged = json.loads(json.dumps(models.merge_intake_views([booking_views[0], overlaid])))
    assert merged["raw"]["bookings"][1]["view"]["raw"] == overlaid["raw"]
    assert merged["cost_usd"] == pytest.approx(booking_views[0]["cost_usd"] + overlaid["cost_usd"])
    assert merged["generator_choices"][0]["box"] == selected
    assert all(row["origin"] in ("generator", "spark") for row in merged["rows"])
    first_boxes = {row["id"] for row in booking_views[0]["rows"]}
    question = next(question for question in merged["questions"] if question["box"] in first_boxes)
    final = models.answer_papers(merged, {question["id"]: "shut_out"})
    assert final["raw"]["bookings"][1]["view"] == merged["raw"]["bookings"][1]["view"]
    assert next(row for row in final["rows"] if row["id"] == selected)["origin"] == "generator"
    assert final["cost_usd"] == merged["cost_usd"]


def test_generator_choice_never_resolves_a_duplicate_booking_box(synthetic_oog_generator_choice):
    view, base_path, selected = synthetic_oog_generator_choice
    merged = models.merge_intake_views([view, deepcopy(view)])
    assert any(question["field"] == "_duplicate" and question["box"] == selected for question in merged["questions"])
    with pytest.raises(ValueError):
        models.merge_generator_records(merged, base_path, [selected])



def test_generator_choice_before_or_after_booking_merge_updates_effective_summaries(saved_generator_merge_case):
    b2, base_path, selected = saved_generator_merge_case
    first = base_path.parent / "intake/bookings/SYN-149c026dbad8683a-B1-6194b8b9f521e804.json"
    b1 = json.loads(first.read_text())
    source = models.merge_intake_views([b1, b2])
    after_merge = models.merge_generator_records(source, base_path, [selected])
    before_merge = models.merge_intake_views([b1, models.merge_generator_records(b2, base_path, [selected])])
    assert after_merge["raw"]["source_intake"] == source
    assert after_merge["raw"]["source_intake"]["raw"]["bookings"] == source["raw"]["bookings"]
    for result in (after_merge, before_merge):
        assert result["complete"] is True
        assert result["questions"] == []
        assert result["bookings"][1]["complete"] is True
        assert result["bookings"][1]["boxes"] == b2["boxes"]
        assert result["cost_usd"] == source["cost_usd"]
        assert next(row for row in result["rows"] if row["id"] == selected)["origin"] == "generator"
        assert result["generator_choices"][0]["box"] == selected
    assert after_merge["rows"] == before_merge["rows"]


def test_generator_choices_accumulate_without_losing_prior_audit(synthetic_oog_generator_choice):
    from experiments.mvp.pipeline.contract.schema import sha256_json
    view, base_path, first = synthetic_oog_generator_choice
    view = deepcopy(view)
    base = json.loads(base_path.read_text())
    other = next(row["id"] for row in view["rows"] if row["id"] != first)
    index = next(index for index, row in enumerate(view["rows"]) if row["id"] == other)
    view["raw"]["questions"] = [question for question in view["raw"]["questions"] if question["row"] != index]
    view["questions"] = [question for question in view["questions"] if question["box"] != other]
    text = f"{other}: listed OOG cargo requires verified dimensions; an absent model reading cannot declare it in gauge."
    raw_question = {"id": f"{other}:oog:missing_oog_declaration", "row": index, "field": "oog",
                    "reason": "missing_oog_declaration", "prompt": text, "candidates": []}
    view["raw"]["questions"].append(raw_question)
    view["questions"].append({"id": raw_question["id"], "box": other, "field": "oog", "text": text})
    view["receipt_sha256"] = sha256_json(view["raw"])
    dimensions = deepcopy(next(row["oog"] for row in base["containers"] if row["id"] == first))
    next(row for row in base["containers"] if row["id"] == other).update(oog=dimensions, category=None)
    base_path.write_text(json.dumps(base))
    initial = models.merge_generator_records(view, base_path, [first])
    final = models.merge_generator_records(json.loads(json.dumps(initial)), base_path, [other])
    assert final["raw"]["source_intake"] == initial["raw"]["source_intake"]
    assert final["generator_choices"][0] == initial["generator_choices"][0]
    assert {choice["box"] for choice in final["generator_choices"]} == {first, other}
    assert {row["id"] for row in final["raw"]["generator_rows"]} == {first, other}
    assert final["cost_usd"] == initial["cost_usd"] == view["cost_usd"]


def test_saved_generator_choice_resumes_in_a_fresh_guarded_process(saved_generator_merge_case, tmp_path):
    import os
    import subprocess
    view, base_path, selected = saved_generator_merge_case
    chosen = models.merge_generator_records(view, base_path, [selected])
    path = tmp_path / "generator-choice.json"
    path.write_text(json.dumps(chosen))
    child = '''
import importlib.abc
import json
from pathlib import Path
import socket
import sys
import urllib.request
import requests
from llm.spark.spend import SpendLedger

def forbidden(*args, **kwargs):
    raise AssertionError("Generator choice resume attempted network or managed docs accounting")
socket.create_connection = forbidden
socket.socket.connect = forbidden
urllib.request.urlopen = forbidden
requests.post = forbidden
SpendLedger._compute_text = forbidden
SpendLedger._sync_compute = forbidden

class NoModelImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mlx', 'mlx_lm'}:
            raise AssertionError("Generator choice resume attempted a model import")
sys.meta_path.insert(0, NoModelImports())
from app.functional_models import answer_papers
source = json.loads(Path(sys.argv[1]).read_text())
print(json.dumps(answer_papers(source, {})))
'''
    env = os.environ.copy()
    for key in ("MODEL_API_KEY", "SPARK_CREDIT_READY", "STOWLM_ACCEPT_PAID_CALLS"):
        env.pop(key, None)
    env.update(UV_OFFLINE="1", UV_CACHE_DIR="/private/tmp/fa-models-uv-cache")
    completed = subprocess.run(["uv", "run", "--offline", "python", "-c", child, str(path)],
                               text=True, capture_output=True, cwd=Path(__file__).resolve().parents[2],
                               env=env, timeout=20)
    assert completed.returncode == 0, completed.stderr
    final = json.loads(completed.stdout)
    assert final["rows"] == chosen["rows"]
    assert final["generator_choices"] == chosen["generator_choices"]
    assert final["cost_usd"] == chosen["cost_usd"]
    assert final["complete"] is True
    assert final["raw"]["generator_rows"] == chosen["raw"]["generator_rows"]
    assert final["raw"]["source_intake"]["raw"]["records"][:-1] == chosen["raw"]["source_intake"]["raw"]["records"]



def test_generator_choice_completes_all_five_unique_saved_bookings_without_rereading():
    call = Path(__file__).resolve().parents[2] / "experiments/mvp/ui/functional/calls/c20261103-212438-199686"
    names = ["B1-6194b8b9f521e804", "B2-fd88061f0f769900", "B3-77419548423ecb09",
             "B4-b9847273f523a6ad", "B5-d983c03f4d5ef2ae"]
    paths = [call / f"intake/bookings/SYN-149c026dbad8683a-{name}.json" for name in names]
    if any(not path.is_file() for path in paths):
        pytest.skip("The requested local synthetic booking receipts are not present in this clone")
    views = [json.loads(path.read_text()) for path in paths]
    merged = models.merge_intake_views(views)
    assert merged["boxes"] == 25
    assert len(merged["questions"]) == 1
    original_bookings = deepcopy(merged["raw"]["bookings"])
    result = models.merge_generator_records(merged, call / "base-instance.json", ["QZXU0000117"])
    assert result["complete"] is True
    assert result["questions"] == []
    assert result["boxes"] == 25
    assert all(booking["complete"] for booking in result["bookings"])
    assert result["cost_usd"] == merged["cost_usd"]
    assert result["raw"]["source_intake"]["raw"]["bookings"] == original_bookings
    assert {row["id"] for row in result["rows"] if row["origin"] == "generator"} == {"QZXU0000117"}
    assert all(row["origin"] == "spark" for row in result["rows"] if row["id"] != "QZXU0000117")


def _null_setpoint_case(tmp_path):
    """A one-box booking whose synthetic paper quotes masses beside a null reefer set point: the real intake's own
    unit check raises decimal.InvalidOperation on Decimal(str(None)) (seen live on 4 Nov in one of eight bookings)."""
    from stowage.model.fixtures import build_instance
    base = build_instance().model_dump(mode="json")
    box = base["load_list"][0]
    (tmp_path / "base.json").write_text(json.dumps(dict(base, load_list=[box])))
    paper = tmp_path / "B1-shipping-instructions.txt"
    paper.write_text(f"SYNTHETIC DOCUMENT\n{box}; ISO 22G1; POL THLCH; EMPTY NO; REEFER NO; TARE 2350.0 kg; "
                     "MAX GROSS 30480.0 kg; SETPOINT NONE (non-operating)\n")
    document = dict(name=paper.name, path=str(paper), media_type="text/plain", role="shipping_instructions", booking_ref="B1")
    return box, paper, document


def test_an_answer_the_intake_cannot_turn_into_rows_is_a_retryable_booking_error(tmp_path):
    from llm.spark.client import SparkResponse
    from llm.spark.spend import Usage
    box, paper, document = _null_setpoint_case(tmp_path)
    quote = paper.read_text().splitlines()[1]

    class Client:  # only the model's answer is faked; the frozen intake evaluates it for real
        def complete_documents(self, content, **kwargs):
            observation = dict(row_id=box, field="reefer_setpoint_c", value=None, document=paper.stem, quote=quote)
            answer = dict(schema_version="stowlm.intake-interpretation/7", header_maps=[], observations=[observation],
                          exceptions=[], notes=[])
            return SparkResponse(json.dumps(answer), Usage(100, 20), Decimal(".01"), "synthetic-interpretation")

    with pytest.raises(models.AnswerConversionError) as raised:
        models.read_papers([document], tmp_path / "base.json", client=Client(), grant=object())
    error = raised.value
    assert error.retry_booking is True and error.stage == "spark" and isinstance(error, models.ModelError)
    assert error.reason.startswith("The app couldn't turn Muse Spark's reading of this booking into load-list rows (InvalidOperation)")
    assert "Retry this booking" in error.reason and "None" not in error.reason
    assert type(error.__cause__).__name__ == "InvalidOperation"  # the worker log keeps the original


def test_an_error_before_any_answer_is_not_called_a_conversion_failure(tmp_path):
    _, _, document = _null_setpoint_case(tmp_path)

    class Client:
        def complete_documents(self, content, **kwargs):
            raise KeyError("a client bug before any answer")

    with pytest.raises(KeyError):
        models.read_papers([document], tmp_path / "base.json", client=Client(), grant=object())
