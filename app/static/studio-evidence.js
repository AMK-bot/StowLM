/* Recorded evaluation views. Counts and claim wording come from studio_data.py. */
(function () {
  'use strict';
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const sentence = value => String(value == null ? '' : value).trim().replace(/\.$/, '').replace(/^./, letter => letter.toUpperCase());
  const pct = value => (value * 100).toFixed(1).replace(/\.0$/, '') + '%';
  const groups = card => card.variants ? ['original', 'extension'].map(key => ({key, rows: card.variants[key]})) : [{key: 'single', rows: card.series}];
  const glyphs = {
    voyages: '<path d="M5 12h22l-3 9H9zM11 12V6h10v6M7 26h18"/>',
    reader: '<path d="M6 7h9l2 2 2-2h9v19h-9l-2 2-2-2H6zM17 9v19M10 12h3M10 17h3M21 12h3M21 17h3"/>',
    mask: '<path d="M17 4l11 4v9c0 6-6 10-11 13C12 27 6 23 6 17V8zM12 17l4 4 7-8"/>',
    binding: '<path d="M12 21l-2 2a6 6 0 01-8-8l6-6a6 6 0 018 0M22 13l2-2a6 6 0 018 8l-6 6a6 6 0 01-8 0M11 23l12-12"/>',
    ood: '<circle cx="17" cy="17" r="12"/><path d="M5 17h24M17 5c-8 7-8 17 0 24M17 5c8 7 8 17 0 24"/>'
  };
  const icon = (id, className) => '<svg class="' + className + '" viewBox="0 0 34 34" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' + (glyphs[id] || glyphs.reader) + '</svg>';
  function sourceText(figure) {
    const interval = figure.interval ? '95% interval ' + figure.interval.map(pct).join('–') : 'No interval reported';
    const sources = [figure.source, figure.n_source, figure.interval_source].filter(Boolean);
    return 'n = ' + figure.n + ' · ' + interval + '\n' + Array.from(new Set(sources.map(source => source.path + ' · ' + source.field))).join('\n');
  }
  function row(figure, index) {
    const x = value => 2 + Math.max(0, Math.min(1, value)) * 196;
    const rate = figure.n ? figure.value / figure.n : 0;
    let interval = '';
    if (figure.interval) {
      const [lo, hi] = figure.interval.map(x);
      interval = '<path d="M' + lo + ' 6V16M' + lo + ' 11H' + hi + 'M' + hi + ' 6V16" fill="none" stroke="currentColor" stroke-width="1"/>';
    }
    const marker = index % 2 ? '<rect x="' + (x(rate) - 2.5) + '" y="8.5" width="5" height="5" fill="currentColor"/>' : '<circle cx="' + x(rate) + '" cy="11" r="2.8" fill="currentColor"/>';
    return '<div class="st-evidence-row" data-evidence-row tabindex="0"><span class="st-meta">' + esc(figure.label) + '</span>' +
      '<svg class="st-evidence-plot" viewBox="0 0 200 22" role="img" aria-label="' + esc(figure.label + ': ' + figure.value + ' of ' + figure.n + (figure.interval ? ', 95% interval ' + figure.interval.map(pct).join(' to ') : '')) + '">' +
      '<path d="M2 11H198" stroke="currentColor" stroke-opacity=".1" stroke-width="5"/><path d="M2 11H' + x(rate) + '" stroke="currentColor" stroke-opacity=".25" stroke-width="5"/>' + interval + marker + '</svg>' +
      '<span class="st-evidence-count">' + figure.value + '<span class="st-meta"> / ' + figure.n + '</span></span></div>';
  }
  window.Studio.step({
    id: 'evidence', order: 70, title: 'Evaluation', icon: 'evidence', features: [], ship: false,
    mount(panel, _data, ctx) {
      const root = document.createElement('section');
      root.className = 'st-evidence-root';
      root.setAttribute('aria-label', 'Measured evaluation results');
      panel.appendChild(root);
      let data;
      let focus = 'original';
      let starting;
      let autoplaying = false;
      function closeDetails() { root.querySelectorAll('details').forEach(el => { el.open = false; el.dataset.pinned = 'false'; }); }
      function details(card) {
        return '<details class="st-disclosure st-evidence-details"><summary>Details</summary><div class="st-evidence-reading">' +
          '<p class="st-card-sub"><span class="st-card-title">What this shows</span><br>' + esc(card.variant_shows ? card.variant_shows[focus] : card.show) + '</p>' +
          '<p class="st-card-sub"><span class="st-card-title">What it doesn’t show</span><br>' + esc(card.does_not) + '</p>' +
          groups(card).concat(card.supplemental ? [{rows: card.supplemental}] : []).map(group => group.rows.map(figure =>
            '<div class="st-evidence-source"><p class="st-meta">' + esc(figure.label) + ' · ' + figure.value + ' / ' + figure.n + '</p><pre class="st-meta st-mono">' + esc(sourceText(figure)) + '</pre></div>').join('')).join('') +
          (card.variants ? '<p class="st-meta">' + esc(data.labels.partial_run) + '</p>' : '') + '</div></details>';
      }
      function render() {
        root.innerHTML = '<header class="st-page-header st-evidence-header"><svg class="st-page-icon" width="64" height="64" viewBox="0 0 64 64" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="32" cy="32" r="30"/><path d="M20 43V31M29 43V21M38 43V27M47 43H17"/></svg>' +
          '<h1 class="st-page-title">Evaluation</h1><p class="st-page-subtitle">Measured results, with the comparison and its limits</p></header>' +
          '<div class="st-evidence-toggle" role="group" aria-label="Choose a held-out run to read"><button type="button" class="st-btn ' + (focus === 'original' ? 'primary' : 'secondary') + '" data-evidence-focus="original" aria-pressed="' + (focus === 'original') + '">3.1 held-out run</button><button type="button" class="st-btn ' + (focus === 'extension' ? 'primary' : 'secondary') + '" data-evidence-focus="extension" aria-pressed="' + (focus === 'extension') + '">Fresh extension</button></div>' +
          '<p class="st-meta st-evidence-envelope">' + esc(sentence(data.envelope)) + '</p><div class="st-grid2 st-evidence-grid">' + data.cards.map(card =>
            '<article class="st-card big st-evidence-card" data-evidence-card="' + esc(card.id) + '" tabindex="0" aria-label="View details for ' + esc(card.title) + '"><div class="st-evidence-card-heading">' + '<span class="st-tile st-evidence-icon-tile st-evidence-icon-' + (['reader', 'mask', 'ood'].includes(card.id) ? 'reader' : 'checker') + '" aria-hidden="true">' + icon(card.id, 'st-evidence-card-icon') + '</span>' +
            '<div><h2 class="st-card-title">' + esc(card.title) + '</h2><p class="st-card-sub">' + (card.variant_shows ? '3.1: ' : '') + esc(sentence(card.show)) + '</p></div></div>' +
            '<div class="st-evidence-figures">' + groups(card).map(group => '<div class="st-evidence-group">' +
              (group.key === 'single' ? '<p class="st-meta">' + esc(sentence(card.label)) + '</p>' : '<p class="st-meta">' + (group.key === 'original' ? '3.1 · original held-out run' : 'Fresh held-out extension') + (group.key === focus ? ' <span class="st-chip">Reading this run</span>' : '') + '</p><p class="st-meta">' + esc(sentence(data.labels[group.key])) + '</p>') +
              group.rows.map(row).join('') + '</div>').join('') + '</div>' +
            (card.id === 'voyages' ? '<p class="st-meta">' + esc(sentence(data.labels.baseline)) + '</p>' : '') +
            (card.variants ? '<p class="st-meta">' + esc(sentence(data.labels.partial_run_short)) + '</p>' : '') + details(card) + '</article>').join('') +
          '</div><footer class="st-meta st-evidence-footer">' + esc(sentence(data.scope)) + '</footer>';
        root.querySelectorAll('[data-evidence-focus]').forEach(button => button.addEventListener('click', () => { focus = button.dataset.evidenceFocus; render(); }));
        root.querySelectorAll('[data-evidence-card]').forEach(card => {
          const disclosure = card.querySelector('details');
          const summary = disclosure.querySelector('summary');
          function setOpen(open, pinned) {
            if (autoplaying) { disclosure.open = false; disclosure.dataset.pinned = 'false'; return; }
            disclosure.open = open;
            if (pinned !== undefined) disclosure.dataset.pinned = String(pinned);
          }
          function toggleCard() {
            const open = !disclosure.open || disclosure.dataset.pinned !== 'true';
            setOpen(open, open);
          }
          function isInteractive(target) {
            for (let node = target; node && node !== card; node = node.parentNode) {
              if (['a', 'button', 'input', 'select', 'textarea', 'summary'].includes(String(node.tagName).toLowerCase()) || node.isContentEditable) return true;
            }
            return false;
          }
          card.addEventListener('mouseenter', () => { setOpen(true); });
          card.addEventListener('mouseleave', () => { if (disclosure.dataset.pinned !== 'true') setOpen(false); });
          card.addEventListener('click', event => {
            if (!isInteractive(event.target)) toggleCard();
          });
          card.addEventListener('keydown', event => {
            if (event.target && event.target !== card) return;
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleCard(); }
          });
          summary.addEventListener('click', event => {
            event.preventDefault();
            setOpen(!disclosure.open, !disclosure.open);
          });
          card.querySelectorAll('[data-evidence-row]').forEach(el => el.addEventListener('focus', () => { setOpen(true); }));
        });
      }
      function start() {
        if (!starting) starting = Promise.resolve(ctx.data('evidence')).then(payload => {
          data = payload;
          render();
          ctx.log({who: 'Software', where: null, text: 'Opened the measured evaluation and its source records.', label: 'Recorded results'});
          ctx.done();
        });
        return starting;
      }
      return {
        start,
        async enter() { ctx.center(null); await start(); },
        leave() { closeDetails(); },
        async autoplay(auto) {
          await start();
          autoplaying = true;
          try {
            closeDetails();
            await auto.wait(1800);
            for (const id of ['voyages', 'reader']) {
              const card = root.querySelector('[data-evidence-card="' + id + '"]');
              card.dispatchEvent(new Event('mouseenter'));
              await auto.wait(4200);
              card.dispatchEvent(new Event('mouseleave'));
              closeDetails();
            }
            await auto.click(root.querySelector('[data-evidence-focus="extension"]'));
            await auto.wait(4000);
          } finally {
            closeDetails();
            autoplaying = false;
          }
        }
      };
    }
  });
}());
