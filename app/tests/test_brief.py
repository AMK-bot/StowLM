"""Brief flow (app/brief.py, stage-brief.js): confirmation gate, labels from evidence, plan gates, refusals, spend.

No test reaches a network or a paid model: readers and transcribers are stubs, and the recorded-mode test fences
sockets. Replan processes are small stub scripts. One test runs the real gate on a stored plan with the check memo on.
"""
import io
import json
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.brief as brief
from app.brief import BriefService, NO_STORED_PLAN, ROOT, reader_label, rule_text
from app.server import make_handler
from experiments.mvp.reader.pipeline import GlimmerParser
from experiments.mvp.reader.schema import OnePortPerHatch, refusal

HATCH_SHIP = "sim.small.40100002"
TIER_SHIP = "random.tiny.20000008.0.5"
REFUSAL_SHIP = "random.small.40100016.0.5"
HATCH = "One port per hatch."
HATCH_ENVELOPE = {"schema_version": "stowlm.reader/2", "constraint": {"type": "one_port_per_hatch"}}
SPARK_EVIDENCE = {"parser": "SparkParser", "model": "muse-spark-1.3", "adapter_sha256": None, "request_id": "req-1", "raw": "SECRET-RAW"}
ADAPTER = "a" * 64


class StubParser:
    def __init__(self, answer=None, evidence=None):
        self.answer = answer if answer is not None else OnePortPerHatch(type="one_port_per_hatch")
        self.evidence = dict(SPARK_EVIDENCE if evidence is None else evidence)
        self.calls = []

    def parse(self, sentence, context):
        self.calls.append((sentence, context["id"]))
        return self.answer, dict(self.evidence)


class FakeDemo:
    def __init__(self):
        self.registered = []

    def register(self, inputs, replay=None):
        self.registered.append(inputs)
        return f"plan-{len(self.registered)}"


def service(**options):
    extra = {key: options.pop(key) for key in ("transcriber", "submit", "replan_command", "registry") if key in options}
    extra.setdefault("submit", lambda _key, _fn: None)
    return BriefService(FakeDemo(), options=options, **extra)


def read(svc, sentence=HATCH, call=HATCH_SHIP, edited=None):
    draft = svc.draft(sentence, call)
    return svc.confirm(draft["draft"], sentence if edited is None else edited)


def wav_bytes(seconds=1.0, rate=16000, channels=1, width=2, value=1200):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(width)
        clip.setframerate(rate)
        clip.writeframes(value.to_bytes(width, "little", signed=True) * channels * int(seconds * rate))
    return stream.getvalue()


# ---------------------------------------------------------------- confirmation gate
def test_nothing_is_parsed_before_confirm_and_the_edited_text_is_what_is_parsed():
    parser = StubParser()
    svc = service(parser=parser)
    draft = svc.draft("One prt per hatch", HATCH_SHIP)
    assert draft["status"] == "awaiting_confirmation" and parser.calls == []
    view = svc.confirm(draft["draft"], HATCH)
    assert parser.calls == [(HATCH, HATCH_SHIP)] and view["sentence"] == HATCH


def test_a_draft_is_single_use_and_a_stale_draft_is_refused():
    svc = service(parser=StubParser())
    draft = svc.draft(HATCH, HATCH_SHIP)
    with pytest.raises(ValueError):
        svc.confirm("not-this-draft", HATCH)
    with pytest.raises(ValueError):
        svc.confirm(draft["draft"], HATCH)


def test_blank_confirmation_never_reaches_the_reader():
    parser = StubParser()
    svc = service(parser=parser)
    assert svc.confirm(svc.draft(HATCH, HATCH_SHIP)["draft"], "   ")["status"] == "typed_required"
    assert parser.calls == []


def test_spoken_draft_uses_the_same_gate_and_deletes_its_temp_file():
    seen = {}

    class Transcriber:
        def transcribe(self, path):
            seen["path"], seen["bytes"] = Path(path), Path(path).read_bytes()
            return {"transcript": "One port per hatch"}
    parser = StubParser()
    payload = wav_bytes()
    svc = service(parser=parser, live=True, transcriber=Transcriber())
    draft = svc.audio_draft(payload, HATCH_SHIP)
    assert seen["bytes"] == payload and not seen["path"].exists()
    assert draft["status"] == "awaiting_confirmation" and draft["heard_by"] == "Muse Voice Transcribe · cloud call"
    assert parser.calls == []
    svc.confirm(draft["draft"], "One port per hatch.")
    assert parser.calls == [(HATCH, HATCH_SHIP)]


