/* v6 chapter 3: the planner confirms the reading. Who read it comes from evidence, never from configuration: the
 * on-device states below only appear when the evidence says on_device. Data: /api/v6/reading.
 * Confirm is a server event: POST /api/v6/confirm returns a token bound to this sentence, rule and ship, kept on StageV6. The stored
 * replan shows the plan under the rule only for the reading selected here (StageV6.selectedReading: a live reading's id, or null
 * for the recorded one) with that token, or for the recorded answer chosen by name (code review 12, P2). The pick survives leaving
 * and re-entering this chapter. A live run confirms its own reading on its own button (stage-v6replan.js). The film's scripted
 * press posts nothing: the film plays the recorded answer, labelled. */
(function () {
  'use strict';
  const V = window.StageV6, STORY = window.StageStory, { clamp, lerp, outCubic, inOut, fit, put, cursorHTML } = window.V6FX;
  const pretty = (x) => JSON.stringify(x, null, 2).replace(/\{\n\s+([^{}\n]*)\n\s+\}/g, '{ $1 }'); // a one-field object stays on one line
  const tokens = (text) => text.match(/"(?:[^"\\]|\\.)*"(?=:)|"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null|[{}[\],:]|\s+/g) || [text];
  /* A bar's label: the server's rounded value, the exact one in its title. Pure. */
  const barLabel = (b, side, esc) => `<em title="${esc((b.exact || {})[side] || `exact: ${b[side]} %`)}">${esc((b.shown || {})[side] || `${Math.round(b[side])} %`)}</em>`;
  /* showcase/reader/recordings.json through the server: this rule's prepared wordings as the adopted reader read each one, misses in place,
   * and the file's other misses named under them. Every count is the rows'. No file: no panel. Pure. */
  const outcome = (r, esc) => r.refused ? `<em class="no">refused as ${esc(r.refusal_code)}</em>`
    : !r.as_expected ? `<em class="no">misread as “${esc(r.read || '')}”</em>` : r.status === 'exact' ? '<em>read to this rule</em>' : `<em>${esc(r.status.replace(/_/g, ' '))}</em>`;
  /* Of the hero rule's wordings (radio selection, app/v6.py wording_counts): exact typed and exact radio readings, as counts. Pure. */
  /* The exact readings the counts name that the list below doesn't show, so the rows add up to the counts (logic14 p22). Pure. */
  const notShown = (w, rows) => {
    if (!w || !Array.isArray(rows)) return '';
    const typed = w.typed_exact - rows.filter((r) => !r.heard && r.as_expected && !r.refused).length;
    const radio = w.radio_exact - rows.filter((r) => r.heard && r.as_expected && !r.refused).length;
    const parts = [typed > 0 ? (w.template && typed === 1 ? 'template' : `${typed} typed reading${typed === 1 ? '' : 's'}`) : '',
      radio > 0 ? `${radio} radio reading${radio === 1 ? '' : 's'}` : ''].filter(Boolean);
    return parts.length ? ` (${parts.join(' and ')} not shown)` : '';
  };
  const wordingsLine = (w, rows) => (w && w.total ? `Of ${w.total} wordings (${w.written} written${w.template ? ' + the template' : ''}): ${w.typed_exact} typed and ${w.radio_exact} radio transcripts read exactly; the rest refused${notShown(w, rows)} · selected wording; counts, not a rate` : '');
  const prepared = (p, said, esc, w) => {
    if (!p || !p.rows || !p.rows.length) return '';
    const c = p.counts, h = p.here, ad = (p.adapter_sha256 || '').slice(0, 12);
    const other = (p.elsewhere || []).map((r) => `<li>“${esc(r.sentence)}”: ${r.refused ? `refused as ${esc(r.refusal_code)}` : `read as “${esc(r.read || '')}”`}; stored rule “${esc(r.stored || '')}”</li>`).join('');
    return `<div class="v6t-prep" title="${esc(p.source)} · sha256 ${esc(p.sha256)}">
      <h3>${esc(p.label)} · ${esc(where(p.reader))} · adapter ${esc(ad)} · prepared wordings of this rule, each read once</h3>
      <ul>${p.rows.map((r) => `<li class="${r.as_expected ? '' : 'miss'}${r.sentence === said ? ' now' : ''}"><span>“${esc(r.sentence)}”${r.heard ? ' <small>radio transcript</small>' : ''}</span>${outcome(r, esc)}</li>`).join('')}</ul>
      <p>${wordingsLine(w) ? `${esc(wordingsLine(w, p.rows))} · ` : ''}all ${c.total} prepared sentences in the recording: ${c.as_expected} as expected, ${c.missed} misread or refused${c.no_stored_rule ? `, ${c.no_stored_rule} with no stored rule to compare` : ''}</p>
      ${other ? `<ul class="v6t-prep-other">${other}</ul>` : ''}
    </div>`;
  };
  /* In the recorded playback (window.__play) the reader ran on the presenter's laptop, not the visitor's (logic14 p22). */
  const where = (t) => (window.__play && t ? String(t).replace(/\bon this (laptop)\b/g, "recorded on the presenter's $1") : t);
  const T = { json: [1.1, 5.2], plain: [5.4, 7.0], bars: 7.4, scope: 9.8, confirm: 11.2, press: 13.2, end: 16 };
  /* Interactive, with a live reader attached (d.live_reader): the beat holds before the reading appears, and the presenter reads the
   * sentence on screen live (POST /api/v6/read, the hero ship's own context) or keeps the recorded reading. A live answer redraws the
   * chapter from the server's reply: mode, reader and seconds come from its evidence, and Confirm then binds that reading. The film never
   * holds here and never posts. Pure helpers: liveData merges the reply over the recorded chapter; liveRefusal says why nothing came. */
  const READ_AT = 0.9;
  const liveData = (d, live) => Object.assign({}, d, live, { peak_gb: null, adapter_mb: null, stand_in_note: null, label: null, live_reader: false, seconds_note: null });
  /* The time fact: a live read's own seconds; a recorded one is the timing run's warm median, said as one (M3). Pure. */
  const secondsFact = (d) => (d.seconds == null ? null : d.seconds_note
    ? ['Reading time', `About ${d.seconds.toFixed(1)} s a sentence (${d.seconds_note})`] : ['Read in', `${d.seconds.toFixed(1)} s`]);
  const liveRefusal = (live) => {
    const ref = (live && live.answer && live.answer.refusal) || null;
    if (ref) return `Read live: refused as ${ref.code}${ref.reason ? `: ${ref.reason}` : ''}. Nothing to confirm.`;
    return (live && live.message) || 'The live reader gave no reading. Nothing to confirm.';
  };
  V.chapter({ id: 'reading', order: 30, title: 'Reading', feature: 'reading', duration_s: 16, barLabel, prepared, wordingsLine, notShown, where, secondsFact, liveData, liveRefusal,
    mount(host, d0, ctx) {
      let inner = null, skipped = false, picked = null; // picked: the live reading the planner chose; it stays chosen until the recorded one is
      const swap = (d) => { V.selectedReading = d.mode === 'live' && d.id ? d.id : null; inner = draw(host, d, ctx, { canLive: ctx.interactive && d.live_reader === true && !skipped && d.mode !== 'live', onLive, onSkip }); };
      function onSkip() { skipped = true; picked = null; swap(d0); inner.enter(); ctx.release('read'); }
      function onLive(live) { picked = live; swap(liveData(d0, live)); inner.enter(true); ctx.release('read'); }
      swap(d0);
      return { enter() { skipped = false; if (d0.live_reader === true && ctx.interactive) swap(picked ? liveData(d0, picked) : d0); inner.enter(); }, seek(raw, live) { inner.seek(raw, live); } };
    } });
  function draw(host, d, ctx, opts) {
      const esc = ctx.esc, r = d.reader, ev = d.evidence || {}, local = r.on_device === true;
      const toks = tokens(pretty(d.answer)), words = d.rule.split(' ');
      const kind = (t, next) => (/^"/.test(t) ? (next === ':' ? 'key' : 'str') : /^-?\d/.test(t) ? 'num' : /^\s+$/.test(t) ? 'ws' : 'punct');
      const facts = [];
      if (local && ev.adapter_sha256) facts.push(['Adapter', ev.adapter_sha256.slice(0, 12)]);
      if (secondsFact(d)) facts.push(secondsFact(d));
      if (local && d.peak_gb != null) facts.push(['Memory', `${(+d.peak_gb).toFixed(1)} GB`]);
      const refusal = d.refused_first && d.refused_first.refusal;
      const bars = d.bars;
      host.innerHTML = `<div class="v6x v6t">
        <section class="v6t-left">
          <h2>What the planner said</h2>
          <blockquote>${esc(d.sentence)}</blockquote>
          ${d.heard && d.heard.transcript ? `<p class="v6t-heard">Over the noisy line it came through as “${esc(d.heard.transcript)}”${d.heard.binds === true ? ' and read to the same rule.' : d.heard.binds === false ? ' and was misread.' : '.'}</p>` : ''}
          ${refusal ? `<p class="v6t-ref">Before the fix: “${esc(d.refused_first.sentence)}” was refused as ${esc(refusal.code)}.</p>` : ''}
          <div class="v6t-who"><span class="${local ? 'local' : ''}">${esc(where(r.label))}</span>${d.label ? `<span>${esc(d.label)}</span>` : ''}${STORY.readLive(d) ? '<span class="local">Live</span>' : ''}</div>
          ${opts.canLive ? `<div class="v6t-live" style="margin:14px 0 22px;display:flex;gap:14px;align-items:center;flex-wrap:wrap"><button type="button" class="v6-btn" data-a="read">Read it live</button><button type="button" class="v6-btn ghost" data-a="rec">Use the recorded reading</button><span style="flex-basis:100%;font-size:17px;color:#ffa53a"></span></div>` : ''}
          <dl class="v6t-facts">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
          ${d.stand_in_note ? `<p class="v6t-stand">${esc(d.stand_in_note)}</p>` : ''}
          ${prepared(d.prepared, d.sentence, esc, d.wordings)}
        </section>
        <section class="v6t-right">
          <h2>${d.mode === 'stored' ? 'The stored rule' : 'How it was read'}</h2>
          <pre class="v6t-json" style="height:auto">${toks.map((t, i) => `<i class="${kind(t, toks[i + 1])}">${esc(t)}</i>`).join('')}</pre>
          <p class="v6t-plain">${words.map((w) => `<span>${esc(w)}</span>`).join(' ')}</p>
          ${bars ? `<div class="v6t-bars"><div><span>Same model, without our fine-tune</span><b><i style="width:${bars.without}%"></i></b>${barLabel(bars, 'without', esc)}</div>
            <div class="on"><span>With our fine-tune</span><b><i style="width:${bars.with}%"></i></b>${barLabel(bars, 'with', esc)}</div>
            <small>${[bars.scope, bars.comparisons].filter(Boolean).map(esc).join(' · ')}</small></div>` : ''}
          <p class="v6t-scope">${esc(d.scope_line)}</p>
          <div class="v6t-confirm"><button type="button" class="v6-btn" data-a="ok">Confirm</button><span>Nothing goes on until the planner confirms.</span></div>
        </section>
        <div class="v6n-mouse" hidden>${cursorHTML}</div>
      </div>`;
      const root = host.firstElementChild, $ = (s) => root.querySelector(s);
      const tokEls = [...root.querySelectorAll('.v6t-json i')], wordEls = [...root.querySelectorAll('.v6t-plain span')];
      const prep = $('.v6t-prep');
      const barEls = [...root.querySelectorAll('.v6t-bars b i')], barBox = $('.v6t-bars'), scope = $('.v6t-scope'), box = $('.v6t-confirm'), btn = $('[data-a=ok]'), left = $('.v6t-left'), mouse = $('.v6n-mouse'), note = box.querySelector('span');
      let scale = 0, spot = null, sayTok = -1, confirmedAt = null, moved = false, posting = false, refused = null;
      const liveBox = $('.v6t-live'), liveNote = liveBox && liveBox.querySelector('span');
      let reading = null; // {started} while the live read is out
      if (liveBox) {
        liveBox.querySelector('[data-a=rec]').onclick = () => { if (!reading) opts.onSkip(); };
        liveBox.querySelector('[data-a=read]').onclick = async () => {
          if (reading) return;
          reading = { started: performance.now() };
          try {
            const res = await fetch('/api/v6/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sentence: d.sentence }) });
            const body = await res.json().catch(() => null);
            if (res.ok && body && body.status === 'constraint_ready' && body.binding && body.answer) { opts.onLive(Object.assign(body, { client_s: (performance.now() - reading.started) / 1000 })); return; }
            put(liveNote, 'text', res.ok ? liveRefusal(body) : ((body && body.error) || 'The live reader did not answer.'));
          } catch (e) { put(liveNote, 'text', 'The live reader did not answer.'); }
          reading = null;
        };
      }
      addEventListener('resize', () => { spot = null; });
      btn.onclick = async () => {
        if (!ctx.interactive || confirmedAt != null || posting) return;
        posting = true; refused = null;
        try {
          const r = await fetch('/api/v6/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d.binding || {}) });
          const body = await r.json().catch(() => null);
          if (!r.ok || !body || !body.token) { refused = (body && body.error) || 'The server did not record this confirmation.'; return; }
          V.confirmation = body; confirmedAt = -1; ctx.release('confirm');
        } catch (e) { refused = 'The server did not record this confirmation.'; } finally { posting = false; }
      };
      return {
        enter() { spot = null; sayTok = -1; confirmedAt = null; moved = false; refused = null; V.confirmation = null; tokEls.forEach((e) => e.classList.remove('on')); },
        get liveRead() { return d.mode === 'live' ? d : null; },
        seek(raw, live) {
          if (!spot) {
            scale = fit(host, root); if (!scale) return;
            const b = root.getBoundingClientRect(), a = btn.getBoundingClientRect();
            spot = { x: ((a.left + a.right) / 2 - b.left) / scale, y: ((a.top + a.bottom) / 2 - b.top) / scale };
          }
          let tau = clamp(raw, 0, T.end);
          if (live && liveBox && ctx.waitAt(tau, 'read', READ_AT)) tau = READ_AT;
          if (liveBox) {
            put(liveBox, 'opacity', String(clamp((tau - 0.3) / 0.3)));
            if (reading) put(liveNote, 'text', `Reading live · ${((performance.now() - reading.started) / 1000).toFixed(1)} s`);
          }
          if (live && confirmedAt == null && ctx.waitAt(tau, 'confirm', T.press)) tau = T.press;
          if (live && confirmedAt === -1) confirmedAt = tau;
          if (live && confirmedAt != null && tau >= confirmedAt + 0.8 && !moved) { moved = true; ctx.next(); return; }
          const e = outCubic(clamp(tau / 0.3)); put(left, 'opacity', String(e)); put(left, 'transform', `translateX(${(1 - e) * -30}px)`);
          const count = Math.floor(clamp((tau - T.json[0]) / (T.json[1] - T.json[0])) * tokEls.length);
          if (count !== sayTok) { tokEls.forEach((el, i) => el.classList.toggle('on', i < count)); sayTok = count; }
          const w = clamp((tau - T.plain[0]) / (T.plain[1] - T.plain[0])) * wordEls.length;
          wordEls.forEach((el, i) => { const k = clamp(w - i); put(el, 'opacity', String(k)); put(el, 'transform', `translateY(${(1 - k) * 10}px)`); });
          if (barBox) {
            put(barBox, 'opacity', tau >= T.bars ? '1' : '0');
            barEls.forEach((el, i) => put(el, 'transform', `scaleX(${outCubic(clamp((tau - T.bars - i * 0.5) / 0.8))})`));
          }
          if (prep) put(prep, 'opacity', String(clamp((tau - T.plain[0]) / 0.4)));
          put(scope, 'opacity', String(clamp((tau - T.scope) / 0.3)));
          const pressed = live ? confirmedAt != null : tau >= T.press, up = clamp((tau - T.confirm) / 0.3);
          put(box, 'opacity', String(up)); put(box, 'transform', `translateY(${(1 - up) * 18}px)`);
          put(box, 'state', pressed ? 'done' : '');
          put(btn, 'text', pressed ? 'Confirmed' : 'Confirm');
          put(note, 'text', pressed ? 'The planner confirmed this reading. It goes to the search software.' : refused || 'Nothing goes on until the planner confirms.');
          const hand = !live && tau >= T.confirm + 0.4 && tau < T.press + 0.5;
          if (mouse.hidden === hand) mouse.hidden = !hand;
          if (hand) {
            const k = inOut(clamp((tau - T.confirm - 0.5) / 1.2)), press = Math.abs(tau - T.press) < 0.07 ? 0.86 : 1;
            put(mouse, 'transform', `translate(${lerp(1760, spot.x, k)}px,${lerp(1010, spot.y, k)}px) scale(${press})`);
          }
        },
      };
  }
})();
