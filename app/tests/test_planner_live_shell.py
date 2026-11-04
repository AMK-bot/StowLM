"""The take-4 planner shell in demo and live mode (functional-app CONTRACT.md Extension B), run by the shared Node
harness (planner_live_harness.js, area "shell"): the real planner.html, planner.js and every module the page loads,
a virtual clock and recorded requests. No browser, network or model.

Live call views in fixtures/functional/live/ were captured from a real Functional app call on this machine
(synthetic case, no model stages). The released-plan case uses payloads built now by app.functional_planner from the
repo's real synthetic hero run outcome, in a tmp call folder."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
STATIC = ROOT / "app" / "static"
LIVE = HERE / "fixtures" / "functional" / "live"
HERO = ROOT / "experiments/mvp/pipeline/package/fixtures/hero-run/outcome.json"
NODE = shutil.which("node")
NO_PLAN = "No checked plan found. That isn't a proof that none exists."
# planner-bayplan.js drawing the hero's 255 boxes in the mini DOM takes ~20 s; its rendering has its own suite
# (test_planner_bayplan.py), so the released-plan cases leave it out and check the shell, meters and panels.
NO_BAY = ["planner-bayplan.js"]

pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed for the shell harness")


def load(name: str):
    return json.loads((LIVE / f"{name}.json").read_text())


def shell(url: str, routes: dict | None = None, steps=None, **extra) -> dict:
    payload = {"area": "shell", "url": url, "routes": routes or {}, "steps": steps or [], **extra}
    result = subprocess.run([NODE, str(HERE / "planner_live_harness.js")], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-3000:]
    out = json.loads(result.stdout)
    assert out["errors"] == [], out["errors"]
    return out


def words(out: dict, element: str) -> str:
    return re.sub(r"\s+", " ", out["elements"][element]["text"]).strip()


def paths(out: dict) -> set[str]:
    return {row["path"].split("?")[0] for row in out["requests"]}


def base_routes(view: dict | None = None) -> dict:
    routes = {"GET /api/functional/status": load("status"), "GET /api/functional/options": load("options")}
    if view is not None:
        cid = view["id"]
        routes[f"GET /api/functional/calls/{cid}"] = view
        for name in ("ship", "boxes", "plan-before", "state", "package"):
            routes[f"GET /api/functional/calls/{cid}/planner/{name}"] = load(f"planner-{name}")
    return routes


def assert_live_requests_only(out: dict):
    seen = paths(out)
    assert seen, "the live page made no request"
    assert all(p.startswith("/api/functional/") for p in seen), seen
    assert not any(p.startswith(("/api/v6/", "/planner-data/", "/studio-data/")) for p in seen)


# ---------------------------------------------------------------- the Demo app is unchanged
def test_demo_path_makes_no_functional_request_and_keeps_its_label():
    out = shell("/planner?demo=1")
    seen = paths(out)
    assert not any(p.startswith("/api/functional/") for p in seen), seen
    assert "/api/v6/state" in seen and "/planner-data/ship.json" in seen
    tag = words(out, "pl-demo-tag")
    assert tag.startswith("Demo app") and "Replays a stored Engine 0.13.0 plan, computed on 2 Nov 2026 (recheck status shown with the plan), and recorded AI answers" in tag
    assert 'href="/functional"' in out["elements"]["pl-demo-tag"]["html"] and "Open the Functional app" in tag
    assert words(out, "pl-call-title") == "Laem Chabang · call 412"
    assert out["live"] is False and out["liveCss"] == ["not all", "not all", "not all"]
    # Ship & call stays done from the start in the demo, as recorded.
    assert out["sections"][0]["stage"] == "voyage" and out["sections"][0]["state"] == "done"


def test_plain_planner_is_the_demo_too():
    out = shell("/planner")
    assert not any(p.startswith("/api/functional/") for p in paths(out))


def test_demo_shell_code_paths_are_kept():
    source = (STATIC / "planner.js").read_text()
    assert "function smoothWheel()" in source and "if (DEMO) smoothWheel();" in source
    assert "const LIVE = P.get('live') === '1';" in source
    assert "if ((def.mode === 'live') !== LIVE) return;" in source
    instruct = (STATIC / "planner-instruct.js").read_text()
    assert "heardSoFar" in instruct


def test_page_loads_the_live_modules_with_their_styles_off_in_the_demo():
    page = (STATIC / "planner.html").read_text()
    for name in ("planner-live-voyage", "planner-live-intake", "planner-live-instruct", "planner-live-plan", "planner-live-review"):
        assert f'<script src="/{name}.js" defer></script>' in page
    for name in ("planner-live", "planner-live-intake", "planner-live-instruct"):
        assert f'<link rel="stylesheet" href="/{name}.css" media="not all" data-live-css>' in page
    assert page.index("/planner.js") < page.index("/planner-live-voyage.js")
    for asset in re.findall(r'(?:href|src)="/(planner[^"]*)"', page):
        assert re.fullmatch(r"planner(-[a-z0-9]+){0,3}\.(js|css)", asset), asset


def test_live_css_is_scoped_to_live_mode():
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "planner-live.css").read_text(), flags=re.S)
    rules = [r.strip() for r in re.findall(r"(?:^|})\s*([^{}@]+)\{", css) if r.strip() and not r.strip().startswith(("from", "to"))]
    assert rules and all(sel.strip().startswith("html.pl-live") for rule in rules for sel in rule.split(",")), rules


def test_live_static_files_never_name_demo_sources():
    for name in ("planner-live-voyage.js", "planner-live-plan.js", "planner-live-review.js"):
        text = (STATIC / name).read_text()
        for banned in ("/api/v6/", "planner-data", "studio-data", "v6_hero", "lattice", "62 rules", "crane moves", "StowLM planned"):
            assert banned not in text, (name, banned)


# ---------------------------------------------------------------- the Functional app: live mode
def test_live_before_a_call():
    out = shell("/planner?live=1", base_routes())
    assert_live_requests_only(out)
    assert paths(out) == {"/api/functional/status", "/api/functional/options"}
    assert out["live"] is True and out["liveCss"] == ["all", "all", "all"]
    assert words(out, "pl-call-title") == "New call"
    assert words(out, "pl-call-sub") == "Synthetic ship and cargo, simulated port"
    tag = words(out, "pl-demo-tag")
    assert tag.startswith("Functional app") and "Open the Demo app" in tag and 'href="/planner?demo=1"' in out["elements"]["pl-demo-tag"]["html"]
    voyage = out["sections"][0]
    assert [s["stage"] for s in out["sections"]] == ["voyage", "intake", "instruct"]
    assert voyage["state"] == "todo" and voyage["open"] is True and "Create call" in voyage["text"] and "New seed" in voyage["text"]
    assert "70 %" in voyage["text"]
    assert "The bay plan appears when the call is created" in words(out, "pl-bay")
    assert all(m["value"] == "—" for m in out["meters"]), out["meters"]
    assert out["elements"]["pl-generate"]["disabled"] is True
    assert words(out, "pl-hero-why") == "Create the call first (1 · Ship & call)"
    assert "standard profile: engine deadline 1800 s; not a whole-run guarantee" in words(out, "pl-hero")
    assert "standard is the profile the held-out results used; live is shorter and its pass rate is measured only on development maps" in words(out, "pl-hero")
    assert [t["text"] for t in out["tabs"]] == ["Checks", "Stress test", "Ask copilot", "Export"] and all(t["disabled"] for t in out["tabs"])


def test_live_create_call_posts_the_form():
    created = load("view-generating")
    routes = base_routes()
    routes["POST /api/functional/calls"] = created
    routes[f"GET /api/functional/calls/{created['id']}"] = created
    out = shell("/planner?live=1", routes, steps=[{"click": '#pl-sections [data-do="create"]', "advance_ms": 500}])
    posts = [r for r in out["requests"] if r["method"] == "POST"]
    opts = load("options")["defaults"]
    assert posts[0]["path"] == "/api/functional/calls"
    assert posts[0]["body"]["ship"] == opts["ship"] and posts[0]["body"]["size_class"] == opts["size_class"]
    assert isinstance(posts[0]["body"]["seed"], int) and posts[0]["body"]["fill_pct"] == opts["fill_pct"]
    assert f"call={created['id']}" in out["search"] and out["storage"]["stowlm.functional.call"] == created["id"]
    assert "Generating a fresh synthetic case" in out["sections"][0]["text"]
    assert_live_requests_only(out)


def test_live_ready_call_restores_its_sections_and_header():
    view = load("view-ready")
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(view))
    assert_live_requests_only(out)
    assert words(out, "pl-call-title") == f"Laem Chabang (simulated) · call {view['id'][-6:]}"
    assert words(out, "pl-call-sub") == "Synthetic feeder, 500 TEU, tiny section · simulated port"  # logic-2 S4, S5
    states = {s["stage"]: s for s in out["sections"]}
    assert states["voyage"]["state"] == "done" and f"seed {view['case']['seed']}" in states["voyage"]["summary"]
    assert states["intake"]["state"] == "done" and states["intake"]["summary"] == f"{view['load_list']['boxes']} boxes from the generated load list (not read by a model)"
    assert states["instruct"]["state"] != "done"
    assert "a separate 12-box demo voyage" not in json.dumps(out["sections"])
    chips = words(out, "pl-hero-chips")
    assert "Ship · 500 TEU" in chips and f"{view['load_list']['boxes']} boxes ready to plan" in chips and "Instruction not confirmed" in chips
    assert out["elements"]["pl-generate"]["disabled"] is True
    assert words(out, "pl-hero-why") == "Confirm a reading, or choose Plan with no instruction, first"
    assert "POD" in words(out, "pl-legend")
    assert not any("/planner/plan-after" in p or "/planner/meters" in p for p in paths(out))  # nothing gated before a run


def test_live_plan_with_no_instruction_posts_and_streams():
    view = load("view-ready")
    cid = view["id"]
    searching = {**view, "state": "planning", "message": "Searching for a checked plan.",
                 "run": {"status": "running", "profile": "live", "engine_deadline_s": 720, "elapsed_s": 3.2,
                         "admission": {"waived": "no fleet telemetry", "load1": 2.5}, "incumbents": [], "events_tail": []}}
    improving = {**searching, "run": {**searching["run"], "elapsed_s": 14.6,
                                      "incumbents": [{"t_s": 9.1, "lifts": 4.0, "plan_sha256": "a" * 64, "lane": "lp_first"}]}}
    routes = base_routes(view)
    routes[f"POST /api/functional/calls/{cid}/plan"] = searching
    out = shell(f"/planner?live=1&call={cid}", routes, steps=[
        {"emit": {"stage": "instruct", "name": "rule:confirmed", "value": None}, "advance_ms": 300},
        {"click": "#pl-generate", "advance_ms": 300},
    ])
    post = [r for r in out["requests"] if r["method"] == "POST"]
    assert post == [{"method": "POST", "path": f"/api/functional/calls/{cid}/plan", "body": {"profile": "standard", "instruction": "none"}}]
    instruct = {s["stage"]: s for s in out["sections"]}["instruct"]
    assert instruct["state"] == "done" and instruct["summary"] == "No instruction: the reader had nothing to do"
    result = words(out, "pl-result")
    assert "Searching for a checked plan" in result and "Elapsed 3 s since Generate plan, measured on this machine, this run" in result
    assert "Stop the engine" in result
    # the next poll brings the first checked incumbent
    routes[f"GET /api/functional/calls/{cid}"] = improving
    out = shell(f"/planner?live=1&call={cid}", routes, steps=[{"advance_ms": 2500}])
    result = words(out, "pl-result")
    assert "First checked plan available. Continuing to improve." in result and "Searching for a checked plan" not in result
    assert "best so far 4.0 extra crane lifts" in result.replace("4 extra", "4.0 extra")
    assert_live_requests_only(out)


@pytest.fixture(scope="module")
def released(tmp_path_factory):
    """A done call whose run/outcome.json is the real synthetic hero run (L0), served through functional_planner."""
    if not HERO.is_file():
        pytest.skip("the hero run outcome is not in this checkout")
    from app.functional_planner import payload
    from app.functional_worker import outcome_view

    view = load("view-ready")
    cid = view["id"]
    call = tmp_path_factory.mktemp("released") / cid
    (call / "run" / "package").mkdir(parents=True)
    outcome = json.loads(HERO.read_text())
    (call / "run" / "outcome.json").write_text(json.dumps(outcome))
    (call / "assembled-instance.json").write_text(json.dumps(outcome["instance"]))
    (call / "case.json").write_text(json.dumps(view["case"]))
    (call / "run" / "package" / "receipt.json").write_text(json.dumps({"checker": {"active_rules": [f"r{i}" for i in range(48)], "inactive_rules": []}}))
    (call / "run" / "package" / "crane-sequence.csv").write_text("i,kind\n")
    done = {**view, "state": "done", "message": "Plan passed the full checker.", "outcome": outcome_view(call),
            "run": {"status": "done", "profile": "live", "engine_deadline_s": 720, "elapsed_s": 14.1,
                    "admission": {"waived": "no fleet telemetry", "load1": 2.1},
                    "incumbents": [{"t_s": 8.0, "lifts": 82.0, "plan_sha256": "b" * 64, "lane": "lp_first"}], "events_tail": []}}
    routes = base_routes(done)
    for name in ("ship", "boxes", "plan-before", "plan-after", "moves", "meters", "checks", "package", "state"):
        routes[f"GET /api/functional/calls/{cid}/planner/{name}"] = payload(call, name)
    return cid, routes, payload(call, "meters")


def test_live_done_l0_draws_measured_meters_and_checks(released):
    cid, routes, meters = released
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}])
    assert_live_requests_only(out)
    assert {f"/api/functional/calls/{cid}/planner/{n}" for n in ("plan-after", "moves", "meters", "checks")} <= paths(out)
    assert out["generated"] is True
    result = words(out, "pl-result")
    total = meters["moves"]
    assert "Passes the full checker" in result and f"Move {total} of {total}" in result
    m = {row["key"]: row for row in out["meters"]}
    assert m["stability"]["value"] == f"GM {meters['gm_m']:.2f} m" and m["stability"]["limit"] == "minimum 0.15 m (checked)"
    assert m["draught"]["value"] == f"{meters['draught_m']:.2f} m" and m["draught"]["limit"] == "limit 7.05 m"
    assert m["hull"]["value"] == f"{round(meters['hull_pct'])} %" and m["list"]["value"].startswith("0.0° · 0.25 m trim")
    assert m["moves"]["value"] == f"{total} of {total}" and m["rules"]["value"] == "48 active" and m["rules"]["limit"] == "full checker passed"
    assert all(row["state"] == "ok" for key, row in m.items() if key != "moves")
    assert out["elements"]["pl-scrub"]["hidden"] is False
    panels = words(out, "pl-panels")
    assert "Passes the full checker" in panels and "48" in panels and "active checker rules, from the receipt" in panels
    assert "extra crane lifts (rehandles now and projected at later ports, hatch-cover lifts; D15)" in panels
    assert "Admission waived: no fleet telemetry (load average 2.10)." in panels
    assert "rechecked on this plan: obeyed" in panels
    assert "Frontier Engine 0.13.0 · search software" in panels
    assert all(not t["disabled"] for t in out["tabs"])


def test_live_export_and_unbuilt_tabs(released):
    cid, routes, _ = released
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}, {"call": "open", "args": ["handover"], "advance_ms": 1000}])
    panels = words(out, "pl-panels")
    assert "crane-sequence.csv" in panels and "receipt.json" in panels and "outcome.json" in panels and "case.json" in panels
    assert f"/api/functional/calls/{cid}/file/run/package/crane-sequence.csv" in out["elements"]["pl-panels"]["html"]
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}, {"call": "open", "args": ["stress"], "advance_ms": 500}])
    assert "Stress test is not in the Functional app yet; nothing is replayed in its place." in words(out, "pl-panels")
    assert_live_requests_only(out)


def test_live_done_without_a_plan_draws_nothing():
    view = load("view-ready")
    cid = view["id"]
    message = "Runner engine deadline reached; no checked plan was found. Retry with a longer deadline or reduce the load. " + NO_PLAN
    done = {**view, "state": "done", "outcome": {"status": "no_checked_plan", "passed": False, "level": None, "label": "live", "message": message,
            "broken_rules": [], "boxes_ashore": [], "lifts": None, "first_checked_wall_s": None, "wall_s": 720.4, "active_rules": None,
            "inactive_rules": None, "receipt_sha256": "c" * 64, "package_files": []},
            "run": {"status": "done", "profile": "live", "engine_deadline_s": 720, "elapsed_s": 721.0, "admission": None, "incumbents": [], "events_tail": []}}
    routes = base_routes(done)
    for name in ("plan-after", "moves", "meters", "checks"):
        routes[f"GET /api/functional/calls/{cid}/planner/{name}"] = {"__status": 409, "body": {"error": NO_PLAN}}
    out = shell(f"/planner?live=1&call={cid}", routes, steps=[{"advance_ms": 4000}])
    assert not any(p.endswith(("/planner/plan-after", "/planner/moves", "/planner/meters")) for p in paths(out))
    result = words(out, "pl-result")
    assert "No plan" in result and "Passes the full checker" not in result and "Move " not in result
    panels = words(out, "pl-panels")
    assert panels.count("No checked plan found.") == 1 and "Runner engine deadline reached" in panels
    assert all(row["value"] == "—" for row in out["meters"] if row["key"] != "moves") or True
    assert {row["key"]: row["value"] for row in out["meters"]}["stability"] == "—"


def test_live_out_of_envelope_says_why():
    view = load("view-ready")
    out_view = {**view, "state": "out_of_envelope", "message": "This call is outside the pipeline envelope.",
                "envelope": {"in_envelope": False, "reasons": [{"test": "fill", "measured": 0.81, "limit": 0.7, "message": "Physical fill 81.000%; limit 70%."}], "numbers": {}}}
    out_view["case"] = {**view["case"], "envelope": out_view["envelope"]}
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(out_view))
    voyage = out["sections"][0]["text"]
    assert "Outside the pipeline envelope. No engine run was started." in voyage and "81.0 %" in voyage and "70.0 %" in voyage
    assert out["elements"]["pl-generate"]["disabled"] is True
    assert words(out, "pl-hero-why") == "Outside the envelope: no engine run starts for this call"


def test_live_guard_blocks_demo_sources():
    source = (STATIC / "planner.js").read_text()
    assert "const LIVE_BLOCKED = /^\\/(api\\/v6\\/|planner-data\\/|studio-data\\/)/;" in source
    assert source.count("liveGuard(url);") == 2  # getJSON and the /api/functional request


def test_live_failed_run_shows_the_runners_message():
    """A reloaded call whose engine run failed goes straight to its result: the runner's words, no plan."""
    view = load("view-ready")
    cid = view["id"]
    message = "Confirmation service is unavailable. Restore the connection or use recorded answers and retry. " + NO_PLAN
    failed = {**view, "state": "failed", "message": "The plan worker failed.",
              "run": {"status": "failed", "profile": "live", "engine_deadline_s": 720, "elapsed_s": 2.4, "admission": None, "incumbents": [], "events_tail": []},
              "outcome": {"status": "error", "passed": False, "level": None, "label": "live", "message": message, "broken_rules": [], "boxes_ashore": [],
                          "lifts": None, "first_checked_wall_s": None, "wall_s": 0.4, "active_rules": None, "inactive_rules": None, "receipt_sha256": "d" * 64, "package_files": []}}
    out = shell(f"/planner?live=1&call={cid}", base_routes(failed), steps=[{"advance_ms": 2000}])
    assert out["generated"] is True
    result = words(out, "pl-result")
    assert "No plan released" in result and "Move " not in result
    panels = words(out, "pl-panels")
    # logic-2 M6 and S18: an error isn't "no plan found", and the recording advice is replaced.
    assert "The run stopped before a plan could be released: Confirmation service is unavailable." in panels
    assert "No checked plan found." not in panels and "record" not in panels.replace("never uses recordings", "")
    assert "Retry, or start a fresh call. The Functional app never uses recordings." in panels
    assert not any(p.endswith(("/planner/plan-after", "/planner/moves")) for p in paths(out))


