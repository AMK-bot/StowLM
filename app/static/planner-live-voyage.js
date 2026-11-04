/* Functional app · 1 Ship & call (mode 'live'; functional-app CONTRACT.md Extension B).
 * Before a call: the form (ship, size class, fill with the 70 % envelope mark, generator, port, seed with "New seed"),
 * from GET /api/functional/options; "Create call" → ctx.startCall. After: the generated call's own figures from its
 * case.json (seed and whether it is in any published cohort, measured generation time) and the envelope verdict, each
 * failed test with its measured value and limit. Nothing here is recorded or replayed. */
(function () {
  'use strict';
  if (!window.Planner) return;
  const MEASURED = 'measured on this machine, this run';
  const finite = (v) => typeof v === 'number' && isFinite(v);
  const pct = (v) => (finite(v) ? (Math.round(v * 1000) / 10).toFixed(1) : '—');
  const shortHash = (h) => (typeof h === 'string' ? h.slice(0, 10) : '');

  /* The case's own words for its seed (seed_status.label): "Fresh seed: …", "Published seed: …" or a refusal. */
  function seedWords(status) {
    if (!status) return null;
    const cls = status.sealed ? 'fail' : (status.known_in || []).length ? 'warn' : 'ok';
    return [cls, status.label || (cls === 'ok' ? 'Fresh seed: in no published map set' : 'Published seed')];
  }
  const show = (v) => (Array.isArray(v) ? v.join(' or ') : finite(v) && v > 0 && v < 1 ? (100 * v).toFixed(1) + ' %' : v);

  Planner.stage({
    id: 'voyage', order: 0, title: 'Ship & call', mode: 'live', features: [],
    mount(panel, _data, ctx) {
      const esc = ctx.esc;
      const form = { ship: null, size_class: null, fill_pct: null, generator: null, port: null, seed: '' };
      let options = null, newSeed = null, seedFrom = null, busy = false, error = null, drawn = '';

      function shipAllows(id, size) {
        const ship = ((options && options.ships) || []).find((s) => s.id === id);
        return !ship || !Array.isArray(ship.in_envelope_sizes) || ship.in_envelope_sizes.includes(size);
      }
      const opts = (list, value, label) => (list || []).map((item) => {
        const id = typeof item === 'object' ? item.id : item;
        return `<option value="${esc(id)}"${String(id) === String(value) ? ' selected' : ''}>${esc(label ? label(item) : id)}</option>`;
      }).join('');

      function formHtml() {
        if (!options) return `<p class="pl-meta">${esc(error || 'Loading what this launch offers')}</p>`;
        const fill = options.fill_pct || { min: 10, max: 100, envelope_max: 70 }, envMax = fill.envelope_max || 70;
        const mark = (envMax - fill.min) / ((fill.max - fill.min) || 1), value = Number(form.fill_pct);
        const env = options.envelope || {}, seed = String(form.seed || '').trim();
        const seedHint = !seed ? 'Leave empty for a fresh random seed.'
          : seedFrom === 'new' && seed === String(newSeed) ? 'Fresh seed: a new random seed, in no published map set. Checked again when the call is created.'
            : 'Checked against the published map sets when the call is created; held-out and sealed seeds are refused.';
        return `<div class="pl-live-form">
          <label class="pl-live-field"><span>Ship</span><select data-f="ship">${opts(options.ships, form.ship, (s) => `${s.id} · ${s.teu} TEU`)}</select></label>
          <label class="pl-live-field"><span>Size class</span><select data-f="size_class">${opts(options.size_classes, form.size_class)}</select></label>
          ${shipAllows(form.ship, form.size_class) ? '' : `<p class="pl-live-note warn">${esc(form.ship)} at ${esc(form.size_class)} is outside the envelope; the call will stop at the gate.</p>`}
          <label class="pl-live-field"><span>Fill</span><span class="pl-live-fill"><span class="pl-live-track"><input type="range" data-f="fill_pct" min="${esc(fill.min)}" max="${esc(fill.max)}" step="1" value="${esc(value)}">
            <i class="pl-live-mark" style="left:calc(8px + (100% - 16px) * ${mark.toFixed(4)})" title="Envelope limit ${envMax} %"></i></span><b>${esc(value)} %</b></span></label>
          <p class="pl-live-note${value > envMax ? ' warn' : ''}">${value > envMax ? `Above ${envMax} %: outside the envelope; the call will stop at the gate.` : `The dashed mark is the envelope limit, ${envMax} % of the planned cells.`}</p>
          <label class="pl-live-field"><span>Generator</span><select data-f="generator">${opts(options.generators, form.generator)}</select></label>
          <label class="pl-live-field"><span>Port</span><select data-f="port">${opts(options.ports, form.port)}</select></label>
          <label class="pl-live-field"><span>Seed</span><span class="pl-live-row"><input data-f="seed" inputmode="numeric" value="${esc(seed)}" placeholder="random"><button type="button" class="pl-btn secondary" data-do="seed">New seed</button></span></label>
          <p class="pl-live-note">${esc(seedHint)}</p>
          <p class="pl-live-note">Envelope: up to ${esc(env.max_boxes || 195)} boxes in a half-ship section (tiny and small sections too) of ${esc((env.teu || [500, 800]).join('–'))} TEU feeder ships, fill up to ${Math.round(100 * (env.max_fill || 0.7))} % of that section, empty section on arrival and one loading call. Outside it, the call stops and says why.</p>
          ${error ? `<p class="pl-live-note fail">${esc(error)}</p>` : ''}
          <button type="button" class="pl-btn primary" data-primary data-do="create"${busy ? ' disabled' : ''}>Create call</button>
        </div>`;
      }

      function caseHtml(v) {
        const c = v.case, env = v.envelope || c.envelope || {}, head = c.headline_envelope || null, st = seedWords(c.seed_status);
        const ports = (c.ports || []).filter((p, i, a) => i === 0 || p !== a[i - 1]);
        // Two gates: the runner's envelope check, and the headline envelope (docs/headline.md), which is narrower and stops the call.
        // logic-2 M2: the runner line is the runner's own result (case.json envelope); the call view's envelope is the app's
        // combined gate. The headline envelope (docs/headline.md) is narrower and stops the call.
        const runner = c.envelope || null;
        const headOut = !!(head && head.in_envelope === false), runOut = !!(runner && runner.in_envelope === false) || env.in_envelope === false, out = headOut || runOut;
        const teuOf = (ship) => { const m = /feeder_(\d+)/.exec(String(ship || '')); return m ? Number(m[1]).toLocaleString('en-GB') : null; };
        const why = (r) => (r.test === 'ship' && teuOf(r.measured) ? `Ship ${teuOf(r.measured)} TEU; the envelope is 500–800 TEU feeders` : r.message || r.test);
        const headLine = !head ? '' : `<p class="pl-live-note ${headOut ? 'fail' : 'ok'}">Headline envelope (500–800 TEU feeders): ${headOut ? 'outside: ' + esc((head.reasons || []).map(why).join('; ')) : 'inside'}</p>`;
        const runLine = !runner || runner.in_envelope == null ? '' : `<p class="pl-live-note ${runner.in_envelope === false ? 'fail' : 'ok'}">Runner's envelope gate: ${runner.in_envelope === false ? 'outside' : 'inside (it admits tiny and small sections of any ship)'}</p>`;
        const reasons = [...((head && head.reasons) || []), ...((env.reasons || []).filter((r) => !((head && head.reasons) || []).some((h) => h.test === r.test)))];
        return `<div class="pl-call-card">
            <div class="pl-call-port"><b>${esc(c.ship)} · ${esc(c.size_class)}</b>${ctx.chip('Synthetic ship and cargo, simulated port', 'recorded')}</div>
            <div class="pl-stats">
              <div><b>${esc(c.boxes)}</b><span>boxes to load</span></div>
              <div><b>${finite(c.teu) ? esc(Math.round(c.teu)) : '—'}</b><span>TEU to load</span></div>
              <div><b>${pct(c.fill)} %</b><span>section fill</span></div>
            </div>
            <dl class="pl-live-kv">
              <dt>Rotation</dt><dd>${esc(ports.join(' → '))}</dd>
              <dt>Generator</dt><dd>${esc((c.request || {}).generator || '')} · <code>${esc((c.generator || {}).producer || '')}</code></dd>
              <dt>Seed</dt><dd>${esc(c.seed)} (${c.seed_source === 'random' ? 'new random seed' : 'chosen by the planner'})</dd>
              <dt>Instance</dt><dd><code>${esc(shortHash(c.instance_sha256))}</code></dd>
              <dt>Generated in</dt><dd>${finite(c.wall_s) ? c.wall_s.toFixed(1) : '—'} s, ${MEASURED}</dd>
            </dl>
            ${st ? `<p class="pl-live-note ${st[0]}">${esc(st[1])}</p>` : ''}
          </div>
          ${headLine}${runLine}
          ${out ? `<div class="pl-live-verdict fail"><b>${headOut ? 'Outside the headline envelope' : 'Outside the pipeline envelope'}. No engine run was started.</b><span>Each failed test, with its measured value and limit:</span></div>
            <table class="pl-live-table"><thead><tr><th>Test</th><th>Measured</th><th>Limit</th></tr></thead><tbody>${reasons.map((r) =>
              `<tr title="${esc(r.message)}"><td>${esc(r.test)}</td><td><b>${esc(show(r.measured))}</b></td><td>${esc(show(r.limit))}</td></tr>`).join('')}</tbody></table>`
            : env.in_envelope ? `<p class="pl-live-note ok">Inside the envelope: ${esc((env.numbers || {}).boxes_to_load)} of ${esc((env.numbers || {}).box_limit || 195)} boxes, fill ${pct((env.numbers || {}).physical_cell_fill)} % of ${Math.round(100 * ((env.numbers || {}).fill_limit || 0.7))} %.</p>` : ''}
          <button type="button" class="pl-btn secondary" data-do="new-call">Start a new call</button>`;
      }

      function draw() {
        const v = ctx.call;
        let html;
        if (v && v.case) html = caseHtml(v);
        else if (v && v.state === 'generating') {
          const row = (v.stages || []).filter((s) => s.stage === 'generate').pop();
          const t = row && row.started_utc ? Math.max(0, (Date.now() - Date.parse(row.started_utc)) / 1000) : null;
          html = `<p class="pl-live-progress"><i class="pl-live-spin"></i>Generating a fresh synthetic case${finite(t) ? ` · ${Math.round(t)} s` : ''}</p>`;
        } else if (v && v.state === 'failed') html = `<p class="pl-live-note fail">${esc(v.message || 'The call failed.')}</p><button type="button" class="pl-btn secondary" data-do="new-call">Start a new call</button>`;
        else html = formHtml();
        if (html === drawn) return;
        const focus = document.activeElement && panel.contains(document.activeElement) ? document.activeElement.dataset.f : null;
        panel.innerHTML = html; drawn = html;
        if (focus) { const n = panel.querySelector(`[data-f="${focus}"]`); if (n) n.focus(); }
        if (v && v.case) {
          ctx.status(`${v.case.ship} · ${v.case.size_class} · ${v.case.boxes} boxes · seed ${v.case.seed}`);
          ctx.done();
        } else if (v && v.state === 'generating') ctx.status('Generating the synthetic case');
        clearTimeout(ticker);
        if (v && v.state === 'generating') ticker = setTimeout(draw, 1000); // real elapsed seconds between polls
      }
      let ticker = null;

      async function loadOptions() {
        try {
          options = await ctx.fx.get('options');
          const d = options.defaults || {};
          form.ship = form.ship || d.ship || ((options.ships || [])[0] || {}).id;
          form.size_class = form.size_class || d.size_class || (options.size_classes || [])[0];
          form.fill_pct = form.fill_pct == null ? d.fill_pct || (options.fill_pct || {}).default || 60 : form.fill_pct;
          form.generator = form.generator || d.generator || (options.generators || [])[0];
          form.port = form.port || d.port || (options.ports || [])[0];
          if (options.new_seed != null && !form.seed) { form.seed = String(options.new_seed); newSeed = options.new_seed; seedFrom = 'new'; }
        } catch (e) { error = e.message; }
        draw();
      }

      panel.addEventListener('input', (e) => {
        const f = e.target && e.target.dataset ? e.target.dataset.f : null; if (!f) return;
        form[f] = e.target.value; if (f === 'seed') seedFrom = 'planner';
        if (e.target.tagName === 'SELECT' || e.target.type === 'range') draw();
      });
      panel.addEventListener('change', (e) => { const f = e.target && e.target.dataset ? e.target.dataset.f : null; if (f) { form[f] = e.target.value; draw(); } });
      panel.addEventListener('click', async (e) => {
        const b = e.target.closest && e.target.closest('[data-do]'); if (!b || b.disabled) return;
        if (b.dataset.do === 'new-call') { ctx.newCall(); return; }
        if (b.dataset.do === 'seed') {
          try { const o = await ctx.fx.get('options'); if (o.new_seed != null) { form.seed = String(o.new_seed); newSeed = o.new_seed; seedFrom = 'new'; } } catch (err) { ctx.toast(err.message); }
          draw(); return;
        }
        if (b.dataset.do === 'create') {
          const seed = String(form.seed || '').trim();
          if (seed && !/^\d+$/.test(seed)) { error = 'The seed is a whole number, or leave it empty for a random one.'; draw(); return; }
          busy = true; error = null; draw();
          try {
            await ctx.startCall({ ship: form.ship, size_class: form.size_class, fill_pct: Number(form.fill_pct), generator: form.generator, port: form.port, seed: seed ? Number(seed) : null });
            ctx.log({ who: 'Software', where: 'on this laptop', text: 'Created a synthetic call' });
          } catch (err) { error = err.message; }
          busy = false; draw();
        }
      });
      ctx.on('call:update', draw);
      draw();
      if (!ctx.call) loadOptions();
      return { ownsAction: true, enter() { draw(); }, leave() {} };
    },
  });
})();
