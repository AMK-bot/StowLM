"""Demo v6 x-ray and package chapters: the data, the asset routes, the verify jobs and the pure timelines. No Chrome, no checker rerun."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import textwrap
import time
from http.client import HTTPResponse
from pathlib import Path

import pytest

from app import v6, v6_package
from app.server import make_handler

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(autouse=True)
def verified(monkeypatch, request):
    """The panels are this file's subject; the package gate itself is app/tests/test_v6_gates.py's."""
    monkeypatch.setattr(v6_package, "verification_state", lambda directory: {"status": "passed", "reason": None, "package_sha256": "p", "checked_on": None})
    monkeypatch.setattr(v6_package, "require_hero_plan", lambda directory: None)  # the plan match is test_v6_hero_rule.py's


# ---------------------------------------------------------------- pure timelines
def chapter(name: str, expr: str):
    """Evaluate expr with C = the chapter definition the real page file registers."""
    code = (f"let C; global.window = {{ StageV6: {{ chapter(d) {{ C = d; }} }} }}; require({json.dumps(str(STATIC / name))});"
            f"const D = {json.dumps(v6.package() if 'package' in name else v6.xray())};"
            f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


@needs_node
def test_xray_steps_are_a_pure_walk_over_the_trace():
    expr = "(() => { const s = C.schedule(D.trace.steps, 10); return { s, walk: [...Array(101).keys()].map((i) => C.stepAt(i / 10, s)), again: C.stepAt(7.3, s) === C.stepAt(7.3, s) }; })()"
    out = chapter("stage-v6xray.js", expr)
    assert out["walk"][0] == 0 and out["walk"][-1] == len(v6.xray()["trace"]["steps"]) and out["again"]
    assert out["walk"] == sorted(out["walk"])
    gaps = [b - a for a, b in zip(out["s"], out["s"][1:])]
    film = v6.xray()
    assert film["film"] and film["adopted"], "the film's trace is the adopted reader's, with a real block"
    steps = film["trace"]["steps"]
    first_block = next(i for i, step in enumerate(steps) if step["top_k"][0].get("blocked") and not step.get("is_stop"))
    assert gaps[first_block + 1] == max(gaps) and gaps.count(max(gaps)) == 1, "the blocked word holds longest, and only it"
    assert film["blocked"] == sum(1 for step in steps if step["top_k"][0].get("blocked") and not step.get("is_stop"))  # the end-token swap isn't a block


def test_xray_film_refuses_another_adapters_trace(monkeypatch):
    real = v6._adoption()
    monkeypatch.setattr(v6, "_adoption", lambda: {**real, "adopted": {**real["adopted"], "adapter_sha256": "0" * 64}})
    got = v6.xray()
    assert not got["adopted"] and not got["film"]


def test_xray_never_autoplays_and_keeps_the_players_label():
    source = (STATIC / "stage-v6xray.js").read_text()
    assert "autoplay: false" in source and "autoplay: true" not in source
    assert ".play()" not in source
    assert "Recorded answer" not in source  # the player says it, and only for a recorded trace


@needs_node
def test_package_replay_is_pure_in_tau_and_ticks_in_order():
    expr = """(() => {
      const m = { verify: { passed: true, wall_s: 52, checks: ['files', 'chain', 'signature', 'checker', 'rules', 'coverage', 'handover', 'a_new_one'].map((name) => ({ name, state: 'passed', at_s: 1, errors: [] })) }, tamper: null };
      const done = (tau) => { const r = C.replayAt(tau, m).verify; return r ? r.checks.filter((c) => c.state === 'passed').length : -1; };
      return { before: done(C.T.verify[0] - 0.1), walk: [0, .2, .4, .6, .8, 1].map((p) => done(C.T.verify[0] + p * (C.T.verify[1] - C.T.verify[0]))),
               same: JSON.stringify(C.replayAt(8, m)) === JSON.stringify(C.replayAt(8, m)), end: C.T.end, duration: C.duration_s };
    })()"""
    out = chapter("stage-v6package.js", expr)
    assert out["before"] == -1 and out["same"] and out["walk"] == sorted(out["walk"]) and out["walk"][-1] == 8, "every check the result names lands, however many"
    assert out["end"] == out["duration"] == 13


@needs_node
def test_a_failed_clean_run_never_folds_to_passed():
    expr = """(() => {
      const esc = (x) => String(x), checks = [{ name: 'files', state: 'failed', at_s: 1, errors: ['x'] }];
      const run = (passed) => ({ passed, wall_s: 30, finished: true, status: 'done', live: true, checks });
      return [C.termHtml({ verify: run(false), tamper: run(false) }, {}, esc), C.termHtml({ verify: run(true), tamper: run(false) }, {}, esc)];
    })()"""
    failed, passed = chapter("stage-v6package.js", expr)
    assert "all checks passed" not in failed and "failed files" in failed
    assert "all checks passed" in passed


def test_no_percentage_in_the_terminal():
    source = (STATIC / "stage-v6package.js").read_text()
    assert "progress" not in source.lower().replace("progress p", "")
    assert "elapsed" in source


# ---------------------------------------------------------------- x-ray data
def test_xray_counts_come_from_the_trace():
    data = v6.xray()
    steps = data["trace"]["steps"]
    assert data["tokens"] == len(steps)
    assert data["blocked"] == sum(bool(step["top_k"][0].get("blocked")) and not step.get("is_stop") for step in steps)  # the end-token swap isn't a block
    if not data["real"]:
        assert "FAKE" in data["source_label"]


def test_missing_player_is_a_gap(monkeypatch):
    monkeypatch.setitem(v6.ASSETS, "xray-player.js", (ROOT / "no-such-player.js", "text/javascript"))
    assert v6.feature(v6.xray)["available"] is False


def test_a_real_trace_that_fails_validation_is_a_gap_not_a_fallback(monkeypatch, tmp_path):
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures/real-trace.json").write_text('{"format": "xray.trace/1", "steps": []}')
    monkeypatch.setattr(v6, "XRAY", tmp_path)
    monkeypatch.setattr(v6, "_settings", lambda: {"xray_trace": None})  # no named trace: the fixtures' recording leads
    assert v6.feature(v6.xray)["available"] is False


# ---------------------------------------------------------------- package data
def test_view_reads_the_real_package():
    data, directory = v6_package.view(), v6_package.package_dir()
    index = json.loads((directory / "package.json").read_text())
    assert data["status"] == index["status"] and data["files"] == sorted(index["files"])
    assert data["note"] == v6_package.note_as_shown(v6_package.as_shown((directory / "note-to-master.md").read_text()))
    assert data["signature_note"] == "It says what ran and that nothing has changed since. Our own signature."
    lines = (directory / "crane-sequence.csv").read_text().splitlines()
    assert data["crane"]["total"] == len([x for x in lines if not x.startswith("#")]) - 1
    assert data["crane"]["rows"][0][0] == "1" and data["crane"]["header"][0] == "lift_order"
    assert data["bay_pages"] == (directory / "bay-plan.html").read_text().count('class="bay-page"') > 0
    assert data["stability"]["gm_m"] == json.loads((directory / "stability.json").read_text())["gm_m"]


def test_hero_package_labels_come_from_its_own_provenance():
    data, directory = v6_package.view(), v6_package.package_dir()
    provenance = json.loads((directory / "hero-provenance.json").read_text())
    receipt = json.loads((directory / "receipt.json").read_text())
    if provenance["eligible"] and provenance["engine_version_required"] == receipt["engine_version"]:
        assert data["historical"] is None and data["engine_label"] == f"Engine {receipt['engine_version']}"
    scope = json.loads((directory / "handover-scope.json").read_text())
    assert data["boxes"] == scope["planned_boxes"] and data["scope_line"] and data["scope_line"] in scope["banner"]
    date = v6_package._computed_date(json.loads((directory / "package.json").read_text()))
    assert date in data["label"] and date in data["fresh"]


def test_rechecked_now_is_held_back_until_a_run_says_so():
    data = v6_package.view()
    assert v6_package.RECHECKED not in data["label"] and data["fresh"].endswith(v6_package.RECHECKED)
    without = {key: value for key, value in data.items() if key != "fresh"}
    assert v6_package.RECHECKED not in json.dumps(without)
    source = (STATIC / "stage-v6package.js").read_text()
    assert "outcome(d, live.verify)" in source and "d.fresh" in source


def test_new_papers_are_read_from_the_package():
    data, directory = v6_package.view(), v6_package.package_dir()
    text = v6_package.as_shown((directory / "not-included.html").read_text())
    assert data["not_included"] and all(row["name"] in text for row in data["not_included"])
    work = json.loads((directory / "cargo-work.json").read_text())
    assert data["cargo_work"]["status"] == work["operational_status"] and data["cargo_work"]["flagged"] == len(work["flagged_source_move_indices"])
    assert data["manifest"]["total"] == data["boxes"]
    for row in data["manifest"]["rows"]:
        assert row["box"] in (directory / "manifest.csv").read_text() and row["what"]


def test_chain_links_really_link():
    steps = v6_package.view()["receipt"]["steps"]
    assert steps[0]["prev"] == "0" * 64
    for row, following in zip(steps, steps[1:]):
        assert following["prev"] == row["hash"]


def test_a_package_built_for_another_sentence_says_so(monkeypatch, tmp_path):
    now = v6.hero_reading()
    (tmp_path / "packet.json").write_text(json.dumps({"voyage_sentences": [{"text": now["sentence"]}]}))
    assert v6_package._other_sentence(tmp_path) is None
    (tmp_path / "packet.json").write_text(json.dumps({"voyage_sentences": [{"text": "Close bay 1."}]}))
    assert "Close bay 1." in v6_package._other_sentence(tmp_path)
    (tmp_path / "packet.json").unlink()
    assert v6_package._other_sentence(tmp_path) is None
    assert "d.other_sentence" in (STATIC / "stage-v6package.js").read_text()


def test_the_fixture_is_labelled_historical_never_engine_0_13(package_copy):
    data = v6_package.view()
    assert data["label"].startswith("Stored plan") and data["label"].endswith("checked when packaged") and "rechecked now" not in data["note"]
    assert data["historical"] == "Historical development plan, not Engine 0.13.0"
    assert "not-frozen" in data["receipt"]["engine_version"]


def test_note_credit_follows_its_evidence():
    assert v6_package._note_by({"mode": "recorded", "model": "muse-spark-1.3", "rejection": []}) == "Muse Spark, from checker output"
    assert v6_package._note_by({"mode": "generated", "model": None}) == "Template, from checker output"
    assert v6_package._note_by({"mode": "recorded", "model": "muse-spark-1.3", "rejection": ["bad id"]}) == "Template, from checker output"


def test_package_dir_comes_from_the_hero_file(monkeypatch, tmp_path):
    hero = tmp_path / "hero.json"
    hero.write_text("{}")
    monkeypatch.setattr(v6_package, "HERO_FILE", hero)
    assert v6_package.package_dir() == v6_package.FIXTURE
    hero.write_text(json.dumps({"package_dir": "somewhere/else", "computed_utc": "2026-11-01T10:00:00+00:00"}))
    assert v6_package.package_dir() == (ROOT / "somewhere/else").resolve()
    assert v6.ASSETS["bay-plan.html"][0].name == "bay-plan.html"


@pytest.fixture
def package_copy(monkeypatch, tmp_path):
    copy = tmp_path / "package"
    shutil.copytree(v6_package.FIXTURE, copy)
    hero = tmp_path / "hero.json"
    hero.write_text(json.dumps({"package_dir": str(copy)}))
    monkeypatch.setattr(v6_package, "HERO_FILE", hero)
    monkeypatch.setattr(v6_package, "RECORDED", tmp_path / "none.json")
    return copy


def test_a_package_states_its_own_date_and_has_no_replay_of_another(package_copy):
    index = json.loads((package_copy / "package.json").read_text())
    (package_copy / "package.json").write_text(json.dumps({**index, "banner": "Stored plan, computed on 2026-11-01T10:00:00+00:00, rechecked now."}))
    data = v6_package.view()
    assert data["label"] == "Stored plan, computed on 2026-11-01 · Checked on 2026-11-01" and data["fresh"] == "Stored plan, computed on 2026-11-01, rechecked now"
    assert data["verification"]["verify"] is None


def test_as_shown_never_leaves_rechecked_now():
    assert v6_package.as_shown("Stored plan, computed on 2026-11-02T02:52:29+00:00, rechecked now.") == "Stored plan, computed on 2026-11-02, checked on 2026-11-02."
    assert v6_package.as_shown("rechecked now") == v6_package.PACKAGED


def test_relaxed_label_comes_first(package_copy):
    for name, change in (("package.json", {"status": "relaxed_plan"}), ("stability.json", {"banner": "RELAXED PLAN — level 2; broken rules: []; boxes ashore: 1."})):
        path = package_copy / name
        path.write_text(json.dumps({**json.loads(path.read_text()), **change}))
    assert v6_package.view()["relaxed"].startswith("RELAXED PLAN")
    assert "${d.relaxed ?" in (STATIC / "stage-v6package.js").read_text()


def test_recording_only_counts_for_the_package_it_verified(package_copy, tmp_path, monkeypatch):
    record = tmp_path / "recorded.json"
    monkeypatch.setattr(v6_package, "RECORDED", record)
    record.write_text(json.dumps({"package": {"index_sha256": "0" * 64, "files": {}}, "verify": {"passed": True}}))
    assert v6_package.view()["verification"]["verify"] is None
    run = {"passed": True, "checks": []}
    record.write_text(json.dumps({"package": v6_package.package_binding(package_copy), "verify": run}))
    assert v6_package.view()["verification"]["verify"] == run


def test_measured_summary_replay_is_labelled_with_its_seconds():
    replay = v6_package._summary_replay()
    summary = json.loads(v6_package.SUMMARY.read_text())["initial_verify_190_boxes"]
    assert replay["wall_s"] == summary["wall_s"] and replay["checker_s"] == summary["checker_wall_s"]
    assert "Measured" in replay["scope"]


# ---------------------------------------------------------------- a fixture that changes shape costs a panel, not the chapter
def edit(path: Path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("name", ["stability.json", "note-evidence.json", "crane-sequence.csv", "bay-plan.html", "note-to-master.md", "provenance.json"])
def test_each_paper_can_go_missing_alone(package_copy, name):
    (package_copy / name).unlink()
    block = v6.feature(v6.package)
    assert block["available"] is True and block["receipt"]["steps"]
    if name == "stability.json":
        assert block["stability"]["gm_m"] is None
    if name == "crane-sequence.csv":
        assert block["crane"]["rows"] == [] and block["crane"]["header"] == []
    if name == "note-evidence.json":
        assert block["note_by"] == "From checker output" and block["note_recorded"] is False


@pytest.mark.parametrize("file, change, check", [
    ("receipt.json", lambda d: d.update(stages=d.pop("steps")), lambda b: b["receipt"]["steps"] == [] and b["receipt"]["plan_note"] is None),
    ("receipt.json", lambda d: d.pop("checker"), lambda b: b["receipt"]["checker"] == {"passed": None, "level": None}),
    ("receipt.json", lambda d: d.pop("label"), lambda b: b["label"].startswith("Stored plan")),
    ("package.json", lambda d: d.update(manifest=d.pop("files")), lambda b: b["files"] == []),
    ("package.json", lambda d: d.pop("status"), lambda b: b["status"] is None),
])
def test_renamed_keys_leave_the_rest_standing(package_copy, file, change, check):
    edit(package_copy / file, change)
    block = v6.feature(v6.package)
    assert block["available"] is True and block["note"] and check(block)


@pytest.mark.parametrize("summary", [None, "not json", {"verify_190_boxes": {"passed": True}}, {"renamed": {}}, {"verify_190_boxes": "x"}])
def test_verification_summary_gone_still_shows_the_papers(monkeypatch, tmp_path, summary):
    path = tmp_path / "summary.json"
    if summary is not None:
        path.write_text(summary if isinstance(summary, str) else json.dumps(summary))
    monkeypatch.setattr(v6_package, "SUMMARY", path)
    monkeypatch.setattr(v6_package, "RECORDED", tmp_path / "none.json")
    block = v6.feature(v6.package)
    assert block["available"] is True and block["verification"]["verify"] is None and block["crane"]["rows"] and block["note"]


def test_a_recorded_run_without_checks_is_no_run(package_copy, tmp_path, monkeypatch):
    record = tmp_path / "recorded.json"
    monkeypatch.setattr(v6_package, "RECORDED", record)
    record.write_text(json.dumps({"package": v6_package.package_binding(package_copy), "verify": {"passed": True}, "tamper": {"passed": False}}))
    replay = v6_package.view()["verification"]
    assert replay["verify"] is None and replay["tamper"] is None


@pytest.mark.parametrize("name", ["package.json", "receipt.json"])
def test_no_index_or_receipt_is_no_package(package_copy, name):
    (package_copy / name).unlink()
    assert v6.feature(v6.package)["available"] is False


# ---------------------------------------------------------------- routes
class MemorySocket:
    def __init__(self, request=b""):
        self.incoming, self.outgoing = io.BytesIO(request), bytearray()

    def makefile(self, mode, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.extend(data)


@pytest.fixture
def get(replay_case):
    from app.backend import build_replay
    instance, plan = replay_case
    handler = make_handler(build_replay(instance, plan))

    def fetch(path):
        transport = MemorySocket(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        handler(transport, ("127.0.0.1", 12345), object())
        response = HTTPResponse(MemorySocket(bytes(transport.outgoing)))
        response.begin()
        return response

    return fetch


def test_bay_plan_may_be_framed_by_this_origin_only(get):
    response = get("/api/v6/asset/bay-plan.html")
    assert response.status == 200 and response.headers.get_content_type() == "text/html"
    policy = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in policy and "'unsafe-inline'" in policy and "script-src" not in policy.replace("default-src", "")
    body = response.read()
    assert b"bay-page" in body and b"rechecked now" not in body


@pytest.mark.parametrize("name", ["bay-plan.html", "package-note-to-master.md", "package-crane-sequence.csv", "package-stability.json", "package-not-included.html", "package-ballast-plan.csv", "package-manifest.csv", "package-cargo-work.json"])
def test_served_papers_are_stored_documents(get, name):
    response = get(f"/api/v6/asset/{name}")
    assert response.status == 200 and b"rechecked now" not in response.read()


@pytest.mark.parametrize("name, mime", [("xray-player.js", "text/javascript"), ("xray-player.css", "text/css")])
def test_stage_page_can_load_the_player(get, name, mime):
    response = get(f"/api/v6/asset/{name}")
    assert response.status == 200 and response.headers.get_content_type() == mime
    page = get("/stage").headers["Content-Security-Policy"]
    assert "script-src 'self'" in page and "style-src 'self'" in page and "connect-src 'self'" in page


@pytest.mark.parametrize("name", ["package-crane-sequence.csv", "package-note-to-master.md", "package-stability.json", "package-receipt.json"])
def test_package_files_are_on_the_allow_list(get, name):
    assert get(f"/api/v6/asset/{name}").status == 200


@pytest.mark.parametrize("name", ["plan.json", "../v6.py", "package-../../v6.py", "instance.json"])
def test_nothing_else_is_served(get, name):
    assert get(f"/api/v6/asset/{name}").status == 404


def test_package_route(get):
    data = json.loads(get("/api/v6/package").read())
    assert data["available"] is True and data["bay_plan"] == "/api/v6/asset/bay-plan.html"


# ---------------------------------------------------------------- jobs
SCRIPT = """
    import json, sys, time
    def emit(**e): print(json.dumps(e), flush=True)
    print("a stray line", flush=True)
    emit(names=["files", "chain", "signature", "checker", "rules", "coverage", "handover"])
    for name in ("files", "chain", "signature"): emit(check=name, passed=True, errors=[])
    time.sleep(%s)
    emit(check="checker", passed=False, errors=["plan_sha256 differs from signed receipt"], broken_rules=["expert.moves.bottom_up"])
    emit(check="rules", passed=True, errors=[])
    emit(check="coverage", passed=True, errors=[])
    emit(check="handover", passed=True, errors=[])
    emit(check="a_check_nobody_listed", passed=True, errors=[])
    emit(done=True, passed=False, wall_s=0.2)
