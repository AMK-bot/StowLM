"""Code review 12, P2: no plan under the rule reaches the page for a reading until the planner confirmed exactly that reading, or
the labelled recorded answer was chosen explicitly. A misread live reading never shows the stored plan, even confirmed.

Server side through the in-memory handler (no sockets); the chapter's own state under node. Fake reader, no GPU, no network."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import pytest

from app import v6
from app.tests.test_v6_gates import answer, ask, demo, set_gate, v6_server  # noqa: F401 - demo, v6_server are fixtures
from app.tests.test_v6_live_read import FakeReader, other_rule

STATIC = Path(__file__).resolve().parents[1] / "static"
HARNESS = Path(__file__).with_name("v6_film_harness.js")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
GATE = {"status": "shown", "before": {"plan_id": "b", "passed": True, "lifts": 10.0}, "after": {"plan_id": "a", "passed": True, "lifts": 12.0},
        "boxes_moved": 3, "extra_lifts": 2.0, "forced_boxes": ["X"], "recheck": "rechecked now"}


@pytest.fixture
def server(v6_server, monkeypatch):
    monkeypatch.setattr(v6, "CONFIRMATIONS", v6.Confirmations())
    monkeypatch.setattr(v6, "LIVE_READS", v6.LiveReads())
    monkeypatch.setattr(v6, "RECORDED_SHOWN", v6.RecordedShown())
    set_gate(monkeypatch, GATE)
    v6_server.brief_options = {"v6": True, "parser": FakeReader()}
    v6_server._brief = None
    return v6_server


def post(handler, route, body):
    response = ask(handler, "POST", f"/api/v6/{route}", body)
    return response.status, answer(response)


def read(handler, sentence=None, envelope=None) -> dict:
    handler.brief_options["parser"].envelope = envelope
    status, body = post(handler, "read", {"sentence": sentence or v6.hero()["instruction"]["sentence"]})
    assert status == 200 and body["status"] == "constraint_ready", body
    return body


def replan(handler, **query) -> dict:
    response = ask(handler, "GET", "/api/v6/replan" + ("?" + urlencode(query) if query else ""))
    assert response.status == 200
    return answer(response)


def shown(view) -> bool:
    return view["gate"]["status"] == "shown" and "after" in view["gate"] and view["reading_gate"]["shown"] is True


def refused(view) -> bool:
    gate = view["gate"]
    return (gate["status"] == "before_only" and not {"after", "boxes_moved", "forced_boxes"} & set(gate) and view["facts"] is None
            and view["plans"]["after"] is None and view["reading_gate"]["shown"] is False and view["reading_gate"]["why"] in gate["why"])


def test_a_live_reading_unconfirmed_then_straight_to_the_replan_is_refused(server):
    live = read(server)
    for view in (replan(server), replan(server, reading=live["id"])):
        assert refused(view) and "confirm" in view["reading_gate"]["why"].lower()
    assert v6.RECORDED_SHOWN.rows == []


def test_a_made_up_token_is_refused(server):
    live = read(server)
    assert refused(replan(server, reading=live["id"], token="0" * 32))
    assert refused(replan(server, token="0" * 32))


def test_a_token_for_another_sentence_is_refused(server):
    first, second = read(server), read(server, "Keep bay nine clear for the next port, please.")
    token = post(server, "confirm", first["binding"])[1]["token"]
    assert refused(replan(server, reading=second["id"], token=token))
    assert refused(replan(server, token=token))  # the recorded reading selected, a live reading's token
    hero = post(server, "confirm", v6.reading_binding())[1]["token"]
    assert refused(replan(server, reading=first["id"], token=hero))  # a live reading selected, the recorded reading's token


def test_a_token_for_the_live_replans_own_sentence_does_not_open_the_recorded_reading(server, monkeypatch):
    other = {"sentence": "Another sentence entirely.", "answer": v6.hero()["instruction"]["rule"], "evidence": None, "recorded": True}
    monkeypatch.setattr(v6, "live_reading", lambda: other)
    token = post(server, "confirm", v6._binding_of(other))[1]["token"]
    assert refused(replan(server, token=token))


def test_a_token_for_another_rule_is_refused(server):
    good, wrong = read(server), read(server, "Nothing goes in that bay.", other_rule())
    token = post(server, "confirm", wrong["binding"])[1]["token"]
    assert refused(replan(server, reading=good["id"], token=token))


def test_a_token_for_another_instance_is_refused(server, monkeypatch):
    token = post(server, "confirm", v6.reading_binding())[1]["token"]
    monkeypatch.setattr(v6, "_instance_digest", lambda: "f" * 64)
    view = replan(server, token=token)
    assert refused(view) and "another ship" in view["reading_gate"]["why"]


def test_the_right_token_shows_the_stored_plan(server):
    live = read(server)
    token = post(server, "confirm", live["binding"])[1]["token"]
    view = replan(server, reading=live["id"], token=token)
    assert shown(view) and view["reading_gate"]["live"] is True and view["plans"]["after"]
    hero = post(server, "confirm", v6.reading_binding())[1]["token"]
    view = replan(server, token=hero)
    assert shown(view) and view["reading_gate"]["live"] is False and "label" not in view["reading_gate"]
    assert v6.RECORDED_SHOWN.rows == []


def test_the_recorded_fallback_chosen_explicitly_is_shown_labelled_and_recorded(server):
    read(server)  # whatever the planner read live, the fallback is the recorded reading, by name
    view = replan(server, recorded="1")
    said = view["reading_gate"]
    assert shown(view) and said["fallback"] == "recorded" and said["label"] == "Recorded answer"
    rows = v6.RECORDED_SHOWN.rows
    assert len(rows) == 1 and said["record"] == rows[0]["id"] and rows[0]["label"] == "Recorded answer" and rows[0]["key"] == v6.hero()["keys"]["after"]
    assert {k: rows[0][k] for k, _ in v6._BINDING} == {k: v6.reading_binding()[k] for k, _ in v6._BINDING}
    assert refused(replan(server, recorded="0"))


def test_a_misread_live_reading_never_shows_the_stored_plan_even_confirmed(server):
    wrong = read(server, "Nothing goes in that bay.", other_rule())
    token = post(server, "confirm", wrong["binding"])[1]["token"]
    view = replan(server, reading=wrong["id"], token=token)
    assert refused(view) and "not the stored rule" in view["reading_gate"]["why"]


def test_the_state_call_records_no_fallback(server):
    assert answer(ask(server, "GET", "/api/v6/state"))["features"]["replan"]["available"] is True
    assert v6.RECORDED_SHOWN.rows == []


# ------------------------------------------------------------------ the chapter, under node
def node(expr: str):
    code = (f"global.window = globalThis; let def; global.StageV6 = {{chapter(d) {{ def = d; }}}}; require({json.dumps(str(STATIC / 'stage-story.js'))});"
            f" require({json.dumps(str(STATIC / 'stage-v6replan.js'))}); console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


@needs_node
def test_the_chapter_asks_for_exactly_the_selected_reading():
    q = "def.replanQuery"
    assert node(f"{q}(null, null, true)") == "replan"
    assert node(f"{q}('r1', null, true)") == "replan?reading=r1"
    assert node(f"{q}('r1', {{token: 't'}}, true)") == "replan?reading=r1&token=t"
    assert node(f"{q}(null, {{token: 't'}}, true)") == "replan?token=t"
    assert node(f"{q}('r1', {{token: 't'}}, false)") == "replan?recorded=1"  # the film: the labelled recorded reading, by name
    assert node(f"{q}('r1', null, true, true)") == "replan?recorded=1"


@needs_node
def test_the_chapter_shows_a_plan_only_when_the_server_said_so_for_that_reading():
    s = "def.readingState"
    assert node(f"{s}({json.dumps(GATE)}, {{shown: true}})")["mode"] == "shown"
    held = node(f"{s}({json.dumps(GATE)}, {{shown: false, needs: 'confirmation', why: 'Confirm it'}})")
    assert held == {"mode": "hold", "why": "Confirm it", "label": None, "offerRecorded": True}
    assert node(f"{s}({json.dumps(GATE)}, null)")["mode"] == "hold"  # a server that said nothing shows nothing
    assert node(f"{s}({json.dumps(GATE)}, undefined)")["mode"] == "hold"
    misread = node(f"{s}({json.dumps(GATE)}, {{shown: false, why: 'not the stored rule'}})")
    assert misread["mode"] == "hold" and misread["offerRecorded"] is False
    assert node(f"{s}({json.dumps(GATE)}, {{shown: true, fallback: 'recorded', label: 'Recorded answer'}})")["label"] == "Recorded answer"
    assert node(f"{s}({json.dumps({**GATE, 'status': 'before_only'})}, {{shown: true}})")["mode"] == "hold"
    assert node(f"{s}({{status: 'not_shown'}}, {{shown: true}})")["mode"] == "gap"


def chapter(**scenario) -> dict:
    run = subprocess.run(["node", str(HARNESS)], input=json.dumps({"chapter": "replan", **scenario}), capture_output=True, text=True, check=True, timeout=30)
    return json.loads(run.stdout)


@needs_node
def test_unconfirmed_on_stage_holds_the_plan_before_and_says_why():
    out = chapter(stored_only=True, unconfirmed=True)
    assert "computed on" not in out["labels"] and "Confirm the reading" in out["labels"] and out["split"] == "Standing brief"
    assert out["asked"] == ["replan"] and "Show the recorded answer" in out["go"]


@needs_node
def test_the_film_plays_the_recorded_reading_labelled():
    out = chapter(stored_only=True, video=True)
    assert out["asked"] == ["replan?recorded=1"] and "Stored plan, computed on 1 Nov 2026" in out["labels"] and "Recorded answer" in out["labels"]
