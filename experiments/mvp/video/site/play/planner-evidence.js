/* Evaluation report over recorded evidence; no model or planner runs here. */
(function () {
  'use strict';
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const pct = value => (value * 100).toFixed(1).replace(/\.0$/, '') + '%';
  const groups = card => card.variants ? ['original', 'extension'].map(key => ({key, rows: card.variants[key]})) : [{key: 'single', rows: card.series}];
  function sourceText(figure) {
    const interval = figure.interval ? 'Exact 95% interval: ' + figure.interval.map(pct).join('–') : 'No interval reported';
    const records = [figure.source, figure.n_source, figure.interval_source].filter(Boolean);
    return 'n = ' + figure.n + ' · ' + interval + '\n' + Array.from(new Set(records.map(source => source.path + ' · ' + source.field))).join('\n');
  }
  function plotRow(figure, index) {
    const x = value => 2 + Math.max(0, Math.min(1, value)) * 196;
    const rate = figure.n ? figure.value / figure.n : 0;
    let whisker = '';
    if (figure.interval) {
      const [lo, hi] = figure.interval.map(x);
      whisker = '<path d="M' + lo + ' 5V17M' + lo + ' 11H' + hi + 'M' + hi + ' 5V17" stroke="currentColor" stroke-width="1.2" fill="none"/>';
    }
    const marker = index % 2 ? '<rect x="' + (x(rate) - 2.5) + '" y="8.5" width="5" height="5" fill="currentColor"/>' : '<circle cx="' + x(rate) + '" cy="11" r="2.8" fill="currentColor"/>';
    return '<div class="pl-evidence-row"><span>' + esc(figure.label) + '</span><svg class="pl-evidence-plot" viewBox="0 0 200 22" role="img" aria-label="' +
      esc(figure.label + ': ' + figure.value + ' of ' + figure.n + (figure.interval ? ', exact 95% interval ' + figure.interval.map(pct).join(' to ') : '')) + '">' +
      '<path d="M2 11H198" stroke="currentColor" stroke-opacity=".1" stroke-width="5"/><path d="M2 11H' + x(rate) + '" stroke="currentColor" stroke-opacity=".25" stroke-width="5"/>' + whisker + marker + '</svg>' +
      '<span class="pl-evidence-count">' + figure.value + '<span> / ' + figure.n + '</span></span></div>';
  }
  window.Planner.stage({
    id: 'evidence', order: 70, title: 'Evaluation', features: [], ship: false,
    mount(panel, _data, ctx) {
      const root = document.createElement('section');
      root.className = 'pl-evidence-report';
      root.setAttribute('aria-label', 'Evaluation report');
      panel.appendChild(root);
      let evidence;
      let focus = 'original';
      let starting;
      let autoplaying = false;
      function closeDetails() { ctx.sheet(null); }
      function openDetails(card) {
        if (autoplaying) return;
        const drawer = document.createElement('section');
        drawer.className = 'pl-evidence-drawer';
        drawer.setAttribute('aria-label', card.title + ' details');
        drawer.innerHTML = '<header class="pl-evidence-drawer-heading"><div><p class="pl-evidence-kicker">Recorded results</p><h2>' + esc(card.title) + '</h2></div><button type="button" class="pl-evidence-button" data-pl-evidence-close>Close</button></header>' +
          '<section class="pl-evidence-reading"><h3>What this shows</h3><p>' + esc(card.variant_shows ? card.variant_shows[focus] : card.show) + '</p><h3>What it doesn’t show</h3><p>' + esc(card.does_not) + '</p></section>' +
          groups(card).map(group => '<section class="pl-evidence-source-group"><h3>' + (group.key === 'single' ? esc(card.label) : esc(evidence.labels[group.key])) + '</h3>' + group.rows.map(figure =>
            '<div class="pl-evidence-source"><p>' + esc(figure.label) + ' · ' + figure.value + ' / ' + figure.n + '</p><pre>' + esc(sourceText(figure)) + '</pre></div>').join('') + '</section>').join('') +
          (card.supplemental ? '<section class="pl-evidence-source-group"><h3>Additional checks</h3>' + card.supplemental.map(figure => '<div class="pl-evidence-source"><p>' + esc(figure.label) + ' · ' + figure.value + ' / ' + figure.n + '</p><pre>' + esc(sourceText(figure)) + '</pre></div>').join('') + '</section>' : '') +
          (card.variants ? '<section class="pl-evidence-reading"><h3>Published run history</h3><p>' + esc(evidence.labels.partial_run) + '</p></section>' : '') + '<p class="pl-evidence-scope">' + esc(evidence.scope) + '</p>';
        drawer.querySelector('[data-pl-evidence-close]').addEventListener('click', closeDetails);
        ctx.sheet(drawer);
      }
      function render() {
        root.innerHTML = '<header class="pl-evidence-toolbar"><div><h1>Evaluation</h1><p>Recorded measurements · comparisons and limits</p></div><div class="pl-evidence-toggle" role="group" aria-label="Choose the run to inspect"><button type="button" class="pl-evidence-button" data-pl-evidence-focus="original" aria-pressed="' + (focus === 'original') + '">3.1 held-out run</button><button type="button" class="pl-evidence-button" data-pl-evidence-focus="extension" aria-pressed="' + (focus === 'extension') + '">Fresh extension</button></div></header>' +
          '<p class="pl-evidence-envelope">' + esc(evidence.envelope) + '</p><div class="pl-evidence-grid">' + evidence.cards.map(card =>
            '<article class="pl-evidence-card pl-evidence-card-' + esc(card.id) + '" data-pl-evidence-card="' + esc(card.id) + '"><header class="pl-evidence-card-heading"><h2>' + esc(card.title) + '</h2><button type="button" class="pl-evidence-details-button" data-pl-evidence-details="' + esc(card.id) + '">Details</button></header>' +
            '<div class="pl-evidence-groups">' + groups(card).map(group => '<section class="pl-evidence-group"><p class="pl-evidence-run-title">' + (group.key === 'single' ? esc(card.label) : (group.key === 'original' ? '3.1 · original held-out run' : 'Fresh held-out extension') + (group.key === focus ? '<span class="pl-evidence-inspecting">Inspecting</span>' : '')) + '</p>' +
              (group.key !== 'single' ? '<p class="pl-evidence-label">' + esc(evidence.labels[group.key]) + '</p>' : '') + group.rows.map(plotRow).join('') + '</section>').join('') + '</div>' +
            '<p class="pl-evidence-reading-note">' + (card.variant_shows ? '3.1: ' : '') + esc(card.show) + '</p>' +
            (card.id === 'voyages' ? '<p class="pl-evidence-label">' + esc(evidence.labels.baseline) + '</p>' : '') +
            (card.variants ? '<p class="pl-evidence-run-history">' + esc(evidence.labels.partial_run_short) + '</p>' : '') + '</article>').join('') + '</div><footer class="pl-evidence-footer"><span>Points show observed rates; whiskers show reported 95% intervals</span><span>' + esc(evidence.scope) + '</span></footer>';
        root.querySelectorAll('[data-pl-evidence-focus]').forEach(button => button.addEventListener('click', () => {
          focus = button.dataset.plEvidenceFocus;
          closeDetails();
          render();
        }));
        root.querySelectorAll('[data-pl-evidence-details]').forEach(button => button.addEventListener('click', () => openDetails(evidence.cards.find(card => card.id === button.dataset.plEvidenceDetails))));
        root.querySelectorAll('[data-pl-evidence-card]').forEach(card => {
          card.addEventListener('mouseenter', () => card.classList.add('pl-evidence-hover'));
          card.addEventListener('mouseleave', () => card.classList.remove('pl-evidence-hover'));
        });
      }
      function start() {
        if (!starting) starting = Promise.resolve(ctx.data('evidence')).then(payload => {
          evidence = payload;
          render();
          ctx.log({who: 'Software', where: null, text: 'Opened the evaluation report and its recorded measurements.', label: 'Recorded results'});
          ctx.done();
        });
        return starting;
      }
      return {
        start,
        async enter() { await start(); },
        leave() { closeDetails(); root.querySelectorAll('.pl-evidence-hover').forEach(card => card.classList.remove('pl-evidence-hover')); },
        async autoplay(auto) {
          await start();
          autoplaying = true;
          try {
            closeDetails();
            await auto.wait(1800);
            for (const id of ['voyages', 'reader']) {
              const card = root.querySelector('[data-pl-evidence-card="' + id + '"]');
              card.dispatchEvent(new Event('mouseenter'));
              await auto.wait(4200);
              card.dispatchEvent(new Event('mouseleave'));
            }
            await auto.click(root.querySelector('[data-pl-evidence-focus="extension"]'));
            await auto.wait(4000);
          } finally {
            closeDetails();
            autoplaying = false;
            root.querySelectorAll('.pl-evidence-hover').forEach(card => card.classList.remove('pl-evidence-hover'));
          }
        }
      };
    }
  });
}());
