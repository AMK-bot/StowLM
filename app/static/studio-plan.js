/* Studio Plan. The checker owns the verdict; the stored bundles own every moving box. */
(function () {
  'use strict';
  const clamp = (x) => Math.max(0, Math.min(1, x));
  const num = (x, digits) => typeof x === 'number' && Number.isFinite(x) ? x.toFixed(digits) : '—';
  function finalSlots(bundle) {
    const at = new Map();
    for (const row of (bundle.initial || {}).ship || []) if (row.box) at.set(row.box, { slot: row.slot, cells: row.cells || [row.slot] });
    for (const step of bundle.steps || []) {
      if (!step.box) continue;
      if (/^\d{7}$/.test(String(step.dst))) at.set(step.box, { slot: step.dst, cells: step.slot_cells && step.slot_cells.length ? step.slot_cells : [step.dst] });
      else if (/^\d{7}$/.test(String(step.src))) at.delete(step.box);
    }
    return at;
  }
  // Same dependency order as StageStory.morphOrder: clear occupied target cells first.
  function morphOrder(before, after, forced) {
    const a = finalSlots(before), b = finalSlots(after), sitting = new Map();
    const moved = [...b.keys()].filter((box) => !a.has(box) || a.get(box).slot !== b.get(box).slot);
    if (!a.size || !b.size || moved.some((box) => !a.has(box))) return null;
    for (const box of moved) for (const cell of a.get(box).cells) sitting.set(cell, box);
    const left = new Set(moved), order = [];
    const fly = (box) => { left.delete(box); order.push({ box, from: a.get(box).slot, to: b.get(box).slot }); };
    const clear = (box, seen) => {
      if (!left.has(box) || seen.has(box)) return;
      seen.add(box);
      for (const cell of b.get(box).cells) if (sitting.has(cell) && sitting.get(cell) !== box) clear(sitting.get(cell), seen);
      fly(box);
    };
    for (const box of forced || []) clear(box, new Set());
    while (left.size) fly([...left].find((box) => b.get(box).cells.every((cell) => !sitting.has(cell) || sitting.get(cell) === box || !left.has(sitting.get(cell)))) || left.values().next().value);
    return { order, base: [...a].map(([box, row]) => [row.slot, box]) };
  }
  function morphAt(order, seconds) {
    const gap = order.length > 1 ? 7.2 / (order.length - 1) : 0;
    let started = 0, landed = 0;
    const flyers = [];
    order.forEach((row, i) => {
      const raw = (seconds - i * gap) / 0.8, p = raw > 1 - 1e-9 ? 1 : raw;
      if (p >= 0) started = i + 1;
      if (p >= 1) landed = i + 1;
      else if (p >= 0) flyers.push({ ...row, p });
    });
    return { started, landed, flyers };
  }
  const frameAt = (bundle, k) => ((bundle.physics || {}).frames || []).reduce((best, frame) => frame.step <= k && (!best || frame.step >= best.step) ? frame : best, null);
  function replanQuery(confirmation) {
    if (!confirmation || !confirmation.token) return 'replan?recorded=1';
    const query = new URLSearchParams({ token: confirmation.token });
    if (confirmation.reading) query.set('reading', confirmation.reading);
    return 'replan?' + query;
  }
  const payloadFields = ['gate.status', 'gate.before.plan_id', 'gate.after.plan_id', 'gate.after.passed', 'gate.after.engine', 'gate.boxes_moved', 'gate.forced_boxes', 'gate.recheck', 'gate.computed_utc', 'rule.text', 'rule_counts.active', 'gauges.line', 'facts.forced_ids', 'facts.plan_date', 'facts.peak_heel_deg', 'facts.caveat', 'reading_gate.shown', 'reading_gate.label', 'reading_gate.rule', 'reading_gate.why', 'heel.peak_deg', 'heel.limit_deg'];
  window.Studio.step({
    id: 'plan', order: 30, title: 'Plan', icon: 'plan', features: ['replan'], ship: true,
    payloadFields, logic: { replanQuery, finalSlots, morphOrder, morphAt },
    mount(panel, data, ctx) {
      const esc = ctx.esc;
      panel.innerHTML = `<div class="st-plan-view">
        <div class="st-card st-plan-rule"><div class="st-meta st-plan-rule-label">Confirmed rule</div><div class="st-card-title st-plan-rule-text"></div></div>
        <div class="st-plan-labels"></div>
        <div class="st-plan-progress"><div class="st-plan-progress-head"><span class="st-meta st-plan-counter" aria-live="polite">0 of 0 moves</span><button type="button" class="st-btn ghost st-plan-replay" hidden>Replay</button></div><div class="st-card-sub st-plan-forced"></div><div class="st-meta st-plan-also"></div></div>
        <div class="st-meta st-plan-gauge-note"></div><div class="st-plan-rules"></div>
        <div class="st-plan-result" hidden><div class="st-callout st-plan-check"></div><div class="st-meta st-plan-heel"></div></div>
        <details class="st-disclosure st-plan-details"><summary>Details</summary><div class="st-meta st-plan-note"></div></details></div>`;
      const q = (selector) => panel.querySelector(selector);
      let payload = data.replan, before = null, after = null, diff = null, ready = null, readyKey = '', active = false, epoch = 0, running = null, completed = false;
      const stage = () => ctx.stage || window.__stage;
      const forcedIds = () => (payload.facts || {}).forced_ids || (payload.gate || {}).forced_boxes || [];
      const shown = () => (payload.gate || {}).status === 'shown' && (payload.reading_gate || {}).shown === true;
      function labels() {
        const gate = payload.gate || {}, facts = payload.facts || {}, confirmation = ctx.state.confirm || {};
        const confirmedRule = typeof confirmation.rule === 'string' ? confirmation.rule : (confirmation.rule || {}).text;
        q('.st-plan-rule-text').textContent = confirmedRule || confirmation.text || (payload.reading_gate || {}).rule || (payload.rule || {}).text || '';
        q('.st-plan-rule-label').textContent = confirmation.token ? 'Confirmed rule' : 'Recorded rule';
        const date = facts.plan_date || (gate.computed_utc ? new Date(gate.computed_utc).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '');
        const stored = 'Stored plan' + (date ? ', computed on ' + date : '') + (gate.recheck ? ', ' + gate.recheck : '');
        const engine = (gate.after || {}).engine;
        q('.st-plan-labels').innerHTML = `<div class="st-meta st-plan-provenance">${esc(stored)}</div><div class="st-plan-credits">` + ((payload.reading_gate || {}).label ? ctx.chip(payload.reading_gate.label, 'recorded') : '') + (engine ? `<span class="st-chip"><i class="st-plan-engine-dot" aria-hidden="true"></i>${esc('Engine ' + engine + ' · search')}</span>` : '') + '</div>';
        q('.st-plan-gauge-note').textContent = (payload.gauges || {}).line || '';
        q('.st-plan-note').textContent = shown() ? facts.caveat || '' : (payload.reading_gate || {}).why || '';
      }
      async function bundle(id) {
        for (let attempt = 0; attempt < 20; attempt++) {
          const response = await fetch('/api/stage?' + new URLSearchParams({ plan_id: id }), { headers: { Accept: 'application/json' } });
          if (!response.ok) throw new Error('The stored ship plan is unavailable.');
          const loaded = await response.json();
          if (loaded.physics) return loaded;
          await ctx.wait(500);
        }
        throw new Error('The stored physics frames are unavailable.');
      }
      function ruleRows() {
        const families = (after.rules || {}).families || [];
        q('.st-plan-rules').innerHTML = families.map((family) => {
          const rules = family.rules || [];
          const pass = rules.length && rules.every((rule) => ['pass', 'n/a'].includes(rule.status) && !(rule.violations > 0)) && family.status === 'pass';
          return `<div class="st-meta st-plan-rule-row" data-passed="${pass ? '1' : '0'}"><span class="st-plan-tick">·</span><span>${esc(family.label || family.family || '')}</span></div>`;
        }).join('');
      }
      function install() {
        const st = stage();
        if (!st || !after || !diff) return false;
        if (st.B !== after) st.load(after, 'end');
        st.setPlaying(false);
        st.jumpTo((after.steps || []).length);
        st.peek(0);
        return true;
      }
      function draw(seconds) {
        const st = stage(), m = morphAt(diff.order, seconds), total = (after.steps || []).length;
        const k = Math.floor(clamp(seconds / 8) * total), ship = new Map(diff.base), forced = new Set(forcedIds());
        for (let i = 0; i < m.started; i++) if (ship.get(diff.order[i].from) === diff.order[i].box) ship.delete(diff.order[i].from);
        for (let i = 0; i < m.landed; i++) ship.set(diff.order[i].to, diff.order[i].box);
        const state = Object.assign({}, st.stateAt(total), { ship, buffer: [], coversOff: new Set() });
        const quiet = new Set(diff.order.slice(0, m.landed).filter((row) => !forced.has(row.box)).map((row) => row.box));
        const marks = diff.order.flatMap((row, i) => !forced.has(row.box) || (i < m.started && i >= m.landed) ? [] : [{ box: row.box, slot: i < m.landed ? row.to : row.from }]);
        if (st.scene && st.scene.setMorph) st.scene.setMorph({ state, glow: forced, quiet, marks, flyers: m.flyers });
        else st.seek(k, k === total || k === 0);
        st.peek(k);
        const frame = frameAt(after, k);
        if (frame) st.setGaugeFrame(frame);
        q('.st-plan-counter').textContent = k + ' of ' + total + ' moves';
        const forcedMoved = diff.order.slice(0, m.landed).filter((row) => forced.has(row.box)).length;
        const forcedTotal = diff.order.filter((row) => forced.has(row.box)).length;
        q('.st-plan-forced').textContent = forcedTotal ? forcedMoved + ' of ' + forcedTotal + ' boxes the rule forced out' : m.landed + ' boxes moved';
        const also = m.landed - forcedMoved;
        q('.st-plan-also').textContent = also ? also + ' · the search also rearranged these' : '';
        st.redraw();
      }
      async function preload() {
        const query = replanQuery(ctx.state.confirm);
        if (ready && readyKey === query) return ready;
        if (readyKey && readyKey !== query) { completed = false; q('.st-plan-replay').hidden = true; q('.st-plan-result').hidden = true; }
        readyKey = query;
        ready = (async () => {
          payload = await ctx.api(query);
          labels();
          if (!shown()) return false;
          const gate = payload.gate;
          [before, after] = await Promise.all([bundle(gate.before.plan_id), bundle(gate.after.plan_id)]);
          diff = morphOrder(before, after, forcedIds());
          if (!diff || diff.order.length !== gate.boxes_moved) { diff = null; q('.st-plan-note').textContent = 'The stored plan could not be matched to its checked move list.'; return false; }
          ruleRows();
          q('.st-plan-counter').textContent = '0 of ' + (after.steps || []).length + ' moves';
          if (active && !completed && install()) draw(0);
          return true;
        })().catch(() => { q('.st-plan-note').textContent = 'The stored plan is not ready. Replay to try again.'; q('.st-plan-replay').hidden = false; ready = null; return false; });
        return ready;
      }
      function play() {
        if (running) return running;
        running = (async () => {
          const ticket = epoch;
          if (!await preload() || !active || ticket !== epoch) return;
          for (let tries = 0; !stage() && tries < 40; tries++) await ctx.wait(100);
          if (!active || ticket !== epoch || !install()) return;
          q('.st-plan-replay').hidden = false;
          q('.st-plan-replay').disabled = true;
          q('.st-plan-result').hidden = true;
          q('.st-plan-rules').querySelectorAll('.st-plan-rule-row').forEach((row) => { row.classList.remove('ok'); row.querySelector('.st-plan-tick').textContent = '·'; });
          for (let tick = 0; tick <= 160; tick++) {
            if (!active || ticket !== epoch) return;
            draw(tick / 20);
            if (tick < 160) await ctx.wait(50);
          }
          const rows = [...q('.st-plan-rules').querySelectorAll('.st-plan-rule-row')];
          for (const row of rows) {
            if (!active || ticket !== epoch) return;
            row.classList.toggle('ok', row.dataset.passed === '1'); row.querySelector('.st-plan-tick').textContent = row.dataset.passed === '1' ? '✓' : '–';
            await ctx.wait(100);
          }
          if (!active || ticket !== epoch) return;
          const passed = (payload.gate.after || {}).passed === true;
          q('.st-plan-check').textContent = 'Full checker: ' + (passed ? 'passed' : 'not passed') + (payload.rule_counts ? ' · ' + payload.rule_counts.active + ' active rules' : '');
          const peak = payload.heel ? payload.heel.peak_deg : (payload.facts || {}).peak_heel_deg;
          q('.st-plan-heel').textContent = typeof peak === 'number' ? 'Peak heel ' + num(peak, 2) + '°' + (payload.heel ? ' · limit ' + num(payload.heel.limit_deg, 2) + '°' : '') : '';
          q('.st-plan-result').hidden = false;
          if (passed && !completed) {
            completed = true;
            ctx.log({ who: 'Engine 0.13.0', where: 'on this laptop', text: 'Replayed the stored plan under this rule.', label: q('.st-plan-labels').textContent });
            ctx.log({ who: 'Full checker', where: 'on this laptop', text: q('.st-plan-check').textContent, label: (payload.gate || {}).recheck || 'Stored plan' });
            ctx.emit('plan:replanned', { plan_id: payload.gate.after.plan_id, rule: q('.st-plan-rule-text').textContent });
            ctx.done();
          }
        })().finally(() => { running = null; q('.st-plan-replay').disabled = false; });
        return running;
      }
      function start() {
        if (running) return running;
        if (completed && readyKey === replanQuery(ctx.state.confirm)) return Promise.resolve();
        return play();
      }
      q('.st-plan-replay').addEventListener('click', play);
      labels();
      return {
        async enter() { active = true; ctx.center(null); await preload(); if (active && completed && install()) { draw(8); q('.st-plan-replay').hidden = false; } },
        start,
        leave() { active = false; epoch++; q('.st-plan-replay').hidden = true; const st = stage(); if (st && st.scene && st.scene.setMorph) st.scene.setMorph(null); if (st && st.setGaugeFrame) st.setGaugeFrame(null); },
        async autoplay(auto) {
          await start();
          await auto.wait(5000);
        },
      };
    },
  });
})();
