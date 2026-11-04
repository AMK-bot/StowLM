/* v6 opening: the hook (shot 1) and mess in (shot 3). Data: /api/v6/intake, recorded.
 *
 * Everything on screen is a pure function of tau, so a take repeats. State changes touch the DOM only when they change;
 * flights, dust and flashes are drawn on one canvas. Interactive mode runs the same timeline and holds at each question.
 * All chapters are laid out at 1920x1022 and scaled to fit (V6FX.fit), so positions are in design pixels.
 */
(function () {
  'use strict';
  const V = window.StageV6;
  const W = 1920, H = 1022;
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const outCubic = (k) => 1 - (1 - k) ** 3;
  const inOut = (k) => (k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2);
  const hash = (n) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
  const fit = (host, root) => {
    const w = host.clientWidth, h = host.clientHeight; if (!w) return 0;
    const s = Math.min(w / W, h / H);
    root.style.transform = `translate(${(w - W * s) / 2}px,${(h - H * s) / 2}px) scale(${s})`;
    return s;
  };
  const canvas = (el, w = W, h = H) => { const d = Math.min(2, devicePixelRatio || 1); el.width = w * d; el.height = h * d; const c = el.getContext('2d'); c.scale(d, d); return c; };
  /* A set style only when it differs: the frame loop calls this for everything. */
  const put = (el, key, value) => { if (el._v6 === undefined) el._v6 = {}; if (el._v6[key] !== value) { el._v6[key] = value; if (key === 'text') el.textContent = value; else if (key === 'state') el.dataset.s = value; else el.style[key] = value; } };
  const cursorHTML = '<svg class="v6n-cursor" width="34" height="42" viewBox="0 0 34 42" aria-hidden="true"><path d="M3 2 L3 33 L11 26 L17 39 L23 36 L17 24 L28 23 Z" fill="#fff" stroke="#070e1b" stroke-width="2.4" stroke-linejoin="round"/></svg>';
  window.V6FX = { W, H, clamp, lerp, outCubic, inOut, hash, fit, canvas, put, cursorHTML };

  /* A document falling onto the desk: accelerates, hits, settles. t is seconds since it started to fall. */
  const DROP = 0.15;
  function slap(t, rot) {
    if (t < 0) return null;
    if (t < DROP) { const k = 1 - (t / DROP) ** 2; return { o: clamp(t / 0.05), t: `translateY(${-70 * k}px) rotate(${rot + 7 * k}deg) scale(${1 + 0.7 * k})` }; }
    const u = (t - DROP) / 0.2, squash = u < 1 ? 0.028 * Math.sin(Math.PI * u) * (1 - u) : 0;
    return { o: 1, t: `rotate(${rot}deg) scale(${1 - squash})` };
  }
  function shake(tau, hits) {
    let x = 0, y = 0;
    hits.forEach((at, i) => { const t = tau - at - DROP; if (t >= 0 && t < 0.28) { const a = 7 * (1 - t / 0.28); x += a * Math.sin(t * 90 + i) * 0.7; y += a * Math.cos(t * 75 + i); } });
    return `translate(${x.toFixed(2)}px,${y.toFixed(2)}px)`;
  }
  function dust(c, tau, at, r, seed) {
    const t = tau - at - DROP; if (t < 0 || t > 0.55) return false;
    const k = t / 0.55; c.fillStyle = '#c8d6ec';
    for (let i = 0; i < 34; i++) {
      const edge = i % 4, u = hash(seed + i), v = hash(seed + i + 50), speed = 60 + 170 * v;
      const x0 = edge === 0 ? r.x : edge === 1 ? r.x + r.w : r.x + u * r.w, y0 = edge === 2 ? r.y : edge === 3 ? r.y + r.h : r.y + u * r.h;
      const dx = edge === 0 ? -1 : edge === 1 ? 1 : (u - 0.5), dy = edge === 2 ? -1 : edge === 3 ? 1 : (v - 0.5);
      c.globalAlpha = 0.34 * (1 - k); c.beginPath(); c.arc(x0 + dx * speed * outCubic(k), y0 + dy * speed * outCubic(k), 1 + 2 * v, 0, 7); c.fill();
    }
    c.globalAlpha = 1; return true;
  }
  const sheetColumns = (grid) => grid.reduce((n, r) => Math.max(n, r.length), 0);
  function sheetHTML(esc, grid) {
    const n = sheetColumns(grid);
    /* Column widths follow the table's own rows, not the title lines above it. */
    const body = grid.filter((r) => r.filter(Boolean).length >= 3);
    const width = Array.from({ length: n }, (_, x) => 3 + Math.min(24, Math.max(4, ...body.map((r) => (r[x] || '').length))));
    const total = width.reduce((a, b) => a + b, 0);
    const head = '<tr><th></th>' + width.map((w, i) => `<th style="width:${(100 * w / total * 0.975).toFixed(2)}%">${String.fromCharCode(65 + i)}</th>`).join('') + '</tr>';
    const rows = grid.map((row, y) => `<tr><th>${y + 1}</th>${Array.from({ length: n }, (_, x) => { const v = row[x] == null ? '' : row[x]; return `<td data-c="${String.fromCharCode(65 + x)}${y + 1}" title="${esc(v.length > 70 ? v.slice(0, 70) + '…' : v)}">${esc(v.length > 60 ? v.slice(0, 60) : v)}</td>`; }).join('')}</tr>`);
    return `<table>${head}${rows.join('')}</table>`;
  }
  /* The email as it is, with each quote (and the value inside it) wrapped where the text really says it. */
  function mailHTML(esc, text, quotes) {
    const found = [...quotes.keys()].map((q) => ({ q, at: text.indexOf(q) })).filter((x) => x.at >= 0).sort((a, b) => a.at - b.at);
    let html = '', pos = 0;
    found.forEach(({ q, at }, n) => {
      html += esc(text.slice(pos, at));
      const tokens = [...quotes.get(q)].filter(Boolean).sort((a, b) => b.length - a.length);
      const parts = tokens.length ? q.split(new RegExp(`(${tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`)) : [q];
      html += `<span class="v6n-quote" data-q="${n}" title="Exact quote">${parts.map((p) => (tokens.includes(p) ? `<b data-t="${esc(p)}">${esc(p)}</b>` : esc(p))).join('')}</span>`;
      pos = at + q.length;
    });
    return { html: html + esc(text.slice(pos)), order: found.map((x) => x.q) };
  }

  const T = { hits: [0.1, 0.5, 0.9], read: 1.35, fly: 2.7, rowGap: 0.2, cellGap: 0.05, flight: 0.55, answer: 0.32, between: 0.3, hold: 2.4 };
  /* Where the n-th written line sits on the photo, in % of its frame: [first top, step, height]. Twelve lines is the authority tally. */
  const photoGrid = (n) => (n > 9 ? [23.2, 6.16, 6.1] : [28.6, 9.6, 9]);
  const STATE = { read: 'Read', ask: 'Question', clear: 'Clear', planner: 'Planner' };
  const unit = (q, label) => (q.column === 'mass' && /\d/.test(label) ? `${label} kg` : label);

  V.chapter({ id: 'intake', order: 10, title: 'Mess in', feature: 'intake', duration_s: 16,
    mount(host, d, ctx) {
      const esc = ctx.esc;
      const doc = (kind) => d.documents.find((x) => x.kind === kind);
      const mail = doc('email'), sheet = doc('sheet'), photo = doc('photo');
      /* A document the recording no longer carries is simply off the desk, and nothing flies from it. */
      const on = (x, id) => !!x && x.id === id;
      d.flights = d.flights.filter((f) => on(mail, f.document) || on(sheet, f.document) || on(photo, f.document));
      const quotes = new Map();
      const note = (q, t) => { if (!quotes.has(q)) quotes.set(q, new Set()); if (t) quotes.get(q).add(t); };
      d.flights.forEach((f) => { if (on(mail, f.document)) note(f.quote, f.from); });
      d.questions.forEach((q) => q.spots.forEach((s) => { if (on(mail, s.document)) note(s.quote); }));
      const mailed = mail ? mailHTML(esc, mail.text || '', quotes) : { html: '', order: [] };
      const rowsHTML = d.rows.map((r, y) => `<tr data-y="${y}"><td data-k="id"></td><td data-k="iso"></td><td data-k="pod"></td><td data-k="mass"></td><td data-k="dg"></td><td data-k="reefer"></td><td class="st"><span></span></td></tr>`).join('');
      const grid = photoGrid(d.photo_lines.length), docsOn = [mail, sheet, photo].filter(Boolean).length;
      const closing = d.summary.table ? `<table class="v6n-figures"><thead><tr>${d.summary.table.head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${d.summary.table.rows.map(([what, a, b]) => `<tr><th>${esc(what)}</th><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join('')}</tbody></table><small class="v6n-figscope">${esc(d.summary.table.scope)}</small>` : '';
      host.innerHTML = `<div class="v6x v6n">
        <div class="v6n-desk">
          ${photo ? `<article class="v6n-doc v6n-photo"><img src="${esc(photo.image)}" alt="${esc(photo.title)}" draggable="false">
            ${d.photo_lines.map((q, i) => `<i class="v6n-line" data-p="${i}" style="top:${(grid[0] + i * grid[1]).toFixed(2)}%;height:${grid[2]}%" title="Unverified transcription: ${esc(q.replace(/\s+/g, ' '))}"></i>`).join('')}<em>${esc(photo.label)}</em></article>` : ''}
          ${mail ? `<article class="v6n-doc v6n-mail"><em>${esc(mail.label)}</em><pre>${mailed.html}</pre></article>` : ''}
          ${sheet ? `<article class="v6n-doc v6n-sheet"><em>${esc(sheet.label)}</em>${sheetHTML(esc, sheet.grid)}</article>` : ''}
          ${photo ? `<p class="v6n-cap">Photo readings are unverified transcriptions: the model's reading of handwriting, not checked OCR.</p>` : ''}
        </div>
        <div class="v6n-who"><b>${esc(d.who)}</b><span>${esc(d.label)}</span><small>${[d.wall_s ? `One recorded read took ${d.wall_s.toFixed(1)} s` : '', d.document_count > docsOn ? `${d.document_count} documents read, ${docsOn} on this desk` : ''].filter(Boolean).join(' · ')}</small></div>
        <aside class="v6n-panel">
          <header><h2>Load list</h2><b class="v6n-scope">${esc(d.list_scope || '')}</b><span class="v6n-count"></span></header>
          <table class="v6n-list"><thead><tr><th>Box</th><th>ISO</th><th>POD</th><th>kg</th><th>DG</th><th>Reefer</th><th></th></tr></thead><tbody>${rowsHTML}</tbody></table>
          <div class="v6n-checks">${d.checks.map((c) => `<div data-c="${esc(c.id)}"><i></i><span>${esc(c.label)}</span><b></b></div>`).join('')}</div>
          ${d.derived_note ? `<p class="v6n-derived" title="${esc(d.derived_detail || '')}">${esc(d.derived_note)}</p>` : ''}
          <section class="v6n-card" hidden></section>
          <section class="v6n-end" hidden><h3>${esc(d.summary.headline)}</h3>${closing}${d.summary.lines.map((l) => `<p>${esc(l)}</p>`).join('')}<small>${esc(d.summary.scope)}</small><button type="button" class="v6-btn" data-a="next" hidden>Next: the radio line</button></section>
        </aside>
        <canvas class="v6n-fx"></canvas>
        <div class="v6n-mouse" hidden>${cursorHTML}</div>
      </div>`;
      const root = host.firstElementChild, $ = (s) => root.querySelector(s);
      const c = canvas($('.v6n-fx')), desk = $('.v6n-desk'), who = $('.v6n-who'), card = $('.v6n-card'), end = $('.v6n-end'), mouse = $('.v6n-mouse'), count = $('.v6n-count');
      const docs = [{ el: $('.v6n-photo'), rot: -2.4 }, { el: $('.v6n-mail'), rot: 0.9 }, { el: $('.v6n-sheet'), rot: -0.35 }].filter((x) => x.el);
      const rowEls = [...root.querySelectorAll('.v6n-list tbody tr')];
      const cellOf = (y, k) => rowEls[y].querySelector(`[data-k="${k}"]`);
      const quoteEl = (q) => root.querySelector(`.v6n-quote[data-q="${mailed.order.indexOf(q)}"]`);
      const spotEl = (s) => (on(sheet, s.document) ? root.querySelector(`.v6n-sheet [data-c="${s.cell}"]`) : on(photo, s.document) ? root.querySelector(`.v6n-line[data-p="${s.photo}"]`) : quoteEl(s.quote));

      /* Schedule: fixed per data set, computed once. */
      const perRow = new Map();
      d.flights.forEach((f) => { const j = perRow.get(f.row) || 0; perRow.set(f.row, j + 1); f.t = T.fly + f.row * T.rowGap + j * T.cellGap; f.land = f.t + T.flight; f.el = spotEl(f); f.seed = f.row * 7 + j; });
      const lastLand = Math.max(T.fly, ...d.flights.map((f) => f.land));
      const openRows = [...new Set(d.questions.map((q) => q.row).filter((y) => y != null))].sort((a, b) => a - b);
      const checkAt = lastLand + 0.1, askGap = 0.3, askDone = checkAt + openRows.length * askGap;
      const amberAt = new Map(openRows.map((y, i) => [y, checkAt + i * askGap]));
      const tickAt = d.checks.map((x, i) => checkAt + ((i + 1) / d.checks.length) * (askDone - checkAt));
      const cardAt = askDone + 0.1, qt = d.questions.map((_, i) => cardAt + T.hold + i * T.between);
      const endAt = (qt.length ? qt[qt.length - 1] : cardAt) + 0.4;
      const landed = new Map(); d.flights.forEach((f) => { const k = `${f.row}:${f.column}`; landed.set(k, Math.max(landed.get(k) || 0, f.land)); });
      const rowLand = d.rows.map((_, y) => Math.max(...d.flights.filter((f) => f.row === y).map((f) => f.land), T.fly));
      d.rows.forEach((r, y) => { d.columns.forEach((k) => { const f = d.flights.find((x) => x.row === y && x.column === k); if (f) { const where = on(sheet, f.document) ? `${sheet.title}, cell ${f.cell}` : on(photo, f.document) ? `${photo.title}, unverified transcription` : mail.title; cellOf(y, k).title = `From the ${where}`; } }); });

      let scale = 0, geo = null, done = new Set(), skip = false, message = '', shown = null, nextLatched = false;
      const answered = (i, tau, live) => (live ? done.has(i) : tau >= qt[i]);
      function center(el) { const r = el.getBoundingClientRect(), b = root.getBoundingClientRect(); return { x: (r.left + r.right) / 2 / scale - b.left / scale, y: (r.top + r.bottom) / 2 / scale - b.top / scale, w: r.width / scale, h: r.height / scale }; }
      /* All positions read once, with the documents at rest. Nothing reads layout per frame after this. */
      function measure() {
        scale = fit(host, root); if (!scale) return false;
        const saved = docs.map((x) => [x.el.style.transform, x.el.style.opacity]);
        const deskT = desk.style.transform; desk.style.transform = 'none';
        docs.forEach((x) => { x.el.style.transform = `rotate(${x.rot}deg)`; });
        d.flights.forEach((f) => {
          const a = f.el ? center(on(mail, f.document) ? (f.el.querySelector(`[data-t="${CSS.escape(f.from)}"]`) || f.el) : f.el) : { x: 300, y: 300 };
          f.ax = a.x + (on(photo, f.document) ? (f.column === 'pod' ? -14 : -110) : 0); f.ay = a.y;
          const b = center(cellOf(f.row, f.column)); f.bx = b.x; f.by = b.y; f.bw = b.w; f.bh = b.h;
        });
        c.font = '600 15px ui-monospace, SFMono-Regular, Menlo, monospace';
        d.flights.forEach((f) => { f.wa = c.measureText(f.from).width; f.wb = c.measureText(f.to).width; });
        geo = { docs: docs.map((x) => { const r = x.el.getBoundingClientRect(), b = root.getBoundingClientRect(); return { x: (r.left - b.left) / scale, y: (r.top - b.top) / scale, w: r.width / scale, h: r.height / scale }; }),
          rows: rowEls.map((el) => center(el)), card: { x: 1500, y: 800 }, cells: d.questions.map((q) => (q.row == null ? { x: 1500, y: 800 } : center(q.column ? cellOf(q.row, q.column) : rowEls[q.row]))) };
        docs.forEach((x, i) => { x.el.style.transform = saved[i][0]; x.el.style.opacity = saved[i][1]; });
        desk.style.transform = deskT;
        return true;
      }
      function drawChip(text, w, x, y, s, tilt, amber, alpha) {
        const pw = (w + 18) * s, ph = 28 * s;
        c.save(); c.globalAlpha = alpha; c.translate(x, y); c.rotate(tilt);
        c.fillStyle = 'rgba(0,0,0,.34)'; c.beginPath(); c.roundRect(-pw / 2 + 5 * s, -ph / 2 + 8 * s, pw, ph, 5); c.fill();
        c.fillStyle = amber ? '#ffa53a' : '#f7f0dd'; c.beginPath(); c.roundRect(-pw / 2, -ph / 2, pw, ph, 5); c.fill();
        c.fillStyle = amber ? '#2b1700' : '#10233f'; c.font = `600 ${15 * s}px ui-monospace, SFMono-Regular, Menlo, monospace`; c.textAlign = 'center'; c.textBaseline = 'middle';
        c.fillText(text, 0, 1); c.restore();
      }
      function drawFlight(f, tau) {
        const p = (tau - f.t) / T.flight; if (p < 0 || p > 1) return false;
        const pos = (q) => { const e = inOut(q), arc = 90 + 40 * hash(f.seed); return [lerp(f.ax, f.bx, e), lerp(f.ay, f.by, e) - arc * Math.sin(Math.PI * e)]; };
        const asked = f.to === '?' || f.to !== f.from && d.questions.some((q) => q.row === f.row && q.column === f.column);
        c.strokeStyle = asked ? '#ffa53a' : '#47f5a0'; c.lineCap = 'round';
        for (let i = 1; i <= 5; i++) { const [x0, y0] = pos(clamp(p - 0.035 * i)), [x1, y1] = pos(clamp(p - 0.035 * (i - 1))); c.globalAlpha = 0.34 * (1 - i / 6); c.lineWidth = 7 - i; c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); }
        c.globalAlpha = 1;
        const [x, y] = pos(p), s = 1 + 0.42 * Math.sin(Math.PI * p), tilt = (1 - p) * (hash(f.seed + 3) - 0.5) * 0.5, swap = clamp((p - 0.42) / 0.16);
        if (swap < 1) drawChip(f.from, f.wa, x, y, s, tilt, false, 1 - swap);
        if (swap > 0) drawChip(f.to, f.wb, x, y, s, tilt, asked, swap);
        return true;
      }
      function flash(r, tau, at, color, dur) {
        const k = (tau - at) / dur; if (k < 0 || k > 1) return false;
        c.globalAlpha = 0.7 * (1 - k); c.strokeStyle = color; c.lineWidth = 3; const g = 3 + 12 * outCubic(k);
        c.strokeRect(r.x - r.w / 2 - g, r.y - r.h / 2 - g / 2, r.w + 2 * g, r.h + g); c.globalAlpha = 1; return true;
      }
      function drawScan(tau) {
        const k = (tau - T.read) / (T.fly - T.read + 0.2); if (k < 0 || k > 1) return false;
        geo.docs.forEach((r) => {
          const y = r.y + r.h * inOut(k), g = c.createLinearGradient(0, y - 90, 0, y);
          g.addColorStop(0, 'rgba(71,245,160,0)'); g.addColorStop(1, 'rgba(71,245,160,.26)');
          c.fillStyle = g; c.fillRect(r.x, Math.max(r.y, y - 90), r.w, Math.min(90, y - r.y));
          c.fillStyle = '#47f5a0'; c.fillRect(r.x, y - 1, r.w, 2);
        });
        return true;
      }
      function drawAnswer(i, tau) {
        const k = (tau - qt[i]) / T.answer; if (k < 0 || k > 1) return false;
        const q = d.questions[i], b = geo.cells[i], a = shown && shown.at === i && shown.opt ? shown.opt : geo.card, e = inOut(k);
        const label = unit(q, q.after), w = label.length * 9.2;
        drawChip(label, w, lerp(a.x, b.x, e), lerp(a.y, b.y, e) - 60 * Math.sin(Math.PI * e), 1 + 0.3 * Math.sin(Math.PI * k), 0, false, 1);
        return true;
      }
      let dirty = true, frameKey = '';
      const litEls = [...root.querySelectorAll('.v6n-quote,.v6n-line,.v6n-sheet td')];
      addEventListener('resize', () => { geo = null; frameKey = ''; shown = null; });
      function drawFx(tau, live) {
        c.clearRect(0, 0, W, H); let any = false;
        geo.docs.forEach((r, i) => { any = dust(c, tau, T.hits[i], r, i * 100) || any; });
        any = drawScan(tau) || any;
        for (const f of d.flights) { any = drawFlight(f, tau) || any; any = flash({ x: f.bx, y: f.by, w: f.bw, h: f.bh }, tau, f.land, f.to === '?' ? '#ffa53a' : '#47f5a0', 0.26) || any; }
        amberAt.forEach((at, y) => { any = flash(geo.rows[y], tau, at, '#ffa53a', 0.32) || any; });
        d.questions.forEach((q, i) => { if (answered(i, tau, live)) { any = drawAnswer(i, tau) || any; if (q.row != null) any = flash(geo.rows[q.row], tau, qt[i] + T.answer, '#47f5a0', 0.34) || any; } });
        dirty = any;
      }
      function fillCard(i, tau, live) {
        if (shown && shown.at === i && shown.live === live && shown.message === message) return;
        const q = d.questions[i], left = d.questions.length - (live ? done.size : i);
        card.innerHTML = `<header><b>${q.scope === 'document' ? 'One question for the whole document' : "The software asks, it doesn't guess"}</b><span>${left > 1 ? `${left} questions left` : 'Last question'}</span></header>
          <p>${esc(q.text)}</p>
          <div class="v6n-opts">${q.options.map((o, n) => `<button type="button" data-o="${n}" class="${o.recorded ? 'rec' : ''}">${esc(unit(q, o.label))}${o.recorded ? '<small>Synthetic planner answer</small>' : ''}</button>`).join('')}${live && left > 1 ? '<button type="button" class="ghost" data-a="rest">Answer the rest</button>' : ''}</div>
          ${message ? `<div class="v6n-msg">${esc(message)}</div>` : ''}`;
        card.hidden = false; card.style.transform = 'none'; if (card._v6) card._v6.transform = undefined;
        const rec = q.options.findIndex((o) => o.recorded);
        const o = center(card.querySelector(`[data-o="${rec}"]`));
        shown = { at: i, live, message, opt: o };
        geo.card = { x: o.x, y: o.y };
      }
      card.addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (!b) return;
        const live = ctx.interactive, i = d.questions.findIndex((_, n) => !done.has(n));
        if (!live || i < 0) return;
        if (b.dataset.a === 'rest') { skip = true; d.questions.forEach((_, n) => { done.add(n); ctx.release('q' + n); }); message = ''; return; }
        const q = d.questions[i], o = q.options[+b.dataset.o];
        if (!o.recorded) { message = `This take only has a recording for ${unit(q, q.after)}. Recorded mode makes no calls, so that answer can't be checked here.`; return; }
        message = ''; done.add(i); ctx.release('q' + i);
      });
      end.querySelector('[data-a=next]').onclick = () => ctx.next();

      return {
        enter() { done = new Set(); skip = false; message = ''; shown = null; nextLatched = false; geo = null; frameKey = ''; end.querySelector('[data-a=next]').hidden = true; },
        seek(raw, live) {
          if (!geo && !measure()) return;
          let tau = clamp(raw, 0, 16);
          if (live) { const i = d.questions.findIndex((_, n) => !done.has(n)); if (i >= 0 && ctx.waitAt(tau, 'q' + i, qt[i])) tau = qt[i]; }
          const key = `${tau}|${done.size}|${message}|${!!live}`; if (key === frameKey) return; frameKey = key;
          docs.forEach((x, i) => { const p = slap(tau - T.hits[i], x.rot); put(x.el, 'opacity', p ? String(p.o) : '0'); put(x.el, 'transform', p ? p.t : 'none'); });
          put(desk, 'transform', shake(tau, T.hits));
          /* cells */
          let rowsIn = 0;
          /* Bookkeeping answers have no card of their own: their cells land with the closing frame. */
          const closed = tau >= endAt && d.questions.every((_, n) => answered(n, tau, live));
          d.rows.forEach((r, y) => {
            const open = d.questions.map((q, i) => ({ q, i })).filter((x) => x.q.row === y), solved = open.filter((x) => answered(x.i, tau, live) && tau >= qt[x.i] + T.answer);
            d.columns.forEach((k) => {
              const at = landed.get(`${y}:${k}`), el = cellOf(y, k), fixed = solved.some((x) => x.q.column === k) || (closed && r.before[k] !== r.after[k]);
              const asked = open.some((x) => x.q.column === k) && !fixed && tau >= amberAt.get(y);
              put(el, 'text', fixed ? r.after[k] : at && tau >= at ? r.before[k] : (!at && tau >= rowLand[y] ? r.before[k] : ''));
              put(el, 'state', fixed ? 'fixed' : asked || (at && tau >= at && r.before[k] === '?') ? 'ask' : (r.derived || []).includes(k) ? 'derived' : '');
            });
            const landedRow = tau >= rowLand[y]; rowsIn += landedRow ? 1 : 0;
            const ask = open.length && tau >= amberAt.get(y) && solved.length < open.length;
            const quiet = closed && d.columns.some((k) => r.before[k] !== r.after[k]);
            const state = !landedRow ? '' : quiet || (solved.length === open.length && open.length) ? 'planner' : ask ? 'ask' : tau >= askDone ? 'clear' : 'read';
            put(rowEls[y], 'state', state);
            const pill = rowEls[y].lastElementChild.firstElementChild; put(pill, 'text', STATE[state] || '');
            if (state === 'planner') pill.title = open.map((x) => x.q.evidence || '').filter(Boolean).join(' · ');
          });
          put(count, 'text', tau >= T.fly ? `${rowsIn} of ${d.rows.length} rows` : '');
          /* sources */
          const lit = new Map();
          for (const f of d.flights) { if (!f.el) continue; if (tau >= f.t - 0.12 && tau < f.land + 0.2) lit.set(f.el, 'on'); else if (tau >= f.land && !lit.has(f.el)) lit.set(f.el, 'was'); }
          const cur = d.questions.findIndex((_, n) => !answered(n, tau, live));
          const asking = tau >= cardAt && cur >= 0 && !skip;
          if (asking) d.questions[cur].spots.forEach((s) => { const el = spotEl(s); if (el) lit.set(el, 'ask'); });
          litEls.forEach((el) => put(el, 'state', lit.get(el) || ''));
          /* reader, checks */
          const shownRead = clamp((tau - T.read) / 0.2);
          put(who, 'opacity', String(shownRead)); put(who, 'transform', `translateY(${(1 - shownRead) * 14}px) scale(${1 + (1 - shownRead) * 0.06})`);
          d.checks.forEach((x, i) => {
            const el = root.querySelector(`.v6n-checks [data-c="${x.id}"]`), on = tau >= checkAt - 0.05, ticked = tau >= tickAt[i];
            const open = d.questions.filter((q, n) => q.check === x.id && !answered(n, tau, live)).length;
            put(el, 'state', !on ? '' : !ticked ? 'run' : open ? 'ask' : 'ok');
            put(el.querySelector('b'), 'text', !ticked ? '' : open ? `${open} to ask` : x.flagged ? `${x.flagged} answered` : 'Clear');
          });
          /* question card, end */
          if (tau >= cardAt && cur >= 0 && !skip) {
            fillCard(cur, tau, !!live);
            const k = outCubic(clamp((tau - cardAt) / 0.22));
            put(card, 'opacity', String(k)); put(card, 'transform', `translateY(${(1 - k) * 46}px) scale(${0.97 + 0.03 * k})`);
          } else if (shown || !card.hidden) { card.hidden = true; shown = null; }
          const over = tau >= endAt && (cur < 0);
          if (end.hidden === over) end.hidden = !over;
          if (over) {
            const k = outCubic(clamp((tau - endAt) / 0.3)); put(end, 'opacity', String(k)); put(end, 'transform', `translateY(${(1 - k) * 24}px)`);
            if (live && !nextLatched) { nextLatched = true; end.querySelector('[data-a=next]').hidden = false; }
          }
          /* scripted hand, film only */
          const hand = !live && tau >= cardAt + 1.5 && tau < qt[0] + 0.12 && geo.card;
          if (mouse.hidden === !!hand) mouse.hidden = !hand;
          if (hand) {
            const k = inOut(clamp((tau - cardAt - 1.5) / 1.1)), press = Math.abs(tau - qt[0]) < 0.07 ? 0.86 : 1;
            put(mouse, 'transform', `translate(${lerp(1760, geo.card.x, k) - 4}px,${lerp(1010, geo.card.y, k) - 2}px) scale(${press})`);
          }
          if (dirty || tau < endAt + 0.5) drawFx(tau, live);
        },
      };
    } });

  /* Shot 1. Black, one burst of static, three hits a second apart, then a hole the director fills with the ship grab,
     then the dare. Interactive use steps over the hole. */
  const HOOK = { hits: [1, 2, 3], hole: [4, 8.2], words: ['TALLY SHEET', 'EMAIL', 'RADIO'], dare: 'TRY TO BREAK IT' };
  V.chapter({ id: 'hook', order: 5, title: 'Hook', feature: 'intake', duration_s: 10,
    mount(host, d, ctx) {
      const esc = ctx.esc, doc = (kind) => d.documents.find((x) => x.kind === kind);
      const photo = doc('photo'), mail = doc('email'), sheet = doc('sheet');
      host.innerHTML = `<div class="v6x v6h"><div class="v6h-pile">
          ${photo ? `<article class="v6n-doc v6h-p1"><img src="${esc(photo.image)}" alt="" draggable="false"></article>` : ''}
          ${mail ? `<article class="v6n-doc v6n-mail v6h-p2"><pre>${esc(mail.text)}</pre></article>` : ''}
          ${sheet ? `<article class="v6n-doc v6n-sheet v6h-p3">${sheetHTML(esc, sheet.grid)}</article>` : ''}
        </div><canvas class="v6h-fx"></canvas><div class="v6h-word"></div><div class="v6h-tags"><span>Synthetic document</span><span>Recorded</span></div></div>`;
      const root = host.firstElementChild, $ = (s) => root.querySelector(s);
      const c = canvas($('.v6h-fx')), pile = $('.v6h-pile'), word = $('.v6h-word'), tags = $('.v6h-tags');
      const papers = [{ el: $('.v6h-p1'), rot: -5, hit: 0 }, { el: $('.v6h-p2'), rot: 4, hit: 1 }, { el: $('.v6h-p3'), rot: -2.5, hit: 2 }].filter((x) => x.el);
      let peaks = null, scaled = false, gone = false;
      addEventListener('resize', () => { scaled = false; });
      ctx.api('voice').then((v) => { peaks = v.available === false || !(v.peaks && v.peaks.length) ? null : v.peaks; }).catch(() => { peaks = null; });
      function bar(tau, y, h, gain) {
        c.fillStyle = '#e8eef8'; const step = 6, frame = Math.floor(tau * 30);
        for (let x = 0; x < W; x += step) {
          const n = hash(x * 0.37 + frame * 3.1), p = peaks ? peaks[Math.floor((x / W) * peaks.length)] : 0.3;
          const a = gain * h * (0.1 + (peaks ? p * 1.5 : 0.4) * (0.2 + 1.1 * n));
          c.globalAlpha = 0.45 + 0.5 * n; c.fillRect(x, y - a / 2, step - 2, a);
        }
        c.globalAlpha = 1;
      }
      return {
        enter() { scaled = false; gone = false; },
        seek(raw, live) {
          if (!scaled) scaled = !!fit(host, root);
          let tau = clamp(raw, 0, 10);
          if (live && tau >= HOOK.hole[0]) tau += HOOK.hole[1] - HOOK.hole[0];
          if (live && tau >= 10 && !gone) { gone = true; ctx.next(); return; }
          const hole = tau >= HOOK.hole[0] && tau < HOOK.hole[1];
          host.classList.toggle('v6h-hole', hole);
          c.clearRect(0, 0, W, H);
          if (hole) return;
          const dare = tau >= HOOK.hole[1];
          papers.forEach((x) => { const p = dare ? null : slap(tau - HOOK.hits[x.hit] + DROP, x.rot); put(x.el, 'opacity', p ? '1' : '0'); put(x.el, 'transform', p ? p.t : 'none'); });
          put(pile, 'transform', shake(tau, HOOK.hits.map((h) => h - DROP)));
          if (tau < 0.85) bar(tau, H / 2, 300, clamp(tau / 0.06) * clamp((0.85 - tau) / 0.1));
          const third = tau - HOOK.hits[2];
          if (third >= 0 && third < 1) bar(tau, 800, 260, 1 - third * 0.35);
          const n = dare ? -1 : tau < HOOK.hits[0] ? -1 : Math.min(2, Math.floor(tau - HOOK.hits[0]));
          const text = dare ? HOOK.dare : n >= 0 ? HOOK.words[n] : '', since = dare ? tau - HOOK.hole[1] : tau - (HOOK.hits[n] || 0);
          put(word, 'text', text); put(word, 'state', dare ? 'dare' : '');
          const k = outCubic(clamp(since / 0.1)); put(word, 'transform', `translate(-50%,-50%) scale(${1.45 - 0.45 * k})`); put(word, 'opacity', text ? String(clamp(since / 0.04)) : '0');
          put(tags, 'opacity', tau >= HOOK.hits[0] ? '1' : '0');
        },
      };
    } });
  window.StageV6Hook = { hole: HOOK.hole, duration_s: 10, seek: (tau) => V.seek('hook', tau) };
})();
