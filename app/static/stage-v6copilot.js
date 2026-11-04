/* v6 chapter 7: the copilot says no. Data: /api/v6/copilot (recorded; every word and number is the fixture's).
 *
 * Full-frame, not ship: true. The negotiation's load is 18 boxes on a small section, and the live stage holds the
 * hero. Beside that ship "31 TEU needed" would read as a claim about a ship it isn't about.
 *
 * beats() and stateAt() are pure, so a take repeats and node can test them. All animation is written from tau into opacity
 * and transform; no CSS transitions, or a seek would land on a frame that depends on the wall clock.
 *
 * d.proved: the hero ship's own stored refusal (the file app/v6_hero.json names, through the server), shown under the recorded "No" as
 * what it is: stored, proved by arithmetic before planning, no model in it. Every word and number is the file's; absent, the panel is.
 *
 * At rest the chapter already shows its shape: the agent, its ribbon, the planner's sentence, the section, what happens next
 * and the path a choice has to take. Each piece hands over to the beat that replaces it, so no tau has an empty frame.
 */
(function () {
  'use strict';
  const V = window.StageV6;
  const ramp = (t, a, b) => (b <= a ? (t >= a ? 1 : 0) : Math.max(0, Math.min(1, (t - a) / (b - a))));
  const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2);
  const active = (c) => c.steps.filter((s) => s.kind !== 'locked').length;
  /* The adopted reader's recorded reading of the same sentence (showcase/reader/recordings.json through the server), beside this one's. */
  const adoptedHtml = (a, esc) => (!a ? '' : `<span class="v6c-adopted">${esc(a.label)} · adopted reader ${esc(a.reader)} (adapter ${esc((a.adapter_sha256 || '').slice(0, 12))}), same sentence: ${a.refused ? `refused as ${esc(a.refusal_code)}` : `read as “${esc(a.read || '')}”`}${a.as_expected ? (a.status === 'exact' ? '' : ` · ${esc(a.note)}`) : ` · misread; stored rule “${esc(a.stored || '')}”`}</span>`);
  const fmtWall = (s) => (s == null ? '' : s >= 1 ? `${s.toFixed(2)} s` : `${+(s * 1000).toPrecision(2)} ms`);

  /* Beat times in seconds. A beat a recording can't support is null, and everything keyed on it stays off. */
  function beats(c, hasPlan) {
    if (!c.offer) {
      return { dur: 7.5, type: [0.2, 1.6], ribbon: [1.4, 3.4], reading: 2.6, say: 3, sayStep: 0.4, card: c.arithmetic ? 3.2 : null, count: [3.3, 4.6], no: c.arithmetic ? 4.7 : null,
               refused: c.arithmetic ? null : 3.4, offer: null, offerSay: null, third: null, pick: null, flow: null, reader: null, confirm: null, tick: null, decides: null, end: 5.4 };
    }
    const pick = hasPlan ? 10.2 : 10.8;
    return { dur: 16, type: [0.3, 2.3], ribbon: [2.2, 4.6], reading: 3.4, say: 4, sayStep: 0.5, card: 4.5, count: [4.6, 6.2], no: 6.3, refused: null,
             offer: 7.4, offerSay: 7.2, third: [8.4, 9.8], pick, flow: [pick + 0.2, pick + 1.2], reader: pick + 1.2,
             confirm: hasPlan ? pick + 2 : null, tick: hasPlan ? pick + 3 : null, decides: hasPlan ? null : pick + 1.8, end: 13.6 };
  }

  function stateAt(tau, c, hasPlan) {
    const b = beats(c, hasPlan), n = active(c), lands = b.card != null ? b.card : b.refused;
    const after = (t, d = 0.35) => (t == null ? 0 : ease(ramp(tau, t, t + d)));
    const typedChars = Math.round(ramp(tau, b.type[0], b.type[1]) * c.sentence.length);
    const said = c.sentences.filter((s) => s.place !== 'card');
    let early = 0, late = 0;
    const chat = said.map((s) => {
      const start = s.place === 'offer' ? b.offerSay + b.sayStep * late++ : b.say + b.sayStep * early++;
      return { start, chars: Math.round(ramp(tau, start, start + s.text.length / 120) * s.text.length), opacity: after(start, 0.2) };
    });
    const count = b.card == null ? 0 : ease(ramp(tau, b.count[0], b.count[1]));
    const a = c.arithmetic || { needed: 0, left: 0 };
    const third = b.third && c.offer.open_cells != null ? ease(ramp(tau, b.third[0], b.third[1])) : 0;
    return {
      planner: 1, typedChars, typing: tau < b.type[1] + 0.3,
      lit: ramp(tau, b.ribbon[0], b.ribbon[1]) * n, chat,
      reading: after(b.reading), card: after(b.card), needed: Math.round(count * a.needed), left: Math.round(count * a.left),
      no: after(b.no, 0.22), refused: after(b.refused, 0.25), offer: after(b.offer),
      third: b.third ? Math.round(third * c.offer.open_cells) : 0, thirdOn: b.third ? after(b.third[0], 0.3) : 0,
      cursor: b.pick == null ? 0 : after(b.pick - 1, 0.3) * (1 - after(b.pick + 0.5, 0.3)), cursorMove: b.pick == null ? 0 : ease(ramp(tau, b.pick - 1, b.pick - 0.1)),
      press: b.pick != null && tau >= b.pick - 0.1 && tau < b.pick + 0.1, picked: b.pick != null && tau >= b.pick,
      path: b.pick == null ? 0 : after(b.pick, 0.3), flow: b.flow ? ease(ramp(tau, b.flow[0], b.flow[1])) : 0, reader: after(b.reader, 0.25),
      confirmed: hasPlan ? after(b.confirm, 0.25) : 0, tick: hasPlan ? after(b.tick, 0.3) : 0, decides: after(b.decides, 0.4),
      end: after(b.end, 0.4), done: tau >= b.end,
      restTop: 1 - after(b.reading + 0.05, 0.3), restBody: 1 - after(lands + 0.05, 0.3),
      restTopIn: 1 - after(b.reading - 0.25, 0.25), restBodyIn: 1 - after(lands - 0.25, 0.25),
    };
  }
  const put = {

    o(el, v) { if (el && el._o !== v) { el._o = v; el.style.opacity = v; el.style.visibility = v > 0 ? 'visible' : 'hidden'; } },
    t(el, v) { if (el && el._t !== v) { el._t = v; el.style.transform = v; } },
    x(el, v) { if (el && el._x !== v) { el._x = v; el.textContent = v; } },
    c(el, name, on) { if (el && !!el['_c' + name] !== !!on) { el['_c' + name] = !!on; el.classList.toggle(name, !!on); } },
  };

  /* The hero's proved refusal, after the recorded "No". Pure, like the beats. */
  const PROVED = { on: 4.9, count: [5.1, 5.6], eq: 5.6 };
  function provedAt(tau) {
    return { on: ease(ramp(tau, PROVED.on, PROVED.on + 0.35)), count: ease(ramp(tau, PROVED.count[0], PROVED.count[1])), eq: ease(ramp(tau, PROVED.eq, PROVED.eq + 0.3)) };
  }
  function provedHtml(p, esc) {
    const pitch = Math.min(20, 980 / Math.max(p.needed, 1)), blocks = (n, cls) => Array.from({ length: n }, () => `<i class="${cls}"></i>`).join('');
    const how = ['proved by arithmetic before planning', p.planner_called ? '' : 'no plan attempted', p.checker_called ? '' : 'no checker report'].filter(Boolean).join(' · ');
    return `<div class="v6c-proved" style="--pitch:${pitch.toFixed(2)}px">
      <div class="v6c-proved-head"><b>This voyage's ship</b><span>Stored refusal, computed on ${esc(p.date)} · ${esc(how)}</span></div>
      <ul class="v6c-proved-said">${p.sentences.map((x) => `<li>&ldquo;${esc(x)}&rdquo;</li>`).join('')}</ul>
      <div class="v6c-prow"><b class="amber" data-p="needed">0</b><span>cells needed</span><div class="v6c-cells">${blocks(p.needed, 'on amber')}</div></div>
      <div class="v6c-prow"><b class="green" data-p="allowed">0</b><span>cells allowed</span><div class="v6c-cells">${blocks(p.allowed, 'on green')}${blocks(p.needed - p.allowed, 'gap')}</div></div>
      <p class="v6c-proved-eq"><b>${esc(p.equation)}</b>${p.units ? ` · ${esc(p.units)}` : ''}</p>
      <b class="v6c-proved-no">No.</b>
    </div>`;
  }

  const sectionLine = (s) => [s.size ? `${s.size} section` : '', s.boxes != null ? `${s.boxes} boxes` : '', s.cells != null ? `${s.cells} cells` : ''].filter(Boolean).join(' · ');
  /* What's about to happen, from the recording's own steps. Tools it never reached share a line. */
  function nextHtml(c, esc) {
    const ran = c.steps.filter((x) => x.kind !== 'locked'), locked = c.steps.filter((x) => x.kind === 'locked');
    const line = (cls, name, note) => `<li class="k-${cls}"><b>${esc(name)}</b>${note ? ` ${esc(note)}` : ''}</li>`;
    const rows = ran.map((x) => line(x.kind, x.name, x.note));
    if (locked.length) rows.push(line('locked', locked.map((x) => x.name).join(', '), locked[0].note));
    return rows.length ? `<div class="v6c-next"><small></small><ol>${rows.join('')}</ol></div>` : '';
  }

  function html(d, c, esc) {
    const a = c.arithmetic, o = c.offer, s = d.scores, tag = (x) => `<span class="v6c-tag">${esc(x)}</span>`;
    const pitch = a ? Math.min(20, 880 / Math.max(a.needed, a.left, (o && o.open_cells) || 0)) : 20;
    const blocks = (n, cls) => Array.from({ length: n }, () => `<i class="${cls}"></i>`).join('');
    const short = a ? a.needed - a.left : 0;
    const said = c.sentences.filter((x) => x.place !== 'card'), caption = c.sentences.filter((x) => x.place === 'card').map((x) => x.text).join(' ');
    const sec = c.section || {}, section = sectionLine(sec);
    return `
      <header class="v6c-head"><b>${esc(d.who.split(' · ')[0])}</b><span>copilot · ${esc(d.who.split(' · ').slice(1).join(' · '))}</span><em>One JSON action at a time. Software validates each one.</em></header>
      <div class="v6c-ribbon" style="--n:${c.steps.length}">
        <div class="v6c-rail"><i class="v6c-line"></i><b class="v6c-dot"></b></div>
        <ol>${c.steps.map((x) => `<li class="k-${x.kind}"${x.raw ? ` title="${esc(x.raw)}"` : ''}><b>${esc(x.name)}</b><span>${esc(x.wall_s != null ? fmtWall(x.wall_s) : x.note)}</span></li>`).join('')}</ol>
      </div>
      <div class="v6c-body">
        <section class="v6c-chat">
          ${nextHtml(c, esc)}
          <p class="v6c-b me"><small>Planner</small><span class="v6c-s"></span><span class="v6c-r">${esc(c.sentence)}</span></p>
          ${said.map((x) => `<p class="v6c-b ai"><span class="v6c-s"></span><span class="v6c-r">${esc(x.text)}</span></p>`).join('')}
          <p class="v6c-b ev">Planner chose the alternative.</p>
        </section>
        <section class="v6c-cards${o ? ' has-path' : ''}">
          <div class="v6c-rest">
            ${section ? `<div class="v6c-rest-top"><div class="v6c-rest-in"><small>The ship's section</small><b>${esc(section)}</b></div></div>` : ''}
            <div class="v6c-rest-body"><div class="v6c-rest-in">
              ${sec.boxes ? `<div class="v6c-strip"><span>${esc(sec.boxes)} boxes to load</span><div>${blocks(sec.boxes, 'box')}</div></div>` : ''}
              ${sec.cells ? `<div class="v6c-strip"><span>${esc(sec.cells)} physical cells</span><div>${blocks(sec.cells, 'cell')}</div></div>` : ''}
              <p>It can't accept a plan the checker hasn't passed.</p>
            </div></div>
          </div>
          <div class="v6c-reading"><small>The reader's reading</small>${c.refusal ? `<b class="bad">Refused: ${esc(c.refusal.reason)}</b>` : `<b>${esc(c.rule)}</b>`}<span>${esc(c.reader_label)}</span>${adoptedHtml(c.adopted, esc)}</div>
          ${a ? `<div class="v6c-sum" style="--pitch:${pitch}px;--need:${a.needed}">
            <div class="v6c-row"><div class="v6c-big amber"><b data-n="needed">0</b><span>TEU needed</span></div><div class="v6c-cells" data-row="needed">${blocks(a.needed, 'on amber')}</div></div>
            <div class="v6c-row"><div class="v6c-big green"><b data-n="left">0</b><span>cells left</span></div><div class="v6c-cells" data-row="left">${blocks(a.left, 'on green')}${blocks(short, 'gap')}</div></div>
            ${o && o.open_cells != null ? `<div class="v6c-row v6c-third"><div class="v6c-big green"><b data-n="third">0</b><span>cells left with the offer</span></div><div class="v6c-cells" data-row="third">${blocks(o.open_cells, 'on green')}</div></div><i class="v6c-mark"></i>` : ''}
            <div class="v6c-no"><b>No.</b><span>Proved impossible by geometry before planning. No checker report exists.</span>${short > 0 ? `<em>${short} short</em>` : ''}</div>
            <p class="v6c-cap">${esc(caption)}${section ? ` · ${esc(section)}` : ''}${o ? `<br><span class="v6c-offer-cap">Offer: ${esc(a.scope)}</span>` : ''}</p>
            ${c.no_offer_note ? `<p class="v6c-none">${esc(c.no_offer_note)}</p>` : ''}
          </div>` : `<div class="v6c-refused"><b>Refused.</b><p>It can't accept a plan the checker hasn't passed.</p></div>`}
          ${d.proved && c === d.main && !o ? provedHtml(d.proved, esc) : ''}
          ${o ? `<div class="v6c-offer">
            <div class="v6c-tags">${o.tag ? tag(o.tag) : ''}${o.label ? tag(o.label) : ''}${o.relaxation ? tag('Relaxes your instruction') : ''}</div>
            <p>&ldquo;${esc(o.sentence)}&rdquo;</p>
            <div class="v6c-act"><button type="button" class="v6-btn" data-do="pick">Send it to the reader</button><b class="v6c-cur"></b></div>
          </div>
          <div class="v6c-path">
            <ol><li>Planner's choice</li><li data-p="reader">Reader</li><li data-p="confirm">Planner confirms<button type="button" class="v6-btn" data-do="confirm" hidden>Confirm</button></li><li data-p="check">Full checker<svg viewBox="0 0 24 24" class="v6c-tick" aria-hidden="true"><path d="M4 13l5 5L20 6"/></svg></li></ol>
            <div class="v6c-flow"><b></b></div>
            <p class="v6c-law">Words reach rules only through the reader.</p>
            ${d.offer_plan ? `<p class="v6c-plan">${esc(d.offer_plan.label)}${d.offer_plan.lifts != null ? ` · ${esc(d.offer_plan.lifts)} lifts` : ''}</p>` : '<p class="v6c-decide"><b>The planner decides.</b> Nothing is applied until the reader reads it, the planner confirms it and the full checker passes a plan.</p>'}
          </div>` : ''}
        </section>
      </div>
      <footer class="v6c-foot">
        ${s ? `<p class="v6c-score">It can't accept a plan the checker hasn't passed. <span class="v6c-more">Enforced by software: ${esc(s.no_unchecked)} of ${esc(s.total)} scored scenarios accepted no unchecked plan, ${esc(s.impossible)} of them impossible · ${esc(s.scope)}<i>Exact tool sequence: ${esc(s.sequence)} of ${esc(s.total)}, same scenarios</i></span></p>` : '<p class="v6c-score">It can\'t accept a plan the checker hasn\'t passed.</p>'}
        <nav class="v6c-chips"></nav>
      </footer>`;
  }
  /* The delivered real-tools take, shot 9's second beat. Local seconds from HANDOVER; a pure function of tau like the rest. */
  const HANDOVER = 7.5, LENGTH = 21;
  function takeBeats(k) {
    const rows = [0.4, 1.6, 2.8, 7.0, 9.4].slice(0, k.turns.length);
    return { rows, cards: 3.2, bars: [3.4, 4.6], gap: 4.8, say: 5.2, pick: 6.4, reader: 7.4, confirm: k.confirmed ? 8.6 : null, tick: k.plan ? 10.2 : null, end: 11.5 };
  }
  function takeAt(tau, k) {
    const b = takeBeats(k), u = tau - HANDOVER, after = (t, d = 0.35) => (t == null ? 0 : ease(ramp(u, t, t + d)));
    const turnOf = (i) => b.rows[Math.min(i, b.rows.length - 1)];
    return {
      on: ease(ramp(tau, HANDOVER - 0.2, HANDOVER + 0.3)), rows: b.rows.map((t) => after(t)),
      lit: k.steps.filter((x) => u >= turnOf(x.turn) + 0.3).length, cards: after(b.cards), bars: ease(ramp(u, b.bars[0], b.bars[1])), gap: after(b.gap), say: after(b.say),
      cursor: after(b.pick - 0.8, 0.3) * (1 - after(b.pick + 0.5, 0.3)), cursorMove: ease(ramp(u, b.pick - 0.8, b.pick - 0.1)), picked: u >= b.pick,
      reader: after(b.reader), confirmed: after(b.confirm), tick: after(b.tick, 0.3), end: after(b.end, 0.4), done: u >= b.end,
    };
  }
  function takeHtml(k, esc) {
    const max = Math.max(...k.cards.map((c) => c.lifts || 0), 1), tag = (x, cls = '') => `<span class="v6c-tag ${cls}">${esc(x)}</span>`;
    const rows = k.turns.map((t, i) => `<li class="v6d-turn${t.through ? ' through' : ' held'}" data-r="${i}">
        <p class="v6d-me"><small>Planner</small>${esc(t.planner)}</p>
        <div class="v6d-pair"><p class="v6d-raw"><small>Spark wrote</small><span>${esc(t.raw)}</span></p>
        <p class="v6d-shown"><small>${t.through ? 'Let through' : `Held back: ${esc(t.held.join(', '))}`}</small><span>${esc(t.shown)}</span></p></div></li>`).join('');
    const cards = k.cards.map((c) => `<div class="v6d-card${c.chosen ? ' chosen' : ''}"><div class="v6c-tags">${c.checked ? tag('checked', 'ok') : ''}${tag('not applied')}</div>
        <b>${esc(c.id)}</b><p>&ldquo;${esc(c.sentence)}&rdquo;</p>
        <div class="v6d-bar"><i style="width:${((100 * (c.lifts || 0)) / max).toFixed(1)}%"></i><span>${esc(c.lifts)} weighted lifts</span></div>
        ${c.chosen ? '<div class="v6c-act"><button type="button" class="v6-btn" data-do="take-pick">Choose this card</button><b class="v6c-cur"></b></div>' : ''}</div>`).join('');
    // The take's own request beside its proof, so its numbers never read as the hero's (logic14 p22). From the recording.
    const req = k.request && k.request.length ? `<p class="v6d-req"><small>Request in this take</small>${k.request.map((x) => `&ldquo;${esc(x)}&rdquo;`).join(' ')}${k.request_equation ? ` · <b>${esc(k.request_equation)}</b>` : ''}</p>` : '';
    return `
      <header class="v6c-head"><b>Muse Spark</b><span>copilot · real tools · Meta Model API · cloud</span><em>Planner replies scripted: ${esc(k.scripted_replies)}</em></header>
      <div class="v6c-ribbon" style="--n:${k.steps.length}"><ol>${k.steps.map((x) => `<li class="k-tool"><b>${esc(x.name)}</b><span>${esc(fmtWall(x.wall_s))}</span></li>`).join('')}</ol></div>
      <div class="v6d-body">
        <section class="v6d-strip"><h3>Spark's raw text, and what software let through</h3>${req}<ol>${rows}</ol></section>
        <section class="v6d-right">
          <div class="v6d-cards">${cards}</div>
          <p class="v6d-gap">${k.lift_gap != null ? `<b>${esc(k.lift_gap)}</b> fewer weighted lifts · ` : ''}${esc(k.comparison_label || '')}</p>
          ${k.recommendation ? `<p class="v6d-say"><small>Spark, let through</small>${esc(k.recommendation)}</p>` : ''}
          <div class="v6c-path"><ol><li class="lit">Planner's choice</li><li data-p="reader">Reader<span>${esc(k.rule)}</span><em>${esc(k.reader_label || '')}</em></li>
            <li data-p="confirm">Planner confirms${k.confirmed ? '<button type="button" class="v6-btn" data-do="take-confirm" hidden>Confirm</button>' : ''}</li>
            <li data-p="check">Full checker<svg viewBox="0 0 24 24" class="v6c-tick" aria-hidden="true"><path d="M4 13l5 5L20 6"/></svg></li></ol>
            ${k.plan ? `<p class="v6c-plan">${esc(k.plan.label)}${k.plan.lifts != null ? ` · ${esc(k.plan.lifts)} weighted lifts` : ''}</p>` : '<p class="v6c-decide"><b>The planner decides.</b> No checked plan in this take.</p>'}</div>
        </section>
      </div>
      <footer class="v6c-foot"><p class="v6c-score">Models propose, software decides.${k.facts_label ? ` <span class="v6c-more">${esc(k.facts_label)}</span>` : ''}</p></footer>`;
  }
  window.StageV6Copilot = { beats, stateAt, fmtWall, html, takeBeats, takeAt, takeHtml, provedAt, provedHtml, PROVED, HANDOVER, LENGTH };
  if (!V) return;

  V.chapter({ id: 'copilot', order: 70, title: 'Copilot', feature: 'copilot', duration_s: LENGTH,
    mount(host, d, ctx) {
      const hasPlan = !!(d.offer_plan && d.offer_plan.checker && d.offer_plan.checker.passed === true);
      const wrap = document.createElement('div'); wrap.className = 'v6c-wrap'; host.appendChild(wrap);
      const M = { c: null, el: null, extra: null, t0: 0 };

      function load(c) {
        M.c = c; wrap.innerHTML = html(d, c, ctx.esc);
        if (!ctx.interactive) wrap.querySelectorAll('.v6c-score .v6c-more').forEach((n) => n.remove()); // the film drops the scenario footer (script shot 9, D296): it would need the original campaign's 41 of 42 beside it
        M.heroFirst = !ctx.interactive && c === d.main && !!d.proved && !c.offer; // the film opens on the hero's stored refusal alone: the small-section beat is cut (film-judge edit 3)
        wrap.classList.toggle('v6c-herofirst', M.heroFirst);
        const q = (s) => wrap.querySelector(s), qa = (s) => [...wrap.querySelectorAll(s)];
        const cells = (name) => [...wrap.querySelectorAll(`[data-row="${name}"] .on`)];
        M.el = { chips: qa('.v6c-ribbon li'), line: q('.v6c-line'), dot: q('.v6c-dot'), bubbles: qa('.v6c-chat .v6c-b:not(.ev)'), ev: q('.v6c-b.ev'), reading: q('.v6c-reading'),
                 sum: q('.v6c-sum'), refused: q('.v6c-refused'), offer: q('.v6c-offer'), path: q('.v6c-path'), no: q('.v6c-no'), third: q('.v6c-third'), mark: q('.v6c-mark'), offerCap: q('.v6c-offer-cap'), cap: q('.v6c-cap'), none: q('.v6c-none'),
                 needed: q('[data-n="needed"]'), left: q('[data-n="left"]'), thirdN: q('[data-n="third"]'), cellsNeeded: cells('needed'), cellsLeft: cells('left'), cellsThird: cells('third'),
                 gaps: qa('[data-row="left"] .gap'), pick: q('[data-do="pick"]'), confirm: q('[data-do="confirm"]'), cur: q('.v6c-cur'), token: q('.v6c-flow b'), law: q('.v6c-law'),
                 decide: q('.v6c-decide'), plan: q('.v6c-plan'), nReader: q('[data-p="reader"]'), nConfirm: q('[data-p="confirm"]'), nCheck: q('[data-p="check"]'), tick: q('.v6c-tick'),
                 foot: q('.v6c-score'), proved: q('.v6c-proved'), pNeeded: q('[data-p="needed"]'), pAllowed: q('[data-p="allowed"]'), pEq: q('.v6c-proved-eq'), pNo: q('.v6c-proved-no'),
                 pCells: [...wrap.querySelectorAll('.v6c-proved .v6c-cells .on')], pGaps: [...wrap.querySelectorAll('.v6c-proved .v6c-cells .gap')], chipBox: q('.v6c-chips'), restTop: q('.v6c-rest-top'), restBody: q('.v6c-rest-body'), next: q('.v6c-next'), restTopIn: q('.v6c-rest-top .v6c-rest-in'), restBodyIn: q('.v6c-rest-body .v6c-rest-in'), nextHead: q('.v6c-next small'), nextRows: qa('.v6c-next li') };
        const label = c.label || d.label;
        ctx.label(M.heroFirst ? '' : /Recorded answer/.test(label) ? label : `Recorded answer · ${label}`); // the stored refusal ran no model: no recorded-answer label over it
        const chips = [];
        if (ctx.interactive) {
          if (c !== d.main) chips.push(['main', 'Back to the negotiation']);
          if (d.attack && c !== d.attack) chips.push(['attack', 'Try to talk it round']);
          if (d.second && c !== d.second) chips.push(['second', `Real reader: "${d.second.sentence}"`]);
        }
        M.el.chipBox.innerHTML = chips.map(([k, t]) => `<button type="button" class="v6c-chip" data-go="${k}">${ctx.esc(t)}</button>`).join('');
      }
      wrap.addEventListener('click', (e) => {
        const go = e.target.closest('[data-go]'), act = e.target.closest('[data-do]');
        if (go) { M.extra = go.dataset.go === 'main' ? null : d[go.dataset.go]; M.t0 = performance.now(); load(M.extra || d.main); M.was = null; }
        if (act) ctx.release(act.dataset.do);
      });

      function draw(s, c, interactive) {
        const e = M.el, n = active(c), total = c.steps.length;
        e.chips.forEach((chip, i) => {
          const locked = c.steps[i].kind === 'locked';
          const opened = locked && s.tick > 0.5 && c.steps[i].name !== 'try_ladder';
          put.o(chip, opened ? 1 : locked ? 0.7 : 0.7 + 0.3 * ramp(s.lit, i, i + 0.5));
          put.c(chip, 'lit', !locked && s.lit > i); put.c(chip, 'now', !locked && s.lit > i && s.lit < i + 1); put.c(chip, 'open', opened);
          if (locked) put.x(chip.lastElementChild, opened ? 'stored plan' : c.steps[i].note);
        });
        const frac = Math.min(s.lit, n) / total;
        put.t(e.line, `scaleX(${frac.toFixed(4)})`); put.t(e.dot, `translateX(${(frac * 100).toFixed(2)}%)`); put.o(e.dot, s.lit > 0 && s.lit < n ? 1 : 0);
        const me = e.bubbles[0];
        put.c(me, 'typing', s.typing);
        put.o(me, s.planner); put.x(me.querySelector('.v6c-s'), c.sentence.slice(0, s.typedChars)); put.x(me.querySelector('.v6c-r'), c.sentence.slice(s.typedChars));
        s.chat.forEach((row, i) => {
          const b = e.bubbles[i + 1]; if (!b) return;
          const text = b._full || (b._full = b.textContent);
          put.o(b, row.opacity); put.t(b, `translateY(${((1 - row.opacity) * 10).toFixed(1)}px)`);
          put.x(b.querySelector('.v6c-s'), text.slice(0, row.chars)); put.x(b.querySelector('.v6c-r'), text.slice(row.chars));
        });
        put.o(e.ev, s.picked ? s.path : 0);
        put.o(e.restTop, s.restTop); put.o(e.restBody, s.restBody); put.o(e.restTopIn, s.restTopIn); put.o(e.restBodyIn, s.restBodyIn); put.o(e.next, 1 - s.tick);
        put.x(e.nextHead, s.lit >= n ? 'What ran' : 'What happens next');
        e.nextRows.forEach((row, i) => put.c(row, 'lit', s.lit > i));
        put.o(e.reading, s.reading); put.t(e.reading, `translateY(${((1 - s.reading) * 10).toFixed(1)}px)`);
        if (e.sum) {
          put.o(e.sum, s.card); put.t(e.sum, `translateY(${((1 - s.card) * 14).toFixed(1)}px)`);
          put.x(e.needed, String(s.needed)); put.x(e.left, String(s.left));
          e.cellsNeeded.forEach((x, i) => put.o(x, i < s.needed ? 1 : 0.06)); e.cellsLeft.forEach((x, i) => put.o(x, i < s.left ? 1 : 0.06));
          e.gaps.forEach((x) => put.o(x, s.no));
          put.o(e.no, s.no); put.t(e.no, `scale(${(1.35 - 0.35 * s.no).toFixed(3)})`); put.o(e.cap, s.no); put.o(e.none, s.no);
          if (e.third) { put.o(e.third, s.thirdOn); put.o(e.mark, s.thirdOn); put.o(e.offerCap, s.thirdOn); put.x(e.thirdN, String(s.third)); e.cellsThird.forEach((x, i) => put.o(x, i < s.third ? 1 : 0.06)); }
        }
        if (e.refused) { put.o(e.refused, s.refused); put.t(e.refused, `scale(${(1.2 - 0.2 * s.refused).toFixed(3)})`); }
        if (e.offer) {
          put.o(e.offer, s.offer); put.t(e.offer, `translateY(${((1 - s.offer) * 14).toFixed(1)}px)`);
          put.c(e.pick, 'press', s.press); put.c(e.pick, 'chosen', s.picked); put.x(e.pick, s.picked ? 'Sent to the reader' : 'Send it to the reader');
          put.c(e.pick, 'ask', interactive && s.offer === 1 && !s.picked);
          put.o(e.cur, interactive ? 0 : s.cursor); put.t(e.cur, `translate(${((1 - s.cursorMove) * 260).toFixed(1)}px, ${((1 - s.cursorMove) * 130).toFixed(1)}px)`);
        }
        if (e.path) {
          put.o(e.path, 0.45 + 0.55 * s.path); put.t(e.token, `translateX(${(s.flow * 25).toFixed(2)}%)`); put.o(e.token, s.flow > 0 && s.flow < 1 ? 1 : 0);
          put.c(e.nReader, 'lit', s.reader > 0.5); put.c(e.nConfirm, 'lit', s.confirmed > 0.5); put.c(e.nCheck, 'done', s.tick > 0.5);
          put.o(e.tick, s.tick); put.o(e.decide, s.decides); put.o(e.plan, s.tick);
          if (e.confirm) e.confirm.hidden = !(interactive && hasPlan && s.reader === 1 && s.confirmed < 0.5);
        }
        put.o(e.foot, s.end);
      }
      function drawProved(p) {
        const e = M.el, P = d.proved; if (!e.proved) return;
        put.o(e.proved, p.on); put.t(e.proved, `translateY(${((1 - p.on) * 14).toFixed(1)}px)`);
        const needed = Math.round(p.count * P.needed), allowed = Math.round(p.count * P.allowed);
        put.x(e.pNeeded, String(needed)); put.x(e.pAllowed, String(allowed));
        let n = 0, a = 0;
        e.pCells.forEach((x) => put.o(x, x.classList.contains('amber') ? (n++ < needed ? 1 : 0.06) : (a++ < allowed ? 1 : 0.06)));
        e.pGaps.forEach((x) => put.o(x, p.eq)); put.o(e.pEq, p.eq); put.o(e.pNo, p.eq);
      }

      function fit() {
        const k = Math.min(host.clientWidth / 1920, host.clientHeight / 1022) || 1;
        for (const w of twrap ? [wrap, twrap] : [wrap]) { w.style.transform = `scale(${k})`; w.style.width = `${host.clientWidth / k}px`; w.style.height = `${host.clientHeight / k}px`; }
      }
      const K = d.delivered || null, twrap = K ? document.createElement('div') : null, T = {};
      if (K) {
        twrap.className = 'v6c-wrap v6c-take'; twrap.innerHTML = takeHtml(K, ctx.esc); host.appendChild(twrap);
        const q = (x) => twrap.querySelector(x);
        Object.assign(T, { rows: [...twrap.querySelectorAll('.v6d-turn')], chips: [...twrap.querySelectorAll('.v6c-ribbon li')], cards: q('.v6d-cards'), bars: [...twrap.querySelectorAll('.v6d-bar i')],
                           gap: q('.v6d-gap'), say: q('.v6d-say'), chosen: q('.v6d-card.chosen'), pick: q('[data-do="take-pick"]'), cur: q('.v6c-cur'), path: q('.v6c-path'),
                           nReader: q('[data-p="reader"]'), nConfirm: q('[data-p="confirm"]'), nCheck: q('[data-p="check"]'), confirm: q('[data-do="take-confirm"]'), tick: q('.v6c-tick'),
                           plan: q('.v6c-plan') || q('.v6c-decide'), foot: q('.v6c-score') });
        twrap.addEventListener('click', (e) => { const act = e.target.closest('[data-do]'); if (act) ctx.release(act.dataset.do); });
      }
      function drawTake(s, interactive) {
        put.o(twrap, s.on); put.o(wrap, 1 - s.on);
        T.rows.forEach((row, i) => { put.o(row, s.rows[i]); put.t(row, `translateY(${((1 - s.rows[i]) * 10).toFixed(1)}px)`); });
        T.chips.forEach((chip, i) => { put.c(chip, 'lit', s.lit > i); put.c(chip, 'now', s.lit === i + 1 && !s.done); });
        put.o(T.cards, s.cards); T.bars.forEach((bar) => put.t(bar, `scaleX(${s.bars.toFixed(3)})`));
        put.o(T.gap, s.gap); if (T.say) put.o(T.say, s.say);
        put.c(T.chosen, 'picked', s.picked);
        if (T.pick) { put.x(T.pick, s.picked ? 'Chosen' : 'Choose this card'); put.c(T.pick, 'chosen', s.picked); put.c(T.pick, 'ask', interactive && s.cards === 1 && !s.picked); }
        if (T.cur) { put.o(T.cur, interactive ? 0 : s.cursor); put.t(T.cur, `translate(${((1 - s.cursorMove) * 260).toFixed(1)}px, ${((1 - s.cursorMove) * 130).toFixed(1)}px)`); }
        put.o(T.path, 0.4 + 0.6 * (s.picked ? 1 : 0)); put.c(T.nReader, 'lit', s.reader > 0.5); put.c(T.nConfirm, 'lit', s.confirmed > 0.5); put.c(T.nCheck, 'done', s.tick > 0.5);
        if (T.confirm) T.confirm.hidden = !(interactive && s.reader === 1 && s.confirmed < 0.5);
        put.o(T.tick, s.tick); put.o(T.plan, K.plan ? s.tick : s.end); put.o(T.foot, s.end);
      }
      addEventListener('resize', fit);
      load(d.main);
      return {
        enter() { fit(); const c = M.c; const label = c.label || d.label; ctx.label(/Recorded answer/.test(label) ? label : `Recorded answer · ${label}`); if (K) put.o(twrap, 0); },
        leave() { ctx.label(''); M.extra = null; if (M.c !== d.main) load(d.main); },
        seek(tau, interactive) {
          if (!interactive && M.c !== d.main) { M.extra = null; load(d.main); }
          if (interactive && M.extra) { if (K) { put.o(twrap, 0); put.o(wrap, 1); } draw(stateAt((performance.now() - M.t0) / 1000, M.c, false), M.c, true); return; }
          if (interactive) {
            const b = beats(d.main, hasPlan);
            if (b.pick != null) ctx.waitAt(tau, 'pick', b.pick - 0.1);
            if (b.confirm != null) ctx.waitAt(tau, 'confirm', b.confirm - 0.12);
            if (K) { const t = takeBeats(K); ctx.waitAt(tau, 'take-pick', HANDOVER + t.pick - 0.1); if (t.confirm != null) ctx.waitAt(tau, 'take-confirm', HANDOVER + t.confirm - 0.12); }
          }
          draw(stateAt(tau, d.main, hasPlan), d.main, !!interactive);
          drawProved(provedAt(tau));
          if (K) {
            drawTake(takeAt(tau, K), !!interactive);
            const label = tau >= HANDOVER ? (/Recorded answer/.test(K.label) ? K.label : `Recorded answer · ${K.label}`) : null;
            if (label !== M.takeLabel) { M.takeLabel = label; const c = d.main.label || d.label; ctx.label(label || (M.heroFirst ? '' : /Recorded answer/.test(c) ? c : `Recorded answer · ${c}`)); }
          }
        },
      };
    } });
})();
