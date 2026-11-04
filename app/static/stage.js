/* StowLM stage page controller: loads the stage bundle (CONTRACT.md, stage-v1), replays the plan box by box and
 * drives the ship scene, yard strip, bay plan, physics gauges, move race, rule checklist, mask x-ray and Spark panel.
 * The browser never computes physics or rules: every number shown comes from the bundle (null -> "—" + reason). */
(function () {
  'use strict';
  const L = window.StageLib || {};
  const $ = (id) => document.getElementById(id);
  const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif';
  const num = (v) => typeof v === 'number' && isFinite(v);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtInt = (v) => (num(v) ? Math.round(v).toLocaleString('en-US') : '—');
  const params = new URLSearchParams(location.search);
  const DEV = params.get('dev') === '1';
  const PRESENT = params.get('present') === '1';
  const VIDEO = params.get('video') === '1'; // stage-director.js drives playback (no autoplay)
  const SHIP_RE = /^\d{7}$/;
  const YARD_RE = /^([A-Z0-9]+)-(\d+)-(\d+)(?:-(\d+))?$/i;
  const yardKeyOf = (addr) => { const m = YARD_RE.exec(String(addr || '')); return m ? `${m[1]}-${m[2]}-${m[3]}` : null; };
  const TH = { THLCH: 'แหลมฉบัง', THBKK: 'กรุงเทพฯ', THSGZ: 'สงขลา' };
  const KIND = {
    load: ['LOAD', ''], discharge: ['DISCHARGE', ''], relocate: ['YARD REHANDLE', 'red'], restow_off: ['RESTOW · OFF', 'np'],
    restow_on: ['RESTOW · BACK ON', 'np'], restow: ['RESTOW', 'np'], hatch_cover_off: ['HATCH COVER OFF', 'np'], hatch_cover_on: ['HATCH COVER ON', 'np'],
  };

  // ------------------------------------------------------------------ data loading
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!r.ok) { let msg = `${r.status}`; try { msg += ' ' + ((await r.json()).error || ''); } catch (e) { /* ignore */ } const failed = new Error(`${url}: ${msg}`); failed.status = r.status; throw failed; }
    return r.json();
  }

  function hexRgb(h) {
    const m = /^#?([0-9a-f]{6})$/i.exec(h || '');
    if (!m) return [128, 140, 160];
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  // ------------------------------------------------------------------ replay state
  function emptyState() { return { ship: new Map(), yard: new Map(), coversOff: new Set(), buffer: [], bufferFrom: new Map(), loc: new Map() }; }
  function cloneState(s) {
    return { ship: new Map(s.ship), yard: new Map([...s.yard].map(([k, v]) => [k, v.slice()])), coversOff: new Set(s.coversOff),
      buffer: s.buffer.slice(), bufferFrom: new Map(s.bufferFrom), loc: new Map(s.loc) };
  }
  function initialState(b) {
    const st = emptyState(), ini = b.initial || {};
    for (const e of ini.ship || []) if (e && e.box) { st.ship.set(String(e.slot), e.box); st.loc.set(e.box, { w: 'ship', k: String(e.slot) }); }
    for (const e of ini.yard || []) if (e && e.key) { st.yard.set(e.key, (e.boxes || []).slice()); for (const bx of e.boxes || []) st.loc.set(bx, { w: 'yard', k: e.key }); }
    for (const c of ini.covers_off || []) st.coversOff.add(typeof c === 'string' ? c : c.id || c.cover);
    for (const e of ini.quay_buffer || []) { const bx = typeof e === 'string' ? e : e.box; if (bx) { st.buffer.push(bx); st.loc.set(bx, { w: 'buf' }); if (e.from) st.bufferFrom.set(bx, e.from); } }
    return st;
  }
  const warnings = [];
  function applyStep(st, step) {
    const k = step.kind;
    if (k === 'hatch_cover_off') { st.coversOff.add(step.cover); return; }
    if (k === 'hatch_cover_on') { st.coversOff.delete(step.cover); return; }
    const bx = step.box; if (!bx) return;
    const at = st.loc.get(bx);
    if (at) {
      if (at.w === 'ship') { if (st.ship.get(at.k) === bx) st.ship.delete(at.k); if (step.src && SHIP_RE.test(step.src)) st.bufferFrom.set(bx, at.k); }
      else if (at.w === 'yard') { const a = st.yard.get(at.k); if (a) { const i = a.lastIndexOf(bx); if (i >= 0) a.splice(i, 1); } }
      else if (at.w === 'buf') { const i = st.buffer.indexOf(bx); if (i >= 0) st.buffer.splice(i, 1); }
    } else if (warnings.length < 20) warnings.push(`step ${step.i}: ${bx} not found before ${k}`);
    const dst = String(step.dst || '');
    if (SHIP_RE.test(dst)) { st.ship.set(dst, bx); st.loc.set(bx, { w: 'ship', k: dst }); st.bufferFrom.delete(bx); return; }
    const yk = yardKeyOf(dst);
    if (yk) { if (!st.yard.has(yk)) st.yard.set(yk, []); st.yard.get(yk).push(bx); st.loc.set(bx, { w: 'yard', k: yk }); return; }
    if (k === 'discharge' || k === 'load') { st.loc.delete(bx); return; }
    st.buffer.push(bx); st.loc.set(bx, { w: 'buf' });
    if (at && at.w === 'ship') st.bufferFrom.set(bx, at.k);
  }

  // ------------------------------------------------------------------ app
  let B = null, M = null, scene = null, yard = null, bays = null;
  let steps = [], total = 0, checkpoints = [], frames = [], xrayByStep = new Map();
  const CK = 64;
  const S = { k: 0, t: 0, playing: false, playOne: false, speed: 1, state: null, anim: null, xray: params.get('xray') === '1', xrayHL: params.get('xhl') === '1',
    view: params.get('view') === 'bays' ? 'bays' : 'iso', hover: null, selected: null, lastCraneX: null, lastTrolleyY: 0, revealed: false };
  let baseDur = 1;

  function stateAt(k) {
    const ci = Math.min(Math.floor(k / CK), checkpoints.length - 1);
    const st = cloneState(checkpoints[ci]);
    for (let i = ci * CK; i < k; i++) applyStep(st, steps[i]);
    return st;
  }
  function frameAt(k) { // nearest measured keyframe with step <= k
    if (!frames.length) return null;
    let lo = 0, hi = frames.length - 1, best = null;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (frames[mid].step <= k) { best = frames[mid]; lo = mid + 1; } else hi = mid - 1; }
    return best;
  }
  function draughtAt(k) { const f = frameAt(k); return f && num(f.draught_mean_m) ? f.draught_mean_m : null; }

  function buildModel(b) {
    const podRgb = new Map();
    for (const p of b.pods || []) podRgb.set(p.locode, hexRgb(p.color));
    const yardKeys = new Set(), maxH = new Map();
    for (const e of (b.initial || {}).yard || []) yardKeys.add(e.key);
    for (const s of b.steps || []) for (const a of [s.src, s.dst]) { const k = yardKeyOf(a); if (k) yardKeys.add(k); }
    const portLabel = () => {
      const p = (b.meta || {}).port || {};
      if (TH[p.locode]) return `${(p.label || '').replace(/\s*\(simulated\)/i, '').toUpperCase()}  ${TH[p.locode]}`;
      return (p.label || p.locode || '').replace(/\s*\(simulated\)/i, '').toUpperCase();
    };
    return {
      bundle: b, boxes: b.boxes || {}, font: FONT,
      podRgb: (pod) => podRgb.get(pod) || [120, 132, 150],
      isShipSlot: (id) => SHIP_RE.test(String(id || '')),
      yardKeys: () => [...yardKeys],
      maxYardHeight: (k) => maxH.get(k) || 0,
      _maxH: maxH,
      portLabel,
      refDraught: () => {
        const f = (b.physics && b.physics.frames) || [];
        const ds = f.map((x) => x.draught_mean_m).filter(num);
        if (ds.length) return ds.reduce((a, c) => a + c, 0) / ds.length;
        const h = (b.geometry || {}).hull || {};
        return num(h.design_draught_m) ? h.design_draught_m * 0.85 : null;
      },
    };
  }

  function replayAll() {
    let st = initialState(B);
    checkpoints = [cloneState(st)];
    const track = (s) => { for (const [k, a] of s.yard) if (a.length > (M._maxH.get(k) || 0)) M._maxH.set(k, a.length); };
    track(st);
    for (let i = 0; i < total; i++) {
      applyStep(st, steps[i]);
      track(st);
      if ((i + 1) % CK === 0) checkpoints.push(cloneState(st));
    }
    if (warnings.length) console.warn('[stage] replay warnings', warnings);
  }

  // ------------------------------------------------------------------ header / static panels
  function setText(id, v, reason) { const el = $(id); if (!el) return; el.textContent = v; if (reason) el.title = reason; }
  function renderHeader() {
    const m = B.meta || {};
    renderKpis();

    const v = $('verdict');
    const placeholder = !!m.placeholder || m.planner === 'ui-placeholder' || m.source === 'placeholder';
    const outcome = m.model_outcome;
    if (placeholder) { v.textContent = 'PLACEHOLDER · NOT APPROVED'; v.className = 'verdict warn'; }
    else if (outcome) { v.textContent = outcome.kind === 'dead_end' ? '✕ STOWLM DEAD END · NO PLAN' : '✕ STOWLM · NO SHIPPED PLAN'; v.className = 'verdict fail'; }
    else if (m.checker_passed === false && m.planner === 'stowlm') { v.textContent = '✕ REJECTED BY CHECKER · NOT APPROVED'; v.className = 'verdict fail'; }
    else if (m.checker_passed && m.departure_approved) { v.textContent = m.repaired ? '✓ CHECKER PASS · REPAIRED' : '✓ CHECKER PASS'; v.className = m.repaired ? 'verdict warn' : 'verdict pass'; }
    else if (m.checker_passed === false || m.departure_approved === false) { v.textContent = '✕ NOT APPROVED'; v.className = 'verdict fail'; }
    else { v.textContent = 'VERDICT —'; v.className = 'verdict'; }
    v.title = m.notice || '';
    setText('rung', (m.rung || '—').toUpperCase());
    setText('vessel', m.vessel_label || '—');
    const p = m.port || {};
    $('port').innerHTML = `${esc(p.label || p.locode || '—')}${TH[p.locode] ? `<span class="th">${TH[p.locode]}</span>` : ''}`;
    setText('planner-label', m.planner_label || m.planner || '—');
    const wm = $('watermark');
    if (placeholder) { wm.hidden = false; wm.innerHTML = '<b>PLACEHOLDER</b><span>not a planner output · not approved · for render-scale testing only</span>'; }
    else if (m.source === 'MOCK') { wm.hidden = false; wm.innerHTML = '<b>MOCK DATA</b><span>UI development only · numbers are invented</span>'; }
    // D99: a StowLM run that is not the final showcase run carries this label in frame, every mode (the video
    // director shows it in its own stand-in pill, so it is not doubled there). Rule: app/stage_stowlm.py FINAL_RULE.
    const pv = $('preview-tag');
    if (pv) { pv.hidden = !m.preview_label || (VIDEO && !m.preview_label_all_modes); pv.textContent = m.preview_label || ''; pv.title = m.final_rule || ''; }
    renderOutcome(outcome);

    const scope = [];
    if (m.source === 'MOCK') scope.push('<span class="mock">MOCK DATA — NOT FOR JUDGES</span>');
    scope.push(`<b>Scope</b> ${esc(m.scope_line || '—')}`);
    if (m.notice) scope.push(`<b>Checker</b> ${esc(m.notice)}`);
    scope.push(`<b>Plan</b> ${esc(m.plan_id || '—')} · ${esc(m.instance_id || '')}`);
    scope.push(`<b>Planner</b> ${esc(m.planner || '—')} (${esc(m.source || '—')})`);
    if (warnings.length) scope.push(`<span class="mock" title="${esc(warnings.join('\n'))}">replay warnings: ${warnings.length}</span>`);
    $('scope').innerHTML = scope.join('<i class="dot">·</i>');
  }

  // The header tiles. Every value is this slice's measured difference from two-step, never a per-call figure.
  function renderKpis() {
    const m = B.meta || {}, k = B.kpi, rs = (B.comparison || {}).reasons || {};
    const reason = rs.kpi || 'KPI not available for this plan';
    const t = window.StageRules.kpiTiles(k);
    const n = num(m.slice_boxes) ? ` (${fmtInt(m.slice_boxes)} boxes to load)` : '';
    const why = (txt) => `This slice only${n}, not a whole call. ${txt} Basis: ${(k && k.basis) || '—'}`;
    const prize = k && k.prize && k.prize.text ? ` Size of the prize: ${k.prize.text}.` : '';
    const rate = k && num(k.thb_per_usd) ? `≈: converted at an assumed ฿${k.thb_per_usd} per USD. ${k.thb_rate_note || ''}` : '';
    const split = k && num(k.yard_moves_saved) && num(k.quay_moves_saved) && num(k.hatch_moves_saved) ? `Saved by this plan (negative = added): yard ${fmtInt(k.yard_moves_saved)}, quay ${fmtInt(k.quay_moves_saved)}, hatch ${fmtInt(k.hatch_moves_saved)} lifts.` : '';
    const tiles = [['kpi-moves', t.moves, 'kpi-moves-v', why(`Against ${window.StageRules.TWO_STEP_LONG}. ${split}`)], ['kpi-hours', t.hours, 'kpi-hours-v', why('Quay-crane work, not elapsed berth time.')],
      ['kpi-usd', t.usd, 'kpi-usd-v', why(`Mid-scenario assumptions, not local tariffs.${prize}`)], ['kpi-thb', t.thb, 'kpi-thb-v', why(`${rate}${prize}`)]];
    for (const [id, tile, vid, title] of tiles) {
      setText(vid, tile.value);
      $(id).classList.toggle('null', !tile.available); $(id).classList.toggle('worse', tile.worse);
      $(id).title = tile.available ? title : reason;
    }
    setText('kpi-moves-s', t.moves.sub);
    setText('kpi-scn', k ? `${k.scenario || ''} scenario · this slice`.trim() : 'this slice');
  }

  function renderOutcome(o) {
    const el = $('outcome-card'); if (!el) return;
    el.hidden = !o; if (!o) { el.innerHTML = ''; return; }
    const bt = o.backtracks || {};
    const cellv = (v, lab) => `<div><b>${num(v) ? fmtInt(v) : '—'}</b><span>${esc(lab)}</span></div>`;
    el.innerHTML = `<div class="oc-title">${esc(o.title)}</div>
      <div class="oc-reason"><b>Why:</b> ${esc(o.reason || o.outcome || 'no plan delivered')}${o.failure_detail && o.failure_detail !== o.reason ? `<br><span class="ln">${esc(o.failure_detail)}</span>` : ''}</div>
      <div class="oc-grid">${cellv(o.decoded_actions, `actions decoded${num(o.load_list) ? ` (of ${fmtInt(o.load_list)} boxes to load)` : ''}`)}${cellv(bt.count, 'backtracks (rewinds)')}${cellv(bt.rewound_actions, 'actions rewound')}</div>
      <div class="oc-note">${esc(o.note || '')}${bt.source ? ` Backtracks: ${esc(bt.source)}${bt.note ? ` (${esc(bt.note)})` : ''}.` : ''}${num(o.partial_moves_dropped) ? ` The ${fmtInt(o.partial_moves_dropped)}-lift decoded prefix in result.json is not drawn.` : ''}</div>`;
  }

  function renderLegend() {
    const parts = [];
    const used = new Set(Object.values(B.boxes || {}).map((x) => x.pod));
    for (const p of B.pods || []) if (used.has(p.locode)) parts.push(`<span class="lg" title="Port of discharge ${esc(p.locode)}"><i class="sw" style="background:${esc(p.color)}"></i>${esc((p.label || p.locode).replace(/\s*\(simulated\)/i, ''))}</span>`);
    parts.push('<span class="sep"></span>');
    parts.push('<span class="lg" title="Dangerous goods (IMDG class shown)"><svg width="13" height="13" viewBox="0 0 13 13"><path d="M6.5 0.8L12.2 6.5L6.5 12.2L0.8 6.5Z" fill="#e8413c" stroke="#111" stroke-width="1"/></svg>DG</span>');
    parts.push('<span class="lg" title="Reefer (needs a power plug)"><svg width="13" height="13" viewBox="0 0 13 13" stroke="#e9fbff" stroke-width="1.6" stroke-linecap="round"><path d="M6.5 1v11M1.7 3.8l9.6 5.4M1.7 9.2l9.6-5.4"/></svg>Reefer</span>');
    parts.push('<span class="lg" title="Non-productive lift (restow, hatch cover)"><i class="sw" style="background:transparent;border:1.6px solid #ffb547"></i>Restow</span>');
    parts.push('<span class="lg" title="Yard rehandle (a box moved to dig out another)"><i class="sw" style="background:transparent;border:1.6px solid #ff5b5b"></i>Rehandle</span>');
    $('legend').innerHTML = parts.join('');
  }

  async function renderPicker() {
    const sel = $('plan-select');
    try {
      const cat = await getJSON('/api/catalog');
      const cur = (B.meta || {}).plan_id || params.get('plan_id') || cat.active_plan_id;
      const pickLabel = (p) => { const name = { 'solver.gold_warmstart': 'Search software', 'solver.gold_warmstart.instruction': 'Search software replan', 'baseline.twostep': 'Two-step baseline' }[p.planner]; return name ? `${name} · ${p.instance_id || p.id}` : p.label || p.id; };
      sel.innerHTML = (cat.plans || []).filter((p) => !p.withheld).map((p) => `<option value="${esc(p.id)}"${p.id === cur ? ' selected' : ''}>${esc(pickLabel(p))}</option>`).join('') || '<option>no plans</option>';
      sel.onchange = () => { const q = new URLSearchParams(location.search); q.set('plan_id', sel.value); q.delete('step'); location.search = q.toString(); };
    } catch (e) { sel.innerHTML = '<option>catalogue unavailable</option>'; sel.title = e.message; }
  }

  // ------------------------------------------------------------------ Spark
  function paragraphs(text) { return String(text || '').split(/\n+/).filter((x) => x.trim()).map((x) => `<p>${esc(x)}</p>`).join(''); }
  const SRC = { 'spark-cached': ['MUSE SPARK · CACHED', 'spark'], 'spark-live': ['MUSE SPARK · LIVE', 'spark'], template: ['TEMPLATE (NO LLM)', ''], none: ['NONE', ''] };
  function renderSpark(boxText, boxSrc) {
    const ex = B.explanation || {};
    const src0 = SRC[ex.source] || [String(ex.source || '—').toUpperCase(), ''];
    const src = ex.label && ex.source !== 'template' ? [String(ex.label).toUpperCase(), src0[1]] : src0;
    const badge = $('spark-src'); badge.textContent = src[0]; badge.className = 'src-badge ' + src[1];
    // Only text Spark wrote and that binds to this plan sits under the Muse Spark heading.
    const spark = ex.source === 'spark-cached';
    $('spark-title').textContent = spark ? 'Muse Spark' : 'Plan explanation';
    $('spark-note').textContent = spark ? 'selects and orders checked statements · the checker decides' : ex.source === 'template' ? 'deterministic template (no Spark text bound to this plan)' : 'no explanation text bound to this plan';
    let html = '';
    if (S.selected) {
      const bx = B.boxes[S.selected] || {};
      html += `<div class="sb-k">Selected box</div><div class="sb-box">${esc(S.selected)} → ${esc(podLabel(bx.pod))}</div>`;
      html += boxText == null ? '<p class="sb-wait">Fetching explanation…</p>' : `${paragraphs(boxText)}<div class="sb-src">source: ${esc(boxSrc || '—')}</div>`;
      html += '<div class="sb-sep"></div><div class="sb-k">Whole plan</div>';
    }
    html += ex.text ? paragraphs(ex.text) : `<p class="sb-na">No plan explanation (${esc(ex.live_reason || 'none cached')}).</p>`;
    if (ex.cache_file) html += `<div class="sb-src" title="${esc(ex.binding || '')}">${esc(ex.label || 'cached')} · ${esc(ex.cache_file)}${ex.created_utc ? ' · ' + esc(ex.created_utc.slice(0, 16).replace('T', ' ')) + ' UTC' : ''} · bound to this plan</div>`;
    $('spark-body').innerHTML = html;
    const live = $('spark-live');
    live.disabled = !ex.live_available;
    live.title = ex.live_available ? 'Calls Muse Spark through the Meta credit gate' : ex.live_reason || 'Live Spark not available';
    $('spark-box').textContent = S.selected ? `Box ${S.selected}` : 'Click a box to ask about it';
  }
  let explainSeq = 0;
  async function selectBox(id, live) {
    S.selected = id;
    renderSpark(null);
    if (!id) { renderSpark(); return; }
    const seq = ++explainSeq;
    const cached = ((B.explanation || {}).per_box || {})[id];
    if (cached && !live) { renderSpark(cached, SRC[(B.explanation || {}).source] ? SRC[B.explanation.source][0].toLowerCase() : B.explanation.source); return; }
    try {
      const q = new URLSearchParams({ box: id }); if (B.meta && B.meta.plan_id) q.set('plan_id', B.meta.plan_id); if (live) q.set('live', '1');
      const r = await getJSON('/api/stage/explain?' + q);
      if (seq === explainSeq) renderSpark(r.text || '(no text)', r.source);
    } catch (e) { if (seq === explainSeq) renderSpark(`Explanation unavailable: ${e.message}`, 'error'); }
  }
  function podLabel(pod) { const p = (B.pods || []).find((x) => x.locode === pod); return p ? `${(p.label || pod).replace(/\s*\(simulated\)/i, '')} (${pod})` : pod || '—'; }

  // ------------------------------------------------------------------ rules
  const ICON = { pass: '✓', fail: '✕', unavailable: '?', na: '–', pending: '' };
  function renderRules(reveal) {
    const r = B.rules;
    const el = $('rules');
    if (!r || !(r.families || []).length) { el.innerHTML = '<div class="na-block">Rule results not in this bundle.</div>'; return; }
    const fams = r.families;
    const done = S.k >= total;
    const R = window.StageRules, counts = R.tally(fams);
    const badge = $('rules-badge'), active = ((window.StageV6 && window.StageV6.state) || {}).rule_counts; // v6: the receipt's count, from data
    if ((B.meta || {}).model_outcome) { badge.textContent = 'NO PLAN · NOT CHECKED'; badge.className = 'src-badge fail'; }
    else if (done) { const b = R.badge(counts); badge.textContent = active && b.cls === 'ok' ? `${b.text} · LISTED` : b.text; badge.className = 'src-badge ' + b.cls; }
    else { badge.textContent = 'checking…'; badge.className = 'src-badge'; }
    $('rules-note').textContent = done ? (active ? `${counts.pass} pass · ${counts.fail} fail · ${counts.unavailable} unavailable` : `${R.summary(counts)} · ${r.source || ''}`) : 'full-plan verdict shown when the plan completes';
    el.innerHTML = fams.map((f, i) => {
      const ft = R.familyTally(f);
      const st = done ? R.verdict(ft) : 'pending';
      const rules = (f.rules || []).map((x) => {
        const s2 = done ? R.verdict(R.familyTally({ rules: [x] })) : 'pending';
        return `<div class="rule"><span class="ico ${s2}">${ICON[s2] || ''}</span><span>${esc(x.description || x.id)}<code>${esc(x.id)}${x.hardness ? ' · ' + esc(x.hardness) : ''}</code></span><span class="${x.violations ? 'viol' : ''}">${done && x.violations ? x.violations + ' viol.' : ''}</span></div>`;
      }).join('');
      const applicable = ft.total - ft.na;
      const cnt = !done ? `–/${applicable}` : st === 'na' ? 'n/a' : `${ft.pass}/${applicable}`;
      return `<details class="fam"${done && st === 'fail' ? ' open' : ''}><summary><span class="ico ${st}${done && reveal ? ' reveal' : ''}" style="${reveal ? `animation-delay:${i * 0.14}s` : ''}">${ICON[st] || ''}</span><span>${esc(f.label || f.family)}</span><span class="cnt">${cnt}</span></summary>${rules}</details>`;
    }).join('') + (active ? `<div class="v6-rules-foot">Full checker: ${active.active} active rules, ${active.off} off as run. ${counts.total} listed here.</div>` : '');
  }

  // ------------------------------------------------------------------ race
  const TWO_STEP = 'Two-step baseline: a simple two-step planner, modelled from one field expert\'s description';
  const FLOOR = 'Lower bound (proven floor, not necessarily reachable)';
  const RACE_ROWS = [['loads', 'Loads'], ['yard_rehandles', 'Yard rehandles'], ['restows', 'Restows'], ['hatch_lifts', 'Hatch-cover lifts'], ['total_lifts', 'Total lifts']];
  function liveCounts() { return S.k > 0 ? steps[S.k - 1].counts || {} : {}; }
  function renderRace() {
    const c = B.comparison || {}, rs = c.reasons || {};
    // Every number here is a finished plan's final count, at every lift: a live prefix is never set beside a finished
    // rival. Playback progress is the one "lift k of n" tick under the bars, and it is not a score.
    const fin = (c.stowlm && c.stowlm.counts) || (total ? steps[total - 1].counts : {}) || {};
    const base = c.baseline, lb = c.lower_bound, sp = c.spark;
    // Spark column only when a recorded Spark run exists for this instance (or is being replayed).
    const noPlan = !!(B.meta || {}).model_outcome;
    const showSpark = !!sp || (!!rs.spark && !/^no Muse Spark/i.test(rs.spark) && !/this plan is the Muse Spark/i.test(rs.spark));
    const cell = (v, reason, cls = '') => (num(v) ? `<td class="${cls}">${fmtInt(v)}</td>` : `<td class="na" title="${esc(reason || 'not reported')}">—</td>`);
    const full = (c.stowlm && c.stowlm.label) || (B.meta || {}).planner_label || 'Plan';
    const name = esc((B.meta || {}).placeholder ? 'Placeholder' : full.length > 18 ? full.split(/\s*[·(]/)[0].slice(0, 18) : full);
    const spc = sp && sp.counts;
    const spTitle = sp ? `${sp.label} · ${sp.checker_passed ? 'checker PASS' : 'checker FAIL'} · ${sp.source || ''}` : rs.spark || '';
    const tick = !noPlan && total && S.k < total ? `<span class="race-tick" title="Playback position only: not a score, and not comparable with any number in this panel">lift ${fmtInt(S.k)} of ${fmtInt(total)}</span>` : '';
    let h = `<table class="race-table${showSpark ? ' four' : ''}"><thead><tr><th>${tick}</th><th class="live-h" title="${esc(full)}">${name}<br><span>final plan</span></th><th title="${esc(TWO_STEP)}">Two-step<br><span>baseline</span></th>${showSpark ? `<th class="spark-h" title="${esc(spTitle)}">Spark<br><span>zero-shot</span></th>` : ''}<th title="${esc(FLOOR)}">Lower bound<br><span>proven floor</span></th></tr></thead><tbody>`;
    const allZero = (key) => [fin, base && base.counts, spc, lb].every((o) => !o || !num(o[key]) || o[key] === 0);
    for (const [key, lab] of RACE_ROWS) {
      if (key === 'restows' && allZero(key)) continue; // compact: no restows in any plan
      if (key === 'loads' && showSpark && [base && base.counts, spc].every((o) => o && o.loads === fin.loads)) continue; // same load list everywhere
      if ((B.meta || {}).model_outcome) { h += `<tr class="${key === 'total_lifts' ? 'total' : ''}"><td>${lab}</td><td class="na" title="${esc(rs.stowlm || 'no plan')}">—</td>${cell(base && base.counts ? base.counts[key] : null, base ? 'not reported' : rs.baseline)}${showSpark ? cell(spc ? spc[key] : null, sp ? 'not reported' : rs.spark, 'spark') : ''}${cell(lb ? lb[key] : null, lb ? 'not part of the bound' : rs.lower_bound)}</tr>`; continue; }
      const bv = base && base.counts ? base.counts[key] : null;
      const lbv = lb ? lb[key] : null;
      const better = num(bv) && num(fin[key]) && fin[key] < bv;
      h += `<tr class="${key === 'total_lifts' ? 'total' : ''}"><td>${lab}</td>${cell(fin[key], 'not reported', `live${better ? ' better' : ''}`)}${cell(bv, base ? 'not reported' : rs.baseline)}${showSpark ? cell(spc ? spc[key] : null, sp ? 'not reported' : rs.spark, 'spark') : ''}${cell(lbv, lb ? 'not part of the bound' : rs.lower_bound)}</tr>`;
    }
    const later = (o) => (o && num(o.projected_later_restows) && num(o.projected_later_hatch_lifts) ? o.projected_later_restows + o.projected_later_hatch_lifts : null);
    const liveTd = (v, cls) => (noPlan ? `<td class="na" title="${esc(rs.stowlm || 'no plan')}">—</td>` : cell(v, 'not reported', cls));
    const lbLater = lb ? (num(lb.projected_later) ? lb.projected_later : later(lb)) : null;
    h += `<tr class="later"><td title="Restows and hatch-cover lifts this plan forces at later ports (projected by the move engine)">Later ports (proj.)</td>${liveTd(later(fin), 'live')}${cell(later(base && base.counts), base ? 'not reported' : rs.baseline)}${showSpark ? cell(later(spc), sp ? 'not reported' : rs.spark, 'spark') : ''}${cell(lbLater, lb ? 'not part of the bound' : rs.lower_bound)}</tr>`;
    // D11 objective: non-productive lifts now + projected later (yard rehandles, restows, hatch-cover lifts).
    const wOf = (o) => (o && num(o.weighted_nonproductive) ? o.weighted_nonproductive : null);
    const finalW = num(wOf(fin)) && num(wOf(base && base.counts)) && wOf(fin) < wOf(base.counts);
    h += `<tr class="total objective"><td title="The objective (D11): yard rehandles + restows + hatch-cover lifts, now and projected at later ports. Fewest wins.">Non-productive, now + later</td>${liveTd(wOf(fin), `live${finalW ? ' better' : ''}`)}${cell(wOf(base && base.counts), base ? 'not reported' : rs.baseline)}${showSpark ? cell(wOf(spc), sp ? 'not reported' : rs.spark, 'spark') : ''}${cell(lb ? lb.weighted_nonproductive : null, lb ? 'not reported' : rs.lower_bound)}</tr>`;
    h += '</tbody></table>';
    // bars: every lift the plan causes, now + projected later ports; amber = non-productive part
    const tot = (o) => (o && num(o.total_lifts) ? o.total_lifts + (later(o) || 0) : null);
    const npOf = (o) => (o ? ['yard_rehandles', 'restows', 'hatch_lifts'].reduce((a, k) => a + (num(o[k]) ? o[k] : 0), 0) + (later(o) || 0) : 0);
    const lbTot = lb && num(lb.total_lifts) ? lb.total_lifts + (lbLater || 0) : null;
    const rows = [
      noPlan ? { lab: name, v: null, reason: rs.stowlm } : { lab: name, v: tot(fin), np: npOf(fin), reason: 'not reported', live: true },
      { lab: 'Two-step baseline', v: tot(base && base.counts), np: npOf(base && base.counts), reason: rs.baseline, title: TWO_STEP },
    ];
    if (showSpark) rows.push({ lab: 'Muse Spark', v: tot(spc), np: npOf(spc), reason: rs.spark, cls: 'spark', title: spTitle });
    rows.push({ lab: 'Lower bound', sub: 'proven floor, not necessarily reachable', v: lbTot, np: lb ? (num(lb.nonproductive) ? lb.nonproductive : 0) + (lbLater || 0) : 0, reason: rs.lower_bound, title: FLOOR });
    const mx = Math.max(1, ...rows.map((r) => r.v || 0));
    const barLabel = (r) => `<span class="bl">${esc(r.lab)}${r.sub ? `<small>${esc(r.sub)}</small>` : ''}</span>`;
    h += '<div class="bars">' + rows.map((r) => {
      if (!num(r.v)) return `<div class="bar-row na" title="${esc(r.reason || 'not available')}">${barLabel(r)}<span class="bt"></span><span class="bv">—</span></div>`;
      const w = (r.v / mx) * 100, wn = (Math.min(r.np, r.v) / mx) * 100;
      return `<div class="bar-row${r.live ? ' live' : ''}${r.cls ? ' ' + r.cls : ''}"${r.title ? ` title="${esc(r.title)}"` : ''}>${barLabel(r)}<span class="bt"><i class="pr" style="width:${w}%"></i><i class="np" style="left:${w - wn}%;width:${wn}%"></i></span><span class="bv">${fmtInt(r.v)}</span></div>`;
    }).join('') + `<div class="bars-note">finished plans only · bar = all lifts incl. projected later-port lifts · two-step baseline: ${esc(window.StageRules.TWO_STEP_LONG)}</div></div>`;
    $('race').innerHTML = h;
    // headline delta: the D11 objective (non-productive now + later) from the measured final totals
    const d = $('race-delta');
    const fw = wOf(fin), bw = wOf(base && base.counts), lw = lb && num(lb.weighted_nonproductive) ? lb.weighted_nonproductive : null;
    // "Optimal" needs the exact solver's own proof. A plan on the floor without one only reaches the floor, and the gap
    // to the bound is information, never a target (the bound is loose).
    const proved = (B.meta || {}).proved_optimal === true;
    const gapTitle = ` ${FLOOR}: the gap to it is information, not a target.`;
    if (num(fw) && num(bw) && bw > 0) {
      const diff = fw - bw, pct = (diff / bw) * 100;
      d.className = 'race-delta' + (diff > 0 ? ' worse' : '');
      d.title = 'Non-productive lifts (yard rehandles, restows, hatch covers), now + projected later ports (D11), measured by the move engine.' + gapTitle;
      const gap = num(lw) ? ` · ${fw - lw === 0 ? `<span class="opt">${proved ? '= lower bound (optimal)' : 'reaches the floor'}</span>` : `gap to bound ${fmtInt(fw - lw)}`}` : '';
      d.innerHTML = (diff === 0 // a tie is stated as a tie (no "−0 / −0 %")
        ? `<b>Tie with two-step</b>(${fmtInt(fw)} = ${fmtInt(bw)} wasted lifts)${gap}`
        : `<b>${diff < 0 ? '−' : '+'}${fmtInt(Math.abs(diff))} wasted lifts</b>(${diff < 0 ? '−' : '+'}${Math.abs(pct).toFixed(0)} %) vs two-step${gap}`);
    } else if (num(fw) && num(lw)) {
      d.className = 'race-delta';
      d.innerHTML = fw - lw === 0 ? (proved ? '<b class="opt">= lower bound</b> proved optimal' : '<b class="opt">reaches the floor</b>') : `<b class="gap">+${fmtInt(fw - lw)}</b> wasted lifts above the floor`;
      d.title = (rs.baseline || '') + gapTitle;
    } else { d.textContent = ''; }
    // Ineligible evidence: the counts stay inspectable, never a claimed saving (backend gate, rs.kpi says why).
    if (c.diagnostic && d.innerHTML) { d.innerHTML = '<b class="gap">DIAGNOSTIC · not a claimed saving</b> ' + d.innerHTML; d.title = `${rs.kpi || ''} ${d.title || ''}`.trim(); }
  }

  // ------------------------------------------------------------------ physics
  let lastFrameKey = null, physPoll = null;
  // v6 break-it: a verdict frame replaces the bundle's frame until cleared. Values come from the checker's verdict only.
  function setGaugeFrame(frame) { S.gaugeFrame = frame || null; lastFrameKey = null; renderPhysics(true); }
  function setGaugeNeedle(needle) { // motion only: each gauge still prints the frame's value
    const G = L.gauges, v = S.gaugeFrame || (B.physics ? frameAt(S.k) : null); if (!G || !v) return;
    G.gm($('g-gm'), { gm: v.gm_m, min: v.gm_min_m, needle: needle.gm });
    G.heel($('g-heel'), { heel: v.heel_deg, trim: v.trim_m, needle: needle.heel });
  }
  function renderPhysics(force) {
    const G = L.gauges; if (!G) return;
    const ph = B.physics;
    const f = S.gaugeFrame || (ph ? frameAt(S.k) : null);
    const key = (f ? f.step : 'none') + '|' + (ph ? 1 : 0);
    if (!force && key === lastFrameKey) return;
    lastFrameKey = key;
    const hull = (B.geometry || {}).hull || {};
    const v = f || {};
    const reason = !ph ? 'physics pending' : !f ? 'no keyframe at or before this lift' : v.error || null;
    G.gm($('g-gm'), { gm: v.gm_m, min: v.gm_min_m, reason });
    G.heel($('g-heel'), { heel: v.heel_deg, trim: v.trim_m });
    G.draught($('g-draught'), { aft: v.draught_aft_m, mean: v.draught_mean_m, fwd: v.draught_fwd_m, max: num(v.max_draught_m) ? v.max_draught_m : hull.max_draught_m, disp: v.displacement_t });
    const xs = (ph && ph.stations_x_m) || hull.stations_x_m || [];
    G.line($('c-sf'), { title: 'Shear force', xs, ys: v.sf_n || [], lo: ph && ph.sf_min_n, hi: ph && ph.sf_max_n, passed: v.strength_passed, scale: 1e6, unit: 'MN', reason });
    G.line($('c-bm'), { title: 'Bending moment', xs, ys: v.bm_nm || [], lo: ph && ph.bm_min_nm, hi: ph && ph.bm_max_nm, passed: v.strength_passed, scale: 1e6, unit: 'MN·m', reason });
    G.gz($('c-gz'), { pts: v.gz || [] });
    const pf = $('phys-frame');
    if (!ph) pf.textContent = 'pending';
    else if (!f) pf.textContent = 'no measured frame yet';
    else pf.textContent = `${f.step === 0 ? 'before first lift' : `after lift ${fmtInt(f.step)}`}${f.step !== S.k ? ` · nearest measured keyframe ≤ lift ${fmtInt(S.k)}` : ''}${f.criteria ? ' · ' + f.criteria : ''}`;
    const st = $('phys-status');
    const pb = (f && f.badge) || window.StageRules.physicsBadge(ph, f);
    st.textContent = pb.text; st.className = 'src-badge ' + pb.cls; st.title = pb.title;
    $('phys-note').textContent = ph ? [ph.note, f && f.tank_state ? `tanks: ${f.tank_state}` : null, ph.keyframes ? `keyframes: ${ph.keyframes}` : null].filter(Boolean).join(' · ') : '';
    $('phys-note').title = $('phys-note').textContent;
  }
  async function pollPhysics() {
    const box = $('phys-pending');
    box.hidden = false;
    try {
      const q = new URLSearchParams(); if (B.meta && B.meta.plan_id) q.set('plan_id', B.meta.plan_id);
      const r = await getJSON('/api/stage/physics?' + q);
      if (r.status === 'ready' && r.physics) {
        B.physics = r.physics; frames = (r.physics.frames || []).slice().sort((a, b) => a.step - b.step);
        box.hidden = true; renderPhysics(true); if (scene) showFrame(); return;
      }
      if (r.status === 'unavailable') {
        box.querySelector('.pp-title').textContent = 'Physics keyframes unavailable';
        $('pp-sub').textContent = r.reason || 'the backend could not compute physics for this plan';
        $('pp-fill').style.width = '0%'; return;
      }
      const pct = num(r.done) && num(r.total) && r.total > 0 ? (100 * r.done) / r.total : 0;
      $('pp-fill').style.width = pct.toFixed(1) + '%';
      $('pp-sub').textContent = num(r.done) && num(r.total) ? `${fmtInt(r.done)} / ${fmtInt(r.total)} keyframes measured by the checker` : 'measuring…';
    } catch (e) { $('pp-sub').textContent = `poll failed: ${e.message}`; }
    physPoll = setTimeout(pollPhysics, 1500);
  }

  // ------------------------------------------------------------------ x-ray
  function xrayInfo() {
    const x = B.xray || {};
    const st = steps[S.k];
    const xs = xrayByStep.get(S.k);
    if (!x.available || !xs) return { card: x, xs: null, st };
    return { card: x, xs, st, info: { legal: xs.legal_slots || null, chosen: xs.chosen && xs.chosen.slot, box: xs.chosen && xs.chosen.box } };
  }
  function xrayHighlight(x) {
    const hl = (x.highlights || [])[0];
    if (!hl) return '';
    const lines = (hl.spark_output || []).map((l) => `<div class="hl-line${l === hl.offending_line ? ' bad' : ''}"><code>${esc(l)}</code>${l === hl.offending_line ? '<span>✕ refused</span>' : ''}</div>`).join('');
    const wa = hl.with_appendix;
    return `<div class="xc-hl">
      <div class="xc-title hl">MUSE SPARK WITHOUT THE MASK · RECORDED</div>
      <div class="hl-meta">Instance <code>${esc(hl.instance_id || '—')}</code>${hl.same_instance ? '' : ' (another tiny test call)'} · plain-language prompt, no rules appendix, no mask</div>
      <div class="hl-out">${lines}</div>
      <div class="hl-ref"><b>Move engine:</b> <code>${esc(hl.engine_refusal || '—')}</code></div>
      <div class="hl-point">${esc(hl.point || '')}</div>
      ${wa ? `<div class="xc-foot">With the rules appendix the same model's plan ${wa.checker_passed ? 'passed the full checker' : 'failed the checker'}${num(wa.weighted_nonproductive) ? ` (${fmtInt(wa.weighted_nonproductive)} wasted lifts)` : ''}. Source: ${esc(hl.source || '')}</div>` : ''}
    </div>`;
  }
  function btLine(x) {
    const bt = x && x.backtracks; if (!bt || !num(bt.count)) return '';
    const ev = (x.steps || []).filter((r) => r.event === 'backtrack' || r.event === 'rewind');
    const here = ev.filter((r) => r.at_plan_step === S.k);
    const card = here.length ? `<div class="xc-bt"><b>BACKTRACK before this lift</b> (trace step ${esc(here.map((r) => r.step).join(', '))}${here.some((r) => num(r.n_rewound)) ? `, ${fmtInt(here.reduce((a, r) => a + (num(r.n_rewound) ? r.n_rewound : 0), 0))} actions rewound` : ''}): the decoder hit a dead end, rewound and re-decoded from here.</div>` : '';
    return card + `<div class="xc-bt" title="${esc(bt.source || '')}${bt.note ? ' — ' + esc(bt.note) : ''}">Backtracking: ${bt.count === 0 ? 'none needed on this run' : `${fmtInt(bt.count)} backtrack${bt.count === 1 ? '' : 's'}${num(bt.rewound_actions) ? `, ${fmtInt(bt.rewound_actions)} actions rewound` : ''}`}${ev.length ? ' · amber markers on the timeline' : bt.count ? ' (counted in result.json; the trace keeps the surviving path)' : ''}</div>`;
  }
  function xrayTools(x) {
    const parts = [];
    if (x.alternate && x.alternate.plan_id) parts.push(`<button type="button" class="xc-btn" data-act="alt" data-plan="${esc(x.alternate.plan_id)}" title="${esc(x.alternate.note || '')}">${esc(x.alternate.label || 'Open model run x-ray')} ›</button>`);
    if ((x.highlights || []).length) parts.push(`<button type="button" class="xc-btn hl${S.xrayHL ? ' on' : ''}" data-act="hl" aria-pressed="${S.xrayHL ? 'true' : 'false'}">Spark without mask ${S.xrayHL ? '▾' : '▸'}</button>`);
    return parts.length ? `<div class="xc-tools">${parts.join('')}</div>` : '';
  }
  function renderXray() {
    const card = $('xray-card');
    $('xray-btn').setAttribute('aria-pressed', S.xray ? 'true' : 'false');
    if (!S.xray) { card.hidden = true; if (scene) scene.setXray(null); if (bays) bays.setXray(null); return; }
    const r = xrayInfo();
    card.hidden = false;
    const tools = xrayTools(r.card), hl = S.xrayHL ? xrayHighlight(r.card) : '';
    if (!r.card.available) {
      card.innerHTML = `<div class="xc-title">FEASIBILITY MASK · X-RAY</div><div class="xc-na">Not available for this plan: ${esc(r.card.reason || 'no decoding trace')}.<br>The mask runs inside the model's decoder; solver and supplied plans have no trace to show.</div>${tools}${hl}`;
      scene && scene.setXray(null); bays && bays.setXray(null); return;
    }
    if (!r.xs) {
      const what = r.st ? (KIND[r.st.kind] || [r.st.kind])[0].toLowerCase() : 'end of plan';
      card.innerHTML = `<div class="xc-title">FEASIBILITY MASK · X-RAY</div><div class="xc-na">No model decision at lift ${S.k + 1} (${esc(what)}).<br>Rehandles, restows and hatch lifts are expanded by the move engine from the model's slot choices.</div>${tools}${hl}`;
      scene && scene.setXray(null); bays && bays.setXray(null); return;
    }
    const xs = r.xs;
    const nl = xs.n_legal, na = xs.n_actions;
    const pct = num(nl) && num(na) && na > 0 ? (100 * nl) / na : null;
    const tk = (xs.top_k || []).slice(0, 5);
    const pmax = Math.max(...tk.map((t) => (num(t.logprob) ? Math.exp(t.logprob) : 0)), 1e-9);
    const rows = tk.map((t) => {
      const p = num(t.logprob) ? Math.exp(t.logprob) : null;
      return `<div class="xc-row${t.legal === false ? ' masked' : ''}"><span class="t">${esc(JSON.stringify(t.text).slice(1, -1) || '∅')}</span><span class="p"><i style="width:${p == null ? 0 : (100 * p) / pmax}%"></i></span><span class="tag">${t.legal === false ? 'MASKED' : t.legal ? 'legal' : '?'} ${p == null ? '' : (p * 100).toFixed(p < 0.1 ? 1 : 0) + '%'}</span></div>`;
    }).join('');
    const ov = xs.overridden;
    const badge = ov === true ? '<span class="xc-ov bad" title="The mask forbade the model\'s own top-1 token at one or more decision positions and forced an allowed choice. The checker still decides.">OVERRIDDEN BY MASK</span>'
      : ov === false ? '<span class="xc-ov ok" title="The model\'s own top-1 token was legal at every decision position">MODEL\'S OWN CHOICE</span>' : '';
    const chips = (xs.decisions || []).map((d) => `<span class="xc-chip${d.overridden ? ' bad' : ''}" title="model's top-1 before masking: ${esc(JSON.stringify(d.top1 == null ? '' : d.top1))} · ${num(d.n_allowed) ? d.n_allowed + ' allowed tokens' : ''}">${esc(d.field)} ${d.overridden === true ? '✕' : d.overridden === false ? '✓' : '?'}</span>`).join('');
    card.innerHTML = `<div class="xc-title">FEASIBILITY MASK · DECISION FOR LIFT ${S.k + 1}${badge}</div>
      ${r.card.label ? `<div class="xc-src">${esc(r.card.label)} · recorded trace</div>` : ''}
      <div class="xc-big"><b>${fmtInt(nl)}</b><span>of ${fmtInt(na)} physically possible actions pass the rule mask${pct != null ? ` (${pct < 1 ? pct.toFixed(2) : pct.toFixed(0)} %)` : ''}</span></div>
      <div class="xc-bar"><i style="width:${pct == null ? 0 : Math.max(0.6, pct)}%"></i></div>
      ${chips ? `<div class="xc-chips"><span>model's top-1 per field:</span>${chips}</div>` : ''}
      <div class="xc-sub">MODEL'S TOP ${tk.length} FIRST TOKENS${xs.decisions && xs.decisions[0] ? ` (${esc(String(xs.decisions[0].field).toUpperCase())} FIELD)` : ''}, BEFORE MASKING</div>${rows || '<div class="xc-na">no top-k recorded</div>'}
      <div class="xc-chosen">Emitted: <code>${esc((xs.chosen && xs.chosen.text) || '—')}</code>${num(xs.chosen_logprob) ? ` · log p ${xs.chosen_logprob.toFixed(2)}` : xs.chosen_logprob_recorded ? ` · log p ${esc(xs.chosen_logprob_recorded === '-inf' ? '−∞' : xs.chosen_logprob_recorded)} (recorded)` : ''}${num(xs.chosen_logprob_unmasked) ? ` <span class="ln" title="log-probability of the same action without the mask">(unmasked ${xs.chosen_logprob_unmasked.toFixed(1)})</span>` : ''}</div>
      ${btLine(r.card)}<div class="xc-foot">${xs.legal_slots ? `Green = the ${xs.legal_slots.length} slot${xs.legal_slots.length === 1 ? '' : 's'} the mask allows for this box (recomputed from the action space); The model cannot write a placement the mask forbids · the checker still decides.` : `Legal-slot list not available for this lift (${esc(r.card.legal_slots_note || 'not computed')}); showing the chosen slot and the count only.`}</div>${tools}${hl}`;
    scene && scene.setXray(r.info); bays && bays.setXray(r.info);
  }

  // ------------------------------------------------------------------ caption, strip, counters
  function slotText(id) {
    const m = /^(\d{3})(\d{2})(\d{2})$/.exec(String(id || ''));
    if (!m) return null;
    const tier = +m[3], row = +m[2];
    return `bay ${m[1]} · row ${m[2]}${row === 0 ? '' : row % 2 ? ' stbd' : ' port'} · tier ${m[3]} ${tier >= 80 ? 'deck' : 'hold'}`;
  }
  function placeText(a) { if (!a) return '—'; if (SHIP_RE.test(a)) return `<code>${esc(a)}</code> <span class="ln">${esc(slotText(a))}</span>`; const yk = yardKeyOf(a); if (yk) return `yard <code>${esc(a)}</code>`; if (/^HC-/i.test(a)) return `<code>${esc(a)}</code>`; return `<span class="ln">${esc(String(a).toLowerCase() === 'quay' || /BUF/i.test(a) ? 'quay buffer' : a)}</span>`; }
  function renderCaption() {
    const cap = $('event-cap');
    const st = S.anim || S.playing ? steps[S.k] : S.k >= total ? null : steps[S.k - 1];
    if (!st) { cap.innerHTML = S.k >= total && total ? `<span class="ek ok">PLAN COMPLETE</span><span>${fmtInt(total)} lifts · ship ready for the checker's departure verdict</span>` : `<span class="ek">READY</span><span>${fmtInt(total)} lifts planned · press ▶ or space</span>`; return; }
    const [lab, cls] = KIND[st.kind] || [String(st.kind).toUpperCase(), ''];
    const what = st.box ? `<code>${esc(st.box)}</code>` : `<code>${esc(st.cover || '')}</code>`;
    const bx = st.box ? B.boxes[st.box] : null;
    const extra = bx ? ` <span class="ln">${esc(bx.pod || '')} · ${num(bx.mass_t) ? bx.mass_t.toFixed(1) + ' t' : '—'}${bx.dg ? ' · DG ' + esc(bx.dg.class) : ''}${bx.reefer ? ' · reefer' : ''}</span>` : '';
    const dst = st.kind === 'hatch_cover_off' ? '<span class="ln">quay (cover laid on the apron)</span>' : st.kind === 'hatch_cover_on' ? '<span class="ln">back on the hatch</span>' : placeText(st.dst);
    cap.innerHTML = `<span class="ek-n">#${st.i + 1}</span><span class="ek ${cls}">${lab}</span>${what}${extra}<span class="arrow">→</span>${dst}`;
  }
  const strip = { bright: null, dim: null, w: 0, h: 0 };
  function buildStrip() {
    const c = $('strip-canvas'); const r = c.getBoundingClientRect(); const dpr = Math.min(2, window.devicePixelRatio || 1);
    strip.w = r.width; strip.h = r.height; c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
    const mk = (alpha) => {
      const o = document.createElement('canvas'); o.width = c.width; o.height = c.height; const g = o.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.fillStyle = 'rgba(255,255,255,0.03)'; g.fillRect(0, 0, r.width, r.height);
      const w = r.width / Math.max(1, total);
      for (let i = 0; i < total; i++) {
        const st = steps[i]; let col, hh = 0.55;
        if (st.kind === 'relocate') { col = [255, 91, 91]; hh = 1; }
        else if (st.kind && st.kind.startsWith('restow')) { col = [255, 181, 71]; hh = 1; }
        else if (st.kind && st.kind.startsWith('hatch')) { col = [130, 150, 185]; hh = 0.8; }
        else col = M.podRgb(st.box && B.boxes[st.box] ? B.boxes[st.box].pod : null);
        g.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},${alpha})`;
        g.fillRect(i * w, r.height * (1 - hh), Math.max(w - (w > 3 ? 1 : 0), 0.6), r.height * hh);
      }
      return o;
    };
    strip.dim = mk(0.28); strip.bright = mk(0.95); strip.dpr = dpr;
    // ↶ backtrack markers (trace rows with event backtrack/rewind, placed at the next decoded action's lift)
    const bts = ((B.xray && B.xray.steps) || []).filter((x) => (x.event === 'backtrack' || x.event === 'rewind') && num(x.at_plan_step));
    for (const o of [strip.dim, strip.bright]) {
      const g = o.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (const x of bts) {
        const px = (r.width * x.at_plan_step) / Math.max(1, total);
        g.fillStyle = '#ffb547'; g.fillRect(px - 1, 0, 2, r.height);
        g.beginPath(); g.moveTo(px - 5, 0); g.lineTo(px + 5, 0); g.lineTo(px, 6); g.closePath(); g.fill(); // amber notch
      }
    }
    drawStrip();
  }
  function drawStrip() {
    const c = $('strip-canvas'); const g = c.getContext('2d'); if (!strip.dim || !strip.dim.width || !strip.dim.height) return; // zero while the play panel is hidden (v6)
    g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, c.width, c.height); g.drawImage(strip.dim, 0, 0);
    const x = Math.round((c.width * (S.k + (S.anim ? S.t : 0))) / Math.max(1, total));
    if (x > 0) g.drawImage(strip.bright, 0, 0, x, c.height, 0, 0, x, c.height);
  }
  function renderCounters() {
    $('lift-now').textContent = fmtInt(S.k); $('lift-total').textContent = fmtInt(total);
    const sc = $('scrub'); if (+sc.value !== S.k) sc.value = S.k;
    const st = S.state; if (!st) return;
    let inYard = 0; for (const a of st.yard.values()) inYard += a.length;
    const live = liveCounts();
    $('yard-stats').innerHTML = `in yard <b>${fmtInt(inYard)}</b> · loaded <b>${fmtInt(live.loads || 0)}</b> · rehandles <b class="${live.yard_rehandles ? 'red' : ''}">${fmtInt(live.yard_rehandles || 0)}</b>`;
  }

  // ------------------------------------------------------------------ frame management
  function showFrame() { // paused at a lift boundary: state after S.k lifts, nothing moving
    S.anim = null; S.t = 0;
    const opts = { exclude: {}, draught: draughtAt(S.k), craneX: S.lastCraneX };
    scene.setFrame(S.state, opts); yard.setFrame(S.state, { exclude: {}, nextPick: nextYardPick() }); if (bays) bays.setFrame(S.state, opts);
    if (S.xray) renderXray();
    markDirty(true);
  }
  function nextYardPick() { for (let i = S.k; i < Math.min(total, S.k + 3); i++) { const s = steps[i]; if (s.kind === 'load' || s.kind === 'relocate') return s.box; } return null; }
  function enterStep() { // start animating step S.k from S.state
    const st = steps[S.k];
    if (!st) { S.anim = null; return; }
    const exclude = st.box ? { box: st.box } : st.cover ? { cover: st.cover } : {};
    const opts = { exclude, draught: draughtAt(S.k), craneX: S.lastCraneX };
    scene.setFrame(S.state, opts);
    if (bays) bays.setFrame(S.state, { exclude: {}, draught: opts.draught });
    const path = scene.pathFor(st);
    let ya = null;
    const at = st.box ? S.state.loc.get(st.box) : null;
    if (at && at.w === 'yard') {
      const srcTier = (S.state.yard.get(at.k) || []).lastIndexOf(st.box);
      const dk = yardKeyOf(st.dst);
      ya = dk ? { kind: 'relocate', box: st.box, srcKey: at.k, srcTier, dstKey: dk, dstTier: (S.state.yard.get(dk) || []).length } : { kind: 'out', box: st.box, srcKey: at.k, srcTier };
    }
    yard.setFrame(S.state, { exclude: ya ? { box: st.box } : {}, nextPick: null });
    const dur = baseDur / S.speed;
    S.anim = { path, p: 0, prevX: S.lastCraneX, prevY: S.lastTrolleyY, restY: S.lastTrolleyY, nonproductive: !!st.nonproductive, red: st.kind === 'relocate',
      label: dur >= 0.35 ? (st.box || st.cover) : null, yard: ya, step: st, dst: st.dst, src: st.src };
    if (S.xray) renderXray();
    markDirty(true);
  }
  function commitStep() {
    const st = steps[S.k];
    if (S.anim && S.anim.path) { const d = S.anim.path.dst; S.lastCraneX = (d.x0 + d.x1) / 2; S.lastTrolleyY = (d.y0 + d.y1) / 2; }
    applyStep(S.state, st);
    S.k++;
  }
  function jumpTo(k, keepPlaying) {
    k = Math.max(0, Math.min(total, k | 0));
    S.k = k; S.state = stateAt(k); S.anim = null; S.t = 0;
    // crane rests over the last ship-side lift
    S.lastCraneX = null;
    for (let i = k - 1; i >= 0 && i >= k - 40; i--) { const s = steps[i]; const a = SHIP_RE.test(s.dst) ? s.dst : SHIP_RE.test(s.src) ? s.src : null; if (a) { S.lastCraneX = scene.craneXOfSlot(a); break; } if (s.cover) { S.lastCraneX = scene.coverX(s.cover); break; } }
    S.revealed = S.k >= total;
    if (keepPlaying && S.k < total) enterStep(); else { if (!keepPlaying) S.playing = false; showFrame(); }
    renderRules(false);
    markDirty(true);
  }
  function setPlaying(on) {
    if (on && S.k >= total) jumpTo(0);
    S.playing = on; S.playOne = false;
    if (on && !S.anim) enterStep();
    $('btn-play').textContent = on ? '❚❚' : '▶'; $('btn-play').setAttribute('aria-label', on ? 'Pause' : 'Play');
  }
  function stepForward() { if (S.k >= total) return; if (S.anim && S.playing) { finishAnim(); return; } S.playOne = true; S.playing = true; if (!S.anim) enterStep(); }
  function finishAnim() { commitStep(); S.anim = null; S.playing = false; S.playOne = false; afterCommit(); showFrame(); setPlaying(false); }
  function afterCommit() {
    if (S.k >= total) {
      S.playing = false; setPlaying(false);
      renderRules(!S.revealed); S.revealed = true;
    }
  }

  // DOM updates are throttled to ~20 Hz while playing so the canvas loop never waits on layout.
  let dirty = false, lastDom = 0;
  function markDirty(now) { dirty = true; if (now) lastDom = 0; }
  function flushDom(ts) {
    if (!dirty || ts - lastDom < 50) return;
    dirty = false; lastDom = ts;
    renderCaption(); renderCounters(); renderRace(); renderPhysics(false);
  }

  // ------------------------------------------------------------------ main loop
  let lastTs = 0, mouse = null, mouseMoved = false;
  const perf = { n: 0, sum: 0, max: 0, t0: 0, frames: 0, fps: 0 };
  function loop(ts) {
    const dt = lastTs ? Math.min(0.1, (ts - lastTs) / 1000) : 0; lastTs = ts;
    const t0 = performance.now();
    if (S.playing && S.anim) {
      S.t += dt * S.speed / baseDur;
      let guard = 0;
      while (S.t >= 1 && guard++ < 400) {
        const rest = S.t - 1;
        commitStep();
        if (S.k >= total || S.playOne) { S.t = 0; S.anim = null; const one = S.playOne; afterCommit(); if (one) setPlaying(false); showFrame(); break; }
        enterStep(); S.t = rest;
        if (rest < 1) break;
      }
      if (S.anim) S.anim.p = Math.min(1, S.t);
      markDirty(false);
    }
    if (mouse && mouseMoved) { mouseMoved = false; doHover(); }
    const view = { xray: S.xray, hover: S.hover, selected: S.selected };
    if (S.view === 'iso') scene.draw(S.anim || { restY: S.lastTrolleyY }, view); else bays.draw(S.anim, view);
    yard.draw(S.anim && S.anim.yard ? { yard: S.anim.yard, p: S.anim.p } : null);
    if (S.playing || dirty) drawStrip();
    flushDom(ts);
    const ms = performance.now() - t0;
    if (DEV) {
      perf.n++; perf.sum += ms; perf.max = Math.max(perf.max, ms); perf.frames++;
      if (!perf.t0) perf.t0 = ts;
      if (ts - perf.t0 >= 1000) {
        perf.fps = (perf.frames * 1000) / (ts - perf.t0);
        const line = `fps ${perf.fps.toFixed(0)} · js ${(perf.sum / perf.n).toFixed(1)} ms avg / ${perf.max.toFixed(1)} max · lift ${S.k}/${total}`;
        $('devhud').textContent = line; console.log('[stage perf]', line);
        window.__stagePerf = { fps: perf.fps, avg: perf.sum / perf.n, max: perf.max, k: S.k };
        perf.n = 0; perf.sum = 0; perf.max = 0; perf.frames = 0; perf.t0 = ts;
      }
    }
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------------ hover / tooltip
  function doHover() {
    const wrap = $('ship-wrap'); const r = wrap.getBoundingClientRect();
    const x = mouse.x - r.left, y = mouse.y - r.top;
    const id = S.view === 'iso' ? scene.hit(x, y) : bays.hit(x, y);
    S.hover = id;
    const tt = $('tooltip');
    if (!id) { tt.hidden = true; wrap.style.cursor = ''; return; }
    wrap.style.cursor = 'pointer';
    const bx = B.boxes[id] || {};
    const at = S.state.loc.get(id);
    const slot = at && at.w === 'ship' ? at.k : null;
    const rgb = M.podRgb(bx.pod);
    const rows = [
      ['Discharge', esc(podLabel(bx.pod))],
      ['Mass', num(bx.mass_t) ? `${bx.mass_t.toFixed(1)} t${bx.weight_class ? ` (${esc(bx.weight_class)})` : ''}` : '—'],
      ['Type', `${esc(bx.length_ft || '—')} ft · ${esc(bx.iso_type || '—')}${bx.empty ? ' · empty' : ''}`],
      ['DG', bx.dg ? `class ${esc(bx.dg.class)} · UN ${esc(bx.dg.un || '—')}` : 'none'],
      ['Reefer', bx.reefer ? 'yes (plugged)' : 'no'],
      ['Slot', slot ? `${esc(slot)}<br><span class="ln">${esc(slotText(slot))}</span>` : at && at.w === 'buf' ? 'quay buffer (restow)' : at && at.w === 'yard' ? `yard ${esc(at.k)}` : '—'],
    ];
    tt.innerHTML = `<div class="tt-id"><i style="background:rgb(${rgb.join(',')})"></i>${esc(id)}</div><dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl><div class="tt-hint">click to ${(B.explanation || {}).source === 'spark-cached' ? 'ask Muse Spark about' : 'explain'} this box</div>`;
    tt.hidden = false;
    const tw = tt.offsetWidth, th = tt.offsetHeight;
    tt.style.left = Math.min(r.width - tw - 8, x + 16) + 'px';
    tt.style.top = Math.max(8, Math.min(r.height - th - 8, y - th / 2)) + 'px';
  }

  // ------------------------------------------------------------------ wiring
  const SPEEDS = [0.5, 1, 2, 4, 8];
  function wire() {
    const sp = $('speeds');
    sp.innerHTML = SPEEDS.map((v) => `<button type="button" data-s="${v}" class="${v === S.speed ? 'on' : ''}">${v}×</button>`).join('');
    sp.onclick = (e) => { const b = e.target.closest('button'); if (!b) return; S.speed = +b.dataset.s; for (const x of sp.children) x.classList.toggle('on', x === b); };
    $('btn-play').onclick = () => setPlaying(!S.playing);
    $('btn-fwd').onclick = stepForward;
    $('btn-back').onclick = () => jumpTo(S.k - (S.anim ? 0 : 1));
    const sc = $('scrub');
    sc.oninput = () => { const was = S.playing; jumpTo(+sc.value, false); if (was) setPlaying(false); };
    $('xray-btn').onclick = () => { S.xray = !S.xray; renderXray(); };
    $('xray-card').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-act]'); if (!btn) return;
      if (btn.dataset.act === 'hl') { S.xrayHL = !S.xrayHL; renderXray(); }
      else if (btn.dataset.act === 'alt') { const q = new URLSearchParams(location.search); q.set('plan_id', btn.dataset.plan); q.set('xray', '1'); location.search = q.toString(); }
    });
    for (const b of document.querySelectorAll('[data-view]')) b.onclick = () => setView(b.dataset.view);
    $('spark-live').onclick = () => { if (S.selected) selectBox(S.selected, true); };
    const wrap = $('ship-wrap');
    wrap.addEventListener('mousemove', (e) => { mouse = { x: e.clientX, y: e.clientY }; mouseMoved = true; });
    wrap.addEventListener('mouseleave', () => { mouse = null; S.hover = null; $('tooltip').hidden = true; });
    wrap.addEventListener('click', () => { if (S.hover) selectBox(S.hover === S.selected ? null : S.hover); });
    $('yard-canvas').addEventListener('click', (e) => { const r = e.target.getBoundingClientRect(); const id = yard.hit(e.clientX - r.left, e.clientY - r.top); if (id) selectBox(id); });
    window.addEventListener('keydown', (e) => {
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) && e.target.id !== 'scrub') return;
      if (e.code === 'Space') { e.preventDefault(); setPlaying(!S.playing); }
      else if (e.code === 'ArrowRight') { e.preventDefault(); if (e.shiftKey) jumpTo(S.k + 10); else stepForward(); }
      else if (e.code === 'ArrowLeft') { e.preventDefault(); jumpTo(S.k - (e.shiftKey ? 10 : 1)); }
      else if (e.key === 'x' || e.key === 'X') { S.xray = !S.xray; renderXray(); }
      else if (e.key === 'b' || e.key === 'B') setView(S.view === 'iso' ? 'bays' : 'iso');
      else if (e.key === 'Home') jumpTo(0);
      else if (e.key === 'End') jumpTo(total);
      else if (/^[1-5]$/.test(e.key)) { S.speed = SPEEDS[+e.key - 1]; for (const x of sp.children) x.classList.toggle('on', +x.dataset.s === S.speed); }
    });
    let rz = null;
    window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(resizeAll, 120); });
  }
  function setView(v) {
    S.view = v === 'bays' ? 'bays' : 'iso';
    for (const b of document.querySelectorAll('[data-view]')) b.classList.toggle('on', b.dataset.view === S.view);
    $('ship-canvas').hidden = S.view !== 'iso'; $('bays-canvas').hidden = S.view !== 'bays';
    resizeAll();
  }
  function resizeAll() {
    if (S.view === 'iso') scene.resize(); else bays.resize();
    yard.resize(); buildStrip();
    if (S.anim) { const p = S.anim.p, t = S.t; enterStep(); S.anim.p = p; S.t = t; } else showFrame();
    renderPhysics(true);
  }

  // ------------------------------------------------------------------ boot
  // Video director hook (stage-director.js): a deterministic lift position replaces wall-clock playback.
  // pos = lifts done + fraction of the next lift's animation; rest = true parks at a lift boundary (no motion).
  function seek(pos, rest) {
    pos = Math.max(0, Math.min(total, +pos || 0));
    const k = Math.floor(pos), t = pos - k;
    S.playing = false; S.playOne = false;
    if (k >= total || rest) {
      if (S.k !== k || S.anim) { const was = S.k; jumpTo(k); if (k >= total && was < total) renderRules(true); }
      return;
    }
    if (!(S.anim && S.k === k)) {
      if (S.anim && S.k === k - 1) { commitStep(); enterStep(); }
      else { jumpTo(k); enterStep(); }
    }
    S.t = t; if (S.anim) S.anim.p = t;
    markDirty(false);
  }
  // v6 replan: every panel reads lift k while the scene draws the ship itself (scene.setMorph). No replay, no scene work.
  function peek(k) {
    k = Math.max(0, Math.min(total, k | 0));
    if (S.k === k && !S.anim) return;
    const crossed = (S.k >= total) !== (k >= total);
    S.playing = false; S.playOne = false; S.anim = null; S.k = k; S.revealed = k >= total;
    if (crossed) renderRules(false);
    markDirty(false);
  }
  function setXray(on, hl) {
    const a = !!on, b = !!hl;
    if (S.xray === a && S.xrayHL === b) return;
    S.xray = a; S.xrayHL = b; renderXray(); markDirty(true);
  }

  // (Re)initialise everything that depends on the bundle. Called once at boot and again by the video director
  // (window.__stage.load) to switch plans in place.
  function install(b) {
    B = b; S.gaugeFrame = null;
    if (!B || B.version !== 'stage-v1') console.warn('[stage] unexpected bundle version', B && B.version);
    steps = B.steps || []; total = steps.length;
    frames = B.physics && B.physics.frames ? B.physics.frames.slice().sort((a, c) => a.step - c.step) : [];
    xrayByStep = new Map();
    for (const x of (B.xray && B.xray.steps) || []) {
      if (!num(x.plan_step) && ['end_of_plan', 'backtrack', 'rewind', 'dead_end'].includes(x.event)) continue; // not a lift
      xrayByStep.set(num(x.plan_step) ? x.plan_step : x.step, x);
    }
    warnings.length = 0;
    M = buildModel(B);
    replayAll();
    baseDur = Math.max(0.22, Math.min(1.5, 48 / Math.max(1, total)));
    scene = L.createScene($('ship-canvas'), M);
    yard = L.createYard($('yard-canvas'), M);
    bays = L.createBays ? L.createBays($('bays-canvas'), M) : null;
    for (const b2 of document.querySelectorAll('[data-view="bays"]')) b2.disabled = !bays;
    $('watermark').hidden = true;
    S.k = 0; S.t = 0; S.anim = null; S.playing = false; S.playOne = false; S.hover = null; S.selected = null; S.revealed = false;
    S.lastCraneX = null; S.lastTrolleyY = 0; lastFrameKey = null;
    renderHeader(); renderLegend(); renderSpark();
    const sc = $('scrub'); sc.max = total; sc.value = 0;
    S.state = stateAt(0);
    clearTimeout(physPoll);
  }
  function afterInstall(step0) {
    setView(S.view);
    jumpTo(step0);
    renderRules(false);
    renderXray();
    if (!B.physics) pollPhysics(); else $('phys-pending').hidden = true;
  }

  async function boot() {
    if (PRESENT) document.body.classList.add('present');
    if (DEV) $('devhud').hidden = false;
    const q = new URLSearchParams(); if (params.get('plan_id')) q.set('plan_id', params.get('plan_id'));
    let b;
    try { b = await getJSON('/api/stage?' + q); }
    catch (e) {
      // v6: the plan is withheld until its gates pass. The chapters that need no ship carry on; the ship comes in when it's cleared.
      if (e.status === 403 && params.get('v6') === '1') { $('loading').hidden = true; setTimeout(boot, 3000); return; }
      const l = $('loading'); l.textContent = `Could not load the stage bundle — ${e.message}`; l.classList.add('error'); return;
    }
    if (params.get('speed')) { const v = +params.get('speed'); if (SPEEDS.includes(v)) S.speed = v; }
    install(b);
    renderPicker(); wire();
    $('loading').hidden = true;
    const step0 = params.has('step') ? (params.get('step') === 'end' ? total : +params.get('step') || 0) : 0;
    afterInstall(step0);
    if (params.has('phase') && S.k < total) { enterStep(); S.anim.p = S.t = Math.max(0, Math.min(0.99, +params.get('phase') || 0)); }
    requestAnimationFrame(loop);
    const auto = VIDEO || params.get('v6') === '1' ? false : params.has('autoplay') ? params.get('autoplay') === '1' : !params.has('step');
    if (auto && total) setTimeout(() => setPlaying(true), 900);
    window.__stage = { S, jumpTo, setPlaying, get total() { return total; },
      seek, peek, stateAt, setXray, selectBox, resizeAll, showRules() { if (B) renderRules(false); }, get B() { return B; }, get scene() { return scene; }, get physicsReady() { return !!(B && B.physics); },
      setGaugeFrame, setGaugeNeedle, redraw(exclude) { scene.setFrame(S.state, { exclude: exclude || {}, draught: draughtAt(S.k), craneX: S.lastCraneX }); },
      load(bundle, step) { install(bundle); afterInstall(step === 'end' ? total : +step || 0); } };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
