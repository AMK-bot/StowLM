/* Studio copilot: stored arithmetic first, then the recorded real-tool negotiation. */
(function () {
  'use strict';
  const Studio = window.Studio;
  if (!Studio) return;

  Studio.step({
    id: 'copilot', order: 50, title: 'Copilot', icon: 'copilot',
    features: ['copilot'], ship: true,
    mount(panel, data, ctx) {
      const d = data.copilot, p = d.proved, take = d.delivered;
      const question = p ? p.sentences.join('\n') : d.main.sentence;
      let active = false, generation = 0, running = false, completed = false, pending = null;
      const el = (tag, cls, text) => {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
      };
      const root = el('section', 'st-copilot-root');
      const chip = (text, kind, title) => {
        const n = el('span', 'st-chip ' + kind, text);
        if (title) n.title = title;
        return n;
      };
      function actorTile(actor) {
        const tile = el('span', 'st-tile sm');
        tile.style.setProperty('--c', 'var(--st-c-' + actor + ')');
        tile.setAttribute('aria-hidden', 'true');
        const glyphs = {
          software: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2m4 0h2m-8 4h2m4 0h2m-8 3h2m4 0h2"/>',
          spark: '<path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6L12 3Z"/>',
          checker: '<path d="M12 3 4 6v6c0 4.5 8 9 8 9s8-4.5 8-9V6l-8-3Z"/><path d="m8 12 3 3 5-6"/>',
        };
        tile.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' + glyphs[actor] + '</svg>';
        return tile;
      }
      function actorRow(actor, label) {
        const row = el('div', 'st-copilot-actor-row');
        row.append(actorTile(actor), label);
        return row;
      }
      const checkedMark = () => actorRow('checker', chip('Full checker ✓', 'pass'));
      const thread = el('div', 'st-copilot-response');
      thread.setAttribute('role', 'log');
      thread.setAttribute('aria-label', 'Copilot response');
      root.append(thread); panel.append(root);
      // The shell types and sends this exact stored question through its composer.
      Studio.copilotQuestion = question;

      const current = token => active && token === generation;
      async function pause(ms, token) { await ctx.wait(ms); return current(token); }
      function message(who, text, cls) {
        const box = el('article', 'st-copilot-message ' + (cls || ''));
        if (who) box.append(actorRow('software', el('small', 'st-meta', who)));
        const body = el('p', 'st-copilot-message-text', text);
        box.append(body); thread.append(box);
        return { box, body };
      }
      async function say(text, token, who = '') {
        const msg = message(who, '', 'st-copilot-assistant');
        // Every answer types in 250–1500 ms, scaled by the shell's demo speed.
        const duration = Math.max(250, Math.min(1500, text.length * 5));
        const chunks = Math.max(5, Math.ceil(duration / 45));
        for (let n = 1; n <= chunks; n += 1) {
          if (!current(token)) return false;
          msg.body.textContent = text.slice(0, Math.ceil(text.length * n / chunks));
          if (!await pause(duration / chunks, token)) return false;
        }
        return true;
      }
      function proof() {
        const card = el('section', 'st-copilot-proof st-card');
        card.append(chip('Stored refusal, proved by arithmetic', 'stored'));
        card.append(el('p', 'st-callout bad', p.equation + (p.shortfall != null ? ', ' + p.shortfall + ' short' : '')));
        card.append(el('p', 'st-card-sub', p.units));
        card.append(el('p', 'st-meta', 'Computed on ' + p.date + ' · ' +
          [p.planner_called ? '' : 'no plan attempted', p.checker_called ? '' : 'no checker report'].filter(Boolean).join(' · ')));
        thread.append(card);
      }
      function alternatives() {
        const cards = el('section', 'st-copilot-cards');
        cards.setAttribute('aria-label', 'Checked alternatives from the recorded tools');
        take.cards.forEach(c => {
          const card = el('article', 'st-card st-copilot-card');
          const tags = el('div', 'st-copilot-chips');
          if (c.checked) tags.append(checkedMark());
          tags.append(chip('What-if · not applied', 'stored'));
          card.append(tags, el('h3', 'st-card-title', c.id), el('p', 'st-card-sub', c.sentence));
          const metric = el('div', 'st-meta st-copilot-lifts');
          metric.append(el('b', '', String(c.lifts)), el('span', '', 'weighted lifts'));
          card.append(metric); cards.append(card);
        });
        thread.append(cards);
        if (take.comparison_label) thread.append(el('p', 'st-meta', take.comparison_label));
      }
      function rawStrip() {
        const strip = el('details', 'st-disclosure st-copilot-raw');
        strip.append(el('summary', '', "Spark's raw text vs what software let through"));
        const content = el('div', 'st-copilot-raw-content');
        take.turns.forEach(t => {
          const turn = el('section', 'st-copilot-raw-turn');
          turn.append(el('small', 'st-meta', 'Planner · recorded'), el('p', '', t.planner));
          turn.append(el('small', 'st-meta', 'Spark wrote'), el('p', 'st-card-sub', t.raw));
          turn.append(el('small', 'st-meta', t.through ? 'Let through' : 'Held back: ' + t.held.join(', ')));
          turn.append(el('p', t.through ? 'st-callout ok' : 'st-callout warn', t.shown));
          content.append(turn);
        });
        strip.append(content);
        thread.append(el('p', 'st-meta', "Spark's raw text gets strict flags, and software holds back every flagged line."), strip);
      }
      async function replay(token) {
        if (!await pause(350, token)) return;
        if (p) {
          if (!await say('No, software proved this request impossible before planning.', token, 'Software · proved by arithmetic')) return;
          proof();
          ctx.log({ who: 'Software', where: 'on this laptop', text: p.equation, label: 'Stored refusal, proved by arithmetic' });
        } else {
          for (const s of d.main.sentences) if (!await say(s.text, token)) return;
        }
        if (!await pause(1150, token)) return;
        if (take) {
          const takeHead = el('div', 'st-copilot-take-head');
          takeHead.append(actorRow('spark', el('p', 'st-card-title', 'In a recorded take on a stricter request, the copilot negotiated two checked alternatives')));
          takeHead.append(chip(`Recorded answer · real tools · ${take.scripted_replies_said} scripted planner replies; reader here is cloud Spark`, 'recorded', take.label));
          takeHead.append(el('small', 'st-meta', 'Request in this recorded take'));
          take.request.forEach(s => takeHead.append(el('p', 'st-card-sub st-copilot-take-request', s)));
          if (take.request_equation) takeHead.append(el('p', 'st-callout warn', take.request_equation));
          thread.append(takeHead);
          const ribbon = el('div', 'st-copilot-tools');
          ribbon.setAttribute('aria-label', 'Recorded tool calls'); thread.append(ribbon);
          const called = new Set();
          for (let turn = 0; turn < take.turns.length; turn += 1) {
            const t = take.turns[turn];
            for (let i = 0; i < take.steps.length; i += 1) {
              const tool = take.steps[i];
              if (tool.turn !== turn || called.has(i)) continue;
              if (!current(token)) return;
              called.add(i);
              const pill = el('span', 'st-chip st-copilot-tool', tool.name);
              if (tool.wall_s != null) pill.title = tool.wall_s.toFixed(2) + ' s in recorded take';
              ribbon.append(pill);
              ctx.log({ who: 'Copilot', where: 'cloud', text: tool.name + (tool.wall_s == null ? '' : ' · ' + tool.wall_s.toFixed(2) + ' s'), label: take.label });
              if (!await pause(170, token)) return;
            }
            if (turn === 0) alternatives();
            if (!await say(t.shown, token)) return;
            if (!await pause(330, token)) return;
          }
          if (take.recommendation && !await say(take.recommendation, token)) return;
          const result = el('section', 'st-card st-copilot-result');
          if (take.confirmed) result.append(chip('Scripted planner confirmation · recorded take', 'recorded'));
          result.append(el('p', 'st-card-sub', take.rule));
          if (take.plan) {
            if (take.plan.checker.passed) result.append(checkedMark());
            result.append(el('p', 'st-meta', take.plan.label));
          }
          thread.append(result); rawStrip();
          const details = el('details', 'st-disclosure st-copilot-details');
          details.append(el('summary', '', 'Details'));
          details.append(el('p', 'st-meta', take.label), el('p', 'st-meta', take.reader_label));
          if (take.facts_label) details.append(el('p', 'st-meta', take.facts_label));
          thread.append(details);
        }
        if (!current(token)) return;
        completed = true; running = false; ctx.done();
      }
      function start() {
        if (!active || running || completed) return pending;
        running = true;
        const token = generation;
        pending = replay(token).finally(() => { if (current(token)) running = false; });
        return pending;
      }
      return {
        enter() {
          active = true; generation += 1; ctx.center(null);
          if (completed) ctx.done();
        },
        start,
        leave() {
          active = false; generation += 1;
          if (running && !completed) {
            // A retake begins cleanly; detached work stops at the next scaled wait.
            running = false; thread.replaceChildren(); pending = null;
          }
        },
        async autoplay(playCtx) {
          const auto = playCtx || ctx;
          if (pending) await pending;
          if (active) await auto.wait(6500);
        },
      };
    },
  });
}());