def test_a_failed_transcription_falls_back_to_typed_entry():
    class Broken:
        def transcribe(self, path):
            raise RuntimeError("provider said: secret detail")
    svc = service(parser=StubParser(), live=True, transcriber=Broken())
    out = svc.audio_draft(wav_bytes(), HATCH_SHIP)
    assert out["status"] == "typed_required" and "secret" not in json.dumps(out)


def test_speech_is_off_in_recorded_mode_and_nothing_is_sent():
    class Never:
        def transcribe(self, path):
            raise AssertionError("no ASR in recorded mode")
    out = service(transcriber=Never()).audio_draft(wav_bytes(), HATCH_SHIP)
    assert out["status"] == "typed_required" and "--brief-live" in out["message"]


@pytest.mark.parametrize("payload", [wav_bytes(channels=2), wav_bytes(rate=8000), wav_bytes(seconds=31), wav_bytes(value=0), b"not a wav"])
def test_audio_gate_refuses_anything_but_mono_pcm16_up_to_30_s(payload):
    with pytest.raises(ValueError):
        brief.wav_info(payload)


# ---------------------------------------------------------------- labels from evidence
def test_label_comes_from_evidence_only():
    assert reader_label(SPARK_EVIDENCE)["label"] == "Muse Spark · Meta Model API · cloud"
    glimmer = reader_label({"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": ADAPTER})
    assert glimmer["label"] == "StowLM Reader · fine-tuned Muse Glimmer 30B · on this laptop" and glimmer["on_device"]


@pytest.mark.parametrize("evidence", [None, {}, {"parser": "RulesParser", "model": "x"},
                                      {"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": None},
                                      {"parser": "GlimmerParser", "model": "other", "adapter_sha256": ADAPTER},
                                      {"parser": "SparkParser", "model": "muse-spark-1.3", "adapter_sha256": ADAPTER}])
def test_unknown_evidence_gets_a_neutral_label_never_on_this_laptop(evidence):
    label = reader_label(evidence)
    assert label["kind"] == "unknown" and "laptop" not in label["label"] and "Spark" not in label["label"]


def test_swapping_the_parser_object_swaps_the_label_with_no_code_change():
    spark = read(service(parser=StubParser()))
    text = json.dumps({"schema_version": "stowlm.reader/2", "constraint": {"type": "one_port_per_hatch"}})
    glimmer = read(service(parser=GlimmerParser(lambda _sentence, _context: text, adapter_sha256=ADAPTER)))
    odd = read(service(parser=StubParser(evidence={"parser": "SomethingElse"})))
    assert spark["reader"]["kind"] == "muse_spark" and not spark["reader"]["on_device"]
    assert glimmer["reader"]["kind"] == "stowlm_reader" and "on this laptop" in glimmer["reader"]["label"]
    assert glimmer["evidence"]["adapter_sha256"] == ADAPTER
    assert odd["reader"]["kind"] == "unknown"
    assert all(view["answer"] == HATCH_ENVELOPE for view in (spark, glimmer, odd))


def test_evidence_shown_to_the_browser_never_carries_raw_model_text():
    assert "SECRET-RAW" not in json.dumps(read(service(parser=StubParser())))


def test_glimmer_hook_builds_without_loading_a_model_and_hashes_the_adapter(tmp_path):
    (tmp_path / "adapters.safetensors").write_bytes(b"weights")
    parser = brief.make_reader({"reader": "glimmer", "reader_adapter": str(tmp_path)})()
    assert isinstance(parser, GlimmerParser) and parser.adapter_sha256 == brief._sha(tmp_path / "adapters.safetensors")
    with pytest.raises(ValueError):
        brief.make_reader({"reader": "glimmer"})


def test_plan_label_says_search_software_and_never_stowlm():
    assert brief.plan_label({"method": "frontier.alns"}) == "search software (Frontier engine)"
    assert "StowLM" not in brief.plan_label({"method": "stowlm"})
    assert brief.credits_line(reader_label(SPARK_EVIDENCE)) == "Muse Spark read it; search software planned it; the checker passed it"