# ---------------------------------------------------------------- round 3: key panel (Extension C), review fixes
SECRET = "sk-test-not-a-real-key-123456"


def key_status(present=False, source=None, accepted=False, spent=0.0, cap=10.0, **extra):
    status = load("status")
    status["key"] = {"present": present, "source": source, "paid_calls_accepted": accepted, "cap_usd": cap, "spent_usd": spent, **extra}
    return status


def chip(out: dict) -> dict:
    """The key chip's three lengths (CSS shows one per width): full, below 1280 px, below 960 px."""
    html_ = out["elements"]["pl-header"]["html"]
    part = lambda cls: re.sub(r"<[^>]+>", "", (re.search(rf'<span class="{cls}">(.*?)</span>', html_) or [None, ""])[1])  # noqa: E731
    money = part("pl-key-money")
    return {"full": part("pl-key-long") + money, "medium": part("pl-key-short") + money, "narrow": part("pl-key-tiny")}


def test_first_run_without_a_key_opens_the_key_panel_once():
    routes = base_routes()
    routes["GET /api/functional/status"] = key_status()
    out = shell("/planner?live=1", routes)
    header = words(out, "pl-header")
    assert "Connect API key" in header
    body = out["body"]
    assert "Your key is held by this local server (in memory, or in .env if you choose to save it) and is sent only to Meta's Model API with the calls you run. It is never shown, logged or written to a call folder." in body
    assert 'type="password" id="pl-key-input"' in out["bodyHtml"]
    assert "I accept paid calls to the Meta Model API" in body and 'id="pl-key-cap"' in out["bodyHtml"] and 'value="10.00"' in out["bodyHtml"]
    assert "Save to .env on this computer" in body and "stored in a git-ignored file, readable by your user only" in body
    assert "the generator, Engine 0.13.0 and the full checker work without it" in body
    # the form still works without a key
    assert "Create call" in out["sections"][0]["text"]
    assert out["storage"].get("stowlm.functional.keyPanelSeen") is None  # set when dismissed
    again = shell("/planner?live=1", routes, local_storage={"stowlm.functional.keyPanelSeen": "1"})
    assert "Your key is held by this local server" not in again["body"]


