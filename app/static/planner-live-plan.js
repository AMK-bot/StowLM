/* Functional app · Generate plan and Checks (mode 'live'; functional-app CONTRACT.md Extension B, honesty rules 2, 4, 5, 7).
 * Generate plan → POST calls/<id>/plan with the chosen profile and the planner's instruction choice. While the engine
 * runs: real elapsed seconds from the server, "Searching for a checked plan" until the first checked incumbent, then
 * "First checked plan available. Continuing to improve.", and the incumbents' lifts over time. When the run ends: the
 * verdict exactly as the runner's evidence says, and, only for an L0 pass or a named relaxed plan, the bay plan, moves,
 * meters and checks from GET calls/<id>/planner/<name> (gated by app/functional_view.released). The meters are the
 * checker's measured values for this plan; nothing is borrowed from the demo. */
(function () {
  'use strict';
  if (!window.Planner) return;
  const MEASURED = 'measured on this machine, this run';
  const LIFTS = 'extra crane lifts (rehandles now and projected at later ports, hatch-cover lifts; D15)';
  const SEARCHING = 'Searching for a checked plan';
  const FIRST = 'First checked plan available. Continuing to improve.';
  const NO_PLAN = "No checked plan found. That isn't a proof that none exists.";
  // Wall-time scopes (logic review M5): the runner's clocks start before admission.
  const WHOLE = 'whole run, admission to package';
  const FIRST_SCOPE = 'first checked plan, from the start of the run (includes admission)';
  const PROFILE_WORDS = { live: 'live profile: engine deadline 720 s; not a whole-run guarantee', standard: 'standard profile: engine deadline 1800 s; not a whole-run guarantee' };
  const CREDIT = { engine: 'Frontier Engine 0.13.0 · search software', checker: 'Full checker · software' };
  const finite = (v) => typeof v === 'number' && isFinite(v);
  const n1 = (v, d = 1) => (finite(v) ? v.toFixed(d) : '—');
  const shortHash = (h) => (typeof h === 'string' ? h.slice(0, 10) : '');

  /* Runner advice this app can't follow (logic-2 S18): replaced with what the planner can do here. */
  function plainAdvice(text) {
    return String(text || '')
      .replace(/Retry with a longer deadline or reduce the load\.?/g, 'The 1800 s standard deadline is the longest in this app. Try a fresh call with a lower fill or fewer boxes.')
      .replace(/[^.]*\brecord(ed|ing|ings)?\b[^.]*\./gi, ' Retry, or start a fresh call. The Functional app never uses recordings.')
      .replace(/\s+/g, ' ').trim();
  }
  const NO_PLAN_STATUSES = new Set(['no_checked_plan', 'constraint_unavailable']);
  /* fa-engine's infeasibility sentence, when the outcome carries one (string, or {sentence, proved}). */
  function infeasibility(o) {
    const raw = o.infeasibility != null ? o.infeasibility : o.infeasibility_message != null ? o.infeasibility_message : o.infeasible_reason;
    if (!raw) return null;
    if (typeof raw === 'string') return { sentence: raw, proved: o.infeasibility_proved === true };
    return raw.sentence || raw.message ? { sentence: raw.sentence || raw.message, proved: raw.proved === true } : null;
  }
  /* Plan again on the same call: run is the latest attempt, attempts[] every attempt (fa-engine). */
  const attemptNo = (v) => { const r = (v && v.run) || {}; return r.attempt != null ? r.attempt : r.n != null ? r.n : v && Array.isArray(v.attempts) && v.attempts.length ? v.attempts.length : 1; };
  const finished = (v) => !!(v && v.run && v.run.status !== 'running' && v.state !== 'planning' && (v.state === 'done' || v.state === 'failed' || v.outcome));
  const STATUS_WORDS = { checked_plan: 'passed the full checker', relaxed_plan: 'relaxed plan', no_checked_plan: 'no checked plan found', constraint_unavailable: 'no checked plan found',
    error: 'stopped before a plan was released', stopped: 'stopped by the planner', failed: 'stopped before a plan was released', needs_confirmation: 'not run: needs confirmation' };
  // Explicit provenance from fa-engine (functional/README.md): never inferred from row order or note text.
  const isRevalidation = (r) => r.source === 'runner_receipt' || r.provenance === 'runner_revalidation' || r.provenance === 'runner_no_instruction';
  const drawable = (o) => !!o && ((o.status === 'checked_plan' && o.passed === true) || o.status === 'relaxed_plan');
  function admissionWords(a) {
    if (!a) return 'Admission: not reached yet.';
    if (a.waived) return `Admission waived: ${a.waived}${finite(a.load1) ? ` (load average ${a.load1.toFixed(2)})` : ''}.`;
    return `Admission: admitted by the machine-load gate${finite(a.load1) ? ` (load average ${a.load1.toFixed(2)})` : ''}.`;
  }
  /* Meter values from the meters payload: measured or null, never a placeholder number. */
  function meterUpdate(m, at, total) {
    const ok = (bad) => (bad ? 'fail' : 'ok');
    const none = (limit) => ({ value: null, limit, state: 'empty' });
    const out = {};
    out.stability = finite(m.gm_m) ? { value: `GM ${n1(m.gm_m, 2)} m`, limit: finite(m.gm_min_m) ? `minimum ${n1(m.gm_min_m, 2)} m (checked)` : 'minimum not recorded', ratio: finite(m.gm_min_m) && m.gm_m > 0 ? m.gm_min_m / m.gm_m : null, state: finite(m.gm_min_m) ? ok(m.gm_m < m.gm_min_m) : 'info', title: 'Departure GM measured by the full checker for this plan' } : none('not measured');
    out.list = finite(m.heel_deg) ? { value: `${n1(m.heel_deg, 1)}° · ${finite(m.trim_m) ? n1(Math.abs(m.trim_m), 2) + ' m trim' : '— trim'}`, limit: finite(m.heel_max_deg) ? `list limit ${n1(m.heel_max_deg, 1)}°` : '', ratio: finite(m.heel_max_deg) && m.heel_max_deg > 0 ? m.heel_deg / m.heel_max_deg : null, state: finite(m.heel_max_deg) ? ok(m.heel_deg > m.heel_max_deg || (finite(m.trim_m) && ((finite(m.trim_min_m) && m.trim_m < m.trim_min_m) || (finite(m.trim_max_m) && m.trim_m > m.trim_max_m)))) : 'info', title: [finite(m.trim_min_m) && finite(m.trim_max_m) ? `trim limits ${n1(m.trim_min_m, 2)} to ${n1(m.trim_max_m, 2)} m` : '', 'measured by the full checker at departure'].filter(Boolean).join(' · ') } : none('not measured');
    out.draught = finite(m.draught_m) ? { value: `${n1(m.draught_m, 2)} m`, limit: finite(m.draught_limit_m) ? `limit ${n1(m.draught_limit_m, 2)} m` : '', ratio: finite(m.draught_limit_m) && m.draught_limit_m > 0 ? m.draught_m / m.draught_limit_m : null, state: finite(m.draught_limit_m) ? ok(m.draught_m > m.draught_limit_m) : 'info', title: 'Deeper of the aft and forward draughts, measured by the full checker at departure' } : none('not measured');
    out.hull = finite(m.hull_pct) ? { value: `${Math.round(m.hull_pct)} %`, limit: 'limit 100 %', ratio: m.hull_pct / 100, state: ok(m.hull_pct > 100), title: 'Larger of shear force and bending moment as % of the limit, from the full checker' } : none('not measured');
    out.moves = { at, total, limit: 'all lifts in this plan, loads included' };
    out.rules = finite(m.active_rules) ? { value: `${m.active_rules} active`, limit: m.passed ? 'full checker passed' : m.label || 'relaxed plan', state: m.passed ? 'ok' : 'warn', ratio: 1, title: 'Active rules from this plan\'s package receipt' } : { value: null, limit: m.passed ? 'full checker passed' : m.label || '', state: 'empty' };
    return out;
  }
  const slotKey = (s) => [s.bay, s.row, s.tier].join(':');
  /* The bay state after the first `at` moves, from the call's arrival slots and each move's ship endpoints. */
  function slotsAt(moves, initial, at) {
    const where = new Map((initial || []).map((s) => [s.box_id, s]));
    for (const m of (moves || []).slice(0, at)) {
      if (!m.box_id) continue;
      if (m.from) where.delete(m.box_id);
      if (m.to) where.set(m.box_id, { ...m.to, box_id: m.box_id });
    }
    return [...where.values()];
  }

  Planner.stage({
    id: 'plan', order: 30, title: 'Plan', mode: 'live', features: [],
    logic: { meterUpdate, slotsAt, drawable, admissionWords },
    mount(panel, _data, ctx) {
      const esc = ctx.esc;
      panel.innerHTML = '<div class="pl-live-checks" aria-live="polite"></div>';
      const host = panel.querySelector('.pl-live-checks');
      const result = document.createElement('div');
      result.className = 'pl-live-result'; result.hidden = true;
      let posting = false, loadedFor = null, loaded = null, playing = false, at = 0, active = false, error = null;

      function attach(show) {
        const slot = ctx.slot('result'); if (!slot) return;
        if (result.parentNode !== slot) slot.appendChild(result);
        if (show) { slot.hidden = false; result.hidden = false; }
      }
      function incumbents(list) {
        if (!list || !list.length) return '';
        return `<table class="pl-live-table"><thead><tr><th class="num">At (s)</th><th class="num">Extra crane lifts</th><th>Plan</th><th>Lane</th></tr></thead><tbody>${list.map((i) =>
          `<tr><td class="num">${n1(i.t_s)}</td><td class="num"><b>${esc(i.lifts)}</b></td><td><code>${esc(shortHash(i.plan_sha256))}</code></td><td>${esc(i.lane || '')}</td></tr>`).join('')}</tbody></table>
          <p class="pl-live-note">Each row passed the full checker and every confirmed instruction when it appeared.</p>`;
      }
      function verdict(v) {
        const o = v.outcome || {}, view = loaded && loaded.checks;
        if (o.status === 'checked_plan' && o.passed === true) {
          const rows = view ? view.confirmed_instructions || [] : null;
          const confirmed = rows ? rows.length > 0 && rows.every((r) => r.rechecked) : (v.readings || []).some((r) => r.confirmed);
          return { cls: 'pass', title: 'Passes the full checker', text: `${confirmed ? 'It also obeys the instruction the planner confirmed, rechecked on this plan.' : 'Planned with no instruction.'} ${o.message || ''}`.trim() };
        }
        if (o.status === 'relaxed_plan') {
          const broken = (view && view.broken_rules) || (o.broken_rules || []).map((id) => ({ id, title: id }));
          const ashore = (o.boxes_ashore || []).length;
          return { cls: 'relaxed', title: (view && view.label) || `Relaxed plan at L${o.level}`, broken,
            text: `Level L${o.level}. Not a full checker pass. Breaks ${broken.length} rule${broken.length === 1 ? '' : 's'}${ashore ? `; ${ashore} box${ashore === 1 ? '' : 'es'} left ashore` : ''}.` };
        }
        // M6: the no-plan line only where a search ended without one; other stops say the run stopped before a release.
        const msg = plainAdvice(String(o.message || v.message || '').replace(NO_PLAN, ''));
        if (NO_PLAN_STATUSES.has(o.status)) {
          const inf = infeasibility(o);
          return { cls: 'fail', title: 'No plan', text: [msg, inf ? inf.sentence : '', inf && inf.proved ? '' : NO_PLAN].filter(Boolean).join(' ') };
        }
        return { cls: 'fail', title: 'No plan released', text: `The run stopped before a plan could be released: ${msg}` };
      }
      function provenance(v) {
        // S10: a finished row replaces its own running row (same instance_id; per-booking rows differ), never another's.
        const rows = [];
        const same = (x, r) => (r.instance_id || x.instance_id ? x.instance_id === r.instance_id : x.stage === r.stage && x.who === r.who);
        for (const r of v.stages || []) { const prev = rows.findIndex((x) => x.status === 'running' && same(x, r)); if (prev >= 0 && r.status !== 'running') rows[prev] = r; else rows.push(r); }
        return `<details class="pl-live-prov"><summary>Provenance: who did each stage, where, how long</summary><ol>${rows.map((r) => {
          const notes = r.notes || '';
          // P1-3: a re-validation only when the backend says so (its runner-receipt row), never inferred from row order.
          const revalidation = r.stage === 'reader' && isRevalidation(r);
          const who = r.stage === 'engine' ? CREDIT.engine : r.stage === 'checker' ? CREDIT.checker
            : revalidation ? (r.provenance === 'runner_no_instruction' || r.revalidation === false ? 'StowLM Reader · no instruction, no model call' : "StowLM Reader's confirmed answer · re-validated by software, no model call")
              : r.stage === 'reader' && r.provenance === 'no_instruction' ? 'Planner chose no instruction · no model call' : [r.who, r.where].filter(Boolean).join(' · ');
          const meta = [revalidation ? (r.provenance === 'runner_no_instruction' || r.revalidation === false ? 'no model call' : 're-validation') : r.mode, finite(r.wall_s) ? `${n1(r.wall_s, 2)} s, ${MEASURED}` : '', finite(r.cost_usd) ? `$${r.cost_usd.toFixed(4)}` : '', r.model || '', r.adapter_sha256 ? 'adapter ' + shortHash(r.adapter_sha256) : ''].filter(Boolean).join(' · ');
          return `<li data-stage="${esc(r.stage)}"><b>${esc(r.stage)}</b> <span class="pl-live-tag ${esc(r.status)}">${esc(r.status)}</span><div>${esc(who)}</div>${meta ? `<small>${esc(meta)}</small>` : ''}${notes ? `<small>${esc(notes)}</small>` : ''}</li>`;
        }).join('')}</ol></details>`;
      }
      function instructionWords(v, how) {
        if (!how) return null;
        if (how.mode === 'none') return 'No instruction';
        const r = how.reading || (v.readings || []).find((x) => x.confirmed);
        return r ? `Confirmed: ${r.rule_text || r.sentence || 'the confirmed reading'}` : null;
      }
      function againState(v) {
        const how = ctx.state.instruction, confirmedOk = how && how.mode === 'confirmed' && (v.readings || []).some((r) => r.confirmed);
        const choice = how && (how.mode === 'none' || confirmedOk);
        return { how, choice, ready: finished(v) && choice && !posting };
      }
      function againCard(v) {
        if (!finished(v)) return '';
        const st = againState(v), words = instructionWords(v, st.how);
        const p = ctx.state.profile === 'live' ? 'live' : 'standard';
        return `<section class="pl-live-again" aria-label="Plan again">
          <b>Plan again on this call</b>
          <p class="pl-live-note">Instruction: ${words ? esc(words) : 'not chosen. Read and confirm a new sentence in 3 · Instructions, or choose Plan with no instruction there.'}</p>
          <div class="pl-live-row"><button type="button" class="pl-btn secondary" data-do="change-instruction">Change instruction</button>
            <span class="pl-seg" role="group" aria-label="Engine profile">${['standard', 'live'].map((k) => `<button type="button" data-do="profile" data-profile="${k}" class="${k === p ? 'on' : ''}" aria-pressed="${k === p}">${k === 'standard' ? 'Standard' : 'Live'}</button>`).join('')}</span></div>
          <p class="pl-live-note">${esc(PROFILE_WORDS[p])}</p>
          <button type="button" class="pl-btn primary" data-do="plan-again"${st.ready ? '' : ' disabled'}>Plan again</button>
        </section>`;
      }
      function earlier(v) {
        const list = Array.isArray(v.attempts) ? v.attempts : [], now = attemptNo(v);
        const rows = list.map((a, i) => ({ ...a, n: a.attempt != null ? a.attempt : a.n != null ? a.n : i + 1 })).filter((a) => a.n !== now);
        if (!rows.length) return '';
        const reading = (id) => (v.readings || []).find((r) => r.id === id);
        return `<details class="pl-live-attempts" open><summary>Earlier attempts</summary><ol>${rows.map((a) => {
          const o = a.outcome || {}, status = o.status || a.outcome_status || a.status;
          const instr = a.instruction === 'none' ? 'no instruction' : a.instruction === 'confirmed' ? ((reading(a.confirmed_reading) || {}).rule_text || 'confirmed reading') : 'instruction not recorded';
          const wall = finite(a.wall_s) ? a.wall_s : finite(o.wall_s) ? o.wall_s : finite(a.elapsed_s) ? a.elapsed_s : null;
          const level = status === 'relaxed_plan' && o.level != null ? ` L${o.level}` : '';
          return `<li data-attempt="${esc(a.n)}"><b>#${esc(a.n)}</b> · ${esc(instr)} · ${esc(a.profile || 'standard')} · ${esc((STATUS_WORDS[status] || status || 'unknown') + level)}${wall != null ? ` · ${n1(wall)} s ${WHOLE}` : ''}</li>`;
        }).join('')}</ol></details>`;
      }
      function draw() {
        const v = ctx.call; if (!v) { host.innerHTML = '<p class="pl-live-note">Create a call first.</p>'; return; }
        const run = v.run || {}, inc = run.incumbents || [], profile = PROFILE_WORDS[run.profile] || PROFILE_WORDS[ctx.state.profile] || PROFILE_WORDS.standard;
        const running = v.state === 'planning' || run.status === 'running';
        if (running || posting) {
          attach(true);
          result.innerHTML = `<div class="pl-live-strip"><i class="pl-live-spin"></i><b>${esc(inc.length ? FIRST : SEARCHING)}</b>
            <span>Elapsed ${finite(run.elapsed_s) ? Math.round(run.elapsed_s) : 0} s since Generate plan, ${MEASURED}</span>${inc.length ? `<span>best so far <b>${esc(inc[inc.length - 1].lifts)}</b> extra crane lifts</span>` : ''}
            <button type="button" class="pl-btn secondary" data-do="stop">Stop the engine</button></div>`;
          host.innerHTML = `<p class="pl-live-note">${esc(profile)}. ${esc(admissionWords(run.admission))}</p>
            <p class="pl-live-note">${esc(CREDIT.engine)} searches; ${esc(CREDIT.checker)} decides. Lifts are ${esc(LIFTS)}.</p>${incumbents(inc)}${earlier(v)}${provenance(v)}`;
          ctx.status(inc.length ? FIRST : SEARCHING);
          return;
        }
        if (v.state === 'failed' && !v.outcome) {
          attach(true);
          const stopped = run.status === 'stopped' || /planner stopped/i.test(v.message || '');
          const n = inc.length;
          const words = !stopped ? `The run stopped before a plan could be released: ${plainAdvice(v.message || 'the worker ended without an outcome.')}`
            : n ? `The planner stopped the engine. ${n} checked plan${n === 1 ? ' was' : 's were'} found before the stop; none was released, because a plan is released only when the run finishes.`
              : 'The planner stopped the engine before any checked plan was found.';
          result.innerHTML = `<div class="pl-live-strip fail"><i class="pl-live-dot"></i><b>${stopped ? 'Engine stopped' : 'No plan released'}</b><span>${esc(words)}</span></div>`;
          host.innerHTML = `<p class="pl-live-note fail">${esc(words)}</p>${againCard(v)}${incumbents(inc)}${earlier(v)}${provenance(v)}`;
          return;
        }
        if (v.state !== 'done' && !v.outcome) {
          attach(true);
          result.innerHTML = `<div class="pl-live-strip">${error ? `<span class="fail">${esc(error)}</span>` : '<span>Waiting for the inputs</span>'}</div>`;
          host.innerHTML = `<p class="pl-live-note">${esc(error || 'Generate plan starts Engine 0.13.0 once the inputs are ready.')}</p>`;
          return;
        }
        const o = v.outcome || {}, vd = verdict(v), checks = loaded && loaded.checks, m = loaded && loaded.meters;
        attach(true);
        const total = loaded && loaded.moves ? loaded.moves.moves.length : 0;
        result.innerHTML = `<div class="pl-live-strip ${vd.cls}"><i class="pl-live-dot"></i><b>${esc(vd.title)}</b>
          ${drawable(o) ? `<span class="pl-live-counter">Move ${at} of ${total}</span><span>${esc(o.lifts == null ? '—' : o.lifts)} extra crane lifts</span>` : ''}
          <span>${n1(o.wall_s)} s ${WHOLE}, ${MEASURED}</span>${Array.isArray(v.attempts) && v.attempts.length > 1 ? `<span>attempt ${esc(attemptNo(v))} of ${v.attempts.length}</span>` : ''}
          ${finished(v) ? `<button type="button" class="pl-btn secondary" data-do="plan-again"${againState(v).ready ? '' : ' disabled'}>Plan again</button>` : ''}</div>`;
        const broken = vd.broken ? `<p class="pl-live-note">Rules this plan breaks (every one, from the checker):</p><ul class="pl-live-broken">${vd.broken.map((b) =>
          `<li><code>${esc(b.id)}</code>${b.title && b.title !== b.id ? ' · ' + esc(b.title) : ''}${b.tier ? ` <span class="pl-live-tag warn">${esc(b.tier)}</span>` : ''}</li>`).join('')}</ul>` : '';
        const ashore = (o.boxes_ashore || []).length ? `<p class="pl-live-note">Boxes ashore: ${(o.boxes_ashore || []).map((b) => `<code>${esc(b)}</code>`).join(' ')}</p>` : '';
        const nums = drawable(o) ? `<div class="pl-live-stats">
            <div><b>${esc(o.lifts == null ? '—' : o.lifts)}</b><span>${esc(LIFTS)}</span></div>
            <div><b>${n1(o.first_checked_wall_s)} s</b><span>${FIRST_SCOPE}, ${MEASURED}</span></div>
            <div><b>${n1(o.wall_s)} s</b><span>${WHOLE}, ${MEASURED}</span></div>
            <div><b>${checks && finite(checks.active_rules) ? checks.active_rules : '—'}</b><span>active checker rules, from the receipt</span></div></div>` : '';
        const parts = checks ? `<p class="pl-live-note">${checks.parts_ran} checker parts ran; ${checks.violation_count} violation${checks.violation_count === 1 ? '' : 's'} reported${checks.checker_version ? ` · checker ${esc(checks.checker_version)}` : ''}.</p>
          ${(checks.confirmed_instructions || []).map((r) => `<p class="pl-live-note ${r.rechecked ? 'ok' : 'fail'}">“${esc(r.sentence)}” ${r.rechecked ? 'rechecked on this plan: obeyed' : 'failed its recheck'}</p>`).join('')}` : '';
        host.innerHTML = `<div class="pl-live-verdict ${vd.cls}"><b>${esc(vd.title)}</b><span>${esc(vd.text)}</span></div>${broken}${ashore}${nums}${parts}
          <p class="pl-live-note">${esc(CREDIT.engine)} searched; ${esc(CREDIT.checker)} decided.</p>
          ${error ? `<p class="pl-live-note fail">${esc(error)}</p>` : ''}
          <p class="pl-live-note">${esc(profile)}. ${esc(admissionWords(run.admission))}</p>
          ${againCard(v)}${incumbents(inc)}${earlier(v)}${provenance(v)}`;
        if (m) ctx.meters(meterUpdate(m, at, total));
        ctx.status(vd.title);
      }
      async function loadPlan(v) {
        const o = v.outcome || {};
        const key = v.id + ':' + attemptNo(v) + ':' + (o.receipt_sha256 || o.status);
        if (loadedFor === key) return;
        loadedFor = key;
        if (!drawable(o)) { loaded = null; draw(); ctx.done(); return; }
        try {
          const [after, moves, meters, checks] = await Promise.all(['plan-after', 'moves', 'meters', 'checks'].map((n) => ctx.data(n)));
          loaded = { after, moves, meters, checks, attempt: attemptNo(v) };
          error = null;
        } catch (e) { loaded = null; error = e.message; draw(); ctx.done(); return; }
        await play();
        ctx.log({ who: 'Full checker', where: 'on this laptop', text: loaded.checks.passed ? 'Full checker passed' : loaded.checks.label });
        ctx.emit('package:ready', { call: v.id });
        ctx.done();
      }
      async function play() {
        const { after, moves } = loaded, list = moves.moves || [], bay = ctx.bay;
        at = 0; playing = true; draw();
        ctx.setPlan({ ...after, slots: moves.initial_slots || [], meters: null });
        try { bay.highlight([], 'moved'); } catch (e) { /* ignore */ }
        const bays = [...new Set((after.slots || []).map((s) => s.bay))].slice(0, 3);
        if (bays.length && typeof bay.showBays === 'function') { try { bay.showBays(bays); } catch (e) { /* ignore */ } }
        const speed = Math.max(1, list.length * 320 / 6500) * (ctx.speed > 0 ? ctx.speed : 1);
        let timer = null;
        try {
          await Promise.race([
            bay.play(list, { speed, onMove(_move, index) { at = Math.min(list.length, Number.isInteger(index) ? index : at + 1); ctx.meters({ moves: { at, total: list.length, limit: 'all lifts in this plan, loads included' } }); } }),
            new Promise((res) => { timer = setTimeout(res, 10000); }),
          ]);
        } catch (e) { /* the component is missing: show the final state */ }
        clearTimeout(timer);
        playing = false; at = list.length;
        ctx.setPlan({ ...after, meters: null }); // the shell's own meters stay off; the checker's values follow
        draw();
      }
      ctx.on('moves:seek', (p) => {
        if (!loaded || playing || !p || !Number.isInteger(p.at)) return;
        at = Math.max(0, Math.min(loaded.moves.moves.length, p.at));
        const slots = at >= loaded.moves.moves.length ? loaded.after.slots : slotsAt(loaded.moves.moves, loaded.moves.initial_slots, at);
        ctx.setPlan({ ...loaded.after, slots, meters: null });
        draw();
      });
      async function start() {
        const v = ctx.call; if (!v) return;
        if (!v.run && v.state === 'ready' && !posting) {
          posting = true; error = null; draw();
          const how = ctx.state.instruction;
          try { await ctx.fx.post(`calls/${encodeURIComponent(v.id)}/plan`, { profile: ctx.state.profile === 'live' ? 'live' : 'standard', instruction: how && how.mode === 'confirmed' ? 'confirmed' : 'none' }); }
          catch (e) { error = e.message; ctx.toast(e.message); }
          posting = false;
        }
        draw();
        if (ctx.call && ctx.call.state === 'done') await loadPlan(ctx.call);
      }
      async function planAgain() {
        const v = ctx.call; if (!v || !againState(v).ready) return;
        const how = ctx.state.instruction;
        posting = true; error = null; draw();
        try {
          await ctx.fx.post(`calls/${encodeURIComponent(v.id)}/plan`, { profile: ctx.state.profile === 'live' ? 'live' : 'standard', instruction: how && how.mode === 'confirmed' ? 'confirmed' : 'none' });
          loaded = null; loadedFor = null; at = 0;
        } catch (e) { error = e.message; ctx.toast(e.message); }
        posting = false; draw();
      }
      async function onClick(e) {
        const b = e.target.closest && e.target.closest('[data-do]'); if (!b || b.disabled) return;
        const what = b.dataset.do;
        if (what === 'stop') { b.disabled = true; try { await ctx.fx.post(`calls/${encodeURIComponent(ctx.callId())}/stop`); } catch (err) { ctx.toast(err.message); } return; }
        if (what === 'plan-again') return planAgain();
        if (what === 'profile') { ctx.state.profile = b.dataset.profile === 'live' ? 'live' : 'standard'; draw(); return; }
        if (what === 'change-instruction') {
          ctx.emit('plan:again', { call: ctx.callId(), attempt: attemptNo(ctx.call), choose: 'instruction' });
          if (window.Planner && window.Planner.open) window.Planner.open('instruct');
        }
      }
      result.addEventListener('click', onClick);
      host.addEventListener('click', onClick);
      ctx.on('rule:confirmed', () => draw());
      ctx.on('rule:changed', () => draw());
      ctx.on('call:update', (v) => {
        // A reloaded call with a run: show its run, whatever state the run ended in.
        if (!active && !window.Planner.generated && v && (v.state === 'planning' || v.state === 'done' || v.run)) { window.Planner.generate(); return; }
        if (!window.Planner.generated) return;
        const n = attemptNo(v);
        if (loaded && loaded.attempt !== n) { // a new attempt: nothing from the earlier one stays on the meters
          loaded = null; loadedFor = null; at = 0;
          ctx.meters({ stability: { value: null, limit: 'after this attempt', state: 'empty' }, list: { value: null, limit: 'after this attempt', state: 'empty' },
            draught: { value: null, limit: 'after this attempt', state: 'empty' }, hull: { value: null, limit: 'after this attempt', state: 'empty' },
            rules: { value: null, limit: 'after this attempt', state: 'empty' }, moves: { at: 0, total: 0, limit: 'after this attempt' } });
        }
        draw();
        if (finished(v) && announced !== v.id + ':' + n) { announced = v.id + ':' + n; ctx.emit('run:finished', { call: v.id, attempt: n, instruction: (v.run || {}).instruction || null }); }
        if (v && v.state === 'done') loadPlan(v);
      });
      let announced = null;
      return {
        result, ownsAction: true,
        enter() { active = true; attach(false); draw(); },
        leave() { active = false; },
        start,
      };
    },
  });
})();
