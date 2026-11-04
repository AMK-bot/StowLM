/* StowLM video director: /stage?video=1 plays the 12-shot v6 script (stage-shots.js) on a fixed timeline. Launch: python -m app --v6.
 *
 *   /stage?video=1              all shots in order (auto-start once the first shot is loaded)
 *   &shot=N                     only shot N (id from stage-shots.js); &shot=5a or 5b: one beat of shot 5
 *   &t=S                        freeze at S seconds (global, or within the shot when &shot is given) for stills
 *   &draft=1                    DRAFT watermark, shot-title chip, PENDING chips for unfilled placeholders
 *   &hold=1                     wait for Space / click before starting (for a screen recorder)
 *   &from=S                     start live playback at S seconds (rehearsal)
 *   &clip=x                     the X clip: shots 1 and 8 in a 1080x1920 portrait layout (viewport 1080x1920)
 *   &variant=v5                 rehearsal of the v5 script, on its own launch
 *
 * A v6 shot with "v6": {chapter, from, to} (to defaults to the chapter's duration_s; the span is fitted to the shot) is drawn by the flow itself: the director boots it (StageV6.boot(true), so video needs
 * no ?v6=1), calls StageV6.seek(chapter, tau) and keeps only its captions, dips and chips. A chapter that can't play is a gap,
 * and the shot's "fallback" from the script plays instead, with its spare seconds handed on (StageStory.applyFallbacks).
 *
 * The director runs in the stage page itself (the server's CSP forbids framing): it prefetches each shot's stage
 * bundle, swaps it in with window.__stage.load(bundle) under the black dip between shots, and drives playback with
 * window.__stage.seek(liftPos) from its own clock, so what is on screen at time t is a pure function of t (wall-clock
 * drift only changes frame timing, never content). Numbers come from the stage bundle of the shot's plan or from
 * stage-fillv6.js (fill-v6.json), or the v6 gate for the hero's own; placeholders are never filled with anything else. Story
 * logic lives in stage-story.js, which the tests run under node. */
