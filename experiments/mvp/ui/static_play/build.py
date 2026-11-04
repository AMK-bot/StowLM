"""'Play it yourself': a static, no-API playback of the Functional app (/planner?live=1) on one recorded track.

Two steps. The recording is made once, against a running Functional server, with real model calls; the build can be redone
from it any time without a server.

  1. record (paid calls: Muse Spark reads the call's papers, Muse Voice Transcribe hears the spoken instruction)

       env -u UV_CACHE_DIR uv run --offline --with playwright python experiments/mvp/ui/static_play/build.py record \\
           --base http://127.0.0.1:PORT --ship feeder_500 --size medium --fill 32 --seed 1720145402 \\
           --say "Leave bay one empty on this call."

     Without --base it starts `python -m app --functional --env-file <--env-file>` on --app-port (its own process group, PID in
     static_play/app.pid) and stops it afterwards. --dry rehearses the plumbing with no paid call (generated load list, typed
     instruction). The track recorded on 4 Nov ran against a server started with a $2.30 launch cap, so Muse Spark's admission
     (a $1.38 worst-case hold per booking) stopped new bookings once spend passed $0.92.

     Headless system Chrome walks the planner (walk.py) through the real app: the form set to the track's inputs, Create
     call, Muse Spark reads the call's synthetic papers, a macOS `say` clip of the instruction (mono PCM16, 16 kHz, labelled
     generated voice) goes to Muse Voice Transcribe, the StowLM Reader reads the transcript, the planner confirms, Engine
     0.13.0 plans and the full checker decides, then the review tabs. Every request the page makes is answered by the real
     server through Playwright and recorded with its wall-clock time; polls are thinned to changes (or one every few seconds).
     The files the page links to (papers, package, outcome, case) are fetched once. The recording and the clip stay in the
     call's git-ignored folder (experiments/mvp/ui/functional/calls/<id>/static-play/); track.json here names the call.

  2. emit (default; no server, no network)

       uv run python experiments/mvp/ui/static_play/build.py [emit] [--call <id>]

     Writes experiments/mvp/video/site/play/: index.html (app/static/planner.html with the shim loaded first), every planner
     script and stylesheet copied from app/static with root URLs made relative and a few live-only words relabelled for a
     recording (WORDING), shim play.js/play.css, rec/index.json + gzip bodies, the linked files under api/functional/...,
     README.md and build.json. No audio ships (D309). Fails if any shipped byte carries a local path, a home directory or a
     key-like string, if the copy is over 40 MB, or if a relabel no longer matches the app's source.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT / "experiments/mvp/video/site/play"
STATIC = ROOT / "app/static"
CALLS = ROOT / "experiments/mvp/ui/functional/calls"
TRACK = HERE / "track.json"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
LIMIT_BYTES = 40 * 1024 * 1024
INSTALL_URL = "https://github.com/AMK-bot/StowLM#setup-and-run"
API = "/api/functional/"
AUDIO_ABSENT = "Audio not included in this copy"  # D309: no generated speech ships; the transcript is shown
RECORDED = "This is a recorded run: its inputs are fixed"
TARGET_S = 30.0   # a long recorded stage replays in about this many seconds (its shown times stay the measured ones)
MAX_SPEED = 90.0
CALL_ID = re.compile(r"^c\d{8}-\d{6}-[0-9a-f]{6}$")
MUTATIONS = re.compile(r"^/api/functional/calls(/c\d{8}-\d{6}-[0-9a-f]{6}/(papers|answers|audio|read|confirm|plan|stop))?$")

# The app's words for things that run live on the machine the planner sits at. In this copy they replay a recorded run on the
# recording machine, so they say so. (file, old, new, times it must match)
WORDING = [
    ("planner.js", "badge.textContent = 'Functional app'; badge.title = 'Runs the pipeline live on fresh synthetic inputs; nothing is replayed';",
     "badge.textContent = 'Recorded run'; badge.title = 'A recorded run of the Functional app: precomputed inputs, every answer replayed from the recording';", 1),
    ("planner.js", "if (line) line.textContent = 'Runs the pipeline live on fresh synthetic inputs; nothing is replayed';",
     "if (line) line.textContent = 'A recorded run of the Functional app, replayed: precomputed inputs, synthetic ship and cargo, simulated port';", 1),
    ("planner.js", "if (link) { link.textContent = 'Open the Demo app'; link.setAttribute('href', '/planner?demo=1'); }",
     "if (link) { link.textContent = 'Install the app to try your own inputs'; }", 1),
    ("planner.js", "'StowLM Reader · on this laptop'", "'StowLM Reader · recorded on a laptop'", 2),
    ("planner.js", "'StowLM Reader: fine-tuned Muse Glimmer 30B (LoRA), on this laptop'",
     "'StowLM Reader: fine-tuned Muse Glimmer 30B (LoRA), recorded on the laptop that ran this call'", 1),
    ("planner.html", '<span class="pl-chip actor" style="--c:var(--pl-c-reader)" title="StowLM Reader: fine-tuned Muse Glimmer 30B, loaded on this laptop">StowLM Reader · on this laptop</span>',
     '<span class="pl-chip actor" style="--c:var(--pl-c-reader)" title="StowLM Reader: fine-tuned Muse Glimmer 30B, recorded on the laptop that ran this call">StowLM Reader · recorded on a laptop</span>', 1),
    ("planner-live-instruct.js", "StowLM Reader · fine-tuned Muse Glimmer 30B (LoRA) on this laptop, schema-masked decoding",
     "StowLM Reader · fine-tuned Muse Glimmer 30B (LoRA) on the recording laptop, schema-masked decoding", 2),
    ("planner-live-instruct.js", "Measured on this machine, this run.", "Measured on the recording machine, in this recorded run.", 1),
    ("planner-live-intake.js", "Read times measured on this machine, this run", "Read times measured on the recording machine, in this recorded run", 1),
    ("planner-live-intake.js", "measured on this machine, this run", "measured on the recording machine, in this recorded run", 3),
    ("planner-live-voyage.js", "const MEASURED = 'measured on this machine, this run';", "const MEASURED = 'measured on the recording machine, in this recorded run';", 1),
    ("planner-live-plan.js", "const MEASURED = 'measured on this machine, this run';", "const MEASURED = 'measured on the recording machine, in this recorded run';", 1),
]

SECRET_PATTERNS = [
    re.compile(r"/Users/"), re.compile(r"/home/[a-z]"), re.compile(r"/private/(var|tmp)/"), re.compile(r"/var/folders/"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"), re.compile(r"AKIA[0-9A-Z]{16}"), re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), re.compile(r"(?i)bearer [A-Za-z0-9._-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|password)[\"']?\s*[:=]\s*[\"'][^\"']{8,}"),
    re.compile(r"andakprapakorn"), re.compile(r"MODEL_API_KEY\s*="),
]


# ---------------------------------------------------------------- keys (play.js builds the same)
def enc(s: str) -> str:
    return quote(s, safe="-_.!~*'()")


def canon_body(raw, drop=()) -> str:
    if raw in (None, b"", ""):
        return ""
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in drop}
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def key_of(method: str, url: str, body=None) -> str:
    """method + path + sorted query (+ canonical JSON body for a POST). The audio's bytes never ship, so they never key."""
    u = urlsplit(url)
    path = unquote(u.path)
    rows = sorted(parse_qsl(u.query, keep_blank_values=True))
    key = f"{method} {path}"
    if rows:
        key += "?" + "&".join(f"{enc(k)}={enc(v)}" for k, v in rows)
    b = "" if method == "GET" else canon_body(body, drop=("data_b64",) if path.endswith("/audio") else ())
    return key + (" " + b if b else "")


