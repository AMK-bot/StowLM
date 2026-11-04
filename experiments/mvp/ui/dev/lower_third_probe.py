"""Dev-only: play film shots and check, every quarter second, that no map label is drawn under the lower third.

    env -u UV_CACHE_DIR uv run --offline --with playwright python experiments/mvp/ui/dev/lower_third_probe.py http://127.0.0.1:PORT [--shots 1,3,12]

Each shot plays once in real time in headless system Chrome (/stage?video=1&shot=N, final mode). Every 250 ms the page measures the
lower third's text box (#dir-lower, "Synthetic ships and cargo, simulated port") and every visible map label (SVG <text> inside a
.dc-map), clipped by any ancestor that hides overflow, and records each overlap. Other visible text that touches the lower third
is listed separately, for information. Exit status 1 if a map label ever overlaps it, or the director reports an error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

PROBE = """() => {
  const low = document.getElementById('dir-lower');
  if (!low || low.hidden || !low.firstChild) return {t: (window.__director || {}).t, lower: null, map: [], other: []};
  const range = document.createRange(); range.selectNodeContents(low);
  const L = range.getBoundingClientRect();
  const shown = (el) => { for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const s = getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0) return false; } return true; };
  const clip = (el, r) => { let x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom;
    for (let e = el.parentElement; e; e = e.parentElement) { const s = getComputedStyle(e); if (s.overflow !== 'visible' || s.overflowX !== 'visible' || s.overflowY !== 'visible') { const c = e.getBoundingClientRect(); x0 = Math.max(x0, c.left); y0 = Math.max(y0, c.top); x1 = Math.min(x1, c.right); y1 = Math.min(y1, c.bottom); } }
    return {x0, y0, x1, y1}; };
  const hit = (c) => c.x1 > c.x0 && c.y1 > c.y0 && c.x0 < L.right && c.x1 > L.left && c.y0 < L.bottom && c.y1 > L.top;
  const map = [], other = [];
  for (const t of document.querySelectorAll('.dc-map text')) { if (!shown(t)) continue; const c = clip(t, t.getBoundingClientRect()); if (hit(c)) map.push({text: t.textContent, rect: [c.x0, c.y0, c.x1, c.y1].map(Math.round)}); }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement; if (!n.textContent.trim() || !el || low.contains(el) || el.closest('.dc-map') || !shown(el)) continue;
    const r = document.createRange(); r.selectNodeContents(n); const c = clip(el, r.getBoundingClientRect());
    if (hit(c)) other.push(n.textContent.trim().slice(0, 60));
  }
  return {t: (window.__director || {}).t, lower: [L.left, L.top, L.right, L.bottom].map(Math.round), map, other: [...new Set(other)]};
}"""


async def probe_shot(browser, base: str, shot: str) -> dict:
    ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, bypass_csp=True)
    page = await ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(f"{base}/stage?video=1&shot={shot}")
    await page.wait_for_function("window.__director && window.__director.started", timeout=600000)
    samples, map_hits, other = 0, [], set()
    while not await page.evaluate("window.__director.done"):
        got = await page.evaluate(PROBE)
        samples += 1
        if got["lower"] is None:
            map_hits.append({"t": got["t"], "problem": "no lower third"})
        for m in got["map"]:
            map_hits.append({"t": round(got["t"] or 0, 2), **m})
        other.update(got["other"])
        await page.wait_for_timeout(250)
    director = await page.evaluate("window.__director.errors")
    await ctx.close()
    return {"shot": shot, "samples": samples, "map_overlaps": map_hits, "other_text_touching": sorted(other), "director_errors": director, "page_errors": errors}


async def main() -> int:
    from playwright.async_api import async_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--shots", default="", help="comma-separated shot ids (default: every shot on the page)")
    a = ap.parse_args()
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, headless=True, args=["--hide-scrollbars"])
        shots = [s.strip() for s in a.shots.split(",") if s.strip()]
        if not shots:
            page = await browser.new_page()
            await page.goto(f"{a.base}/stage?video=1&t=0")
            await page.wait_for_function("window.__director && window.__director.shots.length", timeout=600000)
            shots = [str(s["id"]) for s in await page.evaluate("window.__director.shots")]
            await page.close()
        results = [await probe_shot(browser, a.base, s) for s in shots]
        await browser.close()
    print(json.dumps(results, indent=1, ensure_ascii=False))
    bad = [r["shot"] for r in results if r["map_overlaps"] or r["director_errors"] or r["page_errors"]]
    print(f"lower-third probe: {len(results)} shots, {sum(r['samples'] for r in results)} samples, "
          f"map-label overlaps in {bad or 'none'}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
