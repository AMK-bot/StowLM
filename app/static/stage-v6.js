/* StowLM demo v6: one flow on the stage page (task 2.2). /stage?v6=1, launched with `python -m app --v6`.
 *
 *   mess in -> radio -> reading confirmed -> ship replans -> x-ray -> break it -> copilot -> package
 *
 * The shell owns the rail, the keys and the chapter order. Each chapter lives in its own file and registers itself:
 *
 *   StageV6.chapter({ id, title, feature, ship, duration_s, mount(host, data, ctx) -> { seek(tau, interactive), enter(), leave() } })
 *
 *   feature     key in /api/v6/state; an unavailable feature drops the chapter from the rail. No broken panel, ever.
 *   ship        true: the overlay opens a hole on the live stage (ship, gauges); false: full-frame chapter.
 *   seek(tau)   pure function of tau, in seconds from the chapter's start. The video director calls only this, so a take
 *               repeats exactly. Interactive use runs the same timeline; a chapter holds at a beat by returning from
 *               ctx.waitAt(tau, beat), which stops the clock until ctx.release(beat), so a judge can click where the film
 *               plays through.
 *
 * Numbers and labels come from /api/v6/*: nothing on screen is decided here.
 */
(function () {
  'use strict';
  const P = new URLSearchParams(location.search);
  const ON = P.get('v6') === '1';
  const defs = [];
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const api = async (name) => { const r = await fetch('/api/v6/' + name, { headers: { Accept: 'application/json' } }); if (!r.ok) throw new Error(name + ': ' + r.status); return r.json(); };
  const S = { state: null, list: [], i: -1, cur: null, tau: 0, held: null, released: new Set(), root: null, stage: null, rail: null, label: null, ready: false, last: 0, mounted: new Map(), video: false };

  function showLabel(c) { S.label.textContent = c.labelText || ''; S.label.hidden = !c.labelText; }
  function ctx(c) {
    return {
      esc, api, chapter: c,
      waitAt(tau, beat, at) { if (!S.video && tau >= at && !S.released.has(beat)) { S.held = beat; return true; } return false; },
      release(beat) { S.released.add(beat); if (S.held === beat) S.held = null; S.last = performance.now(); },
      next() { go(S.i + 1); },
      rewind() { S.tau = 0; },
      label(text) { c.labelText = text || ''; if (S.list[S.i] === c) showLabel(c); }, // a chapter mounted ahead of its turn keeps its label until then
      get stage() { return window.__stage; },
      get interactive() { return !S.video; },
    };
  }
  /* Resolves to {host, view, data}, or null when the chapter is a clean gap (feature gone, mount threw, fixture half-written). */
  function mountOf(c) {
    if (S.mounted.has(c.id)) return S.mounted.get(c.id);
    const host = document.createElement('section'); host.className = `v6-ch v6-ch-${c.id}`; host.hidden = true; S.stage.appendChild(host);
    const pr = (async () => {
      const data = c.feature ? await api(c.feature) : null;
      if (data && data.available === false) throw new Error(data.reason || 'feature unavailable');
      const view = c.mount(host, data, ctx(c)) || {};
      return { host, view, data };
    })().catch((e) => { console.warn(`[v6] ${c.id} is a gap:`, e.message || e); host.remove(); return null; });
    S.mounted.set(c.id, pr);
    return pr;
  }
  async function show(i) {
    const c = S.list[i]; if (!c) return null;
    const m = await mountOf(c); if (!m) return null;
    for (const [id, p] of S.mounted) { const x = await p; if (!x) continue; const on = id === c.id; if (x.host.hidden === on) { x.host.hidden = !on; if (!on && x.view.leave) x.view.leave(); } }
    document.body.classList.toggle('v6-ship', !!c.ship);
    if (S.top) S.top.textContent = c.title;
    if (window.__stage && window.__stage.resizeAll) window.__stage.resizeAll();
    S.i = i; S.cur = m; showLabel(c);
    S.rail && [...S.rail.children].forEach((el, n) => { el.classList.toggle('now', n === i); el.classList.toggle('done', n < i); });
    if (m.view.enter) { try { await m.view.enter(); } catch (e) { console.warn(`[v6] ${c.id} is a gap:`, e.message || e); m.view.gap = true; } }
    return m.view.gap ? null : m;
  }
  async function go(i, step = 1) {
    if (i < 0 || i >= S.list.length) return;
    S.tau = 0; S.held = null; S.released = new Set();
    if (!(await show(i))) return go(i + step, step); // a gap is skipped, the rail just moves on
    S.last = performance.now();
  }
  function loop(now) {
    if (S.cur && !S.video) {
      const dt = Math.min(0.1, (now - S.last) / 1000); S.last = now;
      if (!S.held) S.tau += dt;
      if (S.cur.view.seek) { try { S.cur.view.seek(S.tau, true); } catch (e) { console.warn(`[v6] chapter stopped: ${e}`); const dead = S.i; S.cur = null; S.mounted.set(S.list[dead].id, Promise.resolve(null)); go(dead + 1); } }
    }
    requestAnimationFrame(loop);
  }
  function chrome() {
    S.root = document.createElement('div'); S.root.id = 'v6-root';
    S.root.innerHTML = '<div id="v6-top"><div class="v6-brand">Stow<b>LM</b></div><div class="v6-now"></div></div><div id="v6-stage"></div><div id="v6-label" hidden></div><nav id="v6-rail" aria-label="Demo chapters"></nav><div id="v6-scope"></div><div id="v6-build" hidden></div>';
    document.body.appendChild(S.root); document.body.classList.add('v6'); document.body.classList.toggle('v6-video', S.video);
    S.stage = S.root.querySelector('#v6-stage'); S.rail = S.root.querySelector('#v6-rail'); S.label = S.root.querySelector('#v6-label'); S.top = S.root.querySelector('.v6-now');
  }
  async function boot(video) {
    if (S.root) return;
    S.video = !!video; chrome();
    S.state = await api('state');
    if (window.__stage && window.__stage.showRules) window.__stage.showRules(); // the rule panel may have drawn before the receipt's count came
    const build = S.root.querySelector('#v6-build'), b = S.state.build || {}; // the server's own words: frozen and its pipeline hash, or a development build
    build.textContent = b.label || ''; build.hidden = !b.label; build.classList.toggle('dev', b.frozen !== true);
    S.root.querySelector('#v6-scope').textContent = S.state.synthetic + ((S.state.stand_ins || {}).ship ? ` · ${S.state.stand_ins.ship}` : '');
    S.list = defs.filter((c) => !c.feature || (S.state.features[c.feature] || {}).available);
    S.rail.innerHTML = S.list.map((c, n) => `<button type="button" data-i="${n}"><i>${n + 1}</i>${esc(c.title)}</button>`).join('');
    S.rail.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) go(+b.dataset.i); });
    addEventListener('keydown', (e) => {
      if (/INPUT|SELECT|TEXTAREA/.test((e.target && e.target.tagName) || '')) return;
      if (e.key === 'ArrowRight' && e.shiftKey) { e.preventDefault(); e.stopPropagation(); go(S.i + 1); }
      if (e.key === 'ArrowLeft' && e.shiftKey) { e.preventDefault(); e.stopPropagation(); go(S.i - 1, -1); }
    }, true);
    S.ready = true;
    if (video) return;
    S.last = performance.now(); requestAnimationFrame(loop);
    const want = S.list.findIndex((c) => c.id === P.get('chapter'));
    await go(want >= 0 ? want : 0);
  }
  window.StageV6 = {
    chapter(def) { defs.push(def); defs.sort((a, b) => a.order - b.order); },
    boot, esc, api,
    get ready() { return S.ready; },
    get state() { return S.state; },
    list: () => S.list.map((c) => ({ id: c.id, title: c.title, duration_s: c.duration_s })),
    /* Dev and tests: stop the interactive clock at tau, with every wait released. */
    freeze(tau) { S.video = true; if (S.cur && S.cur.view.seek) S.cur.view.seek(tau, false); },
    /* Director: mount a chapter ahead of its shot. Resolves true when it can play, false when it's a gap. */
    async prepare(id) {
      const c = S.list.find((x) => x.id === id); if (!c) return false;
      const m = await mountOf(c); if (!m) return false;
      if (m.view.preload) { try { await m.view.preload(); } catch (e) { console.warn(`[v6] ${id} is a gap:`, e.message || e); return false; } }
      return !m.view.gap;
    },
    /* Director: render chapter `id` at tau seconds. Pure; resolves true when drawn, false if the chapter is a gap. */
    async seek(id, tau) {
      const i = S.list.findIndex((c) => c.id === id); if (i < 0) return false;
      if (S.i !== i) {
        const prior = S.seeking; S.seeking = (async () => { await prior; return S.i === i ? S.cur : show(i); })();
        const shown = await S.seeking; if (!shown) return false;
      }
      if (S.cur && S.cur.view.gap) return false;
      if (S.i !== i || !S.cur || !S.cur.view.seek) return !!S.cur;
      try { S.cur.view.seek(tau, false); } catch (e) { console.warn(`[v6] ${id} stopped: ${e}`); return false; }
      return true;
    },
    hide() {
      for (const [, p] of S.mounted) p.then((x) => { if (x && !x.host.hidden) { x.host.hidden = true; if (x.view.leave) x.view.leave(); } });
      S.i = -1; S.cur = null; S.label.hidden = true; document.body.classList.remove('v6-ship');
    },
  };
  if (ON) { const start = () => setTimeout(() => boot(false).catch((e) => { console.error(e); }), 0); if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start(); }
})();
