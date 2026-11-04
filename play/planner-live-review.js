/* Functional app · review tabs (mode 'live'; functional-app CONTRACT.md Extension B).
 * Export: the package the runner wrote for this call (bay-plan.html, crane-sequence.csv, receipt.json, note-to-master.md,
 * package.json) with sizes and hashes, plus run/outcome.json and case.json, from GET calls/<id>/planner/package.
 * Stress test and Ask copilot aren't live yet: they say so plainly, and nothing is replayed in their place. */
(function () {
  'use strict';
  if (!window.Planner) return;
  const NOT_YET = 'is not in the Functional app yet; nothing is replayed in its place.';
  const shortHash = (h) => (typeof h === 'string' ? h.slice(0, 10) : '');
  const kb = (n) => (typeof n === 'number' ? (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`) : '');
  const ROLE = { 'bay-plan.html': 'Bay plan to print', 'crane-sequence.csv': 'Crane sequence, every lift in order', 'receipt.json': 'Signed receipt: inputs, engine, checker, hashes',
    'note-to-master.md': 'Note to the master: checker statements in a fixed order; no model wrote or selected them in this app. This package doesn\'t name a frozen pipeline: it ran from the working tree, with the pinned engine checked by the runner.', 'package.json': 'Package index' };
  /* The receipt's reader step for this call (code-2 P1-3), from fa-engine's explicit evidence only: run.instruction,
   * run.confirmed_reading and the reader stages' provenance (functional/README.md). Never inferred from history. */
  function readerCaption(v) {
    const run = v.run || {}, readers = (v.stages || []).filter((r) => r.stage === 'reader');
    if (run.instruction === 'none') return 'Planned with no instruction: no reader ran for this plan, and the receipt\'s reader step records an empty brief.';
    if (run.instruction === 'confirmed') {
      const check = readers.find((r) => r.provenance === 'runner_revalidation' && (!run.confirmed_reading || r.reading_id === run.confirmed_reading));
      return check ? 'The StowLM Reader ran once for this instruction, in the Instructions step; the receipt\'s reader step re-validated that confirmed reading with no second model call.'
        : 'The StowLM Reader ran once for this instruction, in the Instructions step; this call has no runner re-validation record for it yet.';
    }
    return 'This call does not record which instruction the plan used, so no reader provenance is stated.';
  }

  for (const [id, order, title] of [['stress', 40, 'Stress test'], ['copilot', 50, 'Ask copilot']]) {
    Planner.stage({
      id, order, title, mode: 'live', features: [], who: [], // not live: no model or software is credited for it
      mount(panel, _data, ctx) {
        panel.innerHTML = `<p class="pl-live-missing">${ctx.esc(title)} ${NOT_YET}</p>`;
        return { start() { ctx.status('Not live yet'); } };
      },
    });
  }

  Planner.stage({
    id: 'handover', order: 60, title: 'Export', mode: 'live', features: [],
    mount(panel, _data, ctx) {
      const esc = ctx.esc;
      let pkg = null, error = null, loading = null;
      function draw() {
        const v = ctx.call;
        if (!v) { panel.innerHTML = '<p class="pl-live-note">Create a call first.</p>'; return; }
        if (error) { panel.innerHTML = `<p class="pl-live-note fail">${esc(error)}</p>`; return; }
        if (!pkg) { panel.innerHTML = '<p class="pl-live-note">Loading the package for this call.</p>'; return; }
        const file = (name) => `api/functional/calls/${encodeURIComponent(v.id)}/file/${name}`;
        const rows = (pkg.files || []).map((f) => `<li><a href="${esc(f.url)}" target="_blank" rel="noopener" download="${esc(f.name)}"><b>${esc(f.name)}</b></a><span>${esc(ROLE[f.name] || '')}</span><small>${esc(kb(f.bytes))} · <code>${esc(shortHash(f.sha256))}</code></small>${f.name === 'receipt.json' ? `<small class="pl-live-receipt-reader">${esc(readerCaption(v))}</small>` : ''}</li>`).join('');
        panel.innerHTML = `${pkg.built ? `<p class="pl-live-note">Written by the runner for this call's plan. Synthetic ship and cargo, simulated port.</p><ul class="pl-live-files">${rows}</ul>`
          : `<p class="pl-live-note">No package was written for this call${v.outcome && v.outcome.status ? ` (${esc(v.outcome.status)})` : ''}: the runner writes one only for a released plan.</p>`}
          <ul class="pl-live-files">
            ${pkg.outcome_url ? `<li><a href="${esc(pkg.outcome_url)}" target="_blank" rel="noopener"><b>outcome.json</b></a><span>The runner's full outcome with the checker evidence</span></li>` : ''}
            <li><a href="${esc(file('case.json'))}" target="_blank" rel="noopener"><b>case.json</b></a><span>How this synthetic case was generated</span></li>
          </ul>`;
        if (pkg.built) { ctx.status(`${pkg.files.length} files`); ctx.done(); }
      }
      function load() {
        if (loading || !ctx.callId()) return loading;
        loading = ctx.data('package').then((p) => { pkg = p; error = null; }).catch((e) => { error = e.message; }).finally(() => { loading = null; draw(); });
        return loading;
      }
      ctx.on('package:ready', () => { pkg = null; load(); });
      return { ownsAction: true, enter() { draw(); load(); }, start() { draw(); return load(); }, leave() {} };
    },
  });
})();