"""


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    """Real child process and real plumbing, a scripted check. The real checker takes a minute and has its own tests."""
    monkeypatch.setattr(v6_package, "_JOBS", {})
    monkeypatch.setattr(v6_package.tempfile, "tempdir", str(tmp_path))

    def use(delay="0.2"):
        script = tmp_path / "child.py"
        script.write_text(textwrap.dedent(SCRIPT % delay))
        monkeypatch.setattr(v6_package, "_command", lambda directory, cleanup: [sys.executable, str(script)])
    return use


def wait(job_id: str, limit: float = 20) -> dict:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        row = v6_package.job(job_id)
        if row["status"] != "running":
            return row
        time.sleep(0.02)
    raise AssertionError("job did not end")


def test_job_reports_real_elapsed_and_each_check(jobs):
    jobs("0.4")
    start = v6_package.start_verify({})
    assert start["status"] == "running" and start["pid"] and start["tamper"] is False
    end = time.monotonic() + 10
    while time.monotonic() < end:
        row = v6_package.job(start["id"])
        if row["checks"][2]["state"] == "passed":
            break
    assert row["status"] == "running", row
    assert [c["state"] for c in row["checks"]] == ["passed", "passed", "passed", "running", "pending", "pending", "pending"], row
    row = wait(start["id"])
    assert row["status"] == "done" and row["passed"] is False and row["wall_s"] == 0.2
    assert row["elapsed_s"] >= 0.4 and row["checks"][3]["at_s"] >= 0.4 and row["checks"][-1]["name"] == "a_check_nobody_listed"
    assert row["checks"][3]["state"] == "failed" and row["checks"][3]["errors"] == ["plan_sha256 differs from signed receipt"]
    assert row["checks"][3]["broken_rules"] == ["expert.moves.bottom_up"]
    assert "percent" not in json.dumps(row)


def test_second_request_joins_the_running_job(jobs):
    jobs("0.4")
    first = v6_package.start_verify({"tamper": False})
    assert v6_package.start_verify({"tamper": False})["id"] == first["id"]
    wait(first["id"])


def test_bad_request_and_unknown_job():
    with pytest.raises(ValueError):
        v6_package.start_verify({"tamper": "yes"})
    assert v6_package.job("nope")["status"] == "unknown"


def test_tamper_changes_one_digit_in_a_copy_and_cleans_up(jobs, tmp_path):
    jobs()
    source = v6_package.package_dir()
    before = (source / "plan.json").read_bytes()
    start = v6_package.start_verify({"tamper": True})
    copy = v6_package._JOBS[start["id"]].directory
    assert copy != source and str(copy).startswith(str(tmp_path))
    edited = (copy / "plan.json").read_bytes()
    assert len(edited) == len(before) and sum(a != b for a, b in zip(edited, before)) == 1
    json.loads(edited)
    row = start["tampered"]
    assert row["file"] == "plan.json" and row["before"] != row["after"] and row["before"][:-1] == row["after"][:-1]
    wait(start["id"])
    assert (source / "plan.json").read_bytes() == before
    assert not copy.parent.exists()


def test_exit_stops_a_running_job_and_removes_its_temp_dir(jobs):
    jobs("60")
    start = v6_package.start_verify({"tamper": True})
    record = v6_package._JOBS[start["id"]]
    v6_package._stop_all()
    assert record.proc.wait(timeout=10) != 0
    row = wait(start["id"])
    assert row["status"] == "stopped" and row["reason"] == "the server stopped"
    assert not Path(record.temp).exists()


def test_a_child_that_dies_is_a_failure_not_a_hang(monkeypatch, jobs):
    monkeypatch.setattr(v6_package, "_command", lambda directory, cleanup: [sys.executable, "-c", "print('boom'); raise SystemExit(3)"])
    row = wait(v6_package.start_verify({})["id"])
    assert row["status"] == "failed" and row["reason"] == "boom"


def test_quick_checks_are_verify_packages_own_and_touch_nothing(tmp_path):
    copy = tmp_path / "package"
    shutil.copytree(v6_package.FIXTURE, copy)
    v6_package.change_one_digit(copy)
    import experiments.mvp.pipeline.package as package
    real = package._load
    quick = v6_package.quick_checks(copy)
    assert package._load is real
    assert set(quick) == {"files", "chain", "signature"}
    assert "plan.json: sha256 mismatch" in v6_package._errors(quick["files"])
    assert quick["signature"]["passed"] is True


def test_a_radio_clip_this_copy_does_not_ship_is_a_clean_404(get, monkeypatch, tmp_path):
    monkeypatch.setattr(v6, "radio_wav", lambda: (_ for _ in ()).throw(FileNotFoundError("no take")))
    monkeypatch.setattr(v6, "VOICE", tmp_path)  # the separate clip's folder, with no audio in it
    response = get("/api/v6/asset/radio.wav")
    assert response.status == 404 and json.loads(response.read()) == {"error": "audio not included in this copy"}