# ---------------------------------------------------------------- cleaning and scanning
HOME = str(Path.home())


def clean_str(v: str) -> str:
    if v.startswith("/api/"):
        v = v[1:]  # same-origin, relative: the copy sits under any sub-path
    v = v.replace(str(ROOT) + "/", "").replace(str(ROOT), ".")
    v = re.sub(r"(/private)?/var/folders/[^\s\"'<>]*", "<temporary folder>", v)
    v = re.sub(r"(/private)?/tmp/[^\s\"'<>]*", "<temporary folder>", v)
    v = v.replace(HOME + "/", "~/").replace(HOME, "~")
    return v


def clean_value(v):
    if isinstance(v, dict):
        return {k: clean_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clean_value(x) for x in v]
    if isinstance(v, str):
        return clean_str(v)
    return v


def clean_text(ctype: str, text: str) -> str:
    if "json" in ctype:
        try:
            return json.dumps(clean_value(json.loads(text)), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    text = text.replace(str(ROOT) + "/", "").replace(str(ROOT), ".")
    text = re.sub(r"(/private)?/var/folders/[^\s\"'<>]*", "<temporary folder>", text)
    return text.replace(HOME + "/", "~/").replace(HOME, "~")


def scan(name: str, data: bytes) -> list[str]:
    text = data.decode("utf-8", "replace")
    found = [p.pattern for p in SECRET_PATTERNS if p.search(text)]
    if HOME in text:
        found.append("home directory")
    return [f"{name}: {f}" for f in found]


def secret_words() -> list[str]:
    """Key-like values of this process's environment (never printed): no shipped or recorded byte may contain one."""
    out = []
    for name, value in os.environ.items():
        if re.search(r"(?i)key|token|secret|password", name) and len(value) >= 12:
            out.append(value)
    return out


# ---------------------------------------------------------------- record
class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, str, bytes]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def json(self, path: str):
        s, _, b = self.call("GET", path)
        if s != 200:
            raise SystemExit(f"GET {path}: {s}")
        return json.loads(b)


