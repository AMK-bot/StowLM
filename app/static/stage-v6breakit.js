/* v6 chapter 6: break it (shot 8, and the ship-grab end of shot 1 at negative tau). Data: /api/v6/sandbox.
 *
 *   seek(tau, false)  film: the 14 s beat from the prepared cases, a pure function of tau. tau in [-4.2, 0) is the hook,
 *                     the ship grab that fills StageV6Hook.hole (hook tau 4 to 8.2 maps to tau - 8.2 here).
 *   seek(tau, true)   interactive: the shell's frame tick. Free drags go to the live workers; prepared drags answer from
 *                     their recorded verdict at once, and the live check replaces it when it lands.
 *
 * The dragged box lives on a light overlay canvas over the ship. The ship layer redraws only when a box changes hands, so
 * the stage's own loop stays a few drawImage calls. Gauges take verdict frames only; a needle's overshoot is motion, and
 * the figure printed under it is always the frame's.
 */
(function () {
  'use strict';
  /* In the recorded playback (window.__play) the "live" check is the app's recorded job, replayed (logic14 p22). */
  const runningLabel = (d) => (window.__play ? 'Recorded verdict, replayed at its recorded pace' : d.running_label);
  const RED = [255, 90, 90], GREEN = [71, 245, 160], AMBER = [255, 165, 58], ICE = [150, 195, 255];
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const clamp = (t) => (t < 0 ? 0 : t > 1 ? 1 : t);
  const ease = (t) => { t = clamp(t); return t * t * (3 - 2 * t); };
  const lerp = (a, b, t) => a + (b - a) * t;
  const SHAKE = 0.62, GAUGE = 1.3, LIFT = 0.14, DROP = 0.22, BACK = 0.42;

  // ---------------------------------------------------------------- pure logic (app/tests/test_v6_sandbox.py runs it under node)
  const parse = (id) => { const m = /^(\d{3})(\d{2})(\d{2})$/.exec(String(id)); return m ? { bay: +m[1], row: +m[2], tier: +m[3], deck: +m[3] >= 80 } : null; };
  const baysOf = (p) => (p.bay % 2 ? [p.bay] : [p.bay - 1, p.bay + 1]);
  const pad = (n, w) => String(n).padStart(w, '0');
  function sameStack(a, b) {
    const p = parse(a), q = parse(b);
    if (!p || !q || p.row !== q.row || p.deck !== q.deck) return false;
    const other = baysOf(q);
    return baysOf(p).some((x) => other.includes(x));
  }
  const stackOf = (entries, slot) => entries.filter(([s]) => sameStack(s, slot));

  /* Legal-looking targets for a held box: a box of the same length to swap with, or the next free tier of a stack. Geometry
   * only; the checker decides. */
  function candidates(cells, entries, boxes, held) {
    const long = (id) => +((boxes[id] || {}).length_ft || 20) !== 20, cell = (b, r, t) => `${b}|${r}|${t}`;
    const out = [], taken = new Set(), columns = new Map();
    for (const [slot, box] of entries) {
      if (box === held) continue;
      const p = parse(slot); if (!p) continue;
      for (const b of baysOf(p)) taken.add(cell(b, p.row, p.tier));
      if (long(box) === long(held)) out.push({ slot, swap: box });
    }
    for (const c of cells) {
      const p = parse(c.id || c); if (!p || !(p.bay % 2)) continue;
      const key = `${p.bay}|${p.row}|${p.deck ? 1 : 0}`;
      if (!columns.has(key)) columns.set(key, { ...p, tiers: [] });
      columns.get(key).tiers.push(p.tier);
    }
    const nextFree = (col) => { let free = null; for (const t of col.tiers.slice().sort((a, b) => a - b)) free = taken.has(cell(col.bay, col.row, t)) ? null : free == null ? t : free; return free; };
    for (const col of columns.values()) {
      const t = nextFree(col); if (t == null) continue;
      if (!long(held)) { out.push({ slot: pad(col.bay, 3) + pad(col.row, 2) + pad(t, 2), swap: null }); continue; }
      const aft = columns.get(`${col.bay + 2}|${col.row}|${col.deck ? 1 : 0}`);
      if (aft && nextFree(aft) === t) out.push({ slot: pad(col.bay + 1, 3) + pad(col.row, 2) + pad(t, 2), swap: null });
    }
    return out;
  }
  function nearest(points, x, y, max) {
    let best = null, d = max * max;
    for (const c of points) { const dx = c.sx - x, dy = c.sy - y, e = dx * dx + dy * dy; if (e < d) { d = e; best = c; } }
    return best;
  }
  /* A verdict frame over the frame before it. A field the screen has no number for stays, so that gauge stays. Pass flags are
   * the frame's own: a stale green never survives into a fast screen. */
  function mergeFrame(prev, frame) {
    const out = { ...(prev || {}) };
    for (const [k, v] of Object.entries(frame || {})) if (v != null && !(Array.isArray(v) && !v.length && (prev || {})[k])) out[k] = v;
    for (const k of ['stability_passed', 'strength_passed']) out[k] = (frame || {})[k] == null ? null : frame[k];
    return out;
  }
  /* Needle motion only: starts at `from`, ends at `to`. The sine term is the little overshoot. */
  function needle(from, to, t, kick) {
    const w = 9, k = 3.6, e = Math.exp(-k * t);
    return to + (from - to) * Math.cos(w * t) * e + kick * Math.sin(w * t) * e;
  }
  const DRAGS = [
    { kind: 'securing', at: -4.2, hook: true, hold: 0.45, reach: 0.4, lift: 0.14, carry: 0.8, drop: 0.2, pulse: 0.13, fast: 2.08 },
    { kind: 'securing', at: 0.3, reach: 0.6, lift: 0.2, carry: 1.2, drop: 0.25, pulse: 0.25, fast: 2.8, full: 2.0, undo: 0.7 },
    { kind: 'legal', at: 8.3, reach: 0.4, lift: 0.2, carry: 1.0, drop: 0.25, pulse: 0.25, fast: 1.4, full: 99 },
  ];
  const PHASES = ['hold', 'reach', 'lift', 'carry', 'drop', 'pulse', 'fast', 'full', 'undo'];
  /* Where the film is at tau: which drag, which phase, seconds into it. Pure. */
  function beat(tau) {
    for (const d of DRAGS) {
      let t = tau - d.at;
      if (t < 0) continue;
      for (const phase of PHASES) {
        const len = d[phase]; if (!len) continue;
        if (t < len) return { kind: d.kind, hook: !!d.hook, phase, t, p: t / len, drag: d };
        t -= len;
      }
    }
    return { kind: null, hook: false, phase: 'idle', t: 0, p: 0, drag: null };
  }
  const fmt = (v, unit) => {
    if (typeof v !== 'number' || !isFinite(v)) return null;
    const text = Math.abs(v) >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(Math.abs(v) >= 10 ? 1 : 3);
    return unit && unit !== 'ratio' ? `${text} ${unit}` : text;
  };
  /* The case a film beat plays: its own kind, else any case of the same answer, else none and the beat is skipped. */
  function pickCase(cases, kind) {
    const same = cases.find((c) => c.kind === kind);
    if (same) return same;
    return cases.find((c) => c.verdict.legal === (kind === 'legal')) || null;
  }
  const refusedTone = (v) => ['crane_order', 'crane_unsettled', 'flagged_crane'].includes((v.outcome || {}).kind);
  const num = (v, digits) => (typeof v === 'number' && isFinite(v) ? v.toFixed(digits) : null);
  /* The clock line for a recorded drag. Each number is that drag's own. */
  const recordedTiming = (fastS, fullS) => { const f = num(fastS, 2), v = num(fullS, 1); return v ? `Recorded: ${f ? `fast screen ${f} s, ` : ''}full check ${v} s.` : ''; };
  const logic = { parse, sameStack, stackOf, candidates, nearest, mergeFrame, needle, beat, fmt, pickCase, refusedTone, num, recordedTiming, DRAGS };
  if (typeof window !== 'undefined') window.StageV6Breakit = { logic };
  if (typeof window === 'undefined' || !window.StageV6) return;

  // ---------------------------------------------------------------- the chapter
  const V = window.StageV6;
  V.chapter({ id: 'breakit', order: 60, title: 'Break it', feature: 'sandbox', ship: true, duration_s: 14,
    mount(host, d, ctx) {
      const esc = ctx.esc, $ = (s) => host.querySelector(s);
      const caseBy = (kind) => pickCase(d.cases, kind);
      const caseFor = (box, slot) => d.cases.find((c) => c.box === box && c.slot === slot);
      const m = d.measured;
      host.innerHTML = `
        <div class="v6-card v6b-left">
          <h2 class="v6-h">Break it</h2>
          <p class="v6b-try">Drag any box. The checker answers. Click a box for its checked placement facts.</p>
          <div class="v6b-cases">${d.cases.map((c) => `<button type="button" class="v6-btn ghost" data-case="${esc(c.kind)}">${esc(c.name)}</button>`).join('')}</div>
          <p class="v6b-note"><span class="v6-tag">${esc(d.challenge_tag)}</span> ${esc(d.challenge_note)}</p>
          ${d.derived ? `<p class="v6b-note">${esc(d.derived)}</p>` : ''}
          ${d.stored_label ? `<p class="v6b-note">${esc(d.stored_label)}</p>` : ''}
          <p class="v6b-load" hidden></p>
        </div>
        <div class="v6b-verdict" hidden></div>
        <div class="v6-card v6b-rules" hidden></div>
        <div class="v6-card v6b-why" hidden></div>
        <div class="v6b-no" hidden>NO.</div>`;

      let over = null, g = null, ready = false, bundle = null, entered = false, token = 0, lastTau = null;
      let prev = null, baseShip = null, baseLoc = null, layoutKey = null, cards = null, gaugeKey = null, deckZ = 0, cardKey = null, painted = false;
      let I = null; // interactive drag
      const ST = () => window.__stage;
      const scene = () => ST().scene;
      const frame0 = () => ((ST().B.physics || {}).frames || []).slice(-1)[0] || null;

      // ---- the ship's stack, boxes and layouts
      function moved(box, from, to, swap) {
        const ship = new Map(baseShip);
        ship.delete(from); ship.delete(to); ship.set(to, box);
        if (swap) ship.set(from, swap);
        return ship;
      }
      function setLayout(kind, mv) {
        const key = kind === 'base' ? 'base' : `${kind}|${mv.box}|${mv.to || ''}`;
        if (key === layoutKey) return;
        layoutKey = key;
        const st = ST().S.state;
        st.ship = kind === 'moved' ? moved(mv.box, mv.from, mv.to, mv.swap) : new Map(baseShip);
        st.loc = new Map(baseLoc);
        if (kind === 'out' || kind === 'both') { st.ship.delete(mv.from); if (kind === 'both' && mv.swap) st.ship.delete(mv.to); }
        if (kind === 'moved') { st.loc.set(mv.box, { w: 'ship', k: mv.to }); if (mv.swap) st.loc.set(mv.swap, { w: 'ship', k: mv.from }); }
        ST().redraw({});
      }
      const extOf = (slot, box) => scene().shipExtOfSlot(slot, box);
      const centre = (e) => scene().project((e.x0 + e.x1) / 2, (e.y0 + e.y1) / 2, (e.z0 + e.z1) / 2);
      const topCentre = (e) => scene().project((e.x0 + e.x1) / 2, (e.y0 + e.y1) / 2, e.z1);
      function heldExt(src, cx, cy) {
        const lx = (src.x1 - src.x0) / 2, ly = (src.y1 - src.y0) / 2, y = (src.y0 + src.y1) / 2, h = src.z1 - src.z0, at = scene().unproject(cx, cy, y);
        return { x0: at.x - lx, x1: at.x + lx, y0: y - ly, y1: y + ly, z0: at.z - h / 2, z1: at.z + h / 2, deck: src.deck };
      }
      const arc = (a, b, t, up) => {
        const u = ease(t), bump = 4 * t * (1 - t) * up, f = (k) => lerp(a[k], b[k], u);
        return { x0: f('x0'), x1: f('x1'), y0: f('y0'), y1: f('y1'), z0: f('z0') + bump, z1: f('z1') + bump, deck: b.deck };
      };

      // ---- overlay drawing
      function sizeOver() {
        const c = document.getElementById('ship-canvas'), w = c.clientWidth, h = c.clientHeight, dpr = scene().pixelScale;
        if (over.width !== Math.round(w * dpr) || over.height !== Math.round(h * dpr)) { over.width = Math.round(w * dpr); over.height = Math.round(h * dpr); }
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.clearRect(0, 0, w, h);
      }
      function faces(gfx, e, fill, stroke, lw) {
        const K = scene().kit, f = K.faces(e);
        for (const face of [f.front, f.end, f.top]) { K.poly(gfx, face); if (fill) { gfx.fillStyle = fill; gfx.fill(); } if (stroke) { gfx.strokeStyle = stroke; gfx.lineWidth = lw || 1.4; gfx.stroke(); } }
      }
      function drawShadow(e, target) {
        const K = scene().kit, z = target != null ? target : deckZ, gap = Math.max(0, e.z0 - z), a = Math.max(0.12, 0.42 - gap * 0.015);
        K.poly(g, K.faces({ ...e, z0: z - 0.01, z1: z }).top);
        g.fillStyle = `rgba(0,0,0,${a})`; g.fill();
      }
      function drawBox(e, box, opt) { scene().kit.containerBox(g, e, ST().B.boxes[box], opt || { detail: true }); }
      function drawMass(e, box) {
        const mass = (ST().B.boxes[box] || {}).mass_t; if (typeof mass !== 'number') return;
        const p = topCentre(e), text = `${mass.toFixed(1)} t`;
        g.font = '800 15px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
        const w = g.measureText(text).width + 16;
        g.fillStyle = 'rgba(7,14,27,.92)'; g.fillRect(p[0] - w / 2, p[1] - 34, w, 23);
        g.fillStyle = '#fff'; g.fillText(text, p[0], p[1] - 22);
      }
      function drawLit(cands, snap) {
        for (const c of cands) {
          const e = c.ext; if (!e) continue;
          const on = snap && c.slot === snap.slot;
          if (on) { faces(g, e, rgba(AMBER, 0.34), rgba(AMBER, 1), 2.2); continue; }
          const K = scene().kit; K.poly(g, K.faces(e).top);
          g.fillStyle = rgba(ICE, 0.06 + 0.14 * c.glow); g.fill();
          g.strokeStyle = rgba(ICE, 0.2 + 0.6 * c.glow); g.lineWidth = 1; g.stroke();
        }
        if (snap) {
          const p = topCentre(snap.ext), text = snap.swap ? `swap · ${snap.slot}` : snap.slot, e = snap.ext;
          g.font = '700 12px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
          const w = g.measureText(text).width + 14;
          p[0] = scene().project(e.x1, (e.y0 + e.y1) / 2, e.z1)[0] + w / 2 + 26; p[1] += 12; // clear of the held box, whatever its length
          g.fillStyle = 'rgba(7,14,27,.9)'; g.fillRect(p[0] - w / 2, p[1] - 30, w, 19);
          g.fillStyle = '#ffc670'; g.fillText(text, p[0], p[1] - 20);
        }
      }
      function drawFlare(exts, tone, amount) {
        if (amount <= 0.01) return;
        g.save();
        g.shadowColor = rgba(tone, 0.95); g.shadowBlur = 8 + 26 * amount;
        for (const e of exts) faces(g, e, null, rgba(tone, Math.min(1, 0.35 + amount)), 1.4 + 1.6 * amount);
        g.shadowBlur = 0; g.globalCompositeOperation = 'lighter';
        for (const e of exts) faces(g, e, rgba(tone, 0.16 + 0.5 * amount), null);
        g.restore();
      }
      function drawThud(e, t) { // ring and dust where the box lands; deterministic in t
        if (t > 0.5) return;
        const p = centre({ ...e, z0: e.z0, z1: e.z0 }), a = 1 - t / 0.5, s = scene().scale;
        g.strokeStyle = `rgba(255,235,205,${0.55 * a})`; g.lineWidth = 2 * a + 0.5;
        g.beginPath(); g.ellipse(p[0], p[1], s * (2 + 11 * t), s * (1 + 5 * t), 0.05, 0, 7); g.stroke();
        g.fillStyle = `rgba(214,200,180,${0.55 * a})`;
        for (let i = 0; i < 14; i++) { const q = (i * 2.399) % 6.283, r = s * (1.4 + 7 * t) * (0.6 + (i % 4) * 0.2); g.fillRect(p[0] + Math.cos(q) * r, p[1] + Math.sin(q) * r * 0.45 - 30 * t * t, 2, 2); }
      }
      function drawCursor(x, y, down) {
        g.save(); g.translate(x, y);
        if (down) { g.strokeStyle = 'rgba(255,255,255,.85)'; g.lineWidth = 2; g.beginPath(); g.arc(0, 0, 13, 0, 7); g.stroke(); }
        g.beginPath(); g.moveTo(0, 0); g.lineTo(0, 22); g.lineTo(6, 16.5); g.lineTo(10, 25.5); g.lineTo(14, 23.5); g.lineTo(10, 15); g.lineTo(18, 15); g.closePath();
        g.fillStyle = '#fff'; g.strokeStyle = '#0a1424'; g.lineWidth = 1.6; g.fill(); g.stroke();
        g.restore();
      }

      // ---- one move, drawn from a description. Film and interactive both land here.
      function flareSet(mv, answer) {
        const entries = [...moved(mv.box, mv.from, mv.to, mv.swap)], out = new Map();
        for (const [slot, box] of stackOf(entries, mv.to)) out.set(box, slot);
        const rows = (answer && answer.v.rows) || [];
        if (rows[0] && rows[0].n_boxes <= 6) for (const box of rows[0].boxes) { const hit = entries.find(([, b]) => b === box); if (hit) out.set(box, hit[0]); }
        return [...out].map(([box, slot]) => extOf(slot, box)).filter(Boolean);
      }
      function paintMove(mv, s) { // s: {phase, t, answerT, answer, cursor, down, cands, snap, dropFrom}
        const src = extOf(mv.from, mv.box), dst = mv.to ? extOf(mv.to, mv.box) : null, other = mv.swap ? extOf(mv.to, mv.swap) : null, home = mv.swap ? extOf(mv.from, mv.swap) : null;
        if (!src) return;
        if (s.phase === 'lift' || s.phase === 'carry') {
          setLayout('out', mv);
          if (s.cands) drawLit(s.cands, s.snap);
          const at = centre(src), k = s.phase === 'lift' ? ease(s.t / s.len) : 1;
          const e = heldExt(src, lerp(at[0], s.cursor[0], k), lerp(at[1], s.cursor[1], k));
          drawShadow(e, s.snap ? s.snap.ext.z0 : null);
          drawBox(e, mv.box);
          faces(g, e, null, rgba(AMBER, 0.9), 1.6);
          drawMass(e, mv.box);
        } else if (s.phase === 'back') { // released over nothing: it swings home
          setLayout('out', mv);
          drawBox(arc(s.dropFrom, src, s.t / s.len, 0.6), mv.box);
        } else if (s.phase === 'drop') {
          setLayout('both', mv);
          const k = s.t / s.len, e = { ...dst, x0: lerp(s.dropFrom.x0, dst.x0, ease(k)), x1: lerp(s.dropFrom.x1, dst.x1, ease(k)), y0: lerp(s.dropFrom.y0, dst.y0, ease(k)), y1: lerp(s.dropFrom.y1, dst.y1, ease(k)), z0: lerp(s.dropFrom.z0, dst.z0, k * k), z1: lerp(s.dropFrom.z1, dst.z1, k * k) };
          drawShadow(e, dst.z0);
          if (other) { const a = arc(other, home, k, 5); drawBox(a, mv.swap); }
          drawBox(e, mv.box);
        } else if (s.phase === 'settle') {
          const tone = s.answer ? (s.answer.v.legal ? GREEN : refusedTone(s.answer.v) ? AMBER : RED) : AMBER, at = s.answerT, shaking = !s.answer || at < SHAKE;
          setLayout(shaking ? 'both' : 'moved', mv);
          let dx = 0;
          if (s.answer && !s.answer.v.legal && at < SHAKE) dx = 5.5 * Math.sin(at * 62) * Math.exp(-5 * at);
          const push = dx / scene().scale, nudge = (e) => ({ ...e, x0: e.x0 + push, x1: e.x1 + push });
          if (shaking) { if (home) drawBox(home, mv.swap); drawBox(nudge(dst), mv.box); }
          drawThud(dst, s.t);
          const exts = flareSet(mv, s.answer).map(nudge);
          let amount;
          if (!s.answer) amount = 0.26 + 0.2 * Math.sin(s.t * 13);
          else if (s.answer.v.legal) amount = 0.14 + 0.62 * Math.exp(-3.2 * at);
          else amount = 0.24 + 0.05 * Math.sin(at * 3) + 0.72 * Math.exp(-2.6 * at);
          drawFlare(exts, tone, amount);
        } else if (s.phase === 'undo') {
          setLayout('both', mv);
          const k = s.t / s.len;
          if (other) drawBox(arc(home, other, k, 5), mv.swap);
          drawBox(arc(dst, src, k, 7), mv.box);
        }
      }

      // ---- gauges: verdict frames only
      function frames(fast, full) {
        const f0 = frame0(), fastFrame = fast && fast.gauges.frame ? { ...mergeFrame(f0, fast.gauges.frame), badge: { text: 'FAST SCREEN', cls: 'warn', title: 'Rough estimate. The full check decides.' } } : f0;
        const fullFrame = full && full.gauges.frame ? { ...full.gauges.frame, sf_n: full.gauges.frame.sf_n || (fastFrame || {}).sf_n, criteria: full.gauges.diagnostic_only ? 'requested stow, diagnostic check' : full.gauges.frame.criteria } : fastFrame;
        return { f0, fastFrame, fullFrame };
      }
      function gauge(key, target, from, t, hard) {
        const stage = ST();
        if (gaugeKey !== key) { gaugeKey = key; stage.setGaugeFrame(target); }
        const settled = !from || !target || t >= GAUGE;
        if (settled) { if (gaugeKey !== key + ':end') { gaugeKey = key + ':end'; stage.setGaugeFrame(target); } return; }
        const n = (k) => (typeof from[k] === 'number' && typeof target[k] === 'number' ? { from: from[k], to: target[k] } : null), gm = n('gm_m'), heel = n('heel_deg');
        stage.setGaugeNeedle({ gm: gm ? needle(gm.from, gm.to, t, (hard ? 0.09 : 0.03)) : null, heel: heel ? needle(heel.from, heel.to, t, (hard ? 0.34 : 0.1)) : null });
      }
      function clearGauge() { if (gaugeKey !== null) { gaugeKey = null; ST().setGaugeFrame(null); } }

      // ---- cards: rebuilt only when the answer changes
      function hideCards() { cardKey = null; $('.v6b-verdict').hidden = true; $('.v6b-rules').hidden = true; ctx.label(''); }
      function meter(row) {
        const a = fmt(row.measured, row.unit), b = fmt(row.limit, row.unit);
        if (!a || !b) return row.n_boxes >= 2 && row.n_boxes <= 6 ? `<div class="v6b-num">${row.boxes.map(esc).join(' · ')}</div>` : '';
        const bar = row.measured > 0 && row.limit > 0;
        const over = bar ? Math.min(1, row.limit / Math.max(row.measured, row.limit)) : 0;
        return `<div class="v6b-num"><b>${esc(a)}</b> <i>against limit</i> <b>${esc(b)}</b></div>${bar ? `<div class="v6b-bar"><span style="transform:scaleX(1)"></span><u style="left:${(over * 100).toFixed(1)}%"></u></div>` : ''}`;
      }
      function renderCards(mv, ans, opts) {
        const key = `${mv.box}|${mv.to}|${ans.stage}|${ans.source}|${ans.v.legal}|${ans.checked || ''}|${opts.film ? 1 : 0}`;
        if (key === cardKey) return;
        cardKey = key;
        const v = ans.v, o = v.outcome, lead = v.rows[0], fast = ans.stage === 'fast', tone = v.legal ? 'green' : refusedTone(v) ? 'amber' : 'red', recorded = ans.source === 'recorded';
        const pill = fast ? (v.legal ? 'Clear so far' : 'Flagged') : v.legal ? 'Full checker · pass' : 'Full checker · refused';
        const led = lead && lead.rule_id === o.rule_id ? lead : null;
        const stopped = o.kind === 'stow_rule' && o.crane_too ? 'The crane order stopped first, so this rule and the gauges come from a diagnostic check of the requested stow.'
          : o.kind === 'flagged' && o.crane_too ? 'The crane order can\'t reach it either.'
          : v.gauges.diagnostic_only ? 'The gauges show a diagnostic check of the requested stow, which is not the plan as edited.' : '';
        const snapshot = o.diagnostic ? v.rows.find((r) => r.rule_id === o.diagnostic.rule_id) : null;
        const running = fast && !recorded, tag = running ? runningLabel(d) : recorded ? d.label : 'Live';
        const swap = mv.swap ? `, swapped with ${mv.swap}` : '';
        const el = $('.v6b-verdict');
        el.className = `v6b-verdict ${tone}${fast ? ' fast' : ''}`;
        el.innerHTML = `<div class="v6b-head"><span class="v6b-pill">${esc(pill)}</span><span class="v6b-label">${esc(tag)}</span>${recorded && ans.checked ? `<span class="v6b-checked">${esc(ans.checked)}</span>` : ''}<span class="v6b-clock"></span></div>
          <h3>${esc(o.title)}</h3>${led ? meter(led) : ''}
          <p class="v6b-plain">${esc(o.plain)}</p>
          ${snapshot ? `<div class="v6b-diag"><b>Diagnostic snapshot</b> ${meter(snapshot)}</div>` : ''}
          ${(led && led.note) || (snapshot && snapshot.note) ? `<p class="v6b-fine">${esc((led && led.note) || snapshot.note)}</p>` : ''}
          ${(led && led.challenge) || (snapshot && snapshot.challenge) ? '<p class="v6b-fine">This rule involves the disclosed demo cargo.</p>' : ''}
          ${stopped ? `<p class="v6b-fine">${esc(stopped)}</p>` : ''}
          ${v.sentence ? `<p class="v6b-sentence">${esc(v.sentence.text)} <em>${esc(v.sentence.label)}</em></p>` : ''}
          ${v.lifts ? `<p class="v6b-lifts">${esc(v.lifts)}</p>` : ''}
          <p class="v6b-scope">${esc(v.scope)}${v.quality ? ` <b>${esc(v.quality)}</b>` : ''}</p>
          <div class="v6b-foot"><span>${esc(mv.box)} → ${esc(mv.to)}${esc(swap)}</span>${!fast && !opts.film ? `<button type="button" class="v6-btn ghost" data-explain-box="${esc(mv.box)}" data-explain-slot="${esc(mv.to)}">Explain this move</button>` : ''}${opts.film ? '' : '<button type="button" class="v6-btn ghost v6b-undo">Undo</button>'}</div>`;
        el.hidden = false;
        const timing = opts.timing || '';
        $('.v6b-rules').innerHTML = `<h2 class="v6-h">Rule checker · this move</h2>${v.rows.length ? '' : `<p class="v6b-clear">${fast ? 'No hard rule flagged by the screen.' : 'No rule broken.'}</p>`}
          <ul>${v.rows.map((r) => `<li><i>✕</i><b>${esc(r.name)}</b>${r.diagnostic ? '<span>diagnostic snapshot</span>' : r.count > 1 ? `<span>${esc(r.count)} conditions</span>` : ''}<small>${esc(r.rule_id)} · ${esc(r.message)}</small></li>`).join('')}</ul>
          <p class="v6b-timing">${esc(timing)}</p>${m ? m.lines.map((line) => `<p class="v6b-note">${esc(line)}</p>`).join('') : ''}`;
        $('.v6b-rules').hidden = false;
        ctx.label(running ? runningLabel(d) : recorded ? d.label : '');
      }

      // ---- film
      function filmMove(kind) {
        const c = caseBy(kind);
        const swap = c.verdict.gauges.swapped_box || (c.fast && c.fast.gauges.swapped_box) || null; // a renamed gauge reads as a plain move
        return { box: c.box, from: baseLoc.get(c.box).k, to: c.slot, swap };
      }
      function seekFilm(tau) {
        lastTau = tau;
        if (!ready) return;
        sizeOver(); painted = true;
        const b = beat(tau);
        $('.v6b-no').hidden = true;
        host.classList.add('film');
        host.classList.toggle('hook', b.hook);
        const c = b.kind ? caseBy(b.kind) : null;
        if (!c || b.phase === 'hold') { setLayout('base'); hideCards(); clearGauge(); return; }
        const mv = filmMove(b.kind), d0 = b.drag;
        const rest = [over.clientWidth * 0.62, over.clientHeight * 0.16];
        const a = centre(extOf(mv.from, mv.box)), z = topCentre(extOf(mv.to, mv.box)), up = [lerp(a[0], z[0], 0.5), Math.min(a[1], z[1]) - 150];
        const path = (t) => { const q = 1 - t; return [q * q * a[0] + 2 * q * t * up[0] + t * t * z[0], q * q * (a[1] - 8) + 2 * q * t * up[1] + t * t * (z[1] - 34)]; };
        const after = (phase) => { let t = 0; for (const p of PHASES) { if (p === phase) break; t += d0[p] || 0; } return t; };
        if (b.phase === 'reach') {
          setLayout('base'); hideCards(); clearGauge();
          const k = ease(b.p); drawCursor(lerp(rest[0], a[0], k), lerp(rest[1], a[1], k), b.p > 0.92);
          return;
        }
        if (b.phase === 'lift' || b.phase === 'carry') {
          hideCards(); clearGauge();
          const cursor = b.phase === 'lift' ? [a[0], a[1] - 34 * ease(b.p)] : path(ease(b.p));
          const cands = litNear(mv, cursor), snap = b.phase === 'carry' && b.p > 0.6 ? pinned(mv.box, mv.to) : null;
          paintMove(mv, { phase: b.phase, t: b.t, len: b.phase === 'lift' ? d0.lift : 1, cursor, cands, snap });
          drawCursor(cursor[0], cursor[1], true);
          return;
        }
        const end = path(1), dropFrom = heldExt(extOf(mv.from, mv.box), end[0], end[1]);
        if (b.phase === 'drop') { hideCards(); clearGauge(); paintMove(mv, { phase: 'drop', t: b.t, len: d0.drop, dropFrom }); drawCursor(end[0], end[1], false); return; }
        if (b.phase === 'undo') {
          hideCards();
          const { fullFrame, f0 } = frames(c.fast, c.verdict);
          gauge('undo', f0, fullFrame, b.t, false);
          paintMove(mv, { phase: 'undo', t: b.t, len: d0.undo });
          return;
        }
        const t = tau - d0.at - after('drop') - d0.drop;
        if (b.phase === 'pulse') { hideCards(); clearGauge(); paintMove(mv, { phase: 'settle', t, answerT: 0 }); return; }
        const { f0, fastFrame, fullFrame } = frames(c.fast, c.verdict);
        const hard = !c.verdict.legal;
        if (b.phase === 'fast') {
          const ans = c.fast ? { v: c.fast, stage: 'fast', source: 'recorded', checked: c.checked_on } : { v: c.verdict, stage: 'full', source: 'recorded', checked: c.checked_on };
          paintMove(mv, { phase: 'settle', t, answerT: b.t, answer: ans });
          renderCards(mv, ans, { film: true, timing: filmTiming(c, 'fast') });
          const up = clamp(b.t / 0.22), card = $('.v6b-verdict').style; // CSS animations run on the wall clock; film stays a function of tau
          card.opacity = up; card.transform = `translateY(${(26 * (1 - ease(up))).toFixed(1)}px)`;
          if (b.hook) { ctx.label(d.label); const el = $('.v6b-no'), k = clamp(b.t / 0.18); el.hidden = false; el.style.transform = `translate(-50%,-50%) scale(${lerp(1.5, 1, ease(k))})`; el.style.opacity = k; }
          gauge(`fast|${b.kind}`, fastFrame, f0, b.t, hard);
          return;
        }
        const ans = { v: c.verdict, stage: 'full', source: 'recorded', checked: c.checked_on };
        paintMove(mv, { phase: 'settle', t, answerT: b.t + d0.fast, answer: ans });
        renderCards(mv, ans, { film: true, timing: filmTiming(c, 'full') });
        $('.v6b-verdict').style.opacity = 1; $('.v6b-verdict').style.transform = 'none';
        gauge(`full|${b.kind}`, fullFrame, fastFrame, b.t, false);
      }
      function filmTiming(c, stage) {
        const f = c.fast ? num(c.fast.wall_s, 2) : null, v = num(c.verdict.wall_s, 1);
        if (stage === 'fast') return f ? `This drag's fast screen took ${f} s when recorded.` : '';
        return v ? `This drag: ${f ? `fast screen ${f} s, ` : ''}full check ${v} s when recorded. The wait is cut here.` : '';
      }

      // ---- candidates under the cursor
      function allCands(box) {
        const entries = [...baseShip], B = ST().B;
        return candidates((B.geometry || {}).slots || [], entries, B.boxes, box).map((c) => {
          const e = extOf(c.slot, box); if (!e) return null;
          const p = topCentre(e);
          return { ...c, ext: e, sx: p[0], sy: p[1], glow: 0 };
        }).filter(Boolean);
      }
      const candCache = new Map();
      const pinned = (box, slot) => (candCache.get(box) || []).find((c) => c.slot === slot) || null;
      function litNear(mv, cursor) {
        if (!candCache.has(mv.box)) candCache.set(mv.box, allCands(mv.box));
        const near = [];
        for (const c of candCache.get(mv.box)) {
          if (c.slot === mv.from) continue;
          const r = Math.hypot(c.sx - cursor[0], c.sy - cursor[1]);
          if (r < 170) { c.glow = 1 - r / 170; near.push(c); }
        }
        return near.sort((a, b) => b.glow - a.glow).slice(0, 7);
      }

      // ---- interactive
      const pointer = (e) => { const r = over.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
      function reset(keepCards) {
        if (I && I.pollTimer) clearTimeout(I.pollTimer);
        if (I) I.dead = true;
        I = null;
        if (ready) { setLayout('base'); clearGauge(); }
        if (!keepCards) hideCards();
      }
      function grab(box, cursor) {
        const loc = baseLoc.get(box);
        if (!loc || loc.w !== 'ship') return false;
        document.getElementById('tooltip').hidden = true;
        ST().S.hover = null;
        const swapless = { box, from: loc.k, to: null, swap: null };
        I = { mv: swapless, phase: 'lift', t0: performance.now(), cursor, down: true };
        over.classList.add('grabbing'); over.style.cursor = '';
        return true;
      }
      function drop(now) {
        const held = heldExt(extOf(I.mv.from, I.mv.box), I.cursor[0], I.cursor[1]);
        I.dropFrom = held;
        over.classList.remove('grabbing');
        if (!I.snap) { I.phase = 'back'; I.t0 = now; return; }
        I.mv = { ...I.mv, to: I.snap.slot, swap: I.snap.swap };
        I.phase = 'drop'; I.t0 = now; I.cands = null;
        I.kase = caseFor(I.mv.box, I.mv.to) || null;
        startLive();
      }
      async function startLive() {
        const run = I, body = { box: run.mv.box, slot: run.mv.to };
        if (run.kase) body.case = run.kase.kind;
        try {
          const r = await fetch('/api/v6/try', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          if (!r.ok) throw new Error(String(r.status));
          run.job = (await r.json()).job;
          poll(run);
        } catch (e) { run.failed = true; }
      }
      async function poll(run) {
        if (run.dead) return;
        try {
          const j = await V.api('try/' + run.job);
          if (run.dead) return;
          run.live = j;
          if (j.recorded) { run.kase = { fast: j.fast, verdict: j.full, checked_on: j.checked_on, replay: true }; run.live = null; return; } // the pinned cache: no checker ran
          if (j.full || j.status === 'failed' || j.status === 'superseded') { run.failed = j.status === 'failed'; return; }
        } catch (e) { run.failed = true; return; }
        run.pollTimer = setTimeout(() => poll(run), run.live && run.live.fast ? 400 : 120);
      }
      function currentAnswer(now) {
        const run = I, k = run.kase, live = run.live, sinceLand = (now - run.tLand) / 1000;
        if (live && live.full) return { v: live.full, stage: 'full', source: 'live', timing: `${live.fast_s != null ? `Fast screen ${live.fast_s.toFixed(2)} s, ` : ''}full check ${live.full_s.toFixed(1)} s, live on this machine.` };
        if (k) {
          if (k.fast && sinceLand < 0.9) return { v: k.fast, stage: 'fast', source: 'recorded', checked: k.checked_on, timing: filmTiming(k, 'fast') };
          return { v: k.verdict, stage: 'full', source: 'recorded', checked: k.checked_on, timing: k.replay ? recordedTiming(k.fast && k.fast.wall_s, k.verdict.wall_s) : `${filmTiming(k, 'full').replace(' The wait is cut here.', '')} ${run.failed ? '' : 'Live check running.'}` };
        }
        if (live && live.fast) return { v: live.fast, stage: 'fast', source: 'live', timing: `Fast screen ${live.fast_s.toFixed(2)} s.` };
        return null;
      }
      function tick(now) {
        if (!ready) return;
        if (!I) { if (painted) { sizeOver(); painted = false; } return; }
        sizeOver(); painted = true;
        if (I.auto && (I.phase === 'lift' || I.phase === 'carry')) stepAuto(now);
        const t = (now - I.t0) / 1000, mv = I.mv;
        if (I.phase === 'lift' && t >= LIFT && !I.auto) { I.phase = 'carry'; I.t0 = now; }
        if (I.phase === 'lift' || I.phase === 'carry') {
          const cands = litNear(mv, I.cursor);
          I.snap = I.auto ? (I.auto.p > 0.6 ? pinned(mv.box, I.auto.slot) : null) : nearest(cands, I.cursor[0], I.cursor[1], 150);
          paintMove(mv, { phase: I.phase, t, len: LIFT, cursor: I.cursor, cands, snap: I.snap });
          if (I.auto) drawCursor(I.cursor[0], I.cursor[1], true);
          return;
        }
        if (I.phase === 'back') {
          if (t >= DROP) { reset(); return; }
          paintMove(mv, { phase: 'back', t, len: DROP, dropFrom: I.dropFrom });
          return;
        }
        if (I.phase === 'drop') {
          if (t >= DROP) { I.phase = 'settle'; I.t0 = I.tLand = now; }
          else { paintMove(mv, { phase: 'drop', t, len: DROP, dropFrom: I.dropFrom }); return; }
        }
        if (I.phase === 'undo') {
          if (t >= BACK) { reset(); return; }
          paintMove(mv, { phase: 'undo', t, len: BACK });
          if (I.gaugeFrom) gauge('undo', frame0(), I.gaugeFrom, t, false);
          return;
        }
        const ans = currentAnswer(now), key = ans ? `${ans.stage}|${ans.source}` : '';
        if (key !== I.ansKey) { I.gaugeFrom = I.gaugeNow || frame0(); I.ansKey = key; I.tAns = now; I.firstAns = I.firstAns || now; }
        const sinceAns = ans ? (now - I.tAns) / 1000 : 0;
        paintMove(mv, { phase: 'settle', t: (now - I.tLand) / 1000, answerT: ans ? (now - I.firstAns) / 1000 : 0, answer: ans });
        if (ans) {
          renderCards(mv, ans, { timing: ans.timing });
          const pair = frames(ans.stage === 'fast' ? ans.v : (I.kase && I.kase.fast) || (I.live && I.live.fast), ans.stage === 'full' ? ans.v : null);
          I.gaugeNow = ans.stage === 'fast' ? pair.fastFrame : pair.fullFrame;
          gauge(key + mv.box, I.gaugeNow, I.gaugeFrom, sinceAns, !ans.v.legal);
          const clock = $('.v6b-clock');
          if (ans.stage === 'fast' && ans.source === 'live' && clock) clock.textContent = `${((now - I.t0) / 1000).toFixed(0)} s`;
          else if (clock && clock.textContent) clock.textContent = '';
          if (I.failed && !I.kase && ans.stage === 'fast') {
            const label = $('.v6b-label'); if (label) label.textContent = 'Fast screen only. The full check didn\'t finish.';
            ctx.label('');
          }
        }
      }
      function stepAuto(now) {
        const au = I.auto, p = clamp((now - au.t0) / 1000 / au.dur);
        au.p = p;
        const q = 1 - ease(p);
        I.cursor = [q * q * au.a[0] + 2 * q * (1 - q) * au.up[0] + (1 - q) * (1 - q) * au.z[0], q * q * au.a[1] + 2 * q * (1 - q) * au.up[1] + (1 - q) * (1 - q) * au.z[1]];
        if (p >= 1) drop(now);
      }
      function prepared(kind) {
        if (!ready) return;
        reset();
        const c = caseBy(kind), loc = c && baseLoc.get(c.box);
        if (!loc) return;
        const a = centre(extOf(loc.k, c.box)), z = topCentre(extOf(c.slot, c.box));
        grab(c.box, a);
        I.phase = 'carry'; I.t0 = performance.now();
        I.auto = { t0: I.t0, dur: 0.95, a, z: [z[0], z[1] - 34], up: [lerp(a[0], z[0], 0.5), Math.min(a[1], z[1]) - 150], slot: c.slot, p: 0 };
      }
      function undo() {
        if (!I || I.phase !== 'settle') return;
        I.gaugeFrom = I.gaugeNow || null;
        I.phase = 'undo'; I.t0 = performance.now();
        if (I.pollTimer) clearTimeout(I.pollTimer);
        I.dead = true;
        hideCards();
      }
      function onDown(e) {
        if (!ready || e.button) return;
        const cursor = pointer(e), box = scene().hit(cursor[0], cursor[1]);
        if (!box) return;
        if (I && I.phase === 'settle') reset(); // a new drag supersedes the last one, which goes back in place
        if (I) return;
        if (over.setPointerCapture) over.setPointerCapture(e.pointerId);
        I = { mv: { box }, phase: 'press', cursor, start: cursor };
      }
      function onMove(e) {
        if (!I || I.phase === 'settle') { const at = pointer(e); over.style.cursor = scene().hit(at[0], at[1]) ? 'grab' : ''; return; }
        e.stopPropagation();
        const cursor = pointer(e);
        if (I.phase === 'press') { if (Math.hypot(cursor[0] - I.start[0], cursor[1] - I.start[1]) > 5) { const box = I.mv.box; I = null; if (grab(box, cursor)) { /* dragging */ } } return; }
        if (I.phase === 'lift' || I.phase === 'carry') I.cursor = cursor;
      }
      function onUp(e) {
        if (!I) return;
        if (I.phase === 'press') { const box = I.mv.box; I = null; why(box, whyLang); return; }
        if (I.phase === 'lift' || I.phase === 'carry') { if (!I.auto) { I.cursor = pointer(e); drop(performance.now()); } }
      }

      // ---- a box's checked placement facts, or one move's. The move can wait on a full check; nothing about the wait is a verdict.
      let whyLang = 'en', whyBox = null, whyTarget = null, whyToken = 0, whyAnswer = null;
      const whyHead = (title, lang) => `<div class="v6b-whyhead"><b>${esc(title)}</b>${langs(lang)}<button type="button" class="v6b-close" aria-label="Close">×</button></div>`;
      const whyTitle = (box, target) => (target ? `Move ${box} to ${target}` : `Placement facts for ${box}`);
      async function why(box, lang, target = null) {
        whyBox = box; whyLang = lang; whyTarget = target; whyAnswer = null;
        const mine = ++whyToken, el = $('.v6b-why'), started = performance.now();
        const pollLimit = target ? 240 : 80, pollDelay = target ? 750 : 250;
        let failure = null;
        el.hidden = false;
        el.innerHTML = `${whyHead(whyTitle(box, target), lang)}<p class="v6b-note v6b-why-progress" aria-live="polite"></p>`;
        for (let i = 0; i < pollLimit && mine === whyToken; i++) {
          const elapsed = Math.floor((performance.now() - started) / 1000);
          el.querySelector('.v6b-why-progress').textContent = `${target ? 'Waiting for move evidence (a new full check can take a few minutes)' : 'Loading checked placement facts'} · ${elapsed} s elapsed`;
          let a;
          try { a = await V.api(`explain?box=${encodeURIComponent(box)}&lang=${lang}${target ? `&slot=${encodeURIComponent(target)}` : ''}`); } catch (e) { a = { status: 'failed', error: String(e) }; }
          if (mine !== whyToken) return;
          if (a.status === 'done') { showWhy(a); return; }
          if (a.status === 'failed' || a.available === false) { failure = a.error || a.reason || 'not available for this box'; break; }
          await new Promise((r) => setTimeout(r, pollDelay));
        }
        if (mine === whyToken) el.querySelector('.v6b-why-progress').textContent = failure
          ? `Explanation unavailable: ${failure}. This is not a checker verdict.`
          : 'The explanation is still running or unavailable after this polling window. Ask again to retrieve it; this timeout is not a checker verdict.';
      }
      const langs = (lang) => `<span class="v6b-lang"><button type="button" data-lang="en" class="${lang === 'en' ? 'on' : ''}">EN</button><button type="button" data-lang="th" class="${lang === 'th' ? 'on' : ''}">ไทย</button></span>`;
      function languageFeedback(a) {
        if (a.lang !== 'th') return '';
        const checks = a.statements.map((s) => s.render_checks).find((c) => c && (c.thai_sense || (c.prior_language_checks || []).length));
        if (!checks) return '';
        const current = checks.thai_sense || { status: 'not_run', issues: [] };
        const completed = ['passed', 'flagged'].includes(current.status);
        const issues = (rows) => (rows || []).map((issue) => `<li>${esc(issue.id)}: ${esc(issue.reason)}</li>`).join('');
        const prior = (checks.prior_language_checks || []).map((entry) => {
          const check = entry.check || entry;
          const hash = entry.previous_payload_sha256 || entry.payload_sha256 || check.previous_payload_sha256 || '';
          return `<li>Prior check: ${esc(check.status || 'unreported')}${hash ? ` <code>${esc(hash)}</code>` : ''}${check.issues && check.issues.length ? `<ul>${issues(check.issues)}</ul>` : ''}</li>`;
        }).join('');
        return `<details class="v6b-language-check"><summary>Thai sense check: ${esc(current.status)} — model language feedback, not proof</summary>${current.issues && current.issues.length ? `<ul>${issues(current.issues)}</ul>` : completed ? '<p>No current wording issues were reported by this check; this is not a quality guarantee.</p>' : '<p>No completed model language check is available for this view.</p>'}${prior ? `<p>Prior language checks and flags are retained:</p><ul>${prior}</ul>` : ''}</details>`;
      }
      function moveNote(a) {
        const was = a.recorded ? `Recorded answer${a.checked_on ? `, ${a.checked_on}` : ''}. ` : '';
        if (a.move_check === 'not_checked') return `Move to ${a.target_slot} was not checked; see the invalid or unavailable request evidence.`;
        if (a.move_check === 'fast') return `Fast screen for move to ${a.target_slot}; full check still needed.`;
        if (a.move_check === 'full') return `${was}Full-checker verdict for move to ${a.target_slot}; see the reported rules and crane-replay limits.`;
        return `Requested move to ${a.target_slot}; see the reported evidence.`;
      }
      function showWhy(a) {
        whyAnswer = a;
        const evidenceLink = (e, si, ei) => {
          const href = /^(?:instance|plan|report|replay|sandbox|alternatives):[0-9a-f]{64}#/.test(e.href || '') ? e.href : '#';
          return `<div class="v6b-evidence-item"><a href="${esc(href)}" data-evidence-link="${si}.${ei}" aria-expanded="false" title="${esc(e.sha256)}">${esc(e.source)} ${esc(e.path)} — inspect evidence</a> <code>${esc(e.sha256)}</code> ${esc(e.value)}</div>`;
        };
        const summary = a.statements.find((s) => s.id === 'summary');
        const asked = (s) => (String(s.id).startsWith('what_if') ? 0 : 1); // a move's own findings come before the box's facts
        const rows = a.statements.map((s, si) => [s, si]).filter(([s]) => s.id !== 'summary').sort((x, y) => asked(x[0]) - asked(y[0])).map(([s, si]) => `<li>${esc(s.text)}${s.evidence.length ? `<details class="v6b-ev"><summary>Evidence (${s.evidence.length})</summary>${s.evidence.map((e, ei) => evidenceLink(e, si, ei)).join('')}</details>` : ''}</li>`).join('');
        const note = a.target_slot ? moveNote(a) : a.note || '';
        $('.v6b-why').innerHTML = `${whyHead(whyTitle(a.box, a.target_slot), a.lang)}
          <p class="v6b-whylabel"><span class="v6b-label">${esc(a.label)}</span>${a.recorded && !a.target_slot ? `<span class="v6b-note">${esc(a.recorded)}${a.checked_on ? ` · ${esc(a.checked_on)}` : ''}</span>` : ''}</p>
          ${summary ? `<p class="v6b-summary">${esc(summary.text)}</p>${a.spark ? `<p class="v6b-note">${esc(a.spark)}</p>` : ''}` : ''}
          <ul>${rows}</ul>${languageFeedback(a)}<p class="v6b-note">${esc(note)}${a.thai_note ? ' ' + esc(a.thai_note) : ''}</p>`;
      }
      function inspect(link) { // the value goes into the page only when it's asked for: some are whole ship tables
        const [si, ei] = link.dataset.evidenceLink.split('.').map(Number), item = link.parentElement, open = item.querySelector('.v6b-evidence-value');
        if (open) { open.remove(); link.setAttribute('aria-expanded', 'false'); return; }
        const pre = document.createElement('pre'); pre.className = 'v6b-evidence-value';
        pre.textContent = JSON.stringify(whyAnswer.statements[si].evidence[ei].value_json, null, 2);
        item.appendChild(pre); link.setAttribute('aria-expanded', 'true');
      }

      // ---- chapter lifecycle
      function anchor() { // sit on the stage's own panels, wherever the shell's grid put them
        const home = host.getBoundingClientRect(), ship = document.querySelector('.ship-panel').getBoundingClientRect(), rules = document.querySelector('.rules-panel').getBoundingClientRect();
        const l = $('.v6b-left').style; l.left = `${ship.left - home.left + 10}px`; l.top = `${ship.top - home.top + 10}px`;
        const r = $('.v6b-rules').style;
        r.left = `${rules.left - home.left}px`; r.top = `${rules.top - home.top}px`; r.width = `${rules.width}px`; r.height = `${rules.height}px`;
        const left = ship.left - home.left + 10, bottom = home.bottom - ship.bottom + 10, width = Math.min(760, ship.width * 0.56);
        for (const [sel, l, w] of [['.v6b-verdict', left, width], ['.v6b-why', left + width + 10, ship.width - width - 30]]) {
          const c = $(sel).style; c.left = `${l}px`; c.bottom = `${bottom}px`; c.width = `${w}px`;
        }
      }
      async function load(mine) {
        const note = $('.v6b-load');
        note.hidden = false; note.textContent = 'Bringing the ship alongside.';
        try {
          for (let i = 0; i < 200 && !(ST() && ST().B); i++) await new Promise((r) => setTimeout(r, 100));
          if (!bundle) {
            const id = (await V.api('replan')).plans.sandbox;
            if (!id) throw new Error('no sandbox plan');
            const r = await fetch('/api/stage?plan_id=' + encodeURIComponent(id), { headers: { Accept: 'application/json' } });
            if (!r.ok) throw new Error(String(r.status));
            bundle = await r.json();
            if (d.baseline) bundle.physics = d.baseline;
          }
          if (mine !== token) return;
          prev = { B: ST().B, k: ST().S.k };
          ST().setPlaying(false);
          ST().load(bundle, 'end');
          const st = ST().S.state;
          baseShip = new Map(st.ship); baseLoc = new Map(st.loc);
          deckZ = Math.min(...(bundle.geometry.slots || []).filter((c) => parse(c.id) && parse(c.id).deck).slice(0, 40).map((c) => extOf(c.id, null).z0));
          layoutKey = 'base'; gaugeKey = null;
          const placed = (c) => { const loc = baseLoc.get(c.box); return !!(loc && loc.w === 'ship' && extOf(loc.k, c.box) && extOf(c.slot, c.box)); };
          d.cases = d.cases.filter(placed); // a fixture ahead of this plan: that case isn't offered
          host.querySelectorAll('[data-case]').forEach((el) => { el.hidden = !d.cases.some((c) => c.kind === el.dataset.case); });
          document.body.classList.add('v6b');
          over = document.createElement('canvas'); over.className = 'v6b-over'; g = over.getContext('2d');
          document.getElementById('ship-wrap').appendChild(over);
          over.addEventListener('pointerdown', onDown); over.addEventListener('pointermove', onMove); over.addEventListener('pointerup', onUp);
          over.addEventListener('pointercancel', () => { if (I && I.phase !== 'settle') reset(); });
          over.addEventListener('mousemove', (e) => { if (I && I.phase !== 'settle') e.stopPropagation(); });
          over.addEventListener('click', (e) => e.stopPropagation());
          addEventListener('resize', anchor);
          anchor();
          ready = true; note.hidden = true;
          if (ctx.interactive) host.classList.remove('film'); else over.style.pointerEvents = 'none';
          if (lastTau != null && !ctx.interactive) seekFilm(lastTau);
        } catch (e) {
          note.hidden = true;
          throw e; // the shell treats this as a gap and moves on
        }
      }
      function enter() { entered = true; token += 1; return load(token); }
      function leave() {
        entered = false; token += 1;
        ctx.label('');
        if (!ready) return;
        reset(); candCache.clear();
        ready = false;
        document.body.classList.remove('v6b');
        over.remove(); over = null;
        removeEventListener('resize', anchor);
        $('.v6b-why').hidden = true; $('.v6b-no').hidden = true;
        if (prev && ST().B === bundle) ST().load(prev.B, prev.k);
      }
      host.addEventListener('click', (e) => {
        const c = e.target.closest('[data-case]'); if (c) { prepared(c.dataset.case); return; }
        if (e.target.closest('.v6b-undo')) { undo(); return; }
        if (e.target.closest('.v6b-close')) { whyToken++; $('.v6b-why').hidden = true; return; }
        const link = e.target.closest('[data-evidence-link]'); if (link) { e.preventDefault(); inspect(link); return; }
        const m = e.target.closest('[data-explain-box]'); if (m) { why(m.dataset.explainBox, whyLang, m.dataset.explainSlot); return; }
        const l = e.target.closest('[data-lang]'); if (l && whyBox) why(whyBox, l.dataset.lang, whyTarget);
      });
      addEventListener('keydown', (e) => { if (e.key === 'Escape' && I && (I.phase === 'lift' || I.phase === 'carry') && !I.auto) { I.snap = null; drop(performance.now()); } });
      if (window.StageV6Breakit) { window.StageV6Breakit.isReady = () => ready; window.StageV6Breakit.state = () => ({ phase: I && I.phase, job: I && I.job, card: cardKey, layout: layoutKey, live: I && I.live && I.live.status }); }
      return { enter, leave, seek(tau, interactive) { if (!entered) return; if (interactive) tick(performance.now()); else seekFilm(tau); } };
    } });
})();
