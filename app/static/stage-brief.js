/* StowLM stage page: the brief flow (plan v5 task 2.2). Backend: app/brief.py. Contract: experiments/mvp/ui/CONTRACT.md.
 *
 *   say or type -> editable draft -> planner confirms -> reader's rule, with who read it taken from evidence
 *   -> "Plan this" -> stored plan through the gates, or the plain no-stored-plan message -> the stage switches plans.
 *
 * Nothing here decides a label. Reader, planner, lifts and verdicts arrive from the server; pending things read as
 * pending. View functions are pure strings (window.StageBriefView), so the live flow and the video director render the
 * same cards, and window.StageBrief.show(state) is a pure, idempotent render with no parser call.
 * Key V toggles the panel. Hidden in ?present=1 unless ?brief=1. Video mode has no button: the director calls show().
 */
(function () {
  'use strict';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v) => typeof v === 'number' && Number.isFinite(v);
  const norm = (s) => String(s || '').normalize('NFC').toLowerCase().replace(/\s+/g, ' ').trim().replace(/[\s.!?]+$/, '');
  const clock = (s) => { if (!num(s)) return '—'; if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)} s`; const t = Math.round(s); return `${Math.floor(t / 60)} min ${String(t % 60).padStart(2, '0')} s`; };
  const short = (s, n = 8) => (s ? String(s).slice(0, n) : '');

  const PHASES = [['brief', 'Brief'], ['transcript', 'Confirm'], ['reading', 'Reading'], ['plan', 'Plan and check']];
  const STEP_OF = { brief: 0, listening: 0, transcript: 1, reading: 2, refused: 2, impossible: 2, searching: 3, plan: 3, none: 3, replan: 3 };
  const MIC_SVG = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M12 15a4 4 0 0 0 4-4V6a4 4 0 0 0-8 0v5a4 4 0 0 0 4 4Zm7-4h-2a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1A7 7 0 0 0 19 11Z"/></svg>';

  // ---------------------------------------------------------------- pure views
  function stepsHtml(phase) {
    const at = STEP_OF[phase] == null ? 0 : STEP_OF[phase];
    return `<ol class="brf-steps">${PHASES.map(([, name], i) => `<li class="${i < at ? 'done' : i === at ? 'now' : ''}"><i>${i < at ? '✓' : i + 1}</i>${esc(name)}</li>`).join('')}</ol>`;
  }
  function readerBadge(reader, recorded) {
    const r = reader || { label: 'Reader · provenance not recorded', kind: 'unknown' };
    const tag = recorded == null ? '' : `<em>${recorded ? 'recorded reading' : 'live reading'}</em>`;
    return `<span class="brf-reader k-${esc(r.kind)}${r.on_device ? ' on-device' : ''}"><b>${esc(r.label)}</b>${tag}</span>`;
  }
  function evidenceLine(evidence, seconds, recorded, extra) {
    const e = evidence || {}, bits = [];
    if (e.model) bits.push(['model', e.model]);
    if (e.adapter_sha256) bits.push(['adapter', short(e.adapter_sha256, 12)]);
    if (e.request_id) bits.push(['request', short(e.request_id)]);
    if (num(seconds)) bits.push([recorded ? 'took, when recorded' : 'took', clock(seconds)]);
    for (const x of extra || []) bits.push(x);
    return `<dl class="brf-ev" title="${esc(e.source || '')}">${bits.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`;
  }
  function jsonHtml(answer) {
    return `<pre class="brf-json">${esc(JSON.stringify(answer))}</pre>`;
  }
  function readingHtml(v, readonly) {
    const other = v.context_bound === false ? '<p class="brf-note">Recorded on another ship\'s context, then re-validated against this ship.</p>' : '';
    return `<div class="brf-quote">“${esc(v.sentence)}”</div>
      <div class="brf-rule"><span class="brf-eyebrow">Read as a strict rule</span><strong>${esc(v.rule)}</strong>${jsonHtml(v.answer)}</div>
      <div class="brf-who"><span class="brf-eyebrow">Read by</span>${readerBadge(v.reader, !!v.recorded)}${evidenceLine(v.evidence, v.seconds, v.recorded)}</div>
      ${other}<p class="brf-warn">${esc(v.as_read_note || '')}</p>
      ${readonly ? '' : '<div class="brf-acts"><button type="button" class="brf-go" data-a="plan">Plan this</button><button type="button" data-a="restart">That isn\'t what I said</button></div>'}`;
  }
  function verdictHtml(gate) {
    const row = (ok, text) => `<span class="${ok ? 'ok' : 'no'}">${ok ? '✓' : '✕'} ${esc(text)}</span>`;
    const a = gate.after || {};
    return `<div class="brf-verdict">${row(a.passed, 'Full checker passed on the current code')}${row(a.shippable, 'Clean shipping evidence')}${row(gate.honoured, 'Rule honoured, recomputed on the plan')}</div>`;
  }
  function counterHtml(gate) {
    const b = gate.before || {}, a = gate.after || {};
    if (!num(a.weighted)) return '';
    const delta = gate.delta_weighted;
    const said = !num(delta) ? '' : delta === 0 ? 'Obeying cost no extra lifts' : delta > 0 ? `Obeying cost ${delta} extra lift${delta === 1 ? '' : 's'}` : `${-delta} fewer extra lift${delta === -1 ? '' : 's'} under the rule`;
    return `<div class="brf-lift" title="Weighted non-productive moves: rehandles, restows and hatch lifts, now and projected at later ports">
      <div class="n"><span>${num(b.weighted) ? esc(b.weighted) : '—'}</span><small>before</small></div><div class="arrow">→</div>
      <div class="n after"><span class="brf-count" data-from="${num(b.weighted) ? b.weighted : a.weighted}" data-to="${a.weighted}">${esc(a.weighted)}</span><small>after</small></div>
      <div class="cap"><b>extra crane lifts</b><span>${esc(said)}</span>${num(gate.boxes_moved) ? `<span>${esc(gate.boxes_moved)} boxes moved</span>` : ''}</div></div>`;
  }
  function notShownHtml(gate) {
    return `<div class="brf-card red"><div class="h">Not shown as a plan</div><p>${esc((gate.why || []).join('; ') || 'the gate did not pass')}.</p><p class="f">The stored plan stays off the stage. Nothing was applied.</p></div>`;
  }
  function checkingHtml(text) {
    return `<div class="brf-run"><i class="brf-spin"></i><span>${esc(text)}</span></div>`;
  }
  function planHtml(v, readonly) {
    const gate = v.gate || { status: 'pending' };
    const head = `<div class="brf-quote">“${esc(v.sentence)}”</div><div class="brf-rule small"><span class="brf-eyebrow">Rule as read</span><strong>${esc(v.rule)}</strong></div>`;
    if (gate.status === 'pending' || gate.status === 'checking') return head + checkingHtml('Replaying the stored plan and running the full checker now. A stored plan is shown only after this passes.');
    if (gate.status !== 'shown') return head + notShownHtml(gate);
    const words = v.same_words === false ? `<p class="brf-note">Stored for the same rule on this ship, from the sentence “${esc(v.stored_sentence)}”.</p>` : '';
    return `${head}${counterHtml(gate)}${verdictHtml(gate)}
      <div class="brf-credit">${esc(v.credits || '')}</div>
      <div class="brf-tags"><span class="tag rec">${esc(gate.recorded === false ? 'live replan' : (v.recorded_label || 'recorded plan, not a live solve'))}</span>
      <span class="tag">${gate.recorded === false ? 'real wall time' : 'recorded wall time'} ${esc(clock(v.recorded_wall_s != null ? v.recorded_wall_s : gate.wall_s))}</span>
      <span class="tag">plan: ${esc(gate.plan_label || v.plan_label || '')}</span><span class="tag">${esc(v.checked_against || 'checked against the rule as read')}</span></div>${words}
      ${readonly ? '' : '<div class="brf-acts"><button type="button" class="brf-go" data-a="switch">Replay on the ship</button><button type="button" data-a="restart">New brief</button></div>'}`;
  }
  function noPlanHtml(v, messages, readonly) {
    const replan = v.replan_available && !readonly ? '<div class="brf-acts"><button type="button" class="brf-go" data-a="replan">Start real replan</button><button type="button" data-a="restart">New brief</button></div>'
      : readonly ? '' : '<p class="brf-note">Real replans are off in this launch.</p><div class="brf-acts"><button type="button" data-a="restart">New brief</button></div>';
    return `<div class="brf-quote">“${esc(v.sentence)}”</div><div class="brf-rule small"><span class="brf-eyebrow">Rule as read</span><strong>${esc(v.rule)}</strong></div>
      <div class="brf-card amber"><div class="h">No stored plan</div><p>${esc(v.message || (messages || {}).no_stored_plan || '')}</p>
      ${v.hero ? `<p class="f">${esc(v.hero.note)}</p>` : '<p class="f">No older plan is shown in its place.</p>'}</div>${replan}`;
  }
  /* Slot for task 1.4. Arithmetic only from the proof; alternatives only if certified; nothing here is a checker result. */
  function renderRefusal(e, readonly) {
    if (!e) return `<div class="brf-card pending"><div class="h">Refusal</div><p>refusal evidence pending (task 1.4)</p></div>`;
    if (e.pending) return `<div class="brf-card pending"><div class="h">Refusal</div><p>${esc(e.message || 'refusal evidence pending (task 1.4)')}</p></div>`;
    const a = e.arithmetic;
    const ambiguous = e.code === 'ambiguous';
    const title = a ? 'Impossible on this ship, before planning' : ambiguous ? 'Not clear enough to plan' : `Refused · ${String(e.code || '').replace(/_/g, ' ')}`;
    const shortfall = a && (a.reasons || []).includes('total_open_cell_shortfall');
    const space = `<div><b>${esc(a && a.needed_teu)}</b><span>TEU the load needs</span></div><div class="minus">vs</div><div class="zero"><b>${esc(a && a.left_cells)}</b><span>cells left under the rule</span></div>`;
    const boxes = a && !a.boxes_with_no_slot ? '' : `<div class="${shortfall ? '' : 'zero'}"><b>${esc(a && a.boxes_with_no_slot)}<i>/${esc(a && a.load_boxes)}</i></b><span>boxes with no allowed slot</span></div>`;
    const sized = `<div class="brf-sub">${esc(a && a.needed_teu)} TEU needed, ${esc(a && a.left_cells)} cells stay open, but these boxes have no size-compatible slot among them.</div>`;
    const math = a ? `<div class="brf-math">${shortfall ? space + boxes : boxes + sized}</div>
        <details class="brf-proof"><summary>The proof, box by box</summary><table><tr><th>Box</th><th>ft</th><th>cells</th><th>slots before</th><th>left</th></tr>${(a.rows || []).map((r) => `<tr><td>${esc(r.container_id)}</td><td>${esc(r.length_ft)}</td><td>${esc(r.demand_cells)}</td><td>${esc(r.size_compatible_footprints)}</td><td>${esc(r.allowed_footprints)}</td></tr>`).join('')}</table></details>` : '';
    const reason = !a && e.reason ? `<p class="brf-why"><span class="brf-eyebrow">Reader's reason</span>${esc(e.reason)}</p>` : '';
    const rule = e.existing_rule ? `<p class="brf-why"><span class="brf-eyebrow">Existing rule</span>${esc(e.existing_rule.name)}</p>` : '';
    const ask = (e.clarifications || []).map((q) => `<p class="brf-ask">${esc(q)}</p>`).join('');
    const said = e.explanation ? `<p class="brf-why spark"><span class="brf-eyebrow">${esc(e.explanation_label || 'Muse Spark, recorded')}</span>${esc(e.explanation)}</p>` : '';
    const alts = (e.alternatives || []).filter((x) => !String(e.explanation || '').includes(x.text)).map((x) => `<p class="brf-why"><span class="brf-eyebrow">Alternative, your choice</span>${esc(x.text)}</p>`).join('');
    const foot = a ? `No plan was attempted, so there's no checker report: this is a geometry proof.${(e.alternatives || []).length ? '' : ' No alternative is certified on this map.'}` : ambiguous ? 'No rule was guessed. Nothing was applied.' : 'Nothing was applied; the current plan stands.';
    return `<div class="brf-quote">“${esc(e.sentence)}”</div>${a && e.rule ? `<div class="brf-rule small"><span class="brf-eyebrow">Rule as read</span><strong>${esc(e.rule)}</strong></div>` : ''}
      <div class="brf-card ${ambiguous ? 'amber' : 'red'}"><div class="h">${esc(title)}</div>${math}${reason}${rule}${ask}${said}${alts}<p class="f">${esc(foot)}</p></div>
      <div class="brf-who"><span class="brf-eyebrow">Read by</span>${readerBadge(e.reader, e.recorded == null ? null : !!e.recorded)}</div>
      ${readonly ? '' : '<div class="brf-acts"><button type="button" data-a="restart">New brief</button></div>'}`;
  }
  function searchingHtml(v) {
    return `<div class="brf-quote">“${esc(v.sentence)}”</div><div class="brf-rule small"><span class="brf-eyebrow">Rule as read</span><strong>${esc(v.rule)}</strong></div>
      ${checkingHtml(`Search software replanning under the rule · recorded wall time ${clock(v.recorded_wall_s)}`)}<div class="brf-tags"><span class="tag rec">recorded plan, not a live solve</span></div>`;
  }
  function replanHtml(job, sentence, rule) {
    const stage = job.status === 'running' ? job.stage : job.status;
    return `<div class="brf-quote">“${esc(sentence)}”</div><div class="brf-rule small"><span class="brf-eyebrow">Rule as read</span><strong>${esc(rule)}</strong></div>
      <div class="brf-replan"><div class="t">${esc(clock(job.elapsed_s))}</div><div class="s">${esc(stage)}<br><small>real elapsed time · one CPU worker · reader → search → finisher → full checker</small></div></div>
      <div class="brf-acts"><button type="button" data-a="cancel">Cancel replan</button></div>`;
  }
  function transcriptHtml(v, readonly) {
    const spoken = v.source === 'audio';
    const label = spoken ? (v.heard_by || 'Muse Voice Transcribe · cloud call') : 'Typed by the planner';
    const box = readonly ? `<div class="brf-edit ro">${esc(v.transcript)}</div>` : `<textarea id="brf-edit" class="brf-edit" maxlength="500" rows="3" aria-label="Sentence to confirm">${esc(v.transcript)}</textarea>`;
    return `<div class="brf-heard"><span class="brf-reader k-${spoken ? 'voice' : 'typed'}"><b>${esc(label)}</b>${v.recorded ? '<em>recorded transcript</em>' : ''}</span></div>${box}
      <p class="brf-hint">Nothing is read until you confirm. Fix any word first; what's in the box is what the reader gets.</p>
      ${readonly ? '' : '<div class="brf-acts"><button type="button" class="brf-go" data-a="confirm">Confirm and read</button><button type="button" data-a="restart">Start over</button></div>'}`;
  }
  /* Director lookup: prepared sentences by words, refusal cards, the hero slot. */
  function preparedList(d) {
    if (!d) return [];
    const plans = (d.prepared || []).map((c) => ({ kind: 'plan', sentence: c.sentence, call: c.call, case: c.id, rule: c.rule, status: (c.gate || {}).status || 'pending' }));
    const cards = ((d.refusals || {}).cards || []).map((c) => ({ kind: c.arithmetic ? 'impossible' : 'refused', sentence: c.sentence, call: c.call, case: c.case_id, status: c.status }));
    const clips = (d.voice_clips || []).map((c) => ({ kind: 'clip', sentence: c.transcript, said: c.said, call: c.call, case: c.id, status: 'recorded' }));
    return [...plans, ...cards, ...clips, { kind: 'pending', sentence: (d.hero || {}).sentence, status: 'pending', note: (d.hero || {}).note }];
  }
  function directorHtml(d, state) {
    const stage = state.stage, sentence = state.sentence || '', key = norm(sentence);
    const body = (html) => stepsHtml(stage) + `<div class="brf-body">${html}</div>`;
    if (!d) return body(checkingHtml('Loading recorded evidence'));
    const hero = norm((d.hero || {}).sentence) === key;
    const c = (d.prepared || []).find((x) => norm(x.sentence) === key);
    const clip = (d.voice_clips || []).find((x) => norm(x.transcript) === key || norm(x.said) === key);
    const card = ((d.refusals || {}).cards || []).find((x) => norm(x.sentence) === key);
    if (stage === 'listening') {
      const words = sentence.split(/\s+/), shown = num(state.progress) ? words.slice(0, Math.ceil(words.length * Math.max(0, Math.min(1, state.progress)))) : words;
      const own = state.peaks || (clip && norm(clip.transcript) === key);
      return body(`<div class="brf-listen"><canvas class="brf-wave"></canvas>${own ? '' : '<div class="brf-timer">waveform of a recorded synthetic clip</div>'}</div><div class="brf-quote live">${esc(shown.join(' '))}</div>`);
    }
    if (stage === 'transcript') return body(transcriptHtml({ source: 'audio', transcript: clip ? clip.transcript : sentence, recorded: !!state.recorded }, true));
    if (stage === 'refused' || stage === 'impossible') {
      if ((d.refusals || {}).status === 'pending') return body(renderRefusal({ pending: true, message: (d.refusals || {}).message }));
      return body(renderRefusal(card ? Object.assign({ recorded: true }, card) : null, true));
    }
    if (hero || (!c && !clip)) return body(`<div class="brf-quote">“${esc(sentence)}”</div><div class="brf-card pending"><div class="h">Pending</div><p>${esc(hero ? d.hero.note : 'No recorded evidence for this sentence.')}</p></div>`);
    const row = c || (d.prepared || []).find((x) => x.id === 'one_port_per_hatch-05') || {};
    const reading = row.reading || {};
    const v = { sentence, rule: row.rule, answer: row.constraint, reader: reading.reader, evidence: reading.evidence, seconds: reading.seconds, recorded: true, as_read_note: (d.messages || {}).rule_as_read,
      gate: row.gate, plan_label: row.plan_label, recorded_label: row.recorded_label, recorded_wall_s: row.recorded_wall_s,
      credits: reading.reader ? `${reading.reader.short} read it; search software planned it; the checker passed it` : '' };
    if (stage === 'reading') return body(readingHtml(v, true));
    if (stage === 'searching') return body(searchingHtml(v));
    return body(planHtml(v, true));
  }
  /* 16-bit PCM WAV with no library: linear resample to 16 kHz, mono, little-endian. */
  function encodeWav(chunks, rate, target = 16000) {
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const input = new Float32Array(total); let at = 0;
    for (const c of chunks) { input.set(c, at); at += c.length; }
    const frames = Math.floor(total * target / rate);
    const view = new DataView(new ArrayBuffer(44 + frames * 2));
    const tag = (i, s) => { for (let k = 0; k < s.length; k++) view.setUint8(i + k, s.charCodeAt(k)); };
    tag(0, 'RIFF'); view.setUint32(4, 36 + frames * 2, true); tag(8, 'WAVE'); tag(12, 'fmt '); view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, target, true); view.setUint32(28, target * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); tag(36, 'data'); view.setUint32(40, frames * 2, true);
    for (let i = 0; i < frames; i++) {
      const p = i * rate / target, lo = Math.floor(p), hi = Math.min(lo + 1, total - 1), f = p - lo;
      const s = Math.max(-1, Math.min(1, input[lo] * (1 - f) + input[hi] * f));
      view.setInt16(44 + i * 2, Math.round(s * 32767), true);
    }
    return new Uint8Array(view.buffer);
  }
  window.StageBriefView = { stepsHtml, readerBadge, readingHtml, planHtml, noPlanHtml, renderRefusal, transcriptHtml, searchingHtml, directorHtml, preparedList, encodeWav };
  if (typeof document === 'undefined') return;

  // ---------------------------------------------------------------- page
  const params = new URLSearchParams(location.search);
  const VIDEO = params.get('video') === '1', PRESENT = params.get('present') === '1';
  const S = { data: null, phase: 'brief', call: null, draft: null, reading: null, plan: null, job: null, error: null, text: '', panel: null, btn: null, directorKey: null, directorState: null };
  const $ = (id) => document.getElementById(id);
  const saved = {
    get() { try { return JSON.parse(sessionStorage.getItem('stowlm.brief.last') || 'null'); } catch (e) { return null; } },
    set(v) { try { sessionStorage.setItem('stowlm.brief.last', JSON.stringify(v)); } catch (e) { /* private window */ } },
  };
  async function api(path, body, raw) {
    const opts = body === undefined ? { headers: { Accept: 'application/json' } }
      : { method: 'POST', headers: { Accept: 'application/json', 'Content-Type': raw ? 'audio/wav' : 'application/json' }, body: raw ? body : JSON.stringify(body) };
    const r = await fetch(path, opts);
    let out = null; try { out = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((out && out.error) || `HTTP ${r.status}`);
    return out;
  }

  // ------------------------------------------------ waveform (canvas), live or from a clip's measured peaks
  const wave = { raf: 0, bars: [], analyser: null };
  function sizeCanvas(c) { const r = c.getBoundingClientRect(), d = window.devicePixelRatio || 1; c.width = Math.max(1, r.width * d); c.height = Math.max(1, r.height * d); return { w: c.width, h: c.height, d }; }
  function drawBars(c, bars, fill) {
    const { w, h, d } = sizeCanvas(c), g = c.getContext('2d');
    g.clearRect(0, 0, w, h);
    const n = Math.max(60, Math.floor(w / (5 * d))), step = w / n, mid = h / 2;
    for (let i = 0; i < n; i++) {
      const v = bars[Math.floor(i * bars.length / n)] || 0, head = fill == null ? true : i / n <= fill;
      const y = Math.max(1.5 * d, Math.pow(v, 0.7) * (mid - 4 * d));
      g.fillStyle = head ? `hsl(${34 - 12 * v}, 100%, ${58 + 12 * v}%)` : '#22395e';
      g.fillRect(i * step, mid - y, Math.max(2 * d, step * 0.6), y * 2);
    }
  }
  function liveWave() {
    cancelAnimationFrame(wave.raf);
    const tick = () => {
      const c = document.querySelector('.brf-wave'); if (!c || !wave.analyser) return;
      const t = new Uint8Array(wave.analyser.fftSize); wave.analyser.getByteTimeDomainData(t);
      let e = 0; for (const x of t) e += Math.pow((x - 128) / 128, 2);
      wave.bars.push(Math.min(1, Math.sqrt(e / t.length) * 4)); if (wave.bars.length > 180) wave.bars.shift();
      drawBars(c, wave.bars.length < 180 ? new Array(180 - wave.bars.length).fill(0).concat(wave.bars) : wave.bars);
      wave.raf = requestAnimationFrame(tick);
    };
    tick();
  }
  function clipWave(clip, done) {
    cancelAnimationFrame(wave.raf);
    const started = performance.now(), ms = Math.min(clip.seconds, 7) * 1000;
    const tick = (now) => {
      const c = document.querySelector('.brf-wave'), p = Math.min(1, (now - started) / ms);
      if (c) drawBars(c, clip.peaks, p);
      const el = document.querySelector('.brf-timer'); if (el) el.textContent = `${(p * clip.seconds).toFixed(1)} s of ${clip.seconds.toFixed(1)} s`;
      if (p < 1) wave.raf = requestAnimationFrame(tick); else done();
    };
    wave.raf = requestAnimationFrame(tick);
  }

  // ------------------------------------------------ microphone: buffered PCM, then one POST
  const mic = { stream: null, ctx: null, node: null, chunks: [], rate: 48000, timer: 0, started: 0 };
  async function startMic() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { S.error = 'This browser has no microphone access. Type the sentence instead.'; render(); return; }
    try {
      mic.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch (e) { S.error = 'The microphone is blocked or missing. Type the sentence instead.'; render(); return; }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    mic.ctx = new Ctx(); mic.rate = mic.ctx.sampleRate; mic.chunks = []; wave.bars = [];
    const source = mic.ctx.createMediaStreamSource(mic.stream);
    wave.analyser = mic.ctx.createAnalyser(); wave.analyser.fftSize = 1024; source.connect(wave.analyser);
    mic.node = mic.ctx.createScriptProcessor(4096, 1, 1);
    mic.node.onaudioprocess = (e) => mic.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    const silent = mic.ctx.createGain(); silent.gain.value = 0;
    source.connect(mic.node); mic.node.connect(silent); silent.connect(mic.ctx.destination);
    mic.started = performance.now(); S.error = null; S.phase = 'listening'; S.listening = 'mic'; render(); liveWave();
    mic.timer = setInterval(() => {
      const s = (performance.now() - mic.started) / 1000, el = document.querySelector('.brf-timer');
      if (el) el.textContent = `${s.toFixed(1)} s of 30 s`;
      if (s >= 29.5) stopMic();
    }, 200);
  }
  function closeMic() {
    clearInterval(mic.timer); cancelAnimationFrame(wave.raf); wave.analyser = null;
    if (mic.node) mic.node.disconnect();
    if (mic.stream) for (const t of mic.stream.getTracks()) t.stop();
    if (mic.ctx) mic.ctx.close();
    mic.stream = mic.ctx = mic.node = null;
  }
  async function stopMic() {
    if (!mic.stream) return;
    const chunks = mic.chunks, rate = mic.rate; closeMic();
    S.phase = 'working'; S.working = 'Sending the recording to Muse Voice Transcribe'; render();
    try {
      const out = await api('/api/brief/audio?call=' + encodeURIComponent(S.call), encodeWav(chunks, rate), true);
      acceptDraft(out);
    } catch (e) { fallback(e.message); }
  }

  // ------------------------------------------------ steps
  function fallback(message) { S.phase = 'brief'; S.error = message; S.draft = null; render(); }
  function acceptDraft(out) {
    if (out.status !== 'awaiting_confirmation') { fallback(out.message || 'Type the sentence to continue.'); return; }
    S.draft = out; S.call = out.call || S.call; S.phase = 'transcript'; S.error = null; render();
    const box = $('brf-edit'); if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
  }
  async function makeDraft(text) {
    S.text = text; S.phase = 'working'; S.working = 'Preparing the sentence'; render();
    try { acceptDraft(await api('/api/brief/draft', { text, call: S.call })); } catch (e) { fallback(e.message); }
  }
  async function playClip(clip) {
    S.call = clip.call; S.phase = 'listening'; S.listening = 'clip'; S.error = null; render();
    clipWave(clip, async () => { try { acceptDraft(await api('/api/brief/clip', { clip: clip.id })); } catch (e) { fallback(e.message); } });
  }
  async function confirmDraft() {
    const text = $('brf-edit').value;
    S.phase = 'working'; S.working = 'The reader is reading the confirmed sentence'; render();
    try {
      const out = await api('/api/brief/confirm', { draft: S.draft.draft, text });
      S.draft = null;
      if (out.status === 'constraint_ready') { S.reading = out; S.phase = 'reading'; render(); return; }
      if (out.status === 'refused' || out.status === 'impossible') { S.reading = out; S.phase = out.status; render(); return; }
      if (out.status === 'no_recording') { S.text = out.sentence; S.phase = 'brief'; S.error = out.message + (out.hero ? ` ${out.hero.note}` : ' Restart with --brief-live to read it.'); render(); return; }
      S.text = text; fallback(out.message || 'The reader is unavailable. Type the instruction to retry.');
    } catch (e) { fallback(e.message); }
  }
  async function planThis() {
    S.phase = 'working'; S.working = 'Looking for a stored plan under exactly this rule on this ship'; render();
    try { showPlan(await api('/api/brief/plan', { reading: S.reading.id })); } catch (e) { fallback(e.message); }
  }
  function showPlan(out) {
    S.plan = out;
    if (out.kind === 'none') { S.phase = 'none'; render(); return; }
    S.phase = 'plan'; render();
    const status = (out.gate || {}).status;
    if (status === 'pending' || status === 'checking') setTimeout(pollPlan, 1200);
    if (status === 'shown') { remember(out); animateCounters(); }
  }
  async function pollPlan() {
    if (S.phase !== 'plan' || !S.reading) return;
    try { showPlan(await api('/api/brief/plan', { reading: S.reading.id })); } catch (e) { setTimeout(pollPlan, 2000); }
  }
  function remember(out) { saved.set(Object.assign({}, out, { recorded_wall_s: out.recorded_wall_s })); }
  function switchPlan() {
    const id = ((S.plan || {}).gate || {}).after && S.plan.gate.after.plan_id; if (!id) return;
    const q = new URLSearchParams(location.search); q.set('plan_id', id); q.set('brief', '1'); q.set('brief_case', 'last'); q.delete('step'); q.set('autoplay', '1');
    location.search = q.toString();
  }
  async function startReplan() {
    try { S.job = await api('/api/brief/replan', { reading: S.reading.id }); S.phase = 'replan'; render(); setTimeout(pollJob, 1000); } catch (e) { S.error = e.message; render(); }
  }
  async function pollJob() {
    if (!S.job) return;
    try { S.job = await api('/api/brief/jobs/' + encodeURIComponent(S.job.id)); } catch (e) { fallback(e.message); return; }
    if (S.job.status === 'running') { render(); setTimeout(pollJob, 1000); return; }
    const j = S.job, r = j.result || {}; S.job = null;
    if (j.status === 'done' && r.kind === 'gated') {
      const out = Object.assign({ sentence: j.sentence, rule: j.rule, gate: r.gate, credits: r.credits, recorded_wall_s: r.gate.wall_s, recorded_label: 'live replan', checked_against: 'checked against the rule as read' });
      showPlan(out); return;
    }
    S.plan = { sentence: j.sentence, rule: j.rule, message: j.status === 'cancelled' ? 'Replan cancelled. Nothing was shown.' : (r.message || j.error || 'No checked plan found.') };
    S.phase = 'none'; render();
  }
  async function cancelReplan() { if (S.job) { try { await api('/api/brief/cancel', { job: S.job.id }); } catch (e) { /* the poll reports it */ } } }
  function restart() {
    if (S.draft) api('/api/brief/cancel-draft', {}).catch(() => {});
    closeMic(); S.draft = S.reading = S.plan = S.job = null; S.phase = 'brief'; S.error = null; S.panel.classList.remove('docked'); render();
  }

  // ------------------------------------------------ rendering
  function chip(text, tag, cls, attrs) { return `<button type="button" class="brf-chip ${cls}" ${attrs}><span>${esc(text)}</span><em>${esc(tag)}</em></button>`; }
  function briefHtml() {
    const d = S.data; if (!d) return checkingHtml('Loading');
    const modes = d.modes || {};
    const plans = (d.prepared || []).map((c) => {
      const st = (c.gate || {}).status;
      const g = c.gate || {}, tag = st === 'shown' ? `${g.before.weighted} → ${g.after.weighted} lifts` : st === 'not_shown' ? 'not shown' : 'checking';
      return chip(c.sentence, tag, st === 'not_shown' ? 'bad' : st === 'shown' ? '' : 'wait', `data-s="${esc(c.sentence)}" data-c="${esc(c.call)}"`);
    }).join('');
    const clips = (d.voice_clips || []).map((c) => chip(c.said, 'recorded clip', 'clip', `data-clip="${esc(c.id)}"`)).join('');
    const ref = d.refusals || {};
    const cards = ref.status === 'checking' ? '' : ref.status !== 'available' ? `<span class="brf-pend">${esc(ref.message)}</span>`
      : (ref.cards || []).map((c) => chip(c.sentence, c.arithmetic ? 'impossible' : c.code === 'ambiguous' ? 'unclear' : 'refused', 'ref', `data-s="${esc(c.sentence)}" data-c="${esc(c.call)}"`)).join('');
    const hero = d.hero ? chip(d.hero.sentence, 'plan pending', 'pend', `data-s="${esc(d.hero.sentence)}"`) : '';
    const ships = (d.calls || []).map((c) => `<option value="${esc(c.id)}"${c.id === S.call ? ' selected' : ''}>${esc(c.vessel)} · ${esc(c.load_boxes)} boxes · ${esc(c.id)}</option>`).join('');
    const micTitle = modes.speech === 'live' ? 'Speak the sentence. Muse Voice Transcribe is a cloud call.' : 'Speech is off in recorded mode; you can still try the microphone';
    return `<div class="brf-say"><button type="button" class="brf-mic" id="brf-mic" data-a="mic" title="${esc(micTitle)}" aria-label="Speak the sentence">${MIC_SVG}</button>
        <textarea id="brf-text" class="brf-edit" rows="2" maxlength="500" placeholder="Say or type the instruction" aria-label="Instruction">${esc(S.text)}</textarea></div>
      ${S.error ? `<div class="brf-err">${esc(S.error)}</div>` : ''}
      <div class="brf-row"><label>Ship<select id="brf-ship" aria-label="Ship">${ships}</select></label><button type="button" class="brf-go" data-a="draft">Review sentence</button></div>
      <div class="brf-group"><span class="brf-eyebrow">Prepared, each with its stored plan</span><div class="brf-chips">${plans}${hero}</div></div>
      <div class="brf-group"><span class="brf-eyebrow">Spoken, recorded clip</span><div class="brf-chips">${clips}</div></div>
      <div class="brf-group"><span class="brf-eyebrow">It says no</span><div class="brf-chips">${cards}</div></div>`;
  }
  function bodyHtml() {
    const v = S.reading, d = S.data;
    switch (S.phase) {
      case 'listening': return `<div class="brf-listen"><canvas class="brf-wave"></canvas><div class="brf-timer">${S.listening === 'mic' ? '0.0 s of 30 s' : ''}</div></div>
        ${S.listening === 'mic' ? '<div class="brf-acts"><button type="button" class="brf-go" data-a="stop">Stop and transcribe</button><button type="button" data-a="restart">Cancel</button></div><p class="brf-hint">Recording stays in this browser until you stop, then goes to Muse Voice Transcribe, a cloud call.</p>' : '<p class="brf-hint">Recorded clip, synthetic voice. Its transcript was made by Muse Voice Transcribe, a cloud call.</p>'}`;
      case 'working': return checkingHtml(S.working || 'Working');
      case 'transcript': return transcriptHtml(S.draft);
      case 'reading': return readingHtml(v);
      case 'refused': case 'impossible': return renderRefusal(Object.assign({}, v.card, { sentence: v.sentence, reader: v.reader, recorded: v.recorded }));
      case 'plan': return planHtml(S.plan);
      case 'none': return noPlanHtml(S.plan, (d || {}).messages);
      case 'replan': return replanHtml(S.job, S.reading.sentence, S.reading.rule);
      default: return briefHtml();
    }
  }
  function modeTag() {
    const m = (S.data || {}).modes || {};
    return m.reading === 'live' ? '<span class="brf-mode live">live reading</span>' : `<span class="brf-mode" title="${esc(m.reason || '')}">recorded mode · no model calls</span>`;
  }
  // Lead claim 3 is earned per reading: the header says "on this laptop" only when the reader's evidence shows it.
  function brandFromEvidence(reader) {
    const el = $('brand-sub');
    if (el && reader && reader.on_device) el.innerHTML = 'fine-tuned Muse Glimmer 30B <i>·</i> reading on this laptop';
  }
  function render() {
    if (S.reading) brandFromEvidence(S.reading.reader);
    if (!S.panel || VIDEO) return;
    const box = $('brf-text'); if (box) S.text = box.value;
    S.panel.innerHTML = `<div class="brf-head"><span class="brf-title">VOYAGE BRIEF</span>${modeTag()}<button type="button" class="brf-x" data-a="close" aria-label="Close">×</button></div>${stepsHtml(S.phase === 'working' ? (S.reading ? 'plan' : S.draft ? 'reading' : 'transcript') : S.phase)}<div class="brf-body">${bodyHtml()}</div>`;
  }
  function animateCounters(root) {
    for (const el of (root || S.panel).querySelectorAll('.brf-count')) {
      const from = +el.dataset.from, to = +el.dataset.to; if (from === to) continue;
      const t0 = performance.now(), ms = 900;
      const tick = (now) => {
        const p = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - p, 3);
        el.textContent = String(Math.round(from + (to - from) * e));
        if (p < 1) requestAnimationFrame(tick);
      };
      el.textContent = String(from); requestAnimationFrame(tick);
    }
  }
  function toggle(on) {
    const open = on == null ? S.panel.hidden : on;
    S.panel.hidden = !open; if (S.btn) S.btn.setAttribute('aria-pressed', String(open));
    if (open && S.phase === 'brief') { render(); const t = $('brf-text'); if (t) t.focus(); }
  }
  function onClick(e) {
    const chipEl = e.target.closest('.brf-chip');
    if (chipEl) {
      if (chipEl.dataset.clip) { const clip = S.data.voice_clips.find((c) => c.id === chipEl.dataset.clip); if (clip) playClip(clip); return; }
      if (chipEl.dataset.c) S.call = chipEl.dataset.c;
      makeDraft(chipEl.dataset.s); return;
    }
    const a = (e.target.closest('[data-a]') || {}).dataset; if (!a) return;
    const act = { close: () => toggle(false), mic: startMic, stop: stopMic, restart, confirm: confirmDraft, plan: planThis, switch: switchPlan, replan: startReplan, cancel: cancelReplan,
      draft: () => { const t = $('brf-text').value; if (t.trim()) makeDraft(t); else { S.error = 'Type or say a sentence first.'; render(); } } }[a.a];
    if (act) act();
  }

  // ------------------------------------------------ result strip after the stage switched to the plan
  function dock() {
    const last = saved.get();
    const q = params.get('plan_id');
    if (!last || !q || ((last.gate || {}).after || {}).plan_id !== q) return false;
    S.plan = last; S.phase = 'plan';
    S.panel.classList.add('docked'); S.panel.hidden = false;
    S.panel.innerHTML = `<div class="brf-head"><span class="brf-title">VOYAGE BRIEF</span><span class="brf-mode">${esc(last.recorded_label || 'recorded plan, not a live solve')}</span><button type="button" class="brf-x" data-a="close" aria-label="Close">×</button></div><div class="brf-body">${planHtml(last, true)}<div class="brf-acts"><a class="brf-link" href="${esc(backHref(last))}">Show the plan before the rule</a><button type="button" data-a="restart">New brief</button></div></div>`;
    animateCounters();
    return true;
  }
  function backHref(last) { const q = new URLSearchParams(location.search); q.set('plan_id', ((last.gate || {}).before || {}).plan_id || ''); q.delete('brief_case'); q.delete('autoplay'); return '?' + q.toString(); }

  // ------------------------------------------------ data, gates polling
  async function load() {
    try { S.data = await api('/api/brief/state'); } catch (e) { S.data = null; S.error = `Brief panel unavailable: ${e.message}`; }
    if (S.data && !S.call) S.call = S.data.default_call;
    settle();
    if (S.phase === 'brief') render();
    if (S.directorState) paint(S.directorState, true);
  }
  function settled() {
    const d = S.data; if (!d) return false;
    return (d.refusals || {}).status !== 'checking' && (d.prepared || []).every((c) => ['shown', 'not_shown'].includes((c.gate || {}).status));
  }
  function settle() {
    if (settled()) { for (const f of waiters.splice(0)) f(); return; }
    setTimeout(load, 2000);
  }
  const waiters = [];

  // ------------------------------------------------ the director API (pure render, recorded data, no parser call)
  function paint(state, force) {
    const fp = JSON.stringify([state, settled()]);
    if (!force && fp === S.directorKey) return;
    S.directorKey = fp;
    S.director.hidden = false;
    S.director.classList.toggle('full', !!state.full);
    S.director.innerHTML = `<div class="brf-head"><span class="brf-title">VOYAGE BRIEF</span><span class="brf-mode">recorded</span></div>${directorHtml(S.data, state)}`;
    const c = S.director.querySelector('.brf-wave');
    if (c) {
      const clip = ((S.data || {}).voice_clips || [])[0];
      drawBars(c, state.peaks || (clip ? clip.peaks : []), num(state.progress) ? state.progress : 1);
    }
  }
  window.StageBrief = {
    prepared: () => JSON.parse(JSON.stringify(preparedList(S.data))),
    show(state) {
      if (!S.director) { S.director = document.createElement('aside'); S.director.className = 'brf director'; S.director.id = 'brf-director'; document.body.appendChild(S.director); }
      if (!state) { S.director.hidden = true; S.directorState = null; S.directorKey = null; return; }
      S.directorState = state; paint(state);
    },
    ready: () => (settled() ? Promise.resolve() : new Promise((f) => waiters.push(f))),
    renderRefusal,
  };

  function boot() {
    if (VIDEO) { load(); return; }
    if (PRESENT && params.get('brief') !== '1') return;
    const head = document.querySelector('.ship-panel .panel-head'), wrap = $('ship-wrap');
    if (!head || !wrap) return;
    S.btn = document.createElement('button');
    S.btn.type = 'button'; S.btn.className = 'brf-btn'; S.btn.id = 'brf-btn'; S.btn.innerHTML = '<i></i>BRIEF';
    S.btn.title = 'Say or type a voyage instruction (key V)'; S.btn.setAttribute('aria-pressed', 'false');
    S.btn.onclick = () => toggle();
    head.insertBefore(S.btn, head.querySelector('.ins-btn') || $('xray-btn') || null);
    S.panel = document.createElement('aside'); S.panel.className = 'brf'; S.panel.id = 'brf-panel'; S.panel.hidden = true;
    S.panel.addEventListener('click', onClick);
    wrap.appendChild(S.panel);
    window.addEventListener('keydown', (e) => {
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
      if (e.key === 'v' || e.key === 'V') { e.preventDefault(); toggle(); }
    });
    S.panel.addEventListener('change', (e) => { if (e.target.id === 'brf-ship') S.call = e.target.value; });
    load().then(() => {
      if (params.get('brief_case') === 'last' && dock()) return;
      if (params.get('brief') === '1') toggle(true);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