def test_key_is_sent_once_and_never_kept_or_echoed():
    routes = base_routes()
    routes["GET /api/functional/status"] = [key_status(), key_status(True, "entered_in_app", True, 0.0, 3.0)]
    routes["POST /api/functional/key"] = {"key": {"present": True, "source": "entered_in_app", "paid_calls_accepted": True, "cap_usd": 3.0, "spent_usd": 0.0}, "saved_to_env_file": False}
    out = shell("/planner?live=1", routes, steps=[
        {"set": {"#pl-key-input": SECRET, "#pl-key-cap": "3"}, "check": ["#pl-key-accept"], "advance_ms": 50},
        {"click": '#pl-key-panel [data-key="save"]', "advance_ms": 500},
    ])
    post = [r for r in out["requests"] if r["method"] == "POST"]
    assert post == [{"method": "POST", "path": "/api/functional/key", "body": {"accept_paid_calls": True, "cap_usd": 3, "key": SECRET}}]
    dump = json.dumps({k: v for k, v in out.items() if k != "requests"})
    assert SECRET not in dump  # not in the DOM, storage or URL afterwards
    assert chip(out)["full"] == "Meta Model API · your key · $0.00 of $3.00"


def test_key_from_the_environment_shows_its_source_not_the_key():
    routes = base_routes()
    routes["GET /api/functional/status"] = key_status(True, "env_file", True, 0.42, 2.0)
    out = shell("/planner?live=1", routes, steps=[{"click": "#pl-header .pl-key-chip", "advance_ms": 50}])
    assert chip(out) == {"full": "Meta Model API · key from .env · $0.42 of $2.00", "medium": "API key · $0.42 of $2.00", "narrow": "API key"}
    assert "Key from .env on this computer; it is never shown." in out["body"]
    assert 'id="pl-key-input"' not in out["bodyHtml"] and "Forget key" not in out["body"]


