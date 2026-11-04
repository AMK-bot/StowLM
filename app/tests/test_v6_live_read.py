"""Demo v6: the live reader reads the hero sentence on the hero's own context, the planner confirms that reading, and the
replan chapter shows the stored plan only when the reading is the stored rule. Fake reader, no GPU, no network."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from app import brief, v6
from app.brief import BriefService, HERO_CALL
from app.server import HTTPServer, make_handler
from experiments.mvp.reader.schema import validate_answer

HATCH_SHIP = "sim.small.40100002"
ADAPTER = "a" * 64
GLIMMER = {"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": ADAPTER, "raw": "SECRET-RAW"}


class FakeReader:
    """Reads every sentence to one envelope, validated on the context it was handed, and remembers that context."""

    def __init__(self, envelope=None):
        self.envelope, self.contexts = envelope, []

    def parse(self, sentence, context):
        self.contexts.append(context)
        envelope = self.envelope or v6.hero()["instruction"]["rule"]
        return validate_answer(json.dumps(envelope), context), dict(GLIMMER)


class FakeDemo:
    def register(self, inputs, replay=None):
        return "plan-1"

    def catalog(self):
        return {"plans": []}


def service(reader, **options):
    return BriefService(FakeDemo(), options={"parser": reader, **options}, submit=lambda _key, _fn: None)


def other_rule() -> dict:
    rule = v6.hero()["instruction"]["rule"]
    bay = next(b for b in v6.hero()["context"]["bays"] if b != rule["constraint"].get("bay"))
    return {"schema_version": rule["schema_version"], "constraint": {"type": "close_bay", "bay": bay}}


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(v6, "CONFIRMATIONS", v6.Confirmations())
    monkeypatch.setattr(v6, "LIVE_READS", v6.LiveReads())


# ------------------------------------------------------------------ the context
def test_the_hero_context_is_what_hero_candidates_read_on():
    """read_all.instance_of on the hero source's path, as hero_candidates.py calls it, and the hero source's own context."""
    config = v6.hero()
    assert "instance_of(sys.argv[1], {})" in v6._CONTEXT_CODE and "reader_final.read_all" in v6._CONTEXT_CODE
    source = json.loads((v6.ROOT / config["hero_source"]).read_text())[config["hero_slot"]]
    assert config["instance"] == source["path"]
    assert v6.hero_reader_context() == source["context"] == config["context"]


def test_v6_offers_the_live_reader_the_hero_context():
    reader = FakeReader()
    svc = service(reader, v6=True)
    sentence = v6.hero()["instruction"]["sentence"]
    draft = svc.draft(sentence, HERO_CALL)
    view = svc.confirm(draft["draft"], sentence)
    assert reader.contexts == [v6.hero_reader_context()]
    assert view["status"] == "constraint_ready" and view["call"] == HERO_CALL and view["recorded"] is False
    assert view["reader"]["kind"] == "stowlm_reader" and "SECRET-RAW" not in json.dumps(view)


def test_outside_v6_the_six_brief_ships_still_read_and_the_hero_is_not_there():
    reader = FakeReader({"schema_version": "stowlm.reader/2", "constraint": {"type": "one_port_per_hatch"}})
    svc = service(reader)
    with pytest.raises(KeyError):
        svc.ship(HERO_CALL)
    calls = svc.state()["calls"]
    assert len(calls) == 6 and HERO_CALL not in {c["id"] for c in calls}
    for call in calls:
        draft = svc.draft("One port per hatch.", call["id"])
        assert svc.confirm(draft["draft"], "One port per hatch.")["status"] == "constraint_ready"
    assert [c["id"] for c in reader.contexts] == [c["id"] for c in calls]


# ------------------------------------------------------------------ confirming a live reading
def test_a_live_reading_on_the_hero_binds_to_sentence_rule_and_ship(fresh):
    svc = service(FakeReader(), v6=True)
    sentence = v6.hero()["instruction"]["sentence"]
    draft = svc.draft(sentence, HERO_CALL)
    view = svc.confirm(draft["draft"], sentence)
    shown = v6.LIVE_READS.add(view, svc._reading(view["id"])["ship"], 1.0)
    assert shown["mode"] == "live" and shown["live_read"] == {"status": "done", "sentence": sentence, "id": view["id"]}
    assert shown["stored"]["shown"] is True and shown["stored"]["key"] == v6.hero()["keys"]["after"]
    binding = shown["binding"]
    assert {k: binding[k] for k, _ in v6._BINDING} == {k: v6._binding_of(v6.hero_reading())[k] for k, _ in v6._BINDING}
    token = v6.CONFIRMATIONS.confirm(binding)
    assert token["reading"] == view["id"] and v6.CONFIRMATIONS.require(token["token"])["reading"] == view["id"]
    with pytest.raises(PermissionError):
        v6.CONFIRMATIONS.confirm({**binding, "rule_sha256": "x" * 64})


def test_a_reading_on_another_ships_context_is_refused(fresh):
    svc = service(FakeReader({"schema_version": "stowlm.reader/2", "constraint": {"type": "one_port_per_hatch"}}), v6=True)
    draft = svc.draft("One port per hatch.", HATCH_SHIP)
    view = svc.confirm(draft["draft"], "One port per hatch.")
    with pytest.raises(PermissionError, match="another ship"):
        v6.LIVE_READS.add(view, svc._reading(view["id"])["ship"])
    forged = {**svc._reading(view["id"])["ship"], "key": HERO_CALL}  # the hero's name on another ship's context
    with pytest.raises(PermissionError, match="another ship"):
        v6.LIVE_READS.add(view, forged)
    with pytest.raises(PermissionError):
        v6.CONFIRMATIONS.confirm({**v6.reading_binding(), "reading": view["id"]})


def test_a_live_reading_read_on_a_context_that_has_since_changed_cannot_be_confirmed(fresh, monkeypatch):
    svc = service(FakeReader(), v6=True)
    sentence = v6.hero()["instruction"]["sentence"]
    view = svc.confirm(svc.draft(sentence, HERO_CALL)["draft"], sentence)
    binding = v6.LIVE_READS.add(view, svc._reading(view["id"])["ship"])["binding"]
    monkeypatch.setattr(v6, "hero_reader_context", lambda: {**v6.hero()["context"], "id": "another"})
    with pytest.raises(PermissionError, match="another ship"):
        v6.CONFIRMATIONS.confirm(binding)


# ------------------------------------------------------------------ confirm -> the stored plan
SHOWN = {"status": "shown", "before": {"plan_id": "b", "passed": True}, "after": {"plan_id": "a", "passed": True}, "boxes_moved": 3, "recheck": "rechecked now"}


def confirmed(svc, sentence) -> str:
    view = svc.confirm(svc.draft(sentence, HERO_CALL)["draft"], sentence)
    binding = v6.LIVE_READS.add(view, svc._reading(view["id"])["ship"])["binding"]
    return v6.CONFIRMATIONS.confirm(binding)["token"]


def test_confirm_then_the_stored_plan_under_the_stored_rule(fresh):
    token = confirmed(service(FakeReader(), v6=True), v6.hero()["instruction"]["sentence"])
    said = v6.reading_gate(token, v6.CONFIRMATIONS.require(token)["reading"])
    assert said["shown"] is True and said["key"] == v6.hero()["keys"]["after"]
    assert v6.gate_for_reading(SHOWN, said) == SHOWN
    assert v6.reading_gate(token, v6.CONFIRMATIONS.require(token)["reading"])["shown"] is True  # looking it up spends nothing


def test_a_live_misreading_shows_no_stored_plan_and_says_why(fresh):
    token = confirmed(service(FakeReader(other_rule()), v6=True), v6.hero()["instruction"]["sentence"])
    said = v6.reading_gate(token, v6.CONFIRMATIONS.require(token)["reading"])
    assert said["shown"] is False and "not the stored rule" in said["why"] and "none is shown" in said["why"]
    gate = v6.gate_for_reading(SHOWN, said)
    assert gate["status"] == "before_only" and gate["why"] == [said["why"]] and "after" not in gate and "boxes_moved" not in gate


def test_a_forgotten_token_shows_no_stored_plan(fresh):
    said = v6.reading_gate("0" * 32)
    assert said["shown"] is False and "not shown" in said["why"]
    assert v6.gate_for_reading(SHOWN, said)["status"] == "before_only"


def test_a_recorded_readings_token_shows_the_recorded_reading_only(fresh):
    token = v6.CONFIRMATIONS.confirm(v6.reading_binding())["token"]
    assert v6.reading_gate(token)["shown"] is True and v6.reading_gate(token)["live"] is False
    assert v6.reading_gate(None)["shown"] is False and v6.reading_gate(None)["needs"] == "confirmation"  # no token: nothing under the rule


# ------------------------------------------------------------------ through the server
@pytest.fixture
def server(fresh, monkeypatch):
    reader = FakeReader()
    handler = make_handler({"ok": True}, FakeDemo(), stage_options={})
    handler.brief_options = {"v6": True, "parser": reader}
    handler._brief = None
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    handler.expected_port = httpd.server_port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def send(route, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/v6/{route}", data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    send.reader, send.handler = reader, handler
    yield send
    httpd.shutdown()
    httpd.server_close()


def test_the_stage_reads_live_then_confirms_that_reading(server):
    sentence = v6.hero()["instruction"]["sentence"]
    status, read = server("read", {"sentence": sentence})
    assert status == 200 and read["mode"] == "live" and read["status"] == "constraint_ready"
    assert server.reader.contexts == [v6.hero_reader_context()] and read["stored"]["shown"] is True
    assert read["reader"]["label"] == "StowLM Reader · fine-tuned Muse Glimmer 30B · on this laptop"
    status, body = server("confirm", read["binding"])
    assert status == 200 and body["reading"] == read["id"] and v6.reading_gate(body["token"], read["id"])["shown"] is True
    assert server("confirm", {**read["binding"], "reading": "nope"})[0] == 403


def test_reading_live_needs_a_live_reader(server):
    server.handler.brief_options = {"v6": True}
    assert server("read", {"sentence": "Nothing loads into bay 01."})[0] == 403
    assert server.reader.contexts == []
