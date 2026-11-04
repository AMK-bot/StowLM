"""Draft recording of the StowLM video shots (stage director, /stage?video=1) with Playwright + system Chrome.

    uv run --with playwright python experiments/mvp/ui/dev/record_shots.py http://127.0.0.1:8851 [--shots 1,5] \
        [--stills-only] [--final] [--at 3,7.5] [--clip x] [--out experiments/mvp/ui/video-draft]

Shot list, durations and stills come from the page (window.__director.shots), so they follow the variant chosen in
app/static/stage-shots.js. --clip x records the X clip instead: the page's own portrait layout of shots 1 and 9 at
1080x1920 (&clip=x), one take per the page's shot list, or --whole for one continuous take.

Per shot: shot-NN.webm (1920x1080 VP9, 30 fps, DRAFT watermark unless --final) and shot-NN-mid.png (a frozen still
at the shot's still_t, default mid-shot, via &t=; plus shot-NN-<t>s.png for --at times). Writes timing.json: per
shot the configured duration, the director's measured wall time and the webm's size.

How the video is made (no ffmpeg on this machine; Playwright's own recorder needs a downloaded ffmpeg): the page
captures its own tab with getDisplayMedia (Chrome flag --auto-accept-this-tab-capture, headless) into a
MediaRecorder, started before the director's clock (&hold=1, then Space) and stopped when the director reports
done. Draft quality only; the final take uses a screen recorder on /stage?video=1 (see RECORDING.md).
One browser at a time, one page at a time (1-core budget).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ROOT = Path(__file__).resolve().parents[4]
CHROME_ARGS = ["--hide-scrollbars", "--auto-accept-this-tab-capture"]

START_REC = """async () => {
  const s = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: 30, width: 1920, height: 1080}, audio: false, preferCurrentTab: true});
  const rec = new MediaRecorder(s, {mimeType: 'video/webm;codecs=vp9', videoBitsPerSecond: 6e6});
  const chunks = []; rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  window.__rec = {rec, chunks, s}; rec.start(1000);
  return s.getVideoTracks()[0].getSettings();
}"""
STOP_REC = """async () => {
  const {rec, chunks, s} = window.__rec;
  await new Promise((r) => { rec.onstop = r; rec.stop(); });
  s.getTracks().forEach((t) => t.stop());
  const buf = new Uint8Array(await new Blob(chunks, {type: 'video/webm'}).arrayBuffer());
  let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(bin);
}"""


VIEWPORT = {"width": 1920, "height": 1080}


async def shot_list(browser, base: str, query: str) -> tuple[list[dict], str]:
    """The resolved shots and variant, as the director sees them (no JS is reimplemented here)."""
    ctx = await browser.new_context(viewport=VIEWPORT, bypass_csp=True)
    page = await ctx.new_page()
    await page.goto(f"{base}/stage?video=1&t=0{query}")
    await page.wait_for_function("window.__director && window.__director.shots.length", timeout=120000)
    shots, variant = await page.evaluate("[window.__director.shots, window.__director.variant]")
    await ctx.close()
    return shots, variant


async def main() -> int:
    from playwright.async_api import async_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="server root, e.g. http://127.0.0.1:8851")
    ap.add_argument("--shots", help="comma-separated shot ids (default all)")
    ap.add_argument("--stills-only", action="store_true")
    ap.add_argument("--video-only", action="store_true")
    ap.add_argument("--final", action="store_true", help="no DRAFT watermark / chip (placeholders dropped)")
    ap.add_argument("--at", default="", help="extra still times within each shot, comma-separated seconds")
    ap.add_argument("--variant", default="", help="rehearsal override of the variant in stage-shots.js")
    ap.add_argument("--clip", default="", choices=["", "x"], help="x = the 1080x1920 X clip (shots 1, 9)")
    ap.add_argument("--whole", action="store_true", help="also record one continuous take of all shots (whole-take.webm)")
    ap.add_argument("--out", default=str(ROOT / "experiments/mvp/ui/video-draft"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    want = {s.strip() for s in a.shots.split(",")} if a.shots else None
    draft = ("" if a.final else "&draft=1") + (f"&variant={a.variant}" if a.variant else "")
    if a.clip:
        draft += f"&clip={a.clip}"
        VIEWPORT.update(width=1080, height=1920)
    timing, errors = [], []

    def watch(page, sid):
        page.on("pageerror", lambda e: errors.append(f"shot {sid}: {e}"))
        page.on("console", lambda m: errors.append(f"shot {sid}: {m.text}")
                if m.type == "error" and "favicon" not in m.text and "404" not in m.text else None)

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, headless=True, args=CHROME_ARGS)
        shots, variant = await shot_list(browser, a.base, draft)
        print(f"variant {variant}", file=sys.stderr)
        if a.whole:  # one continuous take: the director runs all shots on its own clock, dips included
            ctx = await browser.new_context(viewport=VIEWPORT, bypass_csp=True)
            page = await ctx.new_page()
            watch(page, "whole")
            await page.goto(f"{a.base}/stage?video=1&hold=1{draft}")
            await page.wait_for_function("window.__director && window.__director.ready", timeout=300000)
            await page.wait_for_timeout(800)
            settings = await page.evaluate(START_REC)
            await page.wait_for_timeout(300)
            await page.keyboard.press("Space")
            total = sum(s["duration_s"] for s in shots)
            await page.wait_for_function("window.__director.done", timeout=(total + 120) * 1000, polling=250)
            await page.wait_for_timeout(400)
            data = base64.b64decode(await page.evaluate(STOP_REC))
            d = await page.evaluate("({wall: window.__director.wall_s, errors: window.__director.errors})")
            (out / "whole-take.webm").write_bytes(data)
            errors.extend(f"whole: {e}" for e in d["errors"])
            whole = {"webm": "whole-take.webm", "webm_bytes": len(data), "configured_total_s": total, "director_wall_s": round(d["wall"] or 0, 2),
                     "capture": f"{settings.get('width')}x{settings.get('height')}@{settings.get('frameRate')}", "director_errors": d["errors"]}
            (out / "whole-take.json").write_text(json.dumps(whole, indent=2))
            print(json.dumps(whole), flush=True)
            await ctx.close()
        for s in shots:
            sid = str(s["id"])
            if want and sid not in want:
                continue
            nn = sid.zfill(3 if sid[-1].isalpha() else 2)  # 01, 05a
            row = {"id": s["id"], "title": s["title"], "duration_s": s["duration_s"]}
            # bypass_csp only lets this probe evaluate its own JS; the page code itself never evals
            ctx = await browser.new_context(viewport=VIEWPORT, bypass_csp=True)
            page = await ctx.new_page()
            watch(page, sid)
            if not a.video_only:  # stills: frozen frames, deterministic
                times = [s.get("still_t") or s["duration_s"] / 2] + [float(x) for x in a.at.split(",") if x.strip()]
                for k, t in enumerate(times):
                    await page.goto(f"{a.base}/stage?video=1&shot={sid}&t={t:.2f}{draft}")
                    await page.wait_for_function("window.__director && window.__director.applied", timeout=120000)
                    await page.wait_for_timeout(1500)
                    await page.screenshot(path=str(out / (f"shot-{nn}-mid.png" if k == 0 else f"shot-{nn}-{t:g}s.png")))
            if not a.stills_only:
                await page.goto(f"{a.base}/stage?video=1&shot={sid}&hold=1{draft}")
                await page.wait_for_function("window.__director && window.__director.ready", timeout=120000)
                await page.wait_for_timeout(800)
                settings = await page.evaluate(START_REC)
                await page.wait_for_timeout(300)
                await page.keyboard.press("Space")
                await page.wait_for_function("window.__director.done", timeout=(s["duration_s"] + 90) * 1000, polling=250)
                await page.wait_for_timeout(400)
                data = base64.b64decode(await page.evaluate(STOP_REC))
                d = await page.evaluate("({wall: window.__director.wall_s, errors: window.__director.errors})")
                (out / f"shot-{nn}.webm").write_bytes(data)
                row.update(webm=f"shot-{nn}.webm", webm_bytes=len(data), capture=f"{settings.get('width')}x{settings.get('height')}@{settings.get('frameRate')}",
                           director_wall_s=round(d["wall"] or 0, 2), director_errors=d["errors"])
                errors.extend(f"shot {sid}: {e}" for e in d["errors"])
            await ctx.close()
            timing.append(row)
            print(json.dumps(row), flush=True)
        await browser.close()
    total = sum(r["duration_s"] for r in timing)
    summary = {"shots": timing, "configured_total_s": total, "errors": errors}
    if not a.stills_only:
        summary["director_wall_total_s"] = round(sum(r.get("director_wall_s", 0) for r in timing), 2)
        tj = out / "timing.json"  # merge with earlier passes (re-recording a few shots keeps the others' rows)
        rows = {r["id"]: r for r in (json.loads(tj.read_text())["shots"] if tj.is_file() else [])}
        rows.update({r["id"]: r for r in timing})
        merged = [rows[k] for k in sorted(rows)]
        tj.write_text(json.dumps({"shots": merged, "configured_total_s": sum(r["duration_s"] for r in merged),
                                  "director_wall_total_s": round(sum(r.get("director_wall_s", 0) for r in merged), 2),
                                  "errors_last_pass": errors}, indent=2))
    print(f"configured total {total} s; director wall {summary.get('director_wall_total_s')}; errors: {len(errors)}", file=sys.stderr)
    for e in errors[:20]:
        print("  ", e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
