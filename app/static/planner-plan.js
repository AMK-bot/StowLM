/* Plan: the real stored plans and checker walk, presented on the 2D bay work surface. */
(function () {
  'use strict';
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const number = (value, digits = 0) => finite(value) ? value.toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits }) : '—';
  function replanQuery(confirmation) {
    if (!confirmation || !confirmation.token) return 'replan?recorded=1';
    const query = new URLSearchParams({ token: confirmation.token });
    if (confirmation.reading) query.set('reading', confirmation.reading);
    return 'replan?' + query;
  }
  const slotKey = (slot) => [slot.bay, slot.row, slot.tier].join(':');
  function changedBoxes(before, after) {
    const old = new Map((before.slots || []).map((slot) => [slot.box_id, slotKey(slot)]));
    return (after.slots || []).filter((slot) => old.get(slot.box_id) !== slotKey(slot)).map((slot) => slot.box_id);
  }
  const frameAt = (frames, move) => (frames || []).reduce((best, frame) => finite(frame.step) && frame.step <= move && (!best || frame.step >= best.step) ? frame : best, null);
  function playbackIndex(move, index, moves) {
    const row = move && move.move ? move.move : move;
    if (row && typeof row === 'object') {
      const at = moves.findIndex((candidate) => candidate === row || (candidate.i != null && candidate.i === row.i));
      if (at >= 0) return at + 1;
    }
    if (Number.isInteger(index)) return Math.max(0, Math.min(moves.length, index));
    if (Number.isInteger(row)) return Math.max(0, Math.min(moves.length, row));
    return null;
  }
  function familyPassed(family) {
    const rules = family.rules || [];
    return family.status === 'pass' && rules.length > 0 && rules.every((rule) => ['pass', 'n/a'].includes(rule.status) && !(rule.violations > 0));
  }
  function planMatches(plan, visibleId, checked) {
    const meta = checked && checked.meta || {};
    return plan.plan_id === visibleId || (meta.plan_id === visibleId && meta.plan_ref === plan.plan_id);
  }
  function meterUpdate(frame, limits, at, total) {
    const f = frame || {}, L = limits || {};
    const gmLimits = [L.gm_min_m, f.gm_min_m].filter(finite), draftLimits = [L.draught_m, f.max_draught_m].filter(finite);
    const gmMin = gmLimits.length ? Math.max(...gmLimits) : null, draftMax = draftLimits.length ? Math.min(...draftLimits) : null;
    const heel = finite(f.heel_deg) ? Math.abs(f.heel_deg) : null;
    const hullRatios = [f.max_sf_ratio, f.max_bm_ratio].filter(finite), hull = hullRatios.length ? Math.max(...hullRatios) : null;
    const trimOutside = finite(f.trim_m) && ((finite(L.trim_min_m) && f.trim_m < L.trim_min_m) || (finite(L.trim_max_m) && f.trim_m > L.trim_max_m));
    const heelRatio = finite(heel) && finite(L.heel_max_deg) && L.heel_max_deg > 0 ? heel / L.heel_max_deg : null;
    const trimBound = finite(f.trim_m) && f.trim_m < 0 ? L.trim_min_m : L.trim_max_m;
    const trimRatio = finite(f.trim_m) && finite(trimBound) && trimBound !== 0 ? Math.abs(f.trim_m / trimBound) : null;
    const listRatios = [heelRatio, trimRatio].filter(finite);
    const title = frame ? [f.criteria || 'Recorded checker frame', f.error].filter(Boolean).join(' · ') : 'No measured checker frame at this move';
    const state = (value, passed, outside) => !finite(value) ? 'empty' : f.error ? 'warn' : passed === false || outside ? 'fail' : passed === true ? 'ok' : 'info';
    return {
      stability: { value: finite(f.gm_m) ? 'GM ' + number(f.gm_m, 2) + ' m' : null, limit: finite(gmMin) ? 'minimum ' + number(gmMin, 2) + ' m' : '', ratio: finite(gmMin) && f.gm_m > 0 ? gmMin / f.gm_m : null, state: state(f.gm_m, f.stability_passed, finite(gmMin) && f.gm_m < gmMin), title },
      list: { value: finite(heel) ? number(heel, 1) + '° · ' + (finite(f.trim_m) ? number(Math.abs(f.trim_m), 2) + ' m trim' : '— trim') : null, limit: finite(L.heel_max_deg) ? 'list limit ' + number(L.heel_max_deg, 1) + '°' : '', ratio: listRatios.length ? Math.max(...listRatios) : null, state: state(heel, f.stability_passed, (finite(heelRatio) && heelRatio > 1) || trimOutside), title: [title, finite(L.trim_min_m) && finite(L.trim_max_m) ? 'trim limits ' + number(L.trim_min_m, 2) + ' to ' + number(L.trim_max_m, 2) + ' m' : ''].filter(Boolean).join(' · ') },
      draught: { value: finite(f.draught_mean_m) ? number(f.draught_mean_m, 2) + ' m' : null, limit: finite(draftMax) ? 'limit ' + number(draftMax, 2) + ' m' : '', ratio: finite(f.draught_mean_m) && draftMax > 0 ? f.draught_mean_m / draftMax : null, state: state(f.draught_mean_m, f.stability_passed, finite(draftMax) && f.draught_mean_m > draftMax), title },
      hull: { value: finite(hull) ? number(hull * 100) + ' %' : null, limit: 'limit 100 %', ratio: hull, state: state(hull, f.strength_passed, finite(hull) && hull > 1), title },
      moves: { at, total },
    };
  }
  function actionBays(slots, changed, forced) {
    const moved = new Set(changed), rule = new Set(forced), scores = new Map();
    for (const slot of slots || []) if (moved.has(slot.box_id)) scores.set(slot.bay, (scores.get(slot.bay) || 0) + (rule.has(slot.box_id) ? 2 : 1));
    return [...scores].sort((a, b) => b[1] - a[1] || Number(a[0]) - Number(b[0])).slice(0, 3).map(([bay]) => bay);
  }
  const playbackSpeed = (total, speed = 1) => Math.max(1, total * 320 / 6500) * (speed > 0 ? speed : 1);
  const payloadFields = ['gate.status', 'gate.before.plan_id', 'gate.after.plan_id', 'gate.after.passed', 'gate.after.engine', 'gate.boxes_moved', 'gate.forced_boxes', 'gate.recheck', 'rule.text', 'rule_counts.active', 'gauges.line', 'facts.forced_ids', 'facts.forced', 'facts.rearranged', 'facts.lifts_after', 'facts.plan_date', 'facts.peak_heel_deg', 'facts.caveat', 'reading_gate.shown', 'reading_gate.rule', 'reading_gate.label', 'reading_gate.why', 'heel.peak_deg', 'heel.limit_deg'];
  const dataFields = {
    'plan-before': ['plan_id', 'label', 'slots[].box_id', 'slots[].bay', 'slots[].row', 'slots[].tier', 'source'],
    'plan-after': ['plan_id', 'label', 'slots[].box_id', 'slots[].bay', 'slots[].row', 'slots[].tier', 'source'],
    moves: ['moves[].i', 'moves[].kind', 'moves[].box_id', 'moves[].from', 'moves[].to', 'moves[].forced', 'source'],
    ship: ['limits.gm_min_m', 'limits.heel_max_deg', 'limits.draught_m', 'limits.trim_min_m', 'limits.trim_max_m'],
  };
  window.Planner.stage({
    id: 'plan', order: 30, title: 'Plan', features: ['replan'],
    payloadFields, dataFields, logic: { replanQuery, changedBoxes, frameAt, playbackIndex, familyPassed, planMatches, meterUpdate, actionBays, playbackSpeed },
    mount(panel, data, ctx) {
      const esc = ctx.esc;
      panel.innerHTML = `<div class="pl-plan-view">
        <div class="pl-plan-rules-count pl-plan-meta"></div><div class="pl-plan-families"></div><div class="pl-plan-heel pl-plan-meta"></div>
        <div class="pl-plan-notice pl-plan-meta" role="status" hidden></div>
        <details class="pl-plan-details"><summary>Details</summary><section class="pl-plan-rule"><div class="pl-plan-meta pl-plan-rule-label">Confirmed rule</div><div class="pl-plan-rule-text"></div></section><div class="pl-plan-actors"></div><div class="pl-plan-also pl-plan-meta"></div><div class="pl-plan-gauges pl-plan-meta"></div><div class="pl-plan-lifts"></div><div class="pl-plan-meta">Extra lifts, including projected later-port lifts</div><p class="pl-plan-caveat pl-plan-meta"></p><div class="pl-plan-walk-note pl-plan-meta"></div><pre class="pl-plan-sources"></pre></details>
      </div>`;
      const q = (selector) => panel.querySelector(selector);
      const result = document.createElement('div');
      result.className = 'pl-plan-result-view'; result.hidden = true;
      result.innerHTML = `<div class="pl-plan-result-summary"><div class="pl-plan-summary-copy" aria-live="polite"><span class="pl-plan-counter">Move 0 of 0</span> · <span class="pl-plan-forced"></span> · <span class="pl-plan-check"></span></div> <button type="button" class="pl-plan-replay">Regenerate</button></div>
        <div class="pl-plan-provenance pl-plan-meta"></div>`;
      const r = (selector) => result.querySelector(selector);
      let payload = data.replan, before = null, after = null, moveData = null, limits = {}, frames = [], families = [], changed = [], forced = [], prepared = null, preparedKey = '', preparedReady = false, requested = false, active = false, generation = 0, running = null, completed = false, announced = false, playing = false;
      const bay = () => ctx.bay;
      const setPlan = (plan) => typeof ctx.setPlan === 'function' ? ctx.setPlan(plan) : bay().setPlan(plan);
      const currentQuery = () => replanQuery(ctx.state.confirm);
      function attachResult(show) {
        const host = (typeof ctx.slot === 'function' && ctx.slot('result')) || (document.getElementById && document.getElementById('pl-result'));
        if (host) { if (result.parentNode !== host) host.appendChild(result); if (show) host.hidden = false; }
        else if (typeof ctx.result === 'function') ctx.result(result);
        if (show) result.hidden = false;
      }
      function notice(text) { q('.pl-plan-notice').textContent = text || ''; q('.pl-plan-notice').hidden = !text; }
      function labels() {
        const gate = payload.gate || {}, facts = payload.facts || {}, said = payload.reading_gate || {}, confirmation = ctx.state.confirm || {};
        const confirmedRule = typeof confirmation.rule === 'string' ? confirmation.rule : (confirmation.rule || {}).text;
        q('.pl-plan-rule-label').textContent = confirmation.token ? 'Confirmed rule' : 'Recorded rule';
        q('.pl-plan-rule-text').textContent = confirmedRule || confirmation.text || said.rule || (payload.rule || {}).text || '';
        r('.pl-plan-provenance').textContent = ['Stored plan' + (facts.plan_date ? ', computed on ' + facts.plan_date : ''), gate.recheck].filter(Boolean).join(', ');
        const engine = (gate.after || {}).engine;
        q('.pl-plan-actors').innerHTML = (engine ? `<span class="pl-plan-actor"><i class="pl-plan-engine-dot" aria-hidden="true"></i>${esc('Engine ' + engine + ' · search')}</span>` : '') + (said.label ? `<span class="pl-plan-recorded">${esc(said.label)}</span>` : '');
        q('.pl-plan-gauges').textContent = (payload.gauges || {}).line || '';
        q('.pl-plan-caveat').textContent = facts.caveat || '';
        r('.pl-plan-forced').textContent = finite(facts.forced) ? number(facts.forced) + ' forced by the rule' : forced.length ? number(forced.length) + ' forced by the rule' : '';
        q('.pl-plan-also').textContent = finite(facts.rearranged) ? number(facts.rearranged) + ' more · the search also rearranged these' : '';
        q('.pl-plan-rules-count').textContent = payload.rule_counts ? number(payload.rule_counts.active) + ' active rules' : '';
      }
      async function checkData() {
        if (Object.hasOwn(after, 'physics') && Object.hasOwn(after, 'rules') && after.physics && after.rules && after.plan_id === payload.gate.after.plan_id) return after;
        let result = null;
        for (let attempt = 0; attempt < 12; attempt++) {
          const response = await fetch('/api/stage?' + new URLSearchParams({ plan_id: payload.gate.after.plan_id }), { headers: { Accept: 'application/json' } });
          if (!response.ok) throw new Error('The checked plan data is unavailable.');
          result = await response.json();
          if (result.physics && result.rules) return result;
          await ctx.wait(500);
        }
        throw new Error('The checked plan data is not ready.');
      }
      function renderFamilies(landed) {
        q('.pl-plan-families').innerHTML = families.map((family) => `<div class="pl-plan-family"><span class="pl-plan-tick">${landed ? familyPassed(family) ? '✓' : '–' : '·'}</span><span>${esc(family.label || family.family || '')}</span></div>`).join('');
      }
      function updateMeters(n) {
        const frame = frameAt(frames, n);
        const update = meterUpdate(frame, limits, n, moveData.moves.length);
        const final = completed && !playing;
        update.rules = { value: final && payload.rule_counts ? number(payload.rule_counts.active) + ' active' : 'Pending', limit: final ? 'full checker passed' : 'full-plan verdict after playback', state: final ? 'ok' : 'info', ratio: final ? 1 : 0 };
        ctx.meters(update);
        r('.pl-plan-counter').textContent = 'Move ' + number(n) + ' of ' + number(moveData.moves.length);
      }
      function highlight() {
        bay().highlight(forced, 'forced');
        const forcedSet = new Set(forced);
        bay().highlight(changed.filter((box) => !forcedSet.has(box)), 'moved');
      }
      async function prepare(starting = false) {
        const query = currentQuery();
        if (prepared && preparedKey === query) return prepared;
        if (preparedKey && preparedKey !== query) { completed = false; announced = false; requested = starting; preparedReady = false; result.hidden = true; }
        preparedKey = query;
        prepared = (async () => {
          const values = await Promise.all([ctx.api(query), ctx.data('plan-before'), ctx.data('plan-after'), ctx.data('moves'), ctx.ship ? Promise.resolve(ctx.ship) : ctx.data('ship')]);
          [payload, before, after, moveData] = values;
          limits = (values[4] || {}).limits || {};
          forced = (payload.facts || {}).forced_ids || (payload.gate || {}).forced_boxes || [];
          labels();
          const gate = payload.gate || {}, said = payload.reading_gate || {};
          if (gate.status !== 'shown' || said.shown !== true) { notice(said.why || 'Confirm this rule before replanning.'); return false; }
          changed = changedBoxes(before, after);
          if (changed.length !== gate.boxes_moved || !moveData.moves.length) { notice('The stored moves do not match the checked bay plans.'); return false; }
          const checked = await checkData();
          if (!planMatches(after, gate.after.plan_id, checked)) { notice('The stored bay plan does not match this checked rule.'); return false; }
          frames = (checked.physics || {}).frames || [];
          families = (checked.rules || {}).families || [];
          q('.pl-plan-sources').textContent = JSON.stringify({ before: { label: before.label, source: before.source }, after: { label: after.label, source: after.source }, moves: moveData.source }, null, 2);
          q('.pl-plan-walk-note').textContent = 'Meters follow the checked crane-sequence frames.';
          renderFamilies(false);
          notice('');
          r('.pl-plan-counter').textContent = 'Move 0 of ' + number(moveData.moves.length);
          preparedReady = true;
          return true;
        })().catch(() => { prepared = null; preparedReady = false; notice('The stored plan data is not ready. Generate the plan to try again.'); return false; });
        return prepared;
      }
      function finish() {
        if (!requested || !preparedReady || !after || !bay()) return;
        playing = false;
        const passed = payload.gate.after.passed === true, facts = payload.facts || {};
        completed = passed;
        // This loads an already checked stored plan; playback never issues a checker verdict.
        setPlan(after); highlight(); updateMeters(moveData.moves.length); renderFamilies(true);
        const focused = actionBays(after.slots, changed, forced);
        if (focused.length && typeof bay().showBays === 'function') bay().showBays(focused);
        r('.pl-plan-check').textContent = 'Full checker: ' + (passed ? 'passed' : 'not passed');
        q('.pl-plan-lifts').textContent = number(facts.lifts_after);
        const peak = payload.heel ? payload.heel.peak_deg : facts.peak_heel_deg;
        q('.pl-plan-heel').textContent = finite(peak) ? 'Peak heel ' + number(peak, 2) + '°' + (payload.heel ? ' · limit ' + number(payload.heel.limit_deg, 2) + '°' : '') : '';
        r('.pl-plan-replay').disabled = false;
        attachResult(true);
        if (passed && !announced) {
          announced = true;
          ctx.log({ who: 'Engine 0.13.0', where: 'on this laptop', text: 'Loaded the stored plan under this rule.', label: r('.pl-plan-provenance').textContent });
          ctx.log({ who: 'Full checker', where: 'on this laptop', text: [r('.pl-plan-check').textContent, q('.pl-plan-rules-count').textContent].filter(Boolean).join(' · '), label: payload.gate.recheck || 'Stored plan' });
          ctx.emit('plan:replanned', { plan_id: after.plan_id, rule: q('.pl-plan-rule-text').textContent });
          ctx.done();
        }
      }
      function play() {
        if (running) return running;
        requested = true;
        const ticket = generation;
        running = (async () => {
          if (!await prepare(true) || !active || ticket !== generation || !bay()) return;
          // A changed confirmation resets preparation; this explicit Generate/Regenerate still owns the new run.
          requested = true; playing = true;
          attachResult(true);
          r('.pl-plan-check').textContent = 'Playing the checked stored plan';
          r('.pl-plan-replay').disabled = true;
          const arrival = Object.hasOwn(moveData, 'initial_slots') ? Object.assign({}, before, { slots: moveData.initial_slots }) : before;
          setPlan(arrival); highlight(); renderFamilies(false); updateMeters(0);
          const focused = actionBays(after.slots, changed, forced);
          if (focused.length && typeof bay().showBays === 'function') bay().showBays(focused);
          let count = 0, deadlineHit = false;
          const urlSpeed = Number(new URLSearchParams(window.location ? window.location.search : '').get('speed'));
          const takeSpeed = finite(ctx.speed) && ctx.speed > 0 ? ctx.speed : urlSpeed > 0 ? urlSpeed : 1;
          const deadline = setTimeout(() => {
            if (!active || ticket !== generation) return;
            deadlineHit = true;
            // setPlan cancels the bay player's token; await its real cancellation below before finishing.
            setPlan(after);
          }, 10000 / takeSpeed);
          let playback;
          try {
            playback = await bay().play(moveData.moves, { speed: playbackSpeed(moveData.moves.length, takeSpeed), onMove(move, index) {
              if (!active || ticket !== generation) return;
              count = playbackIndex(move, index, moveData.moves) ?? Math.min(count + 1, moveData.moves.length);
              updateMeters(count);
            } });
          } finally { clearTimeout(deadline); }
          if (!active || ticket !== generation || (playback && playback.cancelled && !deadlineHit)) return;
          finish();
        })().finally(() => { running = null; r('.pl-plan-replay').disabled = false; });
        return running;
      }
      function start() { return running || (completed && preparedKey === currentQuery() ? Promise.resolve() : play()); }
      r('.pl-plan-replay').addEventListener('click', async () => {
        if (!active && typeof window.Planner.go === 'function') await window.Planner.go('plan');
        if (active) return play();
      });
      labels();
      return {
        result,
        async enter() { active = true; attachResult(false); const ready = await prepare(); if (active && ready && completed) finish(); },
        leave() {
          active = false; generation++;
          if (!requested) return;
          if (preparedReady) finish();
          else if (prepared) {
            const key = preparedKey;
            prepared.then((ready) => { if (ready && !active && requested && key === preparedKey) finish(); });
          }
        },
        start,
        async autoplay(auto) { await start(); await auto.wait(1500); },
      };
    },
  });
})();
