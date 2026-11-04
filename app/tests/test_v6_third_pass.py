"""Demo v6, third pass: the freeze guard and its build label (D270), the x-ray trace order (logic review 5, Z4), the copilot's
delivered real-tools take, the static replay page of the pinned drags (F12) and the x-ray's block count (F3)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app import v6, v6_build

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
VERIFY = "experiments.mvp.pipeline.freeze.verify"
SHA = "0123456789abcdef" * 4
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


class FakeDrift(RuntimeError):
    def __init__(self, drifts):
        self.drifts = drifts
        super().__init__("drift")


def fake_verify(outcome):
    module = types.ModuleType(VERIFY)
    module.FreezeDrift = FakeDrift
    calls = []

    def require_frozen(path, *, root=None, full_weights=False):
        calls.append((str(path), full_weights))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    module.require_frozen, module.calls = require_frozen, calls
    return module


@pytest.fixture
def clean_env(monkeypatch):
    for key in (v6_build.SHA_ENV, v6_build.TREE_ENV, getattr(v6_build, "FILM_ENV", "STOWLM_FILM_SHA256"), v6_build.MANIFEST_ENV):
        monkeypatch.delenv(key, raising=False)
    yield
    for key in (v6_build.SHA_ENV, v6_build.TREE_ENV, getattr(v6_build, "FILM_ENV", "STOWLM_FILM_SHA256")):
        os.environ.pop(key, None)


# ---------------------------------------------------------------- 1. the freeze guard and its three states
def test_no_manifest_is_a_development_build(clean_env):
    build = v6_build.current()
    assert build == {"frozen": False, "pipeline_sha256": None, "tree_digest": None, "short": None, "film_sha256": None, "film_short": None,
                     "label": "Development build, not frozen", "receipt_line": "Development build, not frozen"}
    assert v6_build.stamp({"a": 1}) == {"a": 1, "pipeline_sha256": None, "film_manifest_sha256": None, "build": "Development build, not frozen"}


def manifest_file(tmp_path, body: dict, name="manifest.json"):
    """A manifest on disk and the digest of its bytes, which is what require_frozen returns for it."""
    path = tmp_path / name
    path.write_bytes(json.dumps(body).encode())
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_verified_manifest_stamps_its_sha_and_shows_twelve_characters(clean_env, monkeypatch, tmp_path):
    path, sha = manifest_file(tmp_path, {"scope": "pipeline", "tree_sha256": "t" * 64})
    module = fake_verify({"manifest_sha256": sha, "tree_digest": "t" * 64})
    monkeypatch.setitem(sys.modules, VERIFY, module)
    build = v6_build.require(path)
    assert module.calls == [(path, False)]  # never the full weights at launch
    assert build["frozen"] is True and build["pipeline_sha256"] == sha and build["short"] == sha[:12] and build["film_sha256"] is None
    assert build["label"] == f"Frozen pipeline {sha[:12]}" and build["receipt_line"] == f"pipeline {sha[:12]}"
    assert v6_build.stamp({"a": 1})["pipeline_sha256"] == sha
    assert os.environ[v6_build.SHA_ENV] == sha  # what a worker process inherits


# code10 (v6_build.py:48): a film manifest's own digest is the film layer's; receipts carry the pipeline layer's (D275)
PIPELINE = ROOT / "experiments/mvp/pipeline/freeze/manifest-pipeline.json"


def film_body(pipeline_sha):
    return {"scope": "film", "tree_sha256": "f" * 64, "pipeline_layer": {
        "path": "experiments/mvp/pipeline/freeze/manifest-pipeline.json", "manifest_sha256": pipeline_sha, "tree_digest": "p" * 64}}


def test_a_film_manifest_stamps_the_pipeline_layer_and_names_the_film_beside_it(clean_env, monkeypatch, tmp_path):
    pipeline = hashlib.sha256(PIPELINE.read_bytes()).hexdigest()  # the pinned pipeline layer, from data
    path, film = manifest_file(tmp_path, film_body(pipeline), "manifest-film.json")
    monkeypatch.setitem(sys.modules, VERIFY, fake_verify({"manifest_sha256": film, "tree_digest": "f" * 64}))
    build = v6_build.require(path)
    assert build["pipeline_sha256"] == pipeline and build["tree_digest"] == "p" * 64 and build["short"] == pipeline[:12]
    assert build["film_sha256"] == film and build["film_short"] == film[:12]
    assert build["label"] == f"Frozen pipeline {pipeline[:12]}"
    assert build["receipt_line"] == f"pipeline {pipeline[:12]} · film manifest {film[:12]}"
    stamped = v6_build.stamp({"a": 1})
    assert stamped["pipeline_sha256"] == pipeline and stamped["film_manifest_sha256"] == film
    assert os.environ[v6_build.SHA_ENV] == pipeline and os.environ[v6_build.FILM_ENV] == film


def test_the_receipt_card_shows_the_pipeline_layer_the_package_names(clean_env, monkeypatch, tmp_path):
    from app import v6_package
    package = json.loads((v6_package.package_dir() / "receipt.json").read_text())["pipeline_sha256"]  # acf2f275… today (v4, D313)
    path, film = manifest_file(tmp_path, film_body(package), "manifest-film.json")
    monkeypatch.setitem(sys.modules, VERIFY, fake_verify({"manifest_sha256": film, "tree_digest": "f" * 64}))
    v6_build.require(path)
    monkeypatch.setattr(v6_package, "require_verified", lambda directory: {"package_sha256": "p" * 64, "checked_on": "2026-11-02"})
    monkeypatch.setattr(v6_package, "require_hero_plan", lambda directory: None)
    receipt = v6_package.view()["receipt"]
    assert receipt["pipeline_sha256"] == package and receipt["film_manifest_sha256"] == film
    assert receipt["build_line"].startswith(f"pipeline {package[:12]}") and f"film manifest {film[:12]}" in receipt["build_line"]
    assert receipt["reader"]["pipeline"] == package[:12]  # the app's pipeline and the package's agree


@pytest.mark.parametrize("change", ["bytes", "no_layer", "bad_layer"])
def test_a_film_manifest_without_a_clean_pipeline_layer_refuses(clean_env, monkeypatch, tmp_path, change):
    body = film_body("a" * 64)
    if change == "no_layer":
        body.pop("pipeline_layer")
    if change == "bad_layer":
        body["pipeline_layer"]["manifest_sha256"] = "short"
    path, film = manifest_file(tmp_path, body)
    returned = "0" * 64 if change == "bytes" else film  # the bytes read back aren't the bytes verified
    monkeypatch.setitem(sys.modules, VERIFY, fake_verify({"manifest_sha256": returned, "tree_digest": "f" * 64}))
    with pytest.raises(v6_build.Refused) as refused:
        v6_build.require(path)
    assert str(refused.value.code).startswith("Refusing to start") and v6_build.current()["frozen"] is False


def test_drift_refuses_with_every_drift_listed(clean_env, monkeypatch):
    drifts = [{"path": "src/stowage/checker.py", "reason": "changed"}, {"path": "@manifest.status", "reason": "not_frozen", "detail": "dry"}]
    monkeypatch.setitem(sys.modules, VERIFY, fake_verify(FakeDrift(drifts)))
    with pytest.raises(v6_build.Refused) as refused:
        v6_build.require("m.json")
    text = str(refused.value.code)
    assert text.startswith("Refusing to start") and "2 drifts" in text
    assert "src/stowage/checker.py: changed" in text and "@manifest.status: not_frozen (dry)" in text
    assert v6_build.current()["frozen"] is False


def test_a_verifier_that_cannot_load_refuses_plainly(clean_env, monkeypatch):
    monkeypatch.setitem(sys.modules, VERIFY, None)  # import raises ImportError
    with pytest.raises(v6_build.Refused) as refused:
        v6_build.require("m.json")
    assert "could not be loaded" in str(refused.value.code)
    broken = types.ModuleType(VERIFY)  # loads, but has no FreezeDrift: still no freeze
    monkeypatch.setitem(sys.modules, VERIFY, broken)
    with pytest.raises(v6_build.Refused):
        v6_build.require("m.json")


def test_a_verifier_with_no_sha_refuses(clean_env, monkeypatch):
    monkeypatch.setitem(sys.modules, VERIFY, fake_verify({"tree_digest": "x"}))
    with pytest.raises(v6_build.Refused):
        v6_build.require("m.json")


LAUNCH = """
import sys, types
mod = types.ModuleType({verify!r})
class FreezeDrift(RuntimeError):
    def __init__(self, drifts):
        self.drifts = drifts
        super().__init__('drift')
