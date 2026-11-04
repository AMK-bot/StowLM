/* Studio stress test. Geometry and drag semantics follow stage-v6breakit.js. */
(function () {
  'use strict';
  if (!window.Studio) return;

  const parse = (id) => {
    const m = /^(\d{3})(\d{2})(\d{2})$/.exec(String(id));
    return m ? { bay: +m[1], row: +m[2], tier: +m[3], deck: +m[3] >= 80 } : null;
  };
  const baysOf = (p) => p.bay % 2 ? [p.bay] : [p.bay - 1, p.bay + 1];
  const pad = (n, w) => String(n).padStart(w, '0');
  function sameStack(a, b) {
    const p = parse(a), q = parse(b);
    return !!(p && q && p.row === q.row && p.deck === q.deck && baysOf(p).some((x) => baysOf(q).includes(x)));
  }
  function candidates(cells, entries, boxes, held) {
    const long = (id) => +((boxes[id] || {}).length_ft || 20) !== 20;
    const key = (b, r, t) => `${b}|${r}|${t}`, taken = new Set(), columns = new Map(), out = [];
    for (const [slot, box] of entries) {
      if (box === held) continue;
      const p = parse(slot); if (!p) continue;
      baysOf(p).forEach((b) => taken.add(key(b, p.row, p.tier)));
      if (long(box) === long(held)) out.push({ slot, swap: box });
    }
    for (const cell of cells) {
      const p = parse(cell.id || cell); if (!p || !(p.bay % 2)) continue;
      const k = `${p.bay}|${p.row}|${+p.deck}`;
      if (!columns.has(k)) columns.set(k, { ...p, tiers: [] });
      columns.get(k).tiers.push(p.tier);
    }
    const free = (c) => {
      let t = null;
      for (const n of c.tiers.slice().sort((a, b) => a - b)) t = taken.has(key(c.bay, c.row, n)) ? null : t == null ? n : t;
      return t;
    };
    for (const col of columns.values()) {
      const t = free(col); if (t == null) continue;
      if (!long(held)) out.push({ slot: pad(col.bay, 3) + pad(col.row, 2) + pad(t, 2), swap: null });
      else {
        const aft = columns.get(`${col.bay + 2}|${col.row}|${+col.deck}`);
        if (aft && free(aft) === t) out.push({ slot: pad(col.bay + 1, 3) + pad(col.row, 2) + pad(t, 2), swap: null });
      }
    }
    return out;
  }
  const ease = (p) => { p = Math.max(0, Math.min(1, p)); return p * p * (3 - 2 * p); };
  const mix = (a, b, p) => a + (b - a) * p;
  const mergeFrame = (a, b) => {
    const out = { ...(a || {}) };
    for (const [k, v] of Object.entries(b || {})) if (v != null && !(Array.isArray(v) && !v.length && out[k])) out[k] = v;
    for (const k of ['stability_passed', 'strength_passed']) out[k] = (b || {})[k] == null ? null : b[k];
    return out;
  };

  window.Studio.step({
    id: 'stress', order: 40, title: 'Stress test', icon: 'stress', features: ['sandbox'], ship: true,
    logic: { parse, sameStack, candidates, mergeFrame },
    payloadFields: ['cases[].kind', 'cases[].name', 'cases[].box', 'cases[].slot', 'cases[].checked_on', 'cases[].fast', 'cases[].verdict', 'cases[].fast.legal', 'cases[].verdict.legal', 'cases[].fast.wall_s', 'cases[].verdict.wall_s', 'cases[].fast.outcome.title', 'cases[].verdict.outcome.title', 'cases[].fast.outcome.plain', 'cases[].verdict.outcome.plain', 'cases[].fast.outcome.rule_id', 'cases[].verdict.outcome.rule_id', 'cases[].fast.rows', 'cases[].verdict.rows', 'cases[].fast.rows[].rule_id', 'cases[].verdict.rows[].rule_id', 'cases[].fast.rows[].name', 'cases[].verdict.rows[].name', 'cases[].fast.rows[].message', 'cases[].verdict.rows[].message', 'cases[].fast.rows[].measured', 'cases[].verdict.rows[].measured', 'cases[].fast.rows[].limit', 'cases[].verdict.rows[].limit', 'cases[].fast.rows[].unit', 'cases[].verdict.rows[].unit', 'cases[].fast.rows[].boxes', 'cases[].verdict.rows[].boxes', 'cases[].fast.rows[].n_boxes', 'cases[].verdict.rows[].n_boxes', 'cases[].fast.rows[].note', 'cases[].verdict.rows[].note', 'cases[].fast.rows[].diagnostic', 'cases[].verdict.rows[].diagnostic', 'cases[].fast.gauges.frame', 'cases[].verdict.gauges.frame', 'cases[].fast.gauges.diagnostic_only', 'cases[].verdict.gauges.diagnostic_only', 'cases[].verdict.gauges.swapped_box', 'cases[].fast.lifts', 'cases[].verdict.lifts', 'cases[].fast.scope', 'cases[].verdict.scope', 'cases[].fast.quality', 'cases[].verdict.quality', 'replay.label', 'running_label', 'challenge_tag', 'challenge_note', 'derived', 'stored_label', 'baseline'],
    mount(panel, data, ctx) {
      const d = data.sandbox, esc = ctx.esc;
      const preferred = [d.cases.find((c) => c.kind === 'securing'), d.cases.find((c) => c.verdict.legal), d.cases.find((c) => c.kind === 'segregation')].filter(Boolean);
      const cases = preferred.length ? preferred : d.cases.slice(0, 3);
      panel.innerHTML = `<div class="st-stress-panel">
        <p class="st-card-sub">Drag any box to another stack</p>
        <div class="st-stress-cases" hidden>${cases.map((c, i) => `<button type="button" class="st-btn ${i ? 'secondary' : 'primary'} st-stress-case" data-stress-case="${esc(c.kind)}">${esc(c.name)}</button>`).join('')}</div>
        <div class="st-card st-stress-verdict" aria-live="polite" hidden></div>
        <div class="st-stress-rules" hidden></div>
        <p class="st-meta st-stress-status" role="status"></p>
        <p class="st-meta">${esc(d.challenge_note)}</p>
        <details class="st-disclosure st-stress-challenge"><summary>Details</summary><p class="st-meta">${esc(d.challenge_tag)}</p>${d.derived ? `<p class="st-meta">${esc(d.derived)}</p>` : ''}<p class="st-meta">${esc(d.stored_label)}</p></details>
      </div>`;
      const $ = (s) => panel.querySelector(s), ST = () => ctx.stage || window.__stage;
      let entered = false, ready = false, life = 0, frame = 0, run = null, over = null, gfx = null, canvas = null;
      let bundle = null, previous = null, baseShip = null, baseLoc = null, layout = '', candidateCache = new Map();
      let loaded = Promise.resolve(), hasVerdict = false, dragIntent = null;
      const completedCases = new Map();
      const baseFrame = () => (((ST().B.physics || {}).frames || []).slice(-1)[0] || null);
      const ext = (slot, box) => ST().scene.shipExtOfSlot(slot, box);
      const centre = (e, top) => ST().scene.project((e.x0 + e.x1) / 2, (e.y0 + e.y1) / 2, top ? e.z1 : (e.z0 + e.z1) / 2);
      function dragSource(e, id) {
        const scene = ST().scene, shapes = scene.kit.faces(e);
        for (const name of ['top', 'front', 'end']) {
          const polygon = shapes[name];
          if (!polygon || !polygon.length) continue;
          const middle = polygon.reduce((p, q) => [p[0] + q[0] / polygon.length, p[1] + q[1] / polygon.length], [0, 0]);
          const samples = [middle];
          for (const vertex of polygon) for (const fraction of [.3, .6, .8]) samples.push([mix(middle[0], vertex[0], fraction), mix(middle[1], vertex[1], fraction)]);
          for (const p of samples) if (p[0] >= 0 && p[1] >= 0 && p[0] < canvas.clientWidth && p[1] < canvas.clientHeight && scene.hit(p[0], p[1]) === id) return { point: p, occluded: false };
        }
        const p = centre(e);
        return { point: p, occluded: scene.hit(p[0], p[1]) !== id };
      }
      function setLayout(kind, mv) {
        if (!ready) return;
        const key = `${kind}|${mv ? mv.box + '|' + mv.to : ''}`;
        if (layout === key) return;
        layout = key;
        const state = ST().S.state;
        state.ship = new Map(baseShip); state.loc = new Map(baseLoc);
        if (mv && kind !== 'base') {
          state.ship.delete(mv.from);
          if (kind === 'moving' || kind === 'moved') state.ship.delete(mv.to);
          if (kind === 'moved') {
            state.ship.set(mv.to, mv.box); state.loc.set(mv.box, { w: 'ship', k: mv.to });
            if (mv.swap) { state.ship.set(mv.from, mv.swap); state.loc.set(mv.swap, { w: 'ship', k: mv.from }); }
          }
        }
        ST().redraw({});
      }
      function resize() {
        if (!over || !canvas) return;
        const r = canvas.getBoundingClientRect(), scale = ST().scene.pixelScale || window.devicePixelRatio || 1;
        Object.assign(over.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
        over.width = Math.round(r.width * scale); over.height = Math.round(r.height * scale);
        gfx.setTransform(scale, 0, 0, scale, 0, 0); candidateCache.clear();
      }
      function clearCanvas() { if (gfx && over) gfx.clearRect(0, 0, over.clientWidth, over.clientHeight); }
      function faces(e, fill, stroke) {
        const kit = ST().scene.kit, f = kit.faces(e);
        for (const face of [f.front, f.end, f.top]) {
          kit.poly(gfx, face);
          if (fill) { gfx.fillStyle = fill; gfx.fill(); }
          if (stroke) { gfx.strokeStyle = stroke; gfx.lineWidth = 1.8; gfx.stroke(); }
        }
      }
      function box(e, id) { ST().scene.kit.containerBox(gfx, e, ST().B.boxes[id], { detail: true }); }
      function held(e, point) {
        const y = (e.y0 + e.y1) / 2, at = ST().scene.unproject(point[0], point[1], y);
        const x = (e.x1 - e.x0) / 2, h = (e.z1 - e.z0) / 2;
        return { ...e, x0: at.x - x, x1: at.x + x, z0: at.z - h, z1: at.z + h };
      }
      function arc(a, b, p, lift) {
        const e = { ...b }, q = ease(p), up = 4 * p * (1 - p) * lift;
        for (const k of ['x0', 'x1', 'y0', 'y1', 'z0', 'z1']) e[k] = mix(a[k], b[k], q) + (k[0] === 'z' ? up : 0);
        return e;
      }
      function targets(id) {
        if (!candidateCache.has(id)) {
          const options = candidates(ST().B.geometry.slots || [], [...baseShip], ST().B.boxes, id);
          for (const c of d.cases) if (c.box === id && !options.some((item) => item.slot === c.slot)) options.push({ slot: c.slot, swap: c.verdict.gauges.swapped_box || null });
          candidateCache.set(id, options.map((c) => {
          const e = ext(c.slot, id); if (!e) return null;
          const p = centre(e, true); return { ...c, ext: e, x: p[0], y: p[1] };
          }).filter(Boolean));
        }
        return candidateCache.get(id);
      }
      function nearest(id, point, from) {
        let best = null, distance = 150;
        for (const c of targets(id)) {
          if (c.slot === from) continue;
          const dist = Math.hypot(c.x - point[0], c.y - point[1]);
          if (dist < distance) { best = c; distance = dist; }
        }
        return best;
      }
      function snapAt(r, at) {
        // The intended prepared tier can sit behind another stack in this camera.
        // Pin it only when the real drag reaches its projected destination.
        if (r.intent && Math.hypot(at[0] - r.intent.destination[0], at[1] - r.intent.destination[1]) <= 24) return r.intent.target;
        return nearest(r.mv.box, at, r.mv.from);
      }
      function stopRun() {
        if (run) { run.dead = true; finishRun(run, { cancelled: true }); clearTimeout(run.pollTimer); if (run.abort) run.abort.abort(); }
        run = null; dragIntent = null;
        if (ready) { setLayout('base'); ST().setGaugeFrame(null); }
        clearCanvas();
        if (over) over.classList.remove('st-stress-grabbing');
      }
      function reset() {
        stopRun(); $('.st-stress-verdict').hidden = true; $('.st-stress-rules').hidden = true; $('.st-stress-status').textContent = '';
      }
      function finishRun(r, result) {
        if (r.resolveCompletion) { r.resolveCompletion(result); r.resolveCompletion = null; }
      }
      function grab(id, point) {
        const loc = baseLoc.get(id);
        if (!loc || loc.w !== 'ship') return false;
        reset();
        run = { mv: { box: id, from: loc.k }, phase: 'carry', cursor: point, t0: performance.now() };
        run.completion = new Promise((resolve) => { run.resolveCompletion = resolve; });
        over.classList.add('st-stress-grabbing'); ST().selectBox(null);
        return true;
      }
      function drop() {
        const r = run;
        if (!r || r.phase !== 'carry') return;
        over.classList.remove('st-stress-grabbing');
        r.dropFrom = held(ext(r.mv.from, r.mv.box), r.cursor);
        r.t0 = performance.now();
        if (!r.snap) { r.phase = 'back'; return; }
        r.mv.to = r.snap.slot; r.mv.swap = r.snap.swap; r.phase = 'drop';
        r.kase = d.cases.find((c) => c.box === r.mv.box && c.slot === r.mv.to);
        if (!r.kase) startLive(r);
      }
      async function startLive(r) {
        $('.st-stress-status').textContent = d.running_label;
        try {
          const job = await ctx.post('try', { box: r.mv.box, slot: r.mv.to });
          if (r.dead) return;
          r.job = job.job;
          poll(r);
        } catch (_) { if (!r.dead) { $('.st-stress-status').textContent = 'The checker did not return a verdict. Try a prepared move.'; finishRun(r, { cancelled: true }); } }
      }
      async function poll(r) {
        if (r.dead || !entered) return;
        try {
          r.abort = new AbortController();
          const response = await fetch('/api/v6/try/' + encodeURIComponent(r.job), { signal: r.abort.signal, headers: { Accept: 'application/json' } });
          if (!response.ok) throw new Error('checker unavailable');
          const job = await response.json();
          if (r.dead || !entered) return;
          if (job.recorded) { r.kase = { fast: job.fast, verdict: job.full, checked_on: job.checked_on }; return; }
          r.live = job;
          if (job.full) return;
          if (job.status === 'failed' || job.status === 'superseded') { $('.st-stress-status').textContent = 'The full check did not finish. Try a prepared move.'; finishRun(r, { cancelled: true }); return; }
          r.pollTimer = setTimeout(() => poll(r), job.fast ? 400 : 120);
        } catch (_) {
          if (!r.dead && entered) { $('.st-stress-status').textContent = 'The full check did not finish. Try a prepared move.'; finishRun(r, { cancelled: true }); }
        }
      }
      function showAnswer(r, v, fast, recorded) {
        const key = `${fast}|${recorded}|${v.legal}`;
        if (r.answerKey === key) return;
        r.answerKey = key; r.answer = v;
        const o = v.outcome, rows = v.rows.slice().sort((a, b) => (b.rule_id === o.rule_id ? 1 : 0) - (a.rule_id === o.rule_id ? 1 : 0));
        const card = $('.st-stress-verdict'); card.className = 'st-card st-stress-verdict'; card.hidden = false;
        const recordedLabel = d.replay.label;
        card.innerHTML = `<div class="st-stress-tags">${ctx.chip(fast ? d.running_label : recorded ? recordedLabel : 'Full checker', fast ? 'warn' : recorded ? 'recorded' : null)}${fast && recorded ? ctx.chip(recordedLabel, 'recorded') : ''}</div>
          <p class="st-callout ${fast ? 'warn' : 'st-stress-checker'}">${fast ? 'Fast screen' : v.legal ? 'Full checker · pass' : 'Full checker · refused'}</p>
          <h3 class="st-card-title">${esc(o.title)}</h3><p class="st-card-sub">${esc(o.plain)}</p>
          ${v.gauges.diagnostic_only ? '<p class="st-meta">Gauges: diagnostic check of the requested stow.</p>' : ''}
          <p class="st-meta">${esc(v.lifts)}</p><p class="st-meta">${esc(v.scope)} ${esc(v.quality || '')}</p>
          <div class="st-stress-move"><span class="st-meta">${esc(r.mv.box)} → ${esc(r.mv.to)}</span><button type="button" class="st-btn ghost st-stress-undo">Undo</button></div>`;
        const rules = $('.st-stress-rules'); rules.hidden = false;
        rules.innerHTML = `<div class="st-stress-rule-list">${rows.length ? rows.map((row) => `<div class="st-stress-rule"><p class="st-callout ${row.rule_id === o.rule_id ? 'bad' : 'warn'}">${esc(row.name)}${row.diagnostic ? ' · Diagnostic snapshot' : ''}</p><details class="st-disclosure"><summary>Details</summary><p class="st-meta"><code class="st-mono">${esc(row.rule_id)}</code></p><p class="st-meta">${esc(row.message)}</p>${typeof row.measured === 'number' && typeof row.limit === 'number' ? `<dl class="st-kv"><dt>Measured / limit</dt><dd>${esc(row.measured.toFixed(3))} / ${esc(row.limit.toFixed(3))}${row.unit === 'ratio' ? '' : ' ' + esc(row.unit || '')}</dd></dl>` : ''}${row.note ? `<p class="st-meta">${esc(row.note)}</p>` : ''}</details></div>`).join('') : `<p class="st-callout ${fast ? 'warn' : 'ok'}">${fast ? 'No hard rule flagged by the screen.' : 'No rule broken.'}</p>`}</div>`;
        const frame0 = baseFrame(), target = fast ? { ...mergeFrame(frame0, v.gauges.frame), badge: { text: 'Fast screen', cls: 'warn', title: 'Rough estimate. The full check decides.' } } : mergeFrame(frame0, v.gauges.frame);
        ST().setGaugeFrame(target);
        const timings = r.kase ? `Recorded: fast screen ${r.kase.fast.wall_s.toFixed(2)} s, full check ${r.kase.verdict.wall_s.toFixed(1)} s. The wait is cut here.` : '';
        $('.st-stress-status').textContent = `${recorded ? r.kase.checked_on || '' : ''}${timings ? ' · ' + timings : ''}`;
        if (!fast) {
          ctx.log({ who: 'Full checker', where: recorded ? null : 'on this laptop', text: o.title + ' · ' + o.plain, label: recorded ? recordedLabel : 'Full check' });
          ctx.emit('stress:verdict', { box: r.mv.box, slot: r.mv.to, verdict: v });
          const checkedCase = d.cases.find((c) => c.box === r.mv.box && c.slot === r.mv.to);
          if (checkedCase) completedCases.set(`${checkedCase.box}|${checkedCase.slot}`, v.legal);
          const results = [...completedCases.values()];
          if (!hasVerdict && v.legal && completedCases.size >= 2 && results.includes(false) && results.includes(true)) { hasVerdict = true; ctx.done(); }
          panel.querySelectorAll('[data-stress-case]').forEach((button) => {
            const c = cases.find((item) => item.kind === button.dataset.stressCase);
            button.classList.toggle('primary', !!(c && c.verdict.legal)); button.classList.toggle('secondary', !(c && c.verdict.legal));
          });
          finishRun(r, { cancelled: false, verdict: v });
        }
      }
      function draw(now) {
        if (!entered) return;
        frame = requestAnimationFrame(draw); clearCanvas();
        const r = run; if (!ready || !r || r.phase === 'press') return;
        const src = ext(r.mv.from, r.mv.box), elapsed = (now - r.t0) / 1000;
        if (r.auto && r.phase === 'carry') {
          const p = Math.min(1, elapsed / 1.1), q = ease(p), a = r.auto.a, b = r.auto.b;
          r.cursor = [mix(a[0], b[0], q), mix(a[1], b[1], q) - 120 * 4 * p * (1 - p)];
          r.snap = p > .65 ? r.auto.target : null;
          if (p >= 1) { drop(); return; }
        }
        if (r.phase === 'carry') {
          setLayout('out', r.mv);
          for (const c of targets(r.mv.box)) if (c.slot !== r.mv.from && Math.hypot(c.x - r.cursor[0], c.y - r.cursor[1]) < 170) faces(c.ext, null, c === r.snap ? css('--st-orange') : css('--st-muted'));
          const e = held(src, r.cursor); box(e, r.mv.box); faces(e, null, css('--st-orange'));
        } else if (r.phase === 'back') {
          if (elapsed >= .3) { reset(); return; }
          box(arc(r.dropFrom, src, elapsed / .3, 2), r.mv.box);
        } else if (r.phase === 'drop') {
          setLayout('moving', r.mv);
          const dst = ext(r.mv.to, r.mv.box), p = Math.min(1, elapsed / .3);
          box(arc(r.dropFrom, dst, p, 1), r.mv.box);
          if (r.mv.swap) box(arc(ext(r.mv.to, r.mv.swap), ext(r.mv.from, r.mv.swap), p, 5), r.mv.swap);
          if (p >= 1) { r.phase = 'settle'; r.t0 = now; setLayout('moved', r.mv); }
        } else if (r.phase === 'settle') {
          let answer = null, fast = false, recorded = !!r.kase;
          if (r.kase) { fast = elapsed < 2.8; answer = fast ? r.kase.fast : r.kase.verdict; }
          else if (r.live) {
            if (r.live.fast && !r.fastSeenAt) r.fastSeenAt = now;
            fast = !r.live.full || (r.live.fast && now - r.fastSeenAt < 900);
            answer = fast ? r.live.fast : r.live.full;
          }
          if (answer) showAnswer(r, answer, fast, recorded);
          const tone = !r.answer ? css('--st-orange') : r.answer.legal ? css('--st-green') : css('--st-red');
          const entries = [...ST().S.state.ship], lit = new Set([r.mv.box]);
          for (const [slot, id] of entries) if (sameStack(slot, r.mv.to)) lit.add(id);
          if (r.answer && r.answer.rows[0] && r.answer.rows[0].n_boxes <= 6) r.answer.rows[0].boxes.forEach((id) => lit.add(id));
          for (const [slot, id] of entries) if (lit.has(id)) faces(ext(slot, id), null, tone);
        }
      }
      const css = (name) => getComputedStyle(panel).getPropertyValue(name).trim();
      const point = (e) => { const r = over.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
      function down(e) {
        if (!ready || e.button) return;
        const at = point(e);
        const intent = dragIntent && dragIntent.life === life && Math.hypot(at[0] - dragIntent.source[0], at[1] - dragIntent.source[1]) <= 8 ? dragIntent : null;
        if (run && run.phase === 'settle') reset();
        if (run) return;
        const hit = ST().scene.hit(at[0], at[1]);
        const id = intent && intent.occluded ? intent.box : hit;
        if (!id || !baseLoc.has(id) || (intent && id !== intent.box)) return;
        e.preventDefault(); e.stopPropagation();
        // Synthetic shell drags keep their dispatch target, but have no native pointer to capture.
        try { if (over.setPointerCapture) over.setPointerCapture(e.pointerId); } catch (_) { /* Native drags still capture when available. */ }
        run = { mv: { box: id }, phase: 'press', start: at, cursor: at, intent };
      }
      function move(e) {
        if (!ready) return;
        const at = point(e);
        if (!run || run.phase === 'settle') { over.style.cursor = ST().scene.hit(at[0], at[1]) ? 'grab' : ''; return; }
        e.stopPropagation();
        if (run.phase === 'press' && Math.hypot(at[0] - run.start[0], at[1] - run.start[1]) > 5) {
          const intent = run.intent;
          if (grab(run.mv.box, at)) run.intent = intent;
        }
        if (run && run.phase === 'carry') { run.cursor = at; run.snap = snapAt(run, at); }
      }
      function up(e) {
        if (!run) return;
        if (run.phase === 'press') { const id = run.mv.box; run = null; ST().selectBox(id); return; }
        if (run.phase === 'carry' && !run.auto) { run.cursor = point(e); run.snap = snapAt(run, run.cursor); drop(); }
      }
      function cancel() { if (run && run.phase !== 'settle') reset(); }
      function keydown(e) { if (e.key === 'Escape') cancel(); }
      function prepared(kind) {
        if (!ready) return;
        const c = cases.find((item) => item.kind === kind), loc = c && baseLoc.get(c.box);
        if (!loc) return;
        const source = ext(loc.k, c.box), destination = ext(c.slot, c.box);
        if (!source || !destination) return;
        const a = centre(source), b = centre(destination, true);
        if (!grab(c.box, a)) return;
        run.auto = { a, b: [b[0], b[1] - 34], target: { slot: c.slot, ext: destination, swap: c.verdict.gauges.swapped_box || null } };
      }
      panel.addEventListener('click', (e) => {
        const button = e.target.closest('[data-stress-case]'); if (button) prepared(button.dataset.stressCase);
        if (e.target.closest('.st-stress-undo')) reset();
      });
      async function enter() {
        if (entered) return loaded;
        entered = true; const mine = ++life; ctx.center(null);
        loaded = (async () => {
          if (!bundle) {
            const replan = await ctx.api('replan');
            const response = await fetch('/api/stage?plan_id=' + encodeURIComponent(replan.plans.sandbox), { headers: { Accept: 'application/json' } });
            if (!response.ok) throw new Error('sandbox plan unavailable');
            bundle = await response.json(); if (d.baseline) bundle.physics = d.baseline;
          }
          if (!entered || mine !== life) return;
          for (let i = 0; i < 100 && entered && mine === life && !(ST() && ST().B); i++) await ctx.wait(100);
          if (!entered || mine !== life) return;
          const stage = ST(); if (!stage || !stage.B) return;
          previous = { B: stage.B, k: stage.S.k }; stage.setPlaying(false); stage.load(bundle, 'end');
          baseShip = new Map(stage.S.state.ship); baseLoc = new Map(stage.S.state.loc); layout = ''; candidateCache = new Map();
          canvas = document.getElementById('ship-canvas');
          if (!canvas) return;
          over = document.createElement('canvas'); over.className = 'st-stress-overlay'; over.setAttribute('aria-label', 'Drag a container to check another stack');
          document.body.appendChild(over); gfx = over.getContext('2d');
          ready = true; resize();
          // A shell drag can still release on its old target after this turn leaves.
          // Keep those events from reaching a later visit's ship state.
          over.addEventListener('pointerdown', (e) => { if (entered && life === mine) down(e); });
          over.addEventListener('pointermove', (e) => { if (entered && life === mine) move(e); });
          over.addEventListener('pointerup', (e) => { if (entered && life === mine) up(e); });
          over.addEventListener('pointercancel', () => { if (entered && life === mine) cancel(); });
          window.addEventListener('resize', resize); window.addEventListener('keydown', keydown);
          panel.querySelectorAll('[data-stress-case]').forEach((button) => {
            const c = cases.find((item) => item.kind === button.dataset.stressCase), loc = baseLoc.get(c.box);
            button.hidden = !(loc && loc.w === 'ship' && ext(loc.k, c.box) && ext(c.slot, c.box));
          });
          reset(); frame = requestAnimationFrame(draw);
        })();
        return loaded;
      }
      async function start() {
        if (!entered) await enter();
        await loaded;
        if (!entered || !ready) return;
        $('.st-stress-cases').hidden = false;
      }
      function leave() {
        entered = false; ++life; cancelAnimationFrame(frame); stopRun(); ready = false;
        $('.st-stress-cases').hidden = true;
        const undo = panel.querySelector('.st-stress-undo'); if (undo) undo.hidden = true;
        window.removeEventListener('resize', resize); window.removeEventListener('keydown', keydown);
        if (over) over.remove(); over = null; gfx = null;
        if (previous && ST().B === bundle) ST().load(previous.B, previous.k);
        previous = null;
      }
      async function autoplay(auto) {
        await start();
        await loaded; if (!ready || !entered) return;
        const mine = life;
        const play = async (c) => {
          if (!c || !entered || life !== mine) return false;
          const loc = baseLoc.get(c.box);
          if (!loc || loc.w !== 'ship') return false;
          const sourceExt = ext(loc.k, c.box), targetExt = ext(c.slot, c.box);
          if (!sourceExt || !targetExt) return false;
          const pick = dragSource(sourceExt, c.box), source = pick.point, destination = centre(targetExt, true), rect = canvas.getBoundingClientRect();
          const intent = { life: mine, box: c.box, source, occluded: pick.occluded, destination, target: { slot: c.slot, ext: targetExt, swap: c.verdict.gauges.swapped_box || null } };
          dragIntent = intent;
          try {
            await auto.drag({ x: rect.left + source[0], y: rect.top + source[1] }, { x: rect.left + destination[0], y: rect.top + destination[1] }, { ms: 1200 });
          } finally { if (dragIntent === intent) dragIntent = null; }
          const active = run;
          if (!active || active.mv.box !== c.box || active.mv.to !== c.slot || !active.completion || life !== mine || !entered) return false;
          const result = await active.completion;
          if (result.cancelled || !entered || life !== mine) return false;
          await auto.wait(2500);
          return entered && life === mine;
        };
        await auto.wait(900);
        if (!await play(cases.find((c) => !c.verdict.legal))) return;
        if (!await play(cases.find((c) => c.verdict.legal))) return;
        await auto.wait(900);
      }
      return { enter, leave, start, autoplay };
    },
  });
})();
