"""Demo v6 copilot chapter: the API carries only the fixtures' words and numbers, the timeline is pure, and the tick lands only on a plan."""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app import v6
from experiments.mvp.pipeline.copilot import replay

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "app/static/stage-v6copilot.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
ORIGINAL = replay.replay_conversation


def node(expr: str, data: dict):
    code = (f"global.window = globalThis; require({json.dumps(str(JS))}); const C = window.StageV6Copilot;"
            f"const D = {json.dumps(data)}; console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


@pytest.fixture(scope="module")
def data():
    return v6.copilot()


@pytest.fixture(scope="module")
def offered():
    """A real-tools recording that holds a counter-offer leads as it is: the chapter's whole offer path, from the doubles' data."""
    patch = pytest.MonkeyPatch()
    doubles = replay.replay_conversation("negotiation")
    real = dict(doubles, turns=doubles["conversation"]["turns"], label="real label")
    real.pop("conversation")
    patch.setattr(replay, "replay_real_conversation", lambda name: real)
    try:
        return v6.copilot()
    finally:
        patch.undo()


def test_labels_are_the_fixtures_own(data):
    assert data["label"] == replay.replay_conversation("negotiation")["label"]
    assert data["attack"]["label"] == replay.replay_real_conversation("attack")["label"]
    assert data["second"]["reader_label"] == replay.replay_real_conversation("negotiation")["reader_label"]


def test_arithmetic_and_offer_come_from_the_proof(data, offered):
    proof = replay.replay_conversation("negotiation")["conversation"]["turns"][0]["turn"]["tool_calls"][0]["result"]
    numbers, alternative = proof["proof"]["proof"], proof["alternatives"][0]
    assert (data["main"]["arithmetic"]["needed"], data["main"]["arithmetic"]["left"]) == (numbers["demand_teu"], numbers["open_cells"])
    assert data["main"]["offer"] is None  # the doubles' unchecked counter-offer is retired; the delivered take negotiates
    main = offered["main"]
    assert main["offer"]["sentence"] == alternative["sentence"] and main["offer"]["label"] == alternative["label"]
    assert main["offer"]["open_cells"] == alternative["static_proof"]["open_cells"]
    assert main["offer"]["requires_user_choice"] is True and main["offer"]["applied"] is False


def test_say_splits_verbatim_and_arithmetic_leaves_the_chat(data):
    say = replay.replay_conversation("negotiation")["conversation"]["turns"][0]["turn"]["say"]
    sentences = data["main"]["sentences"]
    assert all(row["text"] in say for row in sentences)
    assert not any("TEU" in row["text"] for row in sentences if row["place"] != "card")
    assert not any(row["place"] == "offer" for row in sentences)  # the retired offer's words go with it
    assert not any("QZXU" in row["text"] for row in data["second"]["sentences"])


def test_ribbon_shows_what_ran_and_only_that(data):
    steps = data["main"]["steps"]
    ran = [s for s in steps if s["kind"] == "tool"]
    assert [s["name"] for s in ran] == ["read_instruction"] and ran[0]["wall_s"] > 0
    assert any(s["kind"] == "guard" for s in data["attack"]["steps"])
    assert all("wall_s" not in s for s in steps if s["kind"] == "locked")


def test_nothing_local_or_unchecked_reaches_the_page(data):
    text = json.dumps(data)
    assert "/Users/" not in text and "stored_witness" not in text
    assert data["main"]["plan_sha256"] is None and data["attack"]["plan_sha256"] is None
    assert data["offer_plan"] is None


def test_scores_keep_their_scope(data):
    assert data["scores"] == {"total": 42, "no_unchecked": 42, "sequence": 24, "impossible": 3, "scope": "development control-flow scenarios, not sealed"}


def test_real_recording_leads_once_it_holds_an_offer(monkeypatch):
    doubles = replay.replay_conversation("negotiation")
    real = dict(doubles, turns=doubles["conversation"]["turns"], label="real label")
    real.pop("conversation")
    monkeypatch.setattr(replay, "replay_real_conversation", lambda name: real)
    view = v6.copilot()
    assert view["label"] == "real label" and view["real_tools"] is True and view["second"] is None


def test_missing_recordings_are_a_clean_gap(monkeypatch):
    def gone(name):
        raise FileNotFoundError(name)
    monkeypatch.setattr(replay, "replay_real_conversation", gone)
    monkeypatch.setattr(replay, "replay_conversation", gone)
    assert v6.feature(v6.copilot)["available"] is False


def test_attack_falls_back_to_the_doubles(monkeypatch):
    def gone(name):
        raise FileNotFoundError(name)
    monkeypatch.setattr(replay, "replay_real_conversation", gone)
    view = v6.copilot()
    assert view["attack"]["label"] == replay.replay_conversation("attack")["label"] and view["second"] is None


@pytest.mark.parametrize("stored,lands", [
    ({"checker": {"passed": True}, "computed_utc": "2026-11-01T10:00:00Z", "lifts": 40}, True),
    ({"checker": {"passed": False}, "computed_utc": "2026-11-01T10:00:00Z"}, False),
    ({"checker": {"passed": True}}, False),
    (None, False),
])
def test_lattice_plan_lands_only_with_a_checker_pass(monkeypatch, stored, lands):
    lattice = types.ModuleType("experiments.mvp.pipeline.lattice")
    lattice.lookup = lambda key, rules: stored
    monkeypatch.setitem(sys.modules, "experiments.mvp.pipeline.lattice", lattice)
    doubles = replay.replay_conversation("negotiation")
    real = dict(doubles, turns=doubles["conversation"]["turns"], label="real label")
    real.pop("conversation")
    monkeypatch.setattr(replay, "replay_real_conversation", lambda name: real)  # only a recording with an offer can land one
    plan = v6.copilot()["offer_plan"]
    assert bool(plan) is lands
    if lands:
        assert plan["label"] == "Stored plan, computed on 2026-11-01, checked when computed"


def mangled(change):
    """The doubles recording after a shape change, served as both recordings."""
    def load(name):
        doubles = copy.deepcopy(ORIGINAL(name))
        change(doubles)
        return doubles
    return load


def first_turn(data: dict) -> dict:
    return data["conversation"]["turns"][0]


def first_call(data: dict) -> dict:
    return first_turn(data)["turn"]["tool_calls"][0]


@pytest.mark.parametrize("change, gone", [
    (lambda d: first_turn(d).pop("trace"), lambda m: not [s for s in m["steps"] if s["kind"] != "locked"]),
    (lambda d: first_call(d)["result"].update(options=first_call(d)["result"].pop("alternatives")), lambda m: m["offer"] is None),
    (lambda d: first_call(d)["result"].pop("proof"), lambda m: m["arithmetic"] is None),
    (lambda d: first_turn(d)["turn"].pop("tool_calls"), lambda m: m["arithmetic"] is None and m["offer"] is None and m["sentence"]),
    (lambda d: [row["turn"].pop("say", None) for row in d["conversation"]["turns"]], lambda m: m["sentences"] == []),
    (lambda d: first_call(d)["result"]["alternatives"][0].update(constraint={"constraint": {"type": "close_bay"}}), lambda m: m["offer"] is None),
])
def test_a_changed_recording_loses_its_panel_not_the_chapter(monkeypatch, change, gone):
    monkeypatch.setattr(replay, "replay_real_conversation", lambda name: (_ for _ in ()).throw(FileNotFoundError(name)))
    monkeypatch.setattr(replay, "replay_conversation", mangled(change))
    block = v6.feature(v6.copilot)
    assert block["available"] is True and gone(block["main"])
    main = block["main"]
    assert isinstance(main["sentence"], str) and isinstance(main["sentences"], list) and isinstance(main["steps"], list)


def test_a_renamed_turn_list_falls_to_the_other_recording(monkeypatch):
    monkeypatch.setattr(replay, "replay_conversation", mangled(lambda d: d["conversation"].update(rounds=d["conversation"].pop("turns"))))
    block = v6.feature(v6.copilot)
    assert block["available"] is True and block["main"]["real_tools"] is True


def test_changed_scores_lose_the_line_only(monkeypatch, tmp_path):
    monkeypatch.setattr(v6, "COPILOT", tmp_path)
    assert v6.feature(v6.copilot)["scores"] is None
    (tmp_path / "revalidated-scenarios.json").write_text(json.dumps({"results": [{"renamed": 1}]}))
    block = v6.feature(v6.copilot)
    assert block["available"] is True and block["scores"] is None


def test_the_section_line_has_numbers_from_the_recording(data):
    assert (data["main"]["section"]["boxes"], data["main"]["section"]["cells"]) == (data["main"]["arithmetic"]["boxes"], data["main"]["arithmetic"]["physical"])
    assert data["attack"]["section"]["boxes"] and data["attack"]["section"]["size"]


@needs_node
@pytest.mark.parametrize("name", ["main", "attack", "second"])
def test_every_tau_has_a_full_frame(data, name):
    """Left: the planner's sentence is always up. Right: the rest panels hand over to the cards with no empty moment."""
    c = data[name]
    frames = node("[...Array(161).keys()].map((i) => C.stateAt(i / 10, D, false))", c)
    assert frames[0]["planner"] == 1 and frames[0]["restTop"] == 1 and frames[0]["restBody"] == 1 and frames[0]["lit"] == 0
    for s in frames:
        assert s["planner"] == 1 and s["restTop"] + s["reading"] >= 0.9, s
        assert s["restBody"] + s["card"] + s["refused"] >= 0.9, s
    assert frames[-1]["restTop"] == 0 and frames[-1]["restBody"] == 0


@needs_node
def test_the_first_frame_carries_the_whole_shape_from_the_fixture(data):
    main = data["main"]
    page = node("C.html({ who: 'Muse Spark · Meta Model API · cloud', scores: null }, D, (x) => String(x))", main)
    assert main["sentence"] in page and "18 boxes" in page and "92 cells" in page and "not called: no confirmed reading" in page
    assert all(step["name"] in page for step in main["steps"])


@needs_node
def test_a_bare_recording_still_builds(data):
    bare = {**data["main"], "arithmetic": None, "offer": None, "section": None, "sentences": [], "steps": [], "sentence": ""}
    assert isinstance(node("C.html({ who: 'x', scores: null }, D, (x) => String(x))", bare), str)
    assert node("C.stateAt(16, D, false)", bare)["restBody"] == 0


def test_spark_verbs_on_this_chapter():
    assert not re.search(r"spark (explains|writes)", JS.read_text(), re.I)


@needs_node
def test_seek_is_pure_and_ends_on_the_fixture_numbers(offered):
    main = offered["main"]
    a, b = node("[C.stateAt(9.3, D, false), C.stateAt(2, D, false), C.stateAt(9.3, D, false)]", main)[0::2]
    assert a == b
    end = node("C.stateAt(16, D, false)", main)
    assert (end["needed"], end["left"], end["third"]) == (31, 12, 44)
    assert end["typedChars"] == len(main["sentence"]) and end["picked"] is True
    assert all(row["chars"] > 0 for row in end["chat"])
    assert end["lit"] == sum(1 for s in main["steps"] if s["kind"] != "locked")


@needs_node
def test_timeline_keeps_the_order_of_the_shot(data, offered):
    first = node("C.beats(D, false)", data["main"])  # the film's first beat: the "No", inside shot 9a
    assert first["offer"] is None and first["type"][1] < first["ribbon"][1] < first["count"][1] < first["no"] < first["end"] <= first["dur"] <= 7.5
    b = node("C.beats(D, false)", offered["main"])
    assert b["dur"] == 16 and b["type"][1] < b["ribbon"][1] < b["count"][1] < b["no"] < b["offer"] < b["pick"] < b["reader"] < b["decides"] < b["end"] < 16


@needs_node
def test_no_plan_means_no_tick_and_the_planner_decides(offered):
    end = node("C.stateAt(16, D, false)", offered["main"])
    assert end["tick"] == 0 and end["confirmed"] == 0 and end["decides"] == 1
    landed = node("C.stateAt(16, D, true)", offered["main"])
    assert landed["tick"] == 1 and landed["confirmed"] == 1 and landed["decides"] == 0


@needs_node
def test_beats_without_an_offer_stop_at_the_refusal(data):
    for name in ("attack", "second"):
        end = node("C.stateAt(8, D, false)", data[name])
        assert end["offer"] == 0 and end["path"] == 0 and end["tick"] == 0 and end["picked"] is False
    assert node("C.stateAt(8, D, false)", data["attack"])["refused"] == 1
    assert node("C.stateAt(8, D, false)", data["second"])["no"] == 1


@needs_node
def test_wall_times_read_as_measured():
    assert node("[C.fmtWall(0.002522), C.fmtWall(5.0621), C.fmtWall(null)]", {}) == ["2.5 ms", "5.06 s", ""]


def test_static_copy_obeys_the_headline_contract():
    text = JS.read_text().lower()
    for banned in ("instant", "real-time", "certified", "industry", "production", "pending", "no plan is possible", "no plan exists", "stowlm planned"):
        assert banned not in text
    assert not re.search(r"\bapproved\b", text)
