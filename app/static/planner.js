/* StowLM Planner shell (PLANNER.md, contract v5; design in experiments/mvp/video/studio-demo/DESIGN.md).
 *
 *   header: call · stepper (Cargo → Instructions → Plan → Verify → Handover) · Reports · Reader status · bell
 *   left: load list · centre: legend + 2D bay plan (window.BayPlan) + sheet · right: step panel · bottom: meters + scrubber
 *
 * Stage modules register with Planner.stage(def) (same shape as Studio.step). Lifecycle: mount → enter() when the stage
 * opens → start() when the planner presses the panel's primary action → autoplay (hands-free only). ctx adds bay, loadlist,
 * sheet, meters, plan. Nothing on screen is decided here: numbers and labels come from planner-data/*.json and /api/v6/*.
 * URL flags: ?step=<id>, ?demo=1, ?speed=<x> scales ctx.wait, ?autoplay=1 hands-free, &paced=1 on narration.json's times.
 * ?live=1 is the Functional app (functional-app CONTRACT.md Extension B): the same page with the stages registered as
 * mode 'live', every number from the live call through /api/functional/*; a live page never reads /api/v6/ or
 * /planner-data/ (the shell throws if it tries). Every demo code path is unchanged. */
(function () {
  'use strict';
  const P = new URLSearchParams(location.search);
  const SPEED = +P.get('speed') > 0 ? +P.get('speed') : 1;
  const DEMO = P.get('demo') === '1';
  const LIVE = P.get('live') === '1';
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

  // ---------------------------------------------------------------- IA v2 (PLANNER.md D346): inputs → Generate plan → review
  const WHO = {
    spark: ['Muse Spark · cloud', 'spark'], voice: ['Muse Voice Transcribe · cloud', 'voice'], reader: ['StowLM Reader · on this laptop', 'reader'],
    engine: ['Engine 0.13.0', 'engine'], checker: ['Full checker', 'checker'], software: ['Software', 'software'], arith: ['Software · proved by arithmetic', 'software'],
  };
  // where each stage lives, and the shell's words for it
  const STAGE = {
    voyage: { place: 'input', n: 1, title: 'Ship & call', who: [], done0: true },
    intake: { place: 'input', n: 2, title: 'Cargo', who: ['spark'], action: 'Import cargo papers', hint: 'Read a sample of cargo papers into a load list (a separate 12-box demo voyage)', status: 'Load list ready' },
    instruct: { place: 'input', n: 3, title: 'Instructions', who: ['voice', 'reader'], auto: true, hint: 'The radio call arrives here. Read it into a rule, look inside, then confirm it yourself', status: 'Rule confirmed' },
    plan: { place: 'review', tab: 'Checks', title: 'Checks', who: ['engine', 'checker'], generate: true, status: 'Plan generated' },
    stress: { place: 'review', tab: 'Stress test', title: 'Stress test', who: ['checker'], sandbox: true, status: 'Checked' },
    copilot: { place: 'review', tab: 'Ask copilot', title: 'Ask copilot', who: ['arith', 'spark'], status: 'Answered' },
    handover: { place: 'review', tab: 'Export', title: 'Export', who: ['software'], action: 'Build handover package', status: 'Package built' },
    evidence: { place: 'report', title: 'Evaluation' },
    impact: { place: 'report', title: 'Forward pilot' },
  };
  const meta = (d) => STAGE[d.id] || { place: 'review', title: d.title, who: [], status: 'Done' };
  const whoChips = (keys) => (keys || []).map((k) => { const w = WHO[k]; return w ? `<span class="pl-chip actor" style="--c:var(--pl-c-${w[1]})">${esc(w[0])}</span>` : ''; }).join('');
  const STUDIO_DATA = new Set(['evidence', 'impact']);

  // ---------------------------------------------------------------- data
  const cache = new Map();
  const LIVE_BLOCKED = /^\/(api\/v6\/|planner-data\/|studio-data\/)/;
  function liveGuard(url) { if (LIVE && LIVE_BLOCKED.test(String(url))) throw new Error(`The Functional app never reads ${url}; nothing is replayed in its place.`); }
  async function getJSON(url) {
    liveGuard(url);
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    let body = null; try { body = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) { const err = new Error((body && body.error) || `${url}: ${r.status}`); err.status = r.status; err.data = body; throw err; }
    return body;
  }
  function cached(url) {
    if (!cache.has(url)) { const p = getJSON(url); cache.set(url, p); p.catch(() => cache.delete(url)); }
    return cache.get(url);
  }
  const api = (name) => (LIVE ? liveApi(name) : cached('/api/v6/' + String(name).replace(/^\/+/, '')));
  const data = (name) => { if (LIVE) return liveData(name); const n = String(name).replace(/\.json$/, '').replace(/^(planner|studio):/, ''); const studio = /^studio:/.test(name) || STUDIO_DATA.has(n); return cached((studio ? '/studio-data/' : '/planner-data/') + n + '.json'); };
  async function post(name, body) {
    if (LIVE) return fx.post(name, body);
    const r = await fetch('/api/v6/' + String(name).replace(/^\/+/, ''), { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body == null ? {} : body) });
    let out = null; try { out = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) { const err = new Error((out && out.error) || `${name}: ${r.status}`); err.status = r.status; err.data = out; throw err; }
    return out;
  }

  // ---------------------------------------------------------------- live mode: the call, /api/functional/* (Extension B)
  const L = { call: null, id: null, sig: '', timer: null, world: '', status: null, off: null };
  const CALL_ID = /^c\d{8}-\d{6}-[0-9a-f]{6}$/;
  const CALL_KEY = 'stowlm.functional.call';
  async function fxReq(method, path, body) {
    const url = '/api/functional/' + String(path).replace(/^\/+/, '');
    liveGuard(url);
    const init = method === 'GET' ? { headers: { Accept: 'application/json' } }
      : { method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body == null ? {} : body) };
    const r = await fetch(url, init);
    let out = null; try { out = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) { const err = new Error((out && out.error) || `${path}: ${r.status}`); err.status = r.status; err.data = out; throw err; }
    if (out && out.app === 'functional' && CALL_ID.test(out.id || '')) { acceptCall(out); schedulePoll(); }
    return out;
  }
  const fx = { get: (path) => fxReq('GET', path), post: (path, body) => fxReq('POST', path, body) };
  function liveData(name) {
    const n = String(name).replace(/\.json$/, '').replace(/^(planner|studio):/, '');
    if (!L.id) return Promise.reject(new Error('Create the call first; the planner data comes from the live call.'));
    return fxReq('GET', `calls/${encodeURIComponent(L.id)}/planner/${encodeURIComponent(n)}`);
  }
  function liveApi(name) { const n = String(name).replace(/^\/+/, ''); return n === 'call' ? Promise.resolve(L.call) : fxReq('GET', n); }
  function callBusy(v) {
    if (!v) return false;
    if (['generating', 'reading_papers', 'assembling', 'planning'].includes(v.state) || (v.run && v.run.status === 'running')) return true;
    const last = new Map(); for (const row of v.stages || []) last.set(row.stage, row.status);
    return [...last.values()].includes('running');
  }
  function rememberCall(id) {
    L.id = id;
    try { const q = new URLSearchParams(location.search); q.set('call', id); history.replaceState(history.state, '', location.pathname + '?' + q.toString()); } catch (e) { /* ignore */ }
    try { localStorage.setItem(CALL_KEY, id); } catch (e) { /* storage off */ }
  }
  function forgetCall() {
    L.id = null; L.call = null; L.sig = ''; clearTimeout(L.timer);
    try { localStorage.removeItem(CALL_KEY); } catch (e) { /* storage off */ }
  }
  function acceptCall(v) {
    if (!v || !CALL_ID.test(v.id || '')) return;
    if (L.id !== v.id) rememberCall(v.id);
    const sig = JSON.stringify(v);
    if (sig === L.sig) return;
    L.sig = sig; L.call = v;
    drawLiveHeader(); syncLiveSections(v); liveWorld(v); drawHero();
    emit('call:update', v);
  }
  function schedulePoll() { clearTimeout(L.timer); if (!L.id) return; L.timer = setTimeout(pollCall, callBusy(L.call) ? 1000 : 5000); }
  async function pollCall() {
    try { await fx.get(`calls/${encodeURIComponent(L.id)}`); } catch (e) { if (e.status === 404) { forgetCall(); toast(e.message); } }
    refreshStatus();
    schedulePoll();
  }
  /* code-2 P2-7: spend, holds and engine ownership change without a key change. One status request at a time, at most
   * every 5 s (1 s while a stage runs), and a late answer never overwrites a newer one. */
  let statusSeq = 0, statusApplied = 0, statusAt = -1e9, statusPending = null;
  function refreshStatus(force) {
    if (!LIVE || statusPending) return statusPending;
    const now = performance.now();
    if (!force && now - statusAt < (callBusy(L.call) ? 1000 : 5000)) return null;
    statusAt = now;
    const seq = ++statusSeq;
    statusPending = fx.get('status').then((st) => {
      if (seq < statusApplied) return;
      statusApplied = seq; L.status = st; drawKeyChip(); drawLiveHeader(); drawHero();
    }).catch(() => { /* the next poll retries */ }).finally(() => { statusPending = null; });
    return statusPending;
  }
  async function startCall(request) { const v = await fx.post('calls', request); return v; }
  function newCall() { forgetCall(); location.href = location.pathname + '?live=1'; }
  const PORT_WORDS = (v) => {
    const raw = (v && ((v.request || {}).port || (v.case && v.case.request && v.case.request.port))) || '';
    return String(raw).replace(/_like$/, '').split('_').filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');
  };
  // ---------------------------------------------------------------- live: the planner's own Meta Model API key (Extension C)
  const KEY_SEEN = 'stowlm.functional.keyPanelSeen';
  const KEY_SOURCE = { environment: 'from the environment (MODEL_API_KEY)', env_file: 'from .env on this computer', entered_in_app: 'entered in this app' };
  const money = (v) => (typeof v === 'number' && isFinite(v) ? '$' + v.toFixed(2) : '$—');
  const keyState = () => (L.status && L.status.key) || null;
  function drawKeyChip() {
    const right = document.querySelector('#pl-header .pl-head-right'); if (!right) return;
    let chipEl = right.querySelector('.pl-key-chip');
    if (!chipEl) {
      chipEl = el('button', 'pl-chip pl-key-chip'); chipEl.type = 'button';
      chipEl.addEventListener('click', () => openKeyPanel());
      right.insertBefore(chipEl, right.firstChild);
    }
    const k = keyState(), on = !!(k && k.present);
    const who = !on ? '' : k.source === 'entered_in_app' ? 'your key' : k.source === 'env_file' ? 'key from .env' : 'key from the environment';
    const holds = on && typeof k.holds_usd === 'number' && k.holds_usd > 0 ? ` · ${money(k.holds_usd)} held` : '';
    // Three lengths: the full words, "API key · $ of $" below 1280 px, and "API key" below 960 px (planner-live.css).
    chipEl.innerHTML = on ? `<span class="pl-key-long">Meta Model API · ${esc(who)} · </span><span class="pl-key-short">API key · </span><span class="pl-key-money">${esc(money(k.spent_usd))} of ${esc(money(k.cap_usd))}${esc(holds)}${k.paid_calls_accepted ? '' : ' · paid calls off'}</span><span class="pl-key-tiny">API key</span>`
      : '<span>Connect API key</span>';
    chipEl.classList.toggle('on', on); chipEl.classList.toggle('off', !on);
    chipEl.title = on ? `Meta Model API key ${KEY_SOURCE[k.source] || ''}; it is never shown. Spent ${money(k.spent_usd)} of ${money(k.cap_usd)}${holds}. Click to change the cap${k.source === 'entered_in_app' ? ' or forget the key' : ''}.` : 'Enter your own Meta Model API key for Muse Spark and Muse Voice. The generator, engine and checker work without one.';
  }
  let keyPanelDone = null;
  function openKeyPanel() {
    if (!LIVE) return Promise.resolve(null);
    let panel = $('pl-key-panel');
    if (panel && !panel.hidden) return keyPanelDone ? keyPanelDone.promise : Promise.resolve(keyState());
    if (!panel) {
      panel = el('div', 'pl-key-backdrop'); panel.id = 'pl-key-panel';
      panel.addEventListener('click', (e) => { if (e.target === panel) closeKeyPanel(); });
      document.body.appendChild(panel);
    }
    const k = keyState() || {}, fromHost = k.present && k.source !== 'entered_in_app';
    const cap = typeof k.cap_usd === 'number' && k.cap_usd > 0 ? k.cap_usd : 10;
    const hold = typeof k.hold_usd_per_call === 'number' && k.hold_usd_per_call > 0 ? k.hold_usd_per_call : null; // never invented (logic-2 S3)
    panel.innerHTML = `<div class="pl-key-card" role="dialog" aria-modal="true" aria-labelledby="pl-key-title">
        <h2 id="pl-key-title">Meta Model API key</h2>
        <p>Your key is held by this local server (in memory, or in .env if you choose to save it) and is sent only to Meta's Model API with the calls you run. It is never shown, logged or written to a call folder.</p>
        <p class="pl-key-status">${k.present ? `Key ${esc(KEY_SOURCE[k.source] || 'present')}; it is never shown. Spent ${money(k.spent_usd)} of ${money(k.cap_usd)} this launch${k.paid_calls_accepted ? '' : '; paid calls not accepted yet'}.` : 'No key in this launch. Muse Spark and Muse Voice need one; the generator, Engine 0.13.0 and the full checker work without it.'}</p>
        ${fromHost ? '' : `<label class="pl-key-field"><span>${k.present ? 'Replace the key' : 'API key'}</span><input type="password" id="pl-key-input" autocomplete="off" spellcheck="false" placeholder="Paste your Meta Model API key"></label>`}
        <label class="pl-key-check"><input type="checkbox" id="pl-key-accept"${k.paid_calls_accepted ? ' checked' : ''}> I accept paid calls to the Meta Model API</label>
        <label class="pl-key-field"><span>Spend cap${fromHost ? ` (the key ${esc(KEY_SOURCE[k.source] || '')} stays as it is)` : ''}</span><span class="pl-key-cap">$<input type="number" id="pl-key-cap" min="0.1" step="0.5" value="${cap.toFixed(2)}"></span></label>
        <p class="pl-key-note pl-key-hold">${hold != null ? `Each Muse Spark call holds about ${money(hold)} until it settles (its worst case).` : 'Hold per call unknown: the server has not reported it.'} One development call read 75 boxes in 10 bookings for $0.67 (3 Nov). A booking is sent only while spent + held + its worst-case hold stays under your cap.</p>${typeof k.holds_usd === 'number' && k.holds_usd > 0 ? `<p class="pl-key-note">${money(k.holds_usd)} is held now by calls still settling.</p>` : ''}
        ${fromHost ? '' : `<label class="pl-key-check"><input type="checkbox" id="pl-key-save"> Save to .env on this computer</label><p class="pl-key-note">stored in a git-ignored file, readable by your user only</p>`}
        <p class="pl-key-error" id="pl-key-error" role="alert"></p>
        <div class="pl-key-actions">
          <button type="button" class="pl-btn primary" data-key="save">${k.present ? 'Save' : 'Connect'}</button>
          ${k.present && k.source === 'entered_in_app' ? '<button type="button" class="pl-btn secondary" data-key="forget">Forget key</button>' : ''}
          <button type="button" class="pl-btn secondary" data-key="close">${k.present ? 'Close' : 'Not now'}</button>
        </div></div>`;
    panel.hidden = false;
    panel.querySelector('[data-key="close"]').addEventListener('click', () => closeKeyPanel());
    panel.querySelector('[data-key="save"]').addEventListener('click', () => sendKey(panel));
    const forget = panel.querySelector('[data-key="forget"]');
    if (forget) forget.addEventListener('click', () => forgetKey(panel));
    const input = panel.querySelector('#pl-key-input'); if (input && input.focus) input.focus();
    let resolve; const promise = new Promise((res) => { resolve = res; });
    keyPanelDone = { promise, resolve };
    return promise;
  }
  function closeKeyPanel() {
    const panel = $('pl-key-panel'); if (!panel) return;
    const input = panel.querySelector('#pl-key-input'); if (input) input.value = '';
    panel.hidden = true; panel.innerHTML = '';
    try { localStorage.setItem(KEY_SEEN, '1'); } catch (e) { /* storage off */ }
    if (keyPanelDone) { keyPanelDone.resolve(keyState()); keyPanelDone = null; }
  }
  async function afterKey(result) {
    if (result && result.key) L.status = Object.assign({}, L.status || {}, { key: result.key });
    try { L.status = await fx.get('status'); } catch (e) { /* keep what the key route said */ }
    drawKeyChip(); drawLiveHeader();
    if (L.id) { try { await fx.get(`calls/${encodeURIComponent(L.id)}`); } catch (e) { /* the poll retries */ } }
    emit('key:update', keyState());
  }
  async function sendKey(panel) {
    const input = panel.querySelector('#pl-key-input'), err = panel.querySelector('#pl-key-error');
    const body = { accept_paid_calls: !!panel.querySelector('#pl-key-accept').checked, cap_usd: Number(panel.querySelector('#pl-key-cap').value) };
    const save = panel.querySelector('#pl-key-save');
    if (save && save.checked) body.save_to_env_file = true;
    if (input && input.value.trim()) body.key = input.value.trim();
    if (input) input.value = ''; // never kept in the page once read
    if (!body.key && !(keyState() || {}).present) { err.textContent = 'Paste your API key first.'; return; }
    if (!(body.cap_usd > 0)) { err.textContent = 'Set a spend cap above $0.'; delete body.key; return; }
    try { const result = await fx.post('key', body); delete body.key; await afterKey(result); closeKeyPanel(); toast(result && result.saved_to_env_file ? 'Key connected and saved to .env' : 'Key connected for this launch'); }
    catch (e) { delete body.key; err.textContent = e.message; }
  }
  async function forgetKey(panel) {
    try { const r = await fetch('/api/functional/key', { method: 'DELETE', headers: { Accept: 'application/json' } }); const out = await r.json().catch(() => null); if (!r.ok) throw new Error((out && out.error) || 'The key could not be forgotten.'); await afterKey(out); closeKeyPanel(); toast('Key forgotten'); }
    catch (e) { const err = panel.querySelector('#pl-key-error'); if (err) err.textContent = e.message; }
  }
  function maybeFirstRunKeyPanel() {
    const k = keyState(); if (!k || k.present) return;
    let seen = null; try { seen = localStorage.getItem(KEY_SEEN); } catch (e) { seen = L.keySeen ? '1' : null; }
    if (seen || L.keySeen) return;
    L.keySeen = true; openKeyPanel();
  }

  function drawLiveHeader() {
    if (!LIVE) return;
    drawKeyChip();
    const v = L.call, ship = S.ship && S.ship.ship;
    $('pl-call-title').textContent = v ? `${PORT_WORDS(v) ? PORT_WORDS(v) + ' (simulated)' : 'Synthetic port'} · call ${v.id.slice(-6)}` : 'New call';
    const teu = ship && ship.teu ? ship.teu : v && v.case && /feeder_(\d+)/.test(v.case.ship || '') ? +RegExp.$1 : null;
    // logic-2 S4: name the planned section, never imply the whole ship.
    const size = v && v.case && v.case.size_class, section = { medium: 'half-ship', tiny: 'tiny', small: 'small' }[size];
    $('pl-call-sub').textContent = teu ? `Synthetic feeder, ${teu} TEU${section ? `, ${section} section` : ''} · simulated port` : 'Synthetic ship and cargo, simulated port';
    const tag = $('pl-demo-tag');
    if (tag) {
      const badge = tag.querySelector('.pl-demo-badge'), line = tag.querySelector('.pl-demo-line'), link = tag.querySelector('.pl-demo-link');
      tag.classList.add('live');
      if (badge) { badge.textContent = 'Functional app'; badge.title = 'Runs the pipeline live on fresh synthetic inputs; nothing is replayed'; }
      if (line) line.textContent = 'Runs the pipeline live on fresh synthetic inputs; nothing is replayed';
      if (link) { link.textContent = 'Open the Demo app'; link.setAttribute('href', '/planner?demo=1'); }
    }
    const avail = (v && v.availability) || (L.status && L.status.availability) || {}, reader = avail.reader || null;
    const chipEl = document.querySelector('#pl-header .pl-head-right .pl-chip.actor');
    if (chipEl) {
      const on = !!(reader && reader.available);
      const adopted = !reader || reader.adopted !== false; // logic-2 S15: a non-adopted adapter is not "the StowLM Reader"
      chipEl.textContent = !on ? 'StowLM Reader · unavailable' : adopted ? 'StowLM Reader · on this laptop' : 'Glimmer 30B LoRA (not the adopted reader) · on this laptop';
      chipEl.style.setProperty('--c', on ? 'var(--pl-c-reader)' : 'var(--pl-fail)');
      chipEl.classList.toggle('off', !on);
      chipEl.title = on ? (adopted ? 'StowLM Reader: fine-tuned Muse Glimmer 30B (LoRA), on this laptop' : `A Muse Glimmer 30B LoRA adapter that is not the adopted StowLM Reader (adapter ${String(reader.adapter_sha256 || '').slice(0, 10)})`) : `StowLM Reader unavailable: ${(reader && reader.reason) || (L.off || 'status not loaded')}. Nothing is replayed in its place.`;
    }
  }
  /* A reloaded call: the sections show what the call view says is done (modules may say it again; done is idempotent). */
  function syncLiveSections(v) {
    const voyage = byId('voyage'), intake = byId('intake'), instruct = byId('instruct');
    if (voyage && v.case && !voyage.done) markDone(voyage);
    const after = ['ready', 'planning', 'done'].includes(v.state);
    if (intake && after && !intake.done) {
      const ll = v.load_list || {}, intakeView = (v.papers || {}).intake;
      setStatus(intake, ll.boxes != null ? `${ll.boxes} boxes ${intakeView && !intakeView.skipped ? 'read by Muse Spark' : 'from the generated load list (not read by a model)'}` : null);
      markDone(intake);
    }
    const confirmed = (v.readings || []).find((r) => r.confirmed);
    if (confirmed && (!S.state.instruction || S.state.instruction.reading !== confirmed)) S.state.instruction = { mode: 'confirmed', reading: confirmed };
    // Only a reloaded call infers "no instruction" (undefined); after the planner clears it to plan again (null), never.
    if (!confirmed && S.state.instruction === undefined && (((v.run || {}).instruction === 'none') || (!(v.run || {}).instruction && ['planning', 'done'].includes(v.state)))) S.state.instruction = { mode: 'none' };
    if (instruct && S.state.instruction && !instruct.done && (confirmed || ['planning', 'done'].includes(v.state))) {
      setStatus(instruct, confirmed ? (confirmed.rule_text || 'Reading confirmed') : 'No instruction: the reader had nothing to do');
      markDone(instruct);
    }
  }
  function liveWorld(v) {
    const key = v && v.case ? v.id + ':' + (['ready', 'planning', 'done'].includes(v.state) ? 'assembled' : 'base') : '';
    if (!key || key === L.world) return;
    L.world = key;
    loadLiveWorld(key).catch((e) => console.warn('[planner] live world', e));
  }
  function emptyBay(text) { S.bp = NOOP_BAY; $('pl-bay').innerHTML = `<div class="pl-bay-empty">${esc(text)}</div>`; }
  async function loadLiveWorld(key) {
    const [ship, boxes, before] = await Promise.all(['ship', 'boxes', 'plan-before'].map((n) => data(n).catch(() => null)));
    if (L.world !== key) return;
    S.ship = ship; S.boxes = boxes; S.planBefore = before;
    if (S.ll && boxes) S.ll.setData(boxes);
    // The base and assembled instances usually draw the same ship and boxes: rebuild the bay plan only when they change.
    const sig = JSON.stringify([ship && ship.bays, ship && ship.hatch_covers, boxes && boxes.boxes, boxes && boxes.pods]);
    const same = sig === L.baySig && S.bp && S.bp !== NOOP_BAY;
    L.baySig = sig;
    if (same) { drawLegend(); drawLiveHeader(); drawHero(); emit('world:ready', { ship, boxes, key }); return; }
    $('pl-bay').innerHTML = ''; S.bp = null;
    if (window.BayPlan && ship && boxes) {
      try { S.bp = window.BayPlan.create($('pl-bay'), { ship, boxes: boxes.boxes, pods: boxes.pods }); } catch (e) { console.warn('[planner] BayPlan.create', e); S.bp = null; }
      if (S.bp && S.bp.on) S.bp.on('select', (id) => { if (S.ll) { showLoadList(true); S.ll.focus(id); } });
    }
    if (!S.bp) emptyBay('The bay plan could not be drawn from this call\'s ship data');
    drawLegend(); drawLiveHeader();
    const arrival = (ship && ship.initial_plan) || null;
    // code-2 P1-2: the plan module may have drawn (on the stand-in bay) before this world arrived. Keep what it drew.
    const kept = S.generated && S.plan && S.plan !== L.arrival && (!arrival || S.plan.plan_id !== arrival.plan_id) ? S.plan : null;
    L.arrival = arrival;
    if (kept) {
      try { S.bp.setPlan(kept); } catch (e) { console.warn('[planner] setPlan', e); }
      for (const b of S.closedBays) { try { S.bp.closeBay(b, `Bay ${String(b).padStart(2, '0')} · closed`); } catch (e) { /* ignore */ } }
      if (S.ll) S.ll.setPlan(kept);
    } else {
      S.plan = arrival;
      try { if (S.bp.preGenerate) S.bp.preGenerate(arrival ? arrival.slots || [] : []); else S.bp.setPlan(arrival || { slots: [] }); } catch (e) { console.warn('[planner] preGenerate', e); }
      if (S.ll && before) S.ll.setPlan(before);
    }
    if (!S.generated) metersFromPlan(null);
    drawHero();
    emit('world:ready', { ship, boxes, key, kept: !!kept });
  }

  // ---------------------------------------------------------------- autopilot cursor
  const cur = { x: innerWidth * 0.62, y: innerHeight * 0.58, shown: false };
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  function placeCursor() { const c = $('pl-cursor'); if (c) c.style.transform = `translate(${cur.x - 3}px, ${cur.y - 2}px)`; }
  function showCursor(on) { const c = $('pl-cursor'); if (!c) return; cur.shown = on; c.classList.toggle('on', on); document.body.classList.toggle('pl-autopilot', on); placeCursor(); }
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
    if (typeof target === 'string') for (const root of [scope, $('pl-sheet'), $('pl-report'), $('pl-right'), document]) { if (!root) continue; const n = root.querySelector(target); if (n) return n; }
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
    const pane = n.closest('.pl-panel-body, .pl-sheet-body, #pl-report, .pl-ll-scroll');
    const pr = pane ? pane.getBoundingClientRect() : { top: 0, bottom: innerHeight };
    if (r.top >= pr.top + 8 && r.bottom <= pr.bottom - 8) return;
    n.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
    await sleep(450);
  }
  function ripple(x, y) { const r = el('div', 'pl-ripple'); r.style.left = x + 'px'; r.style.top = y + 'px'; document.body.appendChild(r); setTimeout(() => r.remove(), 550); }
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
    const c = $('pl-cursor'); c && c.classList.add('press');
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
    const c = $('pl-cursor'); c && c.classList.add('press');
    fire(target, 'pointerdown', a.x, a.y, { buttons: 1 }); fire(target, 'mousedown', a.x, a.y, { buttons: 1 });
    await sleep(140);
    const b = pointOf(to);
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    const ms = (opts && opts.ms) || Math.min(1400, Math.max(700, 450 + dist * 0.9));
    // A component may re-render under the pointer mid-drag: send each event where a real pointer's would land (the
    // original target while it is still in the page, else whatever is under the point), so it always reaches the page.
    const live = (x, y) => (target.isConnected ? target : document.elementFromPoint(x, y) || document.body);
    await glide(b.x, b.y, ms, (x, y) => { const t = live(x, y); fire(t, 'pointermove', x, y, { buttons: 1 }); fire(t, 'mousemove', x, y, { buttons: 1 }); });
    await sleep(160);
    { const t = live(b.x, b.y); fire(t, 'pointerup', b.x, b.y); fire(t, 'mouseup', b.x, b.y); }
    c && c.classList.remove('press');
    await sleep(160);
    return true;
  }

  // ---------------------------------------------------------------- shared state, events
  const S = { defs: [], list: [], cur: null, state: {}, handlers: new Map(), stateDoc: null, log: [], ship: null, boxes: null, plan: null, bp: null, ll: null,
    meters: {}, sheetOwner: null, sheets: new Map(), rang: false, generated: false, closedBays: new Set() };
  function emit(evt, payload) { for (const fn of S.handlers.get(evt) || []) { try { fn(payload); } catch (e) { console.warn('[planner] handler for', evt, e); } } }
  function on(evt, fn) { if (!S.handlers.has(evt)) S.handlers.set(evt, new Set()); S.handlers.get(evt).add(fn); return () => S.handlers.get(evt).delete(fn); }
  function chip(text, kind) { return `<span class="pl-chip st-chip${kind ? ' ' + esc(kind) : ''}" title="${esc(text)}">${esc(text)}</span>`; }
  function log(d, entry) { S.log.push({ t: Date.now(), step: d && d.id, ...(entry || {}) }); emit('log', entry); }
  function toast(text, kind) {
    const host = $('pl-toasts'); if (!host) return;
    const t = el('div', 'pl-toast' + (kind ? ' ' + kind : '')); t.textContent = text; host.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 250); }, 3000);
  }

  // ---------------------------------------------------------------- bay plan (window.BayPlan) or a quiet stand-in
  const NOOP_BAY = { setPlan() {}, showBays() {}, focusBay() {}, closeBay() {}, highlight() {}, play: async () => {}, enableDrag() {}, markViolation() {}, clearMarks() {}, on() {}, screenPoint: () => null };
  const slotOf = (boxId) => ((S.plan && S.plan.slots) || []).find((s) => s.box_id === boxId) || null;
  function setPlan(plan) {
    S.plan = plan || null;
    try { S.bp.setPlan(plan); } catch (e) { console.warn('[planner] bay plan setPlan', e); }
    if (S.ll) S.ll.setPlan(plan);
    emit('plan:set', plan);
  }
  function drawLegend() {
    const pods = (S.boxes && S.boxes.pods) || [], colour = window.PlannerLoadList ? window.PlannerLoadList.podColours(pods) : new Map();
    $('pl-legend').innerHTML = `<span class="pl-legend-title" title="Port of discharge">POD</span>${pods.map((p) => `<span class="pl-pod" title="${esc(p.name_words || p.code)}"><i style="--pod:${esc(colour.get(p.code))}"></i>${esc(p.code)}</span>`).join('')}
      <span class="pl-sep"></span><span class="pl-size-key"><i style="width:12px"></i>20′</span><span class="pl-size-key"><i style="width:24px"></i>40′</span>
      <span class="pl-sep"></span><span class="pl-dg">DG</span><span class="pl-reefer">❄︎&nbsp;<span style="color:var(--pl-text-2);font-weight:400">Reefer</span></span>
      <span class="pl-sep"></span><span class="pl-closed-key"><i></i>Closed bay</span>`;
  }

  // ---------------------------------------------------------------- meters (status bar)
  const METERS = [['stability', 'Stability'], ['list', 'List / trim'], ['draught', 'Draught'], ['hull', 'Hull stress'], ['moves', 'Moves'], ['rules', 'Rule checks']];
  const num = (v) => typeof v === 'number' && isFinite(v);
  function buildMeters() {
    $('pl-meters').innerHTML = METERS.map(([k, label]) => `<div class="pl-meter" data-m="${k}" data-state="empty"><div class="pl-meter-top"><b>${label}</b><i>—</i></div><div class="pl-meter-bar"><u></u></div><div class="pl-meter-limit"></div></div>`).join('');
  }
  function setMeter(key, m) {
    const node = document.querySelector(`#pl-meters [data-m="${key}"]`); if (!node || m == null) return;
    const v = typeof m === 'string' ? { value: m } : m;
    S.meters[key] = v;
    node.querySelector('i').textContent = v.value != null ? v.value : '—';
    node.querySelector('.pl-meter-limit').textContent = v.limit || '';
    node.querySelector('u').style.width = num(v.ratio) ? `${Math.max(0, Math.min(1, v.ratio)) * 100}%` : '0';
    node.dataset.state = v.state || (v.value == null ? 'empty' : 'ok');
    node.title = v.title || [v.value, v.limit].filter(Boolean).join(' · ');
  }
  /* Physics meters from a plan file's own measured numbers (plan.meters), limits from ship.json. */
  function metersFromPlan(plan) {
    const L = (S.ship && S.ship.limits) || {}, m = (plan && plan.meters) || null, f1 = (x) => x.toFixed(1), f2 = (x) => x.toFixed(2);
    const gmMin = L.gm_min_m, heelMax = L.heel_max_deg, dMax = L.draught_m;
    if (!m) {
      setMeter('stability', { value: null, limit: num(gmMin) ? `minimum GM ${f2(gmMin)} m` : 'not planned yet', state: 'empty' });
      setMeter('list', { value: null, limit: num(heelMax) ? `list limit ${f1(heelMax)}°` : 'not planned yet', state: 'empty' });
      setMeter('draught', { value: null, limit: num(dMax) ? `limit ${f2(dMax)} m` : 'not planned yet', state: 'empty' });
      setMeter('hull', { value: null, limit: 'limit 100 %', state: 'empty' });
      return;
    }
    if (num(m.gm_m)) setMeter('stability', { value: `GM ${f2(m.gm_m)} m`, limit: num(gmMin) ? `minimum ${f2(gmMin)} m` : '', ratio: num(gmMin) && m.gm_m > 0 ? gmMin / m.gm_m : null, state: num(gmMin) && m.gm_m < gmMin ? 'fail' : 'ok' });
    if (num(m.heel_deg)) setMeter('list', { value: `${f1(Math.abs(m.heel_deg))}° · ${num(m.trim_m) ? f2(Math.abs(m.trim_m)) + ' m' : '—'}`, limit: num(heelMax) ? `list limit ${f1(heelMax)}°` : '', ratio: num(heelMax) ? Math.abs(m.heel_deg) / heelMax : null, state: num(heelMax) && Math.abs(m.heel_deg) > heelMax ? 'fail' : 'ok' });
    if (num(m.draught_m)) setMeter('draught', { value: `${f2(m.draught_m)} m`, limit: num(dMax) ? `limit ${f2(dMax)} m` : '', ratio: num(dMax) ? m.draught_m / dMax : null, state: num(dMax) && m.draught_m > dMax ? 'fail' : 'ok' });
    if (num(m.hull_pct)) setMeter('hull', { value: `${Math.round(m.hull_pct)} %`, limit: 'of limit', ratio: m.hull_pct / 100, state: m.hull_pct > 100 ? 'fail' : 'ok' });
  }
  function meters(update) {
    for (const [k, v] of Object.entries(update || {})) {
      if (k === 'moves' && v && typeof v === 'object' && 'total' in v) {
        setMeter('moves', { value: `${v.at || 0} of ${v.total}`, limit: v.limit || 'crane moves in this plan', ratio: v.total ? (v.at || 0) / v.total : 0, state: 'info' });
        const sc = $('pl-scrub'); sc.hidden = !v.total; const r = $('pl-scrub-range'); r.max = v.total; if (document.activeElement !== r) r.value = v.at || 0;
        $('pl-scrub-label').textContent = `Move ${v.at || 0} of ${v.total}`;
      } else setMeter(k, v);
    }
  }

  // ---------------------------------------------------------------- sheet (documents, x-ray, package over the centre)
  function sheet(d, node, title) {
    const body = $('pl-sheet-body');
    if (!node) { if (!d || S.sheetOwner === d.id) { $('pl-sheet').hidden = true; S.sheetOwner = null; for (const c of body.children) c.style.display = 'none'; } return; }
    if (node.parentNode !== body) body.appendChild(node);
    for (const c of body.children) c.style.display = c === node ? '' : 'none';
    $('pl-sheet-title').textContent = title || node.dataset.sheetTitle || (d && meta(d).title) || '';
    $('pl-sheet').hidden = false; S.sheetOwner = d ? d.id : null;
  }

  // ---------------------------------------------------------------- stages: inputs (left), the plan (centre), review (right), reports
  function makeCtx(d) {
    const c = makeDemoCtx(d);
    if (LIVE) {
      Object.defineProperty(c, 'call', { get: () => L.call, enumerable: true });
      Object.assign(c, { live: true, fx, openKeyPanel, callId: () => L.id, startCall, newCall, refresh: () => (L.id ? fx.get(`calls/${encodeURIComponent(L.id)}`) : Promise.resolve(null)) });
    }
    return c;
  }
  function makeDemoCtx(d) {
    return {
      id: d.id, api, post, data,
      asset: (name) => '/api/v6/asset/' + name,
      get bay() { return S.bp || NOOP_BAY; },
      get loadlist() {
        if (!S.ll) return null;
        return Object.assign(Object.create(S.ll), { setRows: (rows, m) => { S.llOwner = d.id; return S.ll.setRows(rows, m); }, show: (on) => showLoadList(on !== false) });
      },
      get ship() { return S.ship; }, get boxes() { return S.boxes; }, get plan() { return S.plan; },
      setPlan, slotOf,
      slot: (name) => (name === 'result' ? $('pl-result') : null), // the plan module's result strip lives in the centre
      sheet: (node, title) => sheet(d, node, title),
      center: (node) => sheet(d, node),
      meters,
      log: (e) => log(d, e), chip, toast, esc,
      done() { markDone(d); },
      next() { const n = nextOf(d); if (n) open(n.id); },
      begin: (id) => act(id),
      status: (text) => setStatus(d, text),
      get state() { return S.state; },
      emit, on, wait,
      get demo() { return DEMO; }, get speed() { return SPEED; }, get autopilot() { return !!S.autoplaying; },
      click: (t) => click(t, d.body), drag,
      point: (slotOrBox) => { try { return S.bp && S.bp.screenPoint ? S.bp.screenPoint(slotOrBox) : null; } catch (e) { return null; } },
    };
  }
  const visible = () => S.list.filter((d) => !d.gap);
  const byId = (id) => S.list.find((x) => x.id === id && !x.gap);
  function nextOf(d) { const v = visible(); const i = v.indexOf(d); return i >= 0 ? v[i + 1] || null : null; }
  const STATE_ICON = { todo: '', running: '', needs: '', done: 'check' };
  function setState(d, state) {
    d.state = state;
    if (d.sec) { d.sec.dataset.state = state; d.sec.querySelector('.pl-sec-mark').innerHTML = STATE_ICON[state] ? icon(STATE_ICON[state]) : ''; }
    if (d.tabBtn) d.tabBtn.dataset.state = state;
    drawHero(); drawInputsSum();
  }
  const LIVE_HINT = { voyage: 'Choose the ship and call; a fresh synthetic case is generated', intake: 'Read the call\'s cargo papers with Muse Spark, or use the generated load list', instruct: 'Type or say one instruction; the StowLM Reader reads it, you confirm it' };
  function setStatus(d, text) { d.statusText = text; if (d.sec) d.sec.querySelector('.pl-sec-sum').textContent = text || (LIVE ? LIVE_HINT[d.id] : meta(d).hint) || ''; }

  function buildStage(d) {
    const m = meta(d);
    if (m.place === 'report') {
      const part = el('section', 'pl-reportstep'); part.hidden = true;
      part.innerHTML = '<div class="pl-report-center"></div><div class="pl-report-panel"></div>';
      $('pl-report-inner').appendChild(part);
      d.part = part; d.sheetHost = part.querySelector('.pl-report-center'); d.body = part.querySelector('.pl-report-panel');
      return;
    }
    if (m.place === 'input') {
      const sec = el('section', 'pl-sec');
      sec.dataset.stage = d.id;
      sec.innerHTML = `<button type="button" class="pl-sec-head" aria-expanded="false"><span class="pl-sec-mark"></span><span class="pl-sec-title"><b>${m.n} · ${esc(m.title)}</b><span class="pl-sec-sum"></span></span>${icon('chevron')}</button>
        <div class="pl-sec-body">${m.who.length ? `<div class="pl-panel-who">${whoChips(m.who)}</div>` : ''}<div class="pl-sec-panel"></div><div class="pl-sec-foot"></div></div>`;
      sec.querySelector('.pl-sec-head').addEventListener('click', () => toggleSection(d));
      $('pl-sections').appendChild(sec);
      d.sec = sec; d.body = sec.querySelector('.pl-sec-panel'); d.foot = sec.querySelector('.pl-sec-foot');
      setStatus(d, null); setState(d, m.done0 && !LIVE ? 'done' : 'todo');
      return;
    }
    // review tab
    const btn = el('button', 'pl-tab', `<span>${esc(m.tab || d.title)}</span><i class="pl-tab-dot"></i>`);
    btn.type = 'button'; btn.dataset.tab = d.id; btn.disabled = true; btn.title = 'Generate a plan first';
    btn.addEventListener('click', () => open(d.id));
    $('pl-tabs').appendChild(btn); d.tabBtn = btn;
    const panel = el('section', 'pl-panel'); panel.hidden = true; panel.dataset.stage = d.id;
    panel.innerHTML = `<div class="pl-panel-who">${whoChips(Array.isArray(d.who) ? d.who : m.who)}</div><div class="pl-panel-body"></div><footer class="pl-panel-foot"></footer>`;
    $('pl-panels').appendChild(panel);
    d.panel = panel; d.body = panel.querySelector('.pl-panel-body'); d.foot = panel.querySelector('.pl-panel-foot');
    setState(d, 'todo');
  }
  /* A stage's primary action, unless the module draws its own (an element marked data-primary). */
  function drawFoot(d) {
    const m = meta(d), f = d.foot; if (!f) return;
    f.innerHTML = '';
    const own = (d.view && d.view.ownsAction) || (d.body && d.body.querySelector('[data-primary]'));
    if (!d.started && m.action && !own) {
      const b = el('button', 'pl-btn primary', esc(m.action)); b.type = 'button'; b.dataset.action = d.id; b.addEventListener('click', () => act(d.id)); f.appendChild(b);
    }
    if (d.id === 'intake' && (d.done || S.llOwner)) {
      const b = el('button', 'pl-btn secondary', 'View load list'); b.type = 'button'; b.dataset.view = 'loadlist'; b.addEventListener('click', () => showLoadList(true)); f.appendChild(b);
    }
    f.hidden = !f.children.length;
  }
  function toggleSection(d, force) {
    if (!d.sec) return;
    const open1 = force != null ? force : !d.sec.classList.contains('open');
    d.sec.classList.toggle('open', open1);
    d.sec.querySelector('.pl-sec-head').setAttribute('aria-expanded', String(open1));
    if (open1) { S.cur = d; enterOnce(d); }
  }
  function showLoadList(on) {
    $('pl-llview').hidden = !on; $('pl-inputs').hidden = !!on;
  }
  function drawInputsSum() {
    const ins = S.list.filter((x) => meta(x).place === 'input'), done = ins.filter((x) => x.state === 'done').length;
    $('pl-inputs-sum').textContent = ins.length ? `${done} of ${ins.length} ready` : '';
  }
  function dropStep(d, why) {
    if (d.gap) return; d.gap = true; console.warn(`[planner] stage ${d.id} is not shown:`, why);
    if (d.sec) d.sec.remove(); if (d.tabBtn) d.tabBtn.remove(); drawHero();
  }
  function mountStep(d) {
    if (d.mounting) return d.mounting;
    d.mounting = (async () => {
      const feats = d.features || [], doc = S.stateDoc;
      for (const f of feats) { const st = doc && doc.features && doc.features[f]; if (st && st.available === false) throw new Error(st.reason || `${f} unavailable`); }
      const payloads = await Promise.all(feats.map((f) => api(f)));
      const bag = {}; feats.forEach((f, i) => { bag[f] = payloads[i]; });
      for (const f of feats) if (bag[f] && bag[f].available === false) throw new Error(bag[f].reason || `${f} unavailable`);
      d.ctx = d.ctx || makeCtx(d);
      d.view = d.mount(d.body, bag, d.ctx) || {};
      d.mounted = true; drawFoot(d); return true;
    })().catch((e) => { dropStep(d, e && e.message ? e.message : e); return false; });
    return d.mounting;
  }
  async function enterOnce(d) {
    if (d.entered) return; d.entered = true;
    if (!(await mountStep(d))) return;
    if (d.view.enter) { try { await Promise.race([Promise.resolve(d.view.enter()), sleep(80)]); } catch (e) { console.warn('[planner] enter', d.id, e); } }
    drawFoot(d);
  }
  function markDone(d) {
    if (!d || d.done) return;
    d.done = true; d.started = true;
    if (meta(d).auto) { S.rang = false; const dot = $('pl-bell').querySelector('.pl-badge'); if (dot) dot.remove(); }
    if (!d.statusText) setStatus(d, meta(d).status || 'Done');
    setState(d, 'done'); drawFoot(d); emit('step:done', d.id);
    if (meta(d).place === 'input' && d.sec) setTimeout(() => toggleSection(d, false), 2200 / SPEED); // done inputs fold to their one-line summary
    if (d.id === 'intake' && !LIVE) { const nx = byId('instruct'); if (nx && !nx.started && !AUTOPLAY) setTimeout(() => ringRadio(nx), 1500 / SPEED); }
    if (meta(d).generate) planReady();
  }
  function ringRadio(d) {
    const bell = $('pl-bell'); if (!bell.querySelector('.pl-badge')) bell.appendChild(el('i', 'pl-badge'));
    S.rang = true; d.rang = true; if (!d.started) setState(d, 'needs');
    toggleSection(d, true);
    if (!AUTOPLAY) act(d.id);
  }

  // ---------------------------------------------------------------- the centre: pre-generate hero, Generate plan, result
  function inputsReady() { const c = byId('intake'), i = byId('instruct'); return { cargo: !!(c && c.done), instr: !!(i && i.done) }; }
  function drawHero() {
    const hero = $('pl-hero'); if (!hero || S.generated) return;
    if (LIVE) return drawHeroLive(hero);
    const r = inputsReady(), n = (S.ship && S.ship.ship) || {}, closed = S.closedBays && S.closedBays.size ? [...S.closedBays] : [];
    const planned = S.planBefore && S.planBefore.counts ? S.planBefore.counts.boxes : null;
    const chips = [
      [true, `Ship · ${n.teu ? n.teu + ' TEU' : 'synthetic feeder'}${n.section_teu ? `, ${n.section_teu} TEU section` : ''}`],
      [r.cargo, r.cargo ? `${planned != null ? planned + ' boxes' : 'Cargo'} ready to plan` : 'Cargo papers not imported'],
      [r.instr, r.instr ? (closed.length ? closed.map((b) => `Bay ${b} closed`).join(' · ') : 'Instruction confirmed') : 'Instruction not confirmed'],
    ];
    $('pl-hero-chips').innerHTML = chips.map(([ok, t]) => `<li class="${ok ? 'ok' : ''}"><i>${ok ? icon('check') : ''}</i>${esc(t)}</li>`).join('');
    const ready = r.cargo && r.instr, gen = $('pl-generate');
    gen.disabled = !ready;
    $('pl-hero-why').textContent = ready ? 'Inputs ready' : !r.cargo && !r.instr ? 'Import the cargo papers and confirm the radio instruction first' : !r.cargo ? 'Import the cargo papers first' : 'Confirm the radio instruction first';
    hero.classList.toggle('ready', ready);
  }
  const PROFILES = { live: 'live profile: engine deadline 720 s; not a whole-run guarantee', standard: 'standard profile: engine deadline 1800 s; not a whole-run guarantee' };
  function drawHeroLive(hero) {
    const v = L.call, ship = (S.ship && S.ship.ship) || {}, c = byId('intake'), i = byId('instruct');
    const headline = v && v.case && v.case.headline_envelope;
    const inside = !!(v && v.envelope && v.envelope.in_envelope === true) && !(headline && headline.in_envelope === false);
    const cargo = !!(c && c.done && v && ['ready', 'planning', 'done'].includes(v.state));
    const how = S.state.instruction, instr = !!(i && i.done && how);
    const boxes = v && v.load_list ? v.load_list.boxes : null;
    const chips = [
      [!!(v && v.case), v && v.case ? `Ship · ${ship.teu ? ship.teu + ' TEU' : v.case.ship}${ship.section_teu ? `, ${ship.section_teu} TEU section` : ''}` : 'Call not created'],
      [cargo, cargo ? `${boxes != null ? boxes + ' boxes' : 'Cargo'} ready to plan` : 'Cargo papers not read'],
      [instr, instr ? (how.mode === 'confirmed' ? 'Instruction confirmed' : 'No instruction (planner\'s choice)') : 'Instruction not confirmed'],
    ];
    $('pl-hero-chips').innerHTML = chips.map(([ok, t]) => `<li class="${ok ? 'ok' : ''}"><i>${ok ? icon('check') : ''}</i>${esc(t)}</li>`).join('');
    const engine = (L.status && L.status.engine) || {}, busyElsewhere = engine.busy && engine.call && v && engine.call !== v.id;
    const ready = !!(v && v.state === 'ready' && inside && cargo && instr && !busyElsewhere);
    const gen = $('pl-generate'); gen.disabled = !ready;
    const why = !v || !v.case ? 'Create the call first (1 · Ship & call)'
      : v.state === 'out_of_envelope' || (v.envelope && v.envelope.in_envelope === false) || (headline && headline.in_envelope === false) ? 'Outside the envelope: no engine run starts for this call'
        : v.state === 'failed' ? (v.message || 'The call stopped')
          : !cargo ? 'Read the cargo papers, or use the generated load list, first'
            : !instr ? 'Confirm a reading, or choose Plan with no instruction, first'
              : busyElsewhere ? `The engine is busy with call ${engine.call}; one engine job runs at a time` : 'Inputs ready';
    $('pl-hero-why').textContent = why + (S.state.closureHint ? ' · On tiny and small sections a bay closure usually leaves no room for the load: an artefact of the section size, not a crane limit.' : '');
    hero.classList.toggle('ready', ready);
    let prof = hero.querySelector('.pl-live-profile');
    if (!prof) {
      prof = el('div', 'pl-live-profile');
      prof.innerHTML = `<div class="pl-seg" role="group" aria-label="Engine profile"><button type="button" data-profile="standard">Standard</button><button type="button" data-profile="live">Live</button></div><p class="pl-live-profile-words"></p><p class="pl-live-profile-caveat">standard is the profile the held-out results used; live is shorter and its pass rate is measured only on development maps</p>`;
      prof.addEventListener('click', (e) => { const b = e.target.closest('[data-profile]'); if (b) { S.state.profile = b.dataset.profile; drawHero(); } });
      gen.parentNode.insertBefore(prof, gen);
    }
    const p = S.state.profile in PROFILES ? S.state.profile : 'standard'; S.state.profile = p;
    for (const b of prof.querySelectorAll('[data-profile]')) { b.classList.toggle('on', b.dataset.profile === p); b.setAttribute('aria-pressed', String(b.dataset.profile === p)); }
    prof.querySelector('.pl-live-profile-words').textContent = PROFILES[p];
  }
  async function generate() {
    const d = byId('plan'); if (!d || S.generated) return;
    S.generated = true;
    $('pl-hero').classList.add('going'); setTimeout(() => { $('pl-hero').hidden = true; }, 320);
    $('pl-result').hidden = false;
    for (const x of S.list) if (x.tabBtn) { x.tabBtn.disabled = false; x.tabBtn.title = ''; }
    planReady();
    S.cur = d; selectTab(d);
    d.started = true; setState(d, 'running'); paceLog(d, 'Generate plan');
    await enterOnce(d);
    d.startP = (async () => { if (d.view && d.view.start) { try { await d.view.start(); } catch (e) { console.warn('[planner] start', d.id, e); } } })();
    emit('plan:generate');
    await d.startP;
  }
  function planReady() { for (const x of S.list) if (x.tabBtn) { x.tabBtn.disabled = false; x.tabBtn.title = ''; } $('pl-review-lock').hidden = true; $('pl-panels').hidden = false; }
  function stamp(text) {
    const s = $('pl-stamp'); s.textContent = text; s.hidden = false; s.classList.remove('in'); void s.offsetWidth; s.classList.add('in');
    clearTimeout(S.stampT); S.stampT = setTimeout(() => { s.classList.add('out'); setTimeout(() => { s.hidden = true; s.classList.remove('out'); }, 400); }, 2600);
  }

  // ---------------------------------------------------------------- review tabs and reports
  function selectTab(d) {
    for (const x of S.list) { if (x.tabBtn) x.tabBtn.classList.toggle('on', x === d); if (x.panel) x.panel.hidden = x !== d; }
  }
  function showView(view) {
    const rep = view !== 'plan' ? byId(view) : null;
    for (const b of document.querySelectorAll('#pl-nav [data-view]')) b.classList.toggle('on', b.dataset.view === (rep ? view : 'plan'));
    for (const x of S.list) if (x.part) x.part.hidden = x !== rep;
    $('pl-report').hidden = !rep;
    if (rep) $('pl-report').scrollTop = 0;
  }
  /* Open a stage where it lives: an input section expands, a review tab selects, a report shows. Nothing is locked except
   * the review tabs before a plan exists (they say why). */
  async function open(id) {
    const d = byId(id); if (!d) return;
    const m = meta(d), old = S.cur;
    if (old && old !== d && meta(old).place === 'review' && m.place === 'review' && old.view && old.view.leave && old.entered) { try { old.view.leave(); } catch (e) { console.warn('[planner] leave', old.id, e); } old.entered = false; }
    if (old && old !== d && S.sheetOwner === old.id) sheet(old, null);
    if (old && old !== d && meta(old).sandbox && old.meterSnap) { for (const [k, v] of Object.entries(old.meterSnap)) setMeter(k, v); $('pl-scrub').hidden = true; old.meterSnap = null; }
    if (S.llOwner && S.llOwner !== d.id && S.ll && m.place !== 'report') { S.ll.reset(); S.llOwner = null; }
    try { const q = new URLSearchParams(location.search); q.set('step', d.id); history.replaceState(history.state, '', location.pathname + '?' + q.toString()); } catch (e) { /* ignore */ }
    if (m.place === 'report') { showView(d.id); S.cur = d; if (!d.started) { d.started = true; paceLog(d, m.title); } await enterOnce(d); emit('step:enter', d.id); return; }
    showView('plan');
    if (m.place === 'input') { showLoadList(false); toggleSection(d, true); }
    if (m.place === 'review') {
      if (!S.generated && d.id !== 'plan') { toast('Generate a plan first'); return; }
      if (d.id === 'plan' && !S.generated) { selectTab(d); S.cur = d; return; }
      if (m.sandbox) d.meterSnap = { ...S.meters };
      S.cur = d; selectTab(d); d.entered = false; await enterOnce(d);
      if (!d.started && !m.action) { d.started = true; paceLog(d, m.tab); setState(d, 'running'); d.startP = (async () => { if (d.view.start) { try { await d.view.start(); } catch (e) { console.warn('[planner] start', d.id, e); } } })(); }
    }
    emit('step:enter', d.id);
  }
  async function act(id) {
    const d = byId(id); if (!d) return;
    const m = meta(d);
    if (m.generate) return generate();
    await open(id);
    if (d.started) return;
    d.started = true; setState(d, 'running'); drawFoot(d); paceLog(d, (d.view && d.view.ownsAction ? m.tab || m.title : m.action) || m.title);
    d.startP = (async () => { await d.mounting; if (d.view && d.view.start) { await wait(150); try { await d.view.start(); } catch (e) { console.warn('[planner] start', d.id, e); } } })();
    await d.startP;
  }

  // ---------------------------------------------------------------- frame wiring
  function newPlan() {
    if (!S.list.some((x) => x.started && !meta(x).done0)) { showView('plan'); return; }
    const q = new URLSearchParams(location.search); q.delete('step'); q.delete('autoplay'); q.delete('paced');
    location.href = location.pathname + (q.toString() ? '?' + q.toString() : '');
  }
  // Demo takes: a mouse-wheel notch glides instead of jumping 40 px, so a steady run of notches reads as one continuous scroll.
  function smoothWheel() {
    const goal = new Map(), at = new Map();
    const scroller = (n, dy) => {
      for (; n && n !== document.documentElement; n = n.parentElement) {
        const oy = getComputedStyle(n).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight && (dy > 0 ? n.scrollTop + n.clientHeight < n.scrollHeight - 1 : n.scrollTop > 0)) return n;
      }
      return null;
    };
    const step = (n) => {
      const to = goal.get(n), now = at.get(n) + (to - at.get(n)) * 0.16;
      if (Math.abs(to - now) < 0.5) { n.scrollTop = to; goal.delete(n); at.delete(n); return; }
      at.set(n, now); n.scrollTop = now;
      requestAnimationFrame(() => step(n));
    };
    addEventListener('wheel', (e) => {
      const dy = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY;
      if (e.ctrlKey || Math.abs(dy) < 20 || Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.target.closest('textarea')) return;
      const n = scroller(e.target, dy); if (!n) return;
      e.preventDefault();
      const moving = goal.has(n);
      if (!moving) at.set(n, n.scrollTop);
      goal.set(n, Math.max(0, Math.min(n.scrollHeight - n.clientHeight, (moving ? goal.get(n) : n.scrollTop) + dy)));
      if (!moving) requestAnimationFrame(() => step(n));
    }, { passive: false });
  }
  function wireFrame() {
    $('pl-bell').innerHTML = icon('bell'); $('pl-sheet-close').innerHTML = icon('close');
    for (const b of document.querySelectorAll('#pl-nav [data-view]')) b.addEventListener('click', () => (b.dataset.view === 'plan' ? showView('plan') : open(b.dataset.view)));
    $('pl-bell').addEventListener('click', () => { const r = byId('instruct'); if (r) { if (!r.started) act(r.id); else open(r.id); } });
    $('pl-generate').addEventListener('click', () => generate());
    $('pl-ll-back').addEventListener('click', () => showLoadList(false));
    $('pl-ll-back').insertAdjacentHTML('afterbegin', icon('back'));
    $('pl-sheet-close').addEventListener('click', () => { const owner = S.list.find((x) => x.id === S.sheetOwner); sheet(owner || null, null); emit('sheet:closed', owner && owner.id); });
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('pl-sheet').hidden) $('pl-sheet-close').click(); });
    if (LIVE) addEventListener('keydown', (e) => { const k = $('pl-key-panel'); if (e.key === 'Escape' && k && !k.hidden) closeKeyPanel(); });
    if (DEMO) smoothWheel();
    $('pl-scrub-range').addEventListener('input', (e) => emit('moves:seek', { at: +e.target.value }));
    S.closedBays = new Set();
    if (LIVE) { wireLive(); return; }
    on('rule:confirmed', (p) => {
      const bay = p && Number(p.bay); if (!Number.isFinite(bay)) return;
      S.closedBays.add(bay);
      try { S.bp.closeBay(bay, `Bay ${String(bay).padStart(2, '0')} · closed`); } catch (e) { /* component missing */ }
      const r = byId('instruct'); if (r) { setStatus(r, `close_bay · ${bay} confirmed`); markDone(r); }
      drawHero();
    });
    on('rule:changed', (p) => { // a new instruction replaces the confirmed one: Instructions is no longer ready
      for (const b of S.closedBays) { try { if (S.bp.openBay) S.bp.openBay(b); } catch (e) { /* ignore */ } }
      S.closedBays.clear();
      const r = byId('instruct'); if (r && r.done) { r.done = false; r.statusText = null; setStatus(r, null); setState(r, 'running'); }
      drawHero();
    });
    on('loadlist:ready', (p) => { const n = Array.isArray(p) ? p.length : p && Array.isArray(p.rows) ? p.rows.length : null; const r = byId('intake'); if (r && n) setStatus(r, `${n} boxes read (demo voyage)`); });
    on('log', (e) => { if (S.generated && e && e.who === 'Full checker' && /passed/i.test(e.text || '') && S.cur && S.cur.id === 'plan') stamp(e.text.split(' · ')[0]); });
  }
  function wireLive() {
    const openAll = () => { for (const b of S.closedBays) { try { if (S.bp.openBay) S.bp.openBay(b); } catch (e) { /* ignore */ } } S.closedBays.clear(); };
    on('rule:confirmed', (p) => { // p: the confirmed live reading view, or null for "Plan with no instruction"
      openAll();
      S.state.instruction = p ? { mode: 'confirmed', reading: p } : { mode: 'none' };
      const c = p && p.answer && p.answer.constraint;
      if (c && c.type === 'close_bay' && Number.isFinite(Number(c.bay))) {
        const bay = Number(c.bay); S.closedBays.add(bay);
        try { S.bp.closeBay(bay, `Bay ${String(bay).padStart(2, '0')} · closed`); } catch (e) { /* component missing */ }
      }
      const r = byId('instruct'); if (r) { setStatus(r, p ? (p.rule_text || 'Reading confirmed') : 'No instruction: the reader had nothing to do'); markDone(r); }
      const size = L.call && L.call.case && L.call.case.size_class;
      S.state.closureHint = !!(c && c.type === 'close_bay' && (size === 'tiny' || size === 'small'));
      drawHero();
    });
    on('rule:changed', () => {
      openAll(); S.state.instruction = null;
      const r = byId('instruct'); if (r && r.done) { r.done = false; r.statusText = null; setStatus(r, null); setState(r, 'running'); }
      drawHero();
    });
    on('world:ready', () => { const r = S.state.instruction && S.state.instruction.reading; const c = r && r.answer && r.answer.constraint; if (c && c.type === 'close_bay') { try { S.bp.closeBay(Number(c.bay), `Bay ${String(c.bay).padStart(2, '0')} · closed`); } catch (e) { /* ignore */ } S.closedBays.add(Number(c.bay)); } });
  }
  async function loadWorld() {
    const [ship, boxes, before] = await Promise.all([data('ship').catch(() => null), data('boxes').catch(() => null), data('plan-before').catch(() => null)]);
    S.ship = ship; S.boxes = boxes; S.planBefore = before;
    if (ship && ship.ship) { $('pl-call-sub').textContent = [String(ship.ship.words || '').split(' · ')[0], 'simulated port'].filter(Boolean).join(' · '); }
    S.ll = window.PlannerLoadList ? window.PlannerLoadList.create($('pl-loadlist'), { onSelect: (id) => { const s = slotOf(id); try { if (s) S.bp.focusBay(s.bay); S.bp.highlight([id], 'selected'); } catch (e) { /* ignore */ } } }) : null;
    if (S.ll && boxes) S.ll.setData(boxes);
    if (window.BayPlan && ship && boxes) {
      try { S.bp = window.BayPlan.create($('pl-bay'), { ship, boxes: boxes.boxes, pods: boxes.pods }); } catch (e) { console.warn('[planner] BayPlan.create', e); S.bp = null; }
      if (S.bp && S.bp.on) S.bp.on('select', (id) => { if (S.ll) { showLoadList(true); S.ll.focus(id); } });
    }
    if (!S.bp) { S.bp = NOOP_BAY; $('pl-bay').innerHTML = '<div class="pl-bay-empty">The bay plan loads with the ship data</div>'; }
    drawLegend();
    // Before Generate plan: the empty section (fixed cargo only), with the load list from the stored plan's boxes.
    // Before Generate plan: the arrival state, as a larger profile only (the bay sections appear with the plan).
    const arrival = (ship && ship.initial_plan) || null;
    S.plan = arrival;
    try { if (S.bp.preGenerate) S.bp.preGenerate(arrival ? arrival.slots || [] : []); else S.bp.setPlan(arrival || { slots: [] }); } catch (e) { console.warn('[planner] preGenerate', e); }
    if (S.ll && before) { S.ll.setPlan(before); }
    metersFromPlan(arrival && arrival.meters ? arrival : null);
    on('plan:set', (p) => { if (p && p.meters) metersFromPlan(p); });
    const doc = S.stateDoc;
    if (doc && doc.rule_counts) setMeter('rules', { value: `${doc.rule_counts.active} active`, limit: `${doc.rule_counts.off} off · checked per plan`, state: 'empty' });
    setMeter('moves', { value: null, limit: 'after Generate plan', state: 'empty' });
  }

  // ---------------------------------------------------------------- autoplay (hands-free) and the paced take
  const film = () => (performance.now() - S.film0) / 1000;
  async function untilFilm(t) { while (film() < t) await sleep(Math.min(200, Math.max(16, (t - film()) * 1000))); }
  const firstSeg = (id) => (window.StudioPacing ? window.StudioPacing.segments.find((x) => x.step === id) : null);
  function paceLog(d, beat) {
    if (S.film0 == null || !AUTOPLAY) return;
    const seg = firstSeg(d.id), t = film();
    S.beats = S.beats || []; S.beats.push({ step: d.id, beat, t: +t.toFixed(2), target: seg ? seg.start_s : null });
    if (PACED) console.log(`[planner pace] ${d.id} · "${beat}" at film ${t.toFixed(2)} s` + (seg ? ` · target ${seg.start_s} s · drift ${(t - seg.start_s >= 0 ? '+' : '')}${(t - seg.start_s).toFixed(2)} s` : ''));
  }
  function runAuto(d, deadline) {
    const pctx = Object.create(d.ctx);
    if (deadline != null) pctx.wait = (ms) => { const left = deadline - film(); const want = Math.max(0, +ms || 0); return sleep(left <= 0 ? Math.min(want, 250) : Math.min(want, Math.max(250, left * 1000 * 0.4))); };
    d.autoP = (async () => { await d.mounting; if (d.startP) await d.startP; if (d.view && d.view.autoplay) { try { await d.view.autoplay(pctx); } catch (e) { console.warn('[planner] autoplay', d.id, e); } } })();
    return d.autoP;
  }
  const centerOf = (n) => { const r = n.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; };
  /* One beat, the way a person does it: the control on screen for that stage. */
  async function beat(d) {
    const m = meta(d);
    if (m.place === 'report') { await click(document.querySelector(`#pl-nav [data-view="${d.id}"]`)); return; }
    if (m.auto) { ringRadio(d); await sleep(500); await click($('pl-bell')); return; }
    if (m.generate) { await click($('pl-generate')); return; }
    if (m.place === 'review') { await click(d.tabBtn); for (let i = 0; i < 30 && S.cur !== d; i++) await sleep(50); if (m.action) { await sleep(500); const b = d.foot.querySelector(`[data-action="${d.id}"]`); if (b) await click(b); else await act(d.id); } return; }
    // an input section: open it, then its action
    if (d.sec && !d.sec.classList.contains('open')) await click(d.sec.querySelector('.pl-sec-head'));
    const b = d.foot && d.foot.querySelector(`[data-action="${d.id}"]`);
    if (b) await click(b); else await act(d.id);
  }
  /* Segment 9: back to the generated plan: close any sheet, open Checks, hover the result strip and the bay sections. */
  async function restOnPlan(seg) {
    if (!$('pl-sheet').hidden) await click($('pl-sheet-close'));
    const checks = byId('plan');
    if (checks && checks.tabBtn) await click(checks.tabBtn);
    logBeat('checks', 'Checks tab (back on the plan)', seg.start_s);
    for (const n of [$('pl-result'), ...[...document.querySelectorAll('#pl-bay svg')].slice(0, 1)].filter(Boolean)) {
      const r = n.getBoundingClientRect(); await glide(r.left + r.width * 0.3, r.top + Math.min(r.height * 0.5, 40), 900); await sleep(1600);
      await glide(r.left + r.width * 0.7, r.top + Math.min(r.height * 0.5, 40), 900); await sleep(1600);
    }
    const bay = $('pl-bay').getBoundingClientRect();
    await glide(bay.left + bay.width * 0.35, bay.top + bay.height * 0.65, 900); await sleep(1800);
    await glide(bay.left + bay.width * 0.7, bay.top + bay.height * 0.6, 900);
  }
  /* Segment 10: rest on the full plan to the end, the cursor parked off the content. */
  async function quietDrift(seg, T) {
    logBeat('rest', 'Rest on the full plan', seg.start_s);
    const bay = $('pl-bay').getBoundingClientRect();
    await glide(bay.left + bay.width * 0.5, bay.top + bay.height * 0.3, 900); await sleep(3000);
    await glide(bay.right - 40, bay.bottom - 30, 900);
  }
  function logBeat(step, label, target) {
    const t = film(); S.beats = S.beats || []; S.beats.push({ step, beat: label, t: +t.toFixed(2), target });
    console.log(`[planner pace] ${step} · "${label}" at film ${t.toFixed(2)} s · target ${target} s · drift ${(t - target >= 0 ? '+' : '')}${(t - target).toFixed(2)} s`);
  }
  const ORDER = ['intake', 'instruct', 'plan', 'stress', 'copilot', 'handover', 'evidence', 'impact'];
  async function autoplay() {
    if (S.autoplaying) return;
    S.autoplaying = true;
    await wait(1600);
    for (const id of ORDER) {
      const d = byId(id); if (!d) continue;
      await beat(d);
      if (id === 'impact') { await wait(4000); break; }
      if (meta(d).generate) await (d.startP || null);
      runAuto(d); await d.autoP;
      if (!d.done && meta(d).place !== 'report') markDone(d);
      await wait(900);
    }
    await sleep(600); showCursor(false); S.autoplaying = false; emit('autoplay:end');
  }
  async function paced() {
    const T = window.StudioPacing, segs = T.segments;
    S.autoplaying = true;
    cur.x = innerWidth * 0.5; cur.y = innerHeight * 0.55; showCursor(true);
    // 1 · the app opens on the empty plan: drift over the hero, the inputs, the review lock
    await untilFilm(segs[0].start_s + Math.min(6, segs[0].target_s * 0.4));
    for (const n of [$('pl-hero-chips'), $('pl-generate'), $('pl-sections'), $('pl-review-lock')].filter(Boolean)) { const p = centerOf(n); await glide(p.x, p.y, 850); await sleep(700); }
    console.log(`[planner pace] home held to film ${film().toFixed(2)} s`);
    let prev = null;
    for (let k = 1; k < segs.length; k++) {
      const seg = segs[k], d = byId(seg.step);
      if (!d) continue;
      if (d === prev) { await untilFilm(seg.start_s); console.log(`[planner pace] segment ${seg.id} (${seg.title}) at film ${film().toFixed(2)} s · target ${seg.start_s} s (same stage, continues)`); continue; }
      const m = meta(d);
      if (prev && prev.autoP) await Promise.race([prev.autoP, untilFilm(seg.start_s - 1.4)]);
      if (prev && !prev.done && meta(prev).place !== 'report') markDone(prev);
      if (m.place === 'report') { // demo: reports stay out of the UI; segments 9-10 rest on the generated plan (numbers are overlaid in the edit)
        await untilFilm(seg.start_s - (d.id === 'evidence' ? 2.4 : 0));
        if (d.id === 'evidence') await restOnPlan(seg);
        else await quietDrift(seg, T);
        prev = d; d.autoP = Promise.resolve(); continue;
      }
      await untilFilm(seg.start_s - (m.auto ? 1.5 : m.place === 'review' && m.action ? 2.3 : m.place === 'input' ? 1.8 : 0.9));
      await beat(d);
      {
        let j = k + 1; while (j < segs.length && segs[j].step === d.id) j++;
        const last = segs[j - 1], next = segs[j];
        runAuto(d, (next ? next.start_s : T.total_s) - (last.hold_s || 2) - 1.4);
      }
      prev = d;
    }
    await untilFilm(T.total_s);
    console.log(`[planner pace] end at film ${film().toFixed(2)} s · target ${T.total_s} s`);
    showCursor(false); S.autoplaying = false; emit('autoplay:end');
  }

  // ---------------------------------------------------------------- boot
  const LIVE_STAGES = [['voyage', 0], ['intake', 10], ['instruct', 20], ['plan', 30], ['stress', 40], ['copilot', 50], ['handover', 60]];
  function missingLive(id, order) {
    return { id, order, mode: 'live', features: [], who: [], mount(body) {
      body.innerHTML = `<p class="pl-live-missing">${esc(meta({ id }).title)} is not in the Functional app yet; nothing is replayed in its place.</p>`;
      return {};
    } };
  }
  async function bootLive() {
    document.documentElement.classList.add('pl-live');
    for (const link of document.querySelectorAll('link[data-live-css]')) link.media = 'all';
    for (const [id, order] of LIVE_STAGES) if (!S.defs.some((d) => d.id === id)) S.defs.push(missingLive(id, order));
    S.list = S.defs.filter((d) => meta(d).place !== 'report').sort((a, b) => (a.order || 0) - (b.order || 0));
    wireFrame(); buildMeters(); drawLiveHeader();
    try { L.status = await fx.get('status'); } catch (e) { L.off = e.message; }
    drawLiveHeader();
    S.ll = window.PlannerLoadList ? window.PlannerLoadList.create($('pl-loadlist'), { onSelect: (id) => { const s = slotOf(id); try { if (s) S.bp.focusBay(s.bay); S.bp.highlight([id], 'selected'); } catch (e) { /* ignore */ } } }) : null;
    emptyBay(L.off || 'The bay plan appears when the call is created: a fresh synthetic ship and load list from a new seed');
    drawLegend();
    metersFromPlan(null);
    setMeter('moves', { value: null, limit: 'after Generate plan', state: 'empty' });
    setMeter('rules', { value: null, limit: 'from the plan\'s receipt', state: 'empty' });
    S.list.forEach(buildStage);
    S.list.forEach((d) => { mountStep(d); });
    for (const d of S.list) if (meta(d).place === 'input') enterOnce(d);
    let id = P.get('call');
    if (!CALL_ID.test(id || '')) { try { id = localStorage.getItem(CALL_KEY); } catch (e) { id = null; } }
    if (CALL_ID.test(id || '') && !L.off) {
      try { await fx.get(`calls/${encodeURIComponent(id)}`); } catch (e) { forgetCall(); toast(e.message); }
    }
    const v = L.call;
    toggleSection(byId('voyage'), !v || !v.case);
    toggleSection(byId('intake'), !!(v && v.case && !['ready', 'planning', 'done'].includes(v.state)));
    toggleSection(byId('instruct'), !!(v && v.state === 'ready'));
    if (!S.generated) { selectTab(byId('plan')); $('pl-panels').hidden = true; } // a reloaded call with a run is already on its plan
    drawHero(); drawInputsSum();
    schedulePoll();
    maybeFirstRunKeyPanel();
  }
  async function boot() {
    if (LIVE) return bootLive();
    S.list = S.defs.slice().sort((a, b) => (a.order || 0) - (b.order || 0));
    wireFrame(); buildMeters();
    let doc = null;
    try { doc = await Promise.race([api('state'), sleep(2500).then(() => null)]); } catch (e) { console.warn('[planner] /api/v6/state', e.message); }
    S.stateDoc = doc;
    S.list.forEach((d) => { if ((d.features || []).some((f) => doc && doc.features && doc.features[f] && doc.features[f].available === false)) d.gap = true; });
    S.list = S.list.filter((d) => !d.gap);
    await loadWorld();
    S.list.forEach(buildStage);
    S.list.forEach((d) => { mountStep(d); });
    // inputs are live from the start: mount and enter them; Ship & call and Cargo open
    for (const d of S.list) if (meta(d).place === 'input') enterOnce(d);
    toggleSection(byId('voyage'), false); toggleSection(byId('intake'), true);
    selectTab(byId('plan')); $('pl-panels').hidden = true; drawHero(); drawInputsSum();
    const want = byId(P.get('step'));
    if (want && want.id === 'voyage') toggleSection(want, true);
    if (want && want.id !== 'voyage') {
      if (meta(want).place === 'review') { // a deep link into review: the stored inputs, then the plan
        for (const id of ['intake', 'instruct']) { const x = byId(id); if (x) { x.done = true; x.started = true; setState(x, 'done'); setStatus(x, meta(x).status); toggleSection(x, false); } }
        if (want.id === 'plan') await generate(); else { S.generated = true; $('pl-hero').hidden = true; $('pl-result').hidden = false; planReady(); const pd = byId('plan'); if (pd) { await enterOnce(pd); } await open(want.id); }
      } else await open(want.id);
    }
    S.film0 = performance.now() - (window.StudioPacing ? window.StudioPacing.open_s : 0) * 1000;
    if (PACED) paced(); else if (AUTOPLAY) autoplay();
  }

  window.Planner = {
    stage(def) {
      if (!def || !def.id || typeof def.mount !== 'function') { console.warn('[planner] bad stage definition', def); return; }
      if ((def.mode === 'live') !== LIVE) return; // demo modules mount in the Demo app, live ones in the Functional app
      if (S.defs.some((d) => d.id === def.id)) return;
      S.defs.push({ features: [], ...def });
    },
    go: (id) => (id === 'voyage' ? newPlan() : open(id)), open, act, generate, autoplay, newPlan, showView,
    toggleSidebar() { const l = $('pl-left'); const hide = !l.hidden; l.hidden = hide; $('pl-app').style.gridTemplateColumns = hide ? '0 minmax(0, 1fr) var(--pl-right)' : ''; return !hide; },
    get current() { return S.cur ? S.cur.id : null; },
    get generated() { return !!S.generated; },
    get beats() { return (S.beats || []).slice(); },
    get steps() { return visible().map((d) => ({ id: d.id, title: meta(d).title || d.title, done: !!d.done, started: !!d.started, mounted: !!d.mounted, state: d.state || null, status: d.statusText || null })); },
    ctx: (id) => { const d = S.list.find((x) => x.id === id); return d ? d.ctx || (d.ctx = makeCtx(d)) : null; },
    view: (id) => { const d = S.list.find((x) => x.id === id); return d ? d.view || null : null; },
    get log() { return S.log.slice(); },
    get autoplaying() { return !!S.autoplaying; },
    get filmTime() { return S.film0 == null ? null : film(); },
    get bay() { return S.bp; }, get loadlist() { return S.ll; },
    meters, copilotQuestion: null,
    icon, chip, esc, toast, wait, click, drag, demo: DEMO, speed: SPEED, live: LIVE,
    get call() { return L.call; },
  };
  window.Studio = window.Studio || { step: (def) => window.Planner.stage(def), get copilotQuestion() { return window.Planner.copilotQuestion; }, set copilotQuestion(v) { window.Planner.copilotQuestion = v; } };
  let booted = false;
  const go1 = () => { if (booted) return; booted = true; boot().catch((e) => console.error('[planner] boot', e)); };
  if (document.readyState === 'complete') setTimeout(go1, 0);
  else { document.addEventListener('DOMContentLoaded', go1); window.addEventListener('load', go1); }
})();
