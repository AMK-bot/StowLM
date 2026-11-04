"""The Functional app's planner walk (/planner?live=1), clicked through in a Playwright page: shared by build.py record (against
the live app, every step real) and verify_play.py (against the static playback, every answer from the recording).

One page, the planner's own controls, in the order the recorded track used them:

  1 Ship & call   the form set to the track's inputs, then Create call
  2 Cargo         Read these papers with Muse Spark
  3 Instructions  the spoken instruction (a WAV, labelled generated voice) → Transcribe with Muse Voice → Read with StowLM
                  Reader → Confirm this reading
  4 Generate plan Engine 0.13.0, then the review tabs (Checks, Stress test, Ask copilot, Export)

    track  {"form": {...}, "audio": {"path": <wav, record mode only>, "name": ...}}
    mode   "record": fills the form and uploads the WAV itself; "play": the shim does both (it holds the track) and the walk
           checks they are there
    shot   optional async screenshot hook, called once per step with a name
"""
from __future__ import annotations

import asyncio
import time

FORM_ORDER = ("seed", "ship", "size_class", "fill_pct", "generator", "port")  # the seed first: each select redraws the seed hint


async def until(page, js: str, timeout_s: float, every: float = 0.5, arg=None, what: str = "") -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if await page.evaluate(js, arg):
                return True
        except Exception:  # noqa: BLE001 - a navigation or re-render between polls
            pass
        await asyncio.sleep(every)
    raise TimeoutError(f"waited {timeout_s:.0f} s for {what or js[:80]}")


async def click(page, selector: str, scope: str = "") -> bool:
    """A DOM click on the first visible, enabled match. True if something was clicked."""
    return await page.evaluate("""([sel, scope]) => {
      const root = scope ? document.querySelector(scope) : document;
      if (!root) return false;
      const el = [...root.querySelectorAll(sel)].find((e) => { const r = e.getBoundingClientRect(); return !e.disabled && !e.hidden && r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; });
      if (!el) return false;
      el.click(); return true;
    }""", [selector, scope])


async def open_section(page, stage: str) -> None:
    await page.evaluate("""(stage) => { const s = document.querySelector(`.pl-sec[data-stage="${stage}"]`);
      if (s && !s.classList.contains('open')) s.querySelector('.pl-sec-head').click();
      if (s) s.scrollIntoView({ block: 'start' }); }""", stage)
    await asyncio.sleep(0.6)


async def text_of(page, selector: str) -> str:
    return await page.evaluate("(sel) => { const n = document.querySelector(sel); return n ? n.innerText.replace(/\\s+/g, ' ').trim() : ''; }", selector)


async def section_state(page, stage: str) -> str:
    return await page.evaluate("(s) => { const n = document.querySelector(`.pl-sec[data-stage=\"${s}\"]`); return n ? n.dataset.state : ''; }", stage)


async def fill_form(page, form: dict) -> None:
    """The planner's entries in 1 Ship & call, one control at a time (the form re-renders after each)."""
    for name in FORM_ORDER:
        if name not in form:
            continue
        value = "" if form[name] is None else str(form[name])
        await page.evaluate("""([name, value]) => {
          const el = document.querySelector(`.pl-sec[data-stage="voyage"] [data-f="${name}"]`);
          if (!el) throw new Error('no form field ' + name);
          el.value = value;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          if (el.tagName === 'SELECT') el.dispatchEvent(new Event('change', { bubbles: true }));
        }""", [name, value])
        await asyncio.sleep(0.25)


async def form_values(page) -> dict:
    return await page.evaluate("""(names) => { const out = {};
      for (const n of names) { const el = document.querySelector(`.pl-sec[data-stage="voyage"] [data-f="${n}"]`); out[n] = el ? el.value : null; }
      return out; }""", list(FORM_ORDER))