def require_frozen(path, *, root=None, full_weights=False):
    raise FreezeDrift([{{'path': 'app/v6.py', 'reason': 'changed'}}])
mod.FreezeDrift, mod.require_frozen = FreezeDrift, require_frozen
{inject}
sys.argv = ['app'] + {argv!r}
from app.__main__ import main
main()
"""


def launch(argv, inject=True, env=None):
    code = LAUNCH.format(verify=VERIFY, argv=argv, inject=f"sys.modules[{VERIFY!r}] = mod" if inject else f"sys.modules[{VERIFY!r}] = None")
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in (".", "src")), **(env or {})}
    environment.pop(v6_build.MANIFEST_ENV, None) if not env or v6_build.MANIFEST_ENV not in env else None
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("argv,env", [(["--v6", "--manifest", "m.json"], None), (["--v6"], {"PIPELINE_MANIFEST": "m.json"})])
def test_the_launcher_refuses_on_drift_before_loading_anything(argv, env):
    run = launch(argv, env=env)
    assert run.returncode != 0 and "Traceback" not in run.stderr
    assert "Refusing to start" in run.stderr and "app/v6.py: changed" in run.stderr
    assert "StowLM demo:" not in run.stdout


def test_the_launcher_refuses_when_the_verifier_is_missing():
    run = launch(["--v6", "--manifest", "m.json"], inject=False)
    assert run.returncode != 0 and "Traceback" not in run.stderr and "could not be loaded" in run.stderr


def test_state_and_receipts_carry_the_build(clean_env, monkeypatch):
    assert '"build": v6_build.current()' in (ROOT / "app/v6.py").read_text()  # /api/v6/state carries it
    monkeypatch.setenv(v6_build.SHA_ENV, SHA)
    from app import v6_package
    monkeypatch.setattr(v6_package, "require_verified", lambda directory: {"package_sha256": "p" * 64, "checked_on": "2026-11-02"})  # the gate is its own test's
    monkeypatch.setattr(v6_package, "require_hero_plan", lambda directory: None)  # the plan match is test_v6_hero_rule.py's
    receipt = v6_package.view()["receipt"]
    assert receipt["pipeline_sha256"] == SHA and receipt["build_line"] == f"pipeline {SHA[:12]}"
    monkeypatch.delenv(v6_build.SHA_ENV)
    receipt = v6_package.view()["receipt"]
    assert receipt["pipeline_sha256"] is None and receipt["build_line"] == "Development build, not frozen"


def test_the_live_replan_receipts_are_stamped(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv(v6_build.SHA_ENV, SHA)
    job = {"out": tmp_path, "started": 0.0}
    monkeypatch.setattr(v6.LiveReplan, "_land", staticmethod(lambda job, code, service: {"status": "ok", "passed": False}))

    class Done:
        pid = 0
        def wait(self, timeout):
            return 0
    v6.LIVE._watch(job, Done(), None)
    assert job["status"] == "done" and job["result"]["pipeline_sha256"] == SHA
    assert json.loads((tmp_path / "app-receipt.json").read_text())["pipeline_sha256"] == SHA
    source = (ROOT / "app/v6.py").read_text()
    assert '(out / "confirmation.json").write_text(json.dumps(v6_build.stamp(confirmation)))' in source


def test_the_pages_read_the_label_from_data():
    shell, package = (STATIC / "stage-v6.js").read_text(), (STATIC / "stage-v6package.js").read_text()
    assert "S.state.build" in shell and "b.label" in shell and "Development build" not in shell  # the words are the server's
    assert "r.build_line" in package and "Development build" not in package


# ---------------------------------------------------------------- 2. Z4: the x-ray never plays adapter-v2's trace
def test_the_superseded_trace_is_out_of_the_search_order(monkeypatch):
    assert "real-block-trace-adopted.json" not in v6._RECORDED_TRACES and v6._RECORDED_TRACES[0] == "real-block-trace.json"
    monkeypatch.setattr(v6, "_settings", lambda: {"xray_trace": "experiments/mvp/pipeline/xray/fixtures/real-block-trace-adopted.json"})
    assert v6._trace_path().name == "real-block-trace.json"  # named or not, the v2 trace is skipped
    monkeypatch.setattr(v6, "_settings", lambda: {"xray_trace": None})
    assert v6._trace_path().name == "real-block-trace.json"


def test_the_film_plays_the_adopted_readers_trace():
    data = v6.xray()
    adopted = v6._adoption()["adopted"]["adapter_sha256"]
    assert v6._trace_path().name == "real-block-trace.json"
    assert data["trace"]["stage_record"]["adapter_sha256"] == adopted and data["adopted"] is True and data["film"] is True


# ---------------------------------------------------------------- 3c. F3: an end-token swap is never a block
def test_the_stop_steps_swap_is_not_counted():
    row = lambda blocked: [{"token": "x", "blocked": blocked, "reason": "r", "probability": 0.9}]
    trace = {"source": {"label": "Recorded answer", "kind": "recorded"}, "steps": [{"top_k": row(False)}, {"top_k": row(True)}, {"top_k": row(True), "is_stop": True}]}
    facts = v6._trace_facts(trace)
    assert facts["blocked"] == 1 and facts["tokens"] == 3
    real = v6.xray()
    stops = [s for s in real["trace"]["steps"] if s.get("is_stop") and s["top_k"] and s["top_k"][0].get("blocked")]
    assert real["blocked"] == sum(1 for s in real["trace"]["steps"] if s["top_k"] and s["top_k"][0].get("blocked")) - len(stops)


# ---------------------------------------------------------------- 3a. the copilot plays the delivered real-tools take
@pytest.fixture(scope="module")
def copilot():
    return v6.copilot()


def take_files():
    return (json.loads((v6.DELIVERED / "shot10.json").read_text()), json.loads((v6.DELIVERED / "shot10-facts.json").read_text()))


def test_the_delivered_take_is_the_recordings_own(copilot):
    take, facts = take_files()
    k = copilot["delivered"]
    assert k["real_tools"] is True and k["label"] == take["label"] and k["reader_label"] == take["reader"]
    through = next(i for i, row in enumerate(take["turns"]) if not row["gate"]["reasons"])
    assert k["scripted_replies"] == through and k["scripted_replies_said"] == v6._spoken_count(through)  # clarifications sent after withheld prose, up to the first prose let through
    comparison = take["turns"][0]["turn"]["tool_calls"][0]["result"]["alternative_comparisons"][0]
    assert k["lift_gap"] == comparison["right_minus_left_lifts"] and k["comparison_label"] == comparison["label"]
    lifts = [c["lifts"] for c in k["cards"]]
    assert lifts == sorted(lifts) and {c["id"] for c in k["cards"]} == {a["id"] for a in facts["alternatives"]}
    chosen = next(c for c in k["cards"] if c["chosen"])
    assert next(a for a in facts["alternatives"] if a["id"] == chosen["id"])["plan_sha256"] == facts["selected_plan_sha256"]
    assert k["plan"]["checker"]["passed"] is True and k["plan"]["label"].startswith("Stored plan, computed on ") and "Checked on" in k["plan"]["label"]
    assert len(k["turns"]) == len(take["turns"]) and all(t["raw"] for t in k["turns"])
    assert [t["through"] for t in k["turns"]] == [not row["gate"]["reasons"] for row in take["turns"]]
    assert k["recommendation"] in take["turns"][through]["turn"]["say"]


def test_the_doubles_offer_is_retired(copilot):
    main = copilot["main"]
    assert main["real_tools"] is False and main["offer"] is None and copilot["offer_plan"] is None
    assert main["arithmetic"] and not any(row["place"] == "offer" for row in main["sentences"])
    assert "/Users/" not in json.dumps(copilot["delivered"])


def test_a_take_that_never_landed_is_no_take(monkeypatch, tmp_path):
    take, facts = take_files()
    for row in take["turns"]:
        row["gate"]["reasons"] = ["unsupported_number"]
    (tmp_path / "shot10.json").write_text(json.dumps(take))
    (tmp_path / "shot10-facts.json").write_text(json.dumps(facts))
    monkeypatch.setattr(v6, "DELIVERED", tmp_path)
    assert v6.copilot()["delivered"] is None
    take, facts = take_files()
    facts["observed_acceptance"] = []
    (tmp_path / "shot10.json").write_text(json.dumps(take))
    (tmp_path / "shot10-facts.json").write_text(json.dumps(facts))
    assert v6.copilot()["delivered"] is None


def js(expr: str, data):
    code = (f"global.window = globalThis; require({json.dumps(str(STATIC / 'stage-v6copilot.js'))}); const C = window.StageV6Copilot;"
            f"const D = {json.dumps(data)}; console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


@needs_node
def test_the_take_plays_in_the_second_beat_and_is_pure(copilot):
    k = copilot["delivered"]
    frames = js("[0, 5, 7.5, 9.3, 12, 21, 9.3].map((t) => C.takeAt(t, D))", k)
    assert frames[0]["on"] == 0 and frames[1]["on"] == 0 and frames[-2]["on"] == 1 and frames[3] == frames[-1]
    end = frames[-2]
    assert end["picked"] and end["tick"] == 1 and end["confirmed"] == 1 and end["lit"] == len(k["steps"]) and all(r == 1 for r in end["rows"])
    assert js("[C.HANDOVER, C.LENGTH]", {}) == [7.5, 21]
    page = js("C.takeHtml(D, (x) => String(x))", k)
    assert all(card["sentence"] in page for card in k["cards"]) and k["plan"]["label"] in page and "Spark wrote" in page


@needs_node
def test_with_no_plan_there_is_no_tick(copilot):
    k = {**copilot["delivered"], "plan": None}
    assert js("C.takeAt(21, D)", k)["tick"] == 0


@needs_node
def test_the_no_still_fills_the_first_beat(copilot):
    end = js("C.stateAt(7.4, D, false)", copilot["main"])
    assert end["no"] == 1 and end["offer"] == 0 and end["picked"] is False


# ---------------------------------------------------------------- 3b. F12: the pinned drags as a static page
def _replay_build():
    """experiments/mvp/ui/replay/build.py, loaded by path under its own name: static_play/build.py is also "build" in one session."""
    if "replay_build" not in sys.modules:
        spec = importlib.util.spec_from_file_location("replay_build", ROOT / "experiments/mvp/ui/replay/build.py")
        sys.modules["replay_build"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules["replay_build"])
    return sys.modules["replay_build"]


def test_the_replay_page_is_built_from_the_pinned_cache():
    build = _replay_build()
    cache, digest = build.load()
    page = build.page(cache, digest, build._demo_source())
    assert page.count("<details") == cache["entries_count"] and "Recorded verdict" in page and f"sha256 {digest[:16]}" in page
    assert "<script" not in page and "http" not in page.split("<body>")[1]
    for word in ("instant", "real-time", "approved", "certified", "industry", "production", "rechecked now"):
        assert word not in page.lower()
    with pytest.raises(SystemExit):
        build.load(pin="0" * 64)
    shipped = (ROOT / "experiments/mvp/ui/replay/index.html").read_text()
    assert f"sha256 {digest[:16]}" in shipped and shipped.count("<details") == cache["entries_count"]
    assert shipped == page and (ROOT / "experiments/mvp/video/site/replay/index.html").read_text() == page  # rebuilt, and the site's copy is this one


def test_the_replay_page_names_its_plan_and_says_why_the_closed_bay_reads_fine():
    """S18 and M8: extra lifts, lashing as our approximation, the stored plan the drags edit, and the closed-bay row explained; never the fixture's old label."""
    build = _replay_build()
    cache, digest = build.load()
    demo = build._demo_source()
    page = build.page(cache, digest, demo)
    base = json.loads((ROOT / demo["derived_from"]).read_text())
    assert f"Stored plan, computed on {base['computed_utc'][:10]}" in page and base["canonical_rules"] == []
    assert "Crane lifts" not in page and "Extra lifts this call" in page
    assert "Stow breaks rule: Lashing</strong>" not in page and "Lashing (our approximation, synthetic gear)" in page
    assert "limits tightened" not in page and "no vessel or rule limit tightened" in page and demo["label_disclosure"] in page
    bay = v6.hero()["instruction"]["rule"]["constraint"]["bay"]
    rows = [r for r in build.drags(cache) if int(str(r["slot"])[:3]) == bay]
    assert rows and page.count('<p class="why">') == len(rows)
    for r in rows:
        assert build.no_rule_note(r, build.base_plan(demo), {bay}).startswith(f"Why bay {bay} isn't refused here")


