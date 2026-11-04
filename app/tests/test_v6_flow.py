"""Demo v6: the threaded server, the launch warm-up, the replan chapter's gate and gauge walk, the live replan's job, and the rule that the
chapter scripts stay inert on the plain stage page. Fast and offline: no Chrome, no engine, no network."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import server as server_module
from app import v6
from app.flow import ConflictError, DemoService
from app.server import HTTPServer, make_handler

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def node(expr: str):
    code = f"global.window = globalThis; const S = require({json.dumps(str(STATIC / 'stage-story.js'))}); console.log(JSON.stringify({expr}));"
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


# ------------------------------------------------------------------ the server
class SlowDemo:
    """One slow replay, the way a full check is slow."""
    active_plan_id = "plan-1"

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def replay(self, plan_id):
        self.entered.set()
        assert self.release.wait(10)
        return {"ok": plan_id}

    def catalog(self):
        return {"plans": []}


def serve(handler):
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    handler.expected_port = httpd.server_port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def get(httpd, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}{path}", timeout=10) as response:
        return response.status, response.read()


def test_one_slow_request_does_not_stall_the_page():
    demo = SlowDemo()
    httpd = serve(make_handler({}, demo))
    slow = threading.Thread(target=get, args=(httpd, "/api/replay?plan_id=plan-1"), daemon=True)
    slow.start()
    assert demo.entered.wait(5)
    began = time.monotonic()
    assert get(httpd, "/api/health")[0] == 200 and get(httpd, "/stage")[0] == 200
    assert time.monotonic() - began < 2  # served while the replay is still inside the checker
    demo.release.set()
    slow.join(5)
    httpd.shutdown()
    httpd.server_close()


def test_server_threads_are_daemons_and_the_test_seam_keeps_its_name():
    assert issubclass(HTTPServer, server_module.ThreadingHTTPServer) and HTTPServer.daemon_threads is True
    assert server_module.HTTPServer is HTTPServer


def test_one_stage_service_however_many_threads_ask():
    handler = make_handler({}, SlowDemo(), stage_options={"live_physics": False})
    seen, gate = [], threading.Barrier(8)

    def ask():
        gate.wait()
        seen.append(handler.stage_service())
    threads = [threading.Thread(target=ask) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({id(s) for s in seen}) == 1


def test_a_replay_is_built_once_however_many_requests_ask(replay_case, tmp_path, monkeypatch):
    instance, plan = replay_case
    from app import flow
    calls, real = [], flow.build_replay

    def counted(*args, **kwargs):
        calls.append(1)
        time.sleep(.05)
        return real(*args, **kwargs)
    monkeypatch.setattr(flow, "build_replay", counted)
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    threads = [threading.Thread(target=service.replay, args=(service.active_plan_id,)) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1


def test_two_physics_requests_share_one_entry(replay_case, tmp_path):
    from app.stage_service import StageService
    instance, plan = replay_case
    demo = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    stage = StageService(demo, cache_dir=tmp_path, live_physics=True)
    stage._submit = lambda key, fn: None  # the task isn't the point; only one entry may exist for it
    replay = demo.replay(demo.active_plan_id)
    first = stage.physics_status(demo.active_plan_id, replay=replay)
    key = next(iter(stage._physics))
    entry = stage._physics[key]
    assert stage.physics_status(demo.active_plan_id, replay=replay) == first and stage._physics[key] is entry


def test_a_settled_bundle_is_served_again_only_while_nothing_changed(monkeypatch):
    from app import stage_physics
    from app.stage_service import StageService

    class Demo:
        active_plan_id, checked = "plan-1", 0

        def assert_unchanged(self, plan_id):
            self.checked += 1
    stage = StageService(Demo(), memo_bundles=True)
    built = []
    settled = {"physics": {"frames": []}, "comparison": {"reasons": {}}, "explanation": {}}
    monkeypatch.setattr(stage, "_build_bundle", lambda plan_id: built.append(plan_id) or settled)
    monkeypatch.setattr(stage_physics, "physics_drift", lambda: None)
    assert stage.bundle() is settled and stage.bundle() is settled and built == ["plan-1"] and stage.demo.checked == 1
    monkeypatch.setattr(stage_physics, "physics_drift", lambda: "drift")
    stage.bundle()
    assert built == ["plan-1", "plan-1"]  # a drifted epoch never serves the memo
    plain = StageService(Demo())
    monkeypatch.setattr(plain, "_build_bundle", lambda plan_id: built.append(plan_id) or settled)
    plain.bundle(); plain.bundle()
    assert len(built) == 4


def test_the_envelope_is_read_without_importing_the_runner_package():
    source = (ROOT / "app/v6.py").read_text()
    head = source[source.index("def _envelope("):source.index("HERO = Hero()")]
    assert "spec_from_file_location" in head and "from experiments.mvp.pipeline.runner" not in head  # it sets an env var the checker epoch reads


# ------------------------------------------------------------------ the gate and the gauge walk
GREEN = {"step": 0, "stability_passed": True, "strength_passed": True}


def frame(step, **kw):
    return {**GREEN, "step": step, **kw}


def test_gauges_that_never_leave_green_may_say_so():
    walk = v6.gauge_walk({"before": [frame(0), frame(5)], "after": [frame(0), frame(1)]}, {"before": 5, "after": 1})
    assert walk["stay_green"] and walk["ends_green"] and walk["line"] == "The gauges stay green" and walk["frames"] == 4


def test_a_green_sample_of_keyframes_never_says_the_gauges_stay_green():
    keyframes = [frame(0), frame(5), frame(9)]
    assert v6.gauge_walk({"after": keyframes}, {"after": 9})["line"] == "Every gauge ends green"  # steps between were not walked
    assert v6.gauge_walk({"after": keyframes})["line"] == "Every gauge ends green"  # no count of lifts proves it complete
    assert v6.gauge_walk({"after": [frame(k) for k in range(10)]}, {"after": 9})["line"] == "The gauges stay green"


def test_a_frame_outside_limits_mid_plan_cuts_stay_green():
    walk = v6.gauge_walk({"after": [frame(0), frame(5, stability_passed=False, criteria="in port"), frame(9)], "before": [frame(0)]})
    assert not walk["stay_green"] and walk["ends_green"] and walk["line"] == "Every gauge ends green"
    assert walk["outside"] == [{"plan": "after", "step": 5, "why": "outside limits: stability", "criteria": "in port"}]


def test_an_older_plan_before_cannot_talk_a_clean_hero_down():
    red_before = {"before": [frame(0), frame(5, stability_passed=False), frame(9)], "after": [frame(0), frame(1)]}
    walk = v6.gauge_walk(red_before, {"after": 1})
    assert walk["stay_green"] and walk["line"] == "The gauges stay green" and walk["outside"][0]["plan"] == "before"  # still listed


def test_a_last_frame_outside_limits_on_either_plan_drops_the_line():
    red_end = [frame(0), frame(9, stability_passed=False)]
    assert v6.gauge_walk({"before": red_end, "after": [frame(0)]})["line"] is None
    assert v6.gauge_walk({"before": [frame(0)], "after": red_end})["line"] is None
    assert not v6.gauge_walk({"before": red_end, "after": [frame(0)]})["stay_green"]


def test_a_plan_before_still_computing_cannot_blank_the_hero(monkeypatch):
    monkeypatch.setattr(v6, "_GAUGES", {})
    ready = {"status": "ready", "physics": {"frames": [frame(0), frame(9)]}}
    stage = SimpleNamespace(physics_status=lambda plan_id, start: ready if plan_id == "plan-2" else {"status": "running"})
    assert v6._gauges(stage, {"before": "plan-1", "after": "plan-2"})["line"] == "Every gauge ends green"  # keyframes: never "stay"
    assert v6._GAUGES == {}  # not memoised until both are in
    stage = SimpleNamespace(physics_status=lambda plan_id, start: ready if plan_id == "plan-1" else {"status": "running"})
    assert v6._gauges(stage, {"before": "plan-1", "after": "plan-2"}) is None  # the plan played isn't measured yet


def test_only_a_measured_frame_earns_the_stronger_line():
    walk = v6.gauge_walk({"after": [frame(0), frame(5, error="boom"), frame(9)]})  # nothing out of limits, but one frame unknown
    assert walk["line"] == "Every gauge ends green"


def test_an_unmeasured_frame_or_a_red_last_frame_says_nothing_green():
    assert v6.gauge_walk({"after": [frame(0), frame(1, error="boom")]})["line"] is None
    assert v6.gauge_walk({"after": [frame(0), frame(1, strength_passed=False)]})["line"] is None
    assert v6.gauge_walk({"after": []})["line"] is None and v6.gauge_walk({})["line"] is None
    assert v6.frame_verdict({"step": 0}) == "not measured"  # a missing verdict is never green
    assert v6.gauge_walk({"after": [frame(9), frame(1, strength_passed=False)]})["ends_green"]  # order is by step, not by list


def replay(slots, weighted, passed=True):
    return {"checker": {"passed": passed}, "frames": [{"counts": {"weighted_nonproductive": weighted},
            "state": {"ship": {"placements": [{"container_id": k, "slot": v} for k, v in slots.items()]}}}]}


class FakeDemo:
    def __init__(self, before, after, plans=None):
        self.replays = {"plan-1": before, "plan-2": after}
        self.plans = plans or [{"id": "plan-1", "label": "v6:before"}, {"id": "plan-2", "label": "v6:after"}]
        self._records = {key: SimpleNamespace(inputs=SimpleNamespace(instance=None, plan=None)) for key in self.replays}

    def catalog(self):
        return {"plans": self.plans}

    def replay(self, plan_id):
        return self.replays[plan_id]

    gate_replay = replay


REGATED = {"summary": {"passed": ["k1", "k2"], "rejected": [], "errors": {}}, "gauges": {"line": "The gauges stay green"},
           "entries": {"before": {"passed": True, "plan_sha256": "b", "checked_utc": "now", "engine_version": "0.13.0"},
                       "after": {"passed": True, "plan_sha256": "a", "checked_utc": "now", "engine_version": "0.13.0", "computed_utc": "2026-11-02T00:00:00+00:00"}}}


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(v6, "_envelope", lambda service, plan_id: {"inside": True, "boxes": 2, "teu": 800, "fill_pct": 70, "region": "half-ship section"})
    monkeypatch.setattr(v6, "hero_reading", lambda: {"answer": {}, "sentence": "x", "evidence": None, "seconds": None, "recorded": False})
    monkeypatch.setattr(v6, "hero", lambda: {"before_sha256": "b", "after_sha256": "a"})
    monkeypatch.setattr(v6, "regate_hero", lambda: REGATED)
    monkeypatch.setattr(v6, "_registered_sha", lambda service, plan_id: {"plan-1": "b", "plan-2": "a"}[plan_id])
    import experiments.mvp.reader.pipeline as pipeline
    import experiments.mvp.reader.schema as schema
    monkeypatch.setattr(schema, "context_from_instance", lambda instance: {})
    monkeypatch.setattr(schema, "validate_answer", lambda text, context: {})
    monkeypatch.setattr(pipeline, "intrinsic_violations", lambda *args: [])
    return pipeline


def test_the_gate_counts_lifts_moved_boxes_and_the_cost_of_obeying(gated):
    gate = v6.Hero._check(FakeDemo(replay({"A": "0010102", "B": "0030102"}, 130.0), replay({"A": "0010102", "B": "0050102"}, 134.0)))
    assert gate["status"] == "shown" and gate["boxes_moved"] == 1 and gate["extra_lifts"] == 4.0
    assert gate["before"]["lifts"] == 130.0 and gate["after"]["lifts"] == 134.0 and gate["honoured"]
    assert gate["recheck"] == "rechecked now" and gate["computed_utc"] == REGATED["entries"]["after"]["computed_utc"]


def test_rechecked_now_needs_the_launch_regate_to_have_run_on_that_plan(gated, monkeypatch):
    no_run = {**REGATED, "entries": {**REGATED["entries"], "after": {**REGATED["entries"]["after"], "checked_utc": None}}}
    monkeypatch.setattr(v6, "regate_hero", lambda: no_run)
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))
    assert gate["recheck"] is None and gate["after"]["recheck"] is None and gate["before"]["recheck"] == "rechecked now"


@pytest.mark.parametrize("change, status", [({"before": {"passed": False}}, "not_shown"), ({"before": {"plan_sha256": "other"}}, "not_shown"),
                                            ({"after": {"passed": False}}, "before_only"), ({"after": {"plan_sha256": "other"}}, "before_only")])
def test_a_plan_is_shown_only_if_the_regate_passed_exactly_that_plan(gated, monkeypatch, change, status):
    entries = {name: {**row, **change.get(name, {})} for name, row in REGATED["entries"].items()}
    monkeypatch.setattr(v6, "regate_hero", lambda: {**REGATED, "entries": entries})
    assert v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))["status"] == status


def test_a_catalogue_that_moved_shows_nothing_and_says_the_digests(gated, monkeypatch):
    monkeypatch.setattr(v6, "regate_hero", lambda: {"summary": {"errors": {"catalogue": "lattice catalogue does not match"}}, "entries": {}})
    monkeypatch.setattr(v6, "hero", lambda: {"catalogue_sha256": "old"})
    monkeypatch.setattr(v6, "scoped_catalogue_sha256", lambda config=None: "new")
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))
    assert gate["status"] == "not_shown" and "pinned old, current new" in gate["why"][0] and "--repin" in gate["why"][0] and gate["catalogue_sha256"] == "new"


def test_a_regate_that_never_finished_shows_nothing(gated, monkeypatch):
    monkeypatch.setattr(v6, "regate_hero", lambda: {"failed": "the launch regate did not finish"})
    assert v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))["status"] == "not_shown"


def test_a_plan_that_fails_or_breaks_the_rule_holds_the_stored_plan(gated, monkeypatch):
    demo = FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 120.0, passed=False))
    assert v6.Hero._check(demo)["status"] == "before_only"
    monkeypatch.setattr(gated, "intrinsic_violations", lambda *args: ["broken"])
    assert v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 120.0)))["status"] == "before_only"
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({}, 0), plans=[{"id": "plan-1", "label": "v6:before"}]))
    assert gate["status"] == "before_only" and gate["why"] == ["no plan under the rule yet"]


def test_only_boxes_where_the_rule_forbids_them_are_forced_out():
    slots = {"A": "0270102", "B": "0270182", "C": "0310182"}
    assert v6.forced_out(slots, lambda box, slot: "closed" if slot == "0270182" else None) == ["B"]
    assert v6.forced_out({}, lambda box, slot: True) == []


def test_the_gate_credits_the_rule_only_with_what_it_forbids(gated, monkeypatch):
    monkeypatch.setattr(v6, "_forced_boxes", lambda instance, answer, before: ["B"])
    gate = v6.Hero._check(FakeDemo(replay({"A": "1", "B": "2"}, 130.0), replay({"A": "3", "B": "4"}, 134.0)))
    assert gate["boxes_moved"] == 2 and gate["forced_boxes"] == ["B"]
    monkeypatch.setattr(v6, "_forced_boxes", lambda instance, answer, before: None)
    assert v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "3"}, 134.0)))["forced_boxes"] is None


def test_a_rule_that_cant_be_asked_is_credited_with_nothing(monkeypatch):
    import experiments.mvp.reader.occupancy as occupancy

    def broken(instance, answer):
        raise TypeError("new shape")
    monkeypatch.setattr(occupancy, "apply_reader_constraint", broken)
    assert v6._forced_boxes(None, {}, replay({"A": "1"}, 1.0)) is None


def test_a_rule_clip_of_a_new_shape_holds_the_stored_plan(gated, monkeypatch):
    def reshaped():
        raise KeyError("answer")
    monkeypatch.setattr(v6, "hero_reading", reshaped)
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "3"}, 134.0)))
    assert gate["status"] == "before_only" and "can't be read" in gate["why"][0]


@pytest.mark.parametrize("bad", [[], "x", None, {"rule": 1}])
def test_replan_reads_each_input_through_the_adapter(bad, monkeypatch, tmp_path):
    def reshaped(*args):
        return bad

    def missing(*args):
        raise FileNotFoundError("gone")
    monkeypatch.setattr(v6.HERO, "gate", lambda service: {"status": "shown", "before": {"plan_id": "p"}})
    monkeypatch.setattr(v6, "PACKAGE", tmp_path / "nowhere/complete")
    monkeypatch.setattr(v6, "PROFILE", tmp_path / "absent.json")
    monkeypatch.setattr(v6, "hero", reshaped)
    monkeypatch.setattr(v6, "_clip", missing if bad is None else reshaped)
    monkeypatch.setattr(v6, "_gauges", missing)
    assert v6.replan(None)["gate"]["status"] == "before_only"  # an unreadable reading can't be confirmed: nothing under the rule
    monkeypatch.setattr(v6, "reading_gate", lambda *args: {"shown": True})  # confirmed: the panels below are the point
    view = v6.replan(None)
    assert view["rule"] is None and view["gauges"] is None and view["rule_counts"] is None and view["first_plan"] is None
    assert view["gate"]["status"] == "shown"  # the panels went, the chapter didn't


def test_rule_counts_come_from_the_receipt_data_and_never_from_a_literal(monkeypatch, tmp_path):
    package = tmp_path / "fixtures/complete"
    package.mkdir(parents=True)
    monkeypatch.setattr(v6, "PACKAGE", package)
    assert v6.rule_counts() is None
    (package.parent / "verification-summary.json").write_text(json.dumps({"rule_counts": {"checker_active": 40, "inactive": 3}}))
    assert v6.rule_counts() == {"active": 40, "off": 3}
    (package / "rule-coverage.json").write_text(json.dumps({"active_rule_count": 41, "inactive_rule_count": 2}))
    assert v6.rule_counts() == {"active": 41, "off": 2}
    (package / "rule-coverage.json").write_text(json.dumps({"active_rule_count": "54", "inactive_rule_count": True}))
    assert v6.rule_counts() == {"active": 40, "off": 3}


def test_the_real_receipt_says_54_active_and_8_off():
    assert v6.rule_counts() == {"active": 54, "off": 8}


def test_the_first_plan_figure_shows_only_when_the_runner_measured_it(tmp_path):
    path = tmp_path / "selection.json"
    assert v6.first_plan_figure(path) is None
    good = {"measured": True, "profiles": {"live": {"measured_first_legal_s": {"n": 12, "median": 41.5}}}}
    path.write_text(json.dumps(good))
    assert v6.first_plan_figure(path) == {"median_s": 41.5, "runs": 12.0}
    path.write_text(json.dumps({**good, "measured": False}))
    assert v6.first_plan_figure(path) is None
    path.write_text(json.dumps({"measured": True, "profiles": ["reshaped"]}))
    assert v6.first_plan_figure(path) is None


def test_a_stored_plan_shows_no_time_from_the_overloaded_run():
    assert "incumbents" not in v6.replan(None, None).keys()


def test_a_failed_stored_plan_is_not_shown_at_all(gated):
    assert v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0, passed=False), replay({}, 0)))["status"] == "not_shown"


def test_incumbents_keep_their_real_seconds(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join([
        json.dumps({"kind": "incumbent", "elapsed_s": 88.5, "data": {"lifts": 139.0, "checked": True, "lane": "assignment"}}),
        json.dumps({"kind": "incumbent", "elapsed_s": 90.0, "data": {"lifts": 130.0, "checked": False}}),
        "not json"]))
    assert v6.incumbents(path) == [{"t_s": 88.5, "lifts": 139.0, "lane": "assignment"}]
    assert v6.incumbents(tmp_path / "absent.jsonl") == []


def test_warm_says_what_it_warmed_and_names_a_gap(monkeypatch):
    bundles = []
    stage = SimpleNamespace(physics_status=lambda plan_id, start: {"status": "ready" if plan_id == "plan-1" else "running"}, bundle=bundles.append)

    class Handler:
        demo_service = object()
        stage_service = staticmethod(lambda: stage)
    monkeypatch.setattr(v6, "plan_ids", lambda service: {"before": "plan-1", "after": "plan-2", "sandbox": None})
    monkeypatch.setattr(v6.HERO, "wait", lambda service: {"status": "shown"})
    monkeypatch.setattr("app.v6_package.ensure_verified", lambda: "package passed")
    line = v6.warm(Handler)
    assert line.startswith("StowLM v6 warm in ") and "gate shown" in line and "before plan physics ready" in line and "after plan physics MISSING" in line
    assert bundles == ["plan-1", "plan-2"]


# ------------------------------------------------------------------ the hero comes from config
def slot_config(tmp_path, **changes):
    real = json.loads(v6.HERO_FILE.read_text())
    path = tmp_path / "hero.json"
    path.write_text(json.dumps({**real, **changes}))
    return path


def test_the_hero_is_the_slot_the_config_names_and_every_fact_comes_from_it():
    config, source = v6.hero(), json.loads((ROOT / json.loads(v6.HERO_FILE.read_text())["hero_source"]).read_text())
    row, numbers = source[config["slot"]], source[config["slot"]]["envelope"]["numbers"]
    assert config["instance"] == row["path"] and config["instance_sha256"] == row["instance_sha256"]
    assert str(numbers["boxes_to_load"]) in config["ship"] and f"{round(100 * numbers['physical_cell_fill'])} %" in config["ship"]
    assert Path(config["before_plan"]).is_file() and json.loads(Path(config["before_plan"]).read_text())["instance_id"] == row["instance_id"]


def test_a_file_that_is_not_the_named_one_stops_the_launch(tmp_path, monkeypatch):
    source = json.loads((ROOT / "experiments/mvp/pipeline/lattice/hero.json").read_text())
    source["hero"]["file_sha256"] = "0" * 64
    bad = tmp_path / "source.json"
    bad.write_text(json.dumps(source))
    monkeypatch.setattr(v6, "HERO_FILE", slot_config(tmp_path, hero_source=str(bad)))
    with pytest.raises(ValueError, match="file_sha256"):
        v6.hero()


def test_a_slot_with_no_instruction_fixture_holds_its_gap_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(v6, "HERO_FILE", slot_config(tmp_path, hero_slot="backup", instructions={"backup": "experiments/mvp/pipeline/lattice/fixtures/absent-instruction.json"}, catalogue_sha256=None))
    config = v6.hero()
    assert config["instruction"] is None and config["after_plan"] is None and config["keys"].keys() == {"before"}
    assert v6.feature(v6.reading)["available"] is False
    assert v6.optional(v6._replan_rule) is None and "reader" not in v6.stand_ins() and "radio" not in v6.stand_ins()


def test_the_pin_does_not_move_when_the_campaign_adds_another_entry(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    (store / "catalogue.json").write_text(json.dumps({"a" * 64: "1", "b" * 64: "2"}))
    monkeypatch.setattr(v6, "LATTICE_STORE", store)
    config = {"keys": {"before": "a" * 64, "after": "b" * 64}}
    pinned = v6.scoped_catalogue_sha256(config)
    (store / "catalogue.json").write_text(json.dumps({"a" * 64: "1", "b" * 64: "2", "c" * 64: "3"}))
    assert v6.scoped_catalogue_sha256(config) == pinned
    (store / "catalogue.json").write_text(json.dumps({"a" * 64: "1", "b" * 64: "changed"}))
    assert v6.scoped_catalogue_sha256(config) != pinned


def test_stand_ins_are_per_piece_and_only_the_ones_still_standing_in(monkeypatch):
    monkeypatch.setattr(v6, "_worded_reading", lambda config: (_ for _ in ()).throw(LookupError("no reading")))  # the hero wording read: undo it for the stand-in state
    monkeypatch.setattr(v6, "_radio_is_the_rule", lambda: False)
    block = v6.stand_ins()
    assert "ship" not in block and block["reader"] and block["radio"]  # the hero ship is real; reader and radio stand in here
    monkeypatch.setattr(v6, "_radio_is_the_rule", lambda: True)
    monkeypatch.setattr(v6, "_who_read", lambda reading: {"kind": "stowlm_reader"})
    assert v6.stand_ins() == {}


def test_two_engines_only_when_the_plans_own_evidence_says_two():
    assert v6._engine_words({"before": {"engine": "0.13.0"}, "after": {"engine": "0.13.0"}}) == ("Frontier Engine 0.13.0 · search software", False)
    assert v6._engine_words({"before": {"engine": "0.12.1"}, "after": {"engine": "0.13.0"}}) == ("Frontier Engine 0.13.0 · search software", True)
    assert v6._engine_words({}) == (None, False)


def test_a_radio_clip_with_another_rule_says_so_and_drops_the_reading_chapters_refusal(monkeypatch):
    monkeypatch.setattr(v6, "_radio_is_the_rule", lambda: False)
    view = v6.voice()
    assert view["role"] == "illustrative" and "separate clip" in view["note"].lower()
    assert v6.reading()["refused_first"]["refusal"] is None


def test_the_reading_is_never_credited_to_a_model_with_no_recording(monkeypatch):
    monkeypatch.setattr(v6, "_worded_reading", lambda config: (_ for _ in ()).throw(LookupError("no reading")))  # the hero wording read: undo it for the stand-in state
    view = v6.reading()
    assert view["mode"] == "stored" and view["reader"]["kind"] == "none" and view["label"] is None and view["seconds"] is None


def test_the_empty_standing_line_is_not_a_reading(monkeypatch):
    from app.brief import _FixedAnswer
    answer = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}
    context = {"id": "ship", "bays": [1], "max_tiers": {"hold": 3, "deck": 5}, "max_tiers_by_bay": {}, "has_accommodation": True, "hatch_covers": [], "ports": []}
    reader = v6._ConfirmedReader(_FixedAnswer("Close bay 1.", answer, {"parser": "SparkParser", "adapter_sha256": None}, "ship"))
    assert type(reader.parse("Close bay 1.", context)[0]).__name__ == "CloseBay" and type(reader.parse("", context)[0]).__name__ == "Refusal"
    with pytest.raises(ValueError):
        reader.parse("Close bay 2.", context)


# ------------------------------------------------------------------ the live replan
def wait_for(job, live):
    for _ in range(200):
        view = live.view(job["id"])
        if view["status"] != "running":
            return view
        time.sleep(.02)
    raise AssertionError("still running")


def fake_handler(**options):
    return SimpleNamespace(brief_options=options, demo_service=None)


def confirmed(monkeypatch) -> str:
    monkeypatch.setattr(v6, "reading_binding", lambda: {"sentence_sha256": "s", "rule_sha256": "r", "instance_sha256": "i"})
    return v6.CONFIRMATIONS.confirm(v6.reading_binding())["token"]


def test_live_replan_needs_its_flag(tmp_path):
    with pytest.raises(PermissionError):
        v6.LiveReplan().start(fake_handler())


def test_live_replan_runs_in_its_own_process_and_reports_real_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(v6, "LIVE_DIR", tmp_path)
    worker = ("import json,sys,pathlib; o=pathlib.Path(sys.argv[1]);"
              "(o/'events.jsonl').write_text(json.dumps({'kind':'incumbent','elapsed_s':1.5,'data':{'lifts':140.0,'checked':True}})+'\\n');"
              "(o/'outcome.json').write_text(json.dumps({'status':'no_checked_plan','passed':False}))")
    live = v6.LiveReplan(command=lambda out: [sys.executable, "-c", worker, str(out)])
    job = live.start(fake_handler(live_replan=True), confirmed(monkeypatch))
    assert job["status"] == "running" and "percent" not in job and "progress" not in job
    view = wait_for(job, live)
    assert view["status"] == "done" and view["result"]["passed"] is False and view["result"].get("plan_id") is None
    assert view["incumbents"][0]["lifts"] == 140.0 and view["elapsed_s"] >= 0


def test_one_live_replan_at_a_time_and_a_dead_worker_shows_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(v6, "LIVE_DIR", tmp_path)
    live = v6.LiveReplan(command=lambda out: [sys.executable, "-c", "import time; time.sleep(.3)"])
    job = live.start(fake_handler(live_replan=True), confirmed(monkeypatch))
    other = confirmed(monkeypatch)
    with pytest.raises(ConflictError):
        live.start(fake_handler(live_replan=True), other)
    view = wait_for(job, live)
    assert view["status"] == "failed" and view["result"] is None and "FileNotFoundError" in view["error"]


def test_post_replan_is_refused_without_the_flag():
    httpd = serve(make_handler({}, SlowDemo()))
    httpd.RequestHandlerClass.brief_options = {}
    request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/v6/replan", data=b"{}", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)
    assert caught.value.code == 403
    httpd.shutdown()
    httpd.server_close()


# ------------------------------------------------------------------ the morph (pure, under node)
SLOTS = [{"id": "0010102", "bay": 1, "row": 1, "tier": 2}, {"id": "0030102", "bay": 3, "row": 1, "tier": 2}, {"id": "0050102", "bay": 5, "row": 1, "tier": 2}]


def plan(*moves):
    return {"geometry": {"slots": SLOTS}, "initial": {"ship": []}, "steps": [{"box": b, "src": "Y", "dst": d} for b, d in moves]}


@needs_node
def test_a_box_lands_only_after_the_box_in_its_way_has_left():
    before, after = plan(("A", "0010102"), ("B", "0030102")), plan(("A", "0030102"), ("B", "0050102"))
    got = node(f"S.morphOrder({json.dumps(before)}, {json.dumps(after)})")
    assert got["ok"] and [row["box"] for row in got["order"]] == ["B", "A"]
    swap = node(f"S.morphOrder({json.dumps(before)}, {json.dumps(plan(('A', '0030102'), ('B', '0010102')))})")
    assert sorted(row["box"] for row in swap["order"]) == ["A", "B"]  # a swap can't wait on itself
    assert not node(f"S.morphOrder({json.dumps(plan(('A', '0010102')))}, {json.dumps(plan(('Z', '0010102')))})")["ok"]  # a box the other plan doesn't have
    assert not node("S.morphOrder(null, null)")["ok"]


@needs_node
def test_the_morph_is_a_pure_function_of_tau_and_ends_on_the_plan_after():
    order = [{"box": "B", "from": "2", "to": "3"}, {"box": "A", "from": "1", "to": "2"}]
    at = lambda tau: node(f"S.morphAt({json.dumps(order)}, 1, 5, 2, {tau})")  # noqa: E731
    assert at(0.5) == {"started": 0, "landed": 0, "flyers": []}
    assert at(2)["started"] == 1 and at(2)["flyers"][0]["p"] == .5
    assert at(9) == {"started": 2, "landed": 2, "flyers": []}
    assert at(3.4) == at(3.4)
    ship = node(f"[...S.morphShip([['1','A'],['2','B'],['9','C']], {json.dumps(order)}, 2, 2).entries()].sort()")
    assert ship == [["2", "A"], ["3", "B"], ["9", "C"]]
    middle = node(f"[...S.morphShip([['1','A'],['2','B']], {json.dumps(order)}, 1, 0).entries()]")
    assert middle == [["1", "A"]]  # B is in the air
    assert node("[0, 1, 1.7, 9].map((t) => S.familiesShown(3, 1, .5, t))") == [0, 1, 2, 3]


@needs_node
def test_the_rule_is_credited_only_with_the_boxes_it_forced():
    order = [{"box": "A"}, {"box": "B"}, {"box": "C"}]
    split = node(f"S.forcedSplit({json.dumps(order)}, ['B', 'Z'])")
    assert split == {"known": True, "forced": ["B"], "also": ["A", "C"]}  # Z didn't move, so it isn't counted
    assert node(f"S.forcedSplit({json.dumps(order)}, [])")["also"] == ["A", "B", "C"]
    unknown = node(f"S.forcedSplit({json.dumps(order)}, null)")
    assert not unknown["known"] and unknown["forced"] == []
    assert node("S.forcedSplit('reshaped', {})") == {"known": False, "forced": [], "also": []}
    assert node(f"S.landedSplit({json.dumps(order)}, S.forcedSplit({json.dumps(order)}, ['B']), 2)") == {"forced": 1, "also": 1}


@needs_node
def test_the_rules_boxes_fly_first_unless_something_sits_in_their_way():
    before, after = plan(("A", "0010102"), ("B", "0030102"), ("C", "0050102")), plan(("A", "0030102"), ("B", "0050102"), ("C", "0010102"))
    free = plan(("A", "0010102"), ("B", "0030102"))
    assert [r["box"] for r in node(f"S.morphOrder({json.dumps(free)}, {json.dumps(plan(('A', '0050102'), ('B', '0070102')))}, ['B']).order")] == ["B", "A"]
    assert sorted(r["box"] for r in node(f"S.morphOrder({json.dumps(before)}, {json.dumps(after)}, ['C']).order")) == ["A", "B", "C"]
    blocked = node(f"S.morphOrder({json.dumps(plan(('A', '0010102'), ('B', '0030102')))}, {json.dumps(plan(('A', '0030102'), ('B', '0050102')))}, ['A']).order")
    assert [r["box"] for r in blocked] == ["B", "A"]  # A can't land until B has left its cell


@needs_node
def test_a_lift_gap_never_reads_as_the_rules_cost():
    line = node("S.liftGapLine(130, 148, true)")
    assert line == "18 more extra lifts than the plan before. Different engines and search runs: not a measured cost of the rule"
    assert node("S.liftGapLine(130, 120, false)").startswith("10 fewer extra lifts") and "Different search runs" in node("S.liftGapLine(130, 120, false)")
    assert node("S.liftGapLine(130, 130, false)").startswith("Same extra lifts")
    assert node("S.liftGapLine(null, 130, true)") == ""
    assert "Obeying cost" not in (STATIC / "stage-v6replan.js").read_text()


# ------------------------------------------------------------------ inert on the plain page
def test_the_flow_only_boots_on_its_own_flag_or_from_the_director():
    shell = (STATIC / "stage-v6.js").read_text()
    assert "const ON = P.get('v6') === '1';" in shell and shell.count("boot(false)") == 1 and "if (ON) {" in shell
    director = (STATIC / "stage-director.js").read_text()
    assert director.count("FLOW.boot(true)") == 1 and "if (V6 && window.StageV6)" in director


@pytest.mark.parametrize("path", sorted(STATIC.glob("stage-v6*.js")), ids=lambda p: p.name)
def test_a_chapter_script_does_nothing_but_register(path):
    if path.name == "stage-v6.js":
        return
    source = path.read_text()
    probe = """
      const calls = []; const noop = () => { throw new Error('touched the page while loading'); };
      global.window = globalThis;
      global.document = new Proxy({}, { get: () => noop });
      global.location = { search: '' }; global.addEventListener = noop; global.requestAnimationFrame = noop;
      global.fetch = noop; global.setTimeout = noop;
      window.StageV6 = { chapter: (d) => calls.push(d.id), esc: String, api: noop };
      window.StageStory = {};
      require(%s); // holds the chapters' shared helpers
      require(%s);
      console.log(JSON.stringify(calls));
    """ % (json.dumps(str(STATIC / "stage-v6intake.js")), json.dumps(str(path)))
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    out = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-400:]
    assert json.loads(out.stdout.strip().splitlines()[-1]), path.name


def test_stage_autoplay_waits_for_the_chapter():
    assert "const auto = VIDEO || params.get('v6') === '1' ? false" in (STATIC / "stage.js").read_text()


def test_v6_styles_stay_scoped_to_the_flow():
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "stage-v6.css").read_text(), flags=re.S)
    for rule in re.findall(r"(?:^|\})\s*([^{}]+)\{", css):
        for selector in rule.split(","):
            head = selector.strip()
            assert head.startswith(("#v6-", "body.v6", ".v6")), head
