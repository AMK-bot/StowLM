"""Walk the 'Play it yourself' copy the way a visitor would, with no app running.

    env -u UV_CACHE_DIR uv run --offline --with playwright python experiments/mvp/ui/static_play/verify_play.py [--port 9033]

Serves experiments/mvp/video/ with `python -m http.server` (its own PID in static_play/serve.pid), so the copy sits under a
sub-path (/site/play/), and walks the recorded track in a fresh headless Chrome with walk.py: Create call, read the papers, the
spoken instruction, read, confirm, Generate plan, the review tabs. Checks: no console or page errors, no failed or outside
requests during the walk, the recorded-run strip and "Synthetic ships and cargo, simulated port" on every step, the header
badge, the form showing the recorded inputs, the audio note, the recorded outcome and its measured times, and that every
change a visitor might try (ship, seed, New seed, a typed sentence, a file, browser recording, another papers choice, the
engine profile, Plan again, the API key) runs nothing and shows the recorded-run message with the install link. Screenshots go
to static_play/screens/. Writes verify.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SERVE_ROOT = ROOT / "experiments/mvp/video"
PLAY = SERVE_ROOT / "site/play"
SCREENS = HERE / "screens"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
INSTALL = "https://github.com/AMK-bot/StowLM#setup-and-run"
SYNTH = "Synthetic ships and cargo, simulated port"
sys.path.insert(0, str(HERE))
import walk  # noqa: E402


def serve(port: int) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=SERVE_ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    (HERE / "serve.pid").write_text(f"{proc.pid}\n")
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/site/play/index.html", timeout=1)
            return proc
        except OSError:
            time.sleep(0.1)
    raise SystemExit("static server did not start")


NOTE_JS = """(install) => { const n = document.getElementById('play-note'); if (!n || n.hidden) return null;
  const a = n.querySelector('a'); return { kind: n.dataset.kind, text: n.innerText.replace(/\\s+/g, ' ').trim(), link: a ? a.href : null, install: a && a.href === install }; }"""


async def main(port: int) -> dict:
    from playwright.async_api import async_playwright
    base = f"http://127.0.0.1:{port}/site/play/"
    index = json.loads((PLAY / "rec/index.json").read_text())
    build = json.loads((PLAY / "build.json").read_text())
    track = {"form": index["track"]["form"], "dry": index["track"].get("dry", False), "typed": index["track"]["spoken"],
             "on_questions": index["track"].get("on_questions")}
    SCREENS.mkdir(exist_ok=True)
    for old in SCREENS.glob("*.png"):
        old.unlink()
    out = {"errors": [], "failed": [], "external": [], "steps": {}, "attempts": {}}
    phase = {"flow": True}

    async def page_of(browser):
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda m: out["errors"].append(m.text) if m.type == "error" and phase["flow"] else None)
        page.on("pageerror", lambda e: out["errors"].append(str(e)))
        page.on("requestfailed", lambda r: out["failed"].append(f"{r.method} {r.url} {r.failure}"))
        page.on("response", lambda r: out["failed"].append(f"{r.request.method} {r.url} {r.status}") if r.status >= 400 else None)
        page.on("request", lambda r: out["external"].append(r.url) if not r.url.startswith(base) and not r.url.startswith(("data:", "blob:")) else None)  # nothing leaves the folder
        return page

    async def labels(page, name):
        got = await page.evaluate("""(synth) => {
          const b = document.getElementById('play-banner'), r = b ? b.getBoundingClientRect() : null;
          const badge = document.querySelector('.pl-demo-badge');
          return { banner: !!r && r.height > 20 && r.top >= 0 && /Recorded run of the Functional app/.test(b.innerText),
                   synthetic: !!b && b.innerText.includes(synth), badge: badge ? badge.textContent : '',
                   speed: (document.getElementById('play-speed') || {}).hidden === false ? document.getElementById('play-speed').innerText : '' };
        }""", SYNTH)
        out["steps"][name] = got
        await page.screenshot(path=str(SCREENS / f"{name}.png"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, headless=True)
        # 1) the recorded track, start to finish
        page = await page_of(browser)
        t0 = time.monotonic()
        seen = await walk.flow(page, base, track, mode="play", shot=lambda n: labels(page, n), slow=0.2)
        out["walk_s"] = round(time.monotonic() - t0, 1)
        out["seen"] = seen
        out["served"] = await page.evaluate("() => window.__play.served.length")
        out["refused_in_flow"] = await page.evaluate("() => window.__play.refused.slice()")
        out["misses_in_flow"] = await page.evaluate("() => window.__play.misses.slice()")
        out["anchors_done"] = await page.evaluate("() => window.__play.anchor + 1")
        out["linked"] = []
        for href in (seen.get("package_links") or [])[:12]:
            r = await page.request.get(base + href)
            out["linked"].append([href, r.status, r.headers.get("content-type", "")])
        # the package's printable bay plan, opened as a visitor would (a page of its own, under the same policy)
        bay = next((h for h in (seen.get("package_links") or []) if h.endswith("bay-plan.html")), None)
        if bay:
            bp = await page_of(browser)
            await bp.goto(base + bay)
            await asyncio.sleep(1.5)
            out["bay_plan_title"] = await bp.title()
            await bp.screenshot(path=str(SCREENS / "15b-package-bay-plan.png"))
            await bp.close()
        phase["flow"] = False
        failed_in_flow = list(out["failed"])

        # 2) after the run: what a visitor might try on the plan
        attempts = out["attempts"]

        async def attempt(name, js, settle=0.8):
            await page.evaluate("() => { const n = document.getElementById('play-note'); if (n) n.hidden = true; }")
            before = await page.evaluate("() => window.__play.anchor")
            await page.evaluate(js)
            await asyncio.sleep(settle)
            attempts[name] = {"note": await page.evaluate(NOTE_JS, INSTALL), "anchor_moved": (await page.evaluate("() => window.__play.anchor")) != before}
        await walk.click(page, '#pl-tabs [data-tab="plan"]')
        await asyncio.sleep(1)
        await attempt("engine_profile_live", "() => { const b = document.querySelector('[data-do=\"profile\"][data-profile=\"live\"]'); if (b) b.click(); }")
        await attempt("plan_again", "() => { const b = document.querySelector('[data-do=\"plan-again\"]'); if (b) { b.disabled = false; b.click(); } }", 1.5)
        await attempt("no_instruction", "() => document.querySelector('[data-action=\"no-instruction\"]').click()")
        await attempt("api_key_chip", "() => { const c = document.querySelector('.pl-key-chip'); if (c) c.click(); }")
        attempts["api_key_chip"]["key_panel_opened"] = await page.evaluate("() => { const k = document.getElementById('pl-key-panel'); return !!k && !k.hidden; }")
        attempts["api_key_chip"]["chip_text"] = await page.evaluate("() => { const c = document.querySelector('.pl-key-chip'); return c ? getComputedStyle(c, '::after').content : ''; }")
        await labels(page, "16-api-key-needs-none")
        await page.close()

        # 3) a fresh page: every input before and after Create call
        page = await page_of(browser)
        await page.goto(base)
        await walk.until(page, "(f) => { const v = (n) => (document.querySelector(`.pl-sec[data-stage=\"voyage\"] [data-f=\"${n}\"]`) || {}).value; return v('seed') === String(f.seed); }", 20, arg=track["form"])
        await asyncio.sleep(1)
        out["fresh_form"] = await walk.form_values(page)
        await attempt("change_ship", """() => { const s = document.querySelector('[data-f="ship"]'); s.focus(); s.value = 'feeder_800'; s.dispatchEvent(new Event('input', {bubbles: true})); s.dispatchEvent(new Event('change', {bubbles: true})); }""")
        attempts["change_ship"]["ship_after"] = (await walk.form_values(page))["ship"]
        await labels(page, "17-change-input-refused")
        await page.focus('[data-f="seed"]')
        await page.keyboard.type("12")
        await asyncio.sleep(0.5)
        attempts["type_seed"] = {"note": await page.evaluate(NOTE_JS, INSTALL), "seed_after": (await walk.form_values(page))["seed"]}
        await attempt("new_seed", "() => document.querySelector('[data-do=\"seed\"]').click()")
        attempts["new_seed"]["seed_after"] = (await walk.form_values(page))["seed"]
        await walk.click(page, '[data-do="create"]', '.pl-sec[data-stage="voyage"]')
        await walk.until(page, "() => { const b = document.querySelector('[data-action=\"use-generated-load-list\"]'); return b && !b.disabled; }", 60)
        papers_choice = "read-generated" if track["dry"] else "use-generated-load-list"  # the choice the recording didn't make
        await walk.open_section(page, "intake")
        # a control the app itself disables sends nothing; enable it here so the shim's own guard is what's tested
        await attempt("other_papers_choice", f"() => {{ const b = document.querySelector('[data-action=\"{papers_choice}\"]'); b.disabled = false; b.click(); }}", 1.5)
        await attempt("upload_papers", "() => { const f = document.querySelector('[data-live-field=\"files\"]'); if (f) { f.disabled = false; f.click(); } }")
        await walk.open_section(page, "instruct")
        await attempt("record_in_browser", "() => document.querySelector('[data-action=\"record\"]').click()")
        await attempt("upload_wav", "() => { const f = document.querySelector('[data-live-audio]'); f.disabled = false; f.click(); }")
        await page.focus("[data-live-sentence]")
        await page.keyboard.type("close bay 3")
        await asyncio.sleep(0.5)
        attempts["type_instruction"] = {"note": await page.evaluate(NOTE_JS, INSTALL),
                                        "text_after": await page.evaluate("() => document.querySelector('[data-live-sentence]').value")}
        await labels(page, "18-instruction-change-refused")
        await page.close()
        await browser.close()

    final = build.get("outcome") or {}
    attempts_ok = {k: bool(v.get("note") and v["note"].get("install") and not v.get("anchor_moved")) for k, v in attempts.items()}
    attempts_ok["api_key_chip"] = bool(attempts["api_key_chip"]["note"] and attempts["api_key_chip"]["note"]["kind"] == "key"
                                       and attempts["api_key_chip"]["note"]["install"] and not attempts["api_key_chip"]["key_panel_opened"])
    wall = final.get("wall_s")
    checks = {
        "no_console_or_page_errors": not out["errors"],
        "no_failed_requests_in_flow": not failed_in_flow,
        "same_origin_only": not out["external"],
        "every_action_replayed": out["anchors_done"] == len(index["anchors"]) and not out["refused_in_flow"] and not out["misses_in_flow"],
        "labels_every_step": all(v["banner"] and v["synthetic"] for v in out["steps"].values()),
        "badge_says_recorded": all(v["badge"] == "Recorded run" for v in out["steps"].values()),
        "form_shows_recorded_inputs": all(str(out["seen"]["form"][k]) == str("" if v is None else v) for k, v in track["form"].items()),
        "audio_not_included_note": track["dry"] or "Audio not included in this copy" in out["seen"].get("audio_label", ""),
        "outcome_shown": final.get("status") != "checked_plan" or "Passes the full checker" in (out["seen"].get("checks", "") + out["seen"].get("result", "")),
        "measured_wall_time_shown": wall is None or f"{wall:.1f}" in out["seen"].get("checks", ""),
        "linked_files_served": all(s == 200 for _, s, _ in out["linked"]) and len(out["linked"]) >= 5 and "bay plan" in out.get("bay_plan_title", "").lower(),
        "every_change_refused_with_install_link": all(attempts_ok.values()),
        "inputs_unchanged": out["fresh_form"]["ship"] == track["form"]["ship"] and attempts["change_ship"]["ship_after"] == track["form"]["ship"]
                            and attempts["type_seed"]["seed_after"] == str(track["form"]["seed"]) and attempts["new_seed"]["seed_after"] == str(track["form"]["seed"])
                            and attempts["type_instruction"]["text_after"] == "",
    }
    out["attempts_ok"] = attempts_ok
    out["checks"] = checks
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9033)
    args = ap.parse_args()
    proc = serve(args.port)
    try:
        result = asyncio.run(main(args.port))
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        (HERE / "serve.pid").unlink(missing_ok=True)
    (HERE / "verify.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(result["checks"], indent=1))
    print("attempts:", json.dumps(result["attempts_ok"]))
    print("errors:", result["errors"][:10], "failed:", result["failed"][:10], "external:", result["external"][:5])
    sys.exit(0 if all(result["checks"].values()) else 1)
