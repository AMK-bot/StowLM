/* StowLM Studio · Voyage (order 0): the home inspector (shell-owned; CONTRACT.md v4). The call and who does what.
 * The call's numbers are the server's: /api/v6/state (ship words, rule counts) and the stage bundle (port, boxes to load).
 * Actor names come from the features' payloads where they carry them (intake.who, voice.heard_by, reading.reader.label,
 * replan.credits); the role lines are the shell's plain descriptions. The primary action ("Import cargo papers") is the
 * shell's inspector button. Claims per experiments/review/studio/logic1.md M1-M3. */
(function () {
  'use strict';
  if (!window.Studio) return;
  const first = (s) => String(s || '').split(' · ')[0];

  Studio.step({
    id: 'voyage', order: 0, title: "Today's call", icon: 'ship', features: [], ship: true,
    mount(panel, _data, ctx) {
      const esc = ctx.esc;
      panel.innerHTML = `
        <div class="st-call">
          <div class="st-call-port"><b data-v="port">Laem Chabang</b>${ctx.chip('simulated port', 'synthetic')}</div>
          <p class="st-meta" data-v="ship">Synthetic feeder</p>
          <div class="st-call-stats">
            <div><b data-v="boxes">—</b><span>boxes to load</span></div>
            <div><b data-v="teu">—</b><span>ship size</span></div>
            <div><b data-v="fill">—</b><span>section fill</span></div>
          </div>
          <p class="st-meta">Half-ship section; other bays hold fixed synthetic cargo</p>
        </div>
        <div class="st-label">Who does what</div>
        <div class="st-actors" data-v="actors"></div>`;
      const q = (k) => panel.querySelector(`[data-v="${k}"]`);
      const set = (k, v) => { const n = q(k); if (n && v != null && v !== '') n.textContent = v; };
      ctx.api('state').then((doc) => {
        const parts = String(doc.ship || '').split(' · ');
        set('ship', parts[0]);
        const teu = /(\d[\d,]*)\s*TEU/.exec(parts[0] || ''); if (teu) set('teu', `${teu[1]} TEU`);
        const boxes = parts.find((p) => /box/.test(p)); if (boxes) set('boxes', (/[\d,]+/.exec(boxes) || [''])[0]);
        const fill = parts.find((p) => /fill/.test(p)); if (fill) set('fill', fill.replace(/\s*fill/, ''));
        if (doc.rule_counts) { actors[4].role = `Decides: ${doc.rule_counts.active} active rules; a plan that doesn't pass names the rules it breaks`; draw(); }
      }).catch(() => {});
      ctx.stageReady().then((st) => {
        const m = (st.B || {}).meta || {}, p = m.port || {};
        set('port', (p.label || p.locode || '').replace(/\s*\(simulated\)/i, ''));
        if (typeof m.slice_boxes === 'number') set('boxes', m.slice_boxes.toLocaleString('en-US'));
      });
      const actors = [
        { c: 'spark', icon: 'papers', name: 'Muse Spark', where: 'cloud', role: 'Reads the cargo papers · chooses copilot tools · selects checked statements' },
        { c: 'voice', icon: 'radio', name: 'Muse Voice Transcribe', where: 'cloud', role: 'Turns the radio call into words you can check' },
        { c: 'reader', icon: 'rule', name: 'StowLM Reader', where: 'on this laptop', role: 'Fine-tuned Muse Glimmer 30B · reads instructions into rules' },
        { c: 'engine', icon: 'plan', name: 'Engine 0.13.0', where: 'search', role: 'Plans the load: fewest extra crane lifts' },
        { c: 'checker', icon: 'shield', name: 'Full checker', where: 'rules', role: "Decides; a plan that doesn't pass names the rules it breaks" },
      ];
      function draw() {
        q('actors').innerHTML = actors.map((a) => `<div class="st-actor"><span class="st-tile sm" style="--c:var(--st-c-${a.c})">${Studio.icon(a.icon)}</span>
          <div><b>${esc(a.name)}</b><small>${esc(a.role)}</small></div><span class="st-actor-where">${esc(a.where)}</span></div>`).join('');
      }
      draw();
      Promise.allSettled([ctx.api('intake'), ctx.api('voice'), ctx.api('reading'), ctx.api('replan')]).then(([it, vo, rd, rp]) => {
        const v = (r) => (r.status === 'fulfilled' ? r.value : null);
        if (v(it) && v(it).who) actors[0].name = first(v(it).who);
        if (v(vo) && v(vo).heard_by) actors[1].name = first(v(vo).heard_by);
        const reader = v(rd) && v(rd).reader;
        if (reader && reader.label) { const bits = reader.label.split(' · '); actors[2].name = bits[0]; if (bits[1]) actors[2].role = `${bits[1][0].toUpperCase()}${bits[1].slice(1)} · reads instructions into rules`; }
        if (v(rp) && v(rp).credits) actors[3].name = first(v(rp).credits).replace(/^Frontier\s+/, '');
        draw();
      });
      let shown = false;
      return {
        enter() { ctx.stageReady().then((st) => { if (!shown && st.jumpTo) { st.jumpTo(0); shown = true; } }); },
        leave() {},
      };
    },
  });
})();
