/* Radio inbox, recorded reader proposal, and the planner's explicit rule confirmation. */
(function () {
  'use strict';
  const Planner = window.Planner;
  if (!Planner) return;
  Planner.stage({
    id: 'instruct', order: 20, title: 'Instructions', features: ['voice', 'reading', 'xray'],
    mount(panel, data, ctx) {
      const voice = data.voice, reading = data.reading, xray = data.xray, esc = ctx.esc;
      const rule = reading.answer.constraint;
      const ruleParts = [rule.type, ...Object.entries(rule).filter(([key]) => key !== 'type').map(([, value]) => typeof value === 'object' ? JSON.stringify(value) : String(value))];
      const duration = Number(voice.seconds) || 0, peaks = voice.peaks || [], words = voice.words || [];
      const energy = peaks.reduce((sum, peak) => sum + peak * peak, 0) || 1;
      let sum = 0;
      const energyAt = peaks.map((peak) => (sum += peak * peak) / energy);
      const wordAt = words.map((_, i) => Math.max(0, energyAt.findIndex((value) => value >= (i + 0.35) / words.length)) / Math.max(1, peaks.length));
      const timing = reading.seconds == null ? '' : `${reading.seconds_note ? 'About ' : ''}${Number(reading.seconds).toFixed(1)} s${reading.seconds_note ? ` · ${reading.seconds_note}` : ''}`;
      const badge = (text) => text ? `<span class="pl-instruct-badge">${esc(text)}</span>` : '';
      const actor = (text, kind) => `<p class="pl-instruct-meta pl-instruct-actor"><i class="pl-instruct-dot pl-instruct-dot-${kind}" aria-hidden="true"></i><span>${esc(text)}</span></p>`;
      const sentenceKey = (value) => String(value || '').trim().replace(/\s+/g, ' ');
      const recorded = new Map();
      const addRecorded = (sentence) => { if (sentenceKey(sentence)) recorded.set(sentenceKey(sentence), {sentence, example: sentenceKey(sentence) !== sentenceKey(reading.sentence)}); };
      addRecorded(reading.sentence);
      if (voice.role === 'hero' && reading.heard && reading.heard.binds === true) addRecorded(reading.heard.transcript);
      ((reading.prepared && reading.prepared.rows) || []).filter((row) => row.as_expected === true && !row.refused && row.read === reading.rule).forEach((row) => addRecorded(row.sentence));
      const suggestions = [...recorded.values()];
      panel.innerHTML = `<div class="pl-instruct-root">
        <section class="pl-instruct-card pl-instruct-radio" hidden>
          <div class="pl-instruct-card-head"><h3 class="pl-instruct-title">Radio · harbour channel</h3>${badge(reading.label)}</div>
          <p class="pl-instruct-meta">${esc(voice.label)}</p>
          ${voice.role !== 'hero' ? `${badge('Separate demos')}${voice.note ? `<p class="pl-instruct-meta">${esc(voice.note)}</p>` : ''}` : ''}
          <div class="pl-instruct-wave" role="img" aria-label="Recorded radio waveform">${peaks.map((peak) => `<i style="height:${Math.max(2, Number(peak) * 100)}%"></i>`).join('')}</div>
          <div class="pl-instruct-transport"><button type="button" class="pl-instruct-button pl-instruct-primary" data-action="play">Play</button><span class="pl-instruct-meta pl-instruct-clock">${duration.toFixed(2)} s</span></div>
          ${voice.audio_note ? `<p class="pl-instruct-meta">${esc(voice.audio_note)}</p>` : ''}
          <section class="pl-instruct-transcription" hidden>
            ${actor(voice.heard_by, 'voice')}
            <p class="pl-instruct-transcript" aria-label="Radio transcript">${words.map((word) => `<span>${esc(word.heard)}</span>`).join(' ')}</p>
            <p class="pl-instruct-meta pl-instruct-heard"></p>
            ${voice.latency_s == null ? '' : `<p class="pl-instruct-meta">Transcription · ${Number(voice.latency_s).toFixed(2)} s</p>`}
          </section>
        </section>
        <section class="pl-instruct-card pl-instruct-inputs">
          <label class="pl-instruct-title" for="pl-instruct-sentence">Type an instruction</label>
          <textarea id="pl-instruct-sentence" class="pl-instruct-input" aria-label="Type an instruction" rows="3" placeholder="Choose a recorded wording or type an instruction"></textarea>
          <p class="pl-instruct-meta">Recorded wordings for this call</p>
          <div class="pl-instruct-suggestions">${suggestions.map((item, i) => `<button type="button" class="pl-instruct-button pl-instruct-suggestion" data-suggestion="${i}">${esc(item.sentence)}</button>`).join('')}</div>
          <p class="pl-instruct-meta pl-instruct-input-status" role="status" aria-live="polite">Choose a recorded wording, or play the radio call.</p>
        </section>
        <button type="button" class="pl-instruct-button pl-instruct-primary pl-instruct-read" data-action="read" hidden>Read with StowLM Reader</button>
        <section class="pl-instruct-card pl-instruct-rule" hidden>
          <div class="pl-instruct-reader-identity" data-proposal-note>${actor(reading.reader.label, 'reader')}</div>
          <div class="pl-instruct-card-head"><h3 class="pl-instruct-title">Rule proposal</h3>${badge(reading.label)}</div>
          <p class="pl-instruct-meta pl-instruct-compact-source" hidden>${esc(reading.reader.short || 'StowLM Reader')} · ${esc(reading.label || '')}</p>
          <p class="pl-instruct-meta pl-instruct-selected-label" data-proposal-note></p>
          <p class="pl-instruct-selected-sentence" data-proposal-note></p>
          <p class="pl-instruct-rule-tokens">${ruleParts.map((part) => `<span>${esc(part)}</span>`).join(' · ')}</p>
          <p class="pl-instruct-rule-plain">${esc(reading.rule)}</p>
          ${timing ? `<p class="pl-instruct-meta pl-instruct-timing" data-proposal-note>Reading time · ${esc(timing)}</p>` : ''}
          <p class="pl-instruct-meta pl-instruct-scope" data-proposal-note>${esc(reading.scope_line)}</p>
          <section class="pl-instruct-reader-summary" data-proposal-note hidden></section>
          <div class="pl-instruct-actions" hidden><button type="button" class="pl-instruct-button" data-action="inside">Look inside</button><button type="button" class="pl-instruct-button pl-instruct-quiet" data-action="details">Details</button></div>
          <p class="pl-instruct-meta pl-instruct-confirmation-scope" data-proposal-note>Confirms the stored rule for this call using “${esc(reading.sentence)}”.</p>
          <button type="button" class="pl-instruct-button pl-instruct-primary pl-instruct-confirm" data-action="confirm" hidden>Confirm rule</button>
          <button type="button" class="pl-instruct-button pl-instruct-edit" data-action="edit" hidden>Edit instruction</button>
        </section>
        <p class="pl-instruct-status" role="status" aria-live="polite"></p>
      </div>`;
      const root = panel.firstElementChild, $ = (selector) => root.querySelector(selector);
      const playButton = $('[data-action="play"]'), readButton = $('[data-action="read"]'), insideButton = $('[data-action="inside"]');
      const confirmButton = $('[data-action="confirm"]'), detailsButton = $('[data-action="details"]');
      const editButton = $('[data-action="edit"]');
      const input = $('.pl-instruct-input'), inputStatus = $('.pl-instruct-input-status');
      const wave = [...root.querySelectorAll('.pl-instruct-wave i')], wordNodes = [...root.querySelectorAll('.pl-instruct-transcript span')];
      const ruleNodes = [...root.querySelectorAll('.pl-instruct-rule-tokens span')], status = $('.pl-instruct-status');
      let active = false, started = false, epoch = 0, phase = 'radio', audio = null, action = Promise.resolve(), confirmed = false, posting = false;
      let evidenceReady = null, evidenceCards = [], inside = null, details = null, sheetKind = null, player = null, playerReady = null;
      let selected = null;

      function compactConfirmed(compact) {
        root.classList.toggle('pl-instruct-is-confirmed', compact);
        $('.pl-instruct-radio').hidden = compact || !started;
        $('.pl-instruct-inputs').hidden = compact;
        root.querySelectorAll('[data-proposal-note]').forEach((node) => { node.hidden = compact; });
        $('.pl-instruct-compact-source').hidden = !compact;
        editButton.hidden = !compact;
        if (compact) confirmButton.hidden = true;
      }

      function selectSentence(sentence, fromRadio) {
        if (!fromRadio) active = true;
        const invalidated = confirmed || posting || phase === 'reading' || phase === 'read' || Boolean(ctx.state.confirm);
        if (!fromRadio) epoch += 1;
        if (audio) audio.pause();
        closeSheet();
        input.value = sentence;
        selected = recorded.get(sentenceKey(sentence)) || null;
        confirmed = false;
        compactConfirmed(false);
        delete ctx.state.confirm;
        phase = selected ? 'transcribed' : 'input';
        details = null;
        $('.pl-instruct-rule').hidden = true;
        $('.pl-instruct-actions').hidden = true;
        confirmButton.hidden = true; confirmButton.disabled = false;
        confirmButton.textContent = selected && selected.example ? 'Confirm the stored rule for this call' : 'Confirm rule';
        $('.pl-instruct-rule .pl-instruct-title').textContent = 'Rule proposal';
        ruleNodes.forEach((node) => node.classList.remove('pl-instruct-on'));
        status.textContent = ''; status.classList.remove('pl-instruct-ok');
        readButton.hidden = !selected; readButton.disabled = false;
        playButton.disabled = false;
        playButton.classList.toggle('pl-instruct-primary', !selected);
        inputStatus.textContent = selected ? `${selected.example ? 'Recorded example' : 'Recorded reading'} · select Read with StowLM Reader to view the proposal.` : sentenceKey(sentence) ? 'No recorded reading for this sentence in the demo' : 'Choose a recorded wording, or play the radio call.';
        root.querySelectorAll('[data-suggestion]').forEach((button) => button.setAttribute('aria-pressed', String(Boolean(selected && sentenceKey(suggestions[Number(button.dataset.suggestion)].sentence) === sentenceKey(sentence)))));
        if (invalidated) ctx.emit('rule:changed', {sentence, known: Boolean(selected), bay: Number(rule.bay)});
      }

      function loadEvidence() {
        if (!evidenceReady) evidenceReady = ctx.data('evidence').then((value) => { evidenceCards = value.cards; return value; });
        return evidenceReady;
      }
      function start() {
        if (!started) { started = true; $('.pl-instruct-radio').hidden = confirmed; }
        return loadEvidence();
      }
      function showProgress(fraction) {
        wave.forEach((node, i) => node.classList.toggle('pl-instruct-past', i / wave.length <= fraction));
        wordNodes.forEach((node, i) => node.classList.toggle('pl-instruct-on', fraction >= wordAt[i]));
        $('.pl-instruct-clock').textContent = `${(duration * fraction).toFixed(1)} / ${duration.toFixed(2)} s`;
      }
      // The transcript reaches the field as it is heard, a few letters at a time, not in one jump at the end.
      function heardSoFar(fraction) {
        return words.map((word, i) => {
          const to = i + 1 < words.length ? wordAt[i + 1] : 1, share = to > wordAt[i] ? (fraction - wordAt[i]) / (to - wordAt[i]) : Number(fraction >= wordAt[i]);
          return word.heard.slice(0, Math.round(Math.min(1, Math.max(0, share)) * word.heard.length));
        }).filter(Boolean).join(' ');
      }
      function finishVoice() {
        showProgress(1);
        $('.pl-instruct-heard').textContent = voice.heard_line || '';
        selectSentence(voice.transcript, true);
        playButton.disabled = false; playButton.textContent = 'Replay';
        playButton.classList.remove('pl-instruct-primary');
      }
      async function playVoice() {
        if (playButton.disabled) return;
        const run = epoch, replay = phase === 'read' || phase === 'confirmed' || phase === 'reading';
        if (!replay) phase = 'playing';
        playButton.disabled = true; $('.pl-instruct-transcription').hidden = false;
        ctx.log({who: 'Muse Voice Transcribe', where: 'cloud', text: `Recorded transcript · ${voice.transcript}`, label: reading.label});
        if (voice.audio) {
          audio = audio || new Audio(voice.audio); audio.currentTime = 0;
          audio.play().catch(() => { if (active) ctx.toast('Audio playback is unavailable; the recorded transcript is shown.', 'warn'); });
        }
        for (let i = 0; i <= 40; i++) {
          if (!active || run !== epoch) return;
          showProgress(i / 40);
          if (!replay) input.value = heardSoFar(i / 40);
          if (i < 40) await ctx.wait(duration * 1000 / 40);
        }
        if (replay) playButton.disabled = false; else finishVoice();
      }
      function finishReading() {
        phase = 'read'; ruleNodes.forEach((node) => node.classList.add('pl-instruct-on'));
        const card = evidenceCards.find((item) => item.id === 'reader');
        if (card) {
          const rows = card.series.filter((row) => /adopted.*reader|without fine-tune/i.test(row.label));
          const summary = $('.pl-instruct-reader-summary');
          summary.innerHTML = `<p class="pl-instruct-meta">${esc(card.title)} · ${rows.map((row) => `${esc(row.label)}: ${esc(row.value)} of ${esc(row.n)}`).join(' · ')}</p><p class="pl-instruct-meta">${esc(card.does_not)}</p>`;
          summary.hidden = false;
        }
        $('.pl-instruct-actions').hidden = false; confirmButton.hidden = false;
      }
      async function readRule() {
        if (phase !== 'transcribed' || !selected) return;
        phase = 'reading'; readButton.hidden = true;
        const run = epoch;
        await loadEvidence();
        if (!active || run !== epoch) return;
        $('.pl-instruct-selected-label').textContent = selected.example ? 'Recorded example · same stored rule for this call' : reading.label;
        $('.pl-instruct-selected-sentence').textContent = selected.sentence;
        if (timing) $('.pl-instruct-timing').textContent = `${selected.example ? 'Reader timing reference' : 'Reading time'} · ${timing}`;
        $('.pl-instruct-rule').hidden = false;
        ctx.log({who: 'StowLM Reader', where: reading.reader.on_device ? 'on this laptop' : 'cloud', text: `Recorded rule proposal · ${reading.rule}${timing ? ` · ${timing}` : ''}`, label: reading.label});
        for (let i = 0; i < ruleNodes.length; i++) {
          await ctx.wait(1200 / Math.max(1, ruleNodes.length));
          if (!active || run !== epoch) return;
          ruleNodes[i].classList.add('pl-instruct-on');
        }
        finishReading(); ctx.emit('rule:read', {...reading, selected_sentence: selected.sentence});
      }
      function readerEvidenceHTML() {
        const card = evidenceCards.find((item) => item.id === 'reader');
        if (!card) return '';
        const tuned = card.series.find((row) => /adopted.*reader/i.test(row.label));
        const untuned = card.series.find((row) => /without fine-tune/i.test(row.label));
        return `<section class="pl-instruct-card pl-instruct-reader-evidence"><h3 class="pl-instruct-title">${esc(card.title)}</h3><p class="pl-instruct-meta">${esc(card.label)}</p><dl class="pl-instruct-facts">${[[untuned, 'without'], [tuned, 'with']].filter(([row]) => row).map(([row, key]) => `<dt>${esc(row.label)}</dt><dd>${esc(row.value)} of ${esc(row.n)}${reading.bars && reading.bars.shown && reading.bars.shown[key] ? ` · ${esc(reading.bars.shown[key])}` : ''}</dd>`).join('')}</dl><p class="pl-instruct-meta">${esc(card.does_not)}</p>${card.series.filter((row) => /spark|parser/i.test(row.label)).map((row) => `<p class="pl-instruct-meta">${esc(row.label)}${/spark/i.test(row.label) ? ' · cloud · earlier mask, fixed reference' : ''}: ${esc(row.value)} of ${esc(row.n)}</p>`).join('')}</section>`;
      }
      function maskEvidenceHTML() {
        const card = evidenceCards.find((item) => item.id === 'mask');
        if (!card) return '';
        return `<section class="pl-instruct-card pl-instruct-mask-evidence"><h3 class="pl-instruct-title">${esc(card.title)}</h3><p class="pl-instruct-meta">${esc(card.label)}</p><dl class="pl-instruct-facts">${card.series.map((row) => `<dt>${esc(row.label)}</dt><dd>${esc(row.value)} of ${esc(row.n)}</dd>`).join('')}</dl><p class="pl-instruct-meta">${esc(card.show)}</p>${card.does_not && !card.show.includes(card.does_not) ? `<p class="pl-instruct-meta">${esc(card.does_not)}</p>` : ''}</section>`;
      }
      function closeSheet() {
        if (player) { player.pause(); stylePlayer(); }
        if (sheetKind) ctx.sheet(null);
        sheetKind = null;
      }
      function sheetNode(title, contents, kind) {
        const node = document.createElement('section'); node.className = `pl-instruct-sheet pl-instruct-${kind}`;
        node.setAttribute('aria-label', title);
        node.innerHTML = `<header class="pl-instruct-sheet-head"><h2>${esc(title)}</h2><button type="button" class="pl-instruct-button" data-action="close">Back to bay plan</button></header>${contents}`;
        node.querySelector('[data-action="close"]').addEventListener('click', closeSheet);
        return node;
      }
      function openDetails() {
        closeSheet();
        if (!details) details = sheetNode('Reader details', `<p class="pl-instruct-meta">${esc(reading.reader.label)} · ${esc(reading.label || '')}</p><dl class="pl-instruct-facts"><dt>Selected recorded sentence</dt><dd>${esc(selected ? selected.sentence : reading.sentence)}</dd><dt>Confirmation sentence</dt><dd>${esc(reading.sentence)}</dd>${reading.evidence && reading.evidence.adapter_sha256 ? `<dt>Adapter SHA-256</dt><dd class="pl-instruct-code">${esc(reading.evidence.adapter_sha256)}</dd>` : ''}${reading.evidence && reading.evidence.model ? `<dt>Model</dt><dd>${esc(reading.evidence.model)}</dd>` : ''}${timing ? `<dt>Reader timing reference</dt><dd>${esc(timing)}</dd>` : ''}</dl><p class="pl-instruct-meta pl-instruct-confirmation-scope">Confirms the stored rule for this call using “${esc(reading.sentence)}”.</p><p class="pl-instruct-meta pl-instruct-scope">${esc(reading.scope_line)}</p>${readerEvidenceHTML()}<section class="pl-instruct-card pl-instruct-recording-details"><h3 class="pl-instruct-title">Radio call</h3><p class="pl-instruct-meta">${esc(voice.label)}</p><p class="pl-instruct-meta">${esc(voice.heard_by)}</p><p>${esc(voice.transcript)}</p>${voice.role !== 'hero' && voice.note ? `<p class="pl-instruct-meta">Separate demos · ${esc(voice.note)}</p>` : ''}</section><pre class="pl-instruct-code">${esc(JSON.stringify(reading.answer, null, 2))}</pre>`, 'details');
        sheetKind = 'details'; ctx.sheet(details);
      }
      function playerScript() {
        if (window.StowLMXray) return Promise.resolve();
        return new Promise((resolve, reject) => { const script = document.createElement('script'); script.src = xray.player_js; script.onload = resolve; script.onerror = () => reject(new Error('The reader x-ray could not be opened.')); document.head.appendChild(script); });
      }
      function stylePlayer() {
        const host = inside && inside.querySelector('.pl-instruct-player');
        if (!host) return;
        const classes = (selector, ...names) => host.querySelectorAll(selector).forEach((node) => node.classList.add(...names));
        classes('.xray-panel, .xray-chosen-box', 'pl-instruct-card'); classes('h1, h2', 'pl-instruct-title');
        classes('.xray-kicker, .xray-count, .xray-context, .xray-validation, .xray-probability-note, .xray-legend, .xray-footer, .xray-rank, .xray-probability, .xray-decision, .xray-reason, .xray-source', 'pl-instruct-meta');
        classes('.xray-json, .xray-chosen-token, .xray-wish-token', 'pl-instruct-code'); classes('.xray-new-token', 'pl-instruct-new-token');
        classes('button, select', 'pl-instruct-button'); classes('.xray-play', 'pl-instruct-primary');
        const say = (selector, text) => { const node = host.querySelector(selector); if (node && node.textContent !== text) node.textContent = text; };
        say('.xray-brand .xray-kicker', 'StowLM Reader · token choices'); say('.xray-brand h1', 'Token choices and schema mask');
        say('.xray-sentence .xray-kicker', 'Sentence'); say('.xray-chosen-box .xray-kicker', 'Token chosen after the mask'); say('.xray-source', xray.caption_label || xray.source_label);
        host.querySelectorAll('.xray-count, .xray-reason, .xray-decision, .xray-legend-blocked, .xray-empty').forEach((node) => {
          const text = node.textContent.replace(/\bTOKEN\b/g, 'Token').replace(/BEFORE MASK/g, 'Before mask').replace(/Top wish blocked:/gi, "Mask overrode the model's first choice:").replace(/blocked by mask/gi, 'Not allowed by mask').replace(/blocked tokens/gi, 'tokens not allowed by mask').replace(/\bblocked\b/gi, 'Not allowed by mask').replace(/\bCHOSEN\b/g, 'Chosen');
          if (text !== node.textContent) node.textContent = text;
        });
        const position = player && typeof player.getState === 'function' ? player.getState().step : 0;
        const row = position ? xray.trace.steps[position - 1] : null;
        say('.xray-wishes-panel .xray-count', !row ? 'Before mask' : row.is_stop ? 'Complete · stop token permitted' : row.top_k[0] && row.top_k[0].blocked ? "Mask overrode the model's first choice" : "The model's first choice was allowed");
        host.querySelectorAll('.xray-block-button').forEach((node) => node.remove());
      }
      async function openInside() {
        const run = epoch;
        await loadEvidence(); if (!active || run !== epoch) return;
        closeSheet();
        if (!inside) inside = sheetNode('Look inside the reader', `<p class="pl-instruct-meta">${esc(xray.caption_label || xray.source_label)}</p><div class="pl-instruct-evidence-grid">${readerEvidenceHTML()}${maskEvidenceHTML()}</div><p class="pl-instruct-meta">${xray.real ? `In this recorded reading the mask overrode the model's first choice on ${xray.blocked} of ${xray.tokens} next words.` : 'A scripted example: no model ran.'} The mask keeps the form legal. Legal can still be wrong, so a person confirms.</p><div class="pl-instruct-player"></div>`, 'inside');
        sheetKind = 'inside'; ctx.sheet(inside);
        ctx.log({who: 'StowLM Reader', where: 'on this laptop', text: 'Inspect recorded token choices and schema mask', label: xray.source_label});
        if (!playerReady) playerReady = playerScript().then(() => {
          const host = inside.querySelector('.pl-instruct-player');
          player = window.StowLMXray.mount(host, xray.trace, {initialStep: 0, speed: 6, autoplay: false, fit: false});
          stylePlayer();
          if (typeof MutationObserver === 'function') new MutationObserver(stylePlayer).observe(host, {childList: true, subtree: true});
          ['click', 'input', 'keydown', 'change'].forEach((event) => host.addEventListener(event, () => Promise.resolve().then(stylePlayer)));
          return player;
        }).catch((error) => { playerReady = null; ctx.toast(error.message, 'warn'); return null; });
        await playerReady;
        if (!active || run !== epoch || sheetKind !== 'inside' || !player) return;
        const override = xray.trace.steps.findIndex((step) => step.top_k[0] && step.top_k[0].blocked && !step.is_stop) + 1;
        player.seek(override || 1); stylePlayer();
      }
      async function confirmRule() {
        if (phase !== 'read' || !selected || posting || confirmed) return;
        closeSheet(); posting = true; confirmButton.disabled = true;
        const run = epoch;
        try {
          const response = await ctx.post('confirm', reading.binding || {});
          if (run !== epoch) return;
          if (!response || !response.token) throw new Error((response && response.error) || 'The server did not record this confirmation.');
          ctx.state.confirm = response; confirmed = true; phase = 'confirmed';
          const bay = Number(rule.bay);
          ctx.emit('rule:confirmed', {...response, bay});
          if (rule.type === 'close_bay' && Number.isFinite(bay)) { ctx.bay.closeBay(bay, `Bay ${bay} · closed`); ctx.bay.focusBay(bay); }
          $('.pl-instruct-rule .pl-instruct-title').textContent = 'Confirmed rule';
          compactConfirmed(true);
          confirmButton.textContent = 'Rule confirmed'; status.textContent = 'Confirmed for this call.'; status.classList.add('pl-instruct-ok');
          ctx.log({who: 'Planner', where: null, text: `Confirmed · ${reading.rule}`, label: 'Confirmed on screen'});
          ctx.done();
        } catch (error) { if (run !== epoch) return; status.textContent = error.message || 'The server did not record this confirmation.'; confirmButton.disabled = false; }
        finally { posting = false; }
      }
      playButton.addEventListener('click', () => { active = true; action = playVoice(); }); readButton.addEventListener('click', () => { active = true; action = readRule(); });
      input.addEventListener('input', () => selectSentence(input.value));
      editButton.addEventListener('click', () => { selectSentence(input.value); input.focus(); });
      root.querySelectorAll('[data-suggestion]').forEach((button) => button.addEventListener('click', () => selectSentence(suggestions[Number(button.dataset.suggestion)].sentence)));
      insideButton.addEventListener('click', () => { active = true; action = openInside(); }); detailsButton.addEventListener('click', () => { active = true; openDetails(); });
      confirmButton.addEventListener('click', () => { active = true; action = confirmRule(); });
      return {
        start,
        enter() { active = true; if (phase === 'playing') finishVoice(); if (phase === 'reading') { $('.pl-instruct-rule').hidden = false; finishReading(); } if (confirmed) ctx.done(); },
        leave() { active = false; epoch += 1; if (audio) audio.pause(); playButton.disabled = false; closeSheet(); },
        async autoplay(auto) {
          await start(); const run = epoch;
          await auto.wait(1100); if (!active || run !== epoch || confirmed) return;
          if (phase === 'radio') { await auto.click(playButton); await action; }
          else if (phase === 'playing') await action;
          if (!active || run !== epoch || !selected) return;
          await auto.wait(1600); if (!active || run !== epoch) return;
          if (phase === 'transcribed') { await auto.click(readButton); await action; }
          else if (phase === 'reading') await action;
          if (phase !== 'read') return;
          await auto.wait(2400); if (!active || run !== epoch) return;
          await auto.click(insideButton); await action;
          await auto.wait(5600); if (!active || run !== epoch) return;
          if (sheetKind) await auto.click(inside.querySelector('[data-action="close"]'));
          await auto.wait(1200); if (!active || run !== epoch) return;
          await auto.click(confirmButton); await action; await auto.wait(1200);
        },
      };
    },
  });
})();
