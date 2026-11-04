"""'Play it yourself' (build.py): the copy is the app's own planner page on one recorded run, reaches nothing outside its folder,
answers only the recorded track's actions, ships no audio, key or local path, and stays small.

    uv run pytest experiments/mvp/ui/static_play/test_static_play.py -q

Reads the built copy in experiments/mvp/video/site/play/ (build.py emit writes it); skips if it isn't built. The shim tests run
play.js under node against the real recordings.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PLAY = ROOT / "experiments/mvp/video/site/play"
STATIC = ROOT / "app/static"
sys.path.insert(0, str(HERE))
if "static_play_build" not in sys.modules:  # by path, under its own name: other folders have a build.py too
    _spec = importlib.util.spec_from_file_location("static_play_build", HERE / "build.py")
    sys.modules["static_play_build"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["static_play_build"])
build = sys.modules["static_play_build"]

pytestmark = pytest.mark.skipif(not (PLAY / "rec/index.json").is_file(), reason="playback build not made (run build.py emit)")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def index() -> dict:
    return json.loads((PLAY / "rec/index.json").read_text())


def body(ref: str) -> str:
    return gzip.decompress((PLAY / "rec" / ref).read_bytes()).decode()


def last(index: dict, key: str):
    return json.loads(body(index["entries"][key][-1][3]))


# ---------------------------------------------------------------- the app's own page
def test_every_asset_the_planner_loads_is_the_apps_own_file():
    names = build.page_assets()
    assert "planner.js" in names and "planner-live-plan.js" in names and "studio-pacing.js" in names
    for name in names:
        built = (PLAY / name).read_text()
        assert built == build.rewrite_static(name, (STATIC / name).read_text()), f"{name} differs from app/static beyond the rewrite"


def test_index_is_planner_html_with_the_shim_first_and_the_recorded_run_named():
    html = (PLAY / "index.html").read_text()
    assert not re.search(r'(src|href)="/(?!/)', html)
    assert html.index('src="play.js"') < html.index('src="planner.js"') and html.index('href="play.css"') < html.index('href="planner.css"')
    assert 'id="play-banner"' in html and "Recorded run of the Functional app" in html and "Synthetic ships and cargo, simulated port" in html
    assert f'href="{build.INSTALL_URL}"' in html and 'class="pl-demo-link"' in html
    assert "fresh seed" in html and "unseen" not in html.lower()


def test_relabels_say_recording_and_live_claims_are_gone():
    for name, old, new, n in build.WORDING:
        text = (PLAY / ("index.html" if name == "planner.html" else name)).read_text()
        assert new in text and old not in text, (name, old[:50])
    every = "".join(p.read_text() for p in PLAY.glob("*.js")) + (PLAY / "index.html").read_text()
    # what the live page shows (internal ctx.log 'where' fields and the demo app's modules aren't shown in this copy)
    for claim in ("Runs the pipeline live on fresh synthetic inputs; nothing is replayed", "Open the Demo app", "'StowLM Reader · on this laptop'",
                  "measured on this machine", "Measured on this machine", "(LoRA) on this laptop, schema-masked"):
        assert claim not in every, claim


def test_no_absolute_or_external_urls_in_code():
    for p in list(PLAY.glob("*.js")) + list(PLAY.glob("*.css")) + [PLAY / "index.html"]:
        text = p.read_text()
        assert p.name == "play.js" or not re.search(r"""['"`]/api/""", text), p.name
        urls = [u for u in re.findall(r"https?://[^\s'\"`)<>]+", text) if not u.startswith("http://www.w3.org/")]
        assert all(u == build.INSTALL_URL for u in urls), (p.name, urls[:5])
        assert "@import" not in text and "fonts.googleapis" not in text


def test_shim_reads_only_its_own_folder():
    shim = (PLAY / "play.js").read_text()
    assert "http://" not in shim and "https://" not in shim and "XMLHttpRequest" not in shim and "sendBeacon" not in shim
    calls = re.findall(r"\bown\(([^;]*?)\)", shim)
    assert calls and all("ROOT" in c or "url.href" in c for c in calls), calls
    assert "url.origin !== location.origin || !url.pathname.startsWith(ROOT.pathname)" in shim


# ---------------------------------------------------------------- the recorded track
def test_the_recorded_actions_are_the_track(index):
    anchors = index["anchors"]
    order = " ".join(a["what"] for a in anchors)
    assert re.fullmatch(r"create papers( papers| answers)*" + ("" if index["track"]["dry"] else " audio") + "( read)+ confirm plan", order), order
    reads = [a for a in anchors if a["what"] == "read"]
    assert all(a["body"]["origin"] == "planner_edit" for a in reads[1:]), "a second reading is the planner's correction"
    if index["track"]["on_questions"] == "use-generated-load-list":  # the planner set Spark's reading aside, with the button's own body
        assert [a["body"] for a in anchors if a["what"] == "papers"][-1] == {"use_generated_load_list": True}
    for a in anchors[2:]:
        if a["what"] == "papers":  # a retry of the read (the retry button's body) or the generated list; nothing else
            assert a["body"] in ({"use_generated_papers": True}, {"use_generated_load_list": True}), a["body"]
    assert anchors[0]["body"] == {**index["track"]["form"]}
    assert anchors[-1]["body"] == {"profile": index["track"]["profile"], "instruction": "confirmed"}
    assert all(a["t"] < b["t"] for a, b in zip(anchors, anchors[1:]))
    assert all(1 <= a["speed"] <= build.MAX_SPEED for a in anchors)
    if not index["track"]["dry"]:
        assert anchors[1]["body"] == {"use_generated_papers": True}
        audio = next(a for a in anchors if a["what"] == "audio")["body"]
        assert "data_b64" not in audio and audio["name"] == index["track"]["audio_name"] and audio["generated_voice"] is True
        assert next(a for a in anchors if a["what"] == "read")["body"]["origin"] == "voice"  # the transcript as heard, first


def test_the_recorded_run_ends_on_a_checked_plan_that_obeys_the_instruction(index):
    view = last(index, f"GET /api/functional/calls/{index['call_id']}")
    out, run = view["outcome"], view["run"]
    assert view["state"] == "done" and out["status"] == "checked_plan" and out["passed"] is True and out["label"] == "Passes the full checker"
    assert run["instruction"] == "confirmed" and run["confirmed_reading"]
    reading = next(r for r in view["readings"] if r["confirmed"])
    assert reading["status"] == "constraint_ready" and reading["answer"]["constraint"]["type"] == "close_bay"
    assert view["case"]["size_class"] == index["track"]["form"]["size_class"] and view["case"]["seed"] == index["track"]["form"]["seed"]
    assert view["case"]["seed_status"]["label"].startswith("Fresh seed")
    assert view["envelope"]["in_envelope"] is True and view["case"]["boxes"] <= 195
    if not index["track"]["dry"]:
        assert view["voice"]["generated_voice"] is True and view["voice"]["transcript"]
        spark = [r for r in view["stages"] if r.get("stage") == "intake" and (r.get("cost_usd") or 0) > 0]
        assert spark, "Muse Spark read the papers (its measured, charged booking reads)"
    if not index["track"]["dry"] and index["track"]["on_questions"] is None:  # Spark's own reading is the load list planned
        intake = view["papers"]["intake"]
        assert view["load_list"]["origin"] == "intake" and intake.get("complete") is True and not intake.get("skipped")
        assert intake.get("questions") == [] and all(b["status"] == "done" for b in view["papers"]["bookings"])
        assert len(view["papers"]["bookings"]) <= 6 and view["load_list"]["boxes"] == view["case"]["boxes"]


def test_the_flow_routes_have_recordings(index):
    cid = index["call_id"]
    for name in ("status", "options", f"calls/{cid}", *(f"calls/{cid}/planner/{n}" for n in ("ship", "boxes", "plan-before", "plan-after", "moves", "meters", "checks", "package"))):
        assert f"GET /api/functional/{name}" in index["entries"], name
    pkg = last(index, f"GET /api/functional/calls/{cid}/planner/package")
    assert pkg["built"] is True and len(pkg["files"]) == 5
    for f in pkg["files"] + [{"url": pkg["outcome_url"]}]:
        assert f["url"].startswith("api/functional/") and "/" + f["url"] in index["files"]
        assert (PLAY / f["url"]).is_file() or (PLAY / f["url"]).with_suffix(".html").is_file(), f["url"]


def test_linked_files_ship_hardened():
    pages = [p for p in (PLAY / "api").rglob("*.html")]
    assert pages, "the package's bay plan page"
    for p in pages:
        html = p.read_text()
        assert html.count("Content-Security-Policy") == 1 and "<style" not in html and not re.search(r"<script\b", html)


# ---------------------------------------------------------------- what never ships
def test_size_no_audio_no_key_no_local_paths(index):
    files = [p for p in PLAY.rglob("*") if p.is_file()]
    assert sum(p.stat().st_size for p in files) < build.LIMIT_BYTES
    assert not [p for p in files if p.suffix.lower() in {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".webm"}]
    problems = []
    for p in files:
        data = p.read_bytes()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        problems += build.scan(str(p.relative_to(PLAY)), data)
        if p.parts[len(PLAY.parts)] in ("rec", "api"):  # recorded bodies and linked files: the clip's bytes were never kept
            assert b"data_b64" not in data and b"RIFF" not in data[:4], p.name
    assert not problems, problems[:10]


# ---------------------------------------------------------------- the shim under node
NODE = r"""
const fs = require('fs'), path = require('path');
const dir = process.argv[2], base = 'http://127.0.0.1:1/stowlm/play/', I = JSON.parse(fs.readFileSync(path.join(dir, 'rec/index.json')));
const calls = [], infos = []; let clock = 0;
const nodeFetch = async (u) => {
  u = String(u); calls.push(u);
  if (!u.startsWith(base)) throw new Error('outside the folder: ' + u);
  return new Response(fs.readFileSync(path.join(dir, decodeURIComponent(new URL(u).pathname.slice(new URL(base).pathname.length)))));
};
const el = () => ({ setAttribute() {}, appendChild() {}, append() {}, addEventListener() {}, set innerHTML(v) {}, set textContent(v) {}, dataset: {}, hidden: false });
const store = new Map([['stowlm.functional.call', 'c20990101-000000-abcdef']]);
globalThis.window = { fetch: nodeFetch };
globalThis.location = new URL(base + 'index.html?call=c20990101-000000-abcdef');
globalThis.history = { replaceState(_, __, u) { globalThis.location = new URL(u, base); } };
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
globalThis.document = { currentScript: { src: base + 'play.js' }, body: el(), createElement: el, addEventListener() {}, readyState: 'loading', getElementById: () => null, querySelector: () => null };
Object.defineProperty(globalThis, 'performance', { value: { now: () => clock }, configurable: true });
console.info = (m) => infos.push(m);
eval(fs.readFileSync(path.join(dir, 'play.js'), 'utf8'));
const f = window.fetch, api = (p) => 'api/functional/' + p;
const shuffle = (o) => Object.fromEntries(Object.entries(o).reverse());
(async () => {
  const j = async (r) => [r.status, await r.json()];
  const out = { search: location.search, stored: store.get('stowlm.functional.call') ?? null };
  out.status = (await j(await f(api('status'))))[0];
  out.early_now = Date.now();
  const form = I.anchors[0].body;
  out.wrong = await j(await f(api('calls'), { method: 'POST', body: JSON.stringify({ ...form, seed: form.seed + 1 }) }));
  out.after_wrong = window.__play.anchor;
  clock = 5000;
  out.create = await j(await f(api('calls'), { method: 'POST', body: JSON.stringify(shuffle(form)) }));
  out.after_create = window.__play.anchor;
  out.now_at_create = Date.now() - I.t0;
  out.first_view = (await j(await f(api('calls/' + I.call_id))))[1].state;
  clock = 5000 + 3600 * 1000;  // an hour later, without pressing anything: the recorded run waits for the next action
  out.waiting_view = (await j(await f(api('calls/' + I.call_id))))[1].state;
  out.now_waiting = Date.now() - I.t0;
  out.skip = await j(await f(api('calls/' + I.call_id + '/plan'), { method: 'POST', body: JSON.stringify(I.anchors[I.anchors.length - 1].body) }));
  out.missing = (await f(api('calls/' + I.call_id + '/planner/nothing'))).status;
  out.external = (await f('https://example.com/x')).status;
  out.infos = infos; out.calls = calls;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.log(JSON.stringify({ error: String(e && e.stack || e) })); process.exit(0); });
"""


@needs_node
def test_shim_answers_only_the_next_recorded_action(tmp_path, index):
    script = tmp_path / "shim.cjs"
    script.write_text(NODE)
    run = subprocess.run(["node", str(script), str(PLAY)], capture_output=True, text=True, timeout=60)
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert "error" not in out, out
    assert out["search"] == "?live=1" and out["stored"] is None  # the Functional app's live mode, fresh on every load
    assert out["status"] == 200
    assert out["wrong"][0] == 409 and "recorded run" in out["wrong"][1]["error"] and "install the app" in out["wrong"][1]["error"]
    assert out["after_wrong"] == -1
    assert out["create"][0] in (200, 201, 202) and out["create"][1]["id"] == index["call_id"] and out["after_create"] == 0
    assert abs(out["now_at_create"] - index["anchors"][0]["t"]) < 50  # the page's clock is the recorded run's
    assert out["first_view"] in ("generating", "needs_papers")
    assert out["waiting_view"] == "needs_papers" and out["now_waiting"] < index["anchors"][1]["t"]
    assert out["skip"][0] == 409  # Generate plan before the papers are read: not the recorded run's next action
    assert out["missing"] == 404 and out["external"] == 409
    assert any("recorded run" in m for m in out["infos"])
    assert all(c.startswith("http://127.0.0.1:1/stowlm/play/") for c in out["calls"])


def test_readme_names_the_run_and_the_install_steps(index):
    text = (PLAY / "README.md").read_text()
    assert "recorded run" in text and build.INSTALL_URL in text and index["call_id"] in text
    assert "Audio" in text or "audio" in text
