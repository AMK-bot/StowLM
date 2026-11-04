/* One-box checks on the 2D bay plan; recorded answers retain their provenance. */
(function () {
  'use strict';
  if (!window.Planner) return;
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const number = (value, digits = 0) => finite(value) ? value.toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits }) : '—';
  function meterUpdate(frame, limits) {
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
    };
  }
  function slot(value) {
    if (typeof value === 'string') {
      const m = /^(\d{3})(\d{2})(\d{2})$/.exec(value);
      return m ? { bay: +m[1], row: +m[2], tier: +m[3], deck: +m[3] >= 80 } : null;
    }
    if (!value || !['bay', 'row', 'tier'].every((key) => Number.isFinite(+value[key]))) return null;
    return { bay: +value.bay, row: +value.row, tier: +value.tier, deck: +value.tier >= 80 };
  }
  function slotId(value) {
    const s = slot(value);
    return s ? String(s.bay).padStart(3, '0') + String(s.row).padStart(2, '0') + String(s.tier).padStart(2, '0') : null;
  }
  function finalSlots(bundle) {
    const placed = new Map((bundle.initial.ship || []).map((s) => [s.slot, s.box]));
    for (const move of bundle.steps || []) {
      if (!move.box) continue;
      for (const [where, id] of placed) if (id === move.box) placed.delete(where);
      if (/^\d{7}$/.test(String(move.dst))) placed.set(move.dst, move.box);
    }
    return [...placed].map(([where, box_id]) => ({ ...slot(where), box_id }));
  }
  function movedPlan(plan, drop, swapped) {
    const from = slotId(drop.from), to = slotId(drop.to);
    const target = plan.slots.find((s) => slotId(s) === to);
    const swap = swapped || (target && target.box_id !== drop.box_id ? target.box_id : null);
    const slots = plan.slots.filter((s) => s.box_id !== drop.box_id && slotId(s) !== to && !(swap && s.box_id === swap)).map((s) => ({ ...s }));
    slots.push({ ...slot(drop.to), box_id: drop.box_id });
    if (swap && from) slots.push({ ...slot(drop.from), box_id: swap });
    return { ...plan, slots };
  }
  function matchCase(cases, drop) {
    return cases.find((c) => c.box_id === drop.box_id && slotId(c.from) === slotId(drop.from) && slotId(c.to) === slotId(drop.to));
  }
  window.Planner.stage({
    id: 'stress', order: 40, title: 'Stress test', features: ['sandbox'],
    logic: { slot, slotId, finalSlots, movedPlan, matchCase, meterUpdate },
    dataFields: { stress: ['cases[].id', 'cases[].box_id', 'cases[].from', 'cases[].to', 'cases[].fast', 'cases[].full', 'cases[].rule', 'cases[].label', 'cases[].fast.legal', 'cases[].full.legal', 'cases[].fast.outcome.title', 'cases[].full.outcome.title', 'cases[].fast.outcome.plain', 'cases[].full.outcome.plain', 'cases[].fast.outcome.rule_id', 'cases[].full.outcome.rule_id', 'cases[].fast.rows[].name', 'cases[].full.rows[].name', 'cases[].fast.rows[].rule_id', 'cases[].full.rows[].rule_id', 'cases[].fast.rows[].message', 'cases[].full.rows[].message', 'cases[].fast.rows[].note', 'cases[].full.rows[].note', 'cases[].fast.rows[].diagnostic', 'cases[].full.rows[].diagnostic', 'cases[].fast.gauges.frame', 'cases[].full.gauges.frame', 'cases[].fast.gauges.diagnostic_only', 'cases[].full.gauges.diagnostic_only', 'cases[].full.gauges.swapped_box', 'cases[].fast.lifts', 'cases[].full.lifts', 'cases[].fast.scope', 'cases[].full.scope', 'cases[].fast.quality', 'cases[].full.quality', 'plan_id', 'label', 'slots[].bay', 'slots[].row', 'slots[].tier', 'slots[].box_id', 'cargo_overrides'] },
    payloadFields: ['cases[].kind', 'cases[].name', 'cases[].box', 'cases[].slot', 'replay', 'running_label', 'challenge_note', 'challenge_tag', 'derived', 'stored_label'],
    mount(panel, data, ctx) {
      const raw = data.sandbox, esc = ctx.esc;
      panel.innerHTML = `<section class="pl-stress-view">
        <p class="pl-stress-guidance">Drag a container to another slot. The fast screen answers first; the full checker decides.</p>
        <div class="pl-stress-cases" hidden></div>
        <div class="pl-stress-verdict" aria-live="polite" hidden></div>
        <p class="pl-stress-status" role="status"></p>
        <p class="pl-stress-note">${esc(raw.challenge_note)}</p>
        <details class="pl-stress-details"><summary>Details</summary><p>${esc(raw.challenge_tag)}</p>${raw.derived ? `<p>${esc(raw.derived)}</p>` : ''}<p>${esc(raw.stored_label)}</p></details>
      </section>`;
      const $ = (name) => panel.querySelector(name);
      let entered = false, life = 0, loaded = Promise.resolve(), mapped = null, baseline = null, restorePlan = null, current = null, active = null, ready = false, completed = false;
      let reopened = [], limits = {};
      let baselineStrip = null, baselineHost = null, baselineHostHidden = false, hiddenResults = [];
      const setPlan = (plan) => typeof ctx.setPlan === 'function' ? ctx.setPlan(plan) : ctx.bay.setPlan(plan);
      const fullCases = new Map();
      const recordedLabel = raw.replay && raw.replay.label ? raw.replay.label : 'Recorded verdict';
      function showBaselineLabel() {
        if (baselineStrip) return;
        baselineStrip = document.createElement('div');
        baselineStrip.className = 'pl-stress-baseline';
        baselineStrip.textContent = 'Stress-test baseline (separate stored plan)';
        baselineStrip.setAttribute('role', 'status');
        baselineHost = (typeof ctx.slot === 'function' && ctx.slot('result')) || (document.getElementById && document.getElementById('pl-result'));
        if (baselineHost) {
          baselineHostHidden = baselineHost.hidden;
          hiddenResults = Array.from(baselineHost.querySelectorAll('.pl-plan-result-view')).map((node) => ({ node, hidden: node.hidden }));
          hiddenResults.forEach(({ node }) => { node.hidden = true; });
          baselineHost.appendChild(baselineStrip); baselineHost.hidden = false;
        } else {
          const bayHost = document.getElementById && document.getElementById('pl-bay');
          if (bayHost && bayHost.parentNode) bayHost.parentNode.insertBefore(baselineStrip, bayHost);
        }
      }
      function hideBaselineLabel() {
        if (baselineStrip) { baselineStrip.hidden = true; baselineStrip.remove(); }
        hiddenResults.forEach(({ node, hidden }) => { node.hidden = hidden; });
        if (baselineHost) baselineHost.hidden = baselineHostHidden;
        baselineStrip = null; baselineHost = null; hiddenResults = [];
      }
      function finish(run, result) {
        if (run.resolve) { run.resolve(result); run.resolve = null; }
      }
      function cancelRun() {
        if (active) { active.cancelled = true; finish(active, { cancelled: true }); clearTimeout(active.pollTimer); if (active.abort) active.abort.abort(); }
        active = null;
      }
      function reset() {
        cancelRun();
        if (baseline && entered) { current = baseline; setPlan(baseline); ctx.bay.clearMarks(); }
        $('.pl-stress-verdict').hidden = true; $('.pl-stress-status').textContent = '';
      }
      function valid(run) { return entered && ready && active === run && !run.cancelled && run.life === life; }
      function nameOf(c) {
        const original = raw.cases.find((item) => item.kind === c.id || (item.box === c.box_id && item.slot === slotId(c.to)));
        return original ? original.name : c.label;
      }
      function ruleName(c, verdict) {
        const rule = c && c.rule;
        if (typeof rule === 'string' && rule && !/^[a-z_]+\./.test(rule)) return rule;
        if (rule && typeof rule === 'object' && (rule.name || rule.title)) return rule.name || rule.title;
        const rows = verdict.rows || [], deciding = rows.find((row) => row.rule_id === verdict.outcome.rule_id);
        return deciding ? deciding.name : verdict.outcome.title;
      }
      function showAnswer(run, verdict, fast, recorded) {
        if (!valid(run)) return;
        const o = verdict.outcome, tone = fast ? 'warn' : verdict.legal ? 'ok' : 'bad', card = $('.pl-stress-verdict');
        card.hidden = false; card.className = `pl-stress-verdict pl-stress-${tone}`;
        const rows = (verdict.rows || []).slice().sort((a, b) => +(b.rule_id === o.rule_id) - +(a.rule_id === o.rule_id));
        const label = recorded ? recordedLabel : fast ? raw.running_label : 'Full checker';
        card.innerHTML = `<div class="pl-stress-answer-head"><span class="pl-stress-result">${fast ? 'Fast screen' : verdict.legal ? 'Full checker · passed' : 'Full checker · refused'}</span><span class="pl-stress-label">${esc(label)}</span></div>
          <h3>${esc(o.title)}</h3><p>${esc(o.plain)}</p>
          ${fast ? `<p class="pl-stress-note">${esc(raw.running_label)}</p>` : ''}
          ${verdict.gauges && verdict.gauges.diagnostic_only ? '<p class="pl-stress-note">Meters show a diagnostic check of the requested stow.</p>' : ''}
          ${rows.map((row) => `<div class="pl-stress-rule"><b>${esc(row.name)}${row.diagnostic ? ' · Diagnostic snapshot' : ''}</b><details class="pl-stress-details"><summary>Details</summary><code>${esc(row.rule_id)}</code><p>${esc(row.message)}</p>${row.note ? `<p>${esc(row.note)}</p>` : ''}</details></div>`).join('')}
          ${verdict.lifts ? `<p class="pl-stress-note">${esc(verdict.lifts)}</p>` : ''}
          <p class="pl-stress-note">${esc(verdict.scope || '')} ${esc(verdict.quality || '')}</p>
          <button type="button" class="pl-stress-undo">Undo move</button>`;
        ctx.bay.clearMarks();
        if (!verdict.legal) ctx.bay.markViolation(run.drop.to, ruleName(run.kase, verdict));
        const frame = verdict.gauges && verdict.gauges.frame;
        // A one-box check changes physical readings, never the generated plan's move or rule counters.
        if (frame) ctx.meters(meterUpdate(frame, limits));
        if (!fast) {
          ctx.log({ who: 'Full checker', where: recorded ? null : 'on this laptop', text: o.title + ' · ' + o.plain, label });
          ctx.emit('stress:verdict', { box: run.drop.box_id, slot: slotId(run.drop.to), verdict });
          if (run.kase) fullCases.set(run.kase.id, verdict.legal);
          const results = [...fullCases.values()];
          if (!completed && verdict.legal && fullCases.size >= 2 && results.includes(false) && results.includes(true)) { completed = true; ctx.done(); }
          finish(run, { cancelled: false, verdict });
        }
      }
      async function recorded(run) {
        showAnswer(run, run.kase.fast, true, true);
        await ctx.wait(500);
        if (valid(run)) showAnswer(run, run.kase.full, false, true);
      }
      async function poll(run) {
        if (!valid(run)) return;
        try {
          run.abort = new AbortController();
          const response = await fetch('api/v6/try/' + encodeURIComponent(run.job), { signal: run.abort.signal, headers: { Accept: 'application/json' } });
          if (!response.ok) throw new Error('check unavailable');
          const job = await response.json(); if (!valid(run)) return;
          if (job.fast && !run.fastShown) { run.fastShown = true; showAnswer(run, job.fast, true, !!job.recorded); }
          if (job.full) {
            if (job.fast) await ctx.wait(500);
            if (valid(run)) showAnswer(run, job.full, false, !!job.recorded);
            return;
          }
          if (job.status === 'failed' || job.status === 'superseded') throw new Error('full check incomplete');
          run.pollTimer = setTimeout(() => poll(run), job.fast ? 400 : 120);
        } catch (_) {
          if (valid(run)) { $('.pl-stress-status').textContent = 'No full verdict returned. Undo this move or try a prepared check.'; finish(run, { cancelled: true }); }
        }
      }
      function onDrop(drop) {
        if (!entered || !ready || !drop || !slotId(drop.from) || !slotId(drop.to) || slotId(drop.from) === slotId(drop.to)) return Promise.resolve({ cancelled: true });
        cancelRun(); ctx.bay.clearMarks();
        const run = { drop: { box_id: drop.box_id, from: slot(drop.from), to: slot(drop.to) }, life, cancelled: false, kase: matchCase(mapped.cases, drop) };
        run.completion = new Promise((resolve) => { run.resolve = resolve; }); active = run;
        const swap = run.kase && run.kase.full.gauges ? run.kase.full.gauges.swapped_box : drop.swap_with || null;
        const nextPlan = movedPlan(current || baseline, run.drop, swap);
        $('.pl-stress-status').textContent = `${drop.box_id} → ${slotId(drop.to)}`;
        (async () => {
          try {
            const move = { kind: 'shift', box_id: drop.box_id, from: run.drop.from, to: run.drop.to };
            if (swap) move.swap_with = swap;
            const animation = await ctx.bay.play([move], { speed: 1 });
            if (!valid(run)) return;
            if (animation && animation.cancelled) { finish(run, { cancelled: true }); return; }
            current = nextPlan; setPlan(current);
            if (run.kase) await recorded(run);
            else {
              const job = await ctx.post('try', { box: drop.box_id, slot: slotId(drop.to) });
              if (!valid(run)) return;
              run.job = job.job; poll(run);
            }
          } catch (_) {
            if (valid(run)) { $('.pl-stress-status').textContent = 'No verdict returned. Undo this move or try a prepared check.'; finish(run, { cancelled: true }); }
          }
        })();
        return run.completion;
      }
      function choose(id) {
        if (!entered || !ready) return;
        const c = mapped.cases.find((item) => item.id === id); if (!c) return;
        reset(); ctx.bay.showBays([...new Set([c.from.bay, c.to.bay])]);
        onDrop({ box_id: c.box_id, from: c.from, to: c.to });
      }
      panel.addEventListener('click', (event) => {
        const button = event.target.closest('[data-pl-stress-case]');
        if (button) choose(button.dataset.plStressCase);
        if (event.target.closest('.pl-stress-undo')) reset();
      });
      async function enter() {
        if (entered) return loaded;
        entered = true; const mine = ++life;
        loaded = (async () => {
          const documents = await Promise.all([mapped ? Promise.resolve(mapped) : ctx.data('stress'), restorePlan ? Promise.resolve(restorePlan) : ctx.data('plan-after'), ctx.ship ? Promise.resolve(ctx.ship) : ctx.data('ship')]);
          mapped = documents[0]; restorePlan = documents[1]; limits = (documents[2] || {}).limits || {};
          if (!baseline) {
            if ('slots' in mapped && Array.isArray(mapped.slots)) baseline = { ...mapped, label: raw.stored_label };
            else if ('plan' in mapped && mapped.plan && Array.isArray(mapped.plan.slots)) baseline = mapped.plan;
            else {
              const replan = await ctx.api('replan');
              const response = await fetch('api/stage?plan_id=' + encodeURIComponent(replan.plans.sandbox), { headers: { Accept: 'application/json' } });
              if (!response.ok) throw new Error('sandbox plan unavailable');
              const bundle = await response.json();
              baseline = { plan_id: replan.plans.sandbox, label: raw.stored_label, slots: finalSlots(bundle) };
            }
          }
          if (!entered || life !== mine) return;
          // These prepared checks use their own disclosed sandbox baseline.
          // The real component exposes openBay; restore the confirmed closure on leave.
          const instruction = restorePlan && restorePlan.rule || {};
          const constraints = [instruction.rule, ...(instruction.canonical_rules || [])].map((row) => row && row.constraint).filter((c) => c && c.type === 'close_bay' && Number.isFinite(+c.bay));
          reopened = [];
          if (typeof ctx.bay.openBay === 'function') for (const bay of new Set(constraints.map((c) => +c.bay))) { ctx.bay.openBay(bay); reopened.push(bay); }
          if ('cargo_overrides' in mapped) ctx.bay.updateBoxes(mapped.cargo_overrides);
          showBaselineLabel();
          ready = true; current = baseline; setPlan(baseline); ctx.bay.clearMarks();
          const fail = mapped.cases.find((c) => c.id === 'securing') || mapped.cases.find((c) => !c.full.legal), pass = mapped.cases.find((c) => c.full.legal);
          const offered = [fail, pass, mapped.cases.find((c) => c.id === 'segregation')].filter((c, i, list) => c && list.indexOf(c) === i);
          $('.pl-stress-cases').innerHTML = offered.map((c) => `<button type="button" class="pl-stress-suggestion" data-pl-stress-case="${esc(c.id)}">${esc(nameOf(c))}</button>`).join('');
          if (fail) ctx.bay.showBays([...new Set([fail.from.bay, fail.to.bay])]);
          ctx.bay.enableDrag(onDrop, { allowSwap: true });
        })();
        return loaded;
      }
      async function start() { if (!entered) await enter(); await loaded; if (entered && ready) $('.pl-stress-cases').hidden = false; }
      function leave() {
        const restore = entered && ready;
        entered = false; ++life; ready = false; cancelRun(); ctx.bay.enableDrag(null); ctx.bay.clearMarks();
        if (restore) ctx.bay.resetBoxes();
        if (restore && restorePlan) setPlan(restorePlan);
        hideBaselineLabel();
        for (const bay of reopened) ctx.bay.closeBay(bay, `Bay ${String(bay).padStart(2, '0')} · closed`);
        reopened = [];
        $('.pl-stress-cases').hidden = true;
        const undo = $('.pl-stress-undo'); if (undo) { undo.disabled = true; undo.hidden = true; }
      }
      async function autoplay(auto) {
        await start(); if (!entered || !ready) return;
        const mine = life;
        const play = async (c) => {
          if (!c || !entered || mine !== life) return false;
          reset(); ctx.bay.showBays([...new Set([c.from.bay, c.to.bay])]);
          await auto.wait(100);
          if (!entered || mine !== life) return false;
          const from = ctx.bay.screenPoint(c.from), to = ctx.bay.screenPoint(c.to);
          if (!from || !to) return false;
          await auto.drag(from, to, { ms: 1200 });
          const run = active;
          if (!run || !run.kase || run.kase.id !== c.id || !entered || mine !== life) return false;
          const result = await run.completion;
          if (result.cancelled || !entered || mine !== life) return false;
          await auto.wait(400); return entered && mine === life;
        };
        const fail = mapped.cases.find((c) => c.id === 'securing') || mapped.cases.find((c) => !c.full.legal);
        if (!await play(fail)) return;
        await play(mapped.cases.find((c) => c.full.legal));
      }
      return { enter, leave, start, autoplay };
    },
  });
})();