@pytest.mark.parametrize("constraint,words", [
    ({"type": "close_bay", "bay": 1}, "Nothing loads into bay 01."),
    ({"type": "max_tiers", "bay": 2, "zone": "deck", "max_tiers": 0}, "No containers on deck in bay 02."),
    ({"type": "max_tiers", "bay": None, "zone": "hold", "max_tiers": 1}, "At most 1 tier per stack in the hold in every bay."),
    ({"type": "dg_keep_away", "imdg_class": "8", "min_bays": 1, "away_from": {"kind": "bay", "bay": 1}}, "IMDG class 8 stays at least 1 bay from bay 01."),
])
def test_rule_in_plain_words(constraint, words):
    assert rule_text({"constraint": constraint}) == words


# ---------------------------------------------------------------- matching
def test_match_needs_the_same_constraint_on_the_identical_instance():
    svc = service()
    hatch_ship = svc.ship(HATCH_SHIP)
    assert svc.match(hatch_ship, HATCH_ENVELOPE)["id"] == "one_port_per_hatch-05"
    assert svc.match(svc.ship(REFUSAL_SHIP), HATCH_ENVELOPE) is None
    other = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}
    assert svc.match(hatch_ship, other) is None
    changed = dict(hatch_ship, call=dict(hatch_ship["call"], instance_digest="0" * 64))
    assert svc.match(changed, HATCH_ENVELOPE) is None


def test_different_words_for_the_same_rule_match_and_say_so():
    svc = service(parser=StubParser())
    svc._gates["one_port_per_hatch-05"] = {"status": "shown", "before": {"weighted": 10}, "after": {"weighted": 9}}
    out = svc.plan(read(svc, "Keep every hatch to a single port.")["id"])
    assert out["kind"] == "stored" and out["same_words"] is False and out["stored_sentence"] == HATCH


def test_similar_text_with_another_constraint_gets_no_stored_plan():
    answer = refusal("loosening")
    svc = service(parser=StubParser(answer=answer))
    assert read(svc, "One port per hatch, please.")["status"] == "refused"


# ---------------------------------------------------------------- gates
def fake_replay(passed=True, shippable=True, weighted=9):
    return {"checker": {"passed": passed}, "metadata": {"shippable": shippable},
            "frames": [{"counts": {"loads": 1, "weighted_nonproductive": weighted, "total_lifts_now": 5, "future": []}}]}


@pytest.mark.parametrize("after,after_broken,before_clean,word", [
    (fake_replay(passed=False), [], False, "full checker"),
    (fake_replay(shippable=False), [], False, "shipping"),
    (fake_replay(), ["still mixed"], False, "rule as read"),
    (fake_replay(), [], True, "already obeyed"),
])
def test_any_failed_gate_means_not_shown_and_nothing_registered(monkeypatch, after, after_broken, before_clean, word):
    svc = service()
    case = svc.registry["cases"][0]
    replays = iter([after, fake_replay(weighted=10)])
    monkeypatch.setattr("app.backend.build_replay", lambda *_a, **_k: next(replays))
    results = iter([after_broken, [] if before_clean else ["mixed"]])
    monkeypatch.setattr(brief, "honoured", lambda *_a: next(results))
    monkeypatch.setattr("app.stage_instruction.boxes_moved", lambda *_a: 3)
    view = svc.gate_case(case)
    assert view["status"] == "not_shown" and any(word in why for why in view["why"])
    assert view["before"] is None and view["after"] is None and svc.demo.registered == []


def test_passing_gate_registers_both_plans_with_search_software_labels(monkeypatch):
    svc = service()
    case = svc.registry["cases"][0]
    replays = iter([fake_replay(), fake_replay(weighted=10)])
    monkeypatch.setattr("app.backend.build_replay", lambda *_a, **_k: next(replays))
    results = iter([[], ["mixed"]])
    monkeypatch.setattr(brief, "honoured", lambda *_a: next(results))
    monkeypatch.setattr("app.stage_instruction.boxes_moved", lambda *_a: 13)
    view = svc.gate_case(case)
    assert view["status"] == "shown" and (view["before"]["weighted"], view["after"]["weighted"], view["delta_weighted"]) == (10, 9, -1)
    assert view["plan_label"] == "search software (Frontier engine)"
    assert view["after"]["plan_id"] == "plan-2" and len(svc.demo.registered) == 2
    assert all("StowLM" not in inputs.metadata["label"] and inputs.metadata["planner"] == "frontier.alns" for inputs in svc.demo.registered)


