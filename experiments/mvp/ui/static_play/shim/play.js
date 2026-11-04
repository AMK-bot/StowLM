/* StowLM 'Play it yourself': a recorded run of the Functional app (/planner?live=1), answered from recordings. Loaded before
 * every other script of the page. Nothing here reaches the network: it reads this folder's own files only.
 *
 * The recording (rec/index.json, build.py) is one run of the real app: every answer the server gave, with its wall-clock time,
 * and the planner's actions in order (Create call, read the papers, the spoken instruction, read, confirm, Generate plan).
 *   actions   a POST is answered only when it is the recorded run's next action with the same inputs; anything else runs
 *             nothing and says this is a recorded run, with the install pointer
 *   answers   a GET gets what the server answered at the same moment of the recorded run
 *   clock     the page's clock (Date.now) follows the recorded run: it jumps to each action's recorded time when the visitor
 *             presses it, and long stages replay faster than they ran (the page says by how much). Every time shown is the
 *             one measured in the recorded run.
 *   inputs    the form shows the recorded inputs; editing them is refused with the same message. No key is accepted: the key
 *             chip and panel say a recorded run needs none. The spoken clip's audio isn't included (D309): a silent
 *             placeholder carries the clip's name through the app's own upload step, and its recorded transcript replays.
 * Each page load starts the recorded run from the beginning.
 */