(function () {
  'use strict';
  const P = new URLSearchParams(location.search);
  const VIDEO = P.get('video') === '1';

  if (!VIDEO) return;
  // Crisp camera zoom: render the stage canvases at 2x even on a 1x screen (stage code caps dpr at 2).
  try { if ((window.devicePixelRatio || 1) < 2) Object.defineProperty(window, 'devicePixelRatio', { get: () => 2, configurable: true }); } catch (e) { /* ignore */ }

  // ------------------------------------------------------------------ host page
  const STORY = window.StageStory;
  const PICK = STORY.chooseVariant(window.STAGE_SHOTS, window.STAGE_VARIANT, P.get('variant'));
  const CFG = PICK.variant ? STORY.resolveVariant(window.STAGE_SHOTS, PICK.variant) : { shots: [], measured: {} };
  const FILL = (CFG.fill === 'v6' ? window.STAGE_FILL_V6 : window.STAGE_FILL_V5) || {};
  const V6 = PICK.variant === 'v6'; // the v5 rehearsal never starts the v6 flow
  let FLOW = null; // window.StageV6, loaded after this file
  CFG.measured = CFG.measured || {}; CFG.runtime = {};
  let SHOTS = CFG.shots || [];
  const DRAFT = P.get('draft') === '1';
  const CLIP = P.get('clip') === 'x'; // portrait crops of shots 1, 3 and 5a for the X post
  const SEEK = P.has('t') ? Math.max(0, +P.get('t') || 0) : null;
  const HOLD = P.get('hold') === '1';
  const FROM = P.has('from') ? Math.max(0, +P.get('from') || 0) : 0; // live playback from S seconds (rehearsal)
  const W = CLIP ? 1080 : 1920, H = CLIP ? 1920 : 1080, DIP = 0.28;
  const SHIP_RE = /^\d{7}$/;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v) => typeof v === 'number' && isFinite(v);
  const clamp01 = (t) => (t < 0 ? 0 : t > 1 ? 1 : t);
  const ease = (t) => { t = clamp01(t); return t * t * (3 - 2 * t); };
  const lerp = (a, b, t) => a + (b - a) * t;
  const fmtInt = (v) => (num(v) ? Math.round(v).toLocaleString('en-US') : '—');
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  function env(tau, from, to, fade = 0.35) {
    if (tau < from || tau > to) return 0;
    return ease(Math.min(1, (tau - from) / fade, (to - tau) / fade));
  }

  // timeline: segments of the shot list, each {i, id, from, to} with from/to in shot time
  const seg = (i, beat) => ({ i, id: beat ? beat.id : SHOTS[i].id, from: beat ? beat.from : 0, to: beat ? beat.to : +SHOTS[i].duration_s });
  const spanSeg = (x) => { const f = STORY.findShot(SHOTS, x.shot), o = f.beat ? f.beat.from : 0; return f.index < 0 ? null : { i: f.index, id: x.shot, from: o + x.from, to: o + x.to }; };
  let SEGS = [], starts = [], TOTAL = 0;
  const segAt = (t) => { let k = 0; while (k + 1 < SEGS.length && t >= starts[k + 1]) k++; return k; };
  const D = (window.__director = { ready: false, started: false, done: false, applied: false, t: 0, total: 0, shot: null, errors: [], variant: PICK.variant, rehearsal: PICK.rehearsal, clip: CLIP, shots: [], gaps: [] });
  function layout() { // the timeline, once the fallbacks are known: a fallback can change a shot's length
    SEGS = CLIP ? ((CFG.clip_x || {}).spans || []).map(spanSeg).filter(Boolean)
      : P.has('shot') ? [STORY.findShot(SHOTS, P.get('shot'))].filter((f) => f.index >= 0).map((f) => seg(f.index, f.beat))
        : SHOTS.map((_, i) => seg(i, null));
    starts = []; let acc = 0;
    for (const g of SEGS) { starts.push(acc); acc += g.to - g.from; }
    TOTAL = acc; D.total = TOTAL;
    D.shots = SEGS.map((g, k) => ({ id: g.id, title: SHOTS[g.i].title, start: starts[k], duration_s: g.to - g.from, still_t: g.from ? null : SHOTS[g.i].still_t, fell_back: !!SHOTS[g.i].fell_back }));
    if (P.has('shot') && !SEGS.length) D.errors.push(`no shot ${P.get('shot')}`);
  }
  if (PICK.error) D.errors.push(PICK.error);

  // ------------------------------------------------------------------ DOM
  // Overlays live in a 1920x1080 layer scaled to the window; the stage page underneath lays itself out natively.
  const root = document.createElement('div'); root.id = 'dir-root';
  root.innerHTML = `<div id="dir-veil"></div><div id="dir-ovl"></div><div id="dir-strip"></div><div id="dir-cap" class="dir-cap"></div>
    <div id="dir-lower" hidden></div><div id="dir-standin" hidden></div><div id="dir-slot" hidden></div><div id="dir-brief" hidden></div><div id="dir-dip"></div><div id="dir-chip" hidden></div><div id="dir-wm" hidden>DRAFT</div><div id="dir-rehearsal" hidden></div>
    <div id="dir-start"><div class="ds-in"><b>Stow<i>LM</i></b><span id="dir-start-msg">loading…</span></div></div>`;
  const backdrop = document.createElement('div'); backdrop.id = 'dir-backdrop';
  const $ = (id) => document.getElementById(id);
  let SC = 1; // window px per 1920-frame px
  let ROOT_X = 0, ROOT_Y = 0;
  function fit() { SC = Math.min(innerWidth / W, innerHeight / H); ROOT_X = (innerWidth - W * SC) / 2; ROOT_Y = (innerHeight - H * SC) / 2; root.style.width = `${W}px`; root.style.height = `${H}px`; root.style.transform = `translate(${ROOT_X}px, ${ROOT_Y}px) scale(${SC})`; }
  function mount() {
    document.body.classList.add('video', 'present');
    document.body.classList.toggle('dir-clip', CLIP);
    document.body.classList.toggle('dir-v6', V6);
    document.body.appendChild(backdrop); document.body.appendChild(root);
    addEventListener('resize', fit); fit();
    if (DRAFT) $('dir-wm').hidden = false;
    if (CFG.lower_third) { $('dir-lower').hidden = false; $('dir-lower').textContent = CFG.lower_third; }
    if (PICK.rehearsal) { $('dir-rehearsal').hidden = false; $('dir-rehearsal').textContent = `REHEARSAL · variant ${PICK.variant}`; }
  }

  // ------------------------------------------------------------------ text: placeholders + bundle refs
  function getPath(o, path) { for (const k of String(path).split('.')) { if (o == null) return null; o = o[k]; } return o; }
  // Returns {html, empty}. Segments split on " · "; in final mode a segment with an unfilled placeholder is dropped.
  function fill(text, rt) {
    const segs = STORY.ifBlocks(text, CFG, FILL).split(' · ');
    const out = [];
    for (const seg of segs) {
      let missing = false;
      let h = esc(seg).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
      h = h.replace(STORY.PLACEHOLDER, (m, name) => {
        const v = STORY.value(name, CFG, FILL);
        if (v) return `<span class="dir-meas" title="${esc(v.source)}">${esc(v.value)}</span>`;
        missing = true; return `<span class="dir-pending">PENDING: ${esc(name)}</span>`;
      });
      h = h.replace(/\[\[([a-zA-Z0-9_.]+)\]\]/g, (m, path) => {
        const v = rt && rt.B ? getPath(rt.B, path) : null;
        if (v == null || v === '') { missing = true; return `<span class="dir-pending">PENDING: ${esc(path)}</span>`; }
        return esc(num(v) ? (Number.isInteger(v) ? fmtInt(v) : String(v)) : v);
      });
      if (missing && !DRAFT) continue;
      if (h.trim()) out.push(h);
    }
    return { html: out.join(' <i class="dot">·</i> '), empty: !out.join('').trim() };
  }

  // ------------------------------------------------------------------ catalogue + plan resolution
  let CAT = null, BRIEF = null; // /api/catalog, /api/brief/state (the recorded cases and their fresh gates)
  // D99 honesty: a shot that shows a stand-in where the hero plan belongs carries an in-frame label in EVERY mode (final too,
  // not only the draft chip). It disappears by itself once run 3.2 is in the catalogue. A StowLM plan keeps its measured
  // label from the bundle (meta.preview_label; rule app/stage_stowlm.py FINAL_RULE), so it can never pass for anything else.
  function standinTag(r, B) {
    return [r && r.tag, (B && B.meta && B.meta.preview_label) || ''].filter(Boolean).join(' · ');
  }
  let V6STATE = null; // /api/v6/state, read once at boot
  D.standinTag = standinTag;
  function resolve(s) {
    const r = STORY.resolveShot(s, CFG, (CAT && CAT.plans) || [], CAT && CAT.active_plan_id, BRIEF);
    if (r.missing && !D.errors.includes(`shot ${s.id}: no plan or stand-in in the catalogue`)) D.errors.push(`shot ${s.id}: no plan or stand-in in the catalogue`); // a take must not silently show the active plan
    return r;
  }

  // ------------------------------------------------------------------ shot runtimes (bundle + overlays per shot)
  const RT = new Map(), BUNDLES = new Map();
  const VEIL = new Set(['spark_lines', 'bars', 'live', 'lifts', 'worth', 'rows', 'card']); // full cards: darken the stage behind them
  const PANEL = { ship: '.ship-panel', yard: '.yard-panel', spark: '.spark-panel', physics: '.physics-panel', race: '.race-panel', rules: '.rules-panel', play: '.play-panel', kpis: '#kpis' };
  const ORIG = {};
  let ST = null; // window.__stage
  async function fetchBundle(id) {
    if (BUNDLES.has(id)) return BUNDLES.get(id);
    const pr = (async () => {
      const t0 = performance.now();
      for (;;) { // wait (max 90 s) for physics + background baseline/Spark replays, so every take shows settled numbers
        const r = await fetch('/api/stage?' + new URLSearchParams(id ? { plan_id: id } : {}), { headers: { Accept: 'application/json' } });
        if (!r.ok) throw new Error(`/api/stage ${id}: ${r.status}`);
        const b = await r.json();
        const bg = Object.values((b.comparison || {}).reasons || {}).some((x) => /in the background/.test(String(x || '')));
        const rebinding = /being re-bound/.test(String((b.explanation || {}).cache_reason || '')); // a bound Spark text is on its way, not absent
        if ((b.physics && !bg && !rebinding) || performance.now() - t0 > 90000) return b; // physics measured, race columns and Spark text settled
        await fetch('/api/stage/physics?' + new URLSearchParams(id ? { plan_id: id } : {})).catch(() => null);
        await new Promise((res) => setTimeout(res, 1500));
      }
    })();
    BUNDLES.set(id, pr);
    return pr;
  }
  async function makeRt(i) {
    if (RT.has(i)) return RT.get(i);
    const s = SHOTS[i];
    const rt = { i, s, r: s.v6 ? { state: 'chapter', tag: '' } : resolve(s), B: null, items: [], caps: [], famShown: -1, selDone: false, bigState: {}, ready: false, chapter: s.v6 || null };
    if (s.show_tag && V6STATE && (V6STATE.stand_ins || {}).ship) rt.r.tag = V6STATE.stand_ins.ship; // a stand-in ship says so on every shot, in every mode; a real hero has none
    RT.set(i, rt);
    if (s.v6) { prepare(rt); rt.ready = true; return rt; } // the chapter draws itself; captions and chips are all that's left here
    try { rt.B = await fetchBundle(rt.r.id); } catch (e) { D.errors.push(String(e)); rt.B = null; }
    if (rt.B && !STORY.approved(rt.B)) { rt.blocked = true; D.errors.push(`shot ${s.id}: the fresh checker did not approve this plan; not shown`); }
    if (rt.B && (s.overlays || []).some((o) => o.type === 'diff')) { // the plan before the instruction, for what moved
      const id = (((rt.r.brief || {}).gate || {}).before || {}).plan_id;
      try { if (id) { const b = await fetch('/api/stage?' + new URLSearchParams({ plan_id: id }), { headers: { Accept: 'application/json' } }); if (b.ok) rt.diff = STORY.bayDiff(await b.json(), rt.B); } } catch (e) { D.errors.push('before plan: ' + e); }
    }
    if (rt.B) prepare(rt);
    rt.ready = true;
    return rt;
  }
  function prepare(rt) {
    const s = rt.s;
    if (!rt.B) { buildOverlays(rt); return; }
    // x-ray pick: the trace step named in the config if it shows an override, else the first content-field override
    const xs = ((rt.B.xray || {}).steps || []).filter((x) => num(x.plan_step));
    const contentOv = (x) => (x.decisions || []).some((d) => d.overridden && /^(box|bay|row)$/.test(d.field) && /\d/.test(String(d.top1 || '')));
    const want = s.xray_pick && num(s.xray_pick.trace_step) ? xs.find((x) => x.step === s.xray_pick.trace_step) : null;
    const pick = (want && contentOv(want)) ? want : xs.find(contentOv) || want || xs[0] || null;
    rt.xrayRow = pick; rt.xrayStep = pick ? pick.plan_step : null;
    rt.total = (rt.B.steps || []).length;
    buildOverlays(rt);
  }
  let loadedPlan;
  function activate(rt) { // swap the bundle in (if needed) and reset per-shot page state
    clearFocus();
    document.body.classList.toggle('dir-chapter', !!rt.chapter);
    if (FLOW) { if (rt.chapter) loadedPlan = undefined; else FLOW.hide(); } // a chapter may load its own plan; hand the stage back under the dip
    if (rt.B && loadedPlan !== rt.r.id) {
      const first = (rt.s.play && rt.s.play[0]) ? posVal(rt.s.play[0][1], rt) : 0;
      ST.load(rt.B, Math.floor(first)); loadedPlan = rt.r.id;
    }
    document.body.classList.toggle('dir-no-kpis', !!rt.s.hide_kpis);
    document.body.classList.toggle('dir-no-money', !!rt.s.hide_money);
    document.body.classList.toggle('dir-bleed', !!rt.s.bleed);
    for (const f of document.querySelectorAll('#rules .fam')) f.classList.remove('dir-pend', 'dir-pop');
    rt.famShown = -1; rt.selDone = false; rt.bigState = {}; lastBrief = '';
    if (ST.S.selected) ST.selectBox(null);
    if (rt.s.bleed) resizeStage();
    for (const [, r] of RT) if (r.ovl) r.ovl.style.display = r === rt ? '' : 'none';
    const tag = standinTag(rt.r, rt.B), el = $('dir-standin'); // in-frame label (all modes, D99)
    el.hidden = !tag; el.innerHTML = tag ? `<i></i><span>${esc(tag)}</span>` : '';
    el.className = `dir-standin-s${rt.s.id}`;
    D.label = tag;
    const slot = $('dir-slot'); // draft only: says what will replace the stand-in
    slot.hidden = !(DRAFT && rt.r.state === 'standin'); slot.textContent = CFG.hero_slot || '';
    buildTag(rt);
  }
  // A shot may say which pipeline its figures came from in place of the build tag (logic14 M16: shot 11's card was measured on v3,
  // the app runs v4). The tag's own words come back on every other shot. A line with an unfilled digest hides the tag.
  let BUILD_TAG = null;
  function buildTag(rt) {
    const el = document.getElementById('v6-build');
    if (!el) return;
    if (BUILD_TAG === null) BUILD_TAG = { html: el.innerHTML, hidden: el.hidden };
    if (!rt.s.build_tag) { el.innerHTML = BUILD_TAG.html; el.hidden = BUILD_TAG.hidden; el.classList.remove('dir-build-shot'); return; }
    const f = fill(rt.s.build_tag, rt);
    el.innerHTML = f.empty ? '' : f.html; el.hidden = f.empty; el.classList.add('dir-build-shot');
  }

  // ------------------------------------------------------------------ positions, camera, focus
  function posVal(v, rt) {
    if (v === 'end') return rt.total;
    if (typeof v === 'string' && v.startsWith('xray')) { const base = num(rt.xrayStep) ? rt.xrayStep : 0; const off = +(v.slice(4) || 0); return Math.max(0, Math.min(rt.total, base + off)); }
    return Math.max(0, Math.min(rt.total, +v || 0));
  }
  function playAt(rt, tau) {
    const kf = (rt.s.play || [[0, 0]]).map(([t, v]) => [+t, posVal(v, rt)]);
    if (tau <= kf[0][0]) return { pos: kf[0][1], rest: Number.isInteger(kf[0][1]) };
    for (let j = 0; j + 1 < kf.length; j++) {
      const [ta, a] = kf[j], [tb, b] = kf[j + 1];
      if (tau <= tb) { if (a === b) return { pos: a, rest: Number.isInteger(a) }; return { pos: lerp(a, b, (tau - ta) / Math.max(1e-6, tb - ta)), rest: false }; }
    }
    const last = kf[kf.length - 1][1];
    return { pos: last, rest: Number.isInteger(last) };
  }
  let camNow = { tx: 0, ty: 0, z: 1 };
  function slotScreen(rt, slot, box) {
    const sc = ST.scene; if (!sc || !sc.project) return null;
    const e = sc.shipExtOfSlot(slot, box); if (!e) return null;
    return sc.project((e.x0 + e.x1) / 2, (e.y0 + e.y1) / 2, (e.z0 + e.z1) / 2);
  }
  function stepTarget(rt, k) {
    const steps = rt.B.steps || [];
    for (let j = Math.min(k, steps.length - 1); j >= 0 && j >= k - 6; j--) {
      const st = steps[j]; const a = SHIP_RE.test(st.dst) ? st.dst : SHIP_RE.test(st.src) ? st.src : null;
      if (a) { const p = slotScreen(rt, a, st.box); if (p) return p; }
    }
    return null;
  }
  function focusPoint(rt, f, pos) {
    const cam = $('ship-canvas'), cw = cam.clientWidth, ch = cam.clientHeight, c = [cw / 2, ch / 2];
    if (!f || f === 'center') return c;
    if (f === 'active') {
      const k = Math.floor(pos), t = pos - k;
      const a = stepTarget(rt, k) || c, b = stepTarget(rt, k + 1) || a;
      return [lerp(a[0], b[0], ease(t)), lerp(a[1], b[1], ease(t))];
    }
    if (f === 'xray') { const ch2 = rt.xrayRow && rt.xrayRow.chosen; return (ch2 && slotScreen(rt, ch2.slot, ch2.box)) || c; }
    if (f.startsWith('slot:')) return slotScreen(rt, f.slice(5), null) || c;
    return c;
  }
  function cameraAt(rt, tau, pos) {
    const kf = rt.s.camera; if (!kf || !kf.length) return null;
    let a = kf[0], b = kf[0], u = 0;
    if (tau >= kf[kf.length - 1].t) { a = b = kf[kf.length - 1]; }
    else for (let j = 0; j + 1 < kf.length; j++) if (tau >= kf[j].t && tau < kf[j + 1].t) { a = kf[j]; b = kf[j + 1]; u = ease((tau - a.t) / Math.max(1e-6, b.t - a.t)); break; }
    const fa = focusPoint(rt, a.focus, pos), fb = focusPoint(rt, b.focus, pos);
    return { zoom: lerp(+a.zoom || 1, +b.zoom || 1, u), f: [lerp(fa[0], fb[0], u), lerp(fa[1], fb[1], u)] };
  }
  function applyCamera(rt, cam) {
    const el = $('ship-canvas'); if (!el) return;
    if (!cam || cam.zoom <= 1.001) { el.style.transform = ''; camNow = { tx: 0, ty: 0, z: 1 }; return; }
    const cw = el.clientWidth, ch = el.clientHeight, z = cam.zoom;
    const d = [lerp(cam.f[0], cw / 2, 0.85), lerp(cam.f[1], ch / 2, 0.85)];
    const tx = Math.min(0, Math.max(cw - z * cw, d[0] - z * cam.f[0])), ty = Math.min(0, Math.max(ch - z * ch, d[1] - z * cam.f[1]));
    camNow = { tx, ty, z };
    el.style.transformOrigin = '0 0';
    el.style.transform = `translate(${tx.toFixed(1)}px, ${ty.toFixed(1)}px) scale(${z.toFixed(4)})`;
  }
  function resizeStage() { // re-render canvases at their new size (camera transform off while measuring)
    const el = $('ship-canvas'), tr = el.style.transform;
    el.style.transform = '';
    try { ST.resizeAll(); } catch (e) { D.errors.push('resize: ' + e); }
    el.style.transform = tr;
  }
  const BIG_STYLE = ['position', 'left', 'top', 'width', 'height', 'zoom', 'zIndex', 'margin', 'opacity'];
  function clearFocus() {
    for (const k of Object.keys(PANEL)) {
      const el = document.querySelector(PANEL[k]); if (!el) continue;
      if (el.classList.contains('dir-big')) { for (const p of BIG_STYLE) el.style[p] = ''; el.classList.remove('dir-big', 'dir-mini'); }
      el.classList.remove('dir-hot', 'dir-glow'); el.style.removeProperty('--dir-glow');
    }
    document.body.classList.remove('dir-dim'); backdrop.style.display = 'none';
    $('ship-canvas').style.transform = '';
    resizeStage();
  }
  function applyFocus(rt, tau) {
    const doc = document, list = rt.s.focus || [];
    let dim = 0, back = 0;
    for (const k of Object.keys(PANEL)) {
      const el = doc.querySelector(PANEL[k]); if (!el) continue;
      const specs = list.filter((x) => x.panel === k && (!x.when || STORY.conditionHolds(x.when, rt.r, rt.B, CFG)));
      let glow = 0, hot = 0, big = 0, spec = null;
      for (const x of specs) {
        const e = env(tau, x.from, x.to, x.mode === 'big' ? 0.5 : 0.4);
        if (x.mode === 'glow') glow = Math.max(glow, e);
        else if (x.mode === 'dim') { hot = Math.max(hot, e); dim = Math.max(dim, e); }
        else if (x.mode === 'big' && e >= big) { big = e; spec = x; }
      }
      el.classList.toggle('dir-hot', hot > 0 || big > 0);
      el.style.setProperty('--dir-glow', glow.toFixed(3));
      el.classList.toggle('dir-glow', glow > 0);
      if (big > 0 && spec && ORIG[k]) {
        const bleed = !!rt.s.bleed, m = bleed ? 1 : big; // full-bleed shots: panels fade in where they sit, the page under them is gone
        if (!bleed) back = Math.max(back, big);
        const o = ORIG[k], r = (spec.rect || o).map((v, n) => (n < 2 ? (n ? ROOT_Y : ROOT_X) : 0) + v * SC), z = lerp(1, (+spec.zoom || 1) * SC, m);
        const x = lerp(o[0], r[0], m), y = lerp(o[1], r[1], m), w = lerp(o[2], r[2], m), h = lerp(o[3], r[3], m);
        Object.assign(el.style, { position: 'fixed', left: (x / z) + 'px', top: (y / z) + 'px', width: (w / z) + 'px', height: (h / z) + 'px', zoom: String(z), zIndex: '60', margin: '0', opacity: bleed ? big.toFixed(3) : '' });
        el.classList.add('dir-big'); el.classList.toggle('dir-mini', spec.cls === 'dir-mini');
      } else if (el.classList.contains('dir-big')) {
        for (const p of BIG_STYLE) el.style[p] = '';
        el.classList.remove('dir-big', 'dir-mini');
      }
      const state = rt.s.bleed ? (big > 0 ? 'big' : 'off') : big >= 1 ? 'big' : big > 0 ? 'moving' : 'off';
      if (rt.bigState[k] !== state) { const prev = rt.bigState[k]; rt.bigState[k] = state; if ((prev || state === 'big') && state !== 'moving' && k !== 'kpis') rt.needResize = true; }
    }
    doc.body.classList.toggle('dir-dim', dim > 0);
    doc.body.style.setProperty('--dd', dim.toFixed(3));
    backdrop.style.opacity = (0.8 * back).toFixed(3); backdrop.style.display = back > 0 ? 'block' : 'none';
  }
  function applyRules(rt, tau) {
    const rr = rt.s.rules_reveal; if (!rr) return;
    const fams = document.querySelectorAll('#rules .fam');
    const n = tau < rr.from ? 0 : Math.floor((tau - rr.from) / (+rr.per_family_s || 1)) + 1;
    fams.forEach((f, i) => { const pend = i >= n; f.classList.toggle('dir-pend', pend); if (!pend && i >= rt.famShown) f.classList.add('dir-pop'); });
    rt.famShown = Math.max(rt.famShown, n);
    const badge = $('rules-badge'); if (badge) badge.classList.toggle('dir-pend-badge', n < fams.length);
  }

  // ------------------------------------------------------------------ overlays (host side)
  const pendShown = !V6 || DRAFT; // v6: a card never says pending in a final frame, the row just isn't there
  const pendCard = (label) => (pendShown ? `<div class="dc-pend">pending: ${esc(label)}</div>` : '');
  const pendChip = (label) => (pendShown ? `<span class="dc-pendchip">pending: ${esc(label)}</span>` : '');
  const STEP_PENDING = { speech: '1.3', reading: '1.1', plan: '3.2', check: '3.2' };
  // A recorded voice clip of this very sentence (brief state), if any: who heard it.
  function clipFor(sentence) {
    const norm = (x) => String(x || '').trim().toLowerCase();
    return ((BRIEF || {}).voice_clips || []).find((c) => norm(c.said) === norm(sentence) || norm(c.transcript) === norm(sentence)) || null;
  }
  // Shot 1: a waveform and the words, over black, then over the ship. The waveform is drawn from t, so a take is repeatable.
  function hookHtml(rt) {
    const words = STORY.sentenceFor(rt.r, rt.B, CFG).split(/\s+/).filter(Boolean).map((w, n, all) => `${n ? '' : '“'}${w}${n === all.length - 1 ? '”' : ''}`);
    const bars = Array.from({ length: 56 }, () => '<i></i>').join(''), rule = (rt.r.brief || {}).rule || '';
    return `<div class="dh-black"></div><div class="dh-stack"><div class="dh-wave">${bars}</div><div class="dh-words">${words.map((w) => `<span>${esc(w)}</span>`).join(' ')}</div>${rule ? `<div class="dh-rule"><span>${esc(rule)}</span><b>✓ confirmed</b></div>` : ''}</div>`;
  }
  function hookTick(rt, o, el) {
    const rule = el.querySelector('.dh-rule'), okChip = rule && rule.querySelector('b');
    const black = el.querySelector('.dh-black'), stack = el.querySelector('.dh-stack'), bars = [...el.querySelectorAll('.dh-wave i')], words = [...el.querySelectorAll('.dh-words span')];
    const span = o.speak_to - o.speak_from;
    return (tau) => {
      const out = ease((tau - o.black_until) / 0.9);
      black.style.opacity = (1 - out).toFixed(3);
      stack.style.top = `${lerp(H * 0.42, CLIP ? 150 : 62, out).toFixed(1)}px`;
      stack.style.setProperty('--dh-s', lerp(1, 0.62, out).toFixed(3));
      el.querySelector('.dh-wave').style.height = `${lerp(140, 0, out).toFixed(1)}px`;
      stack.style.gap = `${lerp(28, 0, out).toFixed(1)}px`;
      stack.style.setProperty('--dh-bg', (0.78 * out).toFixed(3));
      const live = tau >= o.speak_from && tau <= o.speak_to + 0.25 ? Math.sin(Math.PI * clamp01((tau - o.speak_from) / (span + 0.25))) ** 0.4 : 0;
      const beat = Math.floor(tau * 16);
      bars.forEach((b, n) => { const r = Math.abs(Math.sin((n + 1) * 12.9898 + beat * 78.233) * 43758.5453) % 1; b.style.height = `${(6 + live * (18 + 110 * r * Math.sin(Math.PI * (n + 0.5) / bars.length))).toFixed(1)}px`; });
      el.querySelector('.dh-wave').style.opacity = (1 - out).toFixed(3);
      const shown = (tau - o.speak_from) / span * words.length;
      words.forEach((w, n) => { w.style.opacity = clamp01(shown - n).toFixed(2); });
      if (rule) { rule.style.opacity = clamp01((tau - o.rule_at) / 0.3).toFixed(2); okChip.style.opacity = clamp01((tau - o.confirm_at) / 0.2).toFixed(2); }
    };
  }
  // Shot 1: light the bay the sentence names, from the slots the plan really uses in it.
  function bayTick(rt, o, el) {
    const c = (rt.r.brief ? (rt.r.brief.constraint || {}).constraint : (rt.B.instruction || {}).constraint) || {}, bay = (c.away_from || {}).bay;
    const key = bay == null ? null : String(bay).padStart(3, '0');
    const cells = new Set();
    for (const st of rt.B.steps || []) for (const cell of st.slot_cells || [st.dst, st.src]) if (key && SHIP_RE.test(String(cell)) && String(cell).startsWith(key)) cells.add(cell);
    el.querySelector('span').textContent = key ? `bay ${bay}` : '';
    let rect = null, cw = 0;
    return () => {
      const sc = ST.scene, cam = $('ship-canvas');
      if (!key || !sc || !sc.project) { el.style.visibility = 'hidden'; return; }
      if (!rect || cw !== cam.clientWidth) {
        cw = cam.clientWidth; rect = null;
        const seen = [];
        for (const cell of cells) {
          const e = sc.shipExtOfSlot(cell, null); if (!e) continue;
          for (const x of [e.x0, e.x1]) for (const y of [e.y0, e.y1]) for (const z of [e.z0, e.z1]) seen.push(sc.project(x, y, z));
        }
        if (seen.length) rect = [Math.min(...seen.map((p) => p[0])), Math.min(...seen.map((p) => p[1])), Math.max(...seen.map((p) => p[0])), Math.max(...seen.map((p) => p[1]))];
      }
      if (!rect) { el.style.visibility = 'hidden'; return; }
      const box = $('ship-wrap').getBoundingClientRect(), { tx, ty, z } = camNow; // the wrap never moves; the canvas carries the camera
      const X = (v) => (box.left + tx + z * v - ROOT_X) / SC, Y = (v) => (box.top + ty + z * v - ROOT_Y) / SC;
      Object.assign(el.style, { left: `${X(rect[0]) - 12}px`, top: `${Y(rect[1]) - 12}px`, width: `${X(rect[2]) - X(rect[0]) + 24}px`, height: `${Y(rect[3]) - Y(rect[1]) + 24}px` });
    };
  }
  function stepsHtml(rt, o) {
    const sentence = STORY.sentenceFor(rt.r, rt.B, CFG), rows = STORY.stepModels(o.steps, rt.r, rt.B, clipFor(sentence));
    return rows.map((x) => `<div class="dst" data-step="${esc(x.id)}"><span>${esc(x.label)}</span>${x.model ? `<b>${esc(x.model)}</b><i>${esc(x.where)}</i>` : pendChip(STEP_PENDING[x.id] || '3.2')}</div>`).join('');
  }
  function stepsTick(rt, o, el) {
    const cells = [...el.querySelectorAll('.dst')];
    return (tau) => { cells.forEach((c, n) => { const x = o.steps[n]; c.classList.toggle('on', tau >= x.from && tau < x.to); c.classList.toggle('done', tau >= x.to); }); };
  }
  // Shot 4: each statement Spark chose, with the checker lines it was rendered from. Render-only; no statement, no card.
  function sparkLinesHtml(rt) {
    const m = STORY.sparkLines((rt.B || {}).explanation);
    if (!m.bound) return `<div class="dc-eyebrow">MUSE SPARK · WHY</div><div class="dsl-none">${pendCard('3.2')}<div class="dc-meta">Spark's explanation is shown only when it is bound to this plan's checker report: ${esc(m.reason)}.</div></div>`;
    const st = m.statements.map((s, n) => `<div class="dsl-st" data-n="${n}"><p>${esc(s.text)}</p></div>`).join('');
    const ln = m.lines.map((l) => `<div class="dsl-ln" data-key="${esc(l.key)}"><span>${esc(l.label)}</span><b>${esc(l.value)}</b></div>`).join('');
    return `<div class="dsl-col"><div class="dc-eyebrow">MUSE SPARK · ${esc(((rt.B.explanation || {}).model) || '')}</div><div class="dsl-sub">chose and ordered these checked statements</div>${st}</div>
      <div class="dsl-col"><div class="dc-eyebrow">CHECKER REPORT · THIS PLAN</div><div class="dsl-sub">${esc((rt.B.meta || {}).planner_label || '')}</div>${ln}</div>`;
  }
  function sparkLinesTick(rt, o, el) {
    const m = STORY.sparkLines((rt.B || {}).explanation);
    if (!m.bound) return null;
    const st = [...el.querySelectorAll('.dsl-st')], ln = [...el.querySelectorAll('.dsl-ln')];
    return (tau) => {
      const lit = STORY.litIndex(m.statements.length, o.lit_from, o.lit_to, tau), keys = lit >= 0 ? new Set(m.statements[lit].lines) : new Set();
      st.forEach((e, n) => { e.classList.toggle('on', n === lit); e.classList.toggle('done', n < lit); });
      ln.forEach((e) => e.classList.toggle('on', keys.has(e.dataset.key)));
    };
  }
  function barRow(r) {
    if (r.value == null && !pendShown) return '';
    return `<div class="dbr ${r.own ? 'own' : ''} ${r.reference ? 'ref' : ''}" data-at="${r.at}"><div class="dbr-l">${esc(r.label)}</div><div class="dbr-v">${r.value == null ? pendChip(r.pending) : `<div class="dbr-track"><i data-w="${r.width_pct}"></i></div><b>${esc(r.text)}</b>`}</div></div>`;
  }
  function barsHtml(c) {
    return `<div class="dc-eyebrow">${esc(c.eyebrow)}</div><div class="dbc-title">${c.title ? esc(c.title) : c.rows.every((r) => r.value == null) ? pendCard(c.pending) : ''}</div>
      <div class="dbc-rows">${c.rows.map(barRow).join('')}</div>${c.notes.length ? `<div class="dbc-notes">${c.notes.map(esc).join(' <i class="dot">·</i> ')}</div>` : ''}${c.partial ? `<div class="dbc-partial">${esc(c.partial)}</div>` : ''}`;
  }
  function barsTick(rt, o, el) { // one bar at a time; width is the value's true share of the scale, the animation only reaches it
    const off = +o.offset || 0, rows = [...el.querySelectorAll('.dbr')];
    return (tau) => {
      for (const r of rows) {
        const at = +r.dataset.at, e = ease((tau - off - at) / 0.8), i = r.querySelector('.dbr-track i'), b = r.querySelector('b');
        r.style.opacity = clamp01((tau - off - at) / 0.3).toFixed(2);
        if (i) i.style.width = `${(+i.dataset.w * e).toFixed(2)}%`;
        if (b) b.style.opacity = e.toFixed(2);
      }
    };
  }
  // Shot 3b: every moved box, before and after, from the two plans' own steps. The count must equal the gate's.
  function diffHtml(rt) {
    const gate = (rt.r.brief || {}).gate || {}, d = rt.diff, label = rt.r.state === 'hero' ? 'WHAT MOVED' : 'WHAT MOVED · THIS STAND-IN RUN';
    if (!d || !d.ok) return `<div class="dc-eyebrow">${label}</div>${pendCard('3.2')}`;
    if (gate.boxes_moved != null && gate.boxes_moved !== d.moved.length) { D.errors.push(`moved boxes ${d.moved.length} differ from the gate's ${gate.boxes_moved}`); return `<div class="dc-eyebrow">${label}</div>${pendCard('3.2')}`; }
    const bay = (b, side) => {
      const W2 = 22, H2 = 15, cols = b.rows.length, rows = b.tiers.length;
      const cells = b.cells.map((c) => { const x = b.rows.indexOf(c.row) * W2, y = b.tiers.indexOf(c.tier) * H2, v = c[side]; return `<rect x="${x}" y="${y}" width="${W2 - 2}" height="${H2 - 2}" rx="2" class="${v ? (v.moved ? 'mv' : 'fx') : 'em'}"/>`; }).join('');
      return `<div class="ddb"><svg viewBox="0 0 ${cols * W2} ${rows * H2}" style="width:${cols * 24}px">${cells}</svg><span>bay ${b.bay}</span></div>`;
    };
    const side = (name, key) => `<div class="dd-side"><h4>${name}</h4><div class="dd-bays">${d.bays.map((b) => bay(b, key)).join('')}</div></div>`;
    return `<div class="dc-eyebrow">${label} · ${d.moved.length} BOXES MOVED</div><div class="dd-pair">${side('Before', 'before')}${side('After', 'after')}</div><div class="dd-key"><i></i>moved by the replan</div>`;
  }
  function liveHtml(c) {
    return `<div class="dc-eyebrow">${esc(c.eyebrow)}</div><div class="dlv-icon" aria-hidden="true"><svg viewBox="0 0 64 48"><path d="M4 16c16-14 40-14 56 0M14 26c11-9 25-9 36 0M24 36c6-4 10-4 16 0"/><circle cx="32" cy="43" r="3"/><path class="off" d="M10 46L54 2"/></svg></div>
      ${c.rows.map((r) => `<div class="dlv-row"><span>${esc(r.label)}</span>${r.text ? `<b>${esc(r.text)}</b>` : pendChip(r.pending)}</div>`).join('')}<div class="dc-meta">${esc(c.note)}</div>`;
  }
  function liftsHtml(c) {
    const limits = `<div class="dlim">${c.limits.map((l) => `<span>${esc(l)}</span>`).join('')}</div>`;
    return `<div class="dc-eyebrow">${esc(c.eyebrow)}</div><div class="dli-head">${c.headline ? esc(c.headline) : pendCard(c.pending)}</div>${limits}
      <div class="dbc-rows">${c.rows.map(barRow).join('')}</div><div class="dli-who">${esc(c.who)}</div><div class="dc-src">${esc(c.two_step)} · Navis, a commercial planning tool, and human planners: unknown</div>`;
  }
  function worthHtml(c) {
    if (!c.complete && !pendShown) return null; // no sourced scope: the first and last lines stand alone, and no number sits on the map
    return `${MAP_SVG}<div class="dc-eyebrow">${esc(c.eyebrow)}</div>${c.complete ? `<div class="dwo-head">${esc(c.headline)}</div><div class="dwo-sub">${esc(c.sub)}</div><div class="dwo-range">${esc(c.range)}</div>` : pendCard(c.pending)}<div class="dc-meta">${esc(c.note)}</div>`;
  }
  const mapSvg = (k) => `<svg viewBox="0 8 640 292" preserveAspectRatio="xMidYMid slice" class="dc-map" aria-hidden="true">
    <defs><radialGradient id="dmg" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#ffa53a" stop-opacity=".55"/><stop offset="1" stop-color="#ffa53a" stop-opacity="0"/></radialGradient></defs>
    <path d="M0 0H640V400H0Z" fill="#0b2546"/>
    <path d="M0 0L640 0L640 120C606 120 584 132 560 152C536 172 520 196 506 222C494 246 486 272 478 300C470 330 470 366 474 400L640 400L640 0Z" fill="#081426"/>
    <path d="M0 0L250 0C262 30 286 52 316 60C346 68 372 66 400 80C428 94 446 112 470 120C494 128 520 124 540 118L640 112L640 0Z" fill="#081426"/>
    <path d="M0 0L250 0C236 40 214 70 196 104C178 140 168 180 150 220C132 260 112 300 96 340C86 366 80 384 76 400L0 400Z" fill="#081426"/>
    <path d="M250 0C262 30 286 52 316 60C346 68 372 66 400 80C428 94 446 112 470 120C494 128 520 124 540 118" fill="none" stroke="#2c4f82" stroke-width="2"/>
    <path d="M540 118L640 112" fill="none" stroke="#2c4f82" stroke-width="2"/>
    <path d="M640 120C606 120 584 132 560 152C536 172 520 196 506 222C494 246 486 272 478 300C470 330 470 366 474 400" fill="none" stroke="#2c4f82" stroke-width="2"/>
    <path d="M250 0C236 40 214 70 196 104C178 140 168 180 150 220C132 260 112 300 96 340C86 366 80 384 76 400" fill="none" stroke="#2c4f82" stroke-width="2"/>
    <text x="150" y="285" fill="#2f5486" font-size="${18 * k}" letter-spacing="${4 * k}" font-weight="600">GULF OF THAILAND</text>
    <circle cx="318" cy="40" r="${5 * k}" fill="#8193ae"/><text x="${330 - 6 * (1 - k)}" y="${36 + 4 * (1 - k)}" fill="#8193ae" font-size="${15 * k}">Bangkok กรุงเทพฯ</text>
    <circle class="dc-pulse" cx="512" cy="200" r="34" fill="url(#dmg)"/>
    <path d="M512 200c-12-16-18-26-18-36a18 18 0 0 1 36 0c0 10-6 20-18 36z" fill="#ffa53a" stroke="#2b1700" stroke-width="2"/>
    <circle cx="512" cy="164" r="6" fill="#2b1700"/>
    <text x="482" y="${196 - 10 * (1 - k)}" fill="#ffc670" font-size="${21 * k}" font-weight="700" text-anchor="end">Laem Chabang</text>
    <text x="482" y="${222 - 38.3 * (1 - k)}" fill="#ffc670" font-size="${19 * k}" text-anchor="end">แหลมฉบัง</text>
  </svg>`;
  const MAP_SVG = mapSvg(1), MAP_SVG_SMALL = mapSvg(0.4); // shot 12: the place name at 40 % of shot 2's, so the money reads first
  // Shot 1's cold open: one huge word over its beat's footage; "black" puts it on black (the dare).
  function slamTick(rt, o, el) {
    const w = el.querySelector('b');
    return (tau) => { const u = clamp01((tau - o.from) / 0.14); w.style.transform = `translate(-50%, -50%) scale(${(1.35 - 0.35 * ease(u)).toFixed(3)})`; };
  }
  // Shot 6: same model, same laptop. The locked-test bars, then the mask study's two numbers, all from figures; a part without its numbers drops.
  function pairHtml(rt, c) {
    const bars = (c.bars || []).map((b) => ({ b, label: fill(b.label, rt), v: STORY.number(b.key, CFG, FILL) }));
    const barsOk = bars.length && bars.every((x) => x.v != null && !x.label.empty);
    const big = fill(c.big, rt), note = fill(c.bars_note || '', rt), lines = (c.lines || []).map((l) => fill(l, rt)).filter((f) => !f.empty), scope = fill(c.scope || '', rt);
    if (!barsOk && big.empty) return null;
    return `<div class="dc-eyebrow">${esc(c.eyebrow)}</div>
      ${barsOk ? `<div class="dpr-bars">${bars.map((x, n) => `<div class="dpr-bar${x.b.own ? ' own' : ''}" data-n="${n}"><span>${esc(x.b.name)}</span><b><i style="width:${Math.max(0, Math.min(100, x.v))}%"></i></b><em>${x.label.html}</em></div>`).join('')}
        ${note.empty ? '' : `<div class="dpr-note">${note.html}</div>`}</div>` : ''}
      ${big.empty ? '' : `<div class="dpr-mask"><div class="dpr-head">${esc(c.big_head || '')}</div><div class="dpr-big">${big.html}</div>${lines.map((f) => `<div class="dpr-line">${f.html}</div>`).join('')}${scope.empty ? '' : `<div class="dpr-scope">${scope.html}</div>`}</div>`}`;
  }
  function pairTick(rt, o, el) {
    const bars = [...el.querySelectorAll('.dpr-bar i')], mask = el.querySelector('.dpr-mask'), at = o.reveal || [0.4, 3.0];
    return (tau) => {
      bars.forEach((b) => { b.style.transform = `scaleX(${ease((tau - o.from - at[0]) / 1.1).toFixed(3)})`; });
      if (mask) { const k = ease((tau - o.from - at[1]) / 0.45); mask.style.opacity = k.toFixed(3); mask.style.transform = `translateY(${((1 - k) * 24).toFixed(1)}px)`; }
    };
  }
  // Shot 12: the exposure huge, the pool large, the fine print, over the map with the place name small. Every figure from data;
  // short of any one, the card is absent (final) or pending (draft), never a part of it.
  function pilotHtml(c) {
    if (!c.complete && !pendShown) return null;
    if (!c.complete) return `${MAP_SVG_SMALL}${pendCard(c.pending)}`;
    return `${MAP_SVG_SMALL}<div class="dpl-big" data-r="0"><b>${esc(c.big)}</b><span>${esc(c.big_sub)}</span></div>
      <div class="dpl-mid" data-r="1"><b>${esc(c.headline)}</b><span>${esc(c.sub)}</span></div>
      <div class="dpl-fine" data-r="2">${c.fine.map(esc).join(' <i class="dot">·</i> ')}</div>`;
  }
  function pilotTick(rt, o, el) {
    const map = el.querySelector('.dc-map'), parts = [...el.querySelectorAll('[data-r]')], at = o.reveal || [0, 0, 0];
    return (tau) => {
      const u = ease((tau - o.from) / (o.to - o.from)); if (map) map.style.transform = `scale(${lerp(2.4, 1, u).toFixed(3)})`;
      parts.forEach((x) => { const k = ease((tau - o.from - at[+x.dataset.r]) / 0.4); x.style.opacity = k.toFixed(3); x.style.transform = `translateY(${((1 - k) * 18).toFixed(1)}px)`; });
    };
  }
  // Shot 1's fallback: three hits and the dare, one a second, from what's really in the film.
  function hitsHtml(rt, o) {
    const h = STORY.hookHits(o, (V6STATE || {}).features);
    return `<div class="dh-black"></div>${h.words.map((w) => `<b class="dht">${esc(w)}</b>`).join('')}<b class="dht dare">${esc(h.dare)}</b>`;
  }
  function hitsTick(rt, o, el) {
    const words = [...el.querySelectorAll('.dht')], dare = words.pop();
    return (tau) => {
      words.forEach((w, n) => { const a = o.at[n], on = tau >= a && tau < (o.at[n + 1] || o.end_at), u = clamp01((tau - a) / 0.16); w.style.opacity = on ? '1' : '0'; w.style.transform = `translate(-50%, -50%) scale(${(1.5 - 0.5 * ease(u)).toFixed(3)})`; });
      dare.style.opacity = tau >= o.end_at ? '1' : '0'; dare.style.transform = 'translate(-50%, -50%)';
    };
  }
  // Shot 3's fallback: real rows from the ship's own load list, under the label the script names.
  function rowsHtml(rt, o) {
    const rows = Object.entries((rt.B || {}).boxes || {}).slice(0, +o.count || 9);
    return `<div class="dc-eyebrow">${esc(String(o.title).toUpperCase())}</div><table class="drw"><tr><th>Box</th><th>Length</th><th>Discharge</th><th>Mass</th><th>Dangerous goods</th></tr>${rows.map(([id, x]) => `<tr><td>${esc(id)}</td><td>${esc(x.length_ft)} ft</td><td>${esc(x.pod)}</td><td>${num(x.mass_t) ? x.mass_t.toFixed(1) + ' t' : '—'}</td><td>${x.dg ? 'class ' + esc(x.dg.class) : ''}</td></tr>`).join('')}</table>`;
  }
  function plainCard(rt, o) {
    const c = CFG.cards[o.card], title = fill(c.title, rt), lines = (c.lines || []).map((l) => fill(l, rt)).filter((f) => !f.empty);
    return `<div class="dc-eyebrow">${esc(c.eyebrow)}</div>${title.empty ? '' : `<div class="dcd-title">${title.html}</div>`}${lines.map((f) => `<div class="dcd-line">${f.html}</div>`).join('')}`;
  }
  function overlayHtml(rt, o) {
    const B = rt.B || {}, m = B.meta || {};
    switch (o.type) {
      case 'hits': return hitsHtml(rt, o);
      case 'slam': return `<b class="dsl"${o.at ? ` style="left:${+o.at[0]}px;top:${+o.at[1]}px${o.size ? `;font-size:${+o.size}px` : ''}"` : ''}>${esc(o.text)}</b>`; // at: the word's centre, clear of what its beat shows
      case 'chip': return `<span>${esc(o.text)}</span>`;
      case 'pair': return pairHtml(rt, CFG.cards[o.card]);
      case 'pilot': return pilotHtml(STORY.pilotCard(CFG.cards[o.card], CFG, FILL));
      case 'rows': return rowsHtml(rt, o);
      case 'card': return plainCard(rt, o);
      case 'tally': return `<img src="/api/v6/asset/tally_sheet.png" alt="Tally sheet"><span>${esc(o.label)}</span>`;
      case 'footage': return DRAFT ? `<span>${esc(o.text)}</span>` : null;
      case 'hook': return hookHtml(rt);
      case 'baylight': return '<span></span>';
      case 'tag': return `<span>${esc(o.text)}</span>`;
      case 'map': return `${MAP_SVG}<div class="dmp-fact"><span>Thailand's main container port</span><b>9,325</b><i>container-ship calls</i></div>`;
      case 'steps': return stepsHtml(rt, o);
      case 'spark_lines': return sparkLinesHtml(rt);
      case 'bars': return barsHtml(STORY.barCard(CFG.cards[o.card], CFG, FILL));
      case 'live': return liveHtml(STORY.liveCard(CFG.cards[o.card], CFG, FILL));
      case 'diff': return diffHtml(rt);
      case 'confirm': return '<b>✓</b><span>Confirmed by the planner</span>';
      case 'lifts': return liftsHtml(STORY.liftsCard(CFG.cards[o.card], CFG, FILL));
      case 'worth': return worthHtml(STORY.worthCard(CFG.cards[o.card], CFG, FILL));
      case 'override': {
        const x = rt.xrayRow; if (!x) return null;
        const d = (x.decisions || []).find((y) => y.overridden && /^(box|bay|row)$/.test(y.field) && /\d/.test(String(y.top1 || '')));
        const chosen = esc(String((x.chosen && x.chosen.text) || '').trim());
        if (!d) {
          const ds = x.decisions || [];
          if (!ds.length || !ds.every((y) => y.overridden === false)) return `<div class="dc-eyebrow warn">MASK AT THIS STEP</div><div class="dc-ov">No override of the box, bay or row is recorded at this step.</div><div class="dc-meta">Emitted <code>${chosen}</code></div>`;
          const n = num(x.n_actions) && num(x.n_legal) ? x.n_actions - x.n_legal : null;
          return `<div class="dc-eyebrow ok">MODEL'S OWN CHOICE</div><div class="dc-ov">Its top choices here were all legal; the mask removed ${n == null ? 'the illegal' : fmtInt(n)} actions.</div><div class="dc-meta">Emitted <code>${chosen}</code></div>`;
        }
        const legal2 = (d.top_k || x.top_k || []).find((t) => t.legal);
        return `<div class="dc-eyebrow bad">OVERRIDDEN BY THE MASK · LIFT ${fmtInt(x.plan_step + 1)}</div>
          <div class="dc-ov">Model's own top choice for the <b>${esc(d.field)}</b>: <code class="bad">${esc(d.top1)}</code> — not a legal next action here, so its probability is set to 0.</div>
          <div class="dc-meta">${legal2 ? `Highest-ranked legal choice <code>${esc(legal2.text)}</code> · ` : ''}emitted <code>${chosen}</code> · ${fmtInt(x.n_legal)} legal action${x.n_legal === 1 ? '' : 's'} at this step</div>
          <div class="dc-src">${esc((B.xray || {}).label || m.planner_label || '')} · recorded trace step ${fmtInt(x.step)}</div>`;
      }
      case 'verdict': {
        const ok = m.checker_passed === true && m.departure_approved === true, fams = (B.rules && B.rules.families) || [];
        const line = [fams.length ? `Listed rules: ${window.StageRules.summary(window.StageRules.tally(fams))}` : null, m.notice].filter(Boolean).map(esc).join(' · ');
        return `<div class="dv-stamp ${ok ? 'ok' : 'bad'}">${ok ? o.stamp || '✓ CHECKER PASS · DEPARTURE APPROVED' : o.stamp ? '✕ NOT SHOWN' : '✕ NOT APPROVED'}</div><div class="dc-meta">${line}</div>`;
      }
      case 'end': {
        const sentence = STORY.sentenceFor(rt.r, rt.B, CFG);
        return `<div class="de-logo"><div class="brand-mark"><span></span><span></span><span></span></div><div>Stow<b>LM</b></div></div>
        <div class="de-tag">${esc(o.tag || 'A fine-tuned Meta model reads it. Search software plans. A checker tests. Muse Spark selects and orders checked statements.')}</div>
        ${sentence && o.say !== false ? `<div class="de-say">“${esc(sentence)}”</div>` : ''}
        <div class="de-lines">${(o.lines || []).map((l) => { const f = fill(l.links || l, rt); return f.empty ? '' : l.links ? `<div class="de-links">${f.html}</div>` : `<div>${f.html}</div>`; }).join('')}${CFG.generated_voice ? `<div>${esc(o.voice_line || 'Voice-over: generated voice')}</div>` : ''}</div>`;
      }
      default: return null;
    }
  }
  // The forward pilot's pull-back (layout "pull"). The map starts close on the port and draws away while "Projection" stays on screen.
  function pullTick(rt, o, el) {
    const map = el.querySelector('.dc-map');
    return (tau) => { const u = ease((tau - o.from) / (o.to - o.from)); if (map) map.style.transform = `scale(${lerp(2.4, 1, u).toFixed(3)})`; };
  }
  const TICKS = { hook: hookTick, baylight: bayTick, steps: stepsTick, spark_lines: sparkLinesTick, bars: barsTick, lifts: barsTick, hits: hitsTick, worth: pullTick, slam: slamTick, pair: pairTick, pilot: pilotTick };
  function buildOverlays(rt) {
    const box = document.createElement('div'); box.className = 'dir-shot-ovl'; box.dataset.shot = rt.s.id;
    rt.items = [];
    for (const o of rt.s.overlays || []) {
      if (o.when && !STORY.conditionHolds(o.when, rt.r, rt.B, CFG)) continue;
      const html = overlayHtml(rt, o); if (html == null) continue;
      const el = document.createElement('div'); el.className = `dov dov-${o.type} dov-s${rt.s.id}${o.layout ? ` dov-${o.layout}` : ''}${o.black ? ' dov-black' : ''}`; el.innerHTML = html; el.style.opacity = '0'; // layout: a look tied to the overlay, not to a shot number
      box.appendChild(el); rt.items.push({ o, el, tick: TICKS[o.type] ? TICKS[o.type](rt, o, el) : null });
    }
    rt.caps = (rt.s.captions || []).map((c) => ({ c, f: fill(STORY.captionText(c, rt.r, rt.B, CFG) || '', rt) }));
    rt.ovl = box; $('dir-ovl').appendChild(box);
  }
  // Shots 3 and 5: the brief flow draws itself (window.StageBrief, recorded data). Until it is on the page the director holds
  // the slot with what the bundle recorded, marked as such.
  let lastBrief = '';
  function briefState(row, sentence, tau) {
    const out = { sentence, stage: row.stage, recorded: true, full: !!row.full };
    if (row.progress_from != null) out.progress = clamp01((tau - row.progress_from) / (row.progress_to - row.progress_from));
    return out;
  }
  function driveBrief(rt, tau) {
    const row = STORY.briefAt(rt.s.brief, tau), el = $('dir-brief'), api = window.StageBrief;
    document.body.classList.toggle('dir-brief-on', !!row);
    if (!row) { el.hidden = true; if (lastBrief !== 'off') { lastBrief = 'off'; if (api && api.show) api.show(null); } return; }
    const sentence = row.sentence || STORY.sentenceFor(rt.r, rt.B, CFG), key = `${rt.i}|${row.stage}|${sentence}|${row.progress_from != null ? Math.round(tau * 8) : ''}`;
    if (key === lastBrief) return;
    lastBrief = key;
    if (api && api.show) {
      el.hidden = true;
      try { api.show(briefState(row, sentence, tau)); } catch (e) { D.errors.push('StageBrief.show: ' + e); }
      return;
    }
    el.hidden = false; // the brief flow isn't on the page: hold its slot, say so
    el.innerHTML = `<div class="dc-eyebrow">VOYAGE BRIEF · ${esc(row.stage.toUpperCase())}</div><div class="dbf-say">“${esc(sentence)}”</div>${pendCard('brief flow')}`;
  }

  // ------------------------------------------------------------------ per-frame apply
  let active = -1, lastCapKey = '';
  function apply(t) {
    const k = segAt(t), g = SEGS[k]; if (!g) return false;
    const i = g.i, tau = Math.max(0, t - starts[k] + g.from), rt = RT.get(i);
    D.t = t; D.shot = g.id;
    if (!rt || !rt.ready || (!rt.B && !rt.chapter)) return false;
    if (active !== i) { activate(rt); active = i; }
    const s = rt.s;
    if (rt.chapter) { // the flow draws this shot; a pure function of tau, like everything else here
      const c = STORY.chapterAt(s, tau, (FLOW.list().find((x) => x.id === s.v6.chapter) || {}).duration_s);
      if (rt.track !== c.chapter) { rt.track = c.chapter; rt.drawn = false; }
      FLOW.seek(c.chapter, c.tau).then((ok) => {
        rt.drawn = true;
        if (!ok && !rt.gap) { rt.gap = true; FLOW.hide(); console.warn(`[director] ${c.chapter} stopped: the shot holds on black with its captions`); }
      }, (e) => { rt.drawn = true; D.errors.push(`chapter ${c.chapter}: ${e}`); });
    } else {
      const pl = playAt(rt, tau);
      try { ST.seek(pl.pos, pl.rest); } catch (e) { D.errors.push('seek: ' + e); }
      const xr = (s.xray || []).find((x) => tau >= x.from && tau <= x.to);
      ST.setXray(!!xr, xr && xr.hl);
      applyFocus(rt, tau);
      if (rt.needResize && !Object.values(rt.bigState).includes('moving')) { rt.needResize = false; resizeStage(); }
      applyCamera(rt, cameraAt(rt, tau, pl.pos));
      applyRules(rt, tau);
      driveBrief(rt, tau);
    }
    let veil = 0;
    for (const it of rt.items) if (VEIL.has(it.o.type)) veil = Math.max(veil, env(tau, it.o.from, it.o.to, it.o.fade));
    $('dir-veil').style.opacity = rt.blocked || rt.gap ? '1' : (0.9 * veil).toFixed(3); // a plan the fresh checker refused is never on screen
    for (const it of rt.items) { const e = env(tau, it.o.from, it.o.to, it.o.fade); it.el.style.opacity = e.toFixed(3); it.el.style.transform = it.o.type === 'hook' || it.o.type === 'baylight' || it.o.type === 'hits' || it.o.type === 'slam' || it.o.type === 'pilot' || it.o.type === 'pair' ? '' : `translateY(${((1 - e) * 14).toFixed(1)}px)`; it.el.style.visibility = e > 0 ? 'visible' : 'hidden'; if (it.tick && e > 0) it.tick(tau); }
    let cap = null, ce = 0;
    for (const c of rt.caps) { const e = env(tau, c.c.from, c.c.to, 0.3); if (e > 0 && !c.f.empty) { cap = c; ce = e; break; } }
    const capEl = $('dir-cap'), key = cap ? `${i}|${cap.c.from}` : '';
    if (key !== lastCapKey) { capEl.innerHTML = cap ? `<div class="dc-bar"></div><div class="dc-txt">${cap.f.html}</div>` : ''; lastCapKey = key; }
    capEl.style.opacity = ce.toFixed(3); capEl.style.transform = `translateX(${((1 - ce) * -18).toFixed(1)}px)`;
    let dip = 0; // dip to black at shot boundaries and at the very start / end
    for (const b of [...starts, TOTAL]) dip = Math.max(dip, 1 - Math.abs(t - b) / DIP);
    $('dir-dip').style.opacity = clamp01(dip).toFixed(3);
    if (DRAFT) {
      const chip = $('dir-chip'); chip.hidden = false;
      chip.innerHTML = `<b>${esc(g.id)}/${SHOTS.length}</b> ${esc(s.title)} · ${esc(PICK.variant)} · t ${tau.toFixed(1)} s${rt.r.state === 'standin' ? ' · <span class="w">stand-in plan</span>' : ''} · ${esc(rt.r.id || '')}`;
    }
    return true;
  }

  // ------------------------------------------------------------------ boot + clock
  // Boots the flow, finds which chapters can play, and turns the gate's numbers into the hero's measured values.
  async function bootFlow() {
    try { await FLOW.boot(true); V6STATE = FLOW.state; }
    catch (e) { V6STATE = null; console.warn('[director] the v6 flow is off:', e); }
    const shots = CFG.shots.filter((s) => s.v6), gaps = [], chapters = [...new Set(shots.flatMap(STORY.shotChapters))]; // every chapter a shot cuts to must play
    await Promise.all(chapters.map(async (id) => { if (!V6STATE || !(await FLOW.prepare(id))) gaps.push(id); }));
    D.gaps = gaps;
    if (!V6STATE) return gaps;
    try { Object.assign(CFG.measured, STORY.figureValues((await FLOW.api('figures')).figures)); } // no figures: every numeric segment drops, the film still plays
    catch (e) { console.warn('[director] no figures:', e); }
    try {
      const d = await FLOW.api('replan?recorded=1'), g = d.gate || {}, m = {}; // the film plays the recorded answer, as the chapter asks for it (replanQuery); bare 'replan' is the unconfirmed live flow's before-only view
      const put = (k, v) => { if (v != null) m[k] = { value: v, source: 'the v6 gate, this launch' }; };
      if (g.status === 'shown') put('hero_recheck', g.recheck); // what really happened to the check in this launch, from the gate
      CFG.runtime.hero_shown = g.status === 'shown'; // shot 1's "THE PLAN CHANGES." and "FULL CHECKER: PASSED" only over a plan this launch's gate passed
      for (const s of shots) for (const h of [].concat(s.v6.holes || [])) if (h.speed && h.rate > 0) m[h.speed] = { value: String(h.rate), source: `shot ${s.id}'s own playback rate of the ${h.chapter} chapter` }; // "sped up N×" is the rate it plays at
      put('hero_sentence', (d.rule || {}).sentence); put('hero_gauges_line', (d.gauges || {}).line);
      // Boxes, forced, lifts, frames, heel, engine and date are the lattice's facts file's (change_* in /api/v6/figures): they win on screen.
      put('hero_rules_active', (d.rule_counts || {}).active); put('hero_rules_off', (d.rule_counts || {}).off);
      put('hero_heel_limit', (d.heel || {}).limit_deg); // the ship's own list limit, a rule constant, not a fact of this plan
      const f = g.status === 'shown' ? d.facts : null, walked = !!f && f.frames_passed && f.frames > 0;
      CFG.runtime.hero_walked = walked; CFG.runtime.hero_walked_repaired = walked && f.repaired; CFG.runtime.hero_walked_plain = walked && !f.repaired; // "crane sequence repaired" only over a repaired plan
      const cop = await FLOW.api('copilot').catch(() => ({})), take = cop.delivered || null; // the real-tools take, once the chapter carries it
      CFG.runtime.copilot_delivered = !!take;
      if (take) { put('copilot_lift_gap', take.lift_gap); put('copilot_scripted_replies', take.scripted_replies_said); }
      const xray = await FLOW.api('xray').catch(() => ({}));
      put('xray_source_label', xray.caption_label); CFG.runtime.xray_before = !!xray.before; // shot 6's before-fine-tuning lines only beside that trace
      put('xray_blocked_tokens', xray.blocked); put('xray_tokens', xray.tokens);
      const sandbox = await FLOW.api('sandbox').catch(() => ({})), bench = sandbox.measured || {};
      put('breakit_fast_s', bench.fast_median_s == null ? null : bench.fast_median_s.toFixed(1)); put('breakit_full_s', bench.full_median_s == null ? null : bench.full_median_s.toFixed(1));
      put('replay_drags', (sandbox.replay || {}).drags); // the pinned-drag page's own count, once it exists
      const reading = await FLOW.api('reading').catch(() => ({})), pack = await FLOW.api('package').catch(() => ({})), back = pack.background || {};
      put('reader_label', reading.label || (reading.reader || {}).label);
      CFG.runtime.reader_live = STORY.readLive(reading); // the reading on screen decides, never a launch flag
      CFG.runtime.reader_figures = !!reading.bars && (reading.reader || {}).on_device === true; // these captions name the on-device reader
      put('reader_seconds', reading.seconds == null ? null : reading.seconds.toFixed(1)); put('reader_peak_gb', reading.peak_gb); put('reader_adapter_mb', reading.adapter_mb);
      CFG.runtime.note_by_spark = /^Muse Spark/.test(pack.note_by || '');
      CFG.runtime.package_batch_confirmed = ((pack.receipt || {}).reader || {}).confirmed_by === 'user-authorised batch regeneration, no planner present (demo fixture)'; // shot 10's disclosure, only over a batch-confirmed package
      put('hero_background_boxes', back.boxes); put('hero_background_t', back.t == null ? null : Math.round(back.t).toLocaleString('en-US'));
      put('verify_seconds', ((pack.verification || {}).verify || {}).wall_s == null ? null : pack.verification.verify.wall_s.toFixed(1));
      Object.assign(CFG.measured, m);
    } catch (e) { console.warn('[director] no gate numbers:', e); }
    return gaps;
  }
  async function boot() {
    mount();
    try { const r = await fetch('/api/catalog', { headers: { Accept: 'application/json' } }); CAT = await r.json(); }
    catch (e) { D.errors.push('catalog: ' + e); $('dir-start-msg').textContent = 'catalogue unavailable: ' + e.message; return; }
    await new Promise((res) => { const w = () => (window.__stage && window.__stage.B ? res() : setTimeout(w, 80)); w(); });
    ST = window.__stage;
    if (V6 && window.StageV6) { FLOW = window.StageV6; SHOTS = STORY.applyFallbacks(SHOTS, await bootFlow()); }
    else if (V6) SHOTS = STORY.applyFallbacks(SHOTS, SHOTS.filter((s) => s.v6).map((s) => s.v6.chapter));
    else {
      if (window.StageBrief && window.StageBrief.ready) await Promise.race([window.StageBrief.ready(), new Promise((res) => setTimeout(res, 240000))]); // recorded gates settle first
      try { const r = await fetch('/api/brief/state', { headers: { Accept: 'application/json' } }); if (r.ok) BRIEF = await r.json(); } catch (e) { D.errors.push('brief state: ' + e); }
      try { const r = await fetch('/api/catalog', { headers: { Accept: 'application/json' } }); CAT = await r.json(); } catch (e) { D.errors.push('catalog: ' + e); }
    }
    layout();
    clearFocus();
    for (const [k, sel] of Object.entries(PANEL)) { const el = document.querySelector(sel); if (el) { const b = el.getBoundingClientRect(); ORIG[k] = [b.left, b.top, b.width, b.height]; } }
    const t0 = SEEK != null ? Math.min(SEEK, TOTAL - 1e-3) : 0;
    if (SEEK == null && !P.has('shot')) await Promise.all([...new Set(SEGS.map((g) => g.i))].map((i) => makeRt(i))); // all bundles before the take
    else await makeRt(SEGS[segAt(t0)].i);
    D.ready = true;
    if (SEEK != null) { // frozen frame for stills
      $('dir-start').hidden = true;
      apply(t0);
      const settled = () => { const rt = RT.get(SEGS[segAt(t0)].i); if (rt && rt.chapter && !rt.drawn) { apply(t0); setTimeout(settled, 100); } else requestAnimationFrame(() => { apply(t0); D.applied = true; }); };
      settled();
      setInterval(() => apply(t0), 250);
      return;
    }
    if (HOLD) {
      $('dir-start-msg').textContent = 'press space to start';
      await new Promise((res) => { const h = (e) => { if (e.type === 'click' || e.code === 'Space') { removeEventListener('keydown', h, true); removeEventListener('click', h, true); e.preventDefault(); e.stopPropagation(); res(); } }; addEventListener('keydown', h, true); addEventListener('click', h, true); });
    } else await new Promise((res) => setTimeout(res, 600));
    $('dir-start').hidden = true;
    D.started = true; D.startedAt = performance.now();
    let clock = Math.min(FROM, TOTAL - 0.5), last = performance.now();
    const loop = () => {
      const now = performance.now(), dt = (now - last) / 1000; last = now;
      const i = SEGS[segAt(Math.min(clock, TOTAL - 1e-3))].i;
      const rt = RT.get(i); if (!rt) makeRt(i);
      if (rt && rt.ready && (!rt.chapter || rt.drawn)) clock += dt; // hold the clock while a bundle or chapter loads (content stays a function of t)
      const t = Math.min(clock, TOTAL);
      apply(Math.min(t, TOTAL - 1e-3));
      if (t >= TOTAL) { $('dir-dip').style.opacity = '1'; D.done = true; D.wall_s = (now - D.startedAt) / 1000; return; }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
