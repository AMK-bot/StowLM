/* Planner handover: stored document package and receipt checks run here. */
(function () {
  'use strict';
  const Planner = window.Planner;
  if (!Planner) return;
  const short = (value) => String(value || '—').slice(0, 12);
  const seconds = (value) => value == null ? '' : `${Number(value).toFixed(1)} s`;
  const number = (value, places) => value == null ? '—' : Number(value).toFixed(places);
  const local = (value) => typeof value === 'string' && value.startsWith('/api/v6/asset/') ? value : '';

  Planner.stage({
    id: 'handover', order: 60, title: 'Handover', features: ['package'],
    mount(panel, data, ctx) {
      const d = data.package, r = d.receipt, esc = ctx.esc;
      const packagePipeline = r.reader && r.reader.pipeline;
      const packageBuild = packagePipeline
        ? (/\bPipeline:\s*frozen\./i.test(d.note) ? 'Stored package: built on a frozen pipeline.' : 'Stored package: built on a recorded pipeline.')
        : 'Stored package: pipeline provenance not supplied.';
      const center = document.createElement('section');
      center.className = 'pl-handover-sheet';
      center.setAttribute('aria-label', 'Handover package documents');
      const note = String(d.note).split('\n').filter((line) => line.trim()).map((line) =>
        line.startsWith('# ') ? `<h4 class="pl-handover-title">${esc(line.slice(2))}</h4>` : `<p>${esc(line)}</p>`).join('');
      const stability = d.stability;
      const metrics = [
        ['GM', `${number(stability.gm_m, 2)} m`], ['Trim', `${number(stability.trim_m, 2)} m`],
        ['Heel', `${number(stability.heel_deg, 3)}°`], ['Draught aft', `${number(stability.draught_aft_m, 2)} m`],
        ['Draught forward', `${number(stability.draught_fwd_m, 2)} m`], ['Draught limit', `${number(stability.max_draught_m, 2)} m`],
        ['Peak shear', `${number(stability.max_sf_ratio * 100, 0)} % of limit`],
        ['Peak bending', `${number(stability.max_bm_ratio * 100, 0)} % of limit`],
      ];
      const link = (key, text) => `<a href="${esc(local(d.links[key]))}" target="_blank" rel="noopener">${esc(text)} ↗</a>`;
      center.innerHTML = `<div class="pl-handover-sheet-bar"><div><h2>Handover package</h2><p class="pl-handover-meta">Stored documents · live receipt checks</p></div><button type="button" class="pl-handover-button secondary pl-handover-close" aria-label="Close handover documents">Close</button></div><header class="pl-handover-heading"><p class="pl-handover-meta">${esc(d.scope_line || d.synthetic_scope)}</p><p class="pl-handover-meta pl-handover-plan-label">${esc(d.label)}</p></header>
        <div class="pl-handover-documents" hidden>
          <article class="pl-handover-card pl-handover-document pl-handover-bay" data-document="bay"><header><h3 class="pl-handover-title">Bay plan</h3><a href="${esc(local(d.bay_plan))}" target="_blank" rel="noopener">${esc(d.bay_pages)} pages ↗</a></header><div class="pl-handover-bay-viewport"><iframe title="Bay plan pages" src="${esc(local(d.bay_plan))}" sandbox="allow-same-origin" tabindex="0"></iframe></div><footer class="pl-handover-meta"><button type="button" class="pl-handover-button ghost pl-handover-page-button" data-page="prev" aria-label="Previous bay plan page">←</button><span class="pl-handover-meta pl-handover-page-label">Page 1 of ${esc(d.bay_pages)}</span><button type="button" class="pl-handover-button ghost pl-handover-page-button" data-page="next" aria-label="Next bay plan page">→</button></footer></article>
          <article class="pl-handover-card pl-handover-document pl-handover-crane" data-document="crane"><header><h3 class="pl-handover-title">Crane sequence</h3>${link('crane', 'CSV')}</header><div class="pl-handover-document-body pl-handover-crane-scroll" role="region" aria-label="Crane sequence, scroll horizontally for all columns" tabindex="0"><table class="pl-handover-table"><thead><tr>${d.crane.header.map((h) => `<th>${esc(h.replace('lift_order', '#'))}</th>`).join('')}</tr></thead><tbody>${d.crane.rows.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div><footer class="pl-handover-meta">First ${esc(d.crane.rows.length)} of ${esc(d.crane.total)} rows · Scroll for all columns</footer></article>
          <article class="pl-handover-card pl-handover-document" data-document="stability"><header><div class="pl-handover-actor"><h3 class="pl-handover-title">Stability summary</h3></div>${link('stability', 'JSON')}</header><div class="pl-handover-document-body"><p class="pl-handover-callout ${stability.checker_passed ? 'ok' : 'bad'} pl-handover-checker">${esc(stability.checker_passed ? 'Full checker passed' : 'Full checker did not pass')} · ${esc(stability.numbers_label)} · departure</p><dl class="pl-handover-kv pl-handover-metrics">${metrics.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')}</dl><p class="pl-handover-meta pl-handover-fine">${esc(stability.scope)}</p><p class="pl-handover-meta pl-handover-fine">${esc(stability.securing_note)}</p></div></article>
          <article class="pl-handover-card pl-handover-document" data-document="note"><header><h3 class="pl-handover-title">Note to the master</h3>${link('note', 'MD')}</header><div class="pl-handover-document-body pl-handover-note"><p class="pl-handover-meta">${esc(packageBuild)} This note describes that stored package.</p>${note}</div><footer class="pl-handover-meta">${esc(d.note_by)}${d.note_recorded ? ' · Recorded answer' : ''}</footer></article>
        </div>
        <details class="pl-handover-disclosure pl-handover-inventory" hidden><summary>${esc(d.files.length)} package files · scope and exclusions</summary><div class="pl-handover-file-list">${d.files.map((file) => `<code class="pl-handover-mono">${esc(file)}</code>`).join('')}</div>${d.not_included.map((row) => `<p><b>${esc(row.name)}</b> ${esc(row.text)}</p>`).join('')}${d.cargo_work ? `<p class="pl-handover-callout warn pl-handover-hold">${esc(d.cargo_work.status)}</p><p>${esc(d.cargo_work.ballast)}</p>` : ''}</details>`;
      panel.innerHTML = `<div class="pl-handover-panel"><header class="pl-handover-panel-header"><h2>Handover</h2><p class="pl-handover-sub">Bay plan, crane sequence, stability and a note to the master.</p></header><button type="button" class="pl-handover-button primary pl-handover-build">Build handover package</button><div class="pl-handover-warnings">${[d.relaxed, d.historical, d.other_sentence].filter(Boolean).map((text) => `<p class="pl-handover-callout warn">${esc(text)}</p>`).join('')}</div>
        <div class="pl-handover-receipt" hidden><div class="pl-handover-receipt-title"><p class="pl-handover-title">Handover files are ready</p>${link('receipt', 'Receipt JSON')}</div><p class="pl-handover-meta pl-handover-actor"><span>${esc(d.engine_label || `Engine ${r.engine_version}`)} · ${esc(d.files.length)} files</span></p><p class="pl-handover-meta">${esc(packageBuild)}</p><p class="pl-handover-sub pl-handover-signature-note">${esc(d.signature_note)}</p>
          <details class="pl-handover-disclosure pl-handover-details"><summary>Details</summary>
            <dl class="pl-handover-kv pl-handover-hashes"><dt>Package sha</dt><dd class="pl-handover-mono" title="${esc(d.package_sha256)}">${esc(short(d.package_sha256))}</dd><dt>Plan sha</dt><dd class="pl-handover-mono" title="${esc(r.plan_sha256)}">${esc(short(r.plan_sha256))}</dd><dt>Stored package pipeline sha</dt><dd class="pl-handover-mono">${esc(short(packagePipeline))}</dd><dt>Current app pipeline sha</dt><dd class="pl-handover-mono" title="${esc(r.pipeline_sha256 || '')}">${esc(short(r.pipeline_sha256))}</dd><dt>Signature</dt><dd class="pl-handover-mono">${esc(short(r.signature))}</dd></dl>
            <p class="pl-handover-meta">Current app: ${esc(r.build_line || 'build provenance not supplied')}</p>
            ${r.reader ? `<p class="pl-handover-meta">Reader: ${esc(r.reader.name)} · adapter ${esc(r.reader.adapter)}<br>Confirmed by: ${esc(r.reader.confirmed_by)}</p>` : ''}
            <div class="pl-handover-chain">${r.steps.map((step) => `<div><b>${esc(step.stage)}</b><span class="pl-handover-meta">${esc(step.mode)} · ${esc(step.model || 'no model, software')}</span><code class="pl-handover-mono" title="${esc(step.hash)}">${esc(short(step.hash))}</code></div>`).join('')}</div>
          </details>
          <div class="pl-handover-actions"><button type="button" class="pl-handover-button primary pl-handover-verify">Verify receipt</button><button type="button" class="pl-handover-button ghost pl-handover-tamper" disabled>Tamper one byte</button></div>
          <div class="pl-handover-results" aria-live="polite"></div>
        </div></div>`;
      const buildButton = panel.querySelector('.pl-handover-build');
      const verifyButton = panel.querySelector('.pl-handover-verify');
      const tamperButton = panel.querySelector('.pl-handover-tamper');
      const results = panel.querySelector('.pl-handover-results');
      const papers = center.querySelector('.pl-handover-documents');
      const label = center.querySelector('.pl-handover-plan-label');
      const frame = center.querySelector('iframe');
      const viewport = center.querySelector('.pl-handover-bay-viewport');
      let active = false, epoch = 0, built = false, building = false, busy = false, completed = false, page = 0;
      let live = { verify: null, tamper: null }, runningKind = null, request = null, task = null, buildTask = null, postPending = false;

      function openSheet() {
        ctx.sheet(center);
        if (observer) observer.observe(viewport);
        fitBay();
      }
      function closeSheet() {
        if (observer) observer.disconnect();
        ctx.sheet(null);
      }
      center.querySelector('.pl-handover-close').addEventListener('click', closeSheet);
      function fitBay() {
        const scale = Math.min(viewport.clientWidth / 1512, viewport.clientHeight / 1080);
        if (scale > 0) frame.style.transform = `scale(${scale})`;
      }
      function changePage(delta) {
        page = Math.max(0, Math.min(Math.max(0, d.bay_pages - 1), page + delta));
        center.querySelector('.pl-handover-page-label').textContent = `Page ${page + 1} of ${d.bay_pages}`;
        center.querySelector('[data-page=prev]').disabled = page === 0;
        center.querySelector('[data-page=next]').disabled = page >= d.bay_pages - 1;
        try {
          const section = frame.contentDocument.querySelectorAll('.bay-page')[page];
          if (section) frame.contentWindow.scrollTo(0, section.offsetTop);
        } catch (_) { /* The original asset remains usable if iframe access is unavailable. */ }
      }
      frame.addEventListener('load', () => { changePage(0); fitBay(); });
      center.querySelector('[data-page=prev]').addEventListener('click', () => changePage(-1));
      center.querySelector('[data-page=next]').addEventListener('click', () => changePage(1));
      const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(fitBay) : null;
      function validClean(job) {
        return !!job && job.status === 'done' && job.passed === true && job.package_sha256 === d.package_sha256 && job.checks.length > 0 && job.checks.every((row) => row.state === 'passed');
      }
      function validTamper(job) {
        return !!job && job.status === 'done' && job.tamper === true && job.passed === false && !!job.tampered &&
          job.tampered.before !== job.tampered.after && job.checks.length > 0 &&
          job.checks.every((row) => row.state === 'passed' || row.state === 'failed') &&
          job.checks.some((row) => row.name === 'files' && row.state === 'failed');
      }
      function renderRun(job, kind) {
        if (!job) return '';
        const finished = job.status !== 'running';
        const failures = job.checks.filter((row) => row.state === 'failed');
        const failed = finished && (kind === 'verify' ? !validClean(job) : !job.passed);
        const times = job.checks.map((row) => row.at_s).filter((value) => typeof value === 'number');
        const duration = times.length ? Math.max(...times) : job.wall_s;
        const outcome = !finished ? `${seconds(job.elapsed_s)} elapsed` : job.status !== 'done' ? `Verify did not finish: ${job.reason || job.status}` : kind === 'verify' && job.package_sha256 !== d.package_sha256 ? 'Verification ran on a different package' : failed ? `Failed: ${failures.map((row) => row.name).join(', ') || 'receipt verification'}` : `Verified in ${seconds(duration)}, no model, no network`;
        return `<section class="pl-handover-card pl-handover-run"><p class="pl-handover-title pl-handover-run-heading"><span class="pl-handover-actor"><span>${kind === 'tamper' ? 'Tampered copy' : 'Receipt verification'}</span></span><span class="pl-handover-chip live">Live verify, ran here</span></p>${job.tampered ? `<p class="pl-handover-meta pl-handover-change">${esc(job.tampered.file)}, line ${esc(job.tampered.line)}<br><code class="pl-handover-mono">${esc(job.tampered.before)} → ${esc(job.tampered.after)}</code></p>` : ''}<div class="pl-handover-checks">${job.checks.map((row) => `<div class="pl-handover-callout ${row.state === 'passed' ? 'ok' : row.state === 'failed' ? 'bad' : 'warn'} pl-handover-check"><span>${row.state === 'passed' ? '✓' : row.state === 'failed' ? '✗' : row.state === 'running' ? '…' : '·'} ${esc(row.name)}</span><span class="pl-handover-time">${esc(seconds(row.at_s))}</span>${row.state === 'failed' && row.errors.length ? `<small class="pl-handover-meta" title="${esc(row.errors.join(' · '))}">${esc(row.errors[0])}</small>` : ''}</div>`).join('')}</div><p class="pl-handover-callout ${finished ? failed ? 'bad' : 'ok' : 'warn'} pl-handover-verdict">${esc(outcome)}</p></section>`;
      }
      function render() {
        buildButton.disabled = building;
        buildButton.textContent = building ? 'Preparing documents…' : built ? 'Open documents' : 'Build handover package';
        buildButton.classList.toggle('primary', !built && !building);
        buildButton.classList.toggle('secondary', built || building);

        verifyButton.disabled = busy || !built;
        tamperButton.disabled = busy || !validClean(live.verify);
        tamperButton.hidden = !validClean(live.verify);
        verifyButton.hidden = !built;
        verifyButton.classList.toggle('primary', !validClean(live.verify) && !completed);
        verifyButton.classList.toggle('ghost', validClean(live.verify) || completed);
        tamperButton.classList.toggle('primary', validClean(live.verify) && !validTamper(live.tamper) && !completed);
        tamperButton.classList.toggle('ghost', !validClean(live.verify) || validTamper(live.tamper) || completed);
        const clean = live.verify;
        if (clean && clean.status !== 'running') {
          const withdrawn = !validClean(clean);
          papers.hidden = withdrawn;
          label.classList.toggle('fail', withdrawn);
          label.textContent = withdrawn ? 'Plan papers withdrawn' : (d.fresh || d.label);
        }
        const cleanSummary = live.tamper && clean ? `<p class="pl-handover-callout ok pl-handover-clean-summary">${validClean(clean) ? '✓ Live verify: all checks passed' : 'Receipt verification did not pass'}</p>` : renderRun(clean, 'verify');
        results.innerHTML = cleanSummary + renderRun(live.tamper, 'tamper');
      }
      async function build() {
        if (built) { openSheet(); return; }
        if (building || !active) return;
        building = true;
        const token = epoch;
        openSheet();
        render();
        papers.hidden = false;
        panel.querySelector('.pl-handover-receipt').hidden = false;
        const cards = [...papers.querySelectorAll('.pl-handover-document')];
        for (const card of cards) {
          if (!active || token !== epoch) return;
          card.hidden = false;
          if (typeof card.animate === 'function') card.animate([{ opacity: 0, transform: 'translateY(24px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 500, easing: 'ease-out' });
          await ctx.wait(250);
        }
        if (!active || token !== epoch) return;
        built = true; building = false;
        render();
        center.querySelector('.pl-handover-inventory').hidden = false;
        fitBay(); changePage(0);
        ctx.log({ who: 'Software', where: 'on this laptop', text: `Handover package · ${d.files.length} files`, label: d.label });
        ctx.emit('package:ready', d);
      }
      async function poll(kind, token) {
        while (active && token === epoch && live[kind] && live[kind].status === 'running') {
          await ctx.wait(400);
          if (!active || token !== epoch) return;
          request = new AbortController();
          const response = await fetch('/api/v6/verify/' + encodeURIComponent(live[kind].id), { cache: 'no-store', signal: request.signal });
          if (!response.ok) throw new Error(`Verify server returned ${response.status}`);
          const job = await response.json();
          if (!active || token !== epoch) return;
          live[kind] = job;
          render();
        }
        if (active && token === epoch) {
          busy = false; runningKind = null;
          if (!completed && validClean(live.verify) && validTamper(live.tamper)) { completed = true; ctx.done(); }
          render();
          const job = live[kind];
          ctx.log({ who: 'Software', where: 'on this laptop', text: kind === 'tamper' ? `Tampered copy: ${job.checks.filter((row) => row.state === 'failed').map((row) => row.name).join(', ')}` : validClean(job) ? 'Receipt verification passed' : 'Receipt verification failed', label: 'Live verify, ran here' });
        }
      }
      async function run(kind) {
        if (!built || busy || (kind === 'tamper' && !validClean(live.verify))) return;
        let token = epoch;
        busy = true; postPending = true; runningKind = kind;
        if (kind === 'verify') live.tamper = null;
        render();
        try {
          const job = await ctx.post('verify', { tamper: kind === 'tamper' });
          live[kind] = job; postPending = false;
          if (!active) return;
          token = epoch;
          render();
          if (typeof results.scrollIntoView === 'function') results.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          await poll(kind, token);
        } catch (error) {
          postPending = false;
          if (!active || token !== epoch) return;
          busy = false; runningKind = null;
          render();
          results.insertAdjacentHTML('beforeend', `<p class="pl-handover-callout bad pl-handover-request-error">${esc(error.message)}. Try Verify receipt again.</p>`);
        }
      }
      buildButton.addEventListener('click', () => { if (!building) { buildTask = build(); task = buildTask; } });
      verifyButton.addEventListener('click', () => { task = run('verify'); });
      tamperButton.addEventListener('click', () => { task = run('tamper'); });
      [...papers.querySelectorAll('.pl-handover-document')].forEach((card) => { card.hidden = true; });
      changePage(0);
      return {
        ownsAction: true,
        controls: { build: buildButton, verify: verifyButton, tamper: tamperButton },
        enter() {
          active = true; epoch += 1;
          if (runningKind && live[runningKind] && live[runningKind].status === 'running') {
            const kind = runningKind, token = epoch;
            task = poll(kind, token).catch((error) => { if (active && token === epoch) { busy = false; runningKind = null; render(); results.insertAdjacentHTML('beforeend', `<p class="pl-handover-callout bad pl-handover-request-error">${esc(error.message)}</p>`); } });
          } else { busy = postPending; render(); }
        },
        start() { if (!building) { buildTask = build(); task = buildTask; } return buildTask; },
        leave() { active = false; epoch += 1; building = false; if (request) request.abort(); closeSheet(); },
        async autoplay(autoCtx) {
          const token = epoch;
          if (buildTask) await buildTask;
          if (!active || token !== epoch || !built) return;
          await autoCtx.wait(2200);
          if (!active || token !== epoch) return;
          await autoCtx.click(verifyButton); if (task) await task;
          await autoCtx.wait(2500);
          if (!active || token !== epoch || !validClean(live.verify)) return;
          await autoCtx.click(tamperButton); if (task) await task;
          await autoCtx.wait(2500);
        },
      };
    },
  });
})();
