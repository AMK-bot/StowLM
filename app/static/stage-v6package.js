/* v6 chapter 8: package and receipt (shot 10). Data: /api/v6/package.
 *
 *   papers dealt on a desk -> the page turns -> the receipt chain links up -> verify ticks -> one digit changes -> verify fails
 *
 * seek(tau) draws all of it from tau and the measured fixtures, so a take repeats. Interactive use holds at both verify beats:
 * the buttons there start a real job in its own process, and the terminal then shows that job instead of the replay. Only a
 * job that ran here is called live. Elapsed seconds and check states come from the server; there is no percentage anywhere.
 */
(function () {
  const V = window.StageV6;
  const T = { turn: [2.5, 3.2], shrink: [4.3, 4.9], chain: [4.7, 7.0], term: 7.0, verify: [7.3, 9.6], tamper: [9.8, 13], end: 13 };
  const PAPERS = [  // resting place on the desk: x, y, degrees, when it lands. A paper the package lacks isn't dealt.
    { id: 'bay', x: 14, y: 96, r: -2, at: 0.1 },
    { id: 'crane', x: 846, y: 112, r: 1.8, at: 0.7 },
    { id: 'stab', x: 1208, y: 96, r: -1.4, at: 1.2 },
    { id: 'note', x: 1496, y: 112, r: 2, at: 1.6 },
    { id: 'gaps', x: 40, y: 730, r: 1.1, at: 2.0 },
    { id: 'work', x: 690, y: 722, r: -1.2, at: 2.3 },
    { id: 'list', x: 1300, y: 734, r: 0.9, at: 2.6 },
  ];
  /* Where check i of n lands in a replay: the three fast ones early, the checker's group near the end. */
  const tick = (i, n) => (i < 3 ? 0.14 + 0.12 * i : 0.84 + 0.13 * (i - 3) / Math.max(1, n - 4));
  const clamp = (t) => Math.max(0, Math.min(1, t));
  const ease = (t) => 1 - Math.pow(1 - clamp(t), 3);
  const span = (tau, [a, b]) => clamp((tau - a) / (b - a));
  const secs = (s) => (s == null ? '' : `${Number(s).toFixed(s < 1 ? 2 : 1)} s`);
  /* In the recorded playback (window.__play, set by its shim) a "live" job is the app's recorded verify, replayed (logic14 M12). */
  const playback = () => !!window.__play;
  const prefix = (h, n) => String(h || '').slice(0, n || 10);
  const firstSentence = (s) => (String(s).match(/^.*?[.](?=\s|$)/) || [s])[0];

  /* The replay of one measured run at progress p in [0, 1]: which checks have landed. Pure. */
  function replayChecks(run, p) {
    const n = run.checks.length;
    return run.checks.map((row, i) => {
      if (p >= tick(i, n)) return row;
      return { name: row.name, state: i === 0 || p >= tick(i - 1, n) ? 'running' : 'pending', at_s: null, errors: [] };
    });
  }
  /* What the terminal shows at tau from measured data alone. Pure. */
  function replayAt(tau, m) {
    const out = { verify: null, tamper: null };
    if (m.verify && tau > T.verify[0]) {
      const p = span(tau, T.verify);
      out.verify = { ...m.verify, live: false, checks: replayChecks(m.verify, p), finished: p >= 1 };
    }
    if (m.tamper && tau > T.tamper[0]) {
      const p = span(tau, [T.tamper[0] + 0.7, T.tamper[0] + 2.1]);
      out.tamper = { ...m.tamper, live: false, checks: replayChecks(m.tamper, p), finished: p >= 1 };
    }
    return out;
  }
  const fromJob = (job) => (job ? { ...job, finished: job.status !== 'running' } : null);
  /* What this page's own verify run earned. "rechecked now" takes the whole run passing on the exact package on screen;
   * anything else that finished takes the papers back and says why. Pure. */
  function outcome(d, job) {
    if (!job || job.status === 'running') return { label: d.label, withdrawn: false, failure: null };
    const rows = job.checks || [], failed = rows.filter((c) => c.state !== 'passed').map((c) => c.name);
    const same = job.package_sha256 === d.package_sha256;
    const ran = job.status === 'done' && job.passed === true && same && rows.length > 0 && !failed.length;
    if (ran) return { label: playback() ? d.label : ran && d.fresh ? d.fresh : d.label, withdrawn: false, failure: null }; // a replayed job checked nothing now
    const why = !same ? 'it ran on a different package' : job.status !== 'done' ? `it did not finish: ${job.reason || job.status}` : failed.length ? `failed ${failed.join(', ')}` : 'it did not pass';
    return { label: 'Plan papers withdrawn', withdrawn: true, failure: `Plan papers withdrawn. Verification ${why}.` };
  }

  function noteHtml(text, esc) {
    return String(text).split('\n').filter((l) => l.trim()).map((line) => {
      if (line.startsWith('# ')) return `<h4>${esc(line.slice(2))}</h4>`;
      if (line.startsWith('- ')) return `<p class="li">${esc(line.slice(2))}</p>`;
      return `<p>${esc(line)}</p>`;
    }).join('');
  }
  function stabilityHtml(s, esc) {
    const f = (v, n) => (v == null ? '—' : Number(v).toFixed(n));
    const pct = (v) => (v == null ? '—' : `${(100 * v).toFixed(0)} % of limit`);
    const rows = [['GM', `${f(s.gm_m, 2)} m`], ['Trim', `${f(s.trim_m, 2)} m`], ['Heel', `${f(s.heel_deg, 3)}°`], ['Draught aft', `${f(s.draught_aft_m, 2)} m`], ['Draught forward', `${f(s.draught_fwd_m, 2)} m`],
      ['Draught limit', `${f(s.max_draught_m, 2)} m`], ['Peak shear', pct(s.max_sf_ratio)], ['Peak bending', pct(s.max_bm_ratio)]];
    return `<table>${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table><p class="fine">${esc(s.scope)}</p><p class="fine">${esc(s.securing_note)}</p>`;
  }
  function craneHtml(c, esc) {
    const head = c.header.map((h) => `<th>${esc(h.replace('lift_order', '#'))}</th>`).join('');
    const body = c.rows.map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join('')}</tr>`).join('');
    return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table><p class="fine">First ${c.rows.length} of ${c.total} rows</p>`;
  }
  function gapsHtml(rows, esc) {
    return `<dl>${rows.map((row) => `<div><dt>${esc(row.name)}</dt><dd>${esc(firstSentence(row.text))}</dd></div>`).join('')}</dl>`;
  }
  function workHtml(w, esc) {
    const list = w.max_list_deg == null ? '' : `${w.flagged} of ${w.lifts} quay lifts over ${w.threshold_deg}° estimated list, peak ${Number(w.max_list_deg).toFixed(2)}°. Estimate, not a stability check.`;
    return `<p class="hold">${esc(w.status)}</p><p class="fine">${esc(list)}</p><p class="fine">${esc(w.ballast)}</p><p class="tanks">${w.tanks.map((t) => `<span>${esc(t.tank)} <b>${esc(t.t)} t</b></span>`).join('')}</p>`;
  }
  function listHtml(m, esc) {
    return `<table>${m.rows.map((r) => `<tr><td>${esc(r.box)}</td><td>${esc(r.slot)}</td><td>${esc(r.pod)}</td><td>${esc(r.t)} t</td><td>${esc(r.what)}</td></tr>`).join('')}</table>`;
  }
  function chainHtml(r, esc) {
    const links = r.steps.map((s, i) => `<div class="v6k-link" data-i="${i}">
        <b>${esc(s.stage === 'reader' && !s.model ? 'reader (software pre-check)' : s.stage)}</b>
        <span>${esc(s.mode)} · ${esc(s.model || 'no model, software')}</span>
        <code class="prev">prev ${esc(prefix(s.prev))}</code>
        <code class="own">hash ${esc(prefix(s.hash))}</code>
      </div><i class="v6k-ring" data-i="${i}" aria-hidden="true"></i>`).join('');
    return `${links}<div class="v6k-link v6k-seal" data-i="${r.steps.length}"><b>signature</b><span>Ed25519, local demo key</span><code class="own">${esc(r.signature)}…</code></div>`;
  }

  /* The package's own receipt: who read its sentence, the adapter and pipeline it ran on (first 12), who confirmed. All from receipt.json. */
  function readerHtml(x, esc, film) { // film: shot 10's caption names the manifest once, so the line doesn't repeat it (D296)
    if (!x) return '';
    const parts = [x.name && `reader: ${x.name}`, x.adapter && `adapter ${x.adapter}`, !film && x.pipeline && `built on pipeline ${x.pipeline}`, x.confirmed_by && `Confirmed by: ${x.confirmed_by}`];
    return parts.filter(Boolean).map((t) => ` · <span class="v6k-reader">${esc(t)}</span>`).join('');
  }
  function checkLine(row, esc) {
    const mark = { passed: '✓', failed: '✗', running: '…', pending: ' ' }[row.state];
    const failed = row.state === 'failed';
    return `<div class="v6k-check ${row.state}"><span class="m">${mark}</span><span class="n">${esc(row.name)}</span><span class="t">${esc(secs(row.at_s))}</span>${failed && row.errors[0] ? `<span class="e" title="${esc(row.errors.join(' · '))}">${esc(row.errors[0])}</span>` : ''}</div>`;
  }
  const runLabel = (run) => (!run.live ? 'Replay of a measured run'
    : playback() ? 'Recorded verify from the app, replayed at its recorded pace' : 'Live verify, ran here');
  /* A run's seconds: when its last check landed (the rows' own clock), so the banner and the rows read one figure (logic14 p22);
   * the run's wall time only when no row carries a time. Pure. */
  const runSecs = (run) => { const at = run.checks.map((c) => c.at_s).filter((x) => typeof x === 'number'); return at.length ? Math.max(...at) : run.wall_s; };
  function runBlock(run, title, esc) {
    if (!run) return '';
    const live = runLabel(run);
    const rows = run.checks.map((row) => checkLine(row, esc)).join('');
    const failed = run.checks.filter((c) => c.state === 'failed').map((c) => c.name);
    let end = '';
    if (run.finished && run.status && run.status !== 'done') end = `<p class="v6k-end bad">Verify did not finish: ${esc(run.reason || run.status)}</p>`;
    else if (run.finished && run.passed) end = `<p class="v6k-end ok">Verified in ${esc(secs(runSecs(run)))}, no model, no network</p>`;
    else if (run.finished) end = `<p class="v6k-end bad">FAILED: ${esc(failed.join(', '))}${runSecs(run) ? ` · ${esc(secs(runSecs(run)))}` : ''}</p>`;
    else if (run.live) end = `<p class="v6k-end">${esc(secs(run.elapsed_s))} elapsed</p>`;
    return `<div class="v6k-run"><p class="v6k-cmd"><span>${esc(title)}</span><em>${esc(live)}</em></p>${rows}${end}</div>`;
  }
  /* A finished clean run folds to one line, so the tamper run has the room. */
  function termHtml(state, m, esc) {
    let html = '';
    const clean = state.verify, bad = state.tamper;
    if (clean && bad) {
      const failed = clean.checks.filter((c) => c.state === 'failed').map((c) => c.name);
      html += `<p class="v6k-fold${clean.passed ? '' : ' bad'}">${esc(runLabel(clean))}: ${clean.passed ? 'all checks passed' : `failed ${esc(failed.join(', '))}`} in ${esc(secs(runSecs(clean)))}</p>`;
    }
    else html += runBlock(clean, '$ package verify handover/', esc);
    if (bad) {
      const t = bad.tampered || {};
      if (t.before) html += `<p class="v6k-digit">one digit changed in ${esc(t.file)}, line ${esc(t.line)}: <code>${esc(t.before)} → ${esc(t.after.slice(0, -1))}<b>${esc(t.after.slice(-1))}</b></code></p>`;
      html += runBlock(bad, '$ package verify tampered-copy/', esc);
    }
    return html || '<p class="v6k-cmd"><span>$ package verify handover/</span></p>';
  }

  V.chapter({ id: 'package', order: 80, title: 'Package', feature: 'package', duration_s: T.end, T, replayAt, replayChecks, termHtml, runLabel, runSecs, outcome, readerHtml,
    mount(host, d, ctx) {
      const esc = ctx.esc, m = d.verification || {}, r = d.receipt;
      const by = esc(d.note_by) + (d.note_recorded ? ' · Recorded answer' : '');
      const link = (key, text) => (ctx.interactive ? `<a href="${esc(d.links[key])}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
      const sheet = (page) => `<div class="v6k-sheet p${page}"><iframe title="Bay plan page ${page}" src="${esc(d.bay_plan)}" sandbox="" scrolling="no" tabindex="-1"></iframe></div>`;
      host.innerHTML = `<div class="v6k"><div class="v6k-head">
          ${d.relaxed ? `<p class="v6k-relaxed">${esc(d.relaxed)}</p>` : ''}
          <h2>Handover package</h2><span class="v6k-pill v6k-label">${esc(d.label)}</span>
          ${d.historical ? `<span class="v6k-pill warn">${esc(d.historical)}</span>` : ''}
          ${d.engine_label ? `<span class="v6k-pill ok">${esc(d.engine_label)}</span>` : ''}
          ${d.scope_line ? `<span class="v6k-pill">${esc(d.scope_line)}</span>` : d.boxes ? `<span class="v6k-pill">${esc(d.boxes)} boxes</span>` : ''}
        </div>
        <div class="v6k-desk">
          <figure class="v6k-paper bay" data-p="bay"><div class="v6k-pages">${sheet(2)}${sheet(1)}</div><figcaption>Bay plan · ${esc(d.bay_pages)} pages</figcaption></figure>
          <figure class="v6k-paper doc crane" data-p="crane"><h3>Crane sequence</h3>${craneHtml(d.crane, esc)}<figcaption>${link('crane', 'crane-sequence.csv')}</figcaption></figure>
          <figure class="v6k-paper doc stab" data-p="stab"><h3>Stability summary</h3><p class="fine">${esc(d.stability.checker_passed ? 'Full checker passed' : 'Full checker did not pass')}, ${esc(d.stability.numbers_label)}, departure</p>${stabilityHtml(d.stability, esc)}<figcaption>${link('stability', 'stability.json')}</figcaption></figure>
          <figure class="v6k-paper doc note" data-p="note">${noteHtml(d.note, esc)}<figcaption>${link('note', by)}</figcaption></figure>
          ${(d.not_included || []).length ? `<figure class="v6k-paper doc gaps" data-p="gaps"><h3>Not included in this handover</h3>${gapsHtml(d.not_included, esc)}<figcaption>${link('not_included', 'not-included.html')}</figcaption></figure>` : ''}
          ${d.cargo_work ? `<figure class="v6k-paper doc work" data-p="work"><h3>Cargo work and ballast</h3>${workHtml(d.cargo_work, esc)}<figcaption>${link('cargo_work', 'cargo-work.json')}</figcaption></figure>` : ''}
          ${d.manifest ? `<figure class="v6k-paper doc list" data-p="list"><h3>Manifest, special cargo</h3>${listHtml(d.manifest, esc)}<figcaption>${link('manifest', `manifest.csv · ${d.manifest.rows.length} of ${d.manifest.total} planned boxes shown`)}</figcaption></figure>` : ''}
        </div>
        <div class="v6k-chain"><div class="v6k-links">${chainHtml(r, esc)}</div>
          <p class="v6k-sign">${esc(d.signature_note)}</p>
          <p class="v6k-engine">${link('receipt', 'receipt.json')} · engine: ${esc(r.engine_version)}${readerHtml(r.reader, esc, !ctx.interactive)}${r.build_line ? ` · <span class="v6k-build">${esc(r.build_line)}</span>` : ''}${d.other_sentence ? ` · <span style="color:#ffa53a">${esc(d.other_sentence)}</span>` : ''}</p></div>
        <div class="v6k-term"><div class="v6k-out"></div><p class="v6k-scope"></p>
          <div class="v6k-actions" hidden><button type="button" class="v6-btn" data-a="live"></button><button type="button" class="v6-btn ghost" data-a="replay">Replay the measured run</button></div></div>
      </div>`;
      const root = host.querySelector('.v6k'), desk = host.querySelector('.v6k-desk'), chain = host.querySelector('.v6k-chain');
      const term = host.querySelector('.v6k-term'), out = host.querySelector('.v6k-out'), scope = host.querySelector('.v6k-scope');
      const actions = host.querySelector('.v6k-actions'), liveBtn = actions.querySelector('[data-a=live]');
      const papers = PAPERS.map((p) => ({ ...p, el: host.querySelector(`[data-p=${p.id}]`) })).filter((p) => p.el);
      const labelEl = host.querySelector('.v6k-label'), linkGap = Math.min(0.36, 2.1 / (r.steps.length + 0.5));
      const turning = host.querySelector('.v6k-sheet.p1');
      const links = [...host.querySelectorAll('.v6k-link, .v6k-ring')];
      const live = { verify: null, tamper: null }, memo = new Map();
      let timer = null, beat = null, lastKey = '';
      const put = (el, key, value) => { const k = `${key}`; const map = memo.get(el) || {}; if (map[k] !== value) { map[k] = value; memo.set(el, map); if (k === 'opacity') el.style.opacity = value; else el.style.transform = value; } };

      function fit() {
        const box = host.getBoundingClientRect();
        if (box.width) root.style.transform = `scale(${Math.min(box.width / 1920, box.height / 968)})`;
      }
      function draw(tau, interactive) {
        papers.forEach((p) => {
          const t = ease((tau - p.at) / 0.65);
          put(p.el, 'transform', `translate(${p.x}px, ${p.y + (1 - t) * 980}px) rotate(${p.r + (1 - t) * 14}deg)`);
          put(p.el, 'opacity', tau >= p.at ? '1' : '0');
        });
        const turned = span(tau, T.turn);
        put(turning, 'transform', `rotateY(${-92 * ease(turned)}deg)`);
        put(turning, 'opacity', turned >= 1 ? '0' : '1');
        const s = ease(span(tau, T.shrink));
        put(desk, 'transform', `translate(${6 * s}px, ${28 * s}px) scale(${(1 - 0.5 * s).toFixed(4)})`);
        put(chain, 'opacity', tau >= T.chain[0] ? '1' : '0');
        links.forEach((el) => {
          const i = +el.dataset.i + (el.classList.contains('v6k-ring') ? 0.5 : 0);
          const t = ease((tau - T.chain[0] - i * linkGap) / 0.3);
          put(el, 'opacity', t.toFixed(3));
          put(el, 'transform', `translateY(${(1 - t) * 26}px)`);
        });
        put(term, 'opacity', tau >= T.term ? '1' : '0');
        const replay = replayAt(tau, m);
        const state = { verify: fromJob(live.verify) || replay.verify, tamper: fromJob(live.tamper) || replay.tamper };
        const key = JSON.stringify([state.verify, state.tamper]);
        if (key !== lastKey) {
          lastKey = key; out.innerHTML = termHtml(state, m, esc);
          const paper = outcome(d, live.verify);
          labelEl.textContent = paper.failure || paper.label;
          labelEl.style.color = paper.withdrawn ? '#ff5a5a' : '';
          desk.hidden = paper.withdrawn;
          chain.hidden = paper.withdrawn;
          const shown = state.tamper || state.verify;
          term.classList.toggle('failed', !!(state.tamper && state.tamper.finished));
          scope.textContent = !shown ? '' : shown.live ? 'Ran on this machine, in its own process, just now.' : `Measured seconds from a real run, played back faster. ${m.machine || (m.verify && m.verify.scope) || ''}`;
        }
      }
      function poll(kind, id) {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try {
            live[kind] = await ctx.api('verify/' + id);
          } catch (error) { live[kind] = { ...live[kind], status: 'failed', reason: 'lost the server' }; }
          if (live[kind].status === 'running') poll(kind, id);
          else ctx.release(kind);
        }, 400);
      }
      async function run(kind) {
        actions.hidden = true;
        try {
          const r2 = await fetch('/api/v6/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tamper: kind === 'tamper' }) });
          if (!r2.ok) throw new Error(String(r2.status));
          live[kind] = await r2.json();
          poll(kind, live[kind].id);
        } catch (error) { live[kind] = null; ctx.release(kind); }
      }
      liveBtn.addEventListener('click', () => run(beat));
      actions.querySelector('[data-a=replay]').addEventListener('click', () => { actions.hidden = true; ctx.release(beat); });
      addEventListener('resize', fit);
      return {
        enter() { fit(); const k = ['verify', 'tamper'].find((n) => live[n] && live[n].status === 'running'); if (k) poll(k, live[k].id); },
        leave() { clearTimeout(timer); },
        seek(tau, interactive) {
          if (interactive) {
            const free = (k) => live[k] && live[k].status !== 'running';
            const busy = ['verify', 'tamper'].some((k) => live[k] && live[k].status === 'running');
            let held = null;
            if (!free('verify') && ctx.waitAt(tau, 'verify', T.verify[0])) held = 'verify';
            else if (!free('tamper') && ctx.waitAt(tau, 'tamper', T.tamper[0])) held = 'tamper';
            if (held) tau = T[held][0];
            if (held !== beat) {
              beat = held;
              if (held) liveBtn.textContent = playback() ? (held === 'verify' ? 'Play the recorded verify' : 'Play the recorded tamper check')
                : held === 'verify' ? 'Run verify here' : 'Change one digit, verify the copy';
              actions.hidden = !held || busy;
            }
          }
          draw(Math.max(0, Math.min(tau, T.end)), interactive);
        },
      };
    } });
})();