def test_a_stored_plan_file_that_changed_is_not_shown(tmp_path):
    registry = json.loads(brief.REGISTRY.read_text())
    registry["cases"][0]["after"]["sha256"] = "0" * 64
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry))
    svc = service(registry=path, submit=lambda _key, _fn: None)
    svc.registry["cases"] = registry["cases"][:1]
    svc._gates = {registry["cases"][0]["id"]: {"status": "pending"}}
    svc._cards = {"status": "pending", "cards": []}
    svc.run_gates()
    gate = svc.gate_status()[registry["cases"][0]["id"]]
    assert gate["status"] == "not_shown" and svc.demo.registered == []


def test_a_real_stored_plan_passes_the_real_gate_with_the_memo_on(monkeypatch):
    monkeypatch.delenv("STOWLM_NO_CHECK_CACHE", raising=False)
    svc = service()
    view = svc.gate_case(svc.registry["cases"][0])
    assert view["status"] == "shown" and view["binding"] is True and view["honoured"] is True
    assert (view["before"]["weighted"], view["after"]["weighted"]) == (10.0, 9.0)
    assert view["after_violations"] == [] and view["before_violations"]


def test_plan_view_is_scoped_to_the_rule_as_read():
    svc = service(parser=StubParser())
    svc._gates["one_port_per_hatch-05"] = {"status": "shown", "before": {"weighted": 10}, "after": {"weighted": 9}}
    out = svc.plan(read(svc)["id"])
    assert out["claim_scope"] == "parsed_constraint_execution" and out["sentence_compliance_verified"] is False
    assert out["checked_against"] == "checked against the rule as read"
    assert out["recorded_label"] == "recorded plan, not a live solve"
    assert out["credits"] == "Muse Spark read it; search software planned it; the checker passed it"


def test_nothing_is_offered_until_the_gate_says_shown():
    svc = service(parser=StubParser())
    out = svc.plan(read(svc)["id"])
    assert out["kind"] == "stored" and out["gate"]["status"] == "pending" and out["credits"] is None


def test_a_refused_reading_cannot_be_planned():
    svc = service(parser=StubParser(answer=refusal("ambiguous")))
    with pytest.raises(ValueError):
        svc.plan(read(svc)["id"])


# ---------------------------------------------------------------- no stored plan
def test_no_stored_plan_says_so_plainly_and_offers_no_plan():
    svc = service(parser=StubParser())
    out = svc.plan(read(svc, HATCH, REFUSAL_SHIP)["id"])
    assert out["kind"] == "none" and out["message"] == NO_STORED_PLAN and "gate" not in out
    assert out["replan_available"] is False
    assert out["claim_scope"] == "parsed_constraint_execution"


def test_hero_sentence_reads_as_pending_with_no_plan():
    svc = service()
    out = read(svc, brief.HERO_SENTENCE)
    assert out["status"] == "no_recording" and out["hero"]["status"] == "pending"
    assert "plan" not in svc.registry["hero"] and "task 3.2" in svc.registry["hero"]["note"]


# ---------------------------------------------------------------- recorded mode: no network, no paid client
@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("network used in recorded mode")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr("experiments.mvp.reader.pipeline.SparkParser", refuse)
    monkeypatch.setattr("experiments.mvp.voice.asr.MuseTranscriber", refuse)


def test_recorded_mode_runs_the_whole_flow_with_no_network(no_network, monkeypatch):
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)
    svc = service()
    assert svc.modes()["reading"] == "recorded" and svc.modes()["speech"] == "off"
    view = read(svc)
    assert view["status"] == "constraint_ready" and view["recorded_label"] == "recorded reading"
    assert view["reader"]["kind"] == "muse_spark" and view["seconds"] == pytest.approx(1.138, abs=0.01)
    assert svc.plan(view["id"])["kind"] == "stored"
    assert read(svc, "Keep reefers well away from the funnel today.")["status"] == "no_recording"