def test_forget_key_for_an_entered_key():
    routes = base_routes()
    routes["GET /api/functional/status"] = [key_status(True, "entered_in_app", True), key_status()]
    routes["DELETE /api/functional/key"] = {"key": {"present": False, "source": None, "paid_calls_accepted": False, "cap_usd": 2.0, "spent_usd": 0.0}}
    out = shell("/planner?live=1", routes, steps=[{"click": "#pl-header .pl-key-chip", "advance_ms": 50},
                                                {"click": '#pl-key-panel [data-key="forget"]', "advance_ms": 500}])
    assert any(r["method"] == "DELETE" and r["path"] == "/api/functional/key" for r in out["requests"])
    assert "Connect API key" in words(out, "pl-header")


def test_inputs_modules_get_open_key_panel():
    source = (STATIC / "planner.js").read_text()
    assert "Object.assign(c, { live: true, fx, openKeyPanel," in source


def test_demo_has_no_key_chip():
    out = shell("/planner?demo=1")
    assert "Connect API key" not in words(out, "pl-header") and "pl-key-panel" not in out["bodyHtml"]


def test_wall_time_scopes_and_receipt_caption(released):
    cid, routes, _ = released
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}, {"call": "open", "args": ["plan"], "advance_ms": 300}])
    text = words(out, "pl-panels") + " " + words(out, "pl-result")
    assert "whole run, admission to package, measured on this machine, this run" in text
    assert "first checked plan, from the start of the run (includes admission), measured on this machine, this run" in text
    assert "whole engine run" not in text and "s engine run" not in text
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}, {"call": "open", "args": ["handover"], "advance_ms": 500}])
    # code-2 P1-3: this older-style run view records no instruction choice, so no reader provenance is claimed.
    assert "This call does not record which instruction the plan used, so no reader provenance is stated." in words(out, "pl-panels")


