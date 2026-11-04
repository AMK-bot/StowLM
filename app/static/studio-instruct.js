/* Instructions: recorded radio and reader evidence; only the planner's confirmation is posted. */
(function () {
  'use strict';
  const Studio = window.Studio;
  if (!Studio) return;

  Studio.step({
    id: 'instruct', order: 20, title: 'Instructions', icon: 'radio',
    features: ['voice', 'reading', 'xray'], ship: true,
    mount(panel, data, ctx) {
      const voice = data.voice, reading = data.reading, xray = data.xray;
      const esc = ctx.esc;
      const chip = (text, kind) => text ? ctx.chip(text, kind) : '';
      const constraint = reading.answer.constraint;
      const fields = Object.entries(constraint).filter(([key]) => key !== 'type');
      const ruleTitle = [constraint.type, ...fields.map(([, value]) => typeof value === 'object' ? JSON.stringify(value) : String(value))].join(' · ');
      const jsonTokens = JSON.stringify(reading.answer, null, 2).match(/"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null|[{}[\],:]|\s+/g) || [];
      const duration = Number(voice.seconds) || 0;
      const peaks = voice.peaks || [];
      const words = voice.words || [];
      const energy = peaks.reduce((sum, peak) => sum + peak * peak, 0) || 1;
      let accumulated = 0;
      const energyAt = peaks.map((peak) => (accumulated += peak * peak) / energy);
      const wordAt = words.map((_, i) => Math.max(0, energyAt.findIndex((value) => value >= (i + 0.35) / words.length)) / Math.max(1, peaks.length));
      const timing = reading.seconds == null ? '' : `${reading.seconds_note ? 'About ' : ''}${Number(reading.seconds).toFixed(1)} s${reading.seconds_note ? ` · ${reading.seconds_note}` : ''}`;
      const adapter = reading.evidence && reading.evidence.adapter_sha256;
      panel.innerHTML = `<div class="st-instruct-root">
        <section class="st-card st-instruct-radio" hidden>
          <div class="st-card-title st-instruct-channel">Radio · harbour channel</div>
          <p class="st-meta">${esc(voice.label)}</p>
          ${voice.role !== 'hero' ? `<div class="st-instruct-chips">${chip('Separate demos', 'synthetic')}</div>${voice.note ? `<p class="st-meta">${esc(voice.note)}</p>` : ''}` : ''}
          <div class="st-instruct-wave" role="img" aria-label="Recorded radio waveform">${peaks.map((peak) => `<progress max="1" value="${Math.max(0.02, Number(peak))}" aria-hidden="true"></progress>`).join('')}</div>
          <div class="st-instruct-transport"><button type="button" class="st-btn primary" data-action="play">Play</button><span class="st-meta st-mono st-instruct-clock">${duration.toFixed(2)} s</span></div>
          ${voice.audio_note ? `<p class="st-meta">${esc(voice.audio_note)}</p>` : ''}
          <div class="st-instruct-transcription" hidden>
            <p class="st-meta st-instruct-actor-meta"><i class="st-instruct-actor-dot st-instruct-voice-dot" aria-hidden="true"></i>${esc(voice.heard_by)}</p><div class="st-instruct-chips">${chip(reading.label, 'recorded')}</div>
            <blockquote class="st-instruct-transcript" aria-label="Radio transcript">${words.map((word) => `<span>${esc(word.heard)}</span>`).join(' ')}</blockquote>
            <p class="st-instruct-heard st-meta"></p>
            ${voice.latency_s == null ? '' : `<p class="st-meta">Transcription · ${Number(voice.latency_s).toFixed(2)} s</p>`}
          </div>
        </section>
        <button type="button" class="st-btn primary st-instruct-read" data-action="read" hidden>Read with StowLM Reader</button>
        <section class="st-card st-instruct-rule" hidden>
          <p class="st-meta st-instruct-actor-meta"><i class="st-instruct-actor-dot st-instruct-reader-dot" aria-hidden="true"></i>${esc(reading.reader.label)}</p><div class="st-instruct-chips">${chip(reading.label, 'recorded')}</div>
          <div class="st-card-title st-instruct-rule-title st-mono">${ruleTitle.split(' · ').map((token) => `<span>${esc(token)}</span>`).join(' · ')}</div>
          <p class="st-instruct-plain">${esc(reading.rule)}</p>
          ${timing ? `<p class="st-meta">Reading time · ${esc(timing)}</p>` : ''}
          <p class="st-instruct-scope st-meta">${esc(reading.scope_line)}</p>
          <section class="st-instruct-reader-evidence" hidden></section>
          <div class="st-instruct-bay-marker" hidden></div>
          <div class="st-instruct-actions"><button type="button" class="st-btn ghost" data-action="inside" hidden>Look inside</button><button type="button" class="st-btn primary" data-action="confirm" hidden>Confirm rule</button></div>
          <details class="st-disclosure st-instruct-details"><summary>Details</summary><dl class="st-kv">${adapter ? `<dt>Adapter</dt><dd class="st-mono">${esc(adapter)}</dd>` : ''}</dl><pre class="st-instruct-json st-mono">${jsonTokens.map((token) => `<span>${esc(token)}</span>`).join('')}</pre></details>
        </section>
        <p class="st-instruct-status st-callout" role="status" aria-live="polite"></p>
      </div>`;
      const root = panel.firstElementChild;
      const $ = (selector) => root.querySelector(selector);
      const playButton = $('[data-action="play"]'), readButton = $('[data-action="read"]');
      const insideButton = $('[data-action="inside"]'), confirmButton = $('[data-action="confirm"]');
      const bars = [...root.querySelectorAll('.st-instruct-wave progress')];
      bars.forEach((bar, i) => { bar.style.height = `${Math.max(2, Number(peaks[i]) * 100)}%`; });
      const wordNodes = [...root.querySelectorAll('.st-instruct-transcript span')];
      const tokenNodes = [...root.querySelectorAll('.st-instruct-json span')];
      const ruleNodes = [...root.querySelectorAll('.st-instruct-rule-title span')];
      const status = $('.st-instruct-status');
      let active = false, epoch = 0, phase = 'radio', audio = null, action = Promise.resolve();
      let overlay = null, player = null, playerReady = null, overlayOpen = false;
      let confirmed = false, posting = false, started = false, markerScene = null;
      let evidenceReady = null, evidenceCards = null;
      let markerLayer = null, markerCanvas = null, markerLabel = null, markerFrame = 0, markerObserver = null;

      function clearShipMarker() {
        if (markerFrame && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(markerFrame);
        markerFrame = 0;
        if (markerObserver) markerObserver.disconnect();
        markerObserver = null;
        if (markerLayer) markerLayer.remove();
        markerLayer = null; markerCanvas = null; markerLabel = null;
      }
      function drawShipMarker() {
        if (!active || !confirmed || !markerCanvas) return;
        const stage = shipStage(), scene = stage && stage.scene;
        const shipCanvas = document.querySelector('#ship-canvas');
        if (!scene || !shipCanvas || typeof scene.slotList !== 'function' || typeof scene.shipExtOfSlot !== 'function' || typeof scene.project !== 'function') return;
        const slots = scene.slotList().filter((slot) => Number(slot.bay == null ? String(slot.id).slice(0, 3) : slot.bay) === Number(constraint.bay));
        const rect = shipCanvas.getBoundingClientRect();
        const width = rect.width, height = rect.height;
        if (!width || !height) { markerLayer.hidden = true; return; }
        markerLayer.hidden = false;
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        markerCanvas.width = Math.round(width * dpr); markerCanvas.height = Math.round(height * dpr);
        markerCanvas.setAttribute('data-slot-count', slots.length);
        const painter = markerCanvas.getContext('2d');
        if (!painter) return;
        painter.setTransform(dpr, 0, 0, dpr, 0, 0);
        painter.clearRect(0, 0, width, height);
        const palette = typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement || shipCanvas) : null;
        const red = palette && palette.getPropertyValue('--st-red').trim();
        const orange = palette && (palette.getPropertyValue('--st-orange').trim() || palette.getPropertyValue('--st-amber').trim());
        const rgb = (hex) => /^#[0-9a-f]{6}$/i.test(hex || '') ? [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16)) : null;
        const r = rgb(red), o = rgb(orange);
        const tint = r && o ? `rgb(${r.map((value, i) => Math.round((value + o[i]) / 2)).join(',')})` : (red || orange);
        if (tint) { painter.fillStyle = tint; painter.globalAlpha = 0.45; }
        painter.beginPath();
        const points = [];
        slots.forEach((slot) => {
          const e = scene.shipExtOfSlot(String(slot.id));
          if (!e) return;
          const project = (x, y, z) => scene.project(x, y, z);
          const faces = [
            [project(e.x0, e.y0, e.z1), project(e.x1, e.y0, e.z1), project(e.x1, e.y1, e.z1), project(e.x0, e.y1, e.z1)],
            [project(e.x0, e.y0, e.z0), project(e.x1, e.y0, e.z0), project(e.x1, e.y0, e.z1), project(e.x0, e.y0, e.z1)],
            [project(e.x1, e.y0, e.z0), project(e.x1, e.y1, e.z0), project(e.x1, e.y1, e.z1), project(e.x1, e.y0, e.z1)],
          ];
          faces.forEach((face) => {
            points.push(...face);
            const area = face.reduce((sum, point, i) => { const next = face[(i + 1) % face.length]; return sum + point[0] * next[1] - next[0] * point[1]; }, 0);
            const polygon = area < 0 ? [...face].reverse() : face;
            painter.moveTo(polygon[0][0], polygon[0][1]);
            polygon.slice(1).forEach((point) => painter.lineTo(point[0], point[1]));
            painter.closePath();
          });
        });
        // One fill prevents stacked slots from accumulating opacity over the same pixel.
        if (tint) painter.fill();
        painter.globalAlpha = 1;
        if (points.length) {
          const xs = points.map((point) => point[0]), ys = points.map((point) => point[1]);
          markerLabel.style.left = `${Math.max(70, Math.min(width - 70, (Math.min(...xs) + Math.max(...xs)) / 2))}px`;
          markerLabel.style.top = `${Math.max(36, Math.min(height - 12, Math.min(...ys) - 8))}px`;
        }
      }
      function mountShipMarker() {
        const host = document.querySelector('#ship-wrap');
        const stage = shipStage(), scene = stage && stage.scene;
        if (!host || !scene || typeof scene.shipExtOfSlot !== 'function' || typeof scene.project !== 'function') return;
        if (!markerLayer) {
          markerLayer = document.createElement('div');
          markerLayer.className = 'st-instruct-ship-marker';
          markerLayer.setAttribute('data-bay', Number(constraint.bay));
          markerCanvas = document.createElement('canvas');
          markerCanvas.className = 'st-instruct-ship-tint';
          markerCanvas.setAttribute('data-bay', Number(constraint.bay));
          markerCanvas.setAttribute('aria-label', `Bay ${constraint.bay} closed for this call`);
          markerLabel = document.createElement('span');
          markerLabel.className = 'st-chip warn st-instruct-ship-label';
          markerLabel.textContent = `Bay ${constraint.bay} · closed`;
          if (typeof getComputedStyle === 'function') {
            const nightBackground = getComputedStyle(host).backgroundColor;
            if (nightBackground) markerLabel.style.setProperty('--st-instruct-ship-label-bg', nightBackground);
          }
          markerLayer.appendChild(markerCanvas); markerLayer.appendChild(markerLabel); host.appendChild(markerLayer);
          if (typeof ResizeObserver === 'function') { markerObserver = new ResizeObserver(drawShipMarker); markerObserver.observe(host); }
        }
        drawShipMarker();
        if (!markerFrame && typeof requestAnimationFrame === 'function') {
          const tick = () => { markerFrame = 0; if (!active || !confirmed || !markerLayer) return; drawShipMarker(); markerFrame = requestAnimationFrame(tick); };
          markerFrame = requestAnimationFrame(tick);
        }
      }

      function loadEvidence() {
        if (!evidenceReady) evidenceReady = ctx.data('evidence').then((evidence) => {
          evidenceCards = evidence.cards;
          const target = $('.st-instruct-reader-evidence');
          target.innerHTML = comparisonHTML();
          target.hidden = false;
          return evidence;
        });
        return evidenceReady;
      }

      function start() {
        if (!started) {
          started = true;
          $('.st-instruct-radio').hidden = false;
        }
        return loadEvidence();
      }
      function shipStage() { return ctx.stage || window.__stage; }
      function markConfirmedBay() {
        if (constraint.type !== 'close_bay' || constraint.bay == null) return;
        const stage = shipStage();
        const scene = stage && stage.scene;
        const slots = scene && typeof scene.slotList === 'function' ? scene.slotList() : (((stage || {}).B || {}).geometry || {}).slots || [];
        const bayOf = (slot) => slot.bay == null ? Number(String(slot.id).slice(0, 3)) : Number(slot.bay);
        const baySlots = slots.filter((slot) => bayOf(slot) === Number(constraint.bay));
        // Reuse the v6 replan's public through-hull marks, on actual slots, retaining the current ship state.
        if (scene && typeof scene.setMorph === 'function' && stage.S && stage.S.state && baySlots.length) {
          // The scene's legacy marks have a fixed amber glow. Use them only when
          // the projected overlay is unavailable, so its 45% tint stays 45%.
          const projected = document.querySelector('#ship-wrap') && typeof scene.shipExtOfSlot === 'function' && typeof scene.project === 'function';
          const marks = projected ? [] : baySlots.map((slot) => ({ slot: String(slot.id) }));
          scene.setMorph({ state: stage.S.state, glow: new Set(), marks, flyers: [] });
          markerScene = scene;
        }
        mountShipMarker();
        // The compact diagram remains useful when the renderer is absent. Every bay comes from
        // this ship's geometry; without geometry show only the bay explicitly named in the reading.
        const bays = slots.length ? [...new Set(slots.map(bayOf))].filter(Number.isFinite).sort((a, b) => a - b) : [Number(constraint.bay)];
        const diagram = $('.st-instruct-bay-marker');
        diagram.innerHTML = `<p class="st-callout ok">Bay ${esc(constraint.bay)} closed for this call</p><div class="st-instruct-bay-strip" role="img" aria-label="Confirmed bay ${esc(constraint.bay)}">${bays.map((bay) => bay === Number(constraint.bay) ? `<span class="st-chip warn">Bay <s>${esc(bay)}</s> · closed</span>` : chip(`Bay ${bay}`, 'stored')).join('')}</div>`;
        diagram.hidden = false;
      }

      function showProgress(fraction) {
        bars.forEach((bar, i) => bar.classList.toggle('st-instruct-past', i / bars.length <= fraction));
        wordNodes.forEach((node, i) => node.classList.toggle('st-instruct-on', fraction >= wordAt[i]));
        $('.st-instruct-clock').textContent = `${(fraction * duration).toFixed(1)} / ${duration.toFixed(2)} s`;
      }
      function finishVoice() {
        phase = 'transcribed';
        showProgress(1);
        $('.st-instruct-heard').textContent = voice.heard_line || '';
        readButton.hidden = false;
        playButton.disabled = false;
        playButton.textContent = 'Replay';
        playButton.classList.remove('primary');
        playButton.classList.add('ghost');
      }
      async function playVoice() {
        if (phase === 'playing') return;
        const run = epoch;
        const replay = phase !== 'radio';
        if (!replay) phase = 'playing';
        playButton.disabled = true;
        $('.st-instruct-transcription').hidden = false;
        ctx.log({ who: 'Muse Voice Transcribe', where: 'cloud', text: `Transcribe radio · ${voice.transcript}`, label: reading.label });
        if (voice.audio) {
          audio = audio || new Audio(voice.audio);
          audio.currentTime = 0;
          audio.play().catch(() => { if (active) ctx.toast('Audio playback is unavailable; the recorded transcript is shown.', 'warn'); });
        }
        const frames = 40;
        for (let i = 0; i <= frames; i++) {
          if (!active || run !== epoch) return;
          // The envelope and word reveal use the recording's measured energy, as in stage-v6voice.
          showProgress(i / frames);
          if (i < frames) await ctx.wait(duration * 1000 / frames);
        }
        if (replay) { playButton.disabled = false; return; }
        finishVoice();
      }
      async function readRule() {
        if (phase !== 'transcribed') return;
        phase = 'reading';
        const run = epoch;
        await loadEvidence();
        if (!active || run !== epoch) return;
        readButton.hidden = true;
        $('.st-instruct-rule').hidden = false;
        ctx.log({ who: 'StowLM Reader', where: reading.reader.on_device ? 'on this laptop' : 'cloud', text: `Read instruction · ${reading.rule}${timing ? ` · ${timing}` : ''}`, label: reading.label });
        // This is a recorded answer, never an invented live model call.
        const batches = Math.min(12, Math.max(1, tokenNodes.length));
        for (let i = 1; i <= batches; i++) {
          await ctx.wait(100);
          if (!active || run !== epoch) return;
          tokenNodes.forEach((node, j) => node.classList.toggle('st-instruct-on', j < Math.ceil(i * tokenNodes.length / batches)));
          ruleNodes.forEach((node, j) => node.classList.toggle('st-instruct-on', j < Math.ceil(i * ruleNodes.length / batches)));
        }
        phase = 'read';
        insideButton.hidden = false;
        confirmButton.hidden = false;
        ctx.emit('rule:read', reading);
      }
      function comparisonHTML() {
        const card = evidenceCards && evidenceCards.find((item) => item.id === 'reader');
        if (!card) return '';
        const series = card.series;
        const tuned = series.find((row) => /adopted.*reader/i.test(row.label));
        const untuned = series.find((row) => /without fine-tune/i.test(row.label));
        const comparisons = series.filter((row) => /spark|parser/i.test(row.label));
        return `<div class="st-card st-instruct-comparison"><p class="st-card-title">${esc(card.title)}</p><p class="st-meta">${esc(card.label)}</p>${[[untuned, 'without'], [tuned, 'with']].filter(([row]) => row).map(([row, key]) => `<div class="st-instruct-result-row"><span>${esc(row.label)}</span><progress max="${esc(row.n)}" value="${esc(row.value)}"></progress><span class="st-mono">${esc(row.value)} of ${esc(row.n)}${reading.bars && reading.bars.shown && reading.bars.shown[key] ? ` · ${esc(reading.bars.shown[key])}` : ''}</span></div>`).join('')}<p class="st-meta">${esc(card.does_not)}</p>${comparisons.map((row) => `<p class="st-meta">${esc(row.label)}${/spark/i.test(row.label) ? ' · cloud · earlier mask, fixed reference' : ''}: ${esc(row.value)} of ${esc(row.n)}</p>`).join('')}</div>`;
      }
      function maskEvidenceHTML() {
        const card = evidenceCards && evidenceCards.find((item) => item.id === 'mask');
        if (!card) return '';
        return `<section class="st-card st-instruct-mask-evidence"><p class="st-card-title">${esc(card.title)}</p><p class="st-meta">${esc(card.label)}</p><dl class="st-kv">${card.series.map((row) => `<dt>${esc(row.label)}</dt><dd>${esc(row.value)} of ${esc(row.n)}</dd>`).join('')}</dl><p class="st-meta">${esc(card.show)}</p>${card.does_not && !card.show.includes(card.does_not) ? `<p class="st-meta">${esc(card.does_not)}</p>` : ''}</section>`;
      }
      function loadPlayerScript() {
        if (window.StowLMXray) return Promise.resolve();
        return new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = xray.player_js;
          script.onload = resolve;
          script.onerror = () => reject(new Error('The reader x-ray could not be opened.'));
          document.head.appendChild(script);
        });
      }
      function stylePlayer() {
        const host = overlay && overlay.querySelector('.st-instruct-player');
        if (!host) return;
        const classes = (selector, ...names) => host.querySelectorAll(selector).forEach((node) => node.classList.add(...names));
        classes('.xray-panel, .xray-chosen-box', 'st-card');
        classes('h1, h2', 'st-card-title');
        classes('.xray-kicker, .xray-count, .xray-context, .xray-validation, .xray-probability-note, .xray-legend, .xray-footer, .xray-rank, .xray-probability, .xray-decision, .xray-reason', 'st-meta');
        classes('.xray-json, .xray-chosen-token, .xray-wish-token', 'st-mono');
        classes('.xray-new-token', 'st-chip');
        classes('.xray-probability-track', 'st-bar');
        classes('.xray-source', 'st-meta');
        classes('button, select', 'st-btn', 'secondary');
        classes('.xray-play', 'primary');
        host.querySelectorAll('.xray-play').forEach((node) => node.classList.remove('secondary'));
        classes('.is-blocked .xray-reason', 'st-callout', 'bad');
        classes('.is-chosen .xray-reason', 'st-callout', 'ok');
        const say = (selector, text) => {
          const node = host.querySelector(selector);
          if (node && node.textContent !== text) node.textContent = text;
        };
        say('.xray-brand .xray-kicker', 'StowLM Reader · token choices');
        say('.xray-brand h1', 'Token choices and schema mask');
        say('.xray-sentence .xray-kicker', 'Sentence');
        say('.xray-chosen-box .xray-kicker', 'Token chosen after the mask');
        say('.xray-source', xray.caption_label || xray.source_label);
        host.querySelectorAll('.xray-count, .xray-reason, .xray-decision, .xray-legend-blocked, .xray-empty').forEach((node) => {
          // Labels only: token contents, trace fields and provenance remain byte-for-byte intact.
          const text = node.textContent.replace(/\bTOKEN\b/g, 'Token').replace(/BEFORE MASK/g, 'Before mask')
            .replace(/Top wish blocked:/gi, "Mask overrode the model's first choice:")
            .replace(/blocked by mask/gi, 'Not allowed by mask').replace(/blocked tokens/gi, 'tokens not allowed by mask')
            .replace(/\bblocked\b/gi, 'Not allowed by mask')
            .replace(/\bCHOSEN\b/g, 'Chosen');
          if (text !== node.textContent) node.textContent = text;
        });
        const position = player && typeof player.getState === 'function' ? player.getState().step : 0;
        const row = position ? xray.trace.steps[position - 1] : null;
        const firstChoice = row && row.top_k[0];
        say('.xray-wishes-panel .xray-count', !row ? 'Before mask' : row.is_stop ? 'Complete · stop token permitted' : firstChoice && firstChoice.blocked ? "Mask overrode the model's first choice" : "The model's first choice was allowed");
        // The asset's nextBlock jumps to any forbidden alternative, not a first-choice override.
        host.querySelectorAll('.xray-block-button').forEach((node) => node.remove());
      }
      function closeInside() {
        const wasOpen = overlayOpen;
        overlayOpen = false;
        if (player) { player.pause(); stylePlayer(); }
        if (wasOpen) ctx.center(null);
        insideButton.textContent = 'Look inside';
      }
      async function openInside() {
        if (overlayOpen) { closeInside(); return; }
        const run = epoch;
        await loadEvidence();
        if (!active || run !== epoch) return;
        overlayOpen = true;
        insideButton.textContent = 'Back to ship';
        if (!overlay) {
          overlay = document.createElement('section');
          overlay.className = 'st-instruct-inside';
          overlay.setAttribute('aria-label', 'Reader x-ray');
          overlay.innerHTML = `<header class="st-instruct-inside-head"><div><p class="st-card-title">Reader x-ray</p><p class="st-meta">${esc(xray.caption_label || xray.source_label)}</p></div><button type="button" class="st-btn ghost" data-action="close">Back to ship</button></header>${comparisonHTML()}${maskEvidenceHTML()}<p class="st-instruct-xray-note st-meta">${xray.real ? `In this recorded reading the mask overrode the model's first choice on ${xray.blocked} of ${xray.tokens} next words.` : 'A scripted example: no model ran.'} The mask keeps the form legal. Legal can still be wrong, so a person confirms.</p><div class="st-card st-instruct-player"></div>`;
          overlay.querySelector('[data-action="close"]').addEventListener('click', closeInside);
        }
        ctx.center(overlay);
        ctx.log({ who: 'StowLM Reader', where: 'on this laptop', text: 'Inspect recorded token choices and schema mask', label: xray.source_label });
        if (!playerReady) playerReady = loadPlayerScript().then(() => {
          const host = overlay.querySelector('.st-instruct-player');
          player = window.StowLMXray.mount(host, xray.trace, { initialStep: 0, speed: 6, autoplay: false, fit: false });
          stylePlayer();
          // Player render replaces token rows. Reapply shared components without modifying its asset.
          if (typeof MutationObserver === 'function') new MutationObserver(stylePlayer).observe(host, { childList: true, subtree: true });
          ['click', 'input', 'keydown', 'change'].forEach((event) => host.addEventListener(event, () => Promise.resolve().then(stylePlayer)));
          return player;
        }).catch((error) => { playerReady = null; ctx.toast(error.message, 'warn'); return null; });
        await playerReady;
        if (!active || !overlayOpen || !player) return;
        // Open on the same first overridden choice that stage-v6xray holds for inspection.
        const block = xray.trace.steps.findIndex((step) => step.top_k[0] && step.top_k[0].blocked && !step.is_stop) + 1;
        player.seek(block || 1);
        stylePlayer();
      }
      async function confirmRule() {
        if (phase !== 'read' || posting || confirmed) return;
        closeInside();
        posting = true;
        confirmButton.disabled = true;
        try {
          const response = await ctx.post('confirm', reading.binding || {});
          if (!response || !response.token) throw new Error((response && response.error) || 'The server did not record this confirmation.');
          ctx.state.confirm = response;
          confirmed = true;
          phase = 'confirmed';
          confirmButton.textContent = 'Rule confirmed';
          status.textContent = 'The planner confirmed this reading. It goes to the search software.';
          status.classList.add('ok');
          ctx.log({ who: 'Planner', where: null, text: `Confirmed · ${reading.rule}`, label: 'Confirmed on screen' });
          ctx.emit('rule:confirmed', { ...response, bay: Number(constraint.bay) });
          const stage = shipStage();
          if (stage && typeof stage.setPlaying === 'function') stage.setPlaying(false);
          markConfirmedBay();
          ctx.done();
        } catch (error) {
          status.textContent = error.message || 'The server did not record this confirmation.';
          confirmButton.disabled = false;
        } finally { posting = false; }
      }
      playButton.addEventListener('click', () => { action = playVoice(); });
      readButton.addEventListener('click', () => { action = readRule(); });
      insideButton.addEventListener('click', () => { action = openInside(); });
      confirmButton.addEventListener('click', () => { action = confirmRule(); });

      return {
        start,
        enter() {
          active = true;
          const stage = shipStage();
          if (stage && typeof stage.setPlaying === 'function') stage.setPlaying(false);
          if (confirmed) { markConfirmedBay(); ctx.done(); return; }
          if (phase === 'playing') finishVoice();
          if (phase === 'reading') {
            phase = 'read'; tokenNodes.forEach((node) => node.classList.add('st-instruct-on'));
            ruleNodes.forEach((node) => node.classList.add('st-instruct-on'));
            insideButton.hidden = false; confirmButton.hidden = false;
          }
        },
        leave() {
          active = false; epoch += 1;
          if (audio) audio.pause();
          closeInside();
          clearShipMarker();
          if (markerScene) { markerScene.setMorph(null); markerScene = null; }
        },
        async autoplay(auto) {
          await start();
          const run = epoch;
          await auto.wait(1100);
          if (!active || run !== epoch || confirmed) return;
          if (phase === 'radio') { await auto.click(playButton); await action; }
          await auto.wait(1600);
          if (!active || run !== epoch) return;
          if (phase === 'transcribed') { await auto.click(readButton); await action; }
          await auto.wait(2400);
          if (!active || run !== epoch) return;
          await auto.click(insideButton); await action;
          await auto.wait(5600);
          if (!active || run !== epoch) return;
          if (overlayOpen) await auto.click(overlay.querySelector('[data-action="close"]'));
          await auto.wait(1200);
          await auto.click(confirmButton); await action;
          await auto.wait(1200);
        },
      };
    },
  });
})();
