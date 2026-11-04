/* v6 chapter 4: the ship replans. The stored plan before stands loaded and the boxes the rule forces out glow where they sit.
 * Then every moved box lifts and re-lands, the rule's in amber first, the search's own in a quiet blue (logic3, B1: most moves
 * for one rule aren't the rule's doing), while every panel reads the plan under the rule lift by lift. All of it is a pure
 * function of tau. Numbers come from /api/v6/replan (the gate) and the two stage bundles, never from here. Falls back to the
 * stored plan under the standing brief when the plan under the rule isn't shown (script, shot 7). "Live" is earned only by the
 * exact plan a live run returned, loaded and morphing here; short of that the stored plan keeps its label and says so.
 * A stored plan's boxes, forced boxes, lifts and date are the lattice's facts file's (d.facts, bound to these plans by the server),
 * with its own attribution caveat beside them. No facts: the split and the caveat drop and only "N boxes moved" is said.
 * Confirmation before execution (code review 12, P2): the server sends the plan under the rule only for the reading the planner
 * selected (StageV6.selectedReading, kept by the reading chapter) with a token issued for exactly it, or for the recorded answer
 * chosen by name; the film always asks for the latter, labelled. This page only reflects what the server said. */
(function () {
  const V = window.StageV6, STORY = window.StageStory;
  const HOLD_S = 1.8, RUN_S = 7.4, RESULT_S = HOLD_S + RUN_S + 0.4, END_S = 15, FLIGHT_S = 0.8;
  const LIVE_GAP = 'The live result could not be displayed: its plan did not load. This is still the stored plan.';
  const clamp01 = (t) => (t < 0 ? 0 : t > 1 ? 1 : t);
  const count = (v) => (v == null ? '—' : Number.isInteger(v) ? v.toLocaleString('en-US') : v.toFixed(1));
  const day = (utc) => { const d = new Date(utc); return isNaN(d) ? null : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }); };
  /* Pure. What to ask the server: the film and an explicit choice ask for the recorded answer by name; otherwise the selected reading
   * and whatever confirmation the planner holds. Whether that token is for this reading is the server's call, not this page's. */
  const replanQuery = (selected, confirmation, interactive, recorded) => {
    if (!interactive || recorded) return 'replan?recorded=1';
    const p = new URLSearchParams();
    if (selected) p.set('reading', selected);
    if (confirmation && confirmation.token) p.set('token', confirmation.token);
    const q = p.toString();
    return q ? 'replan?' + q : 'replan';
  };
  /* Pure. The chapter's state from the gate and the server's word on the selected reading: a plan under the rule only when both say so. */
  const readingState = (g, said) => {
    if (!g || g.status === 'not_shown' || !g.before) return { mode: 'gap', why: null, label: null, offerRecorded: false };
    const yes = g.status === 'shown' && !!g.after && !!said && said.shown === true;
    return { mode: yes ? 'shown' : 'hold', why: said && said.shown === false ? said.why || null : null,
      label: yes && said.fallback === 'recorded' ? said.label || null : null, offerRecorded: !yes && !!said && said.needs === 'confirmation' };
  };
  const stageReady = () => new Promise((res) => { const w = () => (window.__stage && window.__stage.B ? res(window.__stage) : setTimeout(w, 60)); w(); });
  async function bundle(id) {
    for (let n = 0; ; n++) {
      const r = await fetch('/api/stage?' + new URLSearchParams({ plan_id: id }), { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(`/api/stage: ${r.status}`);
      const b = await r.json();
      if (b.physics || n > 40) return b;
      await new Promise((res) => setTimeout(res, 1500));
    }
  }

  V.chapter({ id: 'replan', order: 40, title: 'The plan changes', feature: 'replan', ship: true, duration_s: END_S, replanQuery, readingState,
    mount(host, d, ctx) {
      const esc = ctx.esc;
      host.innerHTML = `<div class="v6r-rule" hidden><span>Confirmed rule</span><b></b></div>
        <div class="v6r-tag"></div>
        <div class="v6r-count"><div class="v6r-lift"><b>0</b><i>of 0 moves</i></div><div class="v6r-moved"><span class="v6r-forced"></span><span class="v6r-also"></span></div></div>
        <div class="v6r-gauges"><span data-g="stability"><b>Stability</b><i></i></span><span data-g="balance"><b>Balance</b><i></i></span><span data-g="draught"><b>Draught</b><i></i></span><span data-g="hull"><b>Hull stress</b><i></i></span></div>
        <div class="v6r-go" hidden><button type="button" class="v6-btn"></button></div>
        <div class="v6r-result" hidden><div class="v6r-big"></div><div class="v6r-split"><span class="v6r-forced"></span><span class="v6r-also"></span></div><div class="v6r-cost"></div><div class="v6r-green"></div><div class="v6r-note"></div></div>
        <div class="v6r-trail" hidden></div>
        <div class="v6r-labels"></div>`;
      const q = (s) => host.querySelector(s);
      const el = { rule: q('.v6r-rule'), lift: q('.v6r-lift b'), of: q('.v6r-lift i'), moved: q('.v6r-moved'), nowForced: q('.v6r-moved .v6r-forced'), nowAlso: q('.v6r-moved .v6r-also'), go: q('.v6r-go'), result: q('.v6r-result'), trail: q('.v6r-trail'), labels: q('.v6r-labels') };
      const run = { recorded: false, state: readingState(null, null), mode: null, order: [], split: STORY.forcedSplit([], null), forced: new Set(), base: null, key: '', famKey: '', scene: null, B: null, gate: {}, total: 0, why: null, live: null, facts: null, liveGap: null, shownK: -1, fams: -1, landed: -1 };
      const plural = (n, word) => `${count(n)} ${word}${n === 1 ? '' : 'es'}`;
      const forcedWords = (n) => (n ? `${plural(n, 'box')} the rule forced out` : 'The rule forced no box out');

      function words() {
        const g = run.gate, env = g.envelope || {}, rules = d.rule_counts, split = run.split;
        const word = run.mode === 'shown' ? g.recheck : (g.before || {}).recheck, tail = word ? `, ${word}` : ''; // what the check really was, from the gate
        const f = run.live ? null : run.facts, date = f ? f.plan_date : null;
        const stored = run.live ? `Live, ${run.live.elapsed_s} s` : run.mode === 'shown' && date ? `Stored plan, computed on ${date}${tail}` : `Stored plan${tail}`;
        const who = d.credits || 'Search software';
        const article = /^(8|11|18)/.test(env.teu) ? 'an' : 'a', ship = [env.teu && `${env.teu} TEU`, env.kind, 'ship'].filter(Boolean).join(' ');
        const inside = env.inside ? `Inside our design limit: ${env.boxes} boxes in a ${env.region} of ${article} ${ship}, ${env.fill_pct} % of that section` : '';
        const reading = run.mode === 'shown' && !run.live && (d.rule || {}).stand_in_note; // leaves only with a real reader's recording
        const held = run.live ? null : run.state.why, said = run.live || run.mode !== 'shown' ? null : run.state.label; // the server's why no plan under the rule; "Recorded answer" when it was chosen by name
        el.labels.innerHTML = [stored, said, who, inside, reading, run.liveGap, held].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join('')
          + (run.mode === 'shown' && f ? `<span class="v6r-caveat">${esc(f.caveat)}</span>` : ''); // the facts file's own words: "forced" is pointwise
        const tag = (d.stand_ins || {}).ship; // the ship's own stand-in only: each other piece carries its tag where it plays
        q('.v6r-tag').innerHTML = tag ? `<i></i>${esc(tag)}` : '';
        q('.v6r-tag').hidden = !tag;
        el.of.textContent = `of ${count(run.total)} moves`; // the crane sequence's own steps, never "crane lifts" (D296)
        el.rule.querySelector('b').textContent = (run.live ? (d.live || {}).rule : (d.rule || {}).text) || '';
        const lifts = run.live ? run.live.result.lifts : f ? f.lifts_after : null;
        const forcedBig = q('.v6r-split .v6r-forced'), alsoBig = q('.v6r-split .v6r-also');
        if (run.mode === 'shown') {
          // The plan's own lifts only: a before → after gap reads as the rule's cost, which headline.md bars (logic14 p22, shot 7)
          q('.v6r-big').innerHTML = lifts == null ? '' : `<b>${count(lifts)}</b><span>extra lifts, including projected later-port lifts</span>`;
          const nForced = f ? f.forced : split.forced.length, nAlso = f ? f.rearranged : split.also.length;
          forcedBig.textContent = split.known ? forcedWords(nForced) : `${plural(f ? f.moved : run.order.length, 'box')} moved`;
          alsoBig.textContent = split.known && nAlso ? `${count(nAlso)} more: the search also rearranged these` : '';
          q('.v6r-cost').textContent = '';
        } else {
          const held = (g.before || {}).lifts;
          q('.v6r-big').innerHTML = held == null ? '' : `<b>${count(held)}</b><span>extra lifts, including projected later-port lifts</span>`;
          forcedBig.textContent = 'Standing brief'; alsoBig.textContent = ''; q('.v6r-cost').textContent = '';
        }
        q('.v6r-note').textContent = [run.liveGap, held, d.before_note].filter(Boolean).join(' · ');
        q('.v6r-green').textContent = [run.live ? '' : (d.gauges || {}).line, rules ? `Full checker passed: ${rules.active} active rules, ${rules.off} off as run` : ''].filter(Boolean).join(' · ');
      }
      function trail() { // a live run's own plans as they arrive; a stored plan shows only the runner's measured figure, or nothing
        const rows = (run.live ? run.live.incumbents : d.incumbents) || [], first = d.first_plan;
        const stored = !rows.length && first ? `<span>First legal plan: median ${Math.round(first.median_s)} s over ${count(first.runs)} development runs</span>` : '';
        el.trail.hidden = !rows.length && !stored;
        el.trail.innerHTML = rows.length ? rows.slice(0, 6).map((r, n) => `<span>${n ? 'then better' : 'first checked plan'} · ${Math.round(r.t_s)} s · ${count(r.lifts)} extra lifts</span>`).join('<i>→</i>') : stored;
      }
      async function load(job) { // job: a finished live run. True when what it asked for is what now plays
        const r = await poll(), g = r.gate || {}, afterId = job ? job.result.plan_id : null, st = readingState(g, r.reading_gate);
        run.gate = g; run.why = g.why || null; run.state = st;
        if (st.mode === 'gap') { run.mode = 'gap'; return false; }
        const before = await bundle(g.before.plan_id);
        const after = afterId || (st.mode === 'shown' ? (g.after || {}).plan_id : null);
        const B = after ? await bundle(after).catch(() => null) : null;
        const facts = !job && st.mode === 'shown' && d.facts && Array.isArray(d.facts.forced_ids) ? d.facts : null;
        const forced = job ? job.result.forced_boxes : facts ? facts.forced_ids : null; // a live plan answers to its own rule; a stored one to the facts file
        const diff = B ? STORY.morphOrder(before, B, forced) : null;
        const fits = !!diff && diff.ok && (job ? (B.meta || {}).plan_id === afterId : diff.order.length === g.boxes_moved);
        if (job && !fits) return false; // what's on screen stays exactly as it was
        run.live = job || null; run.liveGap = null; run.facts = fits ? facts : null;
        run.mode = fits ? 'shown' : 'hold'; run.B = fits ? B : before; run.order = fits ? diff.order : [];
        run.split = STORY.forcedSplit(run.order, forced); run.forced = new Set(run.split.forced);
        run.base = fits ? [...STORY.finalSlots(before).entries()].map(([box, v]) => [v.slot, box]) : null;
        run.total = (run.B.steps || []).length;
        run.scene = null; run.key = ''; run.shownK = -1; run.landed = -1;
        await poll(); // the walk needs both plans' physics, which the bundles above have just waited for
        words(); trail(); goButtons();
        return true;
      }
      async function showLive(job) {
        view.ready = load(job).catch(() => false);
        if (await view.ready) return;
        run.liveGap = LIVE_GAP; d.incumbents = []; // its numbers go with it
        words(); trail();
      }
      const query = () => replanQuery(V.selectedReading || null, V.confirmation, ctx.interactive, run.recorded);
      async function poll() { for (;;) { const r = await ctx.api(query()); Object.assign(d, r); if ((r.gate || {}).status !== 'checking') return r; await new Promise((res) => setTimeout(res, 1000)); } }
      function ensureLoaded(ST) { // the director or another chapter may have swapped the bundle under the dip
        if (run.scene === ST.scene && ST.B === run.B) return;
        if (ST.B !== run.B) { ST.load(run.B, 0); run.total = ST.total; }
        run.scene = ST.scene; run.key = ''; run.fams = -1; run.shownK = -1;
      }
      function drawMorph(ST, tau) {
        const m = STORY.morphAt(run.order, HOLD_S + 0.2, HOLD_S + RUN_S, FLIGHT_S, tau), key = `${m.started}|${m.landed}`;
        if (key !== run.key) {
          if (!run.key) { ST.jumpTo(run.total); ST.peek(0); }
          run.key = key;
          const state = Object.assign({}, ST.stateAt(run.total), { ship: STORY.morphShip(run.base, run.order, m.started, m.landed), buffer: [], coversOff: new Set() });
          const quiet = new Set(run.order.slice(0, m.landed).map((x) => x.box).filter((box) => !run.forced.has(box)));
          const marks = run.order.flatMap((x, i) => (!run.forced.has(x.box) || (i < m.started && i >= m.landed) ? [] : [{ box: x.box, slot: i < m.landed ? x.to : x.from }]));
          ST.scene.setMorph({ state, glow: run.forced, quiet, marks, flyers: m.flyers });
        } else ST.scene.setFlyers(m.flyers);
      }
      function revealRules(tau) {
        const fams = document.querySelectorAll('#rules .fam'), n = STORY.familiesShown(fams.length, HOLD_S + RUN_S, 0.32, tau);
        if (n === run.fams && !fams.length) return;
        run.fams = n;
        fams.forEach((f, i) => f.classList.toggle('v6-pend', i >= n));
        const badge = document.getElementById('rules-badge'); if (badge) badge.classList.toggle('v6-pend', n < fams.length);
      }
      const view = {
        get gap() { return run.mode === 'gap'; },
        ready: null,
        preload() { if (!view.ready || view.query !== query()) { view.query = query(); view.ready = load(); } return view.ready; },
        async enter() { document.body.classList.add('v6-morph'); await view.preload(); await stageReady(); },
        leave() {
          document.body.classList.remove('v6-morph');
          const ST = window.__stage; if (ST && ST.scene && ST.scene.setMorph) ST.scene.setMorph(null);
          run.scene = null; run.key = '';
        },
        seek(tau, interactive) {
          const ST = window.__stage; if (!ST || !run.mode || run.mode === 'gap') return;
          const held = interactive && ctx.waitAt(tau, 'replan-go', HOLD_S);
          if (held) tau = HOLD_S;
          ensureLoaded(ST);
          const u = clamp01((tau - HOLD_S) / RUN_S), k = Math.floor(u * run.total);
          if (run.mode === 'shown') { drawMorph(ST, tau); ST.peek(k); } else ST.seek(u * run.total, u >= 1 || u <= 0);
          if (k !== run.shownK) { run.shownK = k; el.lift.textContent = k.toLocaleString('en-US'); plainGauges(); }
          revealRules(tau);
          movedCount(STORY.morphAt(run.order, HOLD_S + 0.2, HOLD_S + RUN_S, FLIGHT_S, tau).landed);
          el.rule.hidden = run.mode !== 'shown' || !(d.rule || {}).text;
          const r = clamp01((tau - RESULT_S) / 0.5);
          el.result.hidden = r <= 0; el.result.style.opacity = r.toFixed(2);
          if (held && el.go.hidden) el.go.style.top = `${Math.max(148, el.labels.offsetTop + el.labels.offsetHeight + 14)}px`; // below however many rows the labels wrap to (rehearsal 4.3: they covered the button)
          el.go.hidden = !held;
          el.moved.hidden = r > 0;
        },
      };
      function movedCount(landed) { // before anything flies the count is the rule's own: these boxes sit where it forbids
        if (landed === run.landed) return;
        run.landed = landed;
        if (run.mode !== 'shown') { el.nowForced.textContent = el.nowAlso.textContent = ''; return; }
        const split = run.split, now = STORY.landedSplit(run.order, split, landed);
        const total = run.facts ? run.facts.forced : split.forced.length;
        el.nowForced.textContent = !split.known ? `${plural(landed, 'box')} moved` : landed ? `${count(Math.min(now.forced, total))} of ${plural(total, 'box')} the rule forced out` : `${plural(total, 'box')} the rule forbids`;
        el.nowAlso.textContent = split.known && now.also ? `${count(now.also)} the search also rearranged` : '';
      }
      const label = 'Replan under this rule';
      const gauges = Object.fromEntries([...host.querySelectorAll('.v6r-gauges span')].map((n) => [n.dataset.g, n]));
      function plainGauges() { // the panel's gauges in plain words, from the same measured frame
        const f = STORY.frameAt((run.B.physics || {}).frames, run.shownK);
        for (const [name, row] of Object.entries(STORY.plainGauges(f))) { const n = gauges[name]; n.classList.toggle('bad', row.bad); n.querySelector('i').textContent = row.text; }
      }
      const live = d.live_available && d.live; // its own sentence, off the stored lattice: the press below is the planner's confirmation of this reading
      function goButtons() { // the recorded answer is offered by name only where nothing was confirmed; a misread reading gets no way round
        const rec = ctx.interactive && run.state.offerRecorded && !run.live ? '<button type="button" class="v6-btn ghost" data-recorded="1">Show the recorded answer</button>' : '';
        el.go.innerHTML = `<button type="button" class="v6-btn">${run.mode === 'hold' && !run.live ? 'Play the plan before' : label}</button>${rec}${live ? `<button type="button" class="v6-btn ghost" data-live="1">Confirm and run live</button><span class="v6r-why" style="display:block;margin-top:8px;font-size:15px;color:#ffa53a">“${esc(live.sentence)}” · ${esc(live.rule)} · ${esc(live.reader)} · ${esc(live.label)}</span>` : ''}`;
      }
      goButtons();
      el.go.addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (!b) return;
        if (b.dataset.recorded) { run.recorded = true; view.preload(); return; }
        if (!b.dataset.live) { ctx.release('replan-go'); return; }
        startLive();
      });
      async function startLive() { // --brief-live-replan: real seconds, no percentage, incumbents as they arrive; "Live" only here
        const why = q('.v6r-why'), post = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const said = await post('/api/v6/confirm', d.live.binding), ok = await said.json().catch(() => null);
        if (!said.ok || !ok || !ok.token) { why.textContent = (ok && ok.error) || 'The server did not record this confirmation.'; return; }
        el.go.hidden = true;
        const r = await post('/api/v6/replan', { token: ok.token });
        if (!r.ok) { why.textContent = ((await r.json().catch(() => null)) || {}).error || 'The server refused this replan.'; return; }
        const job = await r.json();
        const tick = async () => {
          const s = await ctx.api('replan/' + job.id);
          ctx.label(`Live · ${Math.round(s.elapsed_s)} s elapsed`);
          run.live = null; d.incumbents = s.incumbents; trail();
          if (s.status === 'running') { setTimeout(tick, 1000); return; }
          ctx.label('');
          if (s.status === 'done' && s.result && s.result.passed && s.result.plan_id) await showLive(s);
          else { d.incumbents = []; trail(); }
          ctx.release('replan-go');
        };
        tick();
      }
      return view;
    } });
})();