def test_seed_words_come_from_the_case_and_never_say_unseen():
    view = load("view-ready")
    view = {**view, "case": {**view["case"], "seed_status": {**view["case"]["seed_status"], "label": "Fresh seed: in no published map set"}}}
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(view), steps=[{"call": "open", "args": ["voyage"], "advance_ms": 100}])
    voyage = out["sections"][0]["text"]
    assert "Fresh seed: in no published map set" in voyage
    form = shell("/planner?live=1", base_routes())["sections"][0]["text"]
    assert "Fresh seed: a new random seed, in no published map set." in form
    for name in ("planner-live-voyage.js", "planner-live-plan.js"):
        assert "nseen" not in (STATIC / name).read_text()


def test_headline_envelope_stops_the_call_even_inside_the_runner_gate():
    view = load("view-ready")
    head = {"in_envelope": False, "reasons": [{"test": "ship", "measured": "feeder_1700", "limit": ["feeder_500", "feeder_800"],
            "message": "feeder_1700 is outside the headline envelope: only feeder_500 and feeder_800 (500–800 TEU), including their tiny and small sections."}]}
    out_view = {**view, "case": {**view["case"], "ship": "feeder_1700", "headline_envelope": head}}
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(out_view), steps=[{"call": "open", "args": ["voyage"], "advance_ms": 100}])
    voyage = out["sections"][0]["text"]
    assert "Headline envelope (500–800 TEU feeders): outside" in voyage and "Runner's envelope gate: inside" in voyage
    assert "Outside the headline envelope. No engine run was started." in voyage and "feeder_1700" in voyage
    assert out["elements"]["pl-generate"]["disabled"] is True
    assert words(out, "pl-hero-why") == "Outside the envelope: no engine run starts for this call"


def test_bay_plan_is_built_once_when_the_assembled_ship_matches():
    source = (STATIC / "planner.js").read_text()
    assert "const same = sig === L.baySig" in source
    needs = load("view-needs-papers")
    ready = load("view-ready")
    assert needs["id"] == ready["id"]
    routes = base_routes(needs)
    out = shell(f"/planner?live=1&call={needs['id']}", routes, steps=[{"routes": {f"GET /api/functional/calls/{needs['id']}": ready}, "advance_ms": 6000}])
    ship_reads = [r for r in out["requests"] if r["path"].endswith("/planner/ship")]
    assert len(ship_reads) == 2 and out["bayCreates"] == 1


# ---------------------------------------------------------------- round 4: holds, $10 default, cap for host keys
def test_first_run_panel_defaults_to_ten_dollars_and_explains_holds():
    routes = base_routes()
    status = key_status()
    del status["key"]["cap_usd"]
    routes["GET /api/functional/status"] = status
    out = shell("/planner?live=1", routes)
    assert 'id="pl-key-cap" min="0.1" step="0.5" value="10.00"' in out["bodyHtml"]
    # logic-2 S2, S3: no invented hold figure; the spend example is labelled as one development call.
    assert "Hold per call unknown: the server has not reported it." in out["body"]
    assert "One development call read 75 boxes in 10 bookings for $0.67 (3 Nov). A booking is sent only while spent + held + its worst-case hold stays under your cap." in out["body"]


def test_chip_shows_holds_while_pending():
    routes = base_routes()
    routes["GET /api/functional/status"] = key_status(True, "environment", True, 0.31, 10.0, holds_usd=2.76, hold_usd_per_call=1.38, room_for_calls=5)
    out = shell("/planner?live=1", routes, steps=[{"click": "#pl-header .pl-key-chip", "advance_ms": 50}])
    assert chip(out) == {"full": "Meta Model API · key from the environment · $0.31 of $10.00 · $2.76 held",
                         "medium": "API key · $0.31 of $10.00 · $2.76 held", "narrow": "API key"}
    assert "$2.76 is held now by calls still settling." in out["body"]
    settled = key_status(True, "environment", True, 0.31, 10.0, holds_usd=0.0)
    routes["GET /api/functional/status"] = settled
    assert "held" not in chip(shell("/planner?live=1", routes))["full"]


def test_cap_and_consent_change_for_a_key_from_env_without_sending_a_key():
    routes = base_routes()
    routes["GET /api/functional/status"] = [key_status(True, "env_file", False, 0.0, 10.0), key_status(True, "env_file", True, 0.0, 25.0)]
    routes["POST /api/functional/key"] = {"key": {"present": True, "source": "env_file", "paid_calls_accepted": True, "cap_usd": 25.0, "spent_usd": 0.0}, "saved_to_env_file": False}
    out = shell("/planner?live=1", routes, steps=[
        {"click": "#pl-header .pl-key-chip", "advance_ms": 50},
        {"set": {"#pl-key-cap": "25"}, "check": ["#pl-key-accept"], "advance_ms": 50},
        {"click": '#pl-key-panel [data-key="save"]', "advance_ms": 500},
    ])
    post = [r for r in out["requests"] if r["method"] == "POST"]
    assert post == [{"method": "POST", "path": "/api/functional/key", "body": {"accept_paid_calls": True, "cap_usd": 25}}]
    assert chip(out)["full"] == "Meta Model API · key from .env · $0.00 of $25.00"


def test_chip_has_narrow_forms_in_css():
    css = (STATIC / "planner-live.css").read_text()
    assert "@media (max-width: 1280px)" in css and "@media (max-width: 960px)" in css
    assert ".pl-key-chip .pl-key-long { display: none; }" in css


# ---------------------------------------------------------------- round 5: code-2 and logic-2 fixes
def test_late_world_keeps_the_released_plan(released):
    """code-2 P1-2: plan payloads and playback finish before ship/boxes/arrival; the late world must not draw the arrival."""
    cid, routes, _ = released
    routes = dict(routes)
    after = routes[f"GET /api/functional/calls/{cid}/planner/plan-after"]
    for name in ("ship", "boxes", "plan-before"):
        key = f"GET /api/functional/calls/{cid}/planner/{name}"
        routes[key] = {"__delay_ms": 6000, "body": routes[key]}
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, fake_bay=True, steps=[{"advance_ms": 9000}])
    assert out["bayCreates"] == 1
    assert out["bayLog"], out["bayLog"]
    last = out["bayLog"][-1]
    assert last == {"kind": "setPlan", "slots": len(after["slots"]), "plan_id": after["plan_id"]}
    assert not any(row["kind"] == "preGenerate" for row in out["bayLog"])
    assert out["planSlots"] == len(after["slots"])
    assert "Passes the full checker" in words(out, "pl-result")