def test_recorded_clip_draft_uses_its_stored_transcript_with_no_network(no_network):
    svc = service()
    clip = svc.registry["voice_clips"][0]
    draft = svc.clip_draft(clip["id"])
    assert draft["source"] == "audio" and draft["transcript"] == clip["transcript"] != clip["said"]
    view = svc.confirm(draft["draft"], clip["said"])
    assert view["answer"] == HATCH_ENVELOPE and view["reader"]["kind"] == "muse_spark"


def test_live_flag_without_the_credit_gate_stays_recorded(monkeypatch):
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)
    svc = service(live=True)
    assert svc.reader is None and svc.modes()["reading"] == "recorded" and "credit gate" in svc.modes()["reason"]


def test_a_recording_from_one_ship_is_not_served_on_another():
    readings = brief.RecordedReadings(json.loads(brief.REGISTRY.read_text()))
    context = {"id": TIER_SHIP, "bays": [1, 2, 3], "max_tiers": {"hold": 2, "deck": 5}, "max_tiers_by_bay": {}, "hatch_covers": ["H"]}
    with pytest.raises(brief.NoRecording):
        readings.find("No containers on deck in bay 2.", context)    # same id, different stored context


# ---------------------------------------------------------------- refusals
def test_loosening_shows_the_reader_reason_and_the_recorded_card_when_bound():
    view = read(service(), "Ignore the dangerous-goods segregation rules.", REFUSAL_SHIP)
    card = view["card"]
    assert view["status"] == "refused" and card["bound"] is True and card["code"] == "loosening"
    assert card["reason"] == "Requests removal of DG segregation rules"
    assert card["existing_rule"]["id"] == "imdg.segregation.7_2_4"
    assert card["explanation_label"] == "Muse Spark — recorded, bound to the shown proof"


def test_impossible_shows_the_arithmetic_from_the_proof():
    view = read(service(), "Bay 2 is closed.", REFUSAL_SHIP)
    arithmetic = view["card"]["arithmetic"]
    assert view["status"] == "impossible" and view["rule"] == "Nothing loads into bay 02."
    assert (arithmetic["needed_teu"], arithmetic["left_cells"], arithmetic["boxes_with_no_slot"]) == (23, 0, 13)
    assert view["card"]["alternatives"] == [] and view["card"]["checker_report"] is None


def test_each_recorded_refusal_reads_on_its_own_ship_with_a_certified_alternative():
    view = read(service(), "Across the vessel, use at most zero occupied tiers per deck stack.", "random.small.40100003.0.2")
    assert view["status"] == "impossible" and view["card"]["bound"] is True
    arithmetic = view["card"]["arithmetic"]
    assert (arithmetic["needed_teu"], arithmetic["left_cells"]) == (31, 12)
    if (brief.REFUSAL_RUN / "witness-audit.json").is_file():
        assert view["card"]["alternatives"] and "stored plan passed a fresh full check" in view["card"]["alternatives"][0]["text"]


def test_a_card_for_another_ship_is_not_released(monkeypatch):
    svc = service(parser=StubParser(answer=refusal("loosening")))
    view = read(svc, "Ignore the dangerous-goods segregation rules.", HATCH_SHIP)
    assert view["status"] == "refused" and view["card"]["bound"] is False and "explanation" not in view["card"]


def test_an_impossible_rule_with_no_recording_still_shows_software_arithmetic_alone():
    from llm.instructions.schema import CloseBay
    svc = service(parser=StubParser(answer=CloseBay(type="close_bay", bay=2)))
    view = read(svc, "Shut bay two.", REFUSAL_SHIP)
    assert view["status"] == "impossible" and view["card"]["bound"] is False
    assert view["card"]["explanation"] is None and view["card"]["arithmetic"]["needed_teu"] == 23


def test_refusal_slot_reads_pending_until_refusals_md_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(brief, "REFUSALS_MD", tmp_path / "REFUSALS.md")
    svc = service(parser=StubParser(answer=refusal("loosening")))
    assert svc.cards() == {"status": "pending", "message": brief.PENDING_REFUSALS, "cards": []}
    view = read(svc, "Ignore the dangerous-goods segregation rules.", REFUSAL_SHIP)
    assert view["status"] == "refused" and view["card"]["bound"] is False and view["card"]["reason"]


