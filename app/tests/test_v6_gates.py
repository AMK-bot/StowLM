"""Demo v6 gates: nothing rejected reaches the page by any route, and a warm launch reuses an exact regate. In-memory, no checker rerun."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
from http.client import HTTPResponse
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import v6, v6_package
from app.flow import DemoService
from app.inputs import LoadedInputs
from app.server import make_handler
from app.tests.test_v6_flow import REGATED, FakeDemo, gated, replay, slot_config  # noqa: F401 - gated is a fixture

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
SHOWN = {"status": "shown", "before": {"passed": True}, "after": {"passed": True}}
BEFORE_ONLY = {"status": "before_only", "why": ["the plan under the rule failed its gate"], "before": {"passed": True}, "after": {"passed": False, "lifts": 9.0}}
CHECKING = {"status": "checking"}
FAILED = {"status": "not_shown", "why": ["the stored plan did not pass the launch regate"], "before": {"passed": False, "lifts": 9.0}}


class Socket:
    def __init__(self, request=b""):
        self.incoming, self.outgoing = io.BytesIO(request), bytearray()

    def makefile(self, mode, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.extend(data)


def ask(handler, method, path, body=None):
    head = f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n"
    payload = b"" if body is None else json.dumps(body).encode()
    if body is not None:
        head += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
    transport = Socket((head + "\r\n").encode() + payload)
    handler(transport, ("127.0.0.1", 12345), object())
    response = HTTPResponse(Socket(bytes(transport.outgoing)))
    response.begin()
    return response


def answer(response):
    return json.loads(response.read())


@pytest.fixture
def demo(replay_case, monkeypatch):
    instance, plan = replay_case
    others = [LoadedInputs(instance, plan, {"label": f"v6:{name}"}) for name in ("after", "sandbox")]
    service = DemoService(instance, plan, {"label": "v6:before"}, others, discover=False)
    monkeypatch.setattr(v6.HERO, "gate", lambda _service: CHECKING)
    return service


@pytest.fixture
def v6_server(demo):
    v6.guard_plans(demo)
    handler = make_handler({"ok": True}, demo, stage_options={"live_physics": False, "memo_bundles": True})
    handler.brief_options = {"v6": True}
    return handler


def set_gate(monkeypatch, value):
    monkeypatch.setattr(v6.HERO, "gate", lambda _service: value)


# ---------------------------------------------------------------- 1: one boundary, every route
ROUTES = ("/api/demo", "/api/replay?plan_id={before}", "/api/replay?plan_id={after}", "/api/comparison?left={before}&right={after}",
          "/api/stage", "/api/stage?plan_id={after}", "/api/stage?plan_id={sandbox}", "/api/stage/physics?plan_id={before}", "/api/stage/explain?plan_id={before}",
          "/api/stage/explain?plan_id={before}&box=X", "/api/instruct/recorded?plan_id={before}")


@pytest.mark.parametrize("gate", [CHECKING, FAILED], ids=["checking", "failed"])
@pytest.mark.parametrize("route", ROUTES)
def test_a_stored_plan_is_withheld_on_every_route_until_its_gates_pass(v6_server, demo, monkeypatch, gate, route):
    set_gate(monkeypatch, gate)
    response = ask(v6_server, "GET", route.format(**v6.plan_ids(demo)))
    body = response.read()
    assert response.status == 403 and b'"moves"' not in body and b'"frames"' not in body
    assert "withheld" in json.loads(body)["error"].lower()


def test_a_memoised_bundle_is_not_a_way_around_the_gate(v6_server, demo, monkeypatch):
    set_gate(monkeypatch, SHOWN)
    ids = v6.plan_ids(demo)
    v6_server.stage_service()._bundles.put(ids["before"], {"plan": "built while it passed"})
    assert answer(ask(v6_server, "GET", f"/api/stage?plan_id={ids['before']}")) == {"plan": "built while it passed"}
    set_gate(monkeypatch, FAILED)
    assert ask(v6_server, "GET", f"/api/stage?plan_id={ids['before']}").status == 403


def test_the_page_still_boots_while_the_plans_are_withheld(v6_server, demo, monkeypatch):
    set_gate(monkeypatch, CHECKING)
    ids = v6.plan_ids(demo)
    assert ask(v6_server, "GET", "/api/health").status == 200
    catalog = answer(ask(v6_server, "GET", "/api/catalog"))
    rows = {row["id"]: row for row in catalog["plans"]}
    assert "withheld" in rows[ids["before"]]["withheld"].lower() and "planner" not in rows[ids["before"]]
    assert v6.plan_ids(demo) == ids  # the gate's own lookup survives the marking
    state = ask(v6_server, "GET", "/api/v6/state")
    assert state.status == 200 and answer(state)["features"]["intake"]["available"] is True
    source = (STATIC / "stage.js").read_text()
    assert "e.status === 403 && params.get('v6') === '1'" in source and "setTimeout(boot, 3000)" in source and "!p.withheld" in source


def test_cleared_plans_pass_and_a_failed_plan_under_the_rule_stays_out(v6_server, demo, monkeypatch):
    ids = v6.plan_ids(demo)
    set_gate(monkeypatch, SHOWN)
    assert ask(v6_server, "GET", f"/api/replay?plan_id={ids['after']}").status == 200
    set_gate(monkeypatch, BEFORE_ONLY)
    assert ask(v6_server, "GET", f"/api/replay?plan_id={ids['before']}").status == 200
    assert ask(v6_server, "GET", f"/api/replay?plan_id={ids['after']}").status == 403
    assert ask(v6_server, "GET", f"/api/comparison?left={ids['before']}&right={ids['after']}").status == 403
    assert "withheld" in answer(ask(v6_server, "GET", "/api/catalog"))["plans"][1]["withheld"].lower()


@pytest.mark.parametrize("gate", [CHECKING, FAILED], ids=["checking", "failed"])
def test_the_break_it_chapter_is_derived_from_the_plan_before_and_goes_with_it(v6_server, monkeypatch, gate):
    set_gate(monkeypatch, gate)
    block = answer(ask(v6_server, "GET", "/api/v6/sandbox"))
    assert block["available"] is False and "withheld" in block["reason"].lower() and "cases" not in block
    assert ask(v6_server, "POST", "/api/v6/try", {"box": "A", "slot": "1"}).status == 403
    assert answer(ask(v6_server, "GET", "/api/v6/explain?box=A"))["available"] is False


def test_the_replan_chapter_shows_nothing_of_a_rejected_plan(demo, monkeypatch):
    v6.guard_plans(demo)
    set_gate(monkeypatch, FAILED)
    view = v6.replan(demo)
    monkeypatch.setattr(v6, "_gauges", lambda stage, ids: {"walk": "a memo from before the verdict"})
    assert view["gate"] == {"status": "not_shown", "why": FAILED["why"]} and view["gauges"] is None
    set_gate(monkeypatch, BEFORE_ONLY)
    assert "after" not in v6.replan(demo)["gate"]


def test_the_gate_itself_still_reads_the_plans_it_is_deciding(v6_server, demo, monkeypatch):
    set_gate(monkeypatch, CHECKING)
    assert demo.gate_replay(v6.plan_ids(demo)["before"])["plan"]["id"]


def test_a_launch_without_v6_has_no_gate(replay_case):
    instance, plan = replay_case
    service = DemoService(instance, plan, {"label": "v6:before"}, discover=False)
    handler = make_handler({"ok": True}, service, stage_options={"live_physics": False})
    assert ask(handler, "GET", "/api/demo").status == 200
    assert "withheld" not in answer(ask(handler, "GET", "/api/catalog"))["plans"][0]


@pytest.mark.parametrize("gate, name, withheld", [(CHECKING, "before", True), (FAILED, "before", True), ({"status": "strange"}, "before", True), (SHOWN, "before", False), (SHOWN, "after", False),
                                                  (BEFORE_ONLY, "before", False), (BEFORE_ONLY, "after", True), ({"status": "shown", "before": {"passed": False}}, "before", True)])
def test_each_plan_needs_its_own_pass(monkeypatch, gate, name, withheld):
    set_gate(monkeypatch, gate)
    reason = v6.HERO.withheld(None, name)
    assert bool(reason) is withheld and (not reason or len(reason) < 200)


def test_the_gate_binds_the_plan_that_was_registered_not_only_the_one_stored(gated, monkeypatch):
    monkeypatch.setattr(v6, "_registered_sha", lambda service, plan_id: "swapped")
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))
    assert gate["status"] == "not_shown"


def test_warm_never_bundles_a_withheld_plan_and_says_so(monkeypatch):
    bundles = []
    stage = SimpleNamespace(physics_status=lambda plan_id, start: pytest.fail("a withheld plan was read"), bundle=bundles.append)

    class Handler:
        demo_service = SimpleNamespace(plan_gate=lambda plan_id: "Stored plan withheld: it failed.")
        stage_service = staticmethod(lambda: stage)
    monkeypatch.setattr(v6, "plan_ids", lambda service: {"before": "plan-1", "after": None, "sandbox": "plan-3"})
    monkeypatch.setattr(v6.HERO, "wait", lambda service: FAILED)
    monkeypatch.setattr(v6_package, "ensure_verified", lambda: "package verified")
    line = v6.warm(Handler)
    assert bundles == [] and "before plan withheld" in line and "package verified" in line


# ---------------------------------------------------------------- 2: /api/solve
def test_v6_refuses_the_legacy_solve_and_other_launches_keep_it(replay_case, monkeypatch):
    instance, plan = replay_case
    service = DemoService(instance, plan, discover=False)
    calls = []
    monkeypatch.setattr(service, "start_solve", lambda **kw: calls.append(kw) or {"id": "job"})
    handler = make_handler({"ok": True}, service)
    handler.brief_options = {"v6": True, "live_replan": False}
    response = ask(handler, "POST", "/api/solve", {"instance_id": instance.id})
    assert response.status == 403 and "disabled" in answer(response)["error"] and calls == []
    handler.brief_options = {"v6": False}
    assert ask(handler, "POST", "/api/solve", {"instance_id": instance.id}).status == 202 and len(calls) == 1
    plain = make_handler({"ok": True}, service)  # a launch that never set brief options
    assert ask(plain, "POST", "/api/solve", {"instance_id": instance.id}).status == 202


# ---------------------------------------------------------------- 3 and part of 1: the package's own gate
CLEAN_RUN = {"passed": True, "wall_s": 20.0, "checks": [{"name": "files", "state": "passed", "at_s": 1, "errors": []}]}


def binding_of(directory: Path) -> dict:
    index = directory / "package.json"
    return {"index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(), "files": json.loads(index.read_text())["files"]}


@pytest.fixture
def package(monkeypatch, tmp_path):
    copy = tmp_path / "package"
    shutil.copytree(v6_package.FIXTURE, copy)
    hero = tmp_path / "hero.json"
    hero.write_text(json.dumps({"package_dir": str(copy)}))
    record = tmp_path / "recorded.json"
    record.write_text(json.dumps({"receipt_sha256": hashlib.sha256((copy / "receipt.json").read_bytes()).hexdigest(), "package": binding_of(copy),
                                  "measured_utc": "2026-11-02T04:26:18+00:00", "machine": "test", "verify": CLEAN_RUN, "tamper": None}))
    monkeypatch.setattr(v6_package, "HERO_FILE", hero)
    monkeypatch.setattr(v6_package, "RECORDED", record)
    monkeypatch.setattr(v6_package, "SUMMARY", tmp_path / "none.json")
    monkeypatch.setattr(v6_package, "_JOBS", {})
    return copy


CHANGES = {"plan": lambda copy: (copy / "plan.json").write_text('{"tampered":true}'),
           "receipt": lambda copy: (copy / "receipt.json").write_text('{"tampered":true}'),
           "a paper": lambda copy: (copy / "manifest.csv").write_text("changed"),
           "a missing paper": lambda copy: (copy / "crane-sequence.csv").unlink(),
           "the index": lambda copy: (copy / "package.json").write_text(json.dumps({**json.loads((copy / "package.json").read_text()), "banner": "another"}))}


def test_a_recording_replays_only_for_the_exact_package(package):
    replay_ = v6_package._replay(json.loads((package / "receipt.json").read_text()), package)
    assert replay_["verify"] == CLEAN_RUN and replay_["checked_on"] == "2026-11-02"


@pytest.mark.parametrize("change", CHANGES)
def test_any_changed_file_drops_the_recording_and_its_checked_date(package, change):
    CHANGES[change](package)
    replay_ = v6_package._replay({}, package)
    assert replay_["verify"] is None and replay_["tamper"] is None and replay_["measured_utc"] is None and replay_["checked_on"] is None


def test_a_recording_with_only_the_receipt_hash_no_longer_counts(package):
    record = json.loads(v6_package.RECORDED.read_text())
    del record["package"]
    v6_package.RECORDED.write_text(json.dumps(record))
    assert v6_package._replay({}, package)["verify"] is None


def test_the_summary_fallback_binds_to_the_package_too(package, monkeypatch, tmp_path):
    monkeypatch.setattr(v6_package, "FIXTURE", package)
    monkeypatch.setattr(v6_package, "RECORDED", tmp_path / "none.json")
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(v6_package, "SUMMARY", summary)
    run = {"label": "scope", "verify_190_boxes": {"passed": True, "wall_s": 50.0, "checker_wall_s": 49.0}}
    summary.write_text(json.dumps(run))
    assert v6_package._replay({}, package)["verify"] is None
    summary.write_text(json.dumps({**run, "package": binding_of(package)}))
    assert v6_package._replay({}, package)["verify"]["passed"] is True
    (package / "plan.json").write_text('{"tampered":true}')
    assert v6_package._replay({}, package)["verify"] is None


@pytest.fixture
def papers(package, replay_case):
    from app.backend import build_replay
    return make_handler(build_replay(*replay_case))


def test_a_verified_package_shows_its_papers(package, papers):
    assert v6.feature(v6.package)["available"] is True
    assert ask(papers, "GET", "/api/v6/asset/bay-plan.html").status == 200


@pytest.mark.parametrize("change", CHANGES)
def test_a_changed_package_is_withdrawn_with_a_plain_reason(package, papers, change):
    CHANGES[change](package)
    block = v6.feature(v6.package)
    assert block["available"] is False and "Error" not in block["reason"] and "crane" not in block
    for name in ("bay-plan.html", "package-note-to-master.md", "package-receipt.json"):
        response = ask(papers, "GET", f"/api/v6/asset/{name}")
        assert response.status == 403 and "package" in answer(response)["error"].lower()


def test_a_package_nobody_verified_is_not_shown(package, papers, monkeypatch, tmp_path):
    monkeypatch.setattr(v6_package, "RECORDED", tmp_path / "none.json")
    assert v6.feature(v6.package)["available"] is False
    assert ask(papers, "GET", "/api/v6/asset/bay-plan.html").status == 403


def test_a_recorded_failure_is_a_failure(package):
    record = json.loads(v6_package.RECORDED.read_text())
    v6_package.RECORDED.write_text(json.dumps({**record, "verify": {**CLEAN_RUN, "passed": False}}))
    assert v6.feature(v6.package)["available"] is False


def live_job(package, **fields):
    job = v6_package._Job(False, package, None, None)
    job.package_sha256 = v6_package.package_digest(package)
    for key, value in fields.items():
        setattr(job, key, value)
    v6_package._JOBS[job.id] = job
    return job


def test_a_live_verify_that_fails_withdraws_a_recorded_pass(package):
    assert v6.feature(v6.package)["available"] is True
    job = live_job(package, status="done", passed=False)
    job.checks["rules"] = {"name": "rules", "state": "failed", "at_s": 3, "errors": ["x"]}
    block = v6.feature(v6.package)
    assert block["available"] is False and "rules" in block["reason"]
    live_job(package, status="done", passed=True)  # the newest run decides
    assert v6.feature(v6.package)["available"] is True


def test_a_live_verify_that_never_finished_is_no_pass(package):
    live_job(package, status="stopped", reason="stopped after 600 s", passed=None)
    assert v6.feature(v6.package)["available"] is False


def test_a_running_verify_changes_nothing_and_a_tamper_run_is_about_its_copy(package):
    live_job(package, status="running")
    tamper = v6_package._Job(True, package, None, None)
    tamper.status, tamper.passed = "done", False
    v6_package._JOBS[tamper.id] = tamper
    assert v6.feature(v6.package)["available"] is True


def test_a_live_pass_clears_a_package_with_no_recording_and_says_when(package, monkeypatch, tmp_path):
    monkeypatch.setattr(v6_package, "RECORDED", tmp_path / "none.json")
    live_job(package, status="done", passed=True)
    block = v6.feature(v6.package)
    assert block["available"] is True and "Checked on 20" in block["label"] and block["verification"]["verify"] is None


def test_the_job_names_the_exact_package_it_verified(package):
    job = live_job(package, status="done", passed=True)
    assert job.view()["package_sha256"] == v6_package.package_digest(package) == v6.feature(v6.package)["package_sha256"]


def test_launch_verifies_a_package_with_no_recording_once(package, monkeypatch, tmp_path):
    monkeypatch.setattr(v6_package, "RECORDED", tmp_path / "none.json")
    started = []
    monkeypatch.setattr(v6_package, "start_verify", lambda fields: started.append(fields) or {"id": "1"})
    assert "verifying" in v6_package.ensure_verified() and started == [{"tamper": False}]


def test_launch_leaves_a_verified_package_alone(package, monkeypatch):
    monkeypatch.setattr(v6_package, "start_verify", lambda fields: pytest.fail("no second run"))
    assert v6_package.ensure_verified() == "package passed"


# ---------------------------------------------------------------- 6: the label and the papers follow the whole verification
def outcome(d, job):
    code = (f"let C; global.window = {{ StageV6: {{ chapter(d) {{ C = d; }} }} }}; require({json.dumps(str(STATIC / 'stage-v6package.js'))});"
            f"console.log(JSON.stringify(C.outcome({json.dumps(d)}, {json.dumps(job)})));")
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


PAPER = {"label": "Stored plan, computed on D · Checked on D", "fresh": "Stored plan, computed on D, rechecked now", "package_sha256": "p"}
NAMES = ("files", "chain", "signature", "checker", "rules", "coverage", "handover")


def verify_job(failing=(), status="done", passed=None, package="p"):
    rows = [{"name": name, "state": "failed" if name in failing else "passed", "errors": ["why"] if name in failing else []} for name in NAMES]
    return {"status": status, "passed": not failing if passed is None else passed, "package_sha256": package, "checks": rows, "reason": None}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("job, fresh, withdrawn", [
    (None, False, False),
    ({"status": "running", "checks": []}, False, False),
    (verify_job(), True, False),
    (verify_job(failing=("rules",)), False, True),  # the review's case: the checker row passed, the rules didn't
    (verify_job(failing=("signature",)), False, True),
    (verify_job(failing=("checker",)), False, True),
    (verify_job(passed=False), False, True),  # every row passed but the run as a whole did not
    (verify_job(package="another"), False, True),
    (verify_job(status="stopped", passed=None), False, True),
    ({**verify_job(), "checks": []}, False, True),
])
def test_only_the_whole_verification_of_this_package_earns_the_fresh_label(job, fresh, withdrawn):
    out = outcome(PAPER, job)
    assert out["withdrawn"] is withdrawn and (out["label"] == PAPER["fresh"]) is fresh
    assert (out["label"] == PAPER["label"]) is (not fresh and not withdrawn)
    if withdrawn:
        assert out["failure"] and "rechecked" not in out["label"]


def test_the_chapter_draws_from_the_pure_outcome_and_can_take_its_papers_back():
    source = (STATIC / "stage-v6package.js").read_text()
    assert "outcome(d, live.verify)" in source and "desk.hidden = paper.withdrawn" in source and "chain.hidden = paper.withdrawn" in source
    assert "clean.checks.some(" not in source


# ---------------------------------------------------------------- 7: shared state costs chapters, not the page
def absent_source(tmp_path):
    return slot_config(tmp_path, hero_source="experiments/absent-hero-source.json")


def malformed_instruction(tmp_path):
    bad = tmp_path / "instruction.json"
    bad.write_text(json.dumps({"canonical_rules": "not a list"}))
    return slot_config(tmp_path, instructions={"hero": str(bad)})


def no_base_entry(tmp_path):
    source = json.loads((ROOT / "experiments/mvp/pipeline/lattice/hero.json").read_text())
    slot = json.loads(v6.HERO_FILE.read_text())["hero_slot"]
    source[slot]["instance_sha256"] = "f" * 64
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source))
    return slot_config(tmp_path, hero_source=str(path), instructions={})


@pytest.mark.parametrize("broken", [absent_source, malformed_instruction, no_base_entry])
def test_a_broken_hero_costs_its_own_chapters_only(v6_server, monkeypatch, tmp_path, broken):
    set_gate(monkeypatch, CHECKING)
    monkeypatch.setattr(v6, "HERO_FILE", broken(tmp_path))
    check_state(ask(v6_server, "GET", "/api/v6/state"))


def test_hero_raising_costs_its_own_chapters_only(v6_server, monkeypatch):
    set_gate(monkeypatch, CHECKING)

    def gone():
        raise FileNotFoundError("hero source is gone")
    monkeypatch.setattr(v6, "hero", gone)
    check_state(ask(v6_server, "GET", "/api/v6/state"))


def check_state(response):
    assert response.status == 200
    data = answer(response)
    features = data["features"]
    for name in ("intake", "voice", "xray", "copilot"):
        assert features[name]["available"] is True, (name, features[name])
    for name in ("replan", "reading"):
        assert features[name]["available"] is False and 0 < len(features[name]["reason"]) < 200, (name, features[name])
    assert data["ship"] is None and data["synthetic"]


def test_a_hero_file_that_is_not_json_still_answers(v6_server, monkeypatch, tmp_path):
    set_gate(monkeypatch, CHECKING)
    bad = tmp_path / "hero.json"
    bad.write_text("{")
    monkeypatch.setattr(v6, "HERO_FILE", bad)
    monkeypatch.setattr(v6_package, "HERO_FILE", bad)
    response = ask(v6_server, "GET", "/api/v6/state")
    assert response.status == 200 and answer(response)["features"]["replan"]["available"] is False


def test_shared_helpers_failing_cost_a_panel(v6_server, monkeypatch):
    set_gate(monkeypatch, CHECKING)

    def boom():
        raise OSError("gone")
    monkeypatch.setattr(v6, "stand_ins", boom)
    monkeypatch.setattr(v6, "rule_counts", boom)
    response = ask(v6_server, "GET", "/api/v6/state")
    data = answer(response)
    assert response.status == 200 and data["stand_ins"] == {} and data["rule_counts"] is None


def test_a_healthy_hero_is_still_a_chapter(v6_server, monkeypatch):
    set_gate(monkeypatch, CHECKING)
    data = answer(ask(v6_server, "GET", "/api/v6/state"))
    assert data["features"]["replan"]["available"] is True and data["ship"]


# ---------------------------------------------------------------- the regate memo
PARTS = {"format": "f", "instance_sha256": "i", "instance_file_sha256": "if", "plan_sha256": {"before": "b", "after": "a"}, "rule_set_sha256": "r", "lattice_entries": {"k": "v"},
         "catalogue_pin": "c", "checker_epoch": "e", "physics_epoch": "ph", "binding_fingerprint": "bf", "engine_manifest_sha256": "m", "engine_version": "0.13.0",
         "reader_identity": "ri", "gate_source_sha256": "g"}
PASSED = {"summary": {"passed": ["k1"], "rejected": [], "skipped": [], "errors": {}}, "entries": {"before": {"passed": True, "plan_sha256": "b", "checked_utc": "2026-11-02T05:45:54.405550+00:00"}}, "gauges": None}
REJECTED = {"summary": {"passed": [], "rejected": ["k1"], "skipped": [], "errors": {}}, "entries": {}, "gauges": None}


@pytest.fixture
def memo(monkeypatch, tmp_path):
    monkeypatch.delenv("STOWLM_NO_CHECK_CACHE", raising=False)
    monkeypatch.setattr(v6, "REGATE_CACHE", tmp_path / "regate")
    calls = []

    def run_with(result, parts=PARTS, confirm=lambda result: True):
        def run():
            calls.append(1)
            return result
        return v6.regate_or_memo(parts, run, confirm)
    run_with.calls = calls
    return run_with


def test_same_inputs_are_a_hit_and_the_second_launch_runs_no_check(memo):
    assert memo(PASSED)["source"] == "fresh" and len(memo.calls) == 1
    again = memo(PASSED)
    assert again["source"] == "memo" and len(memo.calls) == 1 and again["entries"] == PASSED["entries"]


def test_the_key_has_every_part_the_leader_ordered():
    assert {"instance_sha256", "plan_sha256", "rule_set_sha256", "lattice_entries", "catalogue_pin", "checker_epoch", "binding_fingerprint", "engine_manifest_sha256"} <= set(PARTS)


@pytest.mark.parametrize("part", [name for name in PARTS])
def test_any_change_in_a_key_part_is_a_miss(memo, part):
    memo(PASSED)
    assert memo(PASSED, {**PARTS, part: "changed"})["source"] == "fresh" and len(memo.calls) == 2


@pytest.mark.parametrize("which", ["before", "after"])
def test_each_hero_plan_is_in_the_key(memo, which):
    memo(PASSED)
    changed = {**PARTS, "plan_sha256": {**PARTS["plan_sha256"], which: "changed"}}
    assert memo(PASSED, changed)["source"] == "fresh"


def test_no_check_cache_forces_a_real_regate_and_refreshes_the_memo(memo, monkeypatch):
    memo(REJECTED)
    monkeypatch.setenv("STOWLM_NO_CHECK_CACHE", "1")
    assert memo(PASSED)["source"] == "fresh" and len(memo.calls) == 2
    monkeypatch.delenv("STOWLM_NO_CHECK_CACHE")
    hit = memo(PASSED)
    assert hit["source"] == "memo" and hit["summary"]["passed"] == ["k1"] and len(memo.calls) == 2


def test_a_hit_whose_stored_identity_no_longer_holds_runs_the_real_regate(memo):
    memo(PASSED)
    assert memo(PASSED, confirm=lambda result: False)["source"] == "fresh" and len(memo.calls) == 2


def test_a_broken_confirmation_is_a_miss_not_a_crash(memo):
    memo(PASSED)

    def broken(result):
        raise ValueError("lattice catalogue does not match independently pinned SHA256")
    assert memo(PASSED, confirm=broken)["source"] == "fresh"


@pytest.mark.parametrize("result", [{**PASSED, "summary": {**PASSED["summary"], "errors": {"k": "crash"}}}, {"failed": "did not finish"}], ids=["errors", "unfinished"])
def test_an_unfinished_regate_is_never_stored(memo, result):
    memo(result)
    memo(result)
    assert len(memo.calls) == 2


def test_a_corrupt_memo_is_a_miss(memo):
    memo(PASSED)
    for path in v6.REGATE_CACHE.glob("*.json"):
        path.write_text("{")
    assert memo(PASSED)["source"] == "fresh"


def test_a_memo_under_another_key_is_a_miss(memo):
    memo(PASSED)
    path = next(v6.REGATE_CACHE.glob("*.json"))
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, "parts": {**data["parts"], "checker_epoch": "other"}}))
    assert memo(PASSED)["source"] == "fresh"


def test_the_cache_lives_where_the_leader_said():
    assert v6.REGATE_CACHE == ROOT / "experiments/mvp/ui/cache/regate"


MEMO_ENTRIES = {"before": {**REGATED["entries"]["before"], "checked_utc": "2026-11-02T05:45:54.405550+00:00"},
                "after": {**REGATED["entries"]["after"], "checked_utc": "2026-11-02T05:42:42.526146+00:00"}}


def test_a_fresh_regate_says_rechecked_now(gated):
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))
    assert gate["recheck"] == "rechecked now" and gate["before"]["recheck"] == "rechecked now"


def test_a_memo_says_when_it_was_rechecked_and_never_now(gated, monkeypatch):
    monkeypatch.setattr(v6, "regate_hero", lambda: {**REGATED, "entries": MEMO_ENTRIES, "source": "memo"})
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))
    assert gate["status"] == "shown" and gate["recheck"] == "rechecked 2026-11-02 05:42 UTC (exact memo)"  # the older of the two checks
    assert gate["before"]["recheck"] == "rechecked 2026-11-02 05:45 UTC (exact memo)" and "now" not in json.dumps(gate)


def test_a_cached_failure_shows_no_plan_and_says_how_to_rerun(gated, monkeypatch):
    entries = {"before": {**MEMO_ENTRIES["before"], "passed": False}}
    monkeypatch.setattr(v6, "regate_hero", lambda: {**REGATED, "entries": entries, "source": "memo"})
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))
    assert gate["status"] == "not_shown" and "--no-check-cache" in gate["why"][0] and gate["recheck"] is None


def test_a_memoed_plan_under_the_rule_that_failed_holds_the_stored_plan(gated, monkeypatch):
    monkeypatch.setattr(v6, "regate_hero", lambda: {**REGATED, "entries": {"before": MEMO_ENTRIES["before"]}, "source": "memo"})
    gate = v6.Hero._check(FakeDemo(replay({"A": "1"}, 130.0), replay({"A": "2"}, 134.0)))
    assert gate["status"] == "before_only" and gate["after"]["passed"] is False


def test_the_regate_parts_read_the_slot_and_assume_no_hero(tmp_path, monkeypatch):
    monkeypatch.setattr(v6, "HERO_FILE", slot_config(tmp_path, hero_slot="backup", instructions={"backup": "experiments/mvp/pipeline/lattice/fixtures/absent-instruction.json"}, catalogue_sha256=None))
    config = v6.hero()
    identity = {key: "x" for key in ("checker_epoch", "physics_epoch", "binding_fingerprint", "engine_manifest_sha256", "engine_version", "reader_identity", "gate_source_sha256")}
    parts = v6.regate_parts(config, identity)
    assert parts["instance_sha256"] == config["instance_sha256"] and parts["plan_sha256"] == {"before": config["before_sha256"], "after": None}
    assert v6.regate_rules(config) == {"before": []} and parts["catalogue_pin"] is None


class Store:
    """Lattice read helpers, each one a door the confirmation has to pass."""

    def __init__(self, **changes):
        self.row = {"engine_version": "0.13.0", "engine_manifest_sha256": "m", "computed_utc": "t", "status": "checked_plan", "plan": {"id": "p"}, "instance": {"id": "i"},
                    "instance_sha256": "i-sha", "canonical_rules": [], **changes.pop("row", {})}
        self.identity = changes.pop("identity", {"passed": True, "engine_version": "0.13.0", "engine_manifest_sha256": "m"})
        self.binding, self.token, self.pinned = changes.pop("binding", True), changes.pop("token", ("t",)), []

    entry_key = staticmethod(lambda instance, rules: "key")
    canonical_rule_set = staticmethod(lambda rules: list(rules))

    def _catalogue(self, directory, refresh=False, expected_sha256=None):
        self.pinned.append((refresh, expected_sha256))
        return {"key": "digest"}

    def _read(self, key, directory, catalogue=None):
        return self.row

    def _binding_source_matches(self, row):
        return self.binding

    def _current_token(self, row):
        return self.token


def confirm_with(store, entries=None):
    from experiments.mvp.pipeline.contract.schema import sha256_json
    config = {"instance_sha256": "i-sha", "catalogue_sha256": "pin", "instruction": None}
    entries = entries if entries is not None else {"before": {"plan_sha256": sha256_json(store.row["plan"])}}
    return v6.stored_identity_holds(config, "folder", {"entries": entries}, store=store, identity=store.identity)


def test_the_warm_path_keeps_the_identity_checks_lookup_would_run():
    from experiments.mvp.pipeline.contract.schema import sha256_json
    store = Store(row={"instance_sha256": "i-sha"})
    store.row["instance_sha256"] = sha256_json(store.row["instance"])
    assert v6.stored_identity_holds({"instance_sha256": store.row["instance_sha256"], "catalogue_sha256": "pin", "instruction": None}, "folder",
                                    {"entries": {"before": {"plan_sha256": sha256_json(store.row["plan"])}}}, store=store, identity=store.identity)
    assert store.pinned == [(True, "pin")]


@pytest.mark.parametrize("store", [Store(binding=False), Store(token=None), Store(row={"engine_version": "other"}), Store(row={"engine_manifest_sha256": "other"}), Store(row={"status": "relaxed"}),
                                   Store(row={"computed_utc": None}), Store(row={"plan": None}), Store(row={"instance": None}), Store(row={"canonical_rules": ["x"]}),
                                   Store(identity={"passed": False, "engine_version": "0.13.0", "engine_manifest_sha256": "m"})],
                         ids=["binding", "token", "version", "manifest", "status", "computed", "plan", "instance", "rules", "engine"])
def test_each_lookup_identity_check_still_stops_a_warm_launch(store):
    from experiments.mvp.pipeline.contract.schema import sha256_json
    if store.row.get("instance"):
        store.row["instance_sha256"] = sha256_json(store.row["instance"])
    config = {"instance_sha256": store.row["instance_sha256"], "catalogue_sha256": "pin", "instruction": None}
    assert not v6.stored_identity_holds(config, "folder", {"entries": {"before": {"plan_sha256": sha256_json(store.row["plan"])}}}, store=store, identity=store.identity)


def test_a_memo_for_another_plan_than_the_row_is_stopped():
    from experiments.mvp.pipeline.contract.schema import sha256_json
    store = Store()
    store.row["instance_sha256"] = sha256_json(store.row["instance"])
    config = {"instance_sha256": store.row["instance_sha256"], "catalogue_sha256": "pin", "instruction": None}
    assert not v6.stored_identity_holds(config, "folder", {"entries": {"before": {"plan_sha256": "another"}}}, store=store, identity=store.identity)
