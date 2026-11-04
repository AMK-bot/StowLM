/* StowLM video story logic (v6, with v5 as a rehearsal; docs/headline.md): pure functions, no DOM. Used by stage-director.js and run directly by
 * app/tests/test_stage_director.py under node, so what is tested is what plays.
 *   - chooseVariant / resolveVariant: one config value (stage-shots.js, window.STAGE_VARIANT) picks the script.
 *   - resolveShot: which plan a shot shows; a hero shot on a stand-in always carries the stand-in label.
 *   - value / cell: a {{NUMBER: what}} comes only from stage-fillv5.js (experiments/mvp/video/fill-v5.json via
 *     experiments/mvp/ui/sync_fill.py). Nothing else. A missing value is reported, never a blank or a zero.
 *   - barCard, pairCard, liftsCard, worthCard: evidence cards. Bars draw to true scale from values only.
 *   - stepModels: which model did each step of shot 3, read from the bundle and the brief evidence.
 *   - sparkLines: which checker lines each Spark statement lights, from the bundle's explanation links.
 *   - ifBlocks, applyFallbacks, hookHits: v6's conditional text and the script's "If cut" lines.
 *   - morphOrder, morphAt, morphShip, familiesShown: the replan chapter's timeline (shot 7). */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.StageStory = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const STOWLM_RE = /^stowlm($|[.:])/;
  const PLACEHOLDER = /\{\{(?:NUMBER: )?([^{}]+)\}\}/g;
  const clone = (x) => JSON.parse(JSON.stringify(x));

  // ------------------------------------------------------------------ variant
  function chooseVariant(cfg, configured, param) {
    const names = Object.keys((cfg && cfg.variants) || {});
    if (!names.includes(configured)) return { variant: null, rehearsal: false, error: `stage-shots.js: unknown variant ${configured}` };
    if (param == null || param === '' || param === configured) return { variant: configured, rehearsal: false, error: null };
    if (!names.includes(param)) return { variant: configured, rehearsal: false, error: `?variant=${param} is not a script variant` };
    return { variant: param, rehearsal: true, error: null };
  }
  function resolveVariant(cfg, name) {
    const out = clone(cfg), v = out.variants[name];
    delete out.variants;
    const { kpi_hidden: hidden, kpi_money_hidden: money, ...own } = v;
    Object.assign(out, own); // v6 brings its own shots, cards and measured block; v5 keeps the top level
    out.variant = name;
    out.shots = out.shots.map((s) => Object.assign(s, { hide_kpis: !!hidden, hide_money: !!money }));
    return out;
  }
  // The script's "If cut" lines. gaps = chapter ids that can't play. A gap's shot takes its fallback's keys and hands its spare
  // seconds to the named shot, so the film keeps its length. A shot with a gap and no fallback stays, and the director holds it.
  function applyFallbacks(shots, gaps) {
    const gone = new Set(gaps || []), extra = new Map();
    const out = shots.map((s) => {
      if (!s.v6 || !s.fallback || !shotChapters(s).some((id) => gone.has(id))) return s;
      const { fallback: f, v6, ...rest } = s, { give, ...keys } = f;
      if (give) extra.set(String(give.shot), (extra.get(String(give.shot)) || 0) + give.s);
      return Object.assign(rest, keys, { fell_back: true });
    });
    return out.map((s) => (extra.has(String(s.id)) ? Object.assign({}, s, { duration_s: s.duration_s + extra.get(String(s.id)) }) : s));
  }
  // Which chapter draws a shot at tau, and at what chapter time. The span is fitted to the shot; a "hole" (or each of "holes") hands part
  // of the shot to another chapter (shot 1's cold open cuts between five; shot 3 opens on the hook's slams; shot 6's confirm is the
  // reading chapter's last beat). A hole's chapter time is tau + offset, or with "rate", start + (tau - from) * rate: rate 3 plays it
  // three times faster, rate 0 holds the frame at start. length = the chapter's own duration_s. The main span runs from shot time
  // "at" (default 0) to fit_s (default the shot's end).
  function holeAt(v, tau) {
    for (const h of [].concat(v.hole || [], v.holes || [])) {
      if (tau >= h.from && tau < h.to) return { chapter: h.chapter, tau: h.rate != null ? +h.start + (tau - h.from) * h.rate : tau + h.offset };
    }
    return null;
  }
  function chapterAt(s, tau, length) {
    const v = s.v6, hole = holeAt(v, tau);
    if (hole) return hole;
    const at = +v.at || 0, from = +v.from || 0, to = v.to != null ? +v.to : length || s.duration_s;
    const u = (tau - at) / ((v.fit_s != null ? +v.fit_s : s.duration_s) - at);
    return { chapter: v.chapter, tau: from + (to - from) * (u < 0 ? 0 : u > 1 ? 1 : u) };
  }
  // The chapters a shot draws, its own first: each must be able to play, or the shot takes its fallback.
  const shotChapters = (s) => [...new Set([s.v6.chapter, ...[].concat(s.v6.hole || [], s.v6.holes || []).map((h) => h.chapter), ...(s.v6.needs || [])])];
  // Shot 1's fallback, as the script words it: no documents, no drag and no radio each change the hits.
  function hookHits(o, features) {
    const on = (name) => !!((features || {})[name] || {}).available;
    const words = (on('intake') ? o.words : o.no_intake).filter((w) => w !== 'RADIO' || on('voice'));
    return { words, dare: on('sandbox') ? o.dare : o.no_breakit };
  }
  // A beat id ("5a") or a shot id ("3"), for &shot= and the X clip: {index, beat}.
  function findShot(shots, wanted) {
    const key = String(wanted);
    for (let i = 0; i < shots.length; i++) {
      if (String(shots[i].id) === key) return { index: i, beat: null };
      const b = (shots[i].beats || []).find((x) => x.id === key);
      if (b) return { index: i, beat: b };
    }
    return { index: -1, beat: null };
  }

  // ------------------------------------------------------------------ values
  function value(name, cfg, fillData) {
    const ms = ((cfg && cfg.measured) || {})[name];
    if (ms && ms.value != null) return { value: String(ms.value), source: ms.source || '' };
    if (fillData && Object.prototype.hasOwnProperty.call(fillData, name) && fillData[name] != null) return { value: String(fillData[name]), source: `experiments/mvp/video/fill-${(cfg && cfg.fill) || 'v5'}.json` };
    return null;
  }
  // /api/v6/figures into measured values. A figure that isn't bound to its source, its bytes and its scope isn't a figure.
  function figureValues(figures) {
    const out = {};
    for (const [name, f] of Object.entries(figures || {})) {
      if (!f || f.value == null || f.value === '' || !f.source || !f.scope || !/^[0-9a-f]{64}$/.test(String(f.sha256))) continue;
      out[name] = { value: f.value, source: `${f.source} · sha256 ${f.sha256.slice(0, 12)} · ${f.scope}` };
    }
    return out;
  }
  // Shot 5: "Live" is earned by the reading on screen, the tuned reader's finished read of this very sentence on this machine. No setting reaches it.
  function readLive(reading) {
    const r = reading || {}, who = r.reader || {}, proof = r.evidence || {}, read = r.live_read || {};
    return r.mode === 'live' && who.kind === 'stowlm_reader' && who.on_device === true && proof.parser === 'GlimmerParser'
      && /^[0-9a-f]{64}$/.test(String(proof.adapter_sha256)) && read.status === 'done' && !!r.sentence && read.sentence === r.sentence;
  }
  // [[IF name: …]] stays, brackets gone, only when name has a value. Otherwise the whole block goes and the text still reads.
  const ifBlocks = (text, cfg, fillData) => String(text == null ? '' : text).replace(/\[\[IF ([a-z-]+): (.*?)\]\]/g, (m, name, body) => (value(name, cfg, fillData) ? body : ''));
  function cell(text, cfg, fillData) {
    const missing = [];
    const out = ifBlocks(text, cfg, fillData).replace(PLACEHOLDER, (m, name) => {
      const v = value(name, cfg, fillData);
      if (v == null) { missing.push(name); return m; }
      return v.value;
    });
    return { text: out, missing };
  }
  const number = (name, cfg, fillData) => {
    const v = value(name, cfg, fillData), n = v == null ? NaN : parseFloat(String(v.value).replace(/,/g, '').replace('−', '-'));
    return isFinite(n) ? n : null;
  };

  // ------------------------------------------------------------------ plans
  function matchPlan(p, sel) {
    if (!sel || p.planner === 'ui-placeholder') return false;
    if (sel.label) return p.label === sel.label;
    const planner = String(p.planner || '');
    const methodOk = !sel.method || (sel.method === 'stowlm' ? STOWLM_RE.test(planner) && planner !== 'stowlm.base' : planner === sel.method);
    return (!sel.run || p.run === sel.run) && methodOk && (!sel.instance || p.instance_id === sel.instance);
  }
  // A prepared brief case: its recorded plan, only once the fresh gate has shown it.
  function shownCase(id, briefState) {
    const c = ((briefState || {}).prepared || []).find((x) => x.id === id);
    const after = c && c.gate && c.gate.status === 'shown' ? c.gate.after : null;
    return after && after.plan_id ? c : null;
  }
  // r.state: fixed (the shot names its plan) | hero (run 3.2 landed) | standin (a labelled recorded plan holds the hero's place)
  function resolveShot(s, cfg, plans, activeId, briefState) {
    plans = plans || [];
    const find = (sel) => plans.find((x) => matchPlan(x, sel)) || null;
    if (s.hero) {
      const hero = find(cfg.hero_plan);
      if (hero) return { state: 'hero', id: hero.id, plan: hero, tag: '', missing: false, brief: shownCase(cfg.hero_case, briefState) };
      const c = s.standin === 'plan' ? null : shownCase(cfg.standin_case, briefState);
      if (c) return { state: 'standin', id: c.gate.after.plan_id, plan: { id: c.gate.after.plan_id }, tag: cfg.standin_tag, missing: false, brief: c };
      const stand = find(cfg.standin_plan);
      return { state: 'standin', id: stand ? stand.id : activeId, plan: stand || { id: activeId }, tag: cfg.standin_tag, missing: !stand };
    }
    const p = find(s.plan);
    return { state: 'fixed', id: p ? p.id : activeId, plan: p || { id: activeId }, tag: '', missing: !p };
  }
  const approved = (B) => { const m = (B && B.meta) || {}; return m.checker_passed === true && m.departure_approved === true; };
  const RUNTIME = new Set(['hero_shown', 'reader_live', 'reader_figures', 'note_by_spark', 'copilot_delivered', 'hero_walked', 'hero_walked_repaired', 'hero_walked_plain', 'xray_before', 'package_batch_confirmed']);
  function conditionHolds(when, r, B, cfg) {
    if (when === 'hero') return !!r && r.state === 'hero' && approved(B);
    if (when === 'approved') return approved(B);
    if (when === 'spark_explained') return /^spark/.test(String(((B || {}).explanation || {}).source || ''));
    if (RUNTIME.has(when)) return !!((cfg || {}).runtime || {})[when]; // set at boot, from evidence
    return !when;
  }
  // Caption text for this plan: "when" fails -> "otherwise" (itself gated by "otherwise_when"), else nothing.
  function captionText(c, r, B, cfg) {
    if (conditionHolds(c.when, r, B, cfg)) return c.text;
    return c.otherwise && conditionHolds(c.otherwise_when, r, B, cfg) ? c.otherwise : null; // no otherwise_when: always
  }
  // The sentence on screen: the hero's only over the hero; a stand-in shows the sentence it was really recorded under.
  function sentenceFor(r, B, cfg) {
    if (r && r.state === 'hero') return cfg.hero_sentence;
    if (r && r.brief) return r.brief.sentence;
    const I = (B || {}).instruction;
    return I && I.presented ? I.sentence : '';
  }

  // ------------------------------------------------------------------ shot 3 and 5: the brief track
  function briefAt(track, tau) {
    let row = null;
    for (const t of track || []) if (tau >= t.from && tau < t.to) row = t;
    row = row || ((track || []).length && tau >= track[track.length - 1].to ? track[track.length - 1] : null);
    return row && row.stage ? row : null;
  }
  // Which model did each step. Only what the evidence says: a step with no evidence is pending, never a hard-coded name.
  // The reading comes from the brief case (its recorded reader) or the bundle's recorded parse. clip = a recorded voice clip of
  // this very sentence, if any.
  function stepModels(steps, r, B, clip) {
    const m = (B || {}).meta || {}, I = (B || {}).instruction || {};
    const reader = (((r || {}).brief || {}).reading || {}).reader;
    const reading = reader ? { model: reader.label, where: reader.on_device ? 'on device' : 'recorded reading' }
      : I.parse && I.parse.model ? { model: I.parse.model, where: 'recorded reading' } : null;
    const known = {
      speech: clip && clip.heard_by ? { model: clip.heard_by, where: 'recorded' } : null,
      reading,
      confirm: { model: 'The planner', where: 'confirms how it was read' },
      plan: m.planner_label ? { model: m.planner_label, where: /recorded/i.test(m.planner_label) ? '' : 'recorded' } : null,
      check: approved(B) ? { model: 'Full checker', where: 'passed' } : null,
    };
    return (steps || []).map((s) => Object.assign({}, s, known[s.id] || { model: null, where: '' }));
  }

  // ------------------------------------------------------------------ shot 4: statements and their checker lines
  function sparkLines(explanation) {
    const ex = explanation || {}, links = ex.links;
    if (ex.source !== 'spark-cached' || !links || !(links.statements || []).length || !(links.lines || []).length) {
      return { bound: false, reason: ex.source === 'spark-cached' ? 'no statement links in this bundle' : 'no Spark text bound to this plan' };
    }
    const keys = new Set(links.lines.map((l) => l.key));
    const statements = links.statements.filter((s) => (s.lines || []).length && s.lines.every((k) => keys.has(k)));
    if (!statements.length) return { bound: false, reason: 'no statement links to a checker line' };
    return { bound: true, statements, lines: links.lines };
  }
  // Which statement is lit at tau: one at a time, in Spark's order, evenly across the window.
  function litIndex(n, from, to, tau) {
    if (!n || tau < from) return -1;
    return Math.min(n - 1, Math.floor((tau - from) / Math.max(1e-6, (to - from) / n)));
  }

  // ------------------------------------------------------------------ evidence cards
  // Bars to true scale: width_pct = value / scale_max. A row without a value is pending, never a bar of width 0.
  function barCard(card, cfg, fillData) {
    const max = card.scale_key ? number(card.scale_key, cfg, fillData) : +card.scale_max;
    const unit = cell(card.unit || '', cfg, fillData), scaled = (!card.scale_key || max != null) && !unit.missing.length; // no denominator, no bar
    const rows = (card.rows || []).map((row) => {
      const v = number(row.key, cfg, fillData);
      const ok = scaled && v != null && v >= 0 && (!max || v <= max);
      return { label: row.label, key: row.key, own: !!row.own, reference: !!row.reference, at: +row.at || 0, value: ok ? v : null, text: ok ? `${value(row.key, cfg, fillData).value}${unit.text}` : null, width_pct: ok && max ? (100 * v) / max : null, pending: ok ? null : card.pending };
    });
    const title = card.title ? cell(card.title, cfg, fillData) : { text: '', missing: [] };
    const notes = (card.notes || []).map((n) => cell(n, cfg, fillData)).filter((n) => n.text && !n.missing.length).map((n) => n.text);
    const partial = card.partial ? cell(card.partial, cfg, fillData) : null; // shot 11's partial-run line: whole or absent
    return { eyebrow: card.eyebrow, title: title.missing.length ? null : title.text, pending: card.pending, rows, notes,
      partial: partial && partial.text && !partial.missing.length ? partial.text : null, complete: rows.every((r) => r.value != null) };
  }
  // Shot 3c: each row is its own value or pending. Nothing about a live take is drawn from anything else.
  function liveCard(card, cfg, fillData) {
    const rows = (card.rows || []).map((row) => { const v = value(row.key, cfg, fillData); return { label: row.label, text: v ? `${v.value}${row.unit || ''}` : null, pending: v ? null : card.pending }; });
    return { eyebrow: card.eyebrow, pending: card.pending, rows, note: card.note || '', complete: rows.every((r) => r.text != null) };
  }
  // Shot 3b: what moved. Each box's final slot in two plans, replayed from their steps; a slot the bundle's geometry doesn't know
  // fails closed. moved = boxes whose final slot differs; the caller checks it against the gate's own count.
  function finalSlots(B) {
    const at = new Map(), ship = /^\d{7}$/;
    for (const row of ((B || {}).initial || {}).ship || []) if (row && row.box) at.set(row.box, { slot: row.slot, cells: row.cells || [row.slot] });
    for (const st of (B || {}).steps || []) {
      if (!st.box) continue;
      if (ship.test(String(st.dst))) at.set(st.box, { slot: st.dst, cells: st.slot_cells && st.slot_cells.length ? st.slot_cells : [st.dst] });
      else if (ship.test(String(st.src))) at.delete(st.box);
    }
    return at;
  }
  function bayDiff(before, after) {
    const slots = (((after || {}).geometry || {}).slots) || [], cell = new Map(slots.map((s) => [s.id, s]));
    const a = finalSlots(before), b = finalSlots(after);
    if (!slots.length || !a.size || !b.size) return { ok: false, reason: 'no slots to compare' };
    const moved = [...b.keys()].filter((box) => !a.has(box) || a.get(box).slot !== b.get(box).slot);
    const place = (map) => { const out = {}; for (const [box, v] of map) for (const c of v.cells) { if (!cell.has(c)) return null; out[c] = { box, moved: moved.includes(box) }; } return out; };
    const pb = place(a), pa = place(b);
    if (!pb || !pa) return { ok: false, reason: 'a box sits in a slot the ship does not have' };
    const bays = [...new Set(slots.map((s) => s.bay))].sort((x, y) => x - y).map((bay) => {
      const inBay = slots.filter((s) => s.bay === bay);
      return { bay, rows: [...new Set(inBay.map((s) => s.row))].sort((x, y) => x - y), tiers: [...new Set(inBay.map((s) => s.tier))].sort((x, y) => y - x), cells: inBay.map((s) => ({ id: s.id, row: s.row, tier: s.tier, before: pb[s.id] || null, after: pa[s.id] || null })) };
    });
    return { ok: true, moved, bays };
  }
  // Shot 6: every moved box, ordered so a box lands only after whatever sat in its target cells has left. Boxes that swap
  // places can't both wait, so a cycle is broken in the list's own order. Boxes in `first` (the rule's) go as early as their way clears.
  function morphOrder(before, after, first) {
    const a = finalSlots(before), b = finalSlots(after);
    if (!a.size || !b.size) return { ok: false, order: [] };
    const moved = [...b.keys()].filter((box) => !a.has(box) || a.get(box).slot !== b.get(box).slot);
    if (moved.some((box) => !a.has(box))) return { ok: false, order: [] };
    const sitting = new Map();
    for (const box of moved) for (const c of a.get(box).cells) sitting.set(c, box);
    const order = [], left = new Set(moved);
    const free = (box) => b.get(box).cells.every((c) => !sitting.has(c) || sitting.get(c) === box || !left.has(sitting.get(c)));
    const fly = (box) => { left.delete(box); order.push({ box, from: a.get(box).slot, to: b.get(box).slot }); };
    const clearFor = (box, seen) => { // whatever sits in this box's way goes first, then the box
      if (!left.has(box) || seen.has(box)) return;
      seen.add(box);
      for (const c of b.get(box).cells) if (sitting.has(c) && sitting.get(c) !== box) clearFor(sitting.get(c), seen);
      fly(box);
    };
    for (const box of Array.isArray(first) ? first : []) clearFor(box, new Set());
    while (left.size) fly([...left].find(free) || left.values().next().value);
    return { ok: true, order };
  }
  // B1: which moved boxes the rule forced out and which the search moved of its own accord. known is false when the gate couldn't
  // ask the rule: then nothing is credited to it. A forced box that didn't move isn't counted, whatever the list says.
  function forcedSplit(order, forced) {
    const known = Array.isArray(forced), rule = new Set(known ? forced : []), rows = Array.isArray(order) ? order : [];
    return { known, forced: rows.filter((r) => rule.has(r.box)).map((r) => r.box), also: rows.filter((r) => !rule.has(r.box)).map((r) => r.box) };
  }
  // Of the first `landed` boxes in the order, how many each way.
  function landedSplit(order, split, landed) {
    const rule = new Set(split.forced);
    let forced = 0, also = 0;
    for (const row of order.slice(0, landed)) { if (rule.has(row.box)) forced++; else also++; }
    return { forced, also };
  }
  // The gap between two plans in extra lifts, never its cause: lifts move with the search run, so no gap is the rule's cost (logic3, B1).
  function liftGapLine(before, after, mixed) {
    if (typeof before !== 'number' || typeof after !== 'number') return '';
    const gap = Math.abs(after - before), n = Number.isInteger(gap) ? gap : gap.toFixed(1);
    const head = after > before ? `${n} more extra lifts than the plan before` : after < before ? `${n} fewer extra lifts than the plan before` : 'Same extra lifts as the plan before';
    return `${head}. ${mixed ? 'Different engines and search runs' : 'Different search runs'}: not a measured cost of the rule`;
  }
  // Which flights have started and landed at tau, and where the ones in the air are. Pure.
  function morphAt(order, from, to, flight, tau) {
    const n = order.length, gap = n > 1 ? (to - from - flight) / (n - 1) : 0, flyers = [];
    let started = 0, landed = 0;
    for (let i = 0; i < n; i++) {
      const p = (tau - from - i * gap) / flight;
      if (p >= 0) started = i + 1;
      if (p >= 1) landed = i + 1; else if (p >= 0) flyers.push({ box: order[i].box, from: order[i].from, to: order[i].to, p });
    }
    return { started, landed, flyers };
  }
  // The ship map with the first `started` boxes off their old slots and the first `landed` on their new ones.
  function morphShip(base, order, started, landed) {
    const ship = new Map(base);
    for (let i = 0; i < started; i++) if (ship.get(order[i].from) === order[i].box) ship.delete(order[i].from);
    for (let i = 0; i < landed; i++) ship.set(order[i].to, order[i].box);
    return ship;
  }
  // The nearest measured frame at or before lift k, as stage.js reads it.
  function frameAt(frames, k) {
    let best = null;
    for (const f of frames || []) if (f.step <= k && (!best || f.step >= best.step)) best = f;
    return best;
  }
  // Gauges in the script's plain words, one measured frame's numbers each. Nothing here decides a limit: red is the frame's own verdict.
  function plainGauges(f) {
    const n = (v, d) => (typeof v === 'number' && isFinite(v) ? v.toFixed(d) : '—'), x = f || {};
    return {
      stability: { text: `GM ${n(x.gm_m, 2)} m`, bad: x.stability_passed === false },
      balance: { text: `${n(Math.abs(x.heel_deg), 1)}° list · ${n(Math.abs(x.trim_m), 2)} m trim`, bad: x.stability_passed === false },
      draught: { text: `${n(x.draught_mean_m, 2)} of ${n(x.max_draught_m, 2)} m`, bad: typeof x.draught_mean_m === 'number' && x.draught_mean_m > x.max_draught_m },
      hull: { text: typeof x.max_bm_ratio === 'number' ? `${Math.round(100 * Math.max(x.max_bm_ratio, x.max_sf_ratio || 0))} % of limit` : '—', bad: x.strength_passed === false },
    };
  }
  const familiesShown = (n, from, per, tau) => (tau < from ? 0 : Math.min(n, Math.floor((tau - from) / per) + 1));

  // Mean lifts: both bars share one scale from zero, the larger mean; the two means come only as a pair.
  function liftsCard(card, cfg, fillData) {
    const vals = (card.rows || []).map((row) => number(row.key, cfg, fillData)), both = vals.every((v) => v != null && v > 0);
    const max = both ? Math.max(...vals) : 0;
    const head = cell(card.headline, cfg, fillData);
    const rows = (card.rows || []).map((row, i) => ({ label: row.label, own: !!row.own, at: +row.at || 0, value: both ? vals[i] : null, text: both ? value(row.key, cfg, fillData).value : null, width_pct: both ? (100 * vals[i]) / max : null, pending: both ? null : card.pending }));
    return { eyebrow: card.eyebrow, headline: head.missing.length ? null : head.text, pending: card.pending, rows, limits: card.limits || [], who: card.who || '', two_step: card.two_step || '', complete: both && !head.missing.length };
  }
  function worthCard(card, cfg, fillData) {
    const head = cell(card.headline, cfg, fillData), range = cell(card.range, cfg, fillData), sub = cell(card.sub || '', cfg, fillData), note = cell(card.note || '', cfg, fillData);
    const ok = !head.missing.length && !range.missing.length && !sub.missing.length && !note.missing.length;
    return { eyebrow: card.eyebrow, pending: card.pending, headline: ok ? head.text : null, sub: ok ? sub.text : '', range: ok ? range.text : null, note: ok ? note.text : '', complete: ok };
  }
  // v6 shot 12's projection: the exposure (big) and the pool (headline), each with its line under it, then the fine print. Whole or absent.
  function pilotCard(card, cfg, fillData) {
    const part = (t) => cell(t || '', cfg, fillData), fine = (card.fine || []).map(part);
    const parts = { big: part(card.big), big_sub: part(card.big_sub), headline: part(card.headline), sub: part(card.sub) };
    const ok = [...Object.values(parts), ...fine].every((x) => !x.missing.length) && !!parts.big.text && !!parts.headline.text;
    const out = { eyebrow: card.eyebrow, pending: card.pending, complete: ok, fine: ok ? fine.map((x) => x.text) : [] };
    for (const [k, x] of Object.entries(parts)) out[k] = ok ? x.text : null;
    return out;
  }

  return { chooseVariant, resolveVariant, figureValues, readLive, applyFallbacks, chapterAt, shotChapters, hookHits, ifBlocks, findShot, value, cell, number, matchPlan, shownCase, resolveShot, approved, conditionHolds, captionText, sentenceFor, briefAt, stepModels, sparkLines, litIndex, barCard, liveCard, finalSlots, bayDiff, morphOrder, forcedSplit, landedSplit, liftGapLine, morphAt, morphShip, familiesShown, frameAt, plainGauges, liftsCard, worthCard, pilotCard, PLACEHOLDER };
});