def view_sig(text: str) -> str:
    """What a planner sees change in a call view, without the ticking counters (thinning keeps one of those every few s)."""
    try:
        v = json.loads(text)
    except ValueError:
        return text
    if not isinstance(v, dict):
        return text
    run = v.get("run") or {}
    papers = v.get("papers") or {}
    keep = {
        "state": v.get("state"), "message": v.get("message"), "run": [run.get("status"), run.get("incumbents"), run.get("admission")],
        "outcome": v.get("outcome"), "readings": v.get("readings"), "voice": v.get("voice"), "load_list": (v.get("load_list") or {}).get("boxes"),
        "papers": [papers.get("progress"), [(b.get("booking_ref"), b.get("status")) for b in papers.get("bookings") or []], (papers.get("intake") or {}).get("status")],
        "stages": [(s.get("stage"), s.get("instance_id"), s.get("status")) for s in v.get("stages") or []],
    }
    return json.dumps(keep, sort_keys=True, default=str)


def make_clip(text: str, out: Path) -> Path:
    """macOS text-to-speech, mono PCM16 at 16 kHz: the 'generated voice' the planner labels as such (never shipped, D309)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["say", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@16000", text], check=True)
    data = out.read_bytes()
    channels, rate, bits = int.from_bytes(data[22:24], "little"), int.from_bytes(data[24:28], "little"), int.from_bytes(data[34:36], "little")
    if (channels, rate, bits) != (1, 16000, 16):
        raise SystemExit(f"clip is {channels} ch, {rate} Hz, {bits} bit; want mono 16 kHz PCM16")
    return out


async def record_walk(base: str, track: dict, log=print, resume: str | None = None) -> dict:
    sys.path.insert(0, str(HERE))
    import walk  # noqa: E402
    from playwright.async_api import async_playwright

    events: list[dict] = []
    statics: set[str] = set()
    last: dict[str, tuple[float, str]] = {}  # key -> (t of the last kept GET, its signature)
    errors: list[str] = []

    async def route(r):
        req = r.request
        path = urlsplit(req.url).path
        if not path.startswith("/api/"):
            statics.add(path)
            await r.continue_()
            return
        t_req = time.time()
        resp = await r.fetch()
        data = await resp.body()
        t_resp = time.time()
        await r.fulfill(response=resp, body=data)
        key = key_of(req.method, req.url, req.post_data)
        ctype = resp.headers.get("content-type", "")
        text = data.decode("utf-8", "replace")
        if req.method == "GET":
            is_view = re.fullmatch(r"/api/functional/calls/c\d{8}-\d{6}-[0-9a-f]{6}", path) is not None
            sig = view_sig(text) if is_view else hashlib.sha256(data).hexdigest()
            prev = last.get(key)
            gap = 3.0 if is_view else 15.0
            if prev and prev[1] == sig and t_req - prev[0] < gap:
                return  # thinned: same as the last kept answer
            last[key] = (t_req, sig)
        query = urlsplit(req.url).query
        events.append({"t_req": round(t_req * 1000, 1), "t_resp": round(t_resp * 1000, 1), "method": req.method,
                       "url": path + ("?" + query if query else ""), "key": key,
                       "body": None if req.method == "GET" else canon_body(req.post_data, drop=("data_b64",)),  # the clip's bytes stay out
                       "status": resp.status, "ctype": ctype, "text": text})

    shots = HERE / "screens-record"
    shots.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, headless=True)
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route("**/*", route)
        t0 = time.time()
        seen, call_id = {}, None
        url = f"{base}/planner?live=1" + (f"&call={resume}" if resume else "")
        try:
            seen = await walk.flow(page, url, track, mode="record", log=log, resume_at="edit" if resume else None,
                                   shot=lambda n: page.screenshot(path=str(shots / f"{n}.png")))
        finally:
            try:
                call_id = await page.evaluate("() => new URLSearchParams(location.search).get('call')")
            except Exception:  # noqa: BLE001
                pass
            partial = {"t0": round(t0 * 1000, 1), "events": events, "statics": sorted(statics), "seen": seen, "page_errors": errors, "call_id": call_id}
            if call_id and CALL_ID.match(call_id):  # whatever happened, the answers of paid calls are kept (git-ignored call folder)
                (CALLS / call_id / "static-play").mkdir(parents=True, exist_ok=True)
                name = "partial-resume.json" if resume else "partial.json"  # a resumed session never overwrites the first
                (CALLS / call_id / "static-play" / name).write_text(json.dumps(partial, ensure_ascii=False))
            await browser.close()
    return partial


def linked_files(api: Api, call_id: str) -> dict[str, tuple[str, str]]:
    """Every file the planner links for this call: its papers, the package, the outcome and the case (path -> (ctype, b64))."""
    v = api.json(f"{API}calls/{call_id}")
    paths = {"case.json"}
    for doc in (v.get("papers") or {}).get("documents") or []:
        p = doc.get("path")
        if isinstance(p, str) and p.startswith("papers/generated/") and ".." not in p.split("/"):
            paths.add(p)
    manifest = CALLS / call_id / "papers/generated/manifest.json"
    if manifest.is_file():
        for doc in json.loads(manifest.read_text()):
            p = doc.get("path")
            if isinstance(p, str) and ".." not in p.split("/"):
                paths.add(p if p.startswith("papers/") else "papers/generated/" + p)
    s, _, b = api.call("GET", f"{API}calls/{call_id}/planner/package")
    if s == 200:
        pkg = json.loads(b)
        for f in pkg.get("files") or []:
            paths.add(f["url"].split(f"/calls/{call_id}/file/", 1)[1])
        if pkg.get("outcome_url"):
            paths.add(pkg["outcome_url"].split(f"/calls/{call_id}/file/", 1)[1])
    out = {}
    for p in sorted(paths):
        s, ctype, data = api.call("GET", f"{API}calls/{call_id}/file/" + "/".join(quote(x) for x in p.split("/")))
        if s == 200:
            out[p] = (ctype, base64.b64encode(data).decode())
        else:
            print(f"file {p}: {s} (left out)", flush=True)
    return out


def record(args) -> None:
    api = Api(args.base)
    st = api.json(API + "status")
    av, key = st.get("availability") or {}, st.get("key") or {}
    missing = [n for n in (("reader",) if args.dry else ("spark", "voice", "reader")) if not (av.get(n) or {}).get("available")]
    if missing or (not args.dry and (not key.get("present") or not key.get("paid_calls_accepted"))):
        raise SystemExit(f"the server must offer Spark, Voice and the reader with a key and paid calls accepted; missing {missing}, "
                         f"key present={key.get('present')}, accepted={key.get('paid_calls_accepted')}")
    print(f"server ok: key {key.get('source')}, cap ${key.get('cap_usd')}, spent ${key.get('spent_usd')}", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="stowlm-clip-"))
    clip = None if args.dry or args.resume else make_clip(args.say, tmp / args.clip_name)
    form = {"ship": args.ship, "size_class": args.size, "fill_pct": args.fill, "generator": args.generator, "port": args.port, "seed": args.seed}
    track = {"form": form, "audio": {"path": str(clip), "name": args.clip_name}, "spoken": args.say, "profile": "standard"}
    if args.dry:  # a no-model rehearsal of the plumbing: generated load list, a typed instruction
        track.update(dry=True, typed=args.say, audio={"name": args.clip_name})
    if args.edit:
        track["edit"] = args.edit  # the planner's correction if the reader refuses the transcript as heard
    if args.resume:
        # A second session on the same call (the first stopped at a refused reading): the walk picks up at the correction,
        # and both sessions' answers become one recording on the first session's clock.
        first = json.loads((CALLS / args.resume / "static-play/partial.json").read_text())
        rec = asyncio.run(record_walk(args.base, track, resume=args.resume))
        if rec["call_id"] != args.resume:
            raise SystemExit(f"the resumed page shows call {rec['call_id']!r}, not {args.resume}")
        rec = dict(rec, t0=first["t0"], events=first["events"] + rec["events"], statics=sorted(set(first["statics"]) | set(rec["statics"])),
                   seen={**first.get("seen", {}), **rec["seen"]}, page_errors=first.get("page_errors", []) + rec["page_errors"],
                   sessions=[{"t0": first["t0"], "events": len(first["events"])}, {"t0": rec["t0"], "events": len(rec["events"])}])
    else:
        rec = asyncio.run(record_walk(args.base, track))
    call_id = rec["call_id"]
    if not call_id or not CALL_ID.match(call_id):
        raise SystemExit(f"no call id after the walk: {call_id!r}")
    folder = CALLS / call_id / "static-play"
    folder.mkdir(parents=True, exist_ok=True)
    if clip:
        shutil.move(str(clip), folder / args.clip_name)
    shutil.rmtree(tmp, ignore_errors=True)
    rec["files"] = linked_files(api, call_id)
    rec["track"] = {**{k: v for k, v in track.items() if k != "audio"}, "audio": {"name": args.clip_name}, "call_id": call_id}
    for leftover in ("partial.json", "partial-resume.json"):
        (folder / leftover).unlink(missing_ok=True)
    rec["recorded_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec["final_view"] = api.json(f"{API}calls/{call_id}")
    rec["final_status"] = api.json(API + "status")
    text = json.dumps(rec, ensure_ascii=False)
    for word in secret_words():
        if word in text:
            raise SystemExit("a key-like environment value appears in the recording; nothing was written")
    (folder / "recording.json").write_text(text)
    if not args.dry:
        TRACK.write_text(json.dumps({"call_id": call_id, "recorded_utc": rec["recorded_utc"], "form": form, "spoken": args.say,
                                     "clip_name": args.clip_name}, indent=1) + "\n")
    print(f"recorded call {call_id}: {len(rec['events'])} responses, {len(rec['files'])} files, page errors {len(rec['page_errors'])}", flush=True)


# ---------------------------------------------------------------- emit
CSP = ('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\'; '
       'style-src-attr \'unsafe-inline\'; img-src \'self\' data:; media-src \'self\'; font-src \'self\'; connect-src \'self\'; '
       'worker-src \'none\'; object-src \'none\'; base-uri \'self\'; form-action \'none\'">')
CHARSET = '<meta charset="utf-8">'
STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)


def harden_html(rel: str, text: str) -> dict[str, str]:
    """rel's page with the CSP after its charset, its <style> blocks moved to a sibling <stem>.css (style-src 'self' would drop
    them), and nothing inline left to run. Returns {rel: html, [css rel: css]}."""
    if text.count(CHARSET) != 1 or "Content-Security-Policy" in text:
        raise SystemExit(f"{rel}: expected one {CHARSET} and no CSP yet")
    if re.search(r"<script\b(?![^>]*\bsrc=)", text, re.I):
        raise SystemExit(f"{rel}: inline script; the CSP allows script files only")
    out, styles = {}, STYLE_BLOCK.findall(text)
    if styles:
        css = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".css"
        first = [True]

        def swap(m):
            keep, first[0] = first[0], False
            return f'<link rel="stylesheet" href="{css}">' if keep else ""
        text = STYLE_BLOCK.sub(swap, text)
        out[(rel.rsplit("/", 1)[0] + "/" if "/" in rel else "") + css] = "\n".join(styles)
    icon = "" if re.search(r'<link\b[^>]*rel="icon"', text, re.I) else '<link rel="icon" href="data:,">'  # no /favicon.ico request outside the folder
    out[rel] = text.replace(CHARSET, CHARSET + CSP + icon, 1)
    return out


def page_assets() -> list[str]:
    """The files planner.html loads from app/static, in its order."""
    html = (STATIC / "planner.html").read_text()
    names = re.findall(r'(?:href|src)="/([A-Za-z0-9_.-]+\.(?:css|js))"', html)
    missing = [n for n in names if not (STATIC / n).is_file()]
    if missing:
        raise SystemExit(f"planner.html loads files app/static doesn't have: {missing}")
    return names


def wording_for(name: str) -> list[tuple[str, str, int]]:
    return [(old, new, n) for f, old, new, n in WORDING if f == name]


def relabel(name: str, text: str) -> str:
    for old, new, n in wording_for(name):
        if text.count(old) != n:
            raise SystemExit(f"{name}: wording to relabel found {text.count(old)} times, expected {n}: {old[:70]}")
        text = text.replace(old, new)
    return text


def rewrite_static(name: str, text: str) -> str:
    if name.endswith(".js"):
        text = re.sub(r"(['\"`])/api/", r"\1api/", text)
    if name.endswith(".css"):
        text = re.sub(r"/\*(?:(?!\*/).)*://(?:(?!\*/).)*\*/", "", text, flags=re.S)  # a designer's credit link in a comment: not UI
    return relabel(name, text)


def banner_html(track: dict, call_id: str) -> str:
    f = track["form"]
    return (f'<div id="play-banner" role="note"><b>Recorded run of the Functional app</b>'
            f'<span>Precomputed inputs ({f["ship"]}, {f["size_class"]} section, fresh seed {f["seed"]}); every answer replays from a recording '
            f'made on {track["recorded_utc"][:10]}. Synthetic ships and cargo, simulated port.</span>'
            f'<a id="play-install" href="{INSTALL_URL}" target="_blank" rel="noopener">Install the app to try your own inputs</a></div>\n')


def index_html(track: dict, call_id: str) -> str:
    html = relabel("planner.html", (STATIC / "planner.html").read_text())
    if html.count('<a class="pl-demo-link" href="/functional">') != 1:
        raise SystemExit("planner.html changed shape: no demo-tag link")
    html = html.replace('<a class="pl-demo-link" href="/functional">', f'<a class="pl-demo-link" href="{INSTALL_URL}" target="_blank" rel="noopener">', 1)
    html = re.sub(r'(href|src)="/(?!/)', r'\1="', html)
    head = '<link rel="stylesheet" href="play.css">\n<script src="play.js"></script>\n'
    vp = '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    if html.count(vp) != 1 or html.count("<title>StowLM Planner</title>") != 1 or html.count("<body>\n") != 1:
        raise SystemExit("planner.html changed shape: viewport, title or body not found once")
    html = html.replace(vp, vp + head, 1)
    html = html.replace("<title>StowLM Planner</title>", "<title>StowLM · Recorded run of the Functional app</title>", 1)
    html = html.replace("<body>\n", "<body>\n" + banner_html(track, call_id), 1)
    return html


def intake_story(anchors: list, views: list) -> str:
    """What happened in 2 Cargo, from the recorded call views [(t, view)]: the bookings Muse Spark read, a retried read, its
    questions, and the planner's choice. Every figure is read from the recording."""
    papers = [a for a in anchors if a["what"] == "papers"]
    if not papers or not (papers[0]["body"] or {}).get("use_generated_papers"):
        return "The call's load list came from the generator; no model read the papers."
    retried = sum(1 for a in papers[1:] if (a["body"] or {}).get("use_generated_papers"))
    chose = next((a for a in papers[1:] if (a["body"] or {}).get("use_generated_load_list")), None)
    before = [v for t, v in views if t < (chose["t"] if chose else float("inf"))]
    last = before[-1] if before else {}
    bookings = (last.get("papers") or {}).get("bookings") or []
    questions = ((last.get("papers") or {}).get("intake") or {}).get("questions") or []
    spark = [r for r in (last.get("stages") or []) if r.get("stage") == "intake" and (r.get("cost_usd") or 0) > 0
             and not r.get("cost_scope")]  # booking reads, failed ones included; not the cumulative row, which adds no charge
    cost = sum(r["cost_usd"] for r in spark)
    words = (f"Muse Spark read the call's synthetic cargo papers, {len(bookings)} bookings at once (${cost:.2f} in all"
             + (", the failed read included)" if retried else ")"))
    if retried:
        words += f"; {retried} booking read failed in the intake's own parser (a software error on Spark's answer) and the planner retried it"
    if questions:
        words += f". The readings left {len(questions)} questions"
    if chose:
        words += (", none of which a cargo answer can close, so the planner set the reading aside and planned the generated "
                  "load list (the app's own button: software, no model)")
    elif not questions:
        final = views[-1][1] if views else {}
        boxes = (final.get("load_list") or {}).get("boxes")
        origin = (final.get("load_list") or {}).get("origin")
        if origin == "intake":
            words += f", with no open questions: the {boxes} boxes planned are Muse Spark's load list"
    return words + "."


