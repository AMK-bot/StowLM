/* StowLM reader x-ray. No dependencies, network requests, or build step. */
(function (global) {
  'use strict';
  const VERSION = 'xray.trace/1';
  const reasons = {
    not_in_grammar: 'not in grammar',
    bay_not_on_ship: 'bay not on this ship',
    tier_over_cap: 'tier over the cap'
  };

  // Synchronous SHA-256 also works on file://, without WebCrypto or a network.
  function sha256(text) {
    const bytes = Array.from(encodeURIComponent(text).replace(/%([0-9A-F]{2})/g,
      (_, hex) => String.fromCharCode(parseInt(hex, 16))), c => c.charCodeAt(0));
    const length = bytes.length;
    bytes.push(128);
    while (bytes.length % 64 !== 56) bytes.push(0);
    const bits = length * 8;
    for (let i = 7; i >= 0; i--) bytes.push(Math.floor(bits / Math.pow(256, i)) & 255);
    const k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const rotate = (x, n) => (x >>> n) | (x << (32 - n));
    for (let offset = 0; offset < bytes.length; offset += 64) {
      const w = new Array(64);
      for (let i = 0; i < 16; i++) {
        const p = offset + i * 4;
        w[i] = (bytes[p] << 24) | (bytes[p + 1] << 16) | (bytes[p + 2] << 8) | bytes[p + 3];
      }
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        w[i] = (w[i - 16] + (rotate(a, 7) ^ rotate(a, 18) ^ (a >>> 3)) + w[i - 7]
          + (rotate(b, 17) ^ rotate(b, 19) ^ (b >>> 10))) | 0;
      }
      let [a,b,c,d,e,f,g,j] = h;
      for (let i = 0; i < 64; i++) {
        const t = (j + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25))
          + ((e & f) ^ (~e & g)) + k[i] + w[i]) | 0;
        const u = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        j = g; g = f; f = e; e = (d + t) | 0; d = c; c = b; b = a; a = (t + u) | 0;
      }
      [a,b,c,d,e,f,g,j].forEach((v, i) => { h[i] = (h[i] + v) | 0; });
    }
    return h.map(x => (x >>> 0).toString(16).padStart(8, '0')).join('');
  }

  function ordered(value) {
    if (Array.isArray(value)) return value.map(ordered);
    if (value && typeof value === 'object') {
      const result = Object.create(null);
      Object.keys(value).sort().forEach(key => { result[key] = ordered(value[key]); });
      return result;
    }
    return value;
  }

  function validateTrace(trace) {
    if (!trace || trace.format !== VERSION || !Array.isArray(trace.steps)) {
      throw new TypeError('Expected an xray.trace/1 trace');
    }
    const source = trace.source, record = trace.stage_record;
    const fail = () => { throw new TypeError('Inconsistent trace provenance or payload hash'); };
    if (!source || !record || !['fake', 'recorded'].includes(source.kind)
        || typeof source.label !== 'string' || !source.label
        || record.stage !== 'reader_xray' || source.synthetic !== true
        || record.mode !== (source.kind === 'fake' ? 'generated' : 'recorded')) fail();
    const scripted = /fake|script/i;
    if (source.kind === 'fake') {
      if (!source.label.includes('FAKE') || (record.model !== null
          && (typeof record.model !== 'string' || !scripted.test(record.model)))) fail();
    } else if (typeof record.model !== 'string' || !record.model.trim() || scripted.test(record.model)
        || scripted.test(source.label) || scripted.test(record.notes || '')
        || ![record.adapter_sha256, record.prompt_sha256, record.source_sha256]
          .every(value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value))) fail();
    const payload = Object.assign({}, trace);
    delete payload.payload_json;
    payload.stage_record = Object.assign({}, record);
    delete payload.stage_record.output_sha256;
    try {
      if (typeof trace.payload_json !== 'string'
          || JSON.stringify(ordered(JSON.parse(trace.payload_json))) !== JSON.stringify(ordered(payload))
          || sha256(trace.payload_json) !== record.output_sha256) fail();
    } catch (_) { fail(); }
    // Retain a detached snapshot so later caller edits cannot change its label.
    return JSON.parse(JSON.stringify(trace));
  }

  function mount(root, trace, options) {
    options = options || {};
    if (!root || !root.ownerDocument) throw new TypeError('An element is required');
    trace = validateTrace(trace);
    const doc = root.ownerDocument;
    const fake = trace.source && trace.source.kind === 'fake';
    let position = 0, timer = null, disposed = false;
    let speed = Number(options.speed) || 2;
    speed = Math.min(12, Math.max(0.25, speed));
    const listeners = [];
    function element(tag, className, text, parent) {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      if (parent) parent.appendChild(node);
      return node;
    }
    function listen(node, event, callback) {
      node.addEventListener(event, callback);
      listeners.push([node, event, callback]);
    }
    function button(text, action, parent, className) {
      const node = element('button', className || '', text, parent);
      node.type = 'button';
      listen(node, 'click', action);
      return node;
    }
    root.replaceChildren();
    root.classList.add('xray-player-root');
    const frame = element('section', 'xray-frame', undefined, root);
    frame.tabIndex = 0;
    frame.setAttribute('aria-label', 'StowLM Reader token by token x-ray');
    const header = element('header', 'xray-header', undefined, frame);
    const brand = element('div', 'xray-brand', undefined, header);
    element('div', 'xray-kicker', 'STOWLM READER / DECODE X-RAY', brand);
    element('h1', '', 'Models propose. The mask filters.', brand);
    const badge = element('div', 'xray-source' + (fake ? ' is-fake' : ''),
      fake ? 'FAKE · SCRIPTED MODEL' : 'RECORDED ANSWER', header);
    badge.title = trace.source.label || '';

    const sentence = element('section', 'xray-sentence', undefined, frame);
    element('div', 'xray-kicker', "THE PLANNER'S SENTENCE", sentence);
    element('p', '', trace.sentence || '', sentence);
    const context = trace.context || {};
    const contextText = [];
    if (Array.isArray(context.bays)) contextText.push('Bays on this ship: ' + context.bays.join(', '));
    if (context.max_tiers) contextText.push('Physical tier caps: ' + Object.entries(context.max_tiers)
      .map(([key, value]) => key + ' ' + value).join(' · '));
    element('div', 'xray-context', contextText.join('  /  ') || 'Ship context supplied to the reader mask', sentence);

    const body = element('div', 'xray-body', undefined, frame);
    const outputPanel = element('section', 'xray-panel xray-output-panel', undefined, body);
    const outputHead = element('div', 'xray-panel-head', undefined, outputPanel);
    element('h2', '', 'The JSON being written', outputHead);
    const count = element('span', 'xray-count', '', outputHead);
    const json = element('pre', 'xray-json', undefined, outputPanel);
    json.setAttribute('aria-label', 'JSON so far');
    const chosenBox = element('div', 'xray-chosen-box', undefined, outputPanel);
    element('div', 'xray-kicker', 'TOKEN CHOSEN AFTER THE MASK', chosenBox);
    const chosenText = element('code', 'xray-chosen-token', '', chosenBox);
    const decision = element('div', 'xray-decision', '', chosenBox);
    element('p', 'xray-validation', 'The mask enforces the output grammar and ship limits. The planner still confirms the reading; the full checker gates the plan.', outputPanel);

    const wishes = element('section', 'xray-panel xray-wishes-panel', undefined, body);
    const wishesHead = element('div', 'xray-panel-head', undefined, wishes);
    element('h2', '', "The model's wishes", wishesHead);
    const blockedCount = element('span', 'xray-count', '', wishesHead);
    element('p', 'xray-probability-note', 'Probabilities before the mask, across the full vocabulary. Top-k may omit the chosen token.', wishes);
    const list = element('ol', 'xray-wishes', undefined, wishes);
    list.setAttribute('aria-label', 'Ranked model tokens before masking');
    const legend = element('div', 'xray-legend', undefined, wishes);
    element('span', 'xray-legend-allowed', '● Allowed', legend);
    element('span', 'xray-legend-blocked', '╳ Blocked by mask', legend);
    element('span', 'xray-legend-chosen', '→ Chosen', legend);

    const transport = element('div', 'xray-transport', undefined, frame);
    const previous = button('← Back', () => seek(position - 1), transport);
    const playButton = button('▶ Play', toggle, transport, 'xray-play');
    const next = button('Step →', () => seek(position + 1), transport);
    const blockButton = button('Next block ↗', nextBlock, transport, 'xray-block-button');
    const scrub = element('input', 'xray-scrub', undefined, transport);
    scrub.type = 'range'; scrub.min = '0'; scrub.max = String(trace.steps.length); scrub.step = '1';
    scrub.setAttribute('aria-label', 'Token step');
    listen(scrub, 'input', () => seek(Number(scrub.value)));
    const speedLabel = element('label', 'xray-speed', 'Speed ', transport);
    const speedInput = element('select', '', undefined, speedLabel);
    speedInput.setAttribute('aria-label', 'Tokens per second');
    [0.5, 1, 2, 4, 8, 12].forEach(value => {
      const option = element('option', '', value + ' tok/s', speedInput);
      option.value = String(value);
    });
    if (![0.5, 1, 2, 4, 8, 12].includes(speed)) {
      const option = element('option', '', speed + ' tok/s', speedInput);
      option.value = String(speed);
    }
    speedInput.value = String(speed);
    listen(speedInput, 'change', () => setSpeed(Number(speedInput.value)));
    const footer = element('footer', 'xray-footer', undefined, frame);
    element('span', fake ? 'xray-fake-note' : '', fake
      ? 'FAKE fixture · scripted logits, not Glimmer inference · synthetic ship'
      : 'Recorded model answer · synthetic ship', footer);
    element('span', '', '← → step   /   Space play   /   Home reset   /   End finish', footer);

    function tokenText(token) { return JSON.stringify(token || ''); }
    function render() {
      const row = position ? trace.steps[position - 1] : null;
      count.textContent = 'TOKEN ' + position + ' / ' + trace.steps.length;
      scrub.value = String(position);
      previous.disabled = position === 0;
      next.disabled = position >= trace.steps.length;
      blockButton.disabled = !trace.steps.slice(position).some(step => step.top_k.some(t => t.blocked));
      playButton.textContent = timer === null ? '▶ Play' : 'Ⅱ Pause';
      playButton.setAttribute('aria-pressed', String(timer !== null));
      list.replaceChildren();
      json.replaceChildren();
      if (!row) {
        element('span', 'xray-placeholder', 'Waiting for the first token…', json);
        chosenText.textContent = '—';
        decision.textContent = 'Step forward to watch the mask at work.';
        decision.className = 'xray-decision';
        blockedCount.textContent = 'BEFORE MASK';
        element('li', 'xray-empty', 'Each step reveals the raw ranking, the blocked tokens, and the chosen token.', list);
        return;
      }
      const prior = position > 1 ? trace.steps[position - 2].json_so_far : '';
      const output = row.json_so_far || '';
      if (output.startsWith(prior)) {
        element('span', '', prior, json);
        element('mark', 'xray-new-token', output.slice(prior.length), json);
      } else json.textContent = output;
      json.scrollTop = json.scrollHeight;
      chosenText.textContent = tokenText(row.chosen.token);
      const blocked = row.top_k.filter(t => t.blocked).length;
      blockedCount.textContent = blocked + ' / ' + row.top_k.length + ' SHOWN BLOCKED';
      const top = row.top_k[0];
      const override = top && top.blocked;
      decision.className = 'xray-decision' + (override ? ' is-override' : '');
      decision.textContent = row.is_stop ? 'Complete JSON. The decoder permits the stop token.' : override
        ? 'Top wish blocked: ' + (reasons[top.reason] || top.reason || 'not in grammar') + '.'
        : 'The top wish is allowed by the mask.';
      function candidate(token, rank) {
        const chosen = token.token_id === row.chosen.token_id;
        const item = element('li', 'xray-wish' + (token.blocked ? ' is-blocked' : '') + (chosen ? ' is-chosen' : ''), undefined, list);
        element('span', 'xray-rank', rank === null ? '—' : String(rank + 1).padStart(2, '0'), item);
        const words = element('div', 'xray-wish-words', undefined, item);
        element('code', 'xray-wish-token', tokenText(token.token), words);
        element('span', 'xray-reason', token.blocked
          ? 'BLOCKED · ' + (reasons[token.reason] || token.reason || 'not in grammar')
          : chosen ? (rank === null ? 'CHOSEN · outside raw top-k' : 'CHOSEN') : 'Allowed', words);
        const probability = Number(token.probability);
        const graph = element('div', 'xray-probability', undefined, item);
        element('span', '', Number.isFinite(probability) ? (probability * 100).toFixed(2) + '%' : '—', graph);
        const bar = element('div', 'xray-probability-track', undefined, graph);
        const fill = element('i', '', undefined, bar);
        fill.style.width = (Number.isFinite(probability) ? Math.max(0, Math.min(100, probability * 100)) : 0) + '%';
      }
      row.top_k.forEach(candidate);
      if (!row.top_k.some(t => t.token_id === row.chosen.token_id)) {
        candidate(Object.assign({}, row.chosen, {blocked: false}), null);
      }
    }
    function pause() {
      if (timer !== null) global.clearTimeout(timer);
      timer = null;
      if (!disposed) render();
    }
    function schedule() {
      timer = global.setTimeout(() => {
        timer = null;
        if (disposed) return;
        position = Math.min(trace.steps.length, position + 1);
        if (position < trace.steps.length) schedule();
        render();
      }, 1000 / speed);
    }
    function play() {
      if (disposed || timer !== null || !trace.steps.length) return;
      if (position >= trace.steps.length) position = 0;
      schedule(); render();
    }
    function toggle() { if (timer === null) play(); else pause(); }
    function seek(value) {
      pause();
      position = Math.max(0, Math.min(trace.steps.length, Math.trunc(Number(value) || 0)));
      if (!disposed) render();
    }
    function nextBlock() {
      const index = trace.steps.findIndex((step, i) => i >= position && step.top_k.some(t => t.blocked));
      if (index >= 0) seek(index + 1);
    }
    function setSpeed(value) {
      if (!Number.isFinite(value) || value <= 0) throw new TypeError('Speed must be positive');
      const playing = timer !== null;
      pause(); speed = Math.min(12, Math.max(0.25, value));
      speedInput.value = String(speed);
      if (playing) play();
    }
    listen(frame, 'keydown', event => {
      const tag = event.target && event.target.tagName;
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return;
      if (event.key === 'ArrowRight') seek(position + 1);
      else if (event.key === 'ArrowLeft') seek(position - 1);
      else if (event.key === ' ') toggle();
      else if (event.key === 'Home') seek(0);
      else if (event.key === 'End') seek(trace.steps.length);
      else return;
      event.preventDefault();
    });
    function fit() {
      if (options.fit === false) {
        frame.classList.add('xray-fluid');
        return;
      }
      const width = root.clientWidth || 1920, height = root.clientHeight || width * 9 / 16;
      const scale = Math.min(width / 1920, height / 1080);
      frame.style.transform = 'translate(-50%, -50%) scale(' + scale + ')';
    }
    let observer = null;
    if (typeof global.ResizeObserver === 'function') {
      observer = new global.ResizeObserver(fit); observer.observe(root);
    } else listen(global, 'resize', fit);
    fit();
    seek(options.initialStep === undefined ? 1 : options.initialStep);
    if (options.autoplay) play();
    return {
      seek, play, pause, nextBlock, setSpeed,
      getState: () => ({step: position, playing: timer !== null, speed, total: trace.steps.length}),
      destroy: () => {
        pause(); disposed = true;
        listeners.forEach(([node, name, callback]) => node.removeEventListener(name, callback));
        if (observer) observer.disconnect();
        root.replaceChildren(); root.classList.remove('xray-player-root');
      }
    };
  }
  global.StowLMXray = Object.freeze({mount, format: VERSION});
})(typeof window === 'undefined' ? globalThis : window);