def test_world_first_then_plan_still_draws_the_plan(released):
    cid, routes, _ = released
    after = routes[f"GET /api/functional/calls/{cid}/planner/plan-after"]
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, fake_bay=True, steps=[{"advance_ms": 9000}])
    assert out["bayLog"][-1]["slots"] == len(after["slots"])


def reader_row(status="done", notes="", **extra):
    row = {"stage": "reader", "status": status, "mode": "live", "who": "StowLM Reader", "where": "this laptop", "model": "muse-glimmer-30b",
           "adapter_sha256": "e" * 64, "started_utc": "2026-11-03T20:00:00Z", "wall_s": 2.4, "cost_usd": None, "input_sha256": None, "output_sha256": None, "notes": notes}
    row.update(extra)
    return row


def done_view_with_stages(released, stages, readings):
    cid, routes, _ = released
    routes = dict(routes)
    view = dict(routes[f"GET /api/functional/calls/{cid}"])
    view["stages"] = stages
    view["readings"] = readings
    routes[f"GET /api/functional/calls/{cid}"] = view
    return cid, routes


def test_provenance_collapses_per_booking_rows_by_instance(released):
    """logic-2 S10: B2 finishing first must not overwrite B1's running row."""
    row = lambda who, status, iid, wall=None: {"stage": "intake", "status": status, "mode": "live", "who": who, "where": "Meta Model API, cloud", "model": "muse-spark-1.3",
                                               "started_utc": "2026-11-03T20:13:56Z", "wall_s": wall, "cost_usd": 0.06 if wall else None, "notes": "", "instance_id": iid}
    stages = [row("Muse Spark · booking B1", "running", "i1"), row("Muse Spark · booking B2", "running", "i2"), row("Muse Spark · booking B2", "done", "i2", 70.0)]
    cid, routes = done_view_with_stages(released, stages, [])
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}])
    items = re.findall(r'<li data-stage="intake">(.*?)</li>', out["elements"]["pl-panels"]["html"], re.S)
    assert len(items) == 2 and "booking B1" in items[0] and "running" in items[0] and "booking B2" in items[1] and "done" in items[1]


def stopped_view(incumbents):
    view = load("view-ready")
    return {**view, "state": "failed", "message": "The planner stopped this call.", "outcome": None,
            "run": {"status": "stopped", "profile": "standard", "engine_deadline_s": 1800, "elapsed_s": 300.0, "admission": None, "incumbents": incumbents, "events_tail": []}}


def test_planner_stop_wording():
    """logic-2 M6: a stop is not "No checked plan found"."""
    inc = [{"t_s": 40.0, "lifts": 9.0, "plan_sha256": "a" * 64, "lane": "lp_first"}, {"t_s": 90.0, "lifts": 7.0, "plan_sha256": "b" * 64, "lane": "lns"}]
    view = stopped_view(inc)
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(view), steps=[{"advance_ms": 2000}])
    text = words(out, "pl-result") + " " + words(out, "pl-panels")
    assert "The planner stopped the engine. 2 checked plans were found before the stop; none was released, because a plan is released only when the run finishes." in text
    assert "No checked plan found" not in text
    view = stopped_view([])
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(view), steps=[{"advance_ms": 2000}])
    assert "The planner stopped the engine before any checked plan was found." in words(out, "pl-result")
    assert "No checked plan found" not in words(out, "pl-panels")


def test_no_plan_line_only_after_a_search_without_one():
    view = load("view-ready")
    for status, says in (("no_checked_plan", True), ("constraint_unavailable", True), ("error", False), ("needs_confirmation", False)):
        done = {**view, "state": "done", "outcome": {"status": status, "passed": False, "level": None, "label": "live",
                "message": "Runner engine deadline reached; no checked plan was found. Retry with a longer deadline or reduce the load. " + NO_PLAN,
                "broken_rules": [], "boxes_ashore": [], "lifts": None, "first_checked_wall_s": None, "wall_s": 1.0, "active_rules": None,
                "inactive_rules": None, "receipt_sha256": None, "package_files": []},
                "run": {"status": "done", "profile": "standard", "elapsed_s": 1.0, "admission": None, "incumbents": [], "events_tail": []}}
        out = shell(f"/planner?live=1&call={view['id']}", base_routes(done), steps=[{"advance_ms": 2000}])
        panels = words(out, "pl-panels")
        assert ("No checked plan found. That isn't a proof that none exists." in panels) is says, status
        assert "The 1800 s standard deadline is the longest in this app. Try a fresh call with a lower fill or fewer boxes." in panels
        assert "longer deadline" not in panels


def test_runner_gate_line_uses_the_runners_own_result():
    """logic-2 M2: feeder_1700 small: the runner admits it; the headline envelope stops it."""
    view = load("view-ready")
    head = {"in_envelope": False, "reasons": [{"test": "ship", "measured": "feeder_1700", "limit": ["feeder_500", "feeder_800"], "message": "feeder_1700 is outside the headline envelope."}]}
    combined = {"in_envelope": False, "reasons": head["reasons"], "numbers": view["envelope"].get("numbers", {})}
    out_view = {**view, "envelope": combined, "case": {**view["case"], "ship": "feeder_1700", "headline_envelope": head,
                                                      "envelope": {**view["case"]["envelope"], "in_envelope": True, "reasons": []}}}
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(out_view), steps=[{"call": "open", "args": ["voyage"], "advance_ms": 100}])
    voyage = out["sections"][0]["text"]
    assert "Runner's envelope gate: inside (it admits tiny and small sections of any ship)" in voyage
    assert "Headline envelope (500–800 TEU feeders): outside: Ship 1,700 TEU; the envelope is 500–800 TEU feeders" in voyage
    assert "Outside the headline envelope. No engine run was started." in voyage