def instruction_story(spoken: str, final: dict) -> str:
    """The instruction step from the final recorded call view: what Muse Voice heard, every reading, the confirmation."""
    voice = final.get("voice") or {}
    readings = final.get("readings") or []
    words = (f'The spoken instruction "{spoken}" (a macOS text-to-speech clip, labelled generated voice) went to Muse Voice '
             f'Transcribe, which heard "{voice.get("transcript", "")}".')
    for r in readings:
        if r.get("status") == "constraint_ready":
            who = "the planner's corrected sentence" if r.get("origin") == "planner_edit" else "it"
            words += f' The StowLM Reader (fine-tuned Muse Glimmer 30B) read {who} as "{r.get("rule_text")}"'
            words += ", and the planner confirmed it." if r.get("confirmed") else "."
        else:
            words += " The StowLM Reader refused that sentence (it is built to refuse what it can't read as one of its four rules)"
            nxt = readings[readings.index(r) + 1] if readings.index(r) + 1 < len(readings) else None
            words += f', so the planner corrected the transcript to "{nxt.get("sentence")}".' if nxt and nxt.get("origin") == "planner_edit" else "."
    return words


def readme(track: dict, call_id: str, built: str) -> str:
    f = track["form"]
    return f"""# StowLM: Play it yourself (a recorded run)

This folder is a recorded run of the StowLM Functional app (`/planner?live=1`): the same page, scripts and styles as the app,
with every answer the server gave recorded from one run of the app on {track['recorded_utc'][:10]} and replayed here. Nothing is
installed and no request leaves this folder. Synthetic ships and cargo, simulated port.

The recorded track: {f['ship']}, {f['size_class']} section, fill {f['fill_pct']} %, {f['generator']}, {f['port']}, fresh seed
{f['seed']} (call `{call_id}`). {track.get('intake_story', '')} {track.get('instruction_story', '')} Engine 0.13.0 planned
and the full checker decided.

You press the same buttons as in the app. Long stages replay faster than they ran, and the page says so; every time and number
shown is the one measured in the recorded run. The inputs are fixed: changing any of them runs nothing and points to the
install steps. No API key is needed or accepted. The spoken clip's audio isn't included in this copy (generated speech, D309);
its transcript is. Stress test and Ask copilot aren't in the Functional app yet, and say so here as they do there.

To run the app on your own inputs, follow the install steps at {INSTALL_URL}, then:

    uv run python -m app --functional --port 8794
    # open http://127.0.0.1:8794/planner?live=1

Built by `experiments/mvp/ui/static_play/build.py` on {built}. Serve this folder with any static server
(`python -m http.server`), or open it on the project's GitHub Pages site.
"""


