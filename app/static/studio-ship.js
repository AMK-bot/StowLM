/* StowLM Studio: the 3D ship mounted in #st-view (CONTRACT.md, shell side).
 *
 * The ship is the stage page's own renderer, unchanged: stage.js boots against the stage DOM ids that studio.html
 * provides (the ship's in #st-view, the rest hidden in #st-stage-dom). stage.js reads its flags from the URL once, when
 * it is evaluated, so this file runs first and hands it the v6 launch flags (?v6=1: no autoplay, retry while the hero
 * plan is withheld for its gates), with none of the studio's own flags (?step, ?speed, ?autoplay mean other things to
 * stage.js). studio.js, evaluated right after stage.js, puts the studio URL back (StudioShip.restoreURL).
 *
 * The four gauges are the checker's measured frame in plain words (StageStory.plainGauges, the v6 replan chapter's own
 * wording): the break-it verdict frame when a step set one, else the nearest measured keyframe at the shown lift. */
(function () {
  'use strict';
  const original = location.pathname + location.search + location.hash;
  try { history.replaceState(history.state, '', location.pathname + '?v6=1&autoplay=0'); } catch (e) { /* file:// or sandbox: stage.js then autoplays, harmless */ }

  let readyResolve;
  const ready = new Promise((res) => { readyResolve = res; });
  function poll() { const st = window.__stage; if (st && st.B && st.scene) readyResolve(st); else setTimeout(poll, 80); }

  // ---------------------------------------------------------------- gauges
  let lastKey = '';
  function frameNow(st) {
    const S = st.S || {};
    if (S.gaugeFrame) return S.gaugeFrame;
    const B = st.B || {}, frames = (B.physics || {}).frames;
    if (!frames || !frames.length || !window.StageStory) return null;
    return window.StageStory.frameAt(frames, S.k || 0);
  }
  function drawGauges() {
    const st = window.__stage, box = document.getElementById('st-kpis');
    if (!st || !st.B || !box) return;
    const f = frameNow(st);
    const key = f ? `${f.step}|${f.gm_m}|${f.heel_deg}|${f.draught_mean_m}|${f.max_bm_ratio}|${f.stability_passed}|${f.strength_passed}` : 'none';
    if (key === lastKey) return;
    lastKey = key;
    box.classList.remove('off');
    const rows = f && window.StageStory ? window.StageStory.plainGauges(f) : null;
    for (const node of box.querySelectorAll('[data-g]')) {
      const row = rows && rows[node.dataset.g];
      node.classList.toggle('na', !row || /^—/.test(row.text));
      node.classList.toggle('bad', !!(row && row.bad));
      const text = row ? (node.dataset.g === 'balance' ? row.text.replace(/ list| trim/g, '') : row.text) : (st.physicsReady ? '—' : 'measuring…'); // the KPI label already says "List / trim"
      node.querySelector('i').textContent = text; node.title = row ? row.text : '';
    }
  }
  function tick() { try { drawGauges(); } catch (e) { /* a half-loaded bundle: try again next tick */ } setTimeout(tick, 150); }

  window.StudioShip = {
    ready,
    restoreURL() { try { history.replaceState(history.state, '', original); } catch (e) { /* ignore */ } },
    get originalURL() { return original; },
    /* Show or hide the gauge strip (a step that draws its own readings can hide it). */
    gauges(on) { const g = document.getElementById('st-kpis'); if (g) g.style.visibility = on === false ? 'hidden' : ''; },
    refresh() { lastKey = ''; drawGauges(); },
  };
  const start = () => { poll(); tick(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