async def flow(page, url: str, track: dict, *, mode: str, shot=None, log=print, slow: float = 1.0, resume_at: str | None = None) -> dict:
    """Walk the track. Returns what happened at each step (texts the checks read). slow scales the waits for real work."""
    seen: dict = {}
    shot = shot or (lambda name: asyncio.sleep(0))
    long = 3600 * slow  # real work: Spark reading every booking, the engine's 1,800 s deadline plus the checker
    if resume_at == "edit":
        # a second recording session on the same call: the reader refused the transcript, the planner corrects it
        await page.goto(url)
        await until(page, "() => { const t = document.querySelector('[data-live-sentence]'); return !!(t && t.value.trim()) && !!document.querySelector('[data-live-reading-card]'); }",
                    120, what="the restored call with its refused reading")
        await open_section(page, "instruct")
        seen["transcript"] = await page.evaluate("() => document.querySelector('[data-live-sentence]').value")
    else:
        await page.goto(url)
        await until(page, "() => !!document.querySelector('.pl-sec[data-stage=\"voyage\"] [data-f=\"ship\"]')", 60, what="the call form")
        await asyncio.sleep(1.0)
        if mode == "record":
            await fill_form(page, track["form"])
        else:
            await until(page, "(f) => { const v = (n) => (document.querySelector(`.pl-sec[data-stage=\"voyage\"] [data-f=\"${n}\"]`) || {}).value;"
                              " return Object.keys(f).every((k) => String(v(k)) === String(f[k] == null ? '' : f[k])); }", 20, arg=track["form"],
                        what="the recorded form entries")
        seen["form"] = await form_values(page)
        await shot("01-call-form")

        # 1 Ship & call
        assert await click(page, '[data-do="create"]', '.pl-sec[data-stage="voyage"]'), "no Create call button"
        log("create call")
        await until(page, "() => !!document.querySelector('.pl-sec[data-stage=\"voyage\"] .pl-call-card')", 300 * slow, what="the generated call")
        await asyncio.sleep(1.0)
        seen["call_card"] = await text_of(page, '.pl-sec[data-stage="voyage"] .pl-sec-panel')
        await open_section(page, "voyage")
        await shot("02-call-created")

        # 2 Cargo: the call's synthetic papers, read by Muse Spark
        await open_section(page, "intake")
        papers = "use-generated-load-list" if track.get("dry") else "read-generated"  # dry: a no-model rehearsal of the plumbing
        await until(page, f"() => {{ const b = document.querySelector('[data-action=\"{papers}\"]'); return b && !b.disabled; }}", 120, what=papers)
        await shot("03-cargo-papers")
        assert await click(page, f'[data-action="{papers}"]'), f"no {papers} button"
        log("read papers with Muse Spark" if papers == "read-generated" else "use the generated load list")
        await asyncio.sleep(4 * min(slow, 1))
        await shot("04-cargo-reading")
        if mode == "play":
            # the recorded planner's next choices in 2 Cargo, as the recording names them: retry a failed booking read, or set
            # Muse Spark's reading aside for the generated load list when its questions can't be closed by a cargo answer
            deadline = time.monotonic() + long
            while await section_state(page, "intake") != "done" and time.monotonic() < deadline:
                nxt = await page.evaluate("() => { const I = window.__play.I, a = I && I.anchors[window.__play.anchor + 1]; return a ? [a.what, a.body || {}] : null; }")
                if nxt and nxt[0] == "papers" and nxt[1].get("use_generated_papers") and await click(page, '[data-action="retry-failed-bookings"]'):
                    seen["retry"] = True
                    await shot("05-cargo-retry")
                    log("retry the failed booking read")
                elif nxt and nxt[0] == "papers" and nxt[1].get("use_generated_load_list"):
                    sel = '.pl-live-intake-questions [data-action="use-generated-load-list"]'
                    if await page.evaluate("(s) => !!document.querySelector(s + ':not([disabled])')", sel):
                        await asyncio.sleep(1.5)
                        seen["questions"] = await text_of(page, ".pl-live-intake-questions")
                        await shot("05-cargo-questions")
                        await click(page, sel)
                        log("questions: use the generated load list")
                await asyncio.sleep(1.0)
        await until(page, "() => (document.querySelector('.pl-sec[data-stage=\"intake\"]') || {dataset: {}}).dataset.state === 'done'", long, every=1.0,
                    what="the load list from the papers")
        await asyncio.sleep(1.5)
        await open_section(page, "intake")
        seen["intake"] = await text_of(page, '.pl-sec[data-stage="intake"] .pl-sec-panel')
        await shot("05-cargo-ready")

        # 3 Instructions: the spoken instruction (dry: a typed one)
        await open_section(page, "instruct")
        if track.get("dry"):
            if mode == "record":
                await page.evaluate("(s) => { const t = document.querySelector('[data-live-sentence]'); t.value = s; t.dispatchEvent(new Event('input', { bubbles: true })); }", track["typed"])
            await until(page, "(s) => { const t = document.querySelector('[data-live-sentence]'); const b = document.querySelector('[data-action=\"read\"]'); return t && t.value === s && b && !b.disabled; }",
                        120, arg=track["typed"], what="the typed instruction")
            seen["transcript"] = track["typed"]
            await shot("07-typed-instruction")
        elif mode == "record":
            await page.set_input_files("[data-live-audio]", track["audio"]["path"])
            await until(page, "() => { const o = document.querySelector('[data-live-generated-voice-option]'); return o && !o.hidden; }", 20, what="the WAV accepted")
            await page.evaluate("() => { const c = document.querySelector('[data-live-generated-voice]'); if (!c.checked) c.click(); }")
        if not track.get("dry"):
            await until(page, "() => { const b = document.querySelector('[data-action=\"transcribe\"]'); const c = document.querySelector('[data-live-generated-voice]');"
                              " return b && !b.disabled && c && c.checked; }", 60, what="Transcribe with the generated voice ticked")
            seen["audio_label"] = await text_of(page, '.pl-instruct-radio')
            await shot("06-spoken-instruction")
            assert await click(page, '[data-action="transcribe"]'), "no Transcribe button"
            log("transcribe with Muse Voice")
            await until(page, "() => { const t = document.querySelector('[data-live-sentence]'); const b = document.querySelector('[data-action=\"read\"]'); return t && t.value.trim() && b && !b.disabled; }",
                        600 * slow, what="the transcript")
            seen["transcript"] = await page.evaluate("() => document.querySelector('[data-live-sentence]').value")
            await asyncio.sleep(1.0)
            await shot("07-transcript")
    reads = 0
    while True:
        if not (resume_at == "edit" and reads == 0):
            assert await click(page, '[data-action="read"]'), "no Read button"
            log("read with StowLM Reader")
        reads += 1
        await until(page, "() => !!document.querySelector('[data-live-reading-card]')", 900 * slow, what="the reader's answer")
        await asyncio.sleep(1.0)
        if await page.evaluate("() => { const b = document.querySelector('[data-action=\"confirm\"]'); return !!b && !b.disabled; }"):
            break
        # refused, or not a valid rule: the planner corrects the transcript (the app labels it editable) and reads it again
        seen.setdefault("refused", []).append(await text_of(page, "[data-live-reading-card]"))
        await shot("08a-reader-refused")
        if reads > 3:
            raise RuntimeError("the reader refused three readings")
        if mode == "record":
            if not track.get("edit"):
                raise RuntimeError("the reader refused the transcript and the track names no planner correction (--edit)")
            await page.evaluate("(s) => { const t = document.querySelector('[data-live-sentence]'); t.value = s; t.dispatchEvent(new Event('input', { bubbles: true })); }", track["edit"])
        else:
            await until(page, "() => { const I = window.__play.I, a = I && I.anchors[window.__play.anchor + 1]; const t = document.querySelector('[data-live-sentence]');"
                              " return !!(a && a.what === 'read' && t && t.value === a.body.sentence); }", 60, what="the planner's corrected transcript")
        seen["edited"] = await page.evaluate("() => document.querySelector('[data-live-sentence]').value")
        await asyncio.sleep(0.8)
        await shot("08b-transcript-corrected")
    seen["reading"] = await text_of(page, "[data-live-reading-card]")
    await shot("08-reader-proposal")
    assert await click(page, '[data-action="confirm"]'), "no Confirm button"
    log("confirm the reading")
    await until(page, "() => (document.querySelector('.pl-sec[data-stage=\"instruct\"]') || {dataset: {}}).dataset.state === 'done'", 120, what="the confirmed reading")
    await asyncio.sleep(1.0)
    await open_section(page, "instruct")
    seen["confirmed"] = await text_of(page, "[data-live-reading-card]")
    await shot("09-reading-confirmed")

    # 4 Generate plan: Engine 0.13.0 and the full checker
    await until(page, "() => { const b = document.getElementById('pl-generate'); return b && !b.disabled; }", 120, what="Generate plan")
    seen["hero"] = await text_of(page, "#pl-hero")
    await shot("10-ready-to-generate")
    assert await click(page, "#pl-generate"), "no Generate plan button"
    log("generate plan")
    await asyncio.sleep(6 * min(slow, 1))
    seen["planning"] = await text_of(page, "#pl-right")
    await shot("11-planning")
    await until(page, "() => (document.querySelector('#pl-tabs [data-tab=\"plan\"]') || {dataset: {}}).dataset.state === 'done'", long, every=1.0,
                what="the engine run and the full checker")
    await asyncio.sleep(12)  # the plan module plays the crane moves (at most 10 s), then the meters
    seen["result"] = await text_of(page, "#pl-result")
    seen["checks"] = await text_of(page, "#pl-right")
    await shot("12-plan-checked")
    for tab in ("stress", "copilot", "handover"):
        await click(page, f'#pl-tabs [data-tab="{tab}"]')
        await asyncio.sleep(3)
        seen[tab] = await text_of(page, "#pl-right")
        if tab == "handover":
            seen["package_links"] = await page.evaluate("() => [...document.querySelectorAll('#pl-right a[href]')].map((a) => a.getAttribute('href'))")
        await shot({"stress": "13-stress-not-live", "copilot": "14-copilot-not-live", "handover": "15-export"}[tab])
    await click(page, '#pl-tabs [data-tab="plan"]')
    await asyncio.sleep(1.5)
    await open_section(page, "intake")
    seen["paper_links"] = await page.evaluate("() => [...document.querySelectorAll('.pl-sec[data-stage=\"intake\"] a[href]')].map((a) => a.getAttribute('href'))")
    return seen
