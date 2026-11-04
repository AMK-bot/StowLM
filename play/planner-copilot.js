/* Recorded Copilot proposals for the two-dimensional planning workstation. */
(function () {
  'use strict';
  const Planner = window.Planner;
  if (!Planner) return;

  Planner.stage({
    id: 'copilot', order: 50, title: 'Copilot', features: ['copilot'],
    mount(panel, data, ctx) {
      const d = data.copilot, p = d.proved, take = d.delivered;
      const question = p ? p.sentences.join('\n') : d.main.sentence;
      let active = false, generation = 0, running = false, completed = false, pending = null;
      const el = (tag, className, text) => {
        const n = document.createElement(tag);
        if (className) n.className = className;
        if (text != null) n.textContent = text;
        return n;
      };
      const badge = (text, kind) => el('span', 'pl-copilot-badge' + (kind ? ' pl-copilot-' + kind : ''), text);
      const root = el('section', 'pl-copilot-root');
      const prompt = el('section', 'pl-copilot-prompt');
      const label = el('label', 'pl-copilot-question-label', 'Stored planner question');
      const input = el('textarea', 'pl-copilot-question');
      input.value = question; input.rows = 3; input.readOnly = true;
      input.setAttribute('aria-label', 'Stored planner question');
      input.setAttribute('data-planner-question', 'copilot');
      label.append(input);
      const action = el('div', 'pl-copilot-actions');
      const ask = el('button', 'pl-copilot-button pl-copilot-primary pl-copilot-ask', 'Ask');
      ask.type = 'button';
      ask.setAttribute('data-planner-action', 'copilot');
      action.append(el('p', 'pl-copilot-meta', 'Recorded proposal · no new model request'), ask);
      prompt.append(label, action);
      const result = el('div', 'pl-copilot-result');
      result.setAttribute('aria-label', 'Copilot results');
      root.append(prompt, result); panel.append(root);
      Planner.copilotQuestion = question;

      const current = token => active && token === generation;
      async function pause(ms, token) { await ctx.wait(ms); return current(token); }
      async function type(target, text, token) {
        const duration = Math.max(250, Math.min(1500, text.length * 5));
        const chunks = Math.max(5, Math.ceil(duration / 45));
        for (let i = 1; i <= chunks; i += 1) {
          if (!current(token)) return false;
          target.textContent = text.slice(0, Math.ceil(text.length * i / chunks));
          if (!await pause(duration / chunks, token)) return false;
        }
        return true;
      }
      function card(title) {
        const node = el('section', 'pl-copilot-card');
        if (title) node.append(el('h3', 'pl-copilot-card-title', title));
        return node;
      }
      function namedBays(sentence) {
        // A numbered bay reference is distinct from a distance such as "3 bays away".
        return [...new Set(Array.from(sentence.matchAll(/\bbay\s+0*(\d+)\b/gi), match => Number(match[1])))];
      }
      function showReferencedBays(bays) {
        if (!active || !ctx.bay || !bays.length) return;
        if (typeof ctx.bay.showBays === 'function') ctx.bay.showBays(bays);
        else if (typeof ctx.bay.focusBay === 'function') ctx.bay.focusBay(bays[0]);
      }
      function alternatives(host) {
        const cards = el('div', 'pl-copilot-alternatives');
        cards.setAttribute('aria-label', 'Checked what-if alternatives');
        take.cards.forEach(c => {
          const node = card(c.id);
          const badges = el('div', 'pl-copilot-badges');
          if (c.checked) badges.append(badge('Full checker ✓', 'pass'));
          badges.append(badge('What-if · not applied'));
          node.append(badges, el('p', 'pl-copilot-copy', c.sentence));
          node.append(el('p', 'pl-copilot-metric', c.lifts + ' weighted lifts'));
          const bays = namedBays(c.sentence);
          if (bays.length && ctx.bay && (typeof ctx.bay.showBays === 'function' || typeof ctx.bay.focusBay === 'function')) {
            const inspect = el('button', 'pl-copilot-button pl-copilot-inspect', 'Show referenced bay');
            inspect.type = 'button';
            inspect.addEventListener('click', () => showReferencedBays(bays));
            node.append(inspect);
          }
          cards.append(node);
        });
        host.append(cards);
        if (take.comparison_label) host.append(el('p', 'pl-copilot-meta', take.comparison_label));
      }
      function details(host) {
        const disclosure = el('details', 'pl-copilot-details');
        disclosure.append(el('summary', '', 'Details'));
        const content = el('div', 'pl-copilot-detail-content');
        if (p) content.append(el('p', 'pl-copilot-meta', 'Computed on ' + p.date + ' · ' +
          [p.planner_called ? '' : 'no plan attempted', p.checker_called ? '' : 'no checker report'].filter(Boolean).join(' · ')));
        content.append(el('p', 'pl-copilot-meta', take.label), el('p', 'pl-copilot-meta', take.reader_label));
        if (take.facts_label) content.append(el('p', 'pl-copilot-meta', take.facts_label));
        content.append(el('h3', 'pl-copilot-card-title', "Spark's raw text vs what software let through"));
        content.append(el('p', 'pl-copilot-meta', "Spark's raw text gets strict flags, and software holds back every flagged line."));
        take.turns.forEach(t => {
          const turn = el('section', 'pl-copilot-detail-turn');
          turn.append(el('p', 'pl-copilot-meta', 'Planner · recorded'), el('p', 'pl-copilot-copy', t.planner));
          turn.append(el('p', 'pl-copilot-meta', 'Spark wrote'), el('p', 'pl-copilot-copy', t.raw));
          turn.append(el('p', 'pl-copilot-meta', t.through ? 'Let through' : 'Held back: ' + t.held.join(', ')));
          turn.append(el('p', 'pl-copilot-copy', t.shown)); content.append(turn);
        });
        disclosure.append(content); host.append(disclosure);
      }
      async function replay(token) {
        const refusal = card();
        result.append(refusal);
        if (p) {
          refusal.append(el('p', 'pl-copilot-actor', 'Software · proved by arithmetic'));
          const answer = el('p', 'pl-copilot-copy'); refusal.append(answer);
          if (!await type(answer, 'No, software proved this request impossible before planning.', token)) return;
          refusal.append(badge('Stored refusal, proved by arithmetic'));
          refusal.append(el('p', 'pl-copilot-equation', p.equation + (p.shortfall != null ? ', ' + p.shortfall + ' short' : '')));
          refusal.append(el('p', 'pl-copilot-meta', p.units));
          ctx.log({ who: 'Software', where: 'on this laptop', text: p.equation, label: 'Stored refusal, proved by arithmetic' });
        } else {
          for (const sentence of d.main.sentences) {
            const answer = el('p', 'pl-copilot-copy'); refusal.append(answer);
            if (!await type(answer, sentence.text, token)) return;
          }
        }
        if (!await pause(1150, token)) return;
        if (take) {
          const recorded = el('section', 'pl-copilot-recorded');
          recorded.append(el('h3', 'pl-copilot-card-title', 'In a recorded take on a stricter request, the copilot negotiated two checked alternatives'));
          recorded.append(badge(`Recorded answer · real tools · ${take.scripted_replies_said} scripted planner replies; reader here is cloud Spark`));
          const request = card('Request in this recorded take');
          take.request.forEach(s => request.append(el('p', 'pl-copilot-copy', s)));
          if (take.request_equation) request.append(el('p', 'pl-copilot-equation', take.request_equation));
          recorded.append(request); result.append(recorded);
          const ribbon = el('div', 'pl-copilot-tools');
          ribbon.setAttribute('aria-label', 'Recorded tool calls'); recorded.append(ribbon);
          const response = el('p', 'pl-copilot-copy pl-copilot-negotiation');
          recorded.append(response);
          for (let turn = 0; turn < take.turns.length; turn += 1) {
            for (const tool of take.steps) {
              if (tool.turn !== turn) continue;
              if (!current(token)) return;
              const pill = badge(tool.name); ribbon.append(pill);
              if (tool.wall_s != null) pill.title = tool.wall_s.toFixed(2) + ' s in recorded take';
              ctx.log({ who: 'Copilot', where: 'cloud', text: tool.name + (tool.wall_s == null ? '' : ' · ' + tool.wall_s.toFixed(2) + ' s'), label: take.label });
              if (!await pause(170, token)) return;
            }
            if (turn === 0) alternatives(recorded);
            if (!await type(response, take.turns[turn].shown, token)) return;
            if (!await pause(330, token)) return;
          }
          if (take.recommendation) {
            const recommendation = el('p', 'pl-copilot-copy'); recorded.append(recommendation);
            if (!await type(recommendation, take.recommendation, token)) return;
          }
          const outcome = card();
          if (take.confirmed) outcome.append(badge('Scripted planner confirmation · recorded take'));
          outcome.append(el('p', 'pl-copilot-copy', take.rule));
          if (take.plan) {
            if (take.plan.checker.passed) outcome.append(badge('Full checker ✓', 'pass'));
            outcome.append(el('p', 'pl-copilot-meta', take.plan.label));
          }
          recorded.append(outcome); details(recorded);
        }
        if (!current(token)) return;
        completed = true; running = false; ask.textContent = 'Asked'; ctx.done();
      }
      function start() {
        if (!active || running || completed) return pending;
        running = true; ask.disabled = true;
        const token = generation;
        pending = replay(token).finally(() => { if (current(token)) running = false; });
        return pending;
      }
      ask.addEventListener('click', start);
      return {
        ownsAction: true,
        question,
        questionInput: input,
        askButton: ask,
        controls: { question: input, ask },
        enter() {
          if (!active) { active = true; generation += 1; }
          if (completed) ctx.done();
        },
        start,
        leave() {
          active = false; generation += 1;
          if (running && !completed) {
            running = false; pending = null; result.replaceChildren();
            ask.disabled = false; ask.textContent = 'Ask';
          }
        },
        async autoplay(playCtx) {
          const auto = playCtx || ctx;
          if (!active) return;
          if (!running && !completed) await auto.click(ask);
          if (pending) await pending;
          if (active) await auto.wait(6500);
        },
      };
    },
  });
}());
