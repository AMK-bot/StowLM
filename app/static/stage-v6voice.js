/* v6 chapter 2: a noisy line. The hero wording's own radio take (D268 as amended): its measured peaks and its transcript, shown as it
 * came back. Nothing is staged as an error and nothing is fixed: the planner sees the words first, and may edit them in interactive
 * mode. Beside it, every wording of the same rule over the same line, with whether the bay number survived. Before those files land,
 * the separate clip plays under its stand-in tag. Data: /api/v6/voice (recorded). Recorded mode makes no calls, so an edited wording
 * gets a plain message, never a reading. */
(function () {
  'use strict';
  const V = window.StageV6, { clamp, outCubic, hash, fit, canvas, put } = window.V6FX;
  const WAVE_H = 300;
  const plain = (s) => String(s).toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
  V.chapter({ id: 'voice', order: 20, title: 'Radio', feature: 'voice', duration_s: 11,
    mount(host, d, ctx) {
      const esc = ctx.esc, S = d.seconds || 4.3, n = d.peaks.length, lines = d.lines || [];
      /* Words appear as the clip's energy accumulates: real pacing from real peaks, no hand timing. */
      const total = d.peaks.reduce((a, b) => a + b * b, 0) || 1; let sum = 0;
      const energyAt = d.peaks.map((p) => (sum += p * p) / total);
      const wordAt = d.words.map((_, i) => { const at = energyAt.findIndex((e) => e >= (i + 0.35) / d.words.length); return n ? (Math.max(0, at) / n) * S : (i / d.words.length) * S; });
      const T = { seen: S + 0.3, refuse: S + 0.65, lines: S + 1.1, send: Math.min(10.4, S + 1.6 + lines.length * 0.14) };
      const refusal = d.refused_first && d.refused_first.refusal;
      host.innerHTML = `<div class="v6x v6w">
        <header><h2>${lines.length ? 'A noisy line' : 'A noisy line, another clip'}</h2><div><span class="v6-tag">${esc(d.label)}</span><span class="v6-tag">${esc(d.heard_by)}</span><span class="v6-tag">Recorded answer</span>${d.note ? `<span class="v6-tag v6w-note">${esc(d.note)}</span>` : ''}</div></header>
        ${n ? '<canvas class="v6w-wave"></canvas>' : d.audio_note ? `<p class="v6w-noaudio">${esc(d.audio_note)}</p>` : ''}
        <div class="v6w-line">${d.words.map((w, i) => `<span data-i="${i}">${esc(w.heard)}</span>`).join(' ')}</div>
        ${d.heard_line ? `<p class="v6w-seen">${esc(d.heard_line)}</p>` : ''}
        ${refusal ? `<div class="v6w-refuse"><b>Refused</b><span>The reader on this text: ${esc(refusal.code)}, “${esc(refusal.reason)}”</span><small>Recorded answer</small></div>` : ''}
        ${lines.length ? `<section class="v6w-lines"><h3>${lines.length} wordings of the same rule, each over the same line, as transcribed</h3>
          <ol>${lines.map((l) => `<li class="${l.chosen ? 'chosen' : ''}"><q>${esc(l.transcript)}</q><span class="${l.bay_heard ? 'kept' : 'lost'}">${esc(l.tag || '')}</span>${l.chosen ? '<b>Selected</b>' : ''}</li>`).join('')}</ol></section>` : ''}
        <div class="v6w-tools" hidden>${d.audio ? '<button type="button" class="v6-btn ghost" data-a="play">Play the clip</button>' : ''}
          <form><input type="text" aria-label="Transcript" value="${esc(d.transcript)}" spellcheck="false"><button type="submit" class="v6-btn ghost">Use this wording</button></form>
          <button type="button" class="v6-btn" data-a="send">${d.role === 'hero' ? 'Send to the reader' : 'On to this voyage’s rule'}</button><p class="v6w-msg" role="status"></p></div>
      </div>`;
      const root = host.firstElementChild, $ = (s) => root.querySelector(s);
      const c = n ? canvas($('.v6w-wave'), 1920, WAVE_H) : null, spans = [...root.querySelectorAll('.v6w-line span')];
      const seen = $('.v6w-seen'), refuse = $('.v6w-refuse'), box = $('.v6w-lines'), rows = [...root.querySelectorAll('.v6w-lines li')];
      const tools = $('.v6w-tools'), msg = $('.v6w-msg'), input = tools.querySelector('input');
      let scale = 0, audio = null, lastDraw = -1;
      function draw(tau) {
        if (!c) return;
        const frame = Math.floor(tau * 30); if (frame === lastDraw) return; lastDraw = frame;
        c.clearRect(0, 0, 1920, WAVE_H);
        const head = clamp(tau / S), mid = WAVE_H / 2, w = 1800 / n, playing = tau < S;
        for (let i = 0; i < n; i++) {
          const x = 60 + i * w, past = i / n <= head, near = Math.exp(-(((i / n - head) * n / 12) ** 2));
          const noise = hash(i * 1.7 + frame * 2.3), haze = playing ? 0.1 * noise : 0;
          const a = (d.peaks[i] * (1 + (playing ? (0.3 + 0.9 * near) * (noise - 0.3) : 0)) + haze) * WAVE_H * 0.82;
          c.fillStyle = past ? (near > 0.4 && playing ? '#ffa53a' : '#cfe0f7') : '#2a4266';
          c.fillRect(x, mid - a / 2, Math.max(2, w - 3), Math.max(2, a));
        }
        if (playing) {
          c.fillStyle = '#ffa53a'; c.fillRect(60 + head * 1800 - 1, 8, 2, WAVE_H - 16);
          for (let i = 0; i < 140; i++) { c.globalAlpha = 0.28; c.fillStyle = '#cfe0f7'; c.fillRect(hash(i + frame) * 1920, mid + (hash(i * 3 + frame) - 0.5) * WAVE_H * 0.9, 3, 2); }
          c.globalAlpha = 1;
        }
      }
      if (d.audio) $('[data-a=play]').onclick = () => { audio = audio || new Audio(d.audio); audio.currentTime = 0; audio.play().catch(() => { msg.textContent = 'The browser would not play the clip.'; }); };
      $('[data-a=send]').onclick = () => { ctx.release('send'); ctx.next(); };
      tools.querySelector('form').addEventListener('submit', (e) => {
        e.preventDefault();
        msg.textContent = plain(input.value) === plain(d.transcript) ? 'That is the transcript as heard.'
          : "That wording has no recording. Recorded mode makes no calls, so there's no reading of it to show.";
      });
      return {
        enter() { scale = 0; lastDraw = -1; msg.textContent = ''; input.value = d.transcript; },
        seek(raw, live) {
          if (!scale) { scale = fit(host, root); if (!scale) return; if (tools.hidden === !!live) tools.hidden = !live; }
          let tau = clamp(raw, 0, 11);
          if (live && ctx.waitAt(tau, 'send', T.send)) tau = T.send;
          draw(tau);
          d.words.forEach((w, i) => put(spans[i], 'opacity', tau >= wordAt[i] ? '1' : '0'));
          if (seen) { const e = outCubic(clamp((tau - T.seen) / 0.3)); put(seen, 'opacity', String(e)); put(seen, 'transform', `translateY(${(1 - e) * 14}px)`); }
          if (refuse) { const e = outCubic(clamp((tau - T.refuse) / 0.22)); put(refuse, 'opacity', String(e)); put(refuse, 'transform', `translateY(${(1 - e) * 20}px)`); put(refuse, 'visibility', tau >= T.refuse ? 'visible' : 'hidden'); }
          if (box) {
            put(box, 'opacity', String(outCubic(clamp((tau - T.lines) / 0.3))));
            rows.forEach((el, i) => { const e = outCubic(clamp((tau - T.lines - 0.1 - i * 0.14) / 0.25)); put(el, 'opacity', String(e)); put(el, 'transform', `translateX(${(1 - e) * -16}px)`); });
          }
        },
      };
    } });
})();
