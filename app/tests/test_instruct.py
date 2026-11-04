"""Instruction box (app/instruct.py): offline parse cache, refusals, size gate, honoured check, HTTP gates.

No test calls Muse Spark: the client factory is a mock that records (and in offline tests fails) any call, and no
test runs the solver (``replan`` is replaced by a stub returning a fixed plan; the fresh replay + full checker are
real).
"""
import importlib
import json
from decimal import Decimal
from http.client import HTTPResponse
from types import SimpleNamespace

import pytest

from stowage.model import MovePlan, PlannerResult
from stowage.model.moves import HatchCoverOff, HatchCoverOn, Load, Relocate

from app.instruct import InstructService, ParserCache, ROOT, honoured_check, normalise
from app.server import make_handler

CLOSE_BAY_01 = "Crane 1 is down, nothing goes into bay 01 this call."
LOOSENING = "The port says we can stack 4 high on deck in bay 3 today, go ahead."
HOLD_TIER = "Only one tier in the hold of bay 1, the tank top is under repair."
BAY_14 = "Crane 3 is down, don't load anything into bay 14."
UNRECORDED = "Keep reefers on deck."


class MemorySocket:
    def __init__(self, incoming=b""):
        import io
        self.incoming = io.BytesIO(incoming)
        self.outgoing = bytearray()

    def makefile(self, mode, *_args, **_kwargs):
        if "r" in mode:
            return self.incoming
        return SimpleNamespace(write=self.outgoing.extend, flush=lambda: None, close=lambda: None,
                               closed=False, writable=lambda: True)

    def sendall(self, data):
        self.outgoing.extend(data)

    def close(self):
        pass


