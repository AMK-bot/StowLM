/* StowLM Planner · overview (order 0; shell-owned). The call and who does what, in the step panel; the bay plan and the load
 * list already show the ship and the cargo. Numbers are the server's: /api/v6/state (ship words, boxes, fill, rule counts)
 * and planner-data/ship.json. Actor names come from the feature payloads where they carry them; role lines are the
 * shell's plain descriptions. Claims per experiments/review/studio/logic1.md M1–M3. The primary action ("Import cargo
 * papers") is the panel's foot button. */
(function () {
  'use strict';
  if (!window.Planner) return;
  const first = (s) => String(s || '').split(' · ')[0];

  Planner.stage({
    id: 'voyage', order: 0, title: 'Ship & call', features: [],
    mount(panel, _data, ctx) {
      const esc = ctx.esc;
      panel.innerHTML = `
        <div class="pl-call-card">
          <div class="pl-call-port"><b>Laem Chabang</b>${ctx.chip('simulated port', 'recorded')}</div>
          <p class="pl-meta" data-v="ship">Synthetic feeder</p>
          <div class="pl-stats">
            <div><b data-v="boxes">—</b><span>boxes to load</span></div>
            <div><b data-v="teu">—</b><span>ship size</span></div>
            <div><b data-v="fill">—</b><span>section fill</span></div>
          </div>
          <p class="pl-meta">Half-ship section; other bays hold fixed synthetic cargo</p>
        </div>
        <h3 class="pl-section-title">Who does what</h3>
        <div class="pl-actors" data-v="actors"></div>
`;
      const q = (k) => panel.querySelector(`[data-v="${k}"]`);
      const set = (k, v) => { const n = q(k); if (n && v != null && v !== '') n.textContent = v; };
      const actors = [
        { c: 'spark', name: 'Muse Spark', where: 'cloud', role: 'Reads the cargo papers · chooses copilot tools · selects checked statements' },
        { c: 'voice', name: 'Muse Voice Transcribe', where: 'cloud', role: 'Turns the radio call into words you can check' },
        { c: 'reader', name: 'StowLM Reader', where: 'on this laptop', role: 'Fine-tuned Muse Glimmer 30B · reads instructions into rules' },
        { c: 'engine', name: 'Engine 0.13.0', where: 'search', role: 'Plans the load: fewest extra crane lifts' },
        { c: 'checker', name: 'Full checker', where: 'rules', role: "Decides; a plan that doesn't pass names the rules it breaks" },
      ];
      const draw = () => {
        q('actors').innerHTML = actors.map((a) => `<div class="pl-actor" style="--c:var(--pl-c-${a.c})"><i></i><div><b>${esc(a.name)}</b><small>${esc(a.role)}</small></div><em>${esc(a.where)}</em></div>`).join('');
      };
      draw();
      ctx.api('state').then((doc) => {
        const parts = String(doc.ship || '').split(' · ');
        set('ship', parts[0]);
        const teu = /(\d[\d,]*)\s*TEU/.exec(parts[0] || ''); if (teu) set('teu', `${teu[1]} TEU`);
        const boxes = parts.find((p) => /box/.test(p)); if (boxes) set('boxes', (/[\d,]+/.exec(boxes) || [''])[0]);
        const fill = parts.find((p) => /fill/.test(p)); if (fill) set('fill', fill.replace(/\s*fill/, ''));
        if (doc.rule_counts) { actors[4].role = `Decides: ${doc.rule_counts.active} active rules; a plan that doesn't pass names the rules it breaks`; draw(); }
      }).catch(() => {});
      Promise.allSettled([ctx.api('intake'), ctx.api('voice'), ctx.api('reading'), ctx.api('replan')]).then(([it, vo, rd, rp]) => {
        const v = (r) => (r.status === 'fulfilled' ? r.value : null);
        if (v(it) && v(it).who) actors[0].name = first(v(it).who);
        if (v(vo) && v(vo).heard_by) actors[1].name = first(v(vo).heard_by);
        const reader = v(rd) && v(rd).reader;
        if (reader && reader.label) { const bits = reader.label.split(' · '); actors[2].name = bits[0]; if (bits[1]) actors[2].role = `${bits[1][0].toUpperCase()}${bits[1].slice(1)} · reads instructions into rules`; }
        if (v(rp) && v(rp).credits) actors[3].name = first(v(rp).credits).replace(/^Frontier\s+/, '');
        draw();
      });
      return { enter() {}, leave() {} };
    },
  });
})();
