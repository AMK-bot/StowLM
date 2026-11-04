/* Cargo papers: the recorded v6 intake, in the Studio workstation. */
(function () {
  'use strict';
  window.Studio.step({
    id: 'intake', order: 10, title: 'Cargo papers', icon: 'papers',
    features: ['intake'], ship: true,
    mount(panel, data, ctx) {
      const d = data.intake, esc = ctx.esc;
      const documents = ['photo', 'email', 'sheet'].map((kind) => d.documents.find((doc) => doc.kind === kind)).filter(Boolean);
      const questions = d.questions, answered = new Set();
      const sourceKey = (s) => `${s.document}:${s.cell != null ? s.cell : s.photo != null ? 'photo-' + s.photo : s.quote || ''}`;
      const sources = [...d.flights, ...questions.flatMap((q) => q.spots)];
      const unit = (q, label) => q.column === 'mass' && /\d/.test(label) ? `${label} kg` : label;
      const summary = d.summary;
      let active = false, imported = false, importing = null, generation = 0, filled = 0, complete = false;
      const center = document.createElement('section');
      center.className = 'st-intake-desk';
      center.setAttribute('aria-label', 'Cargo papers and source fields');

      function markedText(doc) {
        const text = doc.text || '';
        const quotes = [...new Set(sources.filter((s) => s.document === doc.id && s.quote).map((s) => s.quote))];
        const intervals = quotes.map((quote) => ({ quote, at: text.indexOf(quote) })).filter((q) => q.at >= 0).sort((a, b) => a.at - b.at || b.quote.length - a.quote.length);
        let html = '', end = 0;
        intervals.forEach(({ quote, at }) => {
          if (at < end) return;
          html += esc(text.slice(end, at));
          html += `<span data-source="${esc(sourceKey({ document: doc.id, quote }))}">${esc(quote)}</span>`;
          end = at + quote.length;
        });
        return html + esc(text.slice(end));
      }
      function paper(doc) {
        let body;
        if (doc.kind === 'photo') {
          const grid = d.photo_lines.length > 9 ? [23.2, 6.16, 6.1] : [28.6, 9.6, 9];
          body = `<div class="st-intake-photo"><img src="${esc(doc.image)}" alt="${esc(doc.title)}" draggable="false">${d.photo_lines.map((line, i) => `<i data-source="${esc(sourceKey({ document: doc.id, photo: i }))}" style="top:${grid[0] + i * grid[1]}%;height:${grid[2]}%" title="Unverified transcription: ${esc(line)}"></i>`).join('')}</div><small class="st-meta st-intake-caution">Photo readings are unverified transcriptions: the model's reading of handwriting, not checked OCR.</small>`;
        } else if (doc.kind === 'sheet') {
          body = `<div class="st-intake-sheet-scroll"><table class="st-table st-intake-sheet"><tbody>${doc.grid.map((row, y) => `<tr><th>${y + 1}</th>${row.map((value, x) => `<td data-source="${esc(sourceKey({ document: doc.id, cell: String.fromCharCode(65 + x) + (y + 1) }))}" title="${esc(value)}">${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
        } else body = `<div class="st-meta st-intake-mail">${markedText(doc)}</div>`;
        return `<article class="st-card st-intake-paper st-intake-paper-${doc.kind}" data-document="${esc(doc.id)}" title="${esc(doc.label)}"><header><b>${esc(doc.title)}</b>${ctx.chip(doc.kind === 'photo' ? 'Synthetic · generated picture' : doc.label, 'synthetic')}</header>${body}</article>`;
      }
      center.innerHTML = `<header class="st-intake-desk-head"><p class="st-meta st-intake-source-status"></p><button type="button" class="st-btn ghost st-intake-ship">View ship</button></header><div class="st-intake-documents">${documents.map(paper).join('')}</div><div class="st-callout warn st-intake-evidence" hidden></div>`;
      panel.innerHTML = `<div class="st-intake-panel"><p class="st-meta">${esc(d.list_scope)}</p><div class="st-intake-provenance">${ctx.chip(d.label, 'recorded')}${ctx.chip(d.who.replace(' · Meta Model API', ''), 'cloud')}${ctx.chip('Separate demos: 12-box demo voyage; the plan is the 87-box hero call', 'synthetic')}</div><button type="button" class="st-btn ghost st-intake-stack" aria-label="Inspect the synthetic cargo papers">${documents.map((doc, i) => `<span class="st-card st-intake-preview st-intake-preview-${doc.kind}" style="--st-intake-index:${i}">${doc.kind === 'photo' ? `<img src="${esc(doc.image)}" alt="${esc(doc.title)}">` : `<b>${esc(doc.title)}</b><span class="st-meta">${esc(doc.kind === 'sheet' ? doc.grid.slice(3, 6).map((r) => r.join(', ')).join('\n') : doc.text.slice(0, 250))}</span>`}</span>`).join('')}<span class="st-intake-stack-label">${ctx.chip(documents[0] ? documents[0].label.split(' · ')[0] : '', 'synthetic')}<span class="st-meta">${documents.length} papers on the desk</span></span></button><p class="st-meta st-intake-progress" hidden>Reading papers…</p><section class="st-intake-load" hidden><div class="st-intake-load-head"><b>Load list</b><span class="st-meta st-intake-count"></span></div><div class="st-intake-list-scroll"><table class="st-table st-intake-list"><thead><tr><th>Box</th><th>ISO</th><th>POD</th><th>kg</th><th>DG</th><th>Reefer</th><th aria-label="Review status"></th></tr></thead><tbody>${d.rows.map((r, y) => `<tr data-row="${y}">${d.columns.map((column) => `<td data-column="${esc(column)}"></td>`).join('')}<td class="st-intake-row-status"></td></tr>`).join('')}</tbody></table></div></section><section class="st-card st-intake-question" hidden aria-live="polite"></section><section class="st-card st-intake-ready" hidden><p class="st-callout ok st-intake-ready-label"></p><p class="st-meta">${esc(summary.headline)}</p></section><button type="button" class="st-btn ghost st-intake-inspect" hidden>Inspect sources</button><div class="st-intake-measured st-intake-figures">${summary.table ? `<table class="st-table"><thead><tr>${summary.table.head.map((head) => `<th>${esc(head)}</th>`).join('')}</tr></thead><tbody>${summary.table.rows.map((row) => `<tr>${row.map((value, i) => `<${i ? 'td' : 'th'}>${esc(value)}</${i ? 'td' : 'th'}>`).join('')}</tr>`).join('')}</tbody></table><p class="st-meta">${esc(summary.table.scope)}</p>` : summary.lines.map((line) => `<p class="st-meta">${esc(line)}</p>`).join('')}<p class="st-meta">${esc(summary.scope)}</p></div><details class="st-disclosure st-intake-details"><summary>Details</summary><div class="st-intake-measured"><p class="st-meta">${esc(d.who)} · <span class="st-mono">${esc(d.model)}</span></p><p class="st-meta">${esc(d.derived_note)}</p><p class="st-meta">${esc(d.derived_detail)}</p><p class="st-meta">${d.wall_s ? `One recorded read took ${esc(d.wall_s.toFixed(1))} s · ` : ''}${d.document_count} documents read · ${documents.length} shown</p></div></details></div>`;
      const $ = (selector) => panel.querySelector(selector);
      const card = $('.st-intake-question');
      const rowEls = [...panel.querySelectorAll('[data-row]')];
      const sourceEls = [...center.querySelectorAll('[data-source]')];
      const current = () => questions.findIndex((_, i) => !answered.has(i));
      const showPapers = () => { if (active) ctx.center(center); };
      $('.st-intake-stack').onclick = showPapers;
      $('.st-intake-inspect').onclick = showPapers;
      center.querySelector('.st-intake-ship').onclick = () => ctx.center(null);
      function highlight(spots, kind) {
        const lit = new Set(spots.map(sourceKey));
        sourceEls.forEach((el) => {
          const selected = lit.has(el.dataset.source);
          el.dataset.highlight = selected ? kind : '';
          el.classList.toggle('st-callout', selected);
          el.classList.toggle('warn', selected && kind === 'ask');
          el.classList.toggle('ok', selected && kind === 'read');
        });
      }
      function paintRows() {
        d.rows.forEach((row, y) => {
          const related = questions.map((q, i) => ({ q, i })).filter(({ q }) => q.row === y);
          const pending = related.filter(({ i }) => !answered.has(i));
          const visible = y < filled;
          rowEls[y].dataset.status = !visible ? '' : pending.length ? 'ask' : 'clear';
          d.columns.forEach((column) => {
            const cell = rowEls[y].querySelector(`[data-column="${column}"]`);
            const resolved = complete || related.some(({ q, i }) => q.column === column && answered.has(i));
            const question = pending.find(({ q }) => q.column === column);
            const value = visible ? String((resolved ? row.after : row.before)[column] || '—') : '';
            cell.textContent = value;
            cell.dataset.status = question ? 'ask' : resolved ? 'fixed' : '';
            cell.title = value + ((row.derived || []).includes(column) ? ` · ${d.derived_note}` : '');
          });
          rowEls[y].querySelector('.st-intake-row-status').innerHTML = visible ? `<span class="st-callout ${pending.length ? 'warn' : 'ok'}" aria-label="${pending.length ? 'Question' : 'Clear'}">${pending.length ? '?' : '✓'}</span>` : '';
        });
        $('.st-intake-count').textContent = `${filled} of ${d.rows.length}`;
      }
      function finish() {
        if (complete) return;
        complete = true;
        paintRows(); card.hidden = true;
        highlight([], '');
        center.querySelector('.st-intake-evidence').hidden = true;
        center.querySelector('.st-intake-source-status').textContent = summary.headline;
        const ready = $('.st-intake-ready'); ready.hidden = false;
        ready.querySelector('.st-intake-ready-label').textContent = `Demo load list ready · ${d.rows.length} boxes (separate demo)`;
        ctx.state.loadlist = d.rows.map((row) => ({ ...row.after }));
        ctx.log({ who: 'Planner', where: null, text: summary.headline, label: d.label });
        ctx.emit('loadlist:ready', ctx.state.loadlist);
        ctx.done();
      }
      function showQuestion() {
        const i = current();
        if (i < 0) { finish(); return; }
        const q = questions[i];
        card.hidden = false;
        card.innerHTML = `<header class="st-meta"><span>${q.scope === 'document' ? 'One question for the whole document' : "The software asks, it doesn't guess"}</span><span>${questions.length - answered.size} left</span></header><p>${esc(q.text)}</p><div class="st-intake-options">${q.options.map((option, n) => `<button type="button" class="st-btn ${option.recorded ? 'primary' : 'ghost'}" data-option="${n}">${esc(unit(q, option.label))}${option.recorded ? '<span class="st-meta">Synthetic planner answer</span>' : ''}</button>`).join('')}</div><p class="st-callout warn st-intake-message" hidden></p>`;
        highlight(q.spots, 'ask');
        const evidence = center.querySelector('.st-intake-evidence');
        const outside = q.spots.filter((spot) => !documents.some((doc) => doc.id === spot.document) && spot.quote);
        evidence.hidden = !outside.length;
        evidence.innerHTML = outside.map((spot) => `<blockquote>${esc(spot.quote)}</blockquote>`).join('');
        center.querySelector('.st-intake-source-status').textContent = q.text;
      }
      card.onclick = (event) => {
        const button = event.target.closest('[data-option]');
        if (!button || !active) return;
        const i = current(); if (i < 0) return;
        const q = questions[i], option = q.options[Number(button.dataset.option)];
        if (!option.recorded) {
          const message = card.querySelector('.st-intake-message'); message.hidden = false;
          message.textContent = `This take only has a recording for ${unit(q, q.after)}. Recorded mode makes no calls, so that answer can't be checked here.`;
          return;
        }
        answered.add(i); paintRows(); showQuestion();
      };
      async function importPapers() {
        if (imported || !active) return;
        const token = ++generation;
        $('.st-intake-progress').hidden = false;
        $('.st-intake-stack').hidden = true; $('.st-intake-load').hidden = false;
        showPapers();
        ctx.log({ who: 'Muse Spark', where: 'cloud', text: `Read ${d.document_count} synthetic documents · ${d.model}`, label: d.label });
        for (const doc of documents) {
          if (!active || token !== generation) return;
          center.querySelector('.st-intake-source-status').textContent = `${d.who} · ${doc.title}`;
          center.querySelectorAll('[data-document]').forEach((el) => { el.dataset.reading = el.dataset.document === doc.id ? 'yes' : ''; });
          highlight(d.flights.filter((flight) => flight.document === doc.id), 'read');
          await ctx.wait(1000);
        }
        for (let y = filled; y < d.rows.length; y++) {
          if (!active || token !== generation) return;
          filled = y + 1; paintRows(); await ctx.wait(90);
        }
        if (!active || token !== generation) return;
        imported = true; $('.st-intake-progress').hidden = true;
        center.querySelectorAll('[data-document]').forEach((el) => { el.dataset.reading = ''; });
        $('.st-intake-inspect').hidden = false; showQuestion();
      }
      function startImport() {
        if (imported || !active) return Promise.resolve();
        if (!importing) {
          const pending = importPapers();
          importing = pending;
          pending.finally(() => { if (importing === pending) importing = null; });
        }
        return importing;
      }
      return {
        enter() {
          active = true;
          if (!imported && !importing) {
            filled = 0; paintRows(); $('.st-intake-progress').hidden = true;
            $('.st-intake-stack').hidden = false; $('.st-intake-load').hidden = true;
          }
          ctx.center(imported ? center : null);
          if (complete) ctx.done();
        },
        async start() { await startImport(); },
        leave() {
          active = false; generation++;
          if (!imported) importing = null;
          ctx.center(null);
        },
        async autoplay(auto) {
          await auto.wait(1000);
          if (!active) return;
          if (importing) await importing;
          else if (!imported) await startImport();
          while (active && !complete) {
            await auto.wait(1700);
            if (!active) return;
            const button = card.querySelector('.primary');
            if (!button) break;
            await auto.click(button);
          }
          await auto.wait(1700);
        },
      };
    },
  });
})();