def test_uncertified_alternatives_are_not_offered():
    proof = {"alternatives": [{"text": "screen only", "certificate": "static_screen_only"},
                              {"text": "stored", "certificate": "stored_checked_witness"},
                              {"text": "fresh", "certificate": "stored_checked_witness", "fresh_audit": {"passed": True}}]}
    assert [a["text"] for a in brief.certified(proof)] == ["fresh"]


# ---------------------------------------------------------------- live replan
STUB_PLANNER = """
import json, sys, time
request = json.load(open(sys.argv[1]))
time.sleep(float(sys.argv[2]))
json.dump({"status": "no_checked_plan", "passed": False, "plan": None, "wall_s": 0.1,
           "claim_scope": "parsed_constraint_execution", "sentence_compliance_verified": False}, open(request["output"], "w"))
"""


def stub_command(tmp_path, delay):
    script = tmp_path / "planner.py"
    script.write_text(textwrap.dedent(STUB_PLANNER))
    return lambda request: [sys.executable, str(script), str(request), str(delay)]


def wait_for(svc, job, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = svc.job(job)
        if view["status"] != "running":
            return view
        time.sleep(0.05)
    raise AssertionError("replan job did not finish")


def test_live_replan_is_off_by_default():
    svc = service(parser=StubParser())
    assert svc.modes()["live_replan"] is False
    with pytest.raises(PermissionError):
        svc.replan(read(svc)["id"])


def test_stub_replan_reports_real_time_and_no_plan_without_any_percent(tmp_path):
    svc = service(parser=StubParser(), live_replan=True, replan_command=stub_command(tmp_path, 0.1))
    job = svc.replan(read(svc, HATCH, REFUSAL_SHIP)["id"])
    assert set(job) == {"id", "status", "stage", "sentence", "rule", "result", "error", "elapsed_s"}
    done = wait_for(svc, job["id"])
    assert done["status"] == "done" and done["result"]["kind"] == "no_plan" and "No checked plan" in done["result"]["message"]
    assert done["result"]["claim_scope"] == "parsed_constraint_execution"
    assert svc.demo.registered == []


def test_replan_runs_one_at_a_time_and_cancel_stops_it(tmp_path):
    from app.flow import ConflictError
    svc = service(parser=StubParser(), live_replan=True, replan_command=stub_command(tmp_path, 60))
    reading = read(svc, HATCH, REFUSAL_SHIP)
    job = svc.replan(reading["id"])
    with pytest.raises(ConflictError):
        svc.replan(reading["id"])
    process = svc._jobs[job["id"]]["process"]
    svc.cancel(job["id"])
    done = wait_for(svc, job["id"])
    assert done["status"] == "cancelled" and done["result"] is None and process.poll() is not None
    assert svc._active is None


def test_a_replan_plan_goes_through_the_same_gate(monkeypatch, tmp_path):
    svc = service(parser=StubParser(), live_replan=True, submit=lambda _key, fn: fn())
    reading = svc._reading(read(svc)["id"])
    plan = json.loads((ROOT / svc.registry["cases"][0]["after"]["file"]).read_text())["plan"]
    job = {"id": "job", "status": "running", "stage": "x", "started": time.monotonic(), "sentence": HATCH, "rule": "r",
           "result": None, "error": None}
    svc._jobs["job"] = job
    monkeypatch.setattr(brief, "gate_plans", lambda *_a: {"shown": False, "why": ["the fresh full checker did not pass it"],
                                                          "before": None, "after": None, "replays": (None, None), "honoured": True})
    svc._after_replan(job, reading, {"status": "checked_plan", "plan": plan, "wall_s": 300.0})
    assert job["result"]["kind"] == "gated" and job["result"]["gate"]["status"] == "not_shown"
    assert job["result"]["credits"] is None and svc.demo.registered == []


def test_worker_runs_the_confirmed_reading_only_on_its_own_sentence_and_ship():
    parser = brief._FixedAnswer(HATCH, HATCH_ENVELOPE, {}, HATCH_SHIP)
    with pytest.raises(ValueError):
        parser.parse("Another sentence.", {"id": HATCH_SHIP})
    with pytest.raises(ValueError):
        parser.parse(HATCH, {"id": "another ship"})


# ---------------------------------------------------------------- request gates (through the real handler)
class MemorySocket:
    def __init__(self, incoming=b""):
        self.incoming = io.BytesIO(incoming)
        self.outgoing = bytearray()

    def makefile(self, mode, *_args, **_kwargs):
        if "r" in mode:
            return self.incoming
        return SimpleNamespace(write=self.outgoing.extend, flush=lambda: None, close=lambda: None, closed=False, writable=lambda: True)

    def sendall(self, data):
        self.outgoing.extend(data)

    def close(self):
        pass


def http(svc, method, path, body=b"", content_type="application/json", headers=()):
    handler = make_handler({}, test_authority="test.invalid", service=FakeDemo())
    handler.demo_service = svc.demo
    handler._brief = svc
    handler.stage_service = lambda self: None
    lines = [f"{method} {path} HTTP/1.1", "Host: test.invalid", f"Content-Length: {len(body)}", f"Content-Type: {content_type}", *headers]
    sock = MemorySocket("\r\n".join(lines).encode() + b"\r\n\r\n" + body)
    handler(sock, ("127.0.0.1", 1), SimpleNamespace())
    head, _, payload = bytes(sock.outgoing).partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(payload or b"null")


def test_post_rejects_cross_origin_wrong_type_oversize_and_extra_fields():
    svc = service()
    body = json.dumps({"text": HATCH}).encode()
    assert http(svc, "POST", "/api/brief/draft", body, headers=["Sec-Fetch-Site: cross-site"])[0] == 403
    assert http(svc, "POST", "/api/brief/draft", body, headers=["Origin: http://evil.invalid"])[0] == 403
    assert http(svc, "POST", "/api/brief/draft", body, content_type="text/plain")[0] == 400
    assert http(svc, "POST", "/api/brief/draft", json.dumps({"text": "x" * 5000}).encode())[0] == 400
    assert http(svc, "POST", "/api/brief/draft", json.dumps({"text": HATCH, "path": "/etc/passwd"}).encode())[0] == 400
    assert http(svc, "POST", "/api/brief/draft", json.dumps({"text": HATCH, "call": "../../x"}).encode())[0] == 404
    assert http(svc, "POST", "/api/brief/draft", body)[0] == 200


def test_audio_upload_is_capped_and_needs_the_wav_type():
    svc = service()
    assert http(svc, "POST", "/api/brief/audio", b"\0" * (brief.MAX_AUDIO_BYTES + 1), content_type="audio/wav")[0] == 400
    assert http(svc, "POST", "/api/brief/audio", wav_bytes(), content_type="application/json")[0] == 400
    status, out = http(svc, "POST", "/api/brief/audio", wav_bytes(), content_type="audio/wav")
    assert status == 200 and out["status"] == "typed_required"


def test_replan_route_is_forbidden_without_the_flag_and_jobs_404():
    svc = service(parser=StubParser())
    reading = read(svc)["id"]
    assert http(svc, "POST", "/api/brief/replan", json.dumps({"reading": reading}).encode())[0] == 403
    assert http(svc, "GET", "/api/brief/jobs/nope")[0] == 404


def test_state_lists_cases_clips_hero_and_refusal_status():
    svc = service()
    status, out = http(svc, "GET", "/api/brief/state")
    assert status == 200 and [c["id"] for c in out["prepared"]][0] == "one_port_per_hatch-05"
    assert out["prepared"][0]["reading"]["reader"]["kind"] == "muse_spark"
    assert out["prepared"][0]["recorded_label"] == "recorded plan, not a live solve"
    assert out["hero"]["status"] == "pending" and out["refusals"]["status"] == "checking"


# ---------------------------------------------------------------- registry
def test_every_registry_case_is_a_freshly_audited_binding_plan_bound_by_hash():
    registry = json.loads(brief.REGISTRY.read_text())
    assert registry["hero"]["status"] == "pending" and registry["cases"]
    for case in registry["cases"]:
        audit = json.loads((ROOT / case["audit"]["file"]).read_text())
        assert audit["passed"] is True and audit["binding"] is True and audit["fresh_process"] is True
        assert brief._sha(ROOT / case["after"]["file"]) == case["after"]["sha256"] == audit["plan_record_sha256"]
        assert brief._sha(ROOT / case["before"]["file"]) == case["before"]["sha256"]
        assert case["after"]["method"].startswith("frontier")
        call = registry["calls"][case["call"]]
        assert brief._sha(ROOT / call["input"]) == call["input_sha256"]


def test_app_flags_default_to_recorded_and_off():
    source = (ROOT / "app/__main__.py").read_text()
    assert '"--brief-live", action="store_true"' in source and '"--brief-live-replan", action="store_true"' in source
    assert brief.make_reader({"live": False, "reader": "recorded"}) is None


# ---------------------------------------------------------------- page, under node
NODE = shutil.which("node")
HARNESS = """
global.window = {};
require(process.argv[2]);
const V = window.StageBriefView;
const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const state = {sentence: 'One port per hatch.', stage: 'reading', recorded: true};
const out = {
  reading: V.directorHtml(data, state), reading2: V.directorHtml(data, state),
  plan: V.directorHtml(data, Object.assign({}, state, {stage: 'plan'})),
  hero: V.directorHtml(data, {sentence: data.hero.sentence, stage: 'plan', recorded: true}),
  refused: V.renderRefusal(null),
  bad: V.planHtml({sentence: 's', rule: 'r', gate: {status: 'not_shown', why: ['the fresh full checker did not pass it']}}),
  prepared: V.preparedList(data),
  wav: Array.from(V.encodeWav([new Float32Array(4800).fill(0.5)], 48000).slice(0, 44)),
  wavLength: V.encodeWav([new Float32Array(4800).fill(0.5)], 48000).length,
  glimmer: V.readerBadge({kind: 'stowlm_reader', on_device: true, label: 'L'}, false),
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_views_render_from_data_and_the_director_state_is_pure(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    registry = json.loads(brief.REGISTRY.read_text())
    gate = {"status": "shown", "before": {"weighted": 10}, "after": {"weighted": 9, "passed": True, "shippable": True},
            "delta_weighted": -1, "honoured": True, "boxes_moved": 13, "plan_label": "search software (Frontier engine)"}
    reading = {"reader": reader_label(SPARK_EVIDENCE), "evidence": {"model": "muse-spark-1.3", "request_id": "req-1"}, "seconds": 1.1}
    data = {"prepared": [{"id": "x", "call": "c", "sentence": HATCH, "rule": "Each hatch cover serves one port.", "constraint": HATCH_ENVELOPE,
                          "reading": reading, "gate": gate, "plan_label": gate["plan_label"], "recorded_wall_s": 324.47,
                          "recorded_label": "recorded plan, not a live solve"}],
            "hero": registry["hero"], "voice_clips": [], "refusals": {"status": "pending", "message": brief.PENDING_REFUSALS, "cards": []},
            "messages": {"rule_as_read": brief.RULE_AS_READ}}
    run = subprocess.run([NODE, str(harness), str(ROOT / "app/static/stage-brief.js")], input=json.dumps(data), capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["reading"] == out["reading2"]
    assert "Muse Spark · Meta Model API · cloud" in out["reading"] and "on this laptop" not in out["reading"]
    assert "Muse Spark read it; search software planned it; the checker passed it" in out["plan"]
    assert "recorded plan, not a live solve" in out["plan"] and "5 min 24 s" in out["plan"] and "StowLM" not in out["plan"]
    assert "pending" in out["hero"].lower() and "task 3.2" in out["hero"]
    assert "refusal evidence pending (task 1.4)" in out["refused"]
    assert "Not shown as a plan" in out["bad"]
    assert [row["kind"] for row in out["prepared"]] == ["plan", "pending"]
    header = bytes(out["wav"])
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE" and int.from_bytes(header[24:28], "little") == 16000
    assert int.from_bytes(header[34:36], "little") == 16 and out["wavLength"] == 44 + 1600 * 2
    assert "on-device" in out["glimmer"]


def test_page_wording_stays_inside_the_contract():
    script = (ROOT / "app/static/stage-brief.js").read_text()
    assert "offline" not in script.lower()
    assert "StowLM planned" not in script
    assert "http://" not in script and "https://" not in script
    assert "percent" not in script and "progress bar" not in script


def test_header_claims_the_laptop_only_from_reading_evidence():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "static"
    assert "on this laptop" not in (static / "stage.html").read_text()  # Spark is a cloud call, so no static claim
    js = (static / "stage-brief.js").read_text()
    assert "if (el && reader && reader.on_device)" in js and "brandFromEvidence(S.reading.reader)" in js
