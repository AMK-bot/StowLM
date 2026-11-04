/* v6 chapter 5: x-ray of the mask (shot 6, then the reading chapter's confirm beat). Embeds experiments/mvp/pipeline/xray/player.js, which checks provenance itself
 * and keeps its own FAKE or RECORDED ANSWER badge on screen. seek(tau) maps tau onto the player's steps and never lets it play.
 * The film refuses a fake or a recording with no block: the chapter is a gap there, and shot 6 falls back to its card.
 * Interactive may show the fake, under its FAKE label. A player that doesn't load or mount rejects view.ready, which
 * StageV6.prepare and enter await, and leaves a standing gap: the director picks the fallback before it commits the shot. */
(function () {
  const V = window.StageV6;
  const REASONS = { not_in_grammar: 'not in the grammar', bay_not_on_ship: 'that bay is not on this ship', tier_over_cap: 'over the tier cap' };
  const DURATION = 10;

  /* Seconds each step starts at. Step 0 is the empty prefix; a step whose first choice was blocked holds longest. */
  function schedule(steps, duration) {
    const dwell = [0.7, ...steps.map((s) => (s.top_k[0] && s.top_k[0].blocked && !s.is_stop ? 3.4 : 1))]; // the end-token swap isn't a block
    const unit = duration / dwell.reduce((a, b) => a + b, 0);
    let at = 0;
    return dwell.map((d) => { const start = at; at += d * unit; return start; });
  }
  function stepAt(tau, starts) {
    let step = 0;
    while (step + 1 < starts.length && tau >= starts[step + 1]) step += 1;
    return step;
  }

  V.chapter({ id: 'xray', order: 50, title: 'X-ray', feature: 'xray', duration_s: DURATION, schedule, stepAt,
    mount(host, d, ctx) {
      if (!ctx.interactive && d.film !== true) return { gap: true };
      const esc = ctx.esc, steps = d.trace.steps, starts = schedule(steps, DURATION);
      const block = steps.findIndex((s) => s.top_k[0] && s.top_k[0].blocked && !s.is_stop) + 1;   // player step of the first overridden word
      const hold = block ? starts[block] + 1.4 : null;
      host.innerHTML = `<div class="v6x-player"></div>
        <div class="v6x-caption">
          <p class="v6x-block" hidden></p>
          <p class="v6x-line">A logits processor sets forbidden next words to zero · ${d.real ? `this sentence: ${d.blocked} of ${d.tokens} next words had the model's first choice blocked` : 'a scripted example: no model ran'}</p>
          <p class="v6x-sub">The mask keeps the form legal. Legal can still be wrong, so a person confirms. <b>${esc(d.source_label)}</b></p>
          <button type="button" class="v6-btn v6x-go" hidden>Continue</button>
        </div>`;
      host.classList.toggle('v6x-film', !ctx.interactive);
      const root = host.querySelector('.v6x-player'), hit = host.querySelector('.v6x-block'), go = host.querySelector('.v6x-go');
      if (d.first_block) hit.innerHTML = `Token “<code>${esc(d.first_block.token)}</code>” blocked: ${esc(REASONS[d.first_block.reason] || d.first_block.reason)}`;
      let player = null, shown = -1, touched = false, wanted = 0;
      const draw = (tau) => {
        wanted = tau;
        if (!player) return;
        const step = stepAt(tau, starts);
        if (step !== shown) { shown = step; player.seek(step); hit.hidden = !d.first_block || step !== block; }
      };
      const script = () => new Promise((res, rej) => { const js = document.createElement('script'); js.src = d.player_js; js.onload = res; js.onerror = () => rej(new Error('player script did not load')); document.head.appendChild(js); });
      const start = async () => {
        if (!window.StowLMXray) await script();
        const made = await window.StowLMXray.mount(root, d.trace, { initialStep: 0, speed: 2, autoplay: false, fit: false });
        if (!made || typeof made.seek !== 'function') throw new Error('player did not mount');
        const head = root.querySelector('.xray-sentence .xray-kicker');  // a synthetic validation row, not a planner's words (logic4 F13)
        if (head) head.textContent = 'SENTENCE';
        player = made; draw(wanted);
      };
      const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = d.player_css; document.head.appendChild(css);
      root.addEventListener('pointerdown', () => { touched = true; go.hidden = true; ctx.release('block'); });
      go.addEventListener('click', () => { go.hidden = true; ctx.release('block'); });
      const view = {
        gap: false,
        ready: null,
        preload() { return view.ready; },
        enter() { touched = false; shown = -1; return view.ready; },
        seek(tau, interactive) {
          if (interactive) {
            if (touched) return;   // a judge took the player's own controls; the chapter stops steering it
            const held = hold !== null && ctx.waitAt(tau, 'block', hold);
            if (go.hidden === held) go.hidden = !held;
          }
          draw(Math.max(0, Math.min(tau, DURATION)));
        },
      };
      view.ready = start().catch((error) => { console.warn('x-ray player unavailable: chapter skipped:', error.message); view.gap = true; host.replaceChildren(); throw error; });
      view.ready.catch(() => {}); // read by prepare and enter
      return view;
    } });
})();