def test_the_replay_page_lists_its_cargo_change_and_names_its_build(tmp_path):
    """logic14: "as listed" lists the change in the sandbox source's own words, and the footer says which build the page is."""
    build = _replay_build()
    cache, digest = build.load()
    demo = build._demo_source()
    page, listed = build.page(cache, digest, demo), build.changes(demo)
    assert listed and all(line in page for line in listed) and len(listed) == len(demo["modifications"])
    manifest = ROOT / "experiments/mvp/pipeline/freeze/manifest-pipeline.json"
    line = build.build_line(digest)
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()[:12] in line and line in page and "Development build" not in page
    assert build.build_line(digest, tmp_path / "gone.json") == v6_build.DEVELOPMENT  # no manifest pins it: development, plainly


def test_the_sandbox_names_the_page_only_when_it_matches_the_pin(monkeypatch, tmp_path):
    page = v6.replay_page()
    index = json.loads((v6.SANDBOX / "drag-cache.index.json").read_text())
    assert page == {"drags": index["entries_count"], "page": "/api/v6/asset/replay.html", "label": "Recorded verdict"}
    other = tmp_path / "index.html"
    other.write_text("<html>sha256 0000000000000000</html>")
    monkeypatch.setitem(v6.ASSETS, "replay.html", (other, "text/html; charset=utf-8"))
    assert v6.replay_page() is None


def test_readme_starts_the_v6_demo():
    text = (ROOT / "app/README.md").read_text()
    assert "uv run python -m app --v6" in text.split("```")[1]
