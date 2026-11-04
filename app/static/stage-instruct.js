/* StowLM stage page: instruction box (task 9.3 prep).
 * Shows the recorded replan and the example sentences. A sentence typed here goes to the BRIEF panel: that's the one path
 * from words to a plan, through the reader and the planner's confirmation. Backend: app/instruct.py. Self-contained: it only
 * adds DOM next to stage.js's.
 * Hidden in ?present=1 unless ?instruct=1. Key I toggles the drawer.
 */
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const PRESENT = params.get('present') === '1';
  if (PRESENT && params.get('instruct') !== '1') return;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v) => typeof v === 'number' && Number.isFinite(v);
  const signed = (v) => (num(v) ? (v > 0 ? `+${v}` : v < 0 ? `−${-v}` : '±0') : '—');
  let info = null, record = null, drawer, btn;

  async function getJSON(url, opts) {
    const r = await fetch(url, Object.assign({ headers: { Accept: 'application/json' } }, opts || {}));
    let body = null; try { body = await r.json(); } catch (e) { /* non-JSON */ }
    if (!r.ok) throw new Error((body && body.error) || `HTTP ${r.status}`);
    return body;
  }
  function currentPlanId() { return params.get('plan_id') || null; }

  function constraintText(c) {
    if (!c) return '';
    if (c.type === 'close_bay') return `Close bay ${String(c.bay).padStart(2, '0')} to all loading`;
    if (c.type === 'max_tiers') return `At most ${c.max_tiers} tier${c.max_tiers === 1 ? '' : 's'} per stack ${c.zone === 'deck' ? 'on deck' : 'in the hold'}${c.bay == null ? ' in every bay' : ` in bay ${String(c.bay).padStart(2, '0')}`}`;
    if (c.type === 'dg_keep_away') return `IMDG class ${c.imdg_class} at least ${c.min_bays} bay${c.min_bays === 1 ? '' : 's'} from ${c.away_from.kind === 'bay' ? `bay ${String(c.away_from.bay).padStart(2, '0')}` : 'the accommodation'}`;
    return c.type;
  }
  const lifts = (c) => (c && num(c.total_lifts) ? c.total_lifts : null);
  const later = (c) => (c && num(c.projected_later_restows) ? c.projected_later_restows + (c.projected_later_hatch_lifts || 0) : null);

  function build() {
    const head = document.querySelector('.ship-panel .panel-head');
    const wrap = $('ship-wrap');
    if (!head || !wrap) return false;
    btn = document.createElement('button');
    btn.type = 'button'; btn.id = 'ins-btn'; btn.className = 'ins-btn';
    btn.title = 'Type a planner instruction; Muse Spark parses it, the search software replans, the checker verifies (key I)';
    btn.setAttribute('aria-pressed', 'false');
    btn.innerHTML = '<span class="ins-ic">⌨</span>INSTRUCT';
    const xr = $('xray-btn');
    head.insertBefore(btn, xr || null);
    drawer = document.createElement('aside');
    drawer.className = 'ins-card'; drawer.id = 'ins-card'; drawer.hidden = true;
    drawer.innerHTML = `
      <div class="ins-title">PLANNER INSTRUCTION <span class="ins-src" id="ins-mode"></span><button type="button" class="ins-x" id="ins-close" aria-label="Close">×</button></div>
      <form class="ins-form" id="ins-form" autocomplete="off">
        <input id="ins-input" type="text" maxlength="500" placeholder="e.g. Keep class 8 at least one bay from bay 1." aria-label="Instruction">
        <button type="submit" id="ins-apply">Apply</button>
      </form>
      <div class="ins-chips" id="ins-chips"></div>
      <div class="ins-out" id="ins-out"></div>`;
    wrap.appendChild(drawer);
    btn.onclick = () => toggle();
    $('ins-close').onclick = () => toggle(false);
    $('ins-form').onsubmit = (e) => { e.preventDefault(); submit($('ins-input').value); };
    window.addEventListener('keydown', (e) => {
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
      if (e.key === 'i' || e.key === 'I') { e.preventDefault(); toggle(); }
    });
    return true;
  }
  function toggle(on, focus = true) {
    const open = on == null ? drawer.hidden : on;
    drawer.hidden = !open; btn.setAttribute('aria-pressed', String(open));
    if (open && focus) setTimeout(() => $('ins-input').focus(), 0);
  }

  function renderChips() {
    if (!info) return;
    $('ins-mode').textContent = info.live ? 'Muse Spark · live' : 'Muse Spark · recorded answers';
    $('ins-mode').title = info.reason || 'Live Muse Spark parse for sentences without a recorded answer';
    $('ins-chips').innerHTML = (info.examples || []).map((x) => `<button type="button" class="ins-chip${x.refused ? ' ref' : ''}${x.recorded ? '' : ' miss'}" data-s="${esc(x.sentence)}" title="${esc(x.kind)}${x.recorded ? ' · recorded Spark answer' : ' · no recorded answer: refused offline'}">${esc(x.sentence)}</button>`).join('');
    for (const b of $('ins-chips').querySelectorAll('button')) b.onclick = () => { $('ins-input').value = b.dataset.s; submit(b.dataset.s); };
  }

  function stepsHtml(job) {
    const names = { parse: 'Parse (Muse Spark)', apply: 'Apply (tighten-only)', replan: 'Replan', checker: 'Full checker', honoured: 'Instruction honoured' };
    const rows = (job.steps || []).map((s) => `<li class="${s.ok === true ? 'ok' : s.ok === false ? 'no' : 'na'}"><span class="mk">${s.ok === true ? '✓' : s.ok === false ? '✗' : '–'}</span><b>${esc(names[s.step] || s.step)}</b><span class="dt">${esc(s.step === 'parse' && s.ok ? '' : s.detail)}</span></li>`);
    if (job.status === 'queued' || job.status === 'running') rows.push(`<li class="run"><span class="mk spin"></span><b>${esc(job.stage)}</b><span class="dt">${num(job.elapsed_s) ? job.elapsed_s.toFixed(0) + ' s' : ''}</span></li>`);
    return `<ul class="ins-steps">${rows.join('')}</ul>`;
  }
  function parsedHtml(job) {
    const p = job.parse || {};
    if (!job.constraint && !p.raw) return '';
    const src = p.recorded ? 'recorded Spark answer' : p.raw ? `live Spark · $${esc(p.cost_usd)}` : '';
    return `<div class="ins-parsed"><div class="ip-h">Parsed constraint <span class="ins-src" title="${esc(p.source)}">${esc(src)}</span></div>` +
      (job.constraint ? `<div class="ip-en">${esc(constraintText(job.constraint))}</div><code>${esc(JSON.stringify(job.constraint))}</code>`
        : p.raw ? `<div class="ip-en">No constraint: the parser refused</div><code>${esc(p.raw)}</code>` : '') +
      (job.applied ? `<div class="ip-ap">${esc(job.applied)}</div>` : '') + '</div>';
  }
  function costHtml(rec, linkNew) {
    const o = rec.original || {}, n = rec.replanned || {};
    const ol = lifts(o.counts), nl = lifts(n.counts), d = rec.delta_lifts;
    const okc = n.checker_passed, hon = rec.honoured;
    const shown = rec.shown === true; // the server's combined gate; no other field can offer the plan
    return `<div class="ins-cost">
      <div class="ic-row"><span>Original plan</span><b>${num(ol) ? ol : '—'}</b><i>lifts${num(later(o.counts)) ? ` · +${later(o.counts)} later` : ''}</i></div>
      <div class="ic-row new"><span>${shown ? 'Replanned' : 'Replan attempt (not shown as a plan)'}</span><b>${num(nl) ? nl : '—'}</b><i>lifts${num(later(n.counts)) ? ` · +${later(n.counts)} later` : ''}</i></div>
      <div class="ic-delta${num(d) && d > 0 ? ' worse' : ''}">Instruction cost: <b>${signed(d)} lift${Math.abs(d) === 1 ? '' : 's'} now</b>${num(rec.delta_later) ? ` · ${signed(rec.delta_later)} at later ports` : ''}</div>
      <div class="ic-ver"><span class="${okc ? 'ok' : 'no'}">${okc ? '✓' : '✗'} Full checker ${okc ? 'PASS' : 'FAIL'}</span><span class="${hon ? 'ok' : 'no'}" title="${esc((rec.honoured_check || {}).method)}">${hon ? '✓' : '✗'} Instruction honoured</span></div>
      ${(o.breaks_instruction || []).length ? `<div class="ic-why">The original plan broke it at ${o.breaks_instruction.length} placement${o.breaks_instruction.length === 1 ? '' : 's'}.</div>` : ''}
      ${!shown ? notShownHtml(rec) : linkNew ? `<a class="ins-go" href="${esc(planHref(n.plan_id))}">Show replanned plan ▸</a>` : `<a class="ins-back" href="${esc(planHref(o.plan_id))}">◂ Back to the original plan</a>`}
    </div>`;
  }
  function notShownHtml(rec) {
    const r = rec.refusal || {}, why = (r.why || []).join('; ') || 'the presentation gate did not pass';
    const counts = [[r.move_violations, 'placement violation'], [r.state_violations, 'final-state violation'], [r.checker_violations, 'checker violation']]
      .filter(([v]) => num(v) && v > 0).map(([v, w]) => `${v} ${w}${v === 1 ? '' : 's'}`).join(' · ');
    return `<div class="ins-refused"><div class="ir-h">NOT SHOWN AS A PLAN</div><div>${esc(why)}</div>${counts ? `<div>${esc(counts)}</div>` : ''}<div class="ir-f">Diagnostic only; the current plan stands.</div></div>`;
  }
  function planHref(id) { const q = new URLSearchParams(location.search); q.set('plan_id', id); q.delete('step'); q.set('instruct', '1'); return '?' + q.toString(); }

  function renderJob(job) {
    let h = `<div class="ins-said">“${esc(job.sentence)}”</div>` + parsedHtml(job) + stepsHtml(job);
    if (job.status === 'refused' && job.refusal) {
      h += `<div class="ins-refused"><div class="ir-h">REFUSED · ${esc(String(job.refusal.code).replace(/_/g, ' '))}</div><div>${esc(job.refusal.reason)}</div><div class="ir-f">Nothing was applied; the current plan stands.</div></div>`;
    } else if (job.status === 'error') {
      h += `<div class="ins-refused"><div class="ir-h">ERROR</div><div>${esc(job.error)}</div><div class="ir-f">Nothing was applied; the current plan stands.</div></div>`;
    } else if (job.status === 'done' && job.result) {
      if (job.result.replanned) h += costHtml(job.result, true);
      else h += `<div class="ins-cost"><div class="ic-ver"><span class="ok">✓ Already honoured</span><span class="${job.result.checker_passed ? 'ok' : 'no'}">${job.result.checker_passed ? '✓ Full checker PASS' : '✗ Full checker FAIL'}</span></div><div class="ic-why">The current plan places nothing the instruction forbids, so no replan is needed (0 extra lifts).</div></div>`;
    }
    $('ins-out').innerHTML = h;
    $('ins-chips').classList.add('compact');
  }

  function submit(sentence) {
    sentence = String(sentence || '').trim(); if (!sentence) return;
    const brief = $('brf-btn');
    if (brief) {
      if ($('brf-panel') && $('brf-panel').hidden) brief.click();
      const box = $('brf-text'); if (box) { box.value = sentence; box.dispatchEvent(new Event('input', { bubbles: true })); box.focus(); }
    }
    $('ins-out').innerHTML = `<div class="ins-said">“${esc(sentence)}”</div><div class="ip-ap">${brief ? 'Sent to the BRIEF panel: review it there, confirm the reading, then plan.' : 'Open the BRIEF panel to review this sentence and confirm its reading.'}</div>`;
  }

  function renderRecordBadge() {
    if (!record) return;
    // On a replanned plan: a pill in the panel head and the original-vs-replanned card in the drawer. (A strip in the
    // race panel was tried: no room at 1920x1080 without overlapping its bars.)
    const pill = document.createElement('span');
    pill.className = 'ins-pill' + (record.shown === true ? '' : ' bad');
    pill.title = `Replanned under: “${record.sentence}”`;
    pill.innerHTML = `<b>${record.honoured ? '✓' : '✗'}</b> ${esc(constraintText(record.constraint))}`;
    btn.parentNode.insertBefore(pill, btn);
    $('ins-out').innerHTML = `<div class="ins-said">“${esc(record.sentence)}”</div>` + parsedHtml({ parse: record.parse, constraint: record.constraint, applied: record.applied }) + costHtml(record, false);
    $('ins-chips').classList.add('compact');
  }

  // D144: the recorded solver replan's rule check, recomputed by the backend from the recorded constraint.
  function recordedHtml(x) {
    const own = x.not_stowlm === false; // D170: StowLM's replan under a recorded Spark parse; every other record stays a solver replan
    const beforeName = own ? 'StowLM plan' : 'search plan', afterName = own ? 'StowLM replan' : 'search software replan';
    const flip = (x.flagged || []).map((f) => `<div class="ir-flip">
        <div class="irf-box">${esc(f.box)}${f.imdg_class ? ` <span>IMDG class ${esc(f.imdg_class)}</span>` : ''}</div>
        <div class="irf-row bad"><span>Before</span><i>${beforeName} \u2192 ${esc(f.before_slot)}</i><b>\u2715 breaks instruction</b></div>
        <div class="irf-row ${f.after_honoured ? 'good' : 'bad'}"><span>After</span><i>${afterName} \u2192 ${esc(f.after_slot || '\u2014')}</i><b>${f.after_honoured ? '\u2713 honoured' : '\u2715 not honoured'}</b></div>
      </div>`).join('');
    const b = x.before_counts || {}, a = x.after_counts || {};
    const d = num(a.weighted) && num(b.weighted) ? a.weighted - b.weighted : null;
    const parse = `<div class="ins-parsed"><div class="ip-h">Constraint <span class="ins-src" title="${esc(typeof x.parse === 'string' ? x.parse : (x.parse || {}).raw_answer)}">${own ? 'recorded Spark parse' : 'authored · not a Spark parse'}</span></div><div class="ip-en">${esc(constraintText(x.constraint))}</div><code>${esc(JSON.stringify(x.constraint))}</code><div class="ip-ap">${esc(x.applied)}</div></div>`;
    if (!x.presented) return `<div class="ins-said">\u201c${esc(x.sentence)}\u201d</div>${parse}${flip}<div class="ins-refused"><div class="ir-h">NOT SHOWN AS A PLAN</div><div>${esc(x.refusal)}</div></div>`;
    return `<div class="ins-said">\u201c${esc(x.sentence)}\u201d</div>${parse}${flip}
      <div class="ins-cost">
        <div class="ic-row"><span>${own ? 'StowLM plan' : 'Search plan'}</span><b>${num(b.weighted) ? b.weighted : '\u2014'}</b><i>wasted lifts \u00b7 ${num(b.total_lifts) ? b.total_lifts : '\u2014'} lifts now</i></div>
        <div class="ic-row new"><span>Replan</span><b>${num(a.weighted) ? a.weighted : '\u2014'}</b><i>wasted lifts \u00b7 ${num(a.total_lifts) ? a.total_lifts : '\u2014'} lifts now</i></div>
        <div class="ic-delta${num(d) && d > 0 ? ' worse' : ''}">Instruction cost: <b>${signed(d)} wasted lift${Math.abs(d) === 1 ? '' : 's'}</b></div>
        <div class="ic-ver"><span class="${x.checker_passed ? 'ok' : 'no'}">${x.checker_passed ? '\u2713 Full checker PASS' : '\u2715 Full checker FAIL'}</span><span class="${x.honoured ? 'ok' : 'no'}" title="${esc(x.method)}">${x.honoured ? '\u2713' : '\u2715'} Instruction honoured</span></div>
      </div><div class="ir-tag">${esc(x.in_frame_label)}</div>`;
  }
  async function showRecorded() {
    const pid = currentPlanId();
    const r = await getJSON('/api/instruct/recorded' + (pid ? '?plan_id=' + encodeURIComponent(pid) : ''));
    if (!pid && r.plan_id) { const q = new URLSearchParams(location.search); q.set('plan_id', r.plan_id); location.replace('?' + q.toString()); return; }
    if (!r.instruction) { $('ins-out').innerHTML = '<div class="ins-refused"><div class="ir-h">NO RECORDED REPLAN</div><div>This launch has no recorded instruction replan (use --video-showcase).</div></div>'; toggle(true, false); return; }
    drawer.classList.add('recorded');
    $('ins-form').hidden = true; $('ins-chips').hidden = true;
    $('ins-mode').textContent = 'recorded'; $('ins-mode').title = r.instruction.label || '';
    $('ins-out').innerHTML = recordedHtml(r.instruction);
    toggle(true, false);
  }

  async function boot() {
    if (!build()) return;
    try { info = await getJSON('/api/instruct/examples'); renderChips(); } catch (e) { $('ins-chips').textContent = `Instruction box unavailable: ${e.message}`; }
    const pid = currentPlanId();
    if (pid) {
      try { const r = await getJSON('/api/instruct/plan?plan_id=' + encodeURIComponent(pid)); record = r.instruction; renderRecordBadge(); } catch (e) { /* not a replanned plan */ }
    }
    if (params.get('instruct') === '1' || params.get('instruct_open') === '1') toggle(true, false);
    if (params.get('instruct_recorded') === '1') { try { await showRecorded(); } catch (e) { $('ins-out').innerHTML = `<div class="ins-refused"><div class="ir-h">REQUEST FAILED</div><div>${esc(e.message)}</div></div>`; toggle(true, false); } }
    // ?instruct_job=<id> re-shows an existing job (read-only GET; for rehearsal and screenshots). A URL never starts one.
    if (params.get('instruct_job')) {
      try { const job = await getJSON('/api/instruct/jobs/' + encodeURIComponent(params.get('instruct_job'))); $('ins-input').value = job.sentence || ''; renderJob(job); toggle(true, false); } catch (e) { /* expired job */ }
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