(function () {
  'use strict';
  const here = document.currentScript && document.currentScript.src ? document.currentScript.src : location.href;
  const ROOT = new URL('./', here);
  const CALL_KEY = 'stowlm.functional.call';

  // ---------------------------------------------------------------- the Functional app's live mode, fresh on every load
  const P = new URLSearchParams(location.search);
  let rewrite = false;
  if (P.get('live') !== '1') { P.set('live', '1'); rewrite = true; }
  for (const k of ['call', 'demo', 'autoplay', 'paced', 'step']) if (P.has(k)) { P.delete(k); rewrite = true; }
  if (rewrite) history.replaceState(null, '', location.pathname + '?' + P.toString() + location.hash);
  try { localStorage.removeItem(CALL_KEY); localStorage.setItem('stowlm.functional.keyPanelSeen', '1'); } catch (e) { /* storage off */ }

  const own = window.fetch.bind(window); // used only for this folder's own files
  const local = (rel) => own(new URL(rel, ROOT).href, { credentials: 'same-origin' });
  const perf = () => performance.now();
  const realNow = Date.now.bind(Date);
  const S = { I: null, anchor: -1, real: 0, load: perf(), served: [], refused: [], misses: [], self: false, audioReady: -1, notes: 0 };
  window.__play = S;

  // ---------------------------------------------------------------- the recorded run's clock
  function vt() { // ms since the recording began, at this moment of the replay
    const I = S.I; if (!I) return null;
    const next = S.anchor + 1 < I.anchors.length ? I.anchors[S.anchor + 1].t : Infinity;
    const t = S.anchor < 0 ? perf() - S.load : I.anchors[S.anchor].t + (perf() - S.real) * I.anchors[S.anchor].speed;
    return Math.min(t, next - 1);
  }
  Date.now = function () { const t = vt(); return t == null ? realNow() : Math.round(S.I.t0 + t); };

  const index = local('rec/index.json').then((r) => { if (!r.ok) throw new Error('recordings missing'); return r.json(); })
    .then((I) => { S.I = I; return I; });
  const bodies = new Map();
  function text(ref) {
    if (!bodies.has(ref)) bodies.set(ref, (async () => {
      const r = await local('rec/' + ref);
      if (!r.ok) throw new Error('recording body missing');
      const raw = new Uint8Array(await r.arrayBuffer());
      if (raw[0] === 0x1f && raw[1] === 0x8b && typeof DecompressionStream === 'function') {
        return new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
      }
      return new TextDecoder().decode(raw); // a host that already decoded it
    })());
    return bodies.get(ref);
  }

  // ---------------------------------------------------------------- keys (build.py key_of builds the same)
  const canon = (v) => (Array.isArray(v) ? v.map(canon) : v && typeof v === 'object'
    ? Object.keys(v).sort().reduce((o, k) => { o[k] = canon(v[k]); return o; }, {}) : v);
  function bodyKey(raw, drop) {
    if (raw == null || raw === '') return '';
    try {
      const v = JSON.parse(raw);
      if (v && typeof v === 'object' && !Array.isArray(v) && drop) delete v[drop];
      return JSON.stringify(canon(v));
    } catch (e) { return String(raw); }
  }
  function keyOf(method, url, raw) {
    const path = decodeURIComponent('/' + url.pathname.slice(ROOT.pathname.length));
    const rows = [...url.searchParams].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
    let key = method + ' ' + path;
    if (rows.length) key += '?' + rows.map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
    const b = method === 'GET' ? '' : bodyKey(raw, /\/audio$/.test(path) ? 'data_b64' : null);
    return { key: key + (b ? ' ' + b : ''), path };
  }

  // ---------------------------------------------------------------- what the visitor is told
  const MSG_RECORDED = 'This is a recorded run: its inputs are fixed to the recorded track, so this runs nothing.';
  const MSG_INSTALL = 'To try your own inputs, install the app and run the Functional app.';
  const MSG_KEY = 'No key needed: this recorded run replays answers recorded from the Functional app, so it makes no calls and accepts no key.';
  const MSG_KEY_INSTALL = 'To use your own Meta Model API key, install the app and run the Functional app.';
  let note = null, noteTimer = 0;
  function installLink(words) {
    const src = document.getElementById('play-install');
    const a = document.createElement('a');
    a.href = src ? src.href : new URL('README.md', ROOT).href;
    a.target = '_blank'; a.rel = 'noopener';
    a.textContent = words || 'Install steps';
    return a;
  }
  function tell(kind, what) {
    const [head, tail] = kind === 'key' ? [MSG_KEY, MSG_KEY_INSTALL] : [MSG_RECORDED, MSG_INSTALL];
    console.info(`[playback] ${head} ${what || ''}`.trim());
    if (kind !== 'key') S.refused.push(what || kind);
    const show = () => {
      if (!note) { note = document.createElement('div'); note.id = 'play-note'; note.setAttribute('role', 'status'); document.body.appendChild(note); }
      note.innerHTML = '';
      const b = document.createElement('b'); b.textContent = head;
      const p = document.createElement('span'); p.textContent = tail + ' ';
      p.appendChild(installLink('Install steps'));
      const x = document.createElement('button'); x.type = 'button'; x.className = 'play-note-close'; x.setAttribute('aria-label', 'Close'); x.textContent = '×';
      x.addEventListener('click', () => { note.hidden = true; });
      note.append(b, p, x); note.dataset.kind = kind; note.hidden = false; S.notes++;
      clearTimeout(noteTimer); noteTimer = setTimeout(() => { note.hidden = true; }, 12000);
    };
    if (document.body) show(); else document.addEventListener('DOMContentLoaded', show);
  }
  const reply = (status, body, type) => new Response(body, { status, headers: { 'Content-Type': type || 'application/json; charset=utf-8' } });
  const refusal = () => JSON.stringify({ error: MSG_RECORDED + ' ' + MSG_INSTALL, recorded_run: true });

  // ---------------------------------------------------------------- answers
  async function serve(row) { return reply(row[1], await text(row[3]), row[2]); }
  async function answer(method, url, raw) {
    const I = await index;
    const { key, path } = keyOf(method, url, raw);
    if (method === 'GET' && I.files.includes(path)) return own(url.href); // a file of this copy (papers, package, outcome)
    if (method !== 'GET') {
      const next = I.anchors[S.anchor + 1];
      if (next && key === next.key) {
        S.anchor += 1; S.real = perf(); S.served.push(key);
        return serve(next.post);
      }
      tell('recorded', method + ' ' + path);
      return reply(409, refusal());
    }
    const seq = I.entries[key];
    if (!seq) { S.misses.push(key); tell('recorded', 'GET ' + path); return reply(404, refusal()); }
    const t = vt();
    let row = seq[0];
    for (const r of seq) { if (r[0] <= t) row = r; else break; }
    S.served.push(key);
    return serve(row);
  }

  window.fetch = async function (input, init) {
    const req = typeof Request !== 'undefined' && input instanceof Request ? input : null;
    const method = String((init && init.method) || (req && req.method) || 'GET').toUpperCase();
    const url = new URL(req ? req.url : String(input), location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith(ROOT.pathname)) { tell('recorded', method + ' ' + url.pathname); return reply(409, refusal()); }
    const rel = url.pathname.slice(ROOT.pathname.length);
    if (!rel.startsWith('api/')) return own(url.href, init); // a file of this copy
    let raw = init && init.body != null ? init.body : null;
    if (raw == null && req && method !== 'GET') raw = await req.clone().text();
    try { return await answer(method, url, raw == null ? '' : String(raw)); } catch (e) { tell('recorded', method + ' ' + url.pathname + ' (' + (e.message || e) + ')'); return reply(409, refusal()); }
  };

  // ---------------------------------------------------------------- inputs: fixed to the recorded track
  const LOCKED = '#pl-sections, #pl-key-panel';
  const inLocked = (t) => !!(t && t.closest && t.closest(LOCKED));
  const asSelf = (fn) => { S.self = true; try { return fn(); } finally { S.self = false; } };
  const remember = (e) => { const t = e.target; if (t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName) && !('playPrev' in t.dataset)) t.dataset.playPrev = t.type === 'checkbox' || t.type === 'radio' ? String(t.checked) : t.value; };
  for (const type of ['focusin', 'pointerdown', 'mousedown', 'keydown']) document.addEventListener(type, remember, true);
  function restore(t) {
    if (t.type === 'checkbox' || t.type === 'radio') { t.checked = t.dataset.playPrev === 'true'; return; }
    if ('playPrev' in t.dataset) { t.value = t.dataset.playPrev; return; }
    if (t.tagName === 'SELECT') { const o = [...t.options].find((x) => x.defaultSelected); if (o) t.value = o.value; return; }
    t.value = t.defaultValue;
  }
  document.addEventListener('beforeinput', (e) => {
    if (S.self || !inLocked(e.target)) return;
    e.preventDefault(); e.stopImmediatePropagation(); tell('recorded', 'typing in ' + (e.target.dataset.f || e.target.id || e.target.tagName.toLowerCase()));
  }, true);
  for (const type of ['input', 'change']) {
    document.addEventListener(type, (e) => {
      const t = e.target;
      if (S.self || !inLocked(t) || !/^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return;
      e.stopImmediatePropagation(); restore(t);
      if (type === 'change' || t.type !== 'range') tell('recorded', 'changing ' + (t.dataset.f || t.dataset.liveField || t.id || t.name || t.tagName.toLowerCase()));
    }, true);
  }
  document.addEventListener('drop', (e) => { if (inLocked(e.target) || (e.target && e.target.closest && e.target.closest('#pl-app'))) { e.preventDefault(); e.stopImmediatePropagation(); tell('recorded', 'a dropped file'); } }, true);
  document.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null; if (!t || S.self) return;
    const stop = (kind, what) => { e.preventDefault(); e.stopImmediatePropagation(); tell(kind, what); };
    if (t.closest('.pl-key-chip, [data-action="open-key-panel"], [data-action="raise-spend-cap"]')) return stop('key', 'the API key panel');
    if (t.closest('[data-do="seed"]')) return stop('recorded', 'a new seed');
    if (t.closest('[data-action="record"]')) return stop('recorded', 'recording in this browser');
    if (t.closest('[data-action="no-instruction"], [data-do="change-instruction"]')) return stop('recorded', 'the instruction choice');
    const next = S.I && S.I.anchors[S.anchor + 1];
    if (t.closest('[data-action="submit-answers"], [data-action="use-generator-records"]') && !(next && next.what === 'answers')) return stop('recorded', 'answering the intake questions');
    if (t.closest('[data-action="retry-failed-bookings"]') && !(next && next.what === 'papers' && next.body && next.body.use_generated_papers)) return stop('recorded', 'a retry');
    const prof = t.closest('[data-profile]'); // the hero's profile switch and the Checks panel's
    if (prof && S.I && prof.dataset.profile !== S.I.track.profile) return stop('recorded', 'the engine profile');
    if (inLocked(t) && t.matches('input[type="file"], input[type="checkbox"], input[type="radio"]')) return stop('recorded', t.type === 'file' ? 'a file upload' : 'changing ' + (t.dataset.liveField || t.id || 'a choice'));
  }, true);

  // The recorded entries in 1 Ship & call, as the planner typed them, and the recorded clip in 3 Instructions.
  function fillForm(form) {
    const panel = document.querySelector('.pl-sec[data-stage="voyage"] .pl-sec-panel');
    if (!panel || panel.querySelector('.pl-call-card')) return true; // the call exists: nothing to fill
    let done = true;
    for (const name of ['seed', 'ship', 'size_class', 'fill_pct', 'generator', 'port']) { // the seed first: the selects redraw its hint
      const el = panel.querySelector(`[data-f="${name}"]`);
      if (!el) return false;
      const want = form[name] == null ? '' : String(form[name]);
      if (el.value === want) continue;
      done = false;
      asSelf(() => {
        el.value = want;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        if (el.tagName === 'SELECT') el.dispatchEvent(new Event('change', { bubbles: true }));
      });
      return false; // the form re-renders after each entry: one per tick
    }
    return done;
  }
  function silentWav() { // a few silent samples, only so the app's own upload step runs; the bytes never key or leave the page
    const n = 1600, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const w = (at, s) => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)); };
    w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, 1, true); v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    w(36, 'data'); v.setUint32(40, n * 2, true);
    return new Uint8Array(buf);
  }
  function audioNote(I) {
    const label = document.querySelector('[data-live-audio-label]');
    if (!label || document.getElementById('play-audio-note')) return;
    const p = document.createElement('p'); p.id = 'play-audio-note'; p.className = 'pl-instruct-meta';
    p.textContent = `Audio not included in this copy. The recorded clip ${I.track.audio_name} (macOS text-to-speech, generated voice) said: "${I.track.spoken}". Transcribe replays what Muse Voice Transcribe heard.`;
    label.insertAdjacentElement('afterend', p);
  }
  function loadClip(I) {
    const input = document.querySelector('[data-live-audio]');
    if (!input || input.disabled) return false;
    const opt = document.querySelector('[data-live-generated-voice-option]'), box = document.querySelector('[data-live-generated-voice]');
    const has = input.files && input.files[0] && input.files[0].name === I.track.audio_name;
    if (!has) {
      const dt = new DataTransfer();
      dt.items.add(new File([silentWav()], I.track.audio_name, { type: 'audio/wav' }));
      asSelf(() => { input.files = dt.files; input.dispatchEvent(new Event('change', { bubbles: true })); });
      return false;
    }
    if (opt && !opt.hidden && box && !box.checked) { asSelf(() => { box.checked = true; box.dispatchEvent(new Event('change', { bubbles: true })); }); }
    audioNote(I);
    return !!(box && box.checked);
  }
  function tick() {
    const I = S.I; if (!I) return;
    if (S.anchor < 0) fillForm(I.track.form);
    const next = I.anchors[S.anchor + 1];
    if (next && next.what === 'audio' && S.audioReady !== S.anchor) { if (loadClip(I)) S.audioReady = S.anchor; }
    if (S.audioReady >= 0) audioNote(I);
    if (next && next.what === 'papers' && next.body) { // which choice the recorded planner made here
      const [sel, words] = next.body.use_generated_load_list
        ? ['.pl-live-intake-questions [data-action="use-generated-load-list"]', 'In the recorded run, the planner chose “Use the generated load list, not read by a model” here.']
        : next.body.use_generated_papers ? ['[data-action="retry-failed-bookings"]', 'In the recorded run, the planner pressed “Retry the failed bookings” here.'] : [null, null];
      const btn = sel && document.querySelector(sel);
      if (btn && !document.getElementById('play-guide')) {
        const g = document.createElement('p'); g.id = 'play-guide'; g.className = 'pl-intake-meta'; g.textContent = words;
        btn.insertAdjacentElement('beforebegin', g);
      }
    }
    const prev = I.anchors[S.anchor];
    let edit = next && next.what === 'read' && next.body && next.body.origin === 'planner_edit' && prev && prev.what === 'read';
    if (edit) { // the correction follows the reader's refusal, after the visitor has had a moment to see it
      const card = document.querySelector('[data-live-reading-card]');
      if (!card || card.querySelector('[data-action="confirm"]')) { S.refusalSeen = 0; edit = false; }
      else if (!S.refusalSeen) { S.refusalSeen = perf(); edit = false; }
      else if (perf() - S.refusalSeen < 3500) edit = false;
    }
    if (next && next.what === 'read' && next.body && (next.body.origin === 'typed' || edit)) { // the instruction as the planner typed or corrected it
      const box = document.querySelector('[data-live-sentence]');
      if (box && !box.disabled && box.value !== next.body.sentence) {
        const heard = box.value;
        asSelf(() => { box.value = next.body.sentence; box.dispatchEvent(new Event('input', { bubbles: true })); });
        if (edit && !document.getElementById('play-edit-note')) {
          const g = document.createElement('p'); g.id = 'play-edit-note'; g.className = 'pl-instruct-meta';
          g.textContent = `In the recorded run, the reader refused the transcript as heard ("${heard}"), so the planner corrected it here and read it again.`;
          box.insertAdjacentElement('afterend', g);
        }
      }
    }
    // the key panel never opens here (its first-run prompt included); the key chip says a recorded run needs no key
    const panel = document.getElementById('pl-key-panel');
    if (panel && !panel.hidden) { panel.hidden = true; panel.innerHTML = ''; tell('key', 'the API key panel'); }
    const chip = document.querySelector('.pl-key-chip');
    const title = 'This recorded run makes no calls and accepts no key.';
    if (chip && chip.title !== title) chip.title = title;
    // the replay speed while a long stage replays
    const pill = document.getElementById('play-speed');
    if (pill) {
      const a = I.anchors[S.anchor], t = vt();
      const fast = a && a.speed > 1 && t < a.t + a.busy_s * 1000;
      pill.hidden = !fast;
      if (fast) pill.textContent = `Replaying this stage at ${a.speed >= 10 ? Math.round(a.speed) : a.speed.toFixed(1)}× its recorded pace · times shown are the measured ones`;
    }
  }
  function boot() {
    const banner = document.getElementById('play-banner');
    if (banner && !document.getElementById('play-speed')) { const s = document.createElement('span'); s.id = 'play-speed'; s.hidden = true; banner.appendChild(s); }
    setInterval(tick, 250);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