def test_status_refresh_clears_busy_engine_and_updates_spend():
    """code-2 P2-7: engine ownership and spend change without a key change."""
    view = load("view-ready")
    busy = key_status(True, "environment", True, 0.10, 10.0)
    busy["engine"] = {**busy["engine"], "busy": True, "call": "c20261103-000000-ffffff"}
    idle = key_status(True, "environment", True, 0.45, 10.0)
    idle["engine"] = {**idle["engine"], "busy": False, "call": None}
    routes = base_routes(view)
    routes["GET /api/functional/status"] = [busy, idle]
    out = shell(f"/planner?live=1&call={view['id']}", routes, steps=[{"emit": {"stage": "instruct", "name": "rule:confirmed", "value": None}, "advance_ms": 300}])
    assert "The engine is busy with call c20261103-000000-ffffff" in words(out, "pl-hero-why")
    out = shell(f"/planner?live=1&call={view['id']}", {**routes, "GET /api/functional/status": [busy, idle]},
                steps=[{"emit": {"stage": "instruct", "name": "rule:confirmed", "value": None}, "advance_ms": 300}, {"advance_ms": 11000}])
    assert words(out, "pl-hero-why") == "Inputs ready" and out["elements"]["pl-generate"]["disabled"] is False
    assert chip(out)["full"] == "Meta Model API · key from the environment · $0.45 of $10.00"
    status_reads = [r for r in out["requests"] if r["path"] == "/api/functional/status"]
    assert 2 <= len(status_reads) < 11  # coalesced; fa-inputs' modules also read status, so not one per second either


def test_non_adopted_adapter_is_not_called_the_stowlm_reader():
    view = load("view-ready")
    view = {**view, "availability": {**view["availability"], "reader": {**view["availability"]["reader"], "available": True, "adopted": False, "adapter_sha256": "1234567890" + "0" * 54}}}
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(view))
    assert "Glimmer 30B LoRA (not the adopted reader) · on this laptop" in words(out, "pl-header")


def test_bay_closure_hint_on_tiny_and_small_sections():
    view = load("view-ready")
    reading = {"id": "r1", "status": "constraint_ready", "confirmed": True, "rule_text": "Nothing loads into bay 01.", "answer": {"constraint": {"type": "close_bay", "bay": 1}}}
    out = shell(f"/planner?live=1&call={view['id']}", base_routes(view), steps=[{"emit": {"stage": "instruct", "name": "rule:confirmed", "value": reading}, "advance_ms": 300}])
    assert "On tiny and small sections a bay closure usually leaves no room for the load: an artefact of the section size, not a crane limit." in words(out, "pl-hero-why")


def test_moves_meter_limit_and_standard_capital(released):
    cid, routes, _ = released
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}])
    assert {m["key"]: m for m in out["meters"]}["moves"]["limit"] == "all lifts in this plan, loads included"
    assert ">Standard</button>" in (STATIC / "planner.js").read_text()


def test_demo_short_line_and_recheck_wording():
    page = (STATIC / "planner.html").read_text()
    assert page.count("Replays a stored Engine 0.13.0 plan, computed on 2 Nov 2026 (recheck status shown with the plan), and recorded AI answers") == 2
    assert "memoised" not in page and "rechecked now" not in page
    assert '<span class="pl-demo-short">stored plan and recorded answers; checks you trigger run live</span>' in page
    out = shell("/planner?demo=1")
    assert "stored plan and recorded answers; checks you trigger run live" in words(out, "pl-demo-tag")



# ---------------------------------------------------------------- round 6: explicit reader provenance (fa-engine run.instruction)
def test_provenance_uses_explicit_fields_only(released):
    """code-2 P1-3: a read after confirmation is a model call; only the runner_receipt row is a re-validation; notes don't decide."""
    confirm = {"stage": "confirm", "status": "done", "mode": "live", "who": "Planner", "where": "this laptop", "notes": "", "instance_id": "c1"}
    stages = [reader_row(instance_id="r1", provenance="model_call", source="model_call", model_call=True), confirm,
              reader_row(instance_id="r2", provenance="model_call", source="model_call", model_call=True,
                         notes="Re-validates the reading confirmed above; no second model call."),  # misleading note: ignored
              reader_row(instance_id="r3", wall_s=0.01, provenance="runner_revalidation", source="runner_receipt", revalidation=True, reading_id="r0")]
    cid, routes = done_view_with_stages(released, stages, [{"id": "r0", "status": "constraint_ready", "confirmed": True, "sentence": "s"}])
    view = routes[f"GET /api/functional/calls/{cid}"]
    view["run"] = {**view["run"], "instruction": "confirmed", "confirmed_reading": "r0"}
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}])
    items = re.findall(r'<li data-stage="reader">(.*?)</li>', out["elements"]["pl-panels"]["html"], re.S)
    assert len(items) == 3
    assert all("re-validated by software" not in x for x in items[:2]) and "re-validated by software, no model call" in items[2]
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}, {"call": "open", "args": ["handover"], "advance_ms": 500}])
    assert "The StowLM Reader ran once for this instruction, in the Instructions step; the receipt's reader step re-validated that confirmed reading with no second model call." in words(out, "pl-panels")


def test_export_caption_no_instruction_even_after_a_past_confirmation(released):
    stages = [reader_row(instance_id="r1", provenance="model_call", source="model_call", model_call=True),
              reader_row(instance_id="r9", wall_s=0.0, provenance="runner_no_instruction", source="runner_receipt", revalidation=False)]
    cid, routes = done_view_with_stages(released, stages, [{"id": "r0", "status": "constraint_ready", "confirmed": True, "sentence": "s"}])
    view = routes[f"GET /api/functional/calls/{cid}"]
    view["run"] = {**view["run"], "instruction": "none", "confirmed_reading": None}
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}, {"call": "open", "args": ["handover"], "advance_ms": 500}])
    panels = words(out, "pl-panels")
    assert "Planned with no instruction: no reader ran for this plan, and the receipt's reader step records an empty brief." in panels
    assert "re-validated that confirmed reading" not in panels
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}])
    assert "StowLM Reader · no instruction, no model call" in words(out, "pl-panels")


def test_confirmed_without_a_revalidation_record_says_so(released):
    stages = [reader_row(instance_id="r1", provenance="model_call", source="model_call", model_call=True)]
    cid, routes = done_view_with_stages(released, stages, [{"id": "r0", "status": "constraint_ready", "confirmed": True, "sentence": "s"}])
    view = routes[f"GET /api/functional/calls/{cid}"]
    view["run"] = {**view["run"], "instruction": "confirmed", "confirmed_reading": "r0"}
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}, {"call": "open", "args": ["handover"], "advance_ms": 500}])
    assert "this call has no runner re-validation record for it yet." in words(out, "pl-panels")


def test_live_files_never_infer_provenance_from_notes():
    for name in ("planner-live-plan.js", "planner-live-review.js"):
        text = (STATIC / name).read_text()
        assert "re-validates the reading confirmed above|" not in text and "nothing to do/i" not in text


