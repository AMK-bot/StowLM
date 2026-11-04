/* StowLM Studio shell v4 (CONTRACT.md "v4 (D344)"): a stowage-planning workspace, not a chat.
 *
 *   title band (the call) · sidebar of planning stages with state dots and statuses (+ Evaluation, Forward pilot)
 *   · KPI strip · main view with Ship / Bay plan tabs (ctx.center opens a stage tab) · bay strip + moves scrubber
 *   · activity lines · a ~400 px inspector per stage with its primary action, then "Next".
 *
 * Step modules register with Studio.step(def) (CONTRACT.md "Module API"). Lifecycle: mount → enter() when the stage opens →
 * start() when the planner presses the stage's primary action → autoplay (hands-free only). Nothing on screen is decided
 * here: numbers and labels come from the payloads and the stage bundle.
 * URL flags: ?step=<id>, ?demo=1, ?speed=<x> scales ctx.wait, ?autoplay=1 hands-free, &paced=1 on narration.json's times. */
(function () {
  'use strict';
  if (window.StudioShip) window.StudioShip.restoreURL(); // stage.js has read its flags by now (studio-ship.js)
  const P = new URLSearchParams(location.search);
  const SPEED = +P.get('speed') > 0 ? +P.get('speed') : 1;
  const DEMO = P.get('demo') === '1';
  const AUTOPLAY = P.get('autoplay') === '1';
  const PACED = AUTOPLAY && P.get('paced') === '1' && !!window.StudioPacing;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  const wait = (ms) => sleep(Math.max(0, (+ms || 0) / SPEED));
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };

  // ---------------------------------------------------------------- icons: one line set, 24 grid, stroke 1.5
  const ICON = {
    back: '<path d="M15 18l-6-6 6-6"/>', forward: '<path d="M9 18l6-6-6-6"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
    canvas: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M15 4v16"/>',
    home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    stack: '<rect x="4" y="5" width="16" height="4" rx="1"/><rect x="4" y="10" width="16" height="4" rx="1"/><rect x="4" y="15" width="16" height="4" rx="1"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M21 20H3"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    dots: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    pencil: '<path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6"/><path d="M18.4 2.6a2 2 0 0 1 2.9 2.9L12 14.8 8 16l1.2-4z"/>',
    folder: '<path d="M4 20h16a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-7.9a1 1 0 0 1-.8-.4L9.7 4.4a1 1 0 0 0-.8-.4H4a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1z"/>',
    shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    anchor: '<circle cx="12" cy="5" r="2"/><path d="M12 7v14M5 12H3a9 9 0 0 0 18 0h-2M8 10h8"/>',
    laptop: '<rect x="4" y="5" width="16" height="11" rx="2"/><path d="M2 19h20"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    wave: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    papers: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    photo: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 8"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    sheet: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M4 15h16M10 3v18"/>',
    radio: '<rect x="3" y="9" width="18" height="12" rx="2"/><path d="M7 9l10-5"/><circle cx="15.5" cy="15" r="2.5"/><path d="M6.5 13.5h3M6.5 16.5h3"/>',
    rule: '<path d="M4 5h6a2 2 0 0 1 2 2v12a2 2 0 0 0-2-2H4zM20 5h-6a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2h6z"/>',
    plan: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10M15 10v10"/>',
    move: '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
    package: '<path d="M21 8l-9-5-9 5 9 5z"/><path d="M3 8v8l9 5 9-5V8M12 13v8"/>',
    ship: '<path d="M3 15l2 4h14l2-4z"/><path d="M6 15V9h12v6"/><path d="M9 9V6h3v3M12 7h3v2"/>',
    copilot: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  };
  const icon = (name, cls) => `<svg class="i${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" aria-hidden="true">${ICON[name] || ICON.dots}</svg>`;
  const MARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="5.5" width="14" height="3.4" rx="1" fill="currentColor"/><rect x="5" y="10.3" width="9" height="3.4" rx="1" fill="currentColor" opacity=".7"/><rect x="10" y="15.1" width="9" height="3.4" rx="1" fill="currentColor" opacity=".45"/></svg>';

  // ---------------------------------------------------------------- the stages (shell-owned words; CONTRACT.md v4)
  const STAGE = {
    voyage: { home: true, actor: 'planner', icon: 'ship', title: "Today's call", purpose: 'The call, the ship and who does what', action: 'Import cargo papers' },
    intake: { n: 1, actor: 'spark', icon: 'papers', purpose: 'Read a sample of cargo papers into a load list. A separate 12-box demo voyage', who: 'Muse Spark · cloud', action: 'Import cargo papers', status: 'Load list ready' },
    instruct: { n: 2, actor: 'voice', icon: 'radio', purpose: 'A radio instruction, read into a rule you confirm', who: 'Muse Voice Transcribe · cloud, then StowLM Reader · on this laptop', auto: true, status: 'Rule confirmed' },
    plan: { n: 3, actor: 'engine', icon: 'plan', purpose: 'Replan the load under the confirmed rule. The full checker decides', who: 'Engine 0.13.0 · search, then the full checker', action: 'Replan with confirmed rule', status: 'Replanned' },
    stress: { n: 4, actor: 'checker', icon: 'move', purpose: 'Move a box onto another stack and let the checker rule on it', who: 'Full checker · on this laptop', action: 'Start stress test', status: 'Checked' },
    copilot: { n: 5, actor: 'spark', icon: 'copilot', purpose: 'Ask for a change in plain words', who: 'Software · proved by arithmetic, then Muse Spark · cloud · Recorded answer', action: 'Ask copilot', typed: true, status: 'Answered' },
    handover: { n: 6, actor: 'software', icon: 'package', purpose: 'Bay plan, crane sequence, stability and a note to the master, with a receipt', who: 'Software · on this laptop', action: 'Build handover package', status: 'Package built' },
    evidence: { page: true, actor: 'checker', icon: 'chart', sub: 'What we measured on held-out synthetic voyages, and where each number comes from' },
    impact: { page: true, actor: 'engine', icon: 'globe', sub: 'A projection of what it could be worth at scale. Not a result' },
  };
  const meta = (d) => STAGE[d.id] || { n: null, actor: 'software', icon: 'dots', purpose: '', action: null, status: 'Done' };
  const ACTOR_OF = { 'Muse Spark': 'spark', 'Muse Voice Transcribe': 'voice', 'StowLM Reader': 'reader', 'Engine 0.13.0': 'engine', 'Full checker': 'checker', Planner: 'planner', Copilot: 'spark', Software: 'software' };

  // ---------------------------------------------------------------- data
  const cache = new Map();
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    let body = null; try { body = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) { const err = new Error((body && body.error) || `${url}: ${r.status}`); err.status = r.status; err.data = body; throw err; }
    return body;
  }
  function cached(url) {
    if (!cache.has(url)) { const p = getJSON(url); cache.set(url, p); p.catch(() => cache.delete(url)); }
    return cache.get(url);
  }
  const api = (name) => cached('/api/v6/' + String(name).replace(/^\/+/, ''));
  const data = (name) => cached('/studio-data/' + String(name).replace(/\.json$/, '') + '.json');
  async function post(name, body) {
    const r = await fetch('/api/v6/' + String(name).replace(/^\/+/, ''), { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body == null ? {} : body) });
    let out = null; try { out = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) { const err = new Error((out && out.error) || `${name}: ${r.status}`); err.status = r.status; err.data = out; throw err; }
    return out;
  }

  // ---------------------------------------------------------------- autopilot cursor
  const cur = { x: innerWidth * 0.62, y: innerHeight * 0.58, shown: false };
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  function placeCursor() { const c = $('st-cursor'); if (c) c.style.transform = `translate(${cur.x - 3}px, ${cur.y - 2}px)`; }
  function showCursor(on) { const c = $('st-cursor'); if (!c) return; cur.shown = on; c.classList.toggle('on', on); document.body.classList.toggle('st-autopilot', on); placeCursor(); }
  function glide(x, y, ms, onStep) {
    const x0 = cur.x, y0 = cur.y, dist = Math.hypot(x - x0, y - y0);
    const dur = ms != null ? ms : PACED ? Math.min(900, Math.max(600, 520 + dist * 0.4)) : Math.min(720, Math.max(480, 380 + dist * 0.35));
    const nx = -(y - y0) / (dist || 1), ny = (x - x0) / (dist || 1), bow = Math.min(40, dist * 0.08);
    return new Promise((res) => {
      if (dist < 1) { cur.x = x; cur.y = y; placeCursor(); res(); return; }
      const t0 = performance.now();
      const step = (now) => {
        const t = Math.min(1, (now - t0) / (dur / Math.max(0.5, Math.min(SPEED, 2)))), k = ease(t), arc = Math.sin(Math.PI * k) * bow;
        cur.x = x0 + (x - x0) * k + nx * arc; cur.y = y0 + (y - y0) * k + ny * arc; placeCursor();
        if (onStep) onStep(cur.x, cur.y);
        if (t < 1) requestAnimationFrame(step); else res();
      };
      requestAnimationFrame(step);
    });
  }
  function resolveEl(target, scope) {
    if (!target) return null;
    if (target instanceof Element) return target;
    if (typeof target === 'string') for (const root of [scope, $('st-center'), $('st-page'), $('st-inspector'), document]) { if (!root) continue; const n = root.querySelector(target); if (n) return n; }
    return null;
  }
  const isPoint = (t) => t && typeof t === 'object' && !(t instanceof Element) && 'x' in t;
  function pointOf(target, scope) {
    if (isPoint(target)) return { x: +target.x, y: +target.y, el: null };
    const n = resolveEl(target, scope); if (!n) return null;
    const r = n.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, el: n };
  }
  async function scrollInto(n) {
    if (!n || !n.getBoundingClientRect) return;
    const r = n.getBoundingClientRect();
    const pane = n.closest('.st-insp-body, #st-center, #st-page, .st-scroll');
    const pr = pane ? pane.getBoundingClientRect() : { top: 0, bottom: innerHeight };
    if (r.top >= pr.top + 8 && r.bottom <= pr.bottom - 8) return;
    n.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
    await sleep(450);
  }
  function ripple(x, y) { const r = el('div', 'st-ripple'); r.style.left = x + 'px'; r.style.top = y + 'px'; document.body.appendChild(r); setTimeout(() => r.remove(), 550); }
  function fire(n, type, x, y, extra) {
    const init = { bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y, screenX: x, screenY: y, button: 0, ...extra };
    if (type.startsWith('pointer')) n.dispatchEvent(new PointerEvent(type, { pointerId: 1, pointerType: 'mouse', isPrimary: true, width: 1, height: 1, pressure: init.buttons ? 0.5 : 0, ...init }));
    else n.dispatchEvent(new MouseEvent(type, init));
  }
  async function click(target, scope) {
    let n = resolveEl(target, scope);
    if (!n && isPoint(target)) n = document.elementFromPoint(target.x, target.y);
    if (!n) { console.warn('[studio] autopilot: nothing to click for', target); return false; }
    if (!cur.shown) showCursor(true);
    await scrollInto(n);
    const p = pointOf(isPoint(target) ? target : n, scope);
    await glide(p.x, p.y);
    fire(n, 'pointermove', p.x, p.y); fire(n, 'mousemove', p.x, p.y);
    await sleep(PACED ? 320 + Math.random() * 140 : 110);
    const c = $('st-cursor'); c && c.classList.add('press');
    fire(n, 'pointerdown', p.x, p.y, { buttons: 1 }); fire(n, 'mousedown', p.x, p.y, { buttons: 1 });
    ripple(p.x, p.y);
    await sleep(90);
    c && c.classList.remove('press');
    fire(n, 'pointerup', p.x, p.y); fire(n, 'mouseup', p.x, p.y);
    if (typeof n.click === 'function' && !(n instanceof SVGElement)) n.click(); else fire(n, 'click', p.x, p.y);
    await sleep(140);
    return true;
  }
  async function drag(from, to, opts) {
    const a = pointOf(from);
    if (!a || !pointOf(to)) { console.warn('[studio] autopilot: drag needs two points', from, to); return false; }
    if (!cur.shown) showCursor(true);
    await glide(a.x, a.y);
    const target = a.el || document.elementFromPoint(a.x, a.y) || document.body;
    await sleep(120);
    const c = $('st-cursor'); c && c.classList.add('press');
    fire(target, 'pointerdown', a.x, a.y, { buttons: 1 }); fire(target, 'mousedown', a.x, a.y, { buttons: 1 });
    await sleep(140);
    const b = pointOf(to);
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    const ms = (opts && opts.ms) || Math.min(1400, Math.max(700, 450 + dist * 0.9));
    await glide(b.x, b.y, ms, (x, y) => { fire(target, 'pointermove', x, y, { buttons: 1 }); fire(target, 'mousemove', x, y, { buttons: 1 }); });
    await sleep(160);
    fire(target, 'pointerup', b.x, b.y); fire(target, 'mouseup', b.x, b.y);
    c && c.classList.remove('press');
    await sleep(160);
    return true;
  }

  // ---------------------------------------------------------------- shared state, events
  const S = { defs: [], list: [], cur: null, state: {}, handlers: new Map(), stateDoc: null, log: [], tabs: new Map(), tab: 'ship', closedBays: new Set() };
  function emit(evt, payload) { for (const fn of S.handlers.get(evt) || []) { try { fn(payload); } catch (e) { console.warn('[studio] handler for', evt, e); } } }
  function on(evt, fn) { if (!S.handlers.has(evt)) S.handlers.set(evt, new Set()); S.handlers.get(evt).add(fn); return () => S.handlers.get(evt).delete(fn); }

  // ---------------------------------------------------------------- chips, activity, toasts
  function chip(text, kind) { return `<span class="st-chip${kind ? ' ' + esc(kind) : ''}" title="${esc(text)}">${esc(text)}</span>`; }
  const labelKind = (label) => (/projection/i.test(label) ? 'projection' : /\blive\b|ran here/i.test(label) ? 'live' : /pass/i.test(label) ? 'pass' : /fail|reject|refus/i.test(label) ? 'fail' : '');
  function log(d, entry) {
    const e = entry || {}, list = $('st-activity');
    S.log.push({ t: Date.now(), step: d && d.id, ...e });
    if (!list) return;
    const empty = list.querySelector('.st-act-empty'); if (empty) empty.remove();
    const li = el('li', 'st-act');
    const actor = ACTOR_OF[e.who] || 'software';
    li.innerHTML = `<time>${new Date().toLocaleTimeString('en-GB', { hour12: false })}</time><i class="st-act-dot" style="--c:var(--st-c-${actor})"></i><b>${esc(e.who || 'Software')}</b>${e.where ? `<span class="st-act-where">${esc(e.where)}</span>` : ''}<span class="st-act-text">${esc(e.text || '')}</span>${e.label ? chip(e.label, labelKind(e.label)) : ''}`;
    li.title = [e.who, e.where, e.text, e.label].filter(Boolean).join(' · ');
    list.prepend(li);
    while (list.children.length > 3) list.lastElementChild.remove();
  }
  function toast(text, kind) {
    const host = $('st-toasts'); if (!host) return;
    const t = el('div', 'st-toast' + (kind ? ' ' + kind : '')); t.textContent = text;
    host.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 250); }, 3200);
  }

  // ---------------------------------------------------------------- main view: Ship / Bay plan / stage tabs
  let rzTimer = null;
  function stageResize() { clearTimeout(rzTimer); rzTimer = setTimeout(() => { try { window.__stage && window.__stage.resizeAll && window.__stage.resizeAll(); } catch (e) { /* stage loading */ } }, 40); }
  function selectTab(id) {
    if (id !== 'ship' && id !== 'bays' && !S.tabs.has(id)) id = 'ship';
    S.tab = id;
    for (const b of document.querySelectorAll('#st-tabs .st-tab')) b.classList.toggle('on', b.dataset.tab === id);
    const host = $('st-center'), node = S.tabs.has(id) ? S.tabs.get(id).node : null;
    for (const c of host.children) c.style.display = c === node ? '' : 'none';
    host.classList.toggle('on', !!node);
    if (id === 'ship' || id === 'bays') {
      const btn = document.querySelector(`#st-stage-dom [data-view="${id === 'bays' ? 'bays' : 'iso'}"]`);
      if (btn && window.__stage) btn.click();
      stageResize();
    }
  }
  function openTab(d, node, title) {
    let t = S.tabs.get(d.id);
    if (!t) {
      const b = el('button', 'st-tab st-tab-x', `<span>${esc(title || d.title)}</span><i class="st-tab-close" aria-label="Close">${icon('close', 's16')}</i>`);
      b.type = 'button'; b.dataset.tab = d.id;
      b.addEventListener('click', (e) => { if (e.target.closest('.st-tab-close')) { closeTab(d.id); return; } selectTab(d.id); });
      $('st-tabs').appendChild(b);
      t = { btn: b, node: null }; S.tabs.set(d.id, t);
    }
    t.btn.querySelector('span').textContent = title || d.title;
    t.node = node;
    if (node.parentNode !== $('st-center')) $('st-center').appendChild(node);
    selectTab(d.id);
  }
  function closeTab(id) {
    const t = S.tabs.get(id); if (!t) return;
    t.btn.remove(); if (t.node) t.node.style.display = 'none';
    S.tabs.delete(id);
    if (S.tab === id) selectTab('ship');
  }
  function center(d, node) {
    if (meta(d).page) { // a full-page stage: the node is its main content
      d.centerNode = node || null;
      if (node && d.pageCenter && node.parentNode !== d.pageCenter) d.pageCenter.appendChild(node);
      if (d.pageCenter) for (const c of d.pageCenter.children) c.style.display = c === node ? '' : 'none';
      return;
    }
    if (node) { if (S.cur === d) openTab(d, node, d.title); else d.pendingCenter = node; }
    else { d.pendingCenter = null; closeTab(d.id); }
  }
  function rulesNode() {
    const B = (window.__stage && window.__stage.B) || {}, R = window.StageRules, fams = ((B.rules || {}).families) || [];
    const box = el('div', 'st-rules-list');
    if (!fams.length || !R) { box.innerHTML = '<p class="st-meta" style="padding:16px">The rule list comes with the ship. It is still loading.</p>'; return box; }
    const counts = R.tally(fams);
    box.innerHTML = `<p class="st-meta" style="padding:4px 4px 12px">${esc(counts.total)} rule checks in ${fams.length} families, from the full checker's report on the stored plan${S.stateDoc && S.stateDoc.rule_counts ? ` · ${S.stateDoc.rule_counts.active} rules active, ${S.stateDoc.rule_counts.off} off` : ''}</p>
      <table class="st-table"><thead><tr><th>Family</th><th class="num">Rules</th><th>Verdict</th></tr></thead><tbody>${fams.map((f) => {
        const t = R.familyTally(f), v = R.verdict(t), cls = v === 'pass' ? 'ok' : v === 'fail' ? 'bad' : '';
        return `<tr><td>${esc(f.label || f.family)}</td><td class="num">${t.total - t.na}</td><td><span class="st-callout ${cls}" style="font-size:14px">${v === 'pass' ? 'Passed' : v === 'fail' ? 'Refused' : v === 'na' ? 'Not applicable' : 'Not run'}</span></td></tr>`;
      }).join('')}</tbody></table>`;
    return box;
  }

  // ---------------------------------------------------------------- KPI strip (moves, rules), bay strip, moves scrubber
  function bayOf(slot) { const m = /^(\d{3})\d{4}$/.exec(String(slot || '')); return m ? +m[1] : null; }
  let bayCells = null;
  function buildBays(B) {
    const slots = ((B.geometry || {}).slots) || [], cap = new Map();
    for (const sl of slots) cap.set(sl.bay, (cap.get(sl.bay) || 0) + 1);
    const bays = [...cap.keys()].sort((a, b) => b - a); // bow on the right of the hull view: draw stern (high bay) on the left
    $('st-bays').innerHTML = `<span class="st-bays-end">Stern</span>${bays.map((b) => `<div class="st-bay" data-bay="${b}" title="Bay ${String(b).padStart(2, '0')}: ${cap.get(b)} slots"><i><u></u></i><span>${String(b).padStart(2, '0')}</span></div>`).join('')}<span class="st-bays-end">Bow</span>`;
    bayCells = { cap, nodes: new Map(bays.map((b) => [b, $('st-bays').querySelector(`[data-bay="${b}"]`)])) };
  }
  let lastBayKey = '';
  const stripCache = { B: null, k: -1, ship: null };
  function tickStrip() {
    const st = window.__stage; if (!st || !st.B) return;
    if (!bayCells) buildBays(st.B);
    const S2 = st.S || {}, k = S2.k || 0, total = st.total || 0;
    // the replay state at the shown move (a replan morph moves only S.k, so replay it from the bundle's checkpoints)
    if (stripCache.B !== st.B || stripCache.k !== k) { stripCache.B = st.B; stripCache.k = k; try { stripCache.ship = (st.stateAt ? st.stateAt(k) : S2.state).ship; } catch (e) { stripCache.ship = (S2.state && S2.state.ship) || new Map(); } }
    const ship = stripCache.ship || new Map();
    const count = new Map(); for (const slot of ship.keys()) { const b = bayOf(slot); if (b != null) count.set(b, (count.get(b) || 0) + 1); }
    const key = [...count].join(',') + '|' + [...S.closedBays].join(',');
    if (key !== lastBayKey) {
      lastBayKey = key;
      for (const [b, n] of bayCells.nodes) {
        const c = count.get(b) || 0, cap = bayCells.cap.get(b) || 1;
        n.querySelector('u').style.transform = `scaleY(${Math.min(1, c / cap)})`;
        n.classList.toggle('closed', S.closedBays.has(b));
        n.title = `Bay ${String(b).padStart(2, '0')}: ${c} of ${cap} slots filled${S.closedBays.has(b) ? ' · closed by the confirmed rule' : ''}`;
      }
    }
    const rng = $('st-moves');
    if (+rng.max !== total) rng.max = total;
    if (document.activeElement !== rng && +rng.value !== k) rng.value = k;
    $('st-moves-label').textContent = `Move ${k.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}`;
    const mv = document.querySelector('#st-kpis [data-k="moves"]');
    mv.classList.remove('na'); mv.querySelector('i').textContent = `${k} of ${total}`;
    const R = window.StageRules, fams = ((st.B.rules || {}).families) || [], rk = document.querySelector('#st-kpis [data-k="rules"]');
    if (R && fams.length) {
      const t = R.tally(fams), done = k >= total;
      rk.classList.toggle('na', !done); rk.classList.toggle('bad', done && t.fail > 0);
      rk.querySelector('i').textContent = done ? `${t.pass} of ${t.total - t.na} pass` : 'at the end';
    }
  }

  // ---------------------------------------------------------------- stages
  function makeCtx(d) {
    return {
      id: d.id,
      api, post, data,
      asset: (name) => '/api/v6/asset/' + name,
      get stage() { return window.__stage; },
      stageReady: () => (window.StudioShip ? window.StudioShip.ready : Promise.resolve(window.__stage)),
      center(node) { center(d, node); },
      log: (e) => log(d, e), chip, toast, esc,
      done() { markDone(d); },
      next() { const n = nextOf(d); if (n) open(n.id); },
      begin: (id) => act(id),
      status: (text) => setStatus(d, text),
      get state() { return S.state; },
      emit, on, wait,
      get demo() { return DEMO; },
      get speed() { return SPEED; },
      get autopilot() { return !!S.autoplaying; },
      click: (t) => click(t, d.body), drag,
      gauges: () => {},
    };
  }
  const visible = () => S.list.filter((d) => !d.gap);
  function nextOf(d) { const v = visible(); const i = v.indexOf(d); return i >= 0 ? v[i + 1] || null : null; }
  function setState(d, state) {
    d.state = state;
    const dot = d.row && d.row.querySelector('.st-stage-dot');
    if (d.row) { d.row.dataset.state = state; if (dot) dot.title = { todo: 'To do', running: 'Running', done: 'Done', needs: 'Needs you' }[state] || ''; }
  }
  function setStatus(d, text) { d.statusText = text; if (d.row) { const s = d.row.querySelector('.st-stage-status'); if (!s) return; s.textContent = text || ''; s.hidden = !text; } }

  function buildStage(d) {
    const m = meta(d);
    if (m.page) {
      const part = el('section', 'st-pagestep');
      part.hidden = true;
      part.innerHTML = `<header class="st-page-header st-shell-head"><span class="st-page-icon st-tile" style="--c:var(--st-c-${m.actor})">${icon(m.icon)}</span><h1 class="st-page-title">${esc(d.title)}</h1>${m.sub ? `<p class="st-page-subtitle">${esc(m.sub)}</p>` : ''}</header>
        <div class="st-page-part st-page-center"></div><div class="st-page-part st-page-panel"></div><div class="st-page-foot"></div>`;
      $('st-page-inner').appendChild(part);
      d.part = part; d.pageCenter = part.querySelector('.st-page-center'); d.body = part.querySelector('.st-page-panel'); d.foot = part.querySelector('.st-page-foot');
      const row = el('button', 'st-stage st-stage-page', `<span class="st-stage-ico">${icon(m.icon, 's16')}</span><span class="st-stage-name">${esc(d.title)}</span>`);
      row.type = 'button'; row.dataset.step = d.id; row.addEventListener('click', () => open(d.id));
      $('st-pages').appendChild(row); d.row = row;
      return;
    }
    const insp = el('section', 'st-insp');
    insp.dataset.step = d.id; insp.hidden = true;
    insp.innerHTML = `<header class="st-insp-head"><span class="st-tile" style="--c:var(--st-c-${m.actor})">${icon(m.icon)}</span><div><small>${m.n ? `Stage ${m.n} of 6` : 'Laem Chabang · call 412'}</small><h2>${esc(m.title || d.title)}</h2></div></header>
      <p class="st-insp-purpose">${esc(m.purpose || '')}</p>${m.who ? `<p class="st-insp-who">${esc(m.who)}</p>` : ''}
      <div class="st-insp-body"></div>
      <footer class="st-insp-foot"></footer>`;
    $('st-insp-host').appendChild(insp);
    d.insp = insp; d.body = insp.querySelector('.st-insp-body'); d.foot = insp.querySelector('.st-insp-foot');
    if (!m.home) {
      const row = el('button', 'st-stage', `<span class="st-stage-n">${m.n}</span><span class="st-stage-main"><span class="st-stage-name">${esc(d.title)}</span><span class="st-stage-status" hidden></span></span><i class="st-stage-dot"></i>`);
      row.type = 'button'; row.dataset.step = d.id; row.addEventListener('click', () => open(d.id));
      $('st-stages').appendChild(row); d.row = row; setState(d, 'todo');
    }
    drawFoot(d);
  }
  /* The inspector's foot: the stage's primary action until it has run, then "Next: <stage>". The copilot keeps a compact question box. */
  function drawFoot(d) {
    const m = meta(d), f = d.foot; if (!f) return;
    f.innerHTML = '';
    const nx = nextOf(d);
    if (m.typed && !d.started) {
      const box = el('div', 'st-ask', `<textarea id="st-ask-${d.id}" rows="2" placeholder="Ask for a change to this plan" aria-label="Question for the copilot"></textarea>`);
      f.appendChild(box);
      d.askInput = box.querySelector('textarea');
      d.askInput.value = d.askText || '';
      d.askInput.addEventListener('input', () => { d.askText = d.askInput.value; });
    }
    if (!d.started && m.action) {
      const b = el('button', 'st-btn primary st-action', esc(m.action)); b.type = 'button'; b.dataset.action = d.id;
      b.addEventListener('click', () => act(d.id));
      f.appendChild(b);
    } else if (d.done && nx) {
      const b = el('button', 'st-btn primary st-next', `Next: ${esc(nx.title)}${icon('forward', 's16')}`); b.type = 'button'; b.dataset.next = nx.id;
      b.addEventListener('click', () => open(nx.id));
      f.appendChild(b);
    }
    f.hidden = !f.children.length;
  }
  function dropStep(d, why) {
    if (d.gap) return;
    d.gap = true; console.warn(`[studio] stage ${d.id} is not shown:`, why);
    if (d.row) d.row.remove();
    for (const x of S.list) if (!x.gap) drawFoot(x);
  }
  function mountStep(d) {
    if (d.mounting) return d.mounting;
    d.mounting = (async () => {
      const feats = d.features || [], doc = S.stateDoc;
      for (const f of feats) { const st = doc && doc.features && doc.features[f]; if (st && st.available === false) throw new Error(st.reason || `${f} unavailable`); }
      const payloads = await Promise.all(feats.map((f) => api(f)));
      const bag = {};
      feats.forEach((f, i) => { bag[f] = payloads[i]; });
      for (const f of feats) if (bag[f] && bag[f].available === false) throw new Error(bag[f].reason || `${f} unavailable`);
      d.ctx = d.ctx || makeCtx(d);
      d.view = d.mount(d.body, bag, d.ctx) || {};
      d.mounted = true;
      return true;
    })().catch((e) => { dropStep(d, e && e.message ? e.message : e); return false; });
    return d.mounting;
  }
  function markDone(d) {
    if (!d || d.done) return;
    d.done = true; d.started = true;
    if (meta(d).auto) { const dot = $('st-bell') && $('st-bell').querySelector('.st-badge-dot'); if (dot) dot.remove(); }
    setState(d, 'done');
    if (!d.statusText) setStatus(d, meta(d).status || 'Done');
    drawFoot(d);
    emit('step:done', d.id);
    const nx = nextOf(d); if (!nx) return;
    if (meta(nx).auto && !PACED) setTimeout(() => { if (!nx.started) ringRadio(nx); }, 1500 / SPEED);
  }
  function ringRadio(nx) {
    const bell = $('st-bell');
    if (bell && !bell.querySelector('.st-badge-dot')) bell.appendChild(el('i', 'st-badge-dot'));
    if (nx.row && !nx.started) setState(nx, 'needs');
    nx.rang = true;
    if (!PACED && !S.autoplaying) open(nx.id);
  }

  /* Open a stage in the inspector (sidebar row, Next, bell). Pages cover the main view and the inspector. */
  async function open(id) {
    const d = S.list.find((x) => x.id === id && !x.gap); if (!d) return;
    const old = S.cur;
    if (old === d) return;
    if (old && old.view && old.view.leave) { try { old.view.leave(); } catch (e) { console.warn('[studio] leave', old.id, e); } }
    if (old && old !== d) closeTab(old.id);
    S.cur = d;
    try { const q = new URLSearchParams(location.search); q.set('step', d.id); history.replaceState(history.state, '', location.pathname + '?' + q.toString()); } catch (e) { /* ignore */ }
    const m = meta(d);
    for (const x of S.list) { if (x.insp) x.insp.hidden = x !== d; if (x.part) x.part.hidden = x !== d; if (x.row) x.row.classList.toggle('on', x === d); }
    $('st-page').classList.toggle('on', !!m.page);
    if (m.page) $('st-page').scrollTop = 0;
    if (!m.page) { const ins = d.insp && d.insp.querySelector('.st-insp-body'); if (ins) ins.scrollTop = 0; selectTab('ship'); }
    const ok = await mountStep(d);
    if (!ok || S.cur !== d) return;
    if (d.pendingCenter) { const n = d.pendingCenter; d.pendingCenter = null; center(d, n); }
    if (d.view.enter) { try { await Promise.race([Promise.resolve(d.view.enter()), sleep(80)]); } catch (e) { console.warn('[studio] enter', d.id, e); } }
    if (m.page) {
      const own = () => { const h = d.part.querySelector('.st-shell-head'); if (h) h.hidden = !!d.part.querySelector('.st-page-header:not(.st-shell-head)'); }; own(); setTimeout(own, 400);
      if (!d.started) { d.started = true; paceLog(d); }
      pageFoot(d);
    }
    if (m.auto && !d.started) act(d.id);
    if (m.typed && !d.started && d.askInput && !d.askInput.value && !S.autoplaying) { d.askInput.value = d.askText = window.Studio.copilotQuestion || ''; }
    emit('step:enter', d.id);
  }
  function pageFoot(d) {
    const nx = nextOf(d); d.foot.innerHTML = '';
    if (nx) { const b = el('button', 'st-btn primary st-next', `Next: ${esc(nx.title)}${icon('forward', 's16')}`); b.type = 'button'; b.dataset.next = nx.id; b.addEventListener('click', () => open(nx.id)); d.foot.appendChild(b); }
  }
  /* The stage's primary action: the planner presses it, the module's start() runs. */
  async function act(id) {
    const d = S.list.find((x) => x.id === id && !x.gap); if (!d) return;
    const m = meta(d);
    if (m.home) { const nx = nextOf(d); markDone(d); if (nx) { await open(nx.id); return act(nx.id); } return; }
    if (S.cur !== d) await open(d.id);
    if (d.started) return;
    d.started = true; setState(d, 'running');
    if (m.typed && d.askInput) d.askText = d.askInput.value;
    drawFoot(d);
    paceLog(d);
    d.startP = (async () => { await d.mounting; if (d.view && d.view.start) { await wait(250); if (S.cur === d) { try { await d.view.start(); } catch (e) { console.warn('[studio] start', d.id, e); } } } })();
    await d.startP;
  }

  // ---------------------------------------------------------------- frame wiring
  function paintIcons(root) { for (const n of (root || document).querySelectorAll('[data-icon]')) { if (n.dataset.painted) continue; n.dataset.painted = '1'; n.insertAdjacentHTML('afterbegin', icon(n.dataset.icon, n.closest('.st-tab, .st-honesty') ? 's16' : '')); } }
  function toggleSidebar(onOff) {
    const app = $('st-app'), hide = onOff === undefined ? !app.classList.contains('no-side') : !onOff;
    app.classList.toggle('no-side', hide); setTimeout(stageResize, 320);
    return !hide;
  }
  function newPlan() {
    const started = S.list.some((x) => x.started && !meta(x).home);
    if (!started) { open('voyage'); return; }
    const q = new URLSearchParams(location.search); q.delete('step'); q.delete('autoplay'); q.delete('paced');
    location.href = location.pathname + (q.toString() ? '?' + q.toString() : '');
  }
  function wireFrame() {
    paintIcons();
    $('st-titlebar').querySelector('[data-act="sidebar"]').addEventListener('click', () => toggleSidebar());
    for (const b of document.querySelectorAll('#st-tabs .st-tab[data-tab="ship"], #st-tabs .st-tab[data-tab="bays"]')) b.addEventListener('click', () => selectTab(b.dataset.tab));
    $('st-bell').addEventListener('click', () => { const r = S.list.find((x) => meta(x).auto); if (r && (r.rang || r.started)) open(r.id); });
    $('st-rules-link').addEventListener('click', () => { const holder = S.cur || S.list[0]; openTab({ id: '_rules', title: 'Checker rules' }, rulesNode(), 'Checker rules'); if (holder && meta(holder).page) open('voyage'); });
    const rng = $('st-moves');
    rng.addEventListener('input', () => { const st = window.__stage; if (st && st.jumpTo) { st.jumpTo(+rng.value); if (S.tab !== 'ship' && S.tab !== 'bays') selectTab('ship'); } });
    new ResizeObserver(stageResize).observe($('st-viewport'));
    on('rule:confirmed', (p) => {
      const bay = p && Number(p.bay); if (!Number.isFinite(bay)) return;
      S.closedBays.add(bay); lastBayKey = '';
      const r = S.list.find((x) => x.id === 'instruct'); if (r && !r.statusText) setStatus(r, `close_bay · ${bay} confirmed`);
      const was = S.tab; if (was !== 'ship') { selectTab('ship'); setTimeout(() => { if (S.tab === 'ship' && S.tabs.has(was)) selectTab(was); }, 2000); }
    });
    on('loadlist:ready', (p) => { const n = Array.isArray(p) ? p.length : p && Array.isArray(p.rows) ? p.rows.length : null; const r = S.list.find((x) => x.id === 'intake'); if (r && n) setStatus(r, `${n} boxes · sample papers`); });
    setInterval(() => { try { tickStrip(); } catch (e) { /* stage loading */ } }, 200);
  }

  // ---------------------------------------------------------------- autoplay (hands-free) and the paced take
  const film = () => (performance.now() - S.film0) / 1000;
  async function untilFilm(t) { while (film() < t) await sleep(Math.min(200, Math.max(16, (t - film()) * 1000))); }
  const firstSeg = (id) => (window.StudioPacing ? window.StudioPacing.segments.find((x) => x.step === id) : null);
  function paceLog(d) {
    if (S.film0 == null || !PACED) return;
    const seg = firstSeg(d.id), t = film();
    console.log(`[studio pace] ${d.id} started at film ${t.toFixed(2)} s` + (seg ? ` · target ${seg.start_s} s · drift ${(t - seg.start_s >= 0 ? '+' : '')}${(t - seg.start_s).toFixed(2)} s` : ''));
  }
  /* Run a stage's autoplay. In the paced take its ctx.wait holds shrink only when it runs behind its deadline. */
  function runAuto(d, deadline) {
    const pctx = Object.create(d.ctx);
    if (deadline != null) pctx.wait = (ms) => { const left = deadline - film(); const want = Math.max(0, +ms || 0); return sleep(left <= 0 ? Math.min(want, 250) : Math.min(want, Math.max(250, left * 1000 * 0.4))); };
    d.autoP = (async () => { await d.mounting; if (d.startP) await d.startP; if (d.view && d.view.autoplay) { try { await d.view.autoplay(pctx); } catch (e) { console.warn('[studio] autoplay', d.id, e); } } })();
    return d.autoP;
  }
  async function typeInto(d) {
    const q = window.Studio.copilotQuestion || (d.view && d.view.question) || '';
    if (!d.askInput || !q) return;
    d.askInput.value = '';
    for (const ch of q) { d.askInput.value += ch; await sleep(22); }
    d.askText = d.askInput.value;
  }
  const centerOf = (n) => { const r = n.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; };
  /* Open stage d the way a person would: the previous stage's Next button, else its sidebar row. */
  async function openByHand(prev, d) {
    const nextBtn = prev && prev.foot && prev.foot.querySelector(`[data-next="${d.id}"]`);
    if (nextBtn && S.cur === prev) await click(nextBtn); else if (d.row) await click(d.row); else await open(d.id);
    for (let i = 0; i < 40 && S.cur !== d; i++) await sleep(50);
  }
  async function pressAction(d) {
    if (meta(d).typed) await typeInto(d);
    const b = d.foot && d.foot.querySelector(`[data-action="${d.id}"]`);
    if (b) await click(b); else act(d.id);
  }
  async function autoplay() {
    if (S.autoplaying) return;
    S.autoplaying = true;
    await wait(1400);
    const v = visible();
    let prev = v[0];
    await click(prev.foot.querySelector(`[data-action="${prev.id}"]`)); // Import cargo papers
    prev = S.cur; runAuto(prev); await prev.autoP; markDone(prev);
    for (const d of v.slice(v.indexOf(prev) + 1)) {
      const m = meta(d);
      await wait(900);
      if (m.auto) { ringRadio(d); await wait(700); if (d.row) await click(d.row); }
      else await openByHand(prev, d);
      if (!m.page && !m.auto && m.action) { await wait(600); await pressAction(d); }
      if (d.id === 'impact') { await wait(4000); break; }
      runAuto(d); await d.autoP; markDone(d);
      prev = d;
    }
    await sleep(600); showCursor(false); S.autoplaying = false; emit('autoplay:end');
  }
  async function paced() {
    const T = window.StudioPacing, segs = T.segments;
    S.autoplaying = true;
    cur.x = innerWidth * 0.5; cur.y = innerHeight * 0.55; showCursor(true);
    const home = S.list.find((x) => meta(x).home);
    // 1 · home: hold, drift over the ship and the actor rows, then import the papers
    await untilFilm(segs[0].start_s + Math.min(8, segs[0].target_s * 0.5));
    const rows = home ? [...home.body.querySelectorAll('.st-actor')].slice(0, 5) : [];
    for (const r of rows) { const p = centerOf(r); await glide(p.x - 60, p.y, 800); await sleep(450); }
    console.log(`[studio pace] home held to film ${film().toFixed(2)} s`);
    let prev = home;
    for (let k = 1; k < segs.length; k++) {
      const seg = segs[k], d = S.list.find((x) => x.id === seg.step && !x.gap);
      if (!d) continue;
      if (d === prev) { await untilFilm(seg.start_s); console.log(`[studio pace] segment ${seg.id} (${seg.title}) at film ${film().toFixed(2)} s · target ${seg.start_s} s (same stage, continues)`); continue; }
      const m = meta(d);
      if (prev && prev.autoP) await Promise.race([prev.autoP, untilFilm(seg.start_s - 1.6)]);
      if (prev && !prev.done && !meta(prev).home) markDone(prev);
      if (m.home) continue;
      if (prev === home) { await untilFilm(seg.start_s - 1.0); await click(home.foot.querySelector('[data-action="voyage"]')); }
      else if (m.auto) { await untilFilm(seg.start_s - 1.2); ringRadio(d); await sleep(500); if (d.row) await click(d.row); else await open(d.id); }
      else if (m.page) { await untilFilm(seg.start_s - 1.0); await openByHand(prev, d); }
      else {
        await untilFilm(seg.start_s - (m.typed ? 4.2 : 2.4)); await openByHand(prev, d);
        await untilFilm(seg.start_s - (m.typed ? 2.8 : 0.9)); await pressAction(d);
      }
      for (let i = 0; i < 50 && S.cur !== d; i++) await sleep(60);
      if (d.id === 'impact') { // hover the side panel, never move a slider (logic1 SHOULD 5)
        await sleep(1800);
        const side = d.body && d.body.firstElementChild;
        if (side) { const p = centerOf(side); await glide(Math.min(p.x, innerWidth - 120), Math.min(Math.max(p.y, 160), innerHeight - 160), 900); await sleep(5200); }
        await glide(innerWidth - 180, innerHeight - 60, 900);
        d.autoP = Promise.resolve();
      } else {
        let j = k + 1; while (j < segs.length && segs[j].step === d.id) j++;
        const last = segs[j - 1], next = segs[j];
        runAuto(d, (next ? next.start_s : T.total_s) - (last.hold_s || 2) - 1.6);
      }
      prev = d;
    }
    await untilFilm(T.total_s);
    console.log(`[studio pace] end at film ${film().toFixed(2)} s · target ${T.total_s} s`);
    showCursor(false); S.autoplaying = false; emit('autoplay:end');
  }

  // ---------------------------------------------------------------- boot
  async function boot() {
    S.list = S.defs.slice().sort((a, b) => (a.order || 0) - (b.order || 0));
    if (!S.list.length) return;
    wireFrame();
    let doc = null;
    try { doc = await Promise.race([api('state'), sleep(2500).then(() => null)]); } catch (e) { console.warn('[studio] /api/v6/state', e.message); }
    S.stateDoc = doc;
    if (!doc) api('state').then((x) => { S.stateDoc = x; titleCall(); }).catch(() => {});
    titleCall();
    S.list.forEach((d) => { if ((d.features || []).some((f) => doc && doc.features && doc.features[f] && doc.features[f].available === false)) d.gap = true; });
    S.list = S.list.filter((d) => !d.gap);
    S.list.forEach(buildStage);
    S.list.forEach(drawFoot);
    S.list.forEach((d) => { mountStep(d); });
    const want = S.list.find((d) => d.id === P.get('step')) || S.list[0];
    await open(want.id);
    if (meta(want).auto) act(want.id);
    S.film0 = performance.now() - (window.StudioPacing ? window.StudioPacing.open_s : 0) * 1000;
    if (PACED) paced(); else if (AUTOPLAY) autoplay();
  }
  function titleCall() {
    const doc = S.stateDoc || {}, ship = String(doc.ship || '').split(' · ')[0];
    const set = (port) => { $('st-tb-call').textContent = [`${port || 'Laem Chabang'}, call 412`, ship, 'simulated port'].filter(Boolean).join(' · '); };
    set(null);
    if (window.StudioShip) window.StudioShip.ready.then((st) => { const p = ((st.B || {}).meta || {}).port || {}; set((p.label || '').replace(/\s*\(simulated\)/i, '') || null); });
  }

  window.Studio = {
    step(def) {
      if (!def || !def.id || typeof def.mount !== 'function') { console.warn('[studio] bad step definition', def); return; }
      if (S.defs.some((d) => d.id === def.id)) return;
      S.defs.push({ ship: true, features: [], icon: def.id, ...def });
    },
    go: (id) => (id === 'voyage' ? newPlan() : open(id)), open, act, begin: act, autoplay, toggleSidebar, newPlan, selectTab,
    get current() { return S.cur ? S.cur.id : null; },
    get steps() { return visible().map((d) => ({ id: d.id, title: d.title, done: !!d.done, started: !!d.started, mounted: !!d.mounted, state: d.state || null, status: d.statusText || null })); },
    ctx: (id) => { const d = S.list.find((x) => x.id === id); return d ? d.ctx || (d.ctx = makeCtx(d)) : null; },
    get log() { return S.log.slice(); },
    get autoplaying() { return !!S.autoplaying; },
    get filmTime() { return S.film0 == null ? null : film(); },
    copilotQuestion: null,
    icon, chip, esc, toast, wait, click, drag, demo: DEMO, speed: SPEED,
  };
  // Deferred scripts run while readyState is already "interactive" but before DOMContentLoaded: wait for that event so every
  // step module after this file has registered. "complete" means it has passed (a late, injected copy of this file).
  let booted = false;
  const go1 = () => { if (booted) return; booted = true; boot().catch((e) => console.error('[studio] boot', e)); };
  if (document.readyState === 'complete') setTimeout(go1, 0);
  else { document.addEventListener('DOMContentLoaded', go1); window.addEventListener('load', go1); }
})();
