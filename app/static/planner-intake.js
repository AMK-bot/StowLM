/* Cargo intake: recorded synthetic papers, kept separate from the hero call. */
(function () {
  'use strict';
  window.Planner.stage({
    id: 'intake', order: 10, title: 'Cargo', features: ['intake'],
    mount(panel, data, ctx) {
      const d = data.intake, esc = ctx.esc;
      const documents = ['photo', 'email', 'sheet'].map((kind) => d.documents.find((doc) => doc.kind === kind)).filter(Boolean);
      const questions = d.questions, answered = new Set(), summary = d.summary;
      const sampleLabel = `Separate sample voyage · ${d.rows.length}-box demo`;
      const separateNote = 'Separate demos: 12-box demo voyage; the plan is the 87-box hero call.';
      const sourceKey = (source) => `${source.document}:${source.cell != null ? source.cell : source.photo != null ? 'photo-' + source.photo : source.quote || ''}`;
      const sources = [...d.flights, ...questions.flatMap((question) => question.spots)];
      const chip = (text, kind) => `<span class="pl-intake-chip" data-kind="${kind || ''}">${esc(text)}</span>`;
      const unit = (question, text) => question.column === 'mass' && /\d/.test(text) ? `${text} kg` : text;
      let active = false, imported = false, pending = null, generation = 0, filled = 0, complete = false;
      const sheet = document.createElement('section');
      sheet.className = 'pl-intake-sheet';
      sheet.setAttribute('aria-label', 'Synthetic cargo papers and extracted source fields');

      function markedText(doc) {
        const text = doc.text || '';
        const quotes = [...new Set(sources.filter((source) => source.document === doc.id && source.quote).map((source) => source.quote))];
        const intervals = quotes.map((quote) => ({ quote, at: text.indexOf(quote) })).filter((item) => item.at >= 0).sort((a, b) => a.at - b.at || b.quote.length - a.quote.length);
        let html = '', end = 0;
        intervals.forEach(({ quote, at }) => {
          if (at < end) return;
          html += esc(text.slice(end, at));
          html += `<span data-source="${esc(sourceKey({ document: doc.id, quote }))}">${esc(quote)}</span>`;
          end = at + quote.length;
        });
        return html + esc(text.slice(end));
      }
      function paper(doc, index) {
        let body;
        if (doc.kind === 'photo') {
          // Same image coordinates as the recorded v6 tally-sheet chapter.
          const grid = d.photo_lines.length > 9 ? [23.2, 6.16, 6.1] : [28.6, 9.6, 9];
          body = `<div class="pl-intake-photo"><img src="${esc(doc.image)}" alt="${esc(doc.title)}" draggable="false">${d.photo_lines.map((line, i) => `<i data-source="${esc(sourceKey({ document: doc.id, photo: i }))}" style="top:${grid[0] + i * grid[1]}%;height:${grid[2]}%" title="Unverified transcription: ${esc(line)}"></i>`).join('')}</div><p class="pl-intake-meta pl-intake-caution">Photo readings are unverified transcriptions: the model's reading of handwriting, not checked OCR.</p>`;
        } else if (doc.kind === 'sheet') {
          body = `<div class="pl-intake-grid-scroll"><table class="pl-intake-grid"><tbody>${doc.grid.map((row, y) => `<tr><th>${y + 1}</th>${row.map((value, x) => `<td data-source="${esc(sourceKey({ document: doc.id, cell: String.fromCharCode(65 + x) + (y + 1) }))}" title="${esc(value)}">${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
        } else body = `<div class="pl-intake-mail">${markedText(doc)}</div>`;
        return `<article class="pl-intake-paper pl-intake-paper-${doc.kind}" data-paper="${index}" title="${esc(doc.label)}"><header><b>${esc(doc.title)}</b>${chip(doc.kind === 'photo' ? 'Synthetic · generated picture' : doc.label, 'synthetic')}</header>${body}</article>`;
      }
      const comparisonNames = summary.table ? summary.table.head.slice(1).join(' / ') : '';
      const comparisonRow = summary.table && (summary.table.rows.find((row) => row[0] === 'Fields') || summary.table.rows[0]);
      const comparisonSummary = comparisonRow ? `${comparisonRow[0]}: ${comparisonRow.slice(1).join(' vs ')}` : summary.lines[0] || summary.headline;
      const comparisonDetails = `<section class="pl-intake-measured">${summary.table ? `<table><thead><tr>${summary.table.head.map((head) => `<th>${esc(head)}</th>`).join('')}</tr></thead><tbody>${summary.table.rows.map((row) => `<tr>${row.map((value, i) => `<${i ? 'td' : 'th'}>${esc(value)}</${i ? 'td' : 'th'}>`).join('')}</tr>`).join('')}</tbody></table><p class="pl-intake-meta">${esc(summary.table.scope)}</p>` : summary.lines.map((line) => `<p class="pl-intake-meta">${esc(line)}</p>`).join('')}<p class="pl-intake-meta">${esc(summary.scope)}</p></section>`;
      sheet.innerHTML = `<header class="pl-intake-sheet-head"><p class="pl-intake-meta pl-intake-source-status">${documents.length} source documents · ${esc(sampleLabel)}</p><button type="button" class="pl-intake-button pl-intake-close" data-action="close">Close papers</button></header><div class="pl-intake-documents">${documents.map(paper).join('')}</div><div class="pl-intake-evidence" hidden></div>`;
      panel.innerHTML = `<section class="pl-intake-panel"><div class="pl-intake-provenance">${chip(d.label, 'recorded')}${chip(d.who.replace(' · Meta Model API', ''), 'spark')}</div><p class="pl-intake-scope">${esc(separateNote)}</p><div class="pl-intake-files" aria-label="Source documents">${documents.map((doc, i) => `<button type="button" class="pl-intake-file" data-action="documents" data-file="${i}"><span class="pl-intake-file-kind">${doc.kind === 'photo' ? 'Photo' : doc.kind === 'sheet' ? 'CSV' : 'Email'}</span><span class="pl-intake-file-info"><b>${esc(doc.title)}</b><span class="pl-intake-meta">${esc(doc.label)}</span></span><span class="pl-intake-file-state" data-file-state="${i}">Inspect</span></button>`).join('')}</div><button type="button" class="pl-intake-button pl-intake-primary pl-intake-import" data-action="import" data-primary>Import cargo papers</button><p class="pl-intake-meta pl-intake-progress" hidden aria-live="polite"></p><div class="pl-intake-load-summary" hidden><p class="pl-intake-meta pl-intake-load-count"></p><button type="button" class="pl-intake-button pl-intake-toggle" data-action="loadlist" aria-expanded="false" aria-controls="pl-intake-sample-table">View load list</button></div><div class="pl-intake-load-table" id="pl-intake-sample-table" hidden><table aria-label="Separate sample voyage load list"><thead><tr><th>Box</th><th>Cargo</th><th>POD</th><th>Review</th></tr></thead><tbody></tbody></table></div><section class="pl-intake-question" hidden aria-live="polite"></section><section class="pl-intake-ready" hidden><b class="pl-intake-ready-label"></b><p class="pl-intake-meta">${esc(summary.headline)}</p></section><p class="pl-intake-meta pl-intake-comparison-summary" title="${esc(comparisonNames)}">${esc(comparisonSummary)}</p><button type="button" class="pl-intake-button pl-intake-details-button" data-action="details">Details</button></section>`;
      const details = document.createElement('section');
      details.className = 'pl-intake-sheet pl-intake-details-sheet';
      details.setAttribute('aria-label', 'Cargo intake details');
      details.innerHTML = `<header class="pl-intake-sheet-head"><b>Details</b><button type="button" class="pl-intake-button" data-action="close-details">Close details</button></header><div class="pl-intake-details-body"><p>${esc(d.who)} · <code>${esc(d.model)}</code></p><p>${esc(d.derived_note)}</p><p>${esc(d.derived_detail)}</p><p>${d.wall_s ? `One recorded read took ${esc(d.wall_s.toFixed(1))} s · ` : ''}${d.document_count} documents read · ${documents.length} shown</p></div>${comparisonDetails}`;
      const $ = (selector) => panel.querySelector(selector);
      const card = $('.pl-intake-question'), importButton = $('.pl-intake-import');
      const loadTable = $('.pl-intake-load-table'), loadToggle = $('.pl-intake-toggle');
      const sourceEls = [...sheet.querySelectorAll('[data-source]')];
      const current = () => questions.findIndex((_, i) => !answered.has(i));
      function showPapers(index) {
        if (!active) return;
        ctx.sheet(sheet);
        if (index != null) sheet.querySelector(`[data-paper="${index}"]`).scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
      panel.querySelectorAll('[data-file]').forEach((button) => { button.onclick = () => showPapers(Number(button.dataset.file)); });
      sheet.querySelector('.pl-intake-close').onclick = () => ctx.sheet(null);
      $('.pl-intake-details-button').onclick = () => { if (active) ctx.sheet(details); };
      details.querySelector('[data-action="close-details"]').onclick = () => ctx.sheet(null);
      loadToggle.onclick = () => {
        loadTable.hidden = !loadTable.hidden;
        loadToggle.setAttribute('aria-expanded', String(!loadTable.hidden));
        loadToggle.textContent = loadTable.hidden ? 'View load list' : 'Hide load list';
      };
      function highlight(spots, kind) {
        const lit = new Set(spots.map(sourceKey));
        sourceEls.forEach((el) => { el.dataset.highlight = lit.has(el.dataset.source) ? kind : ''; });
      }
      function loadRows() {
        return d.rows.slice(0, filled).map((row, y) => {
          const related = questions.map((question, i) => ({ question, i })).filter(({ question }) => question.row === y);
          const unresolved = related.filter(({ i }) => !answered.has(i));
          const cells = { ...row.before };
          d.columns.forEach((column) => {
            if (complete || related.some(({ question, i }) => question.column === column && answered.has(i))) cells[column] = row.after[column];
          });
          const mass = String(cells.mass).replace(/,/g, '');
          const weight = /^\d+(?:\.\d+)?$/.test(mass) ? Number(mass) / 1000 : null;
          const iso = String(cells.iso || '');
          const size = iso.startsWith('2') ? 20 : iso.startsWith('4') ? 40 : iso.startsWith('L') ? 45 : null;
          const reefer = cells.reefer === '?' ? null : !!cells.reefer && cells.reefer !== 'none';
          return {
            ...cells, id: row.id, box_id: row.id, before: { ...row.before }, after: { ...row.after },
            size_ft: size, weight_t: weight, pod: cells.pod, dg: cells.dg, dg_class: null,
            reefer, reefer_display: cells.reefer, derived: [...(row.derived || [])],
            status: unresolved.length ? 'sample · question' : 'sample · ready',
            review_status: unresolved.length ? 'question' : 'ready', questions: unresolved.map(({ question }) => question.text),
            label: sampleLabel, voyage_label: sampleLabel, separate_demo: true,
          };
        });
      }
      function renderLoadList(rows) {
        $('.pl-intake-load-summary').hidden = rows.length === 0;
        $('.pl-intake-load-count').textContent = `${rows.length} sample boxes · ${questions.length - answered.size} reviews left`;
        loadTable.querySelector('tbody').innerHTML = rows.map((row) => {
          const size = row.size_ft ? `${row.size_ft}′` : row.iso;
          const weight = `${row.mass} kg`;
          const flags = [row.dg && row.dg !== 'none' ? row.dg : '', row.reefer_display && row.reefer_display !== 'none' ? `RF ${row.reefer_display}` : ''].filter(Boolean).join(' · ');
          return `<tr data-load-row="${esc(row.box_id)}" title="${esc(row.label)}"><td title="${esc(row.box_id)}">${esc(row.box_id)}</td><td title="${esc([row.iso, weight, row.dg, row.reefer_display].join(' · '))}"><span>${esc(size)} · ${esc(weight)}</span>${flags ? `<span class="pl-intake-meta">${esc(flags)}</span>` : ''}</td><td title="${esc(row.pod)}">${esc(row.pod)}</td><td title="${esc(row.status)}"><span class="pl-intake-meta">sample</span><span data-review="${esc(row.review_status)}">${row.review_status === 'question' ? '? question' : '✓ ready'}</span></td></tr>`;
        }).join('');
      }
      function publishRows() {
        const rows = loadRows();
        ctx.loadlist.setRows(rows, { title: 'Demo load list', note: separateNote });
        renderLoadList(rows);
        $('.pl-intake-progress').textContent = `${filled} of ${d.rows.length} sample rows · ${questions.length - answered.size} reviews left`;
        return rows;
      }
      function finish() {
        if (complete) return;
        complete = true;
        ctx.state.loadlist = publishRows();
        card.hidden = true; $('.pl-intake-progress').hidden = true;
        highlight([], '');
        sheet.querySelector('.pl-intake-evidence').hidden = true;
        sheet.querySelector('.pl-intake-source-status').textContent = summary.headline;
        const ready = $('.pl-intake-ready'); ready.hidden = false;
        ready.querySelector('.pl-intake-ready-label').textContent = `Demo load list ready · ${d.rows.length} boxes (separate demo)`;
        ctx.log({ who: 'Planner', where: null, text: summary.headline, label: d.label });
        ctx.emit('loadlist:ready', ctx.state.loadlist);
        ctx.done();
      }
      function showQuestion() {
        const i = current();
        if (i < 0) { finish(); return; }
        const question = questions[i];
        card.hidden = false;
        card.innerHTML = `<header class="pl-intake-meta"><span>${question.scope === 'document' ? 'Whole-document review' : 'Planner review'}</span><span>${questions.length - answered.size} left</span></header><p>${esc(question.text)}</p><div class="pl-intake-options">${question.options.map((option, n) => `<button type="button" class="pl-intake-button ${option.recorded ? 'pl-intake-primary' : ''}" data-action="answer" data-option="${n}">${esc(unit(question, option.label))}</button>`).join('')}</div><p class="pl-intake-meta">Synthetic planner answer · ${esc(d.label)}</p><p class="pl-intake-message" hidden></p>`;
        highlight(question.spots, 'ask');
        const evidence = sheet.querySelector('.pl-intake-evidence');
        const extra = question.spots.filter((spot) => !documents.some((doc) => doc.id === spot.document) && spot.quote);
        evidence.hidden = !extra.length;
        evidence.innerHTML = extra.map((spot) => `<blockquote>${esc(spot.quote)}</blockquote>`).join('');
        sheet.querySelector('.pl-intake-source-status').textContent = question.text;
      }
      card.onclick = (event) => {
        const button = event.target.closest('[data-option]');
        if (!button || !active) return;
        const i = current(); if (i < 0) return;
        const question = questions[i], option = question.options[Number(button.dataset.option)];
        if (!option.recorded) {
          const message = card.querySelector('.pl-intake-message'); message.hidden = false;
          message.textContent = `This take only has a recording for ${unit(question, question.after)}. Recorded mode makes no calls, so that answer can't be checked here.`;
          return;
        }
        answered.add(i); publishRows(); showQuestion();
      };
      async function importPapers() {
        const token = ++generation;
        importButton.hidden = true;
        $('.pl-intake-progress').hidden = false;
        filled = 0; publishRows(); showPapers();
        ctx.log({ who: 'Muse Spark', where: 'cloud', text: `Read ${d.document_count} synthetic documents · ${d.model}`, label: d.label });
        for (let i = 0; i < documents.length; i++) {
          if (!active || token !== generation) return;
          const doc = documents[i];
          $(`[data-file-state="${i}"]`).textContent = 'Reading';
          sheet.querySelector('.pl-intake-source-status').textContent = `${d.who} · ${doc.title}`;
          sheet.querySelectorAll('[data-paper]').forEach((el) => { el.dataset.reading = Number(el.dataset.paper) === i ? 'yes' : ''; });
          highlight(d.flights.filter((flight) => flight.document === doc.id), 'read');
          await ctx.wait(1000);
          if (!active || token !== generation) return;
          $(`[data-file-state="${i}"]`).textContent = 'Read';
        }
        for (let y = 0; y < d.rows.length; y++) {
          if (!active || token !== generation) return;
          filled = y + 1; publishRows(); await ctx.wait(90);
        }
        if (!active || token !== generation) return;
        imported = true;
        sheet.querySelectorAll('[data-paper]').forEach((el) => { el.dataset.reading = ''; });
        showQuestion();
      }
      function startImport() {
        if (imported || !active) return Promise.resolve();
        if (!pending) {
          const run = importPapers(); pending = run;
          run.then(() => { if (pending === run) pending = null; }, () => { if (pending === run) pending = null; });
        }
        return pending;
      }
      importButton.onclick = startImport;
      return {
        enter() {
          active = true;
          if (!imported && !pending) {
            filled = 0; importButton.hidden = false; $('.pl-intake-progress').hidden = true;
            panel.querySelectorAll('[data-file-state]').forEach((el) => { el.textContent = 'Inspect'; });
          }
          if (imported) publishRows();
          if (complete) ctx.done();
        },
        async start() { await startImport(); },
        leave() { active = false; generation++; if (!imported) pending = null; ctx.sheet(null); },
        async autoplay(auto) {
          await auto.wait(1000);
          if (!active) return;
          if (pending) await pending;
          else if (!imported) { await auto.click(importButton); if (pending) await pending; }
          while (active && !complete) {
            await auto.wait(1700);
            if (!active) return;
            const button = card.querySelector('.pl-intake-primary');
            if (!button) break;
            await auto.click(button);
          }
          await auto.wait(1700);
        },
      };
    },
  });
})();