def emit(call_id: str | None = None, log=print) -> dict:
    if not call_id:
        if not TRACK.is_file():
            raise SystemExit(f"no {TRACK.name}: record a track first")
        call_id = json.loads(TRACK.read_text())["call_id"]
    src = CALLS / call_id / "static-play/recording.json"
    rec = json.loads(src.read_text())
    track = {**rec["track"], "recorded_utc": rec["recorded_utc"]}
    t0 = rec["t0"]

    bodies: dict[str, bytes] = {}

    def body(ctype: str, text: str) -> str:
        data = clean_text(ctype, text).encode()
        ref = "b/" + hashlib.sha256(data).hexdigest()[:20] + ".jsonz"
        bodies[ref] = data
        return ref

    view_path = f"{API}calls/{call_id}"
    extra = CALLS / call_id / "static-play/extra-events.json"  # planner actions sent through the API (planner_action.py)
    events = rec["events"] + (json.loads(extra.read_text()) if extra.is_file() else [])
    anchors, entries, sigs = [], {}, []
    for e in sorted(events, key=lambda e: e["t_req"]):
        path = e["url"].split("?", 1)[0]
        if e["method"] == "POST" and MUTATIONS.match(path):
            anchors.append({"key": e["key"], "t": round(e["t_resp"] - t0), "post": [0, e["status"], e["ctype"], body(e["ctype"], e["text"])],
                            "what": path.rsplit("/", 1)[-1] if path != API + "calls" else "create", "body": json.loads(e["body"] or "null"),
                            "via": e.get("via", "page")})
            continue
        if e["method"] != "GET":
            continue  # a mutation the walk never makes; the shim answers it with the recorded-run message
        entries.setdefault(e["key"], []).append([round(e["t_req"] - t0), e["status"], e["ctype"], body(e["ctype"], e["text"])])
        if e["url"] == view_path:
            sigs.append((round(e["t_req"] - t0), view_sig(e["text"])))
    order = " ".join(a["what"] for a in anchors)
    if not re.fullmatch(r"create papers( papers| answers)*" + ("" if track.get("dry") else " audio") + "( read)+ confirm plan", order):
        raise SystemExit(f"unexpected recorded actions: {order}")
    on_questions = ("use-generated-load-list" if any((a["body"] or {}).get("use_generated_load_list") for a in anchors[2:])
                    else "send-answers" if any(a["what"] == "answers" for a in anchors) else None)
    # Speed per stage: real work (the call view still changing) replays in about TARGET_S; the planner's own pauses don't count.
    for i, a in enumerate(anchors):
        end = anchors[i + 1]["t"] if i + 1 < len(anchors) else float("inf")
        changes, prev = [], None
        for t, sig in sigs:
            if t < a["t"] - 2000:
                prev = sig
                continue
            if t >= end:
                break
            if sig != prev:
                changes.append(t)
            prev = sig
        busy = max(0, (changes[-1] if changes else a["t"]) - a["t"]) / 1000
        a["busy_s"] = round(busy, 1)
        a["speed"] = round(min(MAX_SPEED, max(1.0, busy / TARGET_S)), 2)
    # A stage replayed N times faster needs no more than a few answers per second of replay: keep each change of what the
    # planner sees, else one answer per max(3 s, N/2 s) of the recorded run.
    def speed_at(t):
        sp = 1.0
        for a in anchors:
            if a["t"] <= t:
                sp = a["speed"]
        return sp
    sig_of = dict(sigs)
    for key, rows in entries.items():
        if key != f"GET {view_path}":
            continue
        kept, last_t, last_sig = [], None, None
        for i, row in enumerate(rows):
            sig = sig_of.get(row[0])
            final = i == len(rows) - 1
            if last_t is None or sig != last_sig or final or row[0] - last_t >= max(3000, speed_at(row[0]) * 500):
                kept.append(row)
                last_t, last_sig = row[0], sig
        entries[key] = kept
    used = {row[3] for rows in entries.values() for row in rows} | {a["post"][3] for a in anchors}
    for ref in [r for r in bodies if r not in used]:
        del bodies[ref]
    files = {p: (c, base64.b64decode(b)) for p, (c, b) in rec["files"].items()}
    story = intake_story(anchors, [(r[0], json.loads(bodies[r[3]])) for r in entries.get(f"GET {view_path}", [])])
    track["intake_story"] = story
    track["instruction_story"] = instruction_story(track["spoken"], rec.get("final_view") or {})

    if OUT.exists():
        if OUT.name != "play" or OUT.parent.name != "site":
            raise SystemExit(f"refusing to clear {OUT}")
        shutil.rmtree(OUT)
    (OUT / "rec/b").mkdir(parents=True)
    problems: list[str] = []
    written: dict[str, bytes] = {}
    secrets = secret_words()

    def put(rel: str, data: bytes, raw: bytes | None = None) -> None:
        if rel in written:
            raise SystemExit(f"{rel} written twice")
        plain = raw if raw is not None else data
        problems.extend(scan(rel, plain))
        if any(w in plain.decode("utf-8", "replace") for w in secrets):
            problems.append(f"{rel}: a key-like environment value")
        (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
        (OUT / rel).write_bytes(data)
        written[rel] = data

    assets = page_assets()
    for name in assets:
        put(name, rewrite_static(name, (STATIC / name).read_text()).encode())
    for rel, text in harden_html("index.html", index_html(track, call_id)).items():
        put(rel, text.encode())
    put("play.js", (HERE / "shim/play.js").read_bytes())
    put("play.css", (HERE / "shim/play.css").read_bytes())
    file_paths = []
    for p, (ctype, data) in sorted(files.items()):
        rel = f"api/functional/calls/{call_id}/file/{p}"
        if ctype.startswith("text/") or "json" in ctype or p.endswith((".md", ".csv", ".txt", ".html", ".json")):
            text = clean_text(ctype if "json" in ctype or not p.endswith(".json") else "application/json", data.decode())
            if p.endswith(".html"):
                for r2, t2 in harden_html(rel, text).items():
                    put(r2, t2.encode())
            else:
                put(rel, text.encode())
        else:
            put(rel, data)
        file_paths.append("/" + rel)
    for ref, data in bodies.items():
        put(f"rec/{ref}", gzip.compress(data, 9, mtime=0), raw=data)
    built = datetime.now(timezone.utc).isoformat(timespec="seconds")
    f = track["form"]
    index = {"version": 2, "built_utc": built, "recorded_utc": track["recorded_utc"], "call_id": call_id, "t0": t0,
             "track": {"form": f, "spoken": track["spoken"], "audio_name": track["audio"]["name"], "profile": track.get("profile", "standard"),
                       "intake_story": story, "instruction_story": track["instruction_story"],
                       "dry": bool(track.get("dry")), "on_questions": on_questions},
             "anchors": anchors, "entries": dict(sorted(entries.items())), "files": sorted(file_paths),
             "messages": {"recorded": RECORDED, "audio": AUDIO_ABSENT}, "target_s": TARGET_S}
    put("rec/index.json", json.dumps(index, ensure_ascii=False, indent=0).encode())
    put("README.md", readme(track, call_id, built[:10]).encode())
    total = sum(len(d) for d in written.values())
    final = rec.get("final_view") or {}
    out = final.get("outcome") or {}
    info = {"built_utc": built, "recorded_utc": track["recorded_utc"], "call_id": call_id, "files": len(written) + 1, "bytes": total,
            "routes": len(entries), "bodies": len(bodies), "anchors": [{k: a[k] for k in ("what", "t", "busy_s", "speed")} for a in anchors],
            "static_files": assets, "linked_files": len(file_paths), "outcome": {k: out.get(k) for k in ("status", "passed", "label", "wall_s", "first_checked_wall_s")},
            "recording_page_errors": rec.get("page_errors")}
    (OUT / "build.json").write_text(json.dumps(info, indent=1) + "\n")
    if problems:
        raise SystemExit("private-data scan failed:\n  " + "\n  ".join(sorted(set(problems))[:40]))
    if total > LIMIT_BYTES:
        raise SystemExit(f"build is {total / 1e6:.1f} MB, over the {LIMIT_BYTES / 1e6:.0f} MB limit")
    log(f"wrote {OUT.relative_to(ROOT)}: {len(written) + 1} files, {total / 1e6:.2f} MB, {len(entries)} routes, anchors "
        + ", ".join(f"{a['what']} x{a['speed']}" for a in anchors))
    return info


# ---------------------------------------------------------------- a server of our own (record without --base)
def launch(port: int, env_file: str | None, log=print) -> subprocess.Popen:
    env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}
    env.pop("VIRTUAL_ENV", None)
    cmd = [str(ROOT / ".venv/bin/python"), "-m", "app", "--functional", "--port", str(port)] + (["--env-file", env_file] if env_file else [])
    with open(HERE / "app.log", "w") as logf:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    try:  # from here on, any way out but a ready app stops the process group this call created (code14 P2-1)
        (HERE / "app.pid").write_text(f"{proc.pid}\n")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise SystemExit(f"app exited {proc.returncode}; see {HERE / 'app.log'}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{API}status", timeout=2) as r:
                    if r.status == 200:
                        log(f"app ready on port {port}")
                        return proc
            except OSError:
                pass
            time.sleep(2)
        raise SystemExit("app did not start in 5 min")
    except BaseException:
        stop(proc)
        raise


def stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(30)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    (HERE / "app.pid").unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("record")
    r.add_argument("--base", help="a running `python -m app --functional` with a key and paid calls accepted; default: start one")
    r.add_argument("--app-port", type=int, default=9032, help="port for the app this command starts when --base is not given")
    r.add_argument("--env-file", help="the private env file (MODEL_API_KEY, paid-call consent) for the app this command starts")
    r.add_argument("--ship", default="feeder_500")
    r.add_argument("--size", default="medium")
    r.add_argument("--fill", type=int, default=32)
    r.add_argument("--generator", default="random_yard")
    r.add_argument("--port", default="laem_chabang_like")
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--say", required=True)
    r.add_argument("--clip-name", default="radio-instruction.wav")
    r.add_argument("--dry", action="store_true", help="no paid calls: the generated load list and a typed instruction (--say)")
    r.add_argument("--edit", help="the planner's correction of the transcript, used if the reader refuses it as heard")
    r.add_argument("--resume", help="a call whose first session stopped at a refused reading: continue it from the correction")
    e = sub.add_parser("emit")
    e.add_argument("--call")
    args = ap.parse_args()
    if args.cmd == "record":
        proc = None if args.base else launch(args.app_port, args.env_file)
        args.base = args.base or f"http://127.0.0.1:{args.app_port}"
        try:
            record(args)
        finally:
            if proc:
                stop(proc)
    else:
        emit(getattr(args, "call", None))


if __name__ == "__main__":
    main()
