/* Live instruction input. All proposals and transcripts come from this call. */
(function () {
  'use strict';
  const Planner = window.Planner;
  if (!Planner) return;

  function wavBytes(chunks, sourceRate) {
    const length = chunks.reduce((n, chunk) => n + chunk.length, 0);
    const samples = new Float32Array(length);
    let offset = 0;
    chunks.forEach((chunk) => { samples.set(chunk, offset); offset += chunk.length; });
    const count = Math.min(960000, Math.floor(length * 16000 / sourceRate));
    const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
    const word = (at, value) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
    word(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); word(8, 'WAVE');
    word(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); word(36, 'data'); view.setUint32(40, count * 2, true);
    for (let i = 0; i < count; i++) {
      const start = Math.floor(i * sourceRate / 16000), end = Math.max(start + 1, Math.floor((i + 1) * sourceRate / 16000));
      let sum = 0;
      for (let j = start; j < Math.min(end, length); j++) sum += samples[j];
      const value = Math.max(-1, Math.min(1, sum / Math.max(1, Math.min(end, length) - start)));
      view.setInt16(44 + i * 2, value < 0 ? value * 32768 : value * 32767, true);
    }
    return new Uint8Array(buffer);
  }

  function base64(bytes) {
    let text = '';
    for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    return btoa(text);
  }

  function checkWav(bytes) {
    const text = (at, n) => String.fromCharCode.apply(null, bytes.subarray(at, at + n));
    if (bytes.length < 44 || text(0, 4) !== 'RIFF' || text(8, 4) !== 'WAVE') throw new Error('Choose a WAV audio file.');
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let rate = 0, data = 0;
    for (let at = 12; at + 8 <= bytes.length;) {
      const size = view.getUint32(at + 4, true), name = text(at, 4);
      if (at + 8 + size > bytes.length) throw new Error('This WAV file is incomplete.');
      if (name === 'fmt ' && size >= 16) rate = view.getUint32(at + 16, true);
      if (name === 'data') data += size;
      at += 8 + size + (size % 2);
    }
    if (!rate || !data) throw new Error('This WAV file has no readable audio.');
    if (data / rate > 60) throw new Error('Choose audio of at most 60 seconds.');
  }

  Planner.stage({
    // The call view carries these inputs; asking the shell for demo feature payloads prevents mounting.
    id: 'instruct', mode: 'live', order: 20, title: 'Instructions', features: [],
    mount(panel, data, ctx) {
      const esc = ctx.esc;
      panel.innerHTML = `<div class="pl-instruct-root pl-live-instruct">
        <section class="pl-instruct-card pl-instruct-inputs">
          <label class="pl-instruct-title" for="pl-live-instruct-sentence">Type an instruction</label>
          <textarea id="pl-live-instruct-sentence" class="pl-instruct-input" data-live-sentence rows="3" maxlength="20000" placeholder="Type a sentence for this call"></textarea>
          <p class="pl-instruct-meta" data-live-transcript-label hidden>Editable transcript</p>
          <p class="pl-instruct-meta" data-live-reader-identity>StowLM Reader · fine-tuned Muse Glimmer 30B (LoRA) on the recording laptop, schema-masked decoding</p>
          <p class="pl-instruct-meta">The StowLM Reader reads four kinds of instruction: close a bay, cap the tiers, keep dangerous goods apart, one port per hatch. It's built to refuse anything else; it can slip, so the planner confirms.</p>
          <button type="button" class="pl-instruct-button pl-instruct-primary pl-instruct-read" data-action="read">Read with StowLM Reader</button>
        </section>
        <section class="pl-instruct-card pl-instruct-radio">
          <h3 class="pl-instruct-title">Speak an instruction</h3>
          <p class="pl-instruct-meta">Muse Voice Transcribe · Meta Model API, cloud</p>
          <p class="pl-instruct-meta">Record up to 60 seconds, or upload a WAV. The received transcript will appear above for editing.</p>
          <div class="pl-instruct-actions"><button type="button" class="pl-instruct-button" data-action="record">Record in this browser</button><button type="button" class="pl-instruct-button" data-action="stop-recording" hidden>Stop recording</button><span class="pl-instruct-meta pl-instruct-clock" data-live-recording-time hidden></span></div>
          <label class="pl-instruct-meta" for="pl-live-instruct-audio">Upload a WAV</label>
          <input id="pl-live-instruct-audio" data-live-audio type="file" accept=".wav,audio/wav,audio/x-wav">
          <label class="pl-instruct-meta pl-live-instruct-voice-option" data-live-generated-voice-option hidden for="pl-live-instruct-generated-voice"><input id="pl-live-instruct-generated-voice" data-live-generated-voice type="checkbox"> Generated (synthetic) voice</label>
          <p class="pl-instruct-meta" data-live-audio-label hidden></p>
          <button type="button" class="pl-instruct-button" data-action="transcribe" disabled>Transcribe with Muse Voice</button>
          <p class="pl-instruct-meta" data-live-voice-key-status role="status" aria-live="polite"></p>
          <button type="button" class="pl-instruct-button" data-action="open-key-panel">Set up Meta Model API access</button>
          <p class="pl-instruct-meta" data-live-voice-status role="status" aria-live="polite"></p>
        </section>
        <div data-live-reading></div>
        <p class="pl-instruct-meta" data-live-reader-status role="status" aria-live="polite"></p>
        <button type="button" class="pl-instruct-button pl-live-instruct-none" data-action="no-instruction">Plan with no instruction</button>
        <p class="pl-instruct-status" data-live-instruct-status role="status" aria-live="polite"></p>
      </div>`;
      const $ = (selector) => panel.querySelector(selector);
      const input = $('[data-live-sentence]'), audioInput = $('[data-live-audio]');
      const readingHost = $('[data-live-reading]'), status = $('[data-live-instruct-status]');
      let call = null, callId = null, voiceKey = null, ignoredVoiceKey = null, ignoredReadingId = null, readingKey = null, reading = null;
      let sentenceOrigin = 'typed', audio = null, recorder = null, recordingTimer = null;
      let posting = false, recordingPending = false, recordingGeneration = 0, revision = 0, noInstruction = false, emittedReading = null, requestError = null;
      let keyStatus = null, statusAvailability = null, statusError = '', statusVersion = 0, checkingVoice = false;

      const errorText = (error) => error && error.message ? error.message : String(error);
      const setStatus = (text) => { status.textContent = text; if (ctx.status) ctx.status(text); };
      const stages = (name) => (call && call.stages || []).filter((stage) => stage.stage === name);
      const latestStage = (name) => { const rows = stages(name); return rows[rows.length - 1]; };
      function paidCallReason() {
        if (statusError) return `Could not verify Meta Model API key and paid-call consent: ${statusError}. No audio was sent.`;
        if (!keyStatus) return 'Checking Meta Model API key and paid-call consent. No audio will be sent until both are confirmed.';
        if (keyStatus.present !== true) return 'No Meta Model API key: enter yours in the app, or put MODEL_API_KEY in .env (see .env.example).';
        if (keyStatus.paid_calls_accepted !== true) return 'Paid calls are not accepted. Accept paid calls in the app before sending audio to Muse Voice Transcribe.';
        return '';
      }
      function acceptStatus(next) {
        statusVersion += 1; statusError = '';
        const key = next && next.key;
        // Keep only the two permission flags, never key material or form values.
        keyStatus = key ? {present: key.present === true, paid_calls_accepted: key.paid_calls_accepted === true} : null;
        statusAvailability = next && next.availability || null;
        renderButtons();
        renderReaderIdentity(); renderReading();
        $('[data-live-voice-status]').textContent = unavailable('voice', 'Muse Voice Transcribe') || stageText('voice', 'Muse Voice Transcribe');
      }
      async function refreshStatus() {
        const requestedVersion = ++statusVersion;
        try {
          const next = await ctx.fx.get('status');
          if (requestedVersion === statusVersion) acceptStatus(next);
        } catch (error) {
          if (requestedVersion === statusVersion) { keyStatus = null; statusError = errorText(error).replace(/[.\s]+$/, ''); renderButtons(); }
        }
        return !paidCallReason();
      }
      function unavailable(name, who) {
        const availability = statusAvailability && statusAvailability[name] || call && call.availability && call.availability[name];
        const stage = latestStage(name);
        const voiceAccessReady = name === 'voice' && statusAvailability && statusAvailability.voice && statusAvailability.voice.available === true && keyStatus && keyStatus.present === true && keyStatus.paid_calls_accepted === true && !statusError;
        const correctedAccessReason = (reason) => voiceAccessReady && typeof reason === 'string'
          && !/cap|spend|budget|ledger|credit limit|quota|rate.limit/i.test(reason)
          && /MODEL_API_KEY|Meta Model API key|paid.calls|SPARK_CREDIT_READY|STOWLM_ACCEPT_PAID_CALLS/i.test(reason);
        // Current status can clear an old credential failure, never a runtime or spend-limit failure.
        const requestReason = requestError && requestError.stage === name && !correctedAccessReason(requestError.reason) ? requestError.reason : null;
        const stageReason = stage && stage.status === 'unavailable' && !correctedAccessReason(stage.notes) ? stage.notes : null;
        const reason = requestReason || stageReason || (availability && availability.available === false ? availability.reason : null);
        return reason ? `${who} unavailable: ${String(reason).replace(/[.\s]+$/, '')}. Nothing was replayed in its place.` : '';
      }
      function stageText(name, who) {
        const stage = latestStage(name);
        if (stage && stage.status === 'running') return `${who} is working. No answer has been received yet.`;
        if (stage && stage.status === 'failed') return stage.notes || `${who} could not complete this request. Nothing was replayed in its place.`;
        return '';
      }
      const modelBusy = () => ['voice', 'reader'].some((name) => { const stage = latestStage(name); return stage && stage.status === 'running'; });
      const planning = (value) => !!value && (value.state === 'planning' || value.run && value.run.status === 'running');
      function instructionState(value) {
        if (!value || planning(value)) return false;
        if (['ready', 'done'].includes(value.state)) return true;
        return ['failed', 'checked_plan', 'no_checked_plan', 'relaxed', 'relaxed_plan', 'stopped'].includes(value.state) && !!value.run;
      }
      function audioState(value) { return !!value && !planning(value) && (['needs_papers', 'needs_answers'].includes(value.state) || instructionState(value)); }
      function canRead() { return instructionState(call) && !posting && !modelBusy() && !unavailable('reader', 'StowLM Reader'); }
      function renderButtons() {
        $('[data-action="read"]').disabled = !canRead() || !input.value.trim();
        $('[data-action="no-instruction"]').disabled = !instructionState(call) || posting || modelBusy();
        const paidReason = paidCallReason();
        $('[data-action="transcribe"]').disabled = !!paidReason || checkingVoice || !audio || posting || !audioState(call) || !!unavailable('voice', 'Muse Voice Transcribe') || modelBusy();
        $('[data-live-voice-key-status]').textContent = paidReason;
        $('[data-action="open-key-panel"]').hidden = !paidReason;
        $('[data-action="record"]').disabled = !!recorder || recordingPending || posting || !call || planning(call);
        input.disabled = planning(call); audioInput.disabled = planning(call); $('[data-live-generated-voice]').disabled = planning(call);
        const confirm = $('[data-action="confirm"]');
        if (confirm) confirm.disabled = !canRead() || !reading || reading.confirmed || input.value !== reading.sentence || !reading.sentence_sha256 || !reading.rule_sha256;
      }
      function traceFor(value) { return value && (value.trace || value.evidence && (value.evidence.trace || value.evidence.decode_trace)); }
      function readerIdentity(value) {
        const info = statusAvailability && statusAvailability.reader || call && call.availability && call.availability.reader || {};
        const evidence = value && value.evidence || {};
        if (info.adopted === false) return `Read by: a Muse Glimmer 30B LoRA adapter that is not the adopted StowLM Reader (adapter ${evidence.adapter_sha256 || info.adapter_sha256 || 'not reported'})`;
        return 'StowLM Reader · fine-tuned Muse Glimmer 30B (LoRA) on the recording laptop, schema-masked decoding';
      }
      function renderReaderIdentity() { $('[data-live-reader-identity]').textContent = readerIdentity(null); }
      function audioOriginLabel(origin, generated) { return origin === 'browser' ? 'Recorded in this browser' : `Uploaded audio${generated === true ? ' · Generated voice' : ''}`; }
      function renderReading() {
        const match = reading && input.value === reading.sentence && !noInstruction;
        if (!match) { readingHost.innerHTML = ''; renderButtons(); return; }
        const evidence = reading.evidence || {}, trace = traceFor(reading);
        const wallSeconds = reading.wall_s == null ? evidence.wall_s : reading.wall_s;
        const coldSeconds = reading.cold_load_s == null ? evidence.cold_load_s : reading.cold_load_s;
        const seconds = wallSeconds == null ? '' : `Reading · ${Number(wallSeconds).toFixed(2)} s`;
        const cold = coldSeconds == null ? '' : `Cold load · ${Number(coldSeconds).toFixed(2)} s`;
        const adapter = evidence.adapter_sha256 ? `Adapter · ${String(evidence.adapter_sha256).slice(0, 12)}` : '';
        const refusal = reading.refusal && (reading.refusal.reason || reading.refusal.code) || 'The sentence did not produce a valid rule';
        const ready = reading.status === 'constraint_ready';
        const refused = reading.status === 'refused';
        const badge = ready ? 'Ready to confirm' : refused ? 'Refused' : 'Not a valid rule';
        const ruleDescription = ready ? reading.rule_text || 'No plain-language rule was returned; inspect the reading before continuing.'
          : refused ? `StowLM Reader refused to make a rule: ${String(refusal).replace(/[.\s]+$/, '')}. Nothing was planned from this sentence.`
            : "The StowLM Reader's answer didn't validate against the rule schema, so no rule was made. Nothing was planned from this sentence.";
        const sizeClass = call && (call.case && call.case.size_class || call.request && call.request.size_class);
        const smallBayClosure = ready && reading.confirmed && ['tiny', 'small'].includes(sizeClass) && reading.answer && reading.answer.constraint && reading.answer.constraint.type === 'close_bay';
        readingHost.innerHTML = `<section class="pl-instruct-card pl-instruct-rule" data-live-reading-card>
          <div class="pl-instruct-card-head"><h3 class="pl-instruct-title">${reading.confirmed ? 'Confirmed reading' : 'Reader proposal'}</h3><span class="pl-instruct-badge">${esc(badge)}</span></div>
          <p class="pl-instruct-meta" data-live-reading-identity>${esc(readerIdentity(reading))}</p>
          <p class="pl-instruct-selected-sentence">${esc(reading.sentence || '')}</p>
          <p class="pl-instruct-rule-plain">${esc(ruleDescription)}</p>
          <p class="pl-instruct-meta">${esc([adapter, seconds, cold].filter(Boolean).join(' · '))}</p>
          ${seconds || cold ? '<p class="pl-instruct-meta">Measured on the recording machine, in this recorded run.</p>' : ''}
          <p class="pl-instruct-meta">built to refuse anything else; it can slip, so the planner confirms</p>
          ${smallBayClosure ? '<p class="pl-instruct-meta" data-live-small-bay-closure>On tiny and small sections a bay closure usually leaves no room for the load: an artefact of the section size, not a crane limit.</p>' : ''}
          <div class="pl-instruct-actions">${trace ? '<button type="button" class="pl-instruct-button" data-action="inside">Look inside</button>' : ''}${ready ? `<button type="button" class="pl-instruct-button pl-instruct-primary" data-action="confirm">${reading.confirmed ? 'Reading confirmed' : 'Confirm this reading'}</button>` : ''}</div>
        </section>`;
        const confirm = $('[data-action="confirm"]');
        if (confirm) confirm.addEventListener('click', confirmReading);
        const inside = $('[data-action="inside"]');
        if (inside) inside.addEventListener('click', () => {
          const node = document.createElement('section');
          node.className = 'pl-instruct-sheet pl-live-instruct-evidence';
          node.innerHTML = `<div class="pl-instruct-sheet-head"><h2>Live reader trace</h2><button type="button" class="pl-instruct-button" data-action="close">Close</button></div><p class="pl-instruct-meta">Only evidence carried by this reading.</p><pre class="pl-instruct-code">${esc(JSON.stringify(trace, null, 2))}</pre>`;
          node.querySelector('[data-action="close"]').addEventListener('click', () => ctx.sheet(null));
          ctx.sheet(node, 'Live reader trace');
        });
        renderButtons();
      }
      function invalidate() {
        revision += 1; noInstruction = false;
        if (ctx.state) delete ctx.state.confirm;
        ctx.emit('rule:changed', {sentence: input.value});
      }
      const voiceFingerprint = (voice) => voice && [voice.audio_sha256, voice.transcript, voice.origin, voice.generated_voice === true].join(':');
      function receive(next) {
        if (!next) return;
        const restoredCall = next.id !== callId;
        if (restoredCall) {
          stopRecording(false); revision += 1; callId = next.id; voiceKey = null; ignoredVoiceKey = null; ignoredReadingId = null; readingKey = null; emittedReading = null;
          reading = null; audio = null; input.value = ''; audioInput.value = ''; sentenceOrigin = 'typed'; noInstruction = false; requestError = null;
          $('[data-live-generated-voice]').checked = false; $('[data-live-generated-voice-option]').hidden = true;
          $('[data-live-audio-label]').hidden = true; $('[data-live-transcript-label]').hidden = true;
          setStatus(''); if (ctx.state) delete ctx.state.confirm;
        }
        call = next;
        if (planning(call) && (recorder || recordingPending)) stopRecording(false);
        const voice = call.voice;
        const nextVoiceKey = voiceFingerprint(voice);
        if (voice && typeof voice.transcript === 'string' && nextVoiceKey !== voiceKey && nextVoiceKey !== ignoredVoiceKey) {
          ignoredVoiceKey = null;
          voiceKey = nextVoiceKey; input.value = voice.transcript; sentenceOrigin = 'voice'; invalidate();
          $('[data-live-transcript-label]').hidden = false;
          $('[data-live-transcript-label]').textContent = `Editable transcript · ${audioOriginLabel(voice.origin, voice.generated_voice)}`;
        }
        const latest = (call.readings || []).slice(-1)[0] || null;
        const nextReadingKey = latest && [latest.id, latest.sentence_sha256, latest.rule_sha256, latest.confirmed].join(':');
        if (latest && latest.id !== ignoredReadingId && nextReadingKey !== readingKey) {
          ignoredReadingId = null;
          readingKey = nextReadingKey; reading = latest;
          if (!input.value && !noInstruction) { input.value = latest.sentence || ''; sentenceOrigin = latest.origin || 'typed'; }
          if (emittedReading !== latest.id) { ctx.emit('rule:read', latest); emittedReading = latest.id; }
        }
        // A refresh may restore an existing server confirmation, never grant a new one.
        if (restoredCall && latest && latest.confirmed && latest.status === 'constraint_ready' && input.value === latest.sentence && latest.sentence_sha256 && latest.rule_sha256) {
          if (ctx.state) ctx.state.confirm = latest;
          ctx.emit('rule:confirmed', latest);
          setStatus('Reading confirmed for this call.'); if (ctx.done) ctx.done();
        }
        $('[data-live-reader-status]').textContent = unavailable('reader', 'StowLM Reader') || stageText('reader', 'StowLM Reader') || (!call || instructionState(call) ? '' : planning(call) ? 'Wait for this engine run to finish before changing its instruction.' : 'Read the cargo into a load list before reading an instruction.');
        $('[data-live-voice-status]').textContent = unavailable('voice', 'Muse Voice Transcribe') || stageText('voice', 'Muse Voice Transcribe');
        renderReaderIdentity(); renderReading();
      }
      function attemptNumber(value) {
        const run = value && value.run || {};
        return run.attempt != null ? Number(run.attempt) : run.n != null ? Number(run.n) : value && Array.isArray(value.attempts) && value.attempts.length ? value.attempts.length : value && value.run ? 1 : null;
      }
      function acceptRunEvent(event, chooseInstruction) {
        if (!event || !callId) return;
        const supplied = event.call, suppliedId = typeof supplied === 'string' ? supplied : supplied && supplied.id;
        if (suppliedId !== callId) return;
        const snapshot = ctx.call && ctx.call.id === callId ? ctx.call : call;
        // ctx.call may already hold the completed view before this module receives call:update.
        if (planning(snapshot)) return;
        const currentAttempt = Math.max(attemptNumber(snapshot) || 0, attemptNumber(call) || 0);
        if (event.attempt != null && currentAttempt && Number(event.attempt) !== currentAttempt) return;
        const next = supplied && typeof supplied === 'object' ? supplied : snapshot;
        if (!next || next.id !== callId || !next.run || !instructionState(next) || planning(next)) return;
        if (event.attempt != null && attemptNumber(next) != null && Number(event.attempt) !== attemptNumber(next)) return;
        // Receive same-call state without granting or re-emitting a confirmation.
        receive(next);
        if (!chooseInstruction || event.choose !== 'instruction' || posting || modelBusy()) return;
        stopRecording(false);
        ignoredVoiceKey = voiceFingerprint(call.voice); voiceKey = null;
        const latest = (call.readings || []).slice(-1)[0]; ignoredReadingId = latest && latest.id || null;
        reading = null; audio = null; input.value = ''; audioInput.value = ''; sentenceOrigin = 'typed';
        $('[data-live-generated-voice]').checked = false; $('[data-live-generated-voice-option]').hidden = true;
        $('[data-live-audio-label]').hidden = true; $('[data-live-transcript-label]').hidden = true;
        invalidate(); setStatus('Type or speak a new instruction, or choose Plan with no instruction.'); renderReading();
        if (input.focus) input.focus();
      }
      async function post(action, body) {
        if (!callId || posting) return null;
        const expectedId = callId, expectedRevision = revision;
        posting = true; requestError = null; status.textContent = ''; renderButtons();
        try {
          const next = await ctx.fx.post(`calls/${encodeURIComponent(callId)}/${action}`, body);
          if (callId !== expectedId) return null;
          receive(next);
          return {call: next, current: revision === expectedRevision};
        } catch (error) {
          if (callId !== expectedId) return null;
          const reason = errorText(error);
          const stage = action === 'read' ? 'reader' : action === 'audio' ? 'voice' : null;
          if (stage && (error.status === 503 || /unavailable|isn't set|not available|not installed|not found|missing/i.test(reason))) {
            requestError = {stage, reason}; receive(call);
          }
          setStatus(reason); return null;
        } finally { posting = false; renderButtons(); }
      }
      async function confirmReading() {
        if (!reading || !canRead() || input.value !== reading.sentence || reading.confirmed || !reading.sentence_sha256 || !reading.rule_sha256) return;
        const selected = reading;
        const result = await post('confirm', {reading: selected.id, sentence_sha256: selected.sentence_sha256, rule_sha256: selected.rule_sha256});
        if (!result || !result.current || input.value !== selected.sentence || !reading || reading.id !== selected.id) return;
        const confirmed = (result.call.readings || []).find((row) => row.id === selected.id && row.confirmed);
        if (!confirmed) { setStatus('The server did not record this confirmation.'); return; }
        if (ctx.state) ctx.state.confirm = confirmed;
        ctx.emit('rule:confirmed', confirmed); setStatus('Reading confirmed for this call.'); if (ctx.done) ctx.done();
      }
      function showAudio(bytes, origin, name) {
        audio = {bytes, origin, name, generated_voice: false};
        $('[data-live-generated-voice]').checked = false;
        $('[data-live-generated-voice-option]').hidden = origin !== 'upload';
        renderAudioLabel(); renderButtons();
      }
      function renderAudioLabel() {
        if (!audio) return;
        const label = $('[data-live-audio-label]'); label.hidden = false;
        label.textContent = `${audioOriginLabel(audio.origin, audio.generated_voice)} · ${audio.name}`;
      }
      function stopRecording(keep) {
        recordingGeneration += 1;
        if (recordingTimer != null) { clearInterval(recordingTimer); recordingTimer = null; }
        if (recorder) {
          const active = recorder; recorder = null;
          active.node.onaudioprocess = null; active.node.disconnect(); active.source.disconnect();
          active.stream.getTracks().forEach((track) => track.stop());
          const closed = active.context.close(); if (closed && closed.catch) closed.catch(() => {});
          if (keep && active.chunks.length) showAudio(wavBytes(active.chunks, active.context.sampleRate), 'browser', 'browser-recording.wav');
        }
        $('[data-action="stop-recording"]').hidden = true; $('[data-live-recording-time]').hidden = true;
        renderButtons();
      }
      async function startRecording() {
        if (recorder || recordingPending || !callId || planning(call)) return;
        const requestedCall = callId, requestedRevision = revision, requestedGeneration = recordingGeneration;
        let stream = null, context = null;
        recordingPending = true; renderButtons();
        try {
          const AudioContext = window.AudioContext || window.webkitAudioContext;
          if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !AudioContext) throw new Error('Browser recording is unavailable here. Upload a WAV or type an instruction.');
          stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1}, video: false});
          if (requestedCall !== callId || requestedRevision !== revision || requestedGeneration !== recordingGeneration || planning(call)) { stream.getTracks().forEach((track) => track.stop()); return; }
          context = new AudioContext(); if (context.resume) await context.resume();
          if (requestedCall !== callId || requestedRevision !== revision || requestedGeneration !== recordingGeneration || planning(call)) { stream.getTracks().forEach((track) => track.stop()); await context.close(); return; }
          const source = context.createMediaStreamSource(stream), node = context.createScriptProcessor(4096, 1, 1);
          const active = {context, stream, source, node, chunks: [], started: Date.now(), samples: 0};
          recorder = active;
          node.onaudioprocess = (event) => {
            if (recorder !== active) return;
            const channel = event.inputBuffer.getChannelData(0);
            const remaining = Math.max(0, Math.floor(context.sampleRate * 60) - active.samples);
            const chunk = channel.slice(0, remaining); active.chunks.push(chunk); active.samples += chunk.length;
            if (active.samples >= context.sampleRate * 60) stopRecording(true);
          };
          source.connect(node); node.connect(context.destination);
          $('[data-action="stop-recording"]').hidden = false; $('[data-live-recording-time]').hidden = false;
          $('[data-live-recording-time]').textContent = '0 s · Recorded in this browser';
          recordingTimer = setInterval(() => {
            if (recorder !== active) return;
            const seconds = Math.min(60, Math.floor((Date.now() - active.started) / 1000));
            $('[data-live-recording-time]').textContent = `${seconds} s · Recorded in this browser`;
            if (seconds >= 60) stopRecording(true);
          }, 250);
          renderButtons();
        } catch (error) {
          if (stream) stream.getTracks().forEach((track) => track.stop());
          if (context) { const closed = context.close(); if (closed && closed.catch) closed.catch(() => {}); }
          setStatus(errorText(error));
        } finally { recordingPending = false; renderButtons(); }
      }
      input.addEventListener('input', () => { sentenceOrigin = voiceKey ? 'planner_edit' : 'typed'; invalidate(); setStatus(''); renderReading(); });
      $('[data-action="read"]').addEventListener('click', async () => {
        if (!canRead() || !input.value.trim()) return;
        invalidate(); requestError = null; await post('read', {sentence: input.value, origin: sentenceOrigin});
      });
      $('[data-action="record"]').addEventListener('click', startRecording);
      $('[data-action="stop-recording"]').addEventListener('click', () => stopRecording(true));
      $('[data-live-generated-voice]').addEventListener('change', () => {
        if (!audio || audio.origin !== 'upload') { $('[data-live-generated-voice]').checked = false; return; }
        audio.generated_voice = $('[data-live-generated-voice]').checked === true;
        renderAudioLabel();
      });
      audioInput.addEventListener('change', async () => {
        const file = audioInput.files && audioInput.files[0], expectedCall = callId;
        if (!file) return;
        audio = null; renderButtons();
        try {
          if (file.size > 10 * 1024 * 1024) throw new Error('Choose a WAV smaller than 10 MB.');
          const bytes = new Uint8Array(await file.arrayBuffer()); checkWav(bytes);
          if (expectedCall === callId) { showAudio(bytes, 'upload', file.name); setStatus(''); }
        } catch (error) { if (expectedCall === callId) setStatus(errorText(error)); }
      });
      $('[data-action="transcribe"]').addEventListener('click', async () => {
        if (!audio || posting || checkingVoice || !callId) return;
        const selectedAudio = audio, selectedCall = callId;
        checkingVoice = true; renderButtons();
        try {
          if (!await refreshStatus() || selectedCall !== callId || selectedAudio !== audio) return;
          if (!audioState(call) || modelBusy() || unavailable('voice', 'Muse Voice Transcribe')) return;
          await post('audio', {name: selectedAudio.name, media_type: 'audio/wav', data_b64: base64(selectedAudio.bytes), origin: selectedAudio.origin, generated_voice: selectedAudio.origin === 'upload' && selectedAudio.generated_voice === true});
        } finally { checkingVoice = false; renderButtons(); }
      });
      $('[data-action="open-key-panel"]').addEventListener('click', async () => {
        try { if (ctx.openKeyPanel) await ctx.openKeyPanel(); }
        catch (error) { setStatus(errorText(error)); }
        await refreshStatus();
      });
      $('[data-action="no-instruction"]').addEventListener('click', () => {
        if (!instructionState(call) || posting || modelBusy()) return;
        invalidate(); noInstruction = true; renderReading(); ctx.emit('rule:confirmed', null);
        if (ctx.state) ctx.state.confirm = null;
        setStatus('No instruction: the reader had nothing to do'); if (ctx.done) ctx.done();
      });
      ctx.on('call:update', receive);
      ctx.on('status:update', acceptStatus);
      ctx.on('run:finished', (event) => acceptRunEvent(event, false));
      ctx.on('plan:again', (event) => acceptRunEvent(event, true));
      if (ctx.call) receive(ctx.call); else { setStatus('Create a call to add an instruction.'); renderButtons(); }
      refreshStatus();
      return {ownsAction: true, start() { if (ctx.call) receive(ctx.call); }, enter() { if (ctx.call) receive(ctx.call); return refreshStatus(); }, leave() { stopRecording(true); if (ctx.sheet) ctx.sheet(null); }};
    },
  });
})();