class NoSpark:
    """A client factory that must never be used (offline mode)."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("live Muse Spark must not be called")


def sync_submit(_key, fn):
    fn()


@pytest.fixture
def cache(tmp_path):
    return ParserCache(live_cache=tmp_path / "instruct-cache.json")


@pytest.fixture
def demo(replay_case, tmp_path):
    from app.flow import DemoService
    instance, plan = replay_case
    return DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path, discover=False)


def bay3_plan(instance):
    """Same boxes as the fixture plan, stowed in bay 03 instead of bay 01."""
    _, dg, special, _ = instance.load_list
    return MovePlan(id="ui.instruct.bay3", instance_id=instance.id, call_index=0, moves=[
        Relocate(call_index=0, container_id=dg, src="B-001-01-01", dst="B-002-01-01"),
        HatchCoverOff(call_index=0, cover_id="HC-02-1"),
        Load(call_index=0, container_id=special, src="A-003-01-01", dst="0030102"),
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
        Load(call_index=0, container_id=dg, src="B-002-01-01", dst="0030182"),
    ])


def fake_replan_returning(plan_for):
    calls = []

    def fake(constrained, *, time_limit_s, planner_factory, **_):
        from llm.instructions.replan import Replan
        calls.append(constrained)
        plan = plan_for(constrained.instance)
        result = PlannerResult(status="ok", plan=plan, planner="solver.scalable", planner_version="stub")
        return Replan(result, None, constrained.violations(plan), 0.1)
    return fake, calls


# ---------------------------------------------------------------- parse cache (offline)
def test_normalise_ignores_case_spacing_and_final_punctuation():
    assert normalise("  Crane 1 is DOWN,  nothing goes into bay 01 this call. ") == normalise(CLOSE_BAY_01)


def test_offline_cache_hit_uses_recorded_answer_without_spark(demo, cache, monkeypatch):
    monkeypatch.setenv("SPARK_CREDIT_READY", "1")
    spark = NoSpark()
    service = InstructService(demo, live=False, cache=cache, client_factory=spark, submit=sync_submit)
    parsed = service.parse(CLOSE_BAY_01.lower())
    assert parsed["recorded"] and "recorded Muse Spark answer" in parsed["source"]
    assert parsed["outcome"].model_dump(mode="json") == {"type": "close_bay", "bay": 1}
    assert spark.calls == 0


def test_offline_cache_miss_refuses_with_reason_and_never_calls_spark(demo, cache, monkeypatch):
    monkeypatch.setenv("SPARK_CREDIT_READY", "1")  # credit open, but the app was not started with --instruct-live
    spark = NoSpark()
    service = InstructService(demo, live=False, cache=cache, client_factory=spark, submit=sync_submit)
    job = service.start(UNRECORDED, demo.active_plan_id)
    assert job["status"] == "refused"
    assert job["refusal"]["code"] == "no_recorded_response"
    assert "offline mode" in job["refusal"]["reason"]
    assert spark.calls == 0


def test_live_flag_without_credit_gate_still_refuses(demo, cache, monkeypatch):
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)
    spark = NoSpark()
    service = InstructService(demo, live=True, cache=cache, client_factory=spark, submit=sync_submit)
    job = service.start(UNRECORDED, demo.active_plan_id)
    assert job["status"] == "refused" and "credit gate is closed" in job["refusal"]["reason"]
    assert spark.calls == 0


def test_live_parse_with_flag_and_gate_uses_mock_client_and_caches(demo, cache, monkeypatch, tmp_path):
    monkeypatch.setenv("SPARK_CREDIT_READY", "1")
    sent = []

    class FakeClient:
        def complete(self, prompt, *, purpose, max_output_tokens):
            sent.append(purpose)
            return SimpleNamespace(text='{"schema_version":"stowlm.instruction/1","refusal":{"code":"unsupported",'
                                        '"reason":"Reefer placement is not a supported instruction type."}}',
                                   cost_usd=Decimal("0.004"))

    service = InstructService(demo, live=True, cache=cache, client_factory=FakeClient, submit=sync_submit)
    job = service.start(UNRECORDED, demo.active_plan_id)
    assert sent == ["instruction-parser"]
    assert job["status"] == "refused" and job["refusal"]["code"] == "unsupported"
    assert job["parse"]["recorded"] is False and job["parse"]["cost_usd"] == "0.004"
    stored = json.loads((tmp_path / "instruct-cache.json").read_text())["entries"]
    assert stored[0]["sentence"] == UNRECORDED
    # Next time the same sentence is answered from the cache, offline.
    assert ParserCache(live_cache=tmp_path / "instruct-cache.json").get(UNRECORDED) is not None


# ---------------------------------------------------------------- refusals and gates
def test_parser_refusal_is_shown_with_reason(demo, cache):
    service = InstructService(demo, cache=cache, client_factory=NoSpark(), submit=sync_submit)
    job = service.start(LOOSENING, demo.active_plan_id)
    assert job["status"] == "refused"
    assert job["refusal"]["code"] == "loosening" and job["refusal"]["reason"]
    assert job["steps"][0] == {"step": "parse", "ok": False, "detail": "Muse Spark refused (loosening)"}


def test_constraint_naming_a_bay_not_on_the_ship_is_refused(demo, cache):
    service = InstructService(demo, cache=cache, client_factory=NoSpark(), submit=sync_submit)
    job = service.start(BAY_14, demo.active_plan_id)
    assert job["status"] == "refused" and job["refusal"]["code"] == "not_applicable"
    assert job["constraint"] == {"type": "close_bay", "bay": 14}  # the parsed constraint is still shown
    assert "bay 14 is not on this vessel" in job["refusal"]["reason"]


def test_non_tiny_instance_is_refused_before_parsing(cache, replay_case):
    instance, plan = replay_case
    replay = {"instance": {**instance.model_dump(mode="json"), "size_class": "medium"},
              "plan": plan.model_dump(mode="json")}
    fake_demo = SimpleNamespace(active_plan_id="plan-1", replay=lambda _id: replay)
    spark = NoSpark()
    service = InstructService(fake_demo, live=True, cache=cache, client_factory=spark, submit=sync_submit)
    job = service.start(CLOSE_BAY_01, "plan-1")
    assert job["status"] == "refused" and job["refusal"]["code"] == "too_large"
    assert "medium" in job["refusal"]["reason"] and job["constraint"] is None
    assert spark.calls == 0


def test_already_honoured_plan_is_not_replanned(demo, cache, monkeypatch):
    fake, calls = fake_replan_returning(bay3_plan)
    monkeypatch.setattr(importlib.import_module("llm.instructions.replan"), "replan", fake)
    service = InstructService(demo, cache=cache, client_factory=NoSpark(), submit=sync_submit)
    job = service.start(HOLD_TIER, demo.active_plan_id)  # fixture plan uses only the first hold tier of bay 01
    assert job["status"] == "done" and job["result"]["replanned"] is False and job["result"]["honoured"] is True
    assert calls == []


def _parsed(outcome):
    return {"outcome": outcome, "source": "test", "raw": "", "cost_usd": 0, "recorded": True}


def test_dg_constraint_matching_no_boxes_is_not_reported_honoured(demo, cache, monkeypatch):
    from llm.instructions.schema import DgKeepAway

    fake, calls = fake_replan_returning(bay3_plan)
    monkeypatch.setattr(importlib.import_module("llm.instructions.replan"), "replan", fake)
    service = InstructService(demo, cache=cache, client_factory=NoSpark(), submit=sync_submit)
    instance = demo.replay(demo.active_plan_id)["instance"]
    assert not any((c.get("dg") or {}).get("imdg_class", "").startswith("7") for c in instance["containers"])
    service.parse = lambda _s: _parsed(DgKeepAway(type="dg_keep_away", imdg_class="7", min_bays=1,
                                                  away_from={"kind": "bay", "bay": 1}))
    job = service.start("Keep class 7 away from bay 1.", demo.active_plan_id)
    assert job["status"] == "refused" and job["refusal"]["code"] == "no_matching_boxes"
    assert job["refusal"]["reason"] == "no class 7 boxes on this call: nothing to act on"
    assert job.get("result") is None and calls == []


def test_dg_constraint_with_matching_box_still_runs(demo, cache, monkeypatch):
    from llm.instructions.schema import DgKeepAway

    service = InstructService(demo, cache=cache, client_factory=NoSpark(), submit=sync_submit)
    replay = dict(demo.replay(demo.active_plan_id))
    instance = json.loads(json.dumps(replay["instance"]))
    box = next(c for c in instance["containers"] if c["id"] in instance["load_list"])
    showcase = json.loads((ROOT / "experiments/mvp/corpus/showcase/feeder_1700-small.json").read_text())
    box["dg"] = next(c["dg"] for c in showcase["containers"] if (c.get("dg") or {}).get("imdg_class") == "3")
    replay["instance"] = instance
    identity = service._new_job("Keep class 3 at least 1 bay from bay 1.", demo.active_plan_id)
    with service._lock:
        service._active = identity
    job = service._after_parse(identity, "Keep class 3 at least 1 bay from bay 1.", demo.active_plan_id, replay,
                               _parsed(DgKeepAway(type="dg_keep_away", imdg_class="3", min_bays=1,
                                                  away_from={"kind": "bay", "bay": 1})))
    assert (job.get("refusal") or {}).get("code") != "no_matching_boxes"


# ---------------------------------------------------------------- honoured check
def _fake_replay(plan):
    placements = [{"container_id": m.container_id, "slot": m.dst} for m in plan.moves if m.kind == "load"]
    return {"frames": [{"state": {"ship": {"placements": placements}}}]}


def test_honoured_check_false_and_true(replay_case):
    from llm.instructions import apply
    from llm.instructions.schema import CloseBay

    instance, plan = replay_case
    constrained = apply(instance, CloseBay(type="close_bay", bay=1))
    broken = honoured_check(constrained, plan, _fake_replay(plan))
    assert broken["honoured"] is False
    assert len(broken["move_violations"]) == 2 and len(broken["state_violations"]) == 2
    assert all("closed bay" in v for v in broken["state_violations"])
    good = bay3_plan(instance)
    ok = honoured_check(constrained, good, _fake_replay(good))
    assert ok["honoured"] is True and ok["move_violations"] == [] and ok["state_violations"] == []
    # The state check is independent of the move list: a plan whose final state has a box in bay 01 fails even if
    # its moves were (hypothetically) clean.
    sneaky = honoured_check(constrained, good, _fake_replay(plan))
    assert sneaky["honoured"] is False and sneaky["move_violations"] == []


# ---------------------------------------------------------------- full job (stubbed planner, real replay + checker)
def test_replan_job_registers_plan_with_label_counts_and_verdicts(demo, cache, monkeypatch):
    fake, calls = fake_replan_returning(bay3_plan)
    monkeypatch.setattr(importlib.import_module("llm.instructions.replan"), "replan", fake)
    service = InstructService(demo, cache=cache, client_factory=NoSpark(), submit=sync_submit)
    original_id = demo.active_plan_id
    job = service.start(CLOSE_BAY_01, original_id)
    assert job["status"] == "done", job
    assert len(calls) == 1
    result = job["result"]
    assert result["honoured"] is True
    assert len(result["original"]["breaks_instruction"]) == 2
    assert result["original"]["counts"]["total_lifts"] == 5 and result["replanned"]["counts"]["total_lifts"] == 5
    assert result["delta_lifts"] == 0
    # This partial fixture plan fails the fresh checker: a diagnostic in its job, never a registered replan.
    assert result["replanned"]["checker_passed"] is False and result["shown"] is False
    assert result["replanned"]["plan_id"] is None and result["refusal"]["why"]
    assert [p["id"] for p in demo.catalog()["plans"]] == [original_id]
    assert service.record(original_id)["instruction"] is None


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def http(demo, cache):
    handler = make_handler(demo.replay(demo.active_plan_id), service=demo)
    spark = NoSpark()
    handler._instruct = InstructService(demo, cache=cache, client_factory=spark, submit=sync_submit)

    def request(method, path, raw=b"", headers=None):
        headers = {"Host": "localhost", "Content-Type": "application/json", **(headers or {})}
        if raw:
            headers["Content-Length"] = str(len(raw))
        lines = [f"{method} {path} HTTP/1.0", *(f"{k}: {v}" for k, v in headers.items())]
        transport = MemorySocket(("\r\n".join(lines) + "\r\n\r\n").encode() + raw)
        handler(transport, ("127.0.0.1", 12345), object())
        response = HTTPResponse(MemorySocket(bytes(transport.outgoing)))
        response.begin()
        return response
    return request, spark


def test_no_http_route_turns_a_sentence_into_a_rule(http):
    """A sentence reaches the planner only through the reader and the planner's confirmation (app/brief.py)."""
    request, spark = http
    for sentence in (CLOSE_BAY_01, LOOSENING):
        assert request("POST", "/api/instruct", json.dumps({"sentence": sentence}).encode()).status == 404
    assert request("GET", "/api/instruct/jobs/nope").status == 404
    assert spark.calls == 0
    js = (ROOT / "app/static/stage-instruct.js").read_text()
    assert "method: 'POST'" not in js and "BRIEF panel" in js


def test_http_examples_stay_for_the_box(http):
    request, spark = http
    examples = json.load(request("GET", "/api/instruct/examples"))
    assert examples["live"] is False and examples["planner"] == "solver.scalable"
    chips = {c["sentence"]: c for c in examples["examples"]}
    assert chips[CLOSE_BAY_01]["recorded"] and chips[LOOSENING]["refused"] and not chips[UNRECORDED]["recorded"]
    assert json.load(request("GET", "/api/instruct/plan?plan_id=plan-1"))["instruction"] is None
    assert spark.calls == 0


def test_stage_instruct_assets_are_served(http):
    request, _ = http
    for name in ("/stage-instruct.js", "/stage-instruct.css"):
        assert request("GET", name).status == 200