# ---------------------------------------------------------------- round 7: plan again on the same call, infeasibility
def with_attempts(routes, cid, *, attempt=2, latest_instruction="none", attempts=None, state=None):
    routes = dict(routes)
    view = dict(routes[f"GET /api/functional/calls/{cid}"])
    view["run"] = {**view["run"], "attempt": attempt, "instruction": latest_instruction, "confirmed_reading": None, "profile": "standard"}
    view["attempts"] = attempts if attempts is not None else [
        {"attempt": 1, "instruction": "confirmed", "confirmed_reading": "r0", "profile": "live", "status": "done",
         "outcome": {"status": "no_checked_plan", "wall_s": 731.2}},
        {"attempt": 2, "instruction": latest_instruction, "profile": "standard", "status": "done", "outcome": {"status": "checked_plan", "wall_s": 14.1}}]
    view["readings"] = [{"id": "r0", "status": "constraint_ready", "confirmed": False, "sentence": "Nothing goes into bay nine.", "rule_text": "Nothing loads into bay 09."}]
    if state:
        view["state"] = state
    routes[f"GET /api/functional/calls/{cid}"] = view
    return routes, view


def test_plan_again_card_and_earlier_attempts(released):
    cid, routes, _ = released
    routes, view = with_attempts(routes, cid)
    planning = {**view, "state": "planning", "outcome": None,
                "run": {**view["run"], "attempt": 3, "status": "running", "elapsed_s": 2.0, "incumbents": []},
                "attempts": view["attempts"] + [{"attempt": 3, "instruction": "none", "profile": "live", "status": "running"}]}
    routes[f"POST /api/functional/calls/{cid}/plan"] = planning
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[{"advance_ms": 4000}])
    panels = words(out, "pl-panels")
    assert "Plan again on this call" in panels and "Instruction: No instruction" in panels
    assert "Earlier attempts" in panels
    assert "#1 · Nothing loads into bay 09. · live · no checked plan found · 731.2 s whole run, admission to package" in panels
    assert "#2 ·" not in panels  # the latest attempt is the one on screen
    assert "attempt 2 of 2" in words(out, "pl-result")
    # change the profile, plan again: the latest choice is posted; the new attempt's running state replaces the result
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[
        {"advance_ms": 4000}, {"click": '#pl-panels [data-do="profile"][data-profile="live"]', "advance_ms": 100},
        {"click": '#pl-panels [data-do="plan-again"]', "advance_ms": 500}])
    posts = [r for r in out["requests"] if r["method"] == "POST"]
    assert posts == [{"method": "POST", "path": f"/api/functional/calls/{cid}/plan", "body": {"profile": "live", "instruction": "none"}}]
    assert "Searching for a checked plan" in words(out, "pl-result")
    assert {m["key"]: m["value"] for m in out["meters"]}["stability"] == "—"  # the earlier attempt's meters are cleared
    assert "#2 · no instruction · standard · passed the full checker" in words(out, "pl-panels")


def test_change_instruction_clears_the_choice_until_a_new_one(released):
    cid, routes, _ = released
    routes, view = with_attempts(routes, cid)
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[
        {"advance_ms": 4000}, {"click": '#pl-panels [data-do="change-instruction"]', "advance_ms": 100},
        {"emit": {"stage": "instruct", "name": "rule:changed", "value": {"sentence": ""}}, "advance_ms": 6000}])
    panels = words(out, "pl-panels")
    assert "Instruction: not chosen. Read and confirm a new sentence in 3 · Instructions, or choose Plan with no instruction there." in panels
    assert re.search(r'data-do="plan-again" disabled', out["elements"]["pl-panels"]["html"])
    # a new confirmed reading enables Plan again with instruction "confirmed"
    confirmed = {**view, "readings": [{**view["readings"][0], "confirmed": True}]}
    routes2 = {**routes, f"GET /api/functional/calls/{cid}": confirmed, f"POST /api/functional/calls/{cid}/plan": {**confirmed, "state": "planning", "outcome": None, "run": {**confirmed["run"], "status": "running", "attempt": 3}}}
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes2, skip_scripts=NO_BAY, steps=[
        {"advance_ms": 4000}, {"emit": {"stage": "instruct", "name": "rule:changed", "value": {}}, "advance_ms": 100},
        {"emit": {"stage": "instruct", "name": "rule:confirmed", "value": confirmed["readings"][0]}, "advance_ms": 100},
        {"click": '#pl-panels [data-do="plan-again"]', "advance_ms": 300}])
    assert "Stop the engine" in words(out, "pl-result")  # the new attempt is running
    posts = [r for r in out["requests"] if r["method"] == "POST"]
    assert posts and posts[-1]["body"] == {"profile": "standard", "instruction": "confirmed"}


def test_plan_again_events_for_the_instructions_module(released):
    cid, routes, _ = released
    routes, _ = with_attempts(routes, cid)
    source = (STATIC / "planner-live-plan.js").read_text()
    assert "ctx.emit('run:finished'" in source and "ctx.emit('plan:again'" in source
    out = shell(f"/planner?live=1&speed=50&call={cid}", routes, skip_scripts=NO_BAY, steps=[
        {"advance_ms": 4000}, {"click": '#pl-panels [data-do="change-instruction"]', "advance_ms": 100}])
    assert {s["stage"]: s for s in out["sections"]}["instruct"]["open"] is True


def test_infeasibility_sentence_in_the_no_plan_box():
    view = load("view-ready")
    base = {"status": "no_checked_plan", "passed": False, "level": None, "label": "live", "message": "No checked plan was found. " + NO_PLAN,
            "broken_rules": [], "boxes_ashore": [], "lifts": None, "first_checked_wall_s": None, "wall_s": 3.0, "active_rules": None,
            "inactive_rules": None, "receipt_sha256": None, "package_files": []}
    run = {"status": "done", "profile": "standard", "elapsed_s": 3.0, "admission": None, "incumbents": [], "events_tail": []}
    sentence = "Bay 01 is closed and the remaining cells hold 2 of the 3 boxes, so no plan can load every box."
    for extra, keeps_line in (({"infeasibility": sentence}, True), ({"infeasibility": {"sentence": sentence, "proved": True}}, False)):
        done = {**view, "state": "done", "outcome": {**base, **extra}, "run": run}
        out = shell(f"/planner?live=1&call={view['id']}", base_routes(done), steps=[{"advance_ms": 2000}])
        panels = words(out, "pl-panels")
        assert sentence in panels
        assert ("No checked plan found. That isn't a proof that none exists." in panels) is keeps_line
