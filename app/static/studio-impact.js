(function (root) {
  'use strict';

  const value = (field) => typeof field === 'number' ? field : field.value;

  // Operation order mirrors impact_v6.py:_per_call and calculate (pilot branch).
  // Keep unrounded arithmetic throughout; formatting is the only rounding step.
  function project(inputs) {
    const v = Object.fromEntries(Object.entries(inputs).map(([key, field]) => [key, value(field)]));
    const rehandles = v.primary_moves_per_call * v.extra_rehandles_per_primary_move;
    const avoided = rehandles * v.rehandle_reduction;
    const initial = v.initial_baseline_hours - v.initial_assisted_hours;
    const changes = v.instruction_events_per_call * (v.instruction_baseline_minutes - v.instruction_assisted_minutes) / 60;
    const rework = v.rework_events_avoided_per_call * v.rework_minutes_per_event / 60;
    const plannerHours = initial + changes + rework;
    const handling = avoided * v.move_charge_usd;
    const labor = plannerHours * v.planner_usd_per_hour;
    const per_call = {
      baseline_extra_rehandles: rehandles,
      avoided_extra_rehandles: avoided,
      initial_planner_hours_saved: initial,
      instruction_planner_hours_saved: changes,
      rework_planner_hours_saved: rework,
      total_planner_hours_saved: plannerHours,
      handling_charge_equivalent_usd: handling,
      planner_labor_equivalent_usd: labor,
      total_equivalent_usd: handling + labor,
    };
    return { annual_calls: v.annual_calls, per_call,
      annual: Object.fromEntries(Object.entries(per_call).map(([key, amount]) => [key, amount * v.annual_calls])) };
  }

  const api = { project };
  root.StudioImpact = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (!root.Studio) return;

  const fields = [
    ['annual_calls', 'Eligible adopting calls / year', 'count'],
    ['primary_moves_per_call', 'Productive load lifts / call', 'count'],
    ['extra_rehandles_per_primary_move', 'Extra rehandles / productive lift', 'percent'],
    ['rehandle_reduction', 'Reduction in extra lifts', 'percent'],
    ['planner_usd_per_hour', 'Planner opportunity value / hour', 'money'],
    ['move_charge_usd', 'Charge proxy / avoided lift', 'money'],
    ['initial_baseline_hours', 'Initial attention · baseline hours', 'decimal'],
    ['initial_assisted_hours', 'Initial attention · assisted hours', 'decimal'],
    ['instruction_events_per_call', 'Later instruction changes / call', 'count'],
    ['instruction_baseline_minutes', 'Attention / change · baseline minutes', 'count'],
    ['instruction_assisted_minutes', 'Attention / change · assisted minutes', 'count'],
    ['rework_events_avoided_per_call', 'Exceptional corrections avoided / call', 'decimal'],
    ['rework_minutes_per_event', 'Attention / correction · minutes', 'count'],
  ];
  const number = (n, digits = 0) => n.toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const money = (n, digits = 0) => '$' + number(n, digits);
  const text = (tag, className, content) => {
    const el = document.createElement(tag);
    el.className = className;
    if (content !== undefined) el.textContent = content;
    return el;
  };
  const paths = {
    pilot: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/>',
    sliders: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
    chart: '<path d="M4 19V5M4 19h16M8 15l4-5 4 2 4-7"/>',
    conditions: '<path d="m4 6 2 2 4-4M13 6h7M4 13l2 2 4-4M13 13h7M4 20l2 2 4-4M13 20h7"/>',
  };
  function icon(name, className, size) {
    const span = text('span', className);
    span.innerHTML = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>';
    return span;
  }
  function card(name, title, description) {
    const box = text('section', 'st-card big st-impact-card');
    const heading = text('div', 'st-impact-card-heading');
    const copy = text('div', '');
    copy.append(text('h3', 'st-card-title', title), text('p', 'st-card-sub', description));
    heading.append(icon(name, 'st-impact-card-icon', 36), copy);
    box.append(heading);
    return box;
  }
  function details() {
    const node = text('details', 'st-disclosure st-impact-details');
    node.append(text('summary', '', 'Details'));
    return node;
  }

  root.Studio.step({
    id: 'impact', order: 80, title: 'Forward pilot', icon: 'pilot', features: [], ship: false,
    mount(panel, _features, ctx) {
      const view = text('section', 'st-impact-view');
      const controls = new Map();
      const presetButtons = new Map();
      let dataset;
      let current;
      let active = false;
      let starting;
      let resultAmount;
      let resultRows;
      let resultChart;
      let mode;

      function update() {
        if (!current) return;
        const projected = project(current);
        resultAmount.textContent = money(projected.annual.total_equivalent_usd);
        const handling = projected.per_call.handling_charge_equivalent_usd;
        const labor = projected.per_call.planner_labor_equivalent_usd;
        const total = projected.per_call.total_equivalent_usd;
        const handlingWidth = total > 0 ? handling / total * 320 : 0;
        const laborWidth = total > 0 ? labor / total * 320 : 0;
        resultChart.innerHTML = '<svg viewBox="0 0 322 24" width="100%" height="32" role="img" aria-label="Per-call projected equivalents: handling ' + money(handling, 2) + ', planner attention ' + money(labor, 2) + '"><title>Per-call projection · handling ' + money(handling, 2) + ' · planner attention ' + money(labor, 2) + '</title><rect x="1" y="5" width="' + handlingWidth + '" height="14" fill="currentColor" fill-opacity=".5"/><rect x="' + (1 + handlingWidth) + '" y="5" width="' + laborWidth + '" height="14" fill="none" stroke="currentColor" stroke-width="1"/></svg>';
        const rows = [
          ['Gross equivalent / call', money(projected.per_call.total_equivalent_usd, 2)],
          ['Avoided extra lifts / call', number(projected.per_call.avoided_extra_rehandles, 2)],
          ['Planner attention freed / call', number(projected.per_call.total_planner_hours_saved, 2) + ' h'],
          ['Handling-charge equivalent / call', money(projected.per_call.handling_charge_equivalent_usd, 2)],
          ['Labor opportunity / call', money(projected.per_call.planner_labor_equivalent_usd, 2)],
        ];
        resultRows.replaceChildren(...rows.map(([label, amount]) => {
          const row = text('div', 'st-kv');
          row.append(text('span', '', label), text('span', '', amount));
          return row;
        }));
        for (const [key, control] of controls) {
          const n = current[key];
          control.input.value = n;
          const ratio = control.max === control.min ? 0.5 : Math.max(0, Math.min(1, (n - control.min) / (control.max - control.min)));
          control.input.style.cssText = '--st-impact-range-fill: ' + ratio * 100 + '%;';
          control.wrapper.style.cssText = control.input.style.cssText;
          control.output.textContent = control.format === 'percent' ? number(n * 100, 0) + '%' : control.format === 'money' ? money(n, key === 'move_charge_usd' ? 2 : 0) : number(n, control.format === 'decimal' ? 2 : 0);
        }
      }
      function selectPreset(name) {
        current = Object.fromEntries(Object.entries(dataset.pilot[name].inputs).map(([key, figure]) => [key, value(figure)]));
        mode.textContent = name === 'low' ? 'Low sensitivity · assumptions' : 'Mid sensitivity · assumptions';
        for (const [key, button] of presetButtons) button.setAttribute('aria-pressed', String(key === name));
        update();
      }
      function makeSlider([key, label, format]) {
        const amounts = Object.values(dataset.pilot).map((scenario) => value(scenario.inputs[key]));
        const min = Math.min(...amounts);
        const max = Math.max(...amounts);
        if (min === max) {
          const fixed = text('div', 'st-kv st-impact-fixed');
          fixed.dataset.fixedField = key;
          fixed.append(text('span', '', label), text('span', '', format === 'money' ? money(min, 2) : number(min)));
          return fixed;
        }
        const group = text('label', 'st-impact-control');
        const heading = text('span', 'st-impact-control-heading');
        const output = text('output', 'st-impact-control-value');
        heading.append(text('span', '', label), output);
        const input = document.createElement('input');
        input.type = 'range';
        input.className = 'st-impact-range';
        input.min = min;
        input.max = max;
        input.step = format === 'count' ? '1' : '0.01';
        input.setAttribute('aria-label', label);
        input.dataset.field = key;
        const change = (amount) => {
          current[key] = Math.min(max, Math.max(min, amount));
          mode.textContent = 'Custom sensitivity · assumptions';
          for (const button of presetButtons.values()) button.setAttribute('aria-pressed', 'false');
          update();
        };
        input.addEventListener('input', () => change(Number(input.value)));
        let dragging = false;
        const move = (event) => {
          if (!dragging || min === max) return;
          const rect = input.getBoundingClientRect();
          const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
          const step = Number(input.step);
          change(min + Math.round(fraction * (max - min) / step) * step);
        };
        input.addEventListener('pointerdown', () => { dragging = true; });
        input.addEventListener('mousedown', () => { dragging = true; });
        const up = () => { dragging = false; };
        root.addEventListener('pointermove', move);
        root.addEventListener('mousemove', move);
        root.addEventListener('pointerup', up);
        root.addEventListener('mouseup', up);
        const wrapper = text('span', 'st-impact-range-wrap');
        wrapper.append(input);
        group.append(heading, wrapper);
        controls.set(key, { input, wrapper, output, min, max, format });
        return group;
      }
      function sourceDetails() {
        const disclosure = details();
        const model = dataset.model;
        disclosure.append(text('p', 'st-meta', model.path + ' · ' + model.function));
        const list = text('ul', 'st-impact-sources');
        for (const [key, field] of Object.entries(dataset.pilot.low.inputs)) {
          list.append(text('li', 'st-meta', key + ': ' + (field.note || field.label) + ' Source: ' + field.source.path + ' · ' + field.source.field));
        }
        for (const field of [dataset.global.exposure, dataset.global.prize_mid, dataset.global.prize_low]) {
          list.append(text('li', 'st-meta', field.label + ': ' + field.source.path + ' · ' + field.source.field));
        }
        disclosure.append(list);
        return disclosure;
      }
      function render(data) {
        dataset = data;
        const grid = text('div', 'st-grid2 st-impact-grid');
        const inputs = card('sliders', 'Pilot portfolio', 'Explore an assumed portfolio of eligible adopting calls.');
        inputs.append(text('p', 'st-meta', 'Projection, not a result'));
        const toolbar = text('div', 'st-impact-toolbar');
        mode = text('span', 'st-meta');
        const presets = text('div', 'st-impact-presets');
        for (const name of ['low', 'mid']) {
          const button = text('button', 'st-btn secondary', name === 'low' ? 'Low' : 'Mid');
          button.type = 'button';
          button.addEventListener('click', () => selectPreset(name));
          presets.append(button);
          presetButtons.set(name, button);
        }
        toolbar.append(mode, presets);
        inputs.append(toolbar);
        fields.slice(0, 6).forEach((field) => inputs.append(makeSlider(field)));
        const workflow = text('details', 'st-disclosure');
        workflow.append(text('summary', '', 'Planner workflow assumptions'));
        fields.slice(6).forEach((field) => workflow.append(makeSlider(field)));
        inputs.append(workflow);

        const results = card('chart', 'Annual pilot projection', 'Gross opportunity equivalent from handling charges and carrier planner attention.');
        resultAmount = text('div', 'st-big st-impact-amount');
        results.append(resultAmount, text('p', 'st-meta', 'Gross equivalent / year · projection'));
        resultChart = text('figure', 'st-impact-components');
        const componentLegend = text('div', 'st-meta st-impact-component-legend');
        componentLegend.append(text('span', '', 'Handling charge'), text('span', '', 'Planner attention'));
        results.append(resultChart, componentLegend);
        resultRows = text('div', 'st-impact-result-rows');
        results.append(resultRows, text('p', 'st-meta', data.limits));
        results.append(text('p', 'st-meta', 'Avoided lifts × charge proxy, plus planner hours × opportunity value, multiplied by eligible adopting calls / year.'));
        results.append(sourceDetails());

        const global = card('pilot', 'Worldwide context', 'Conditional opportunity at worldwide scale. Separate from the pilot portfolio.');
        global.append(text('span', 'st-chip projection', data.label));
        const amountInBillions = (field) => field.unit.includes('billions') ? value(field) : value(field) / 1e9;
        const exposure = text('p', 'st-kv');
        exposure.append(text('span', '', 'Port-call cost exposure / year'), text('span', '', '≈ ' + money(amountInBillions(data.global.exposure)) + 'B'));
        const pool = text('p', 'st-kv');
        pool.append(text('span', '', 'Pool a better plan can act on / year'), text('span', '', '≈ ' + money(amountInBillions(data.global.prize_mid), 1) + 'B'));
        global.append(exposure, pool, text('p', 'st-meta', 'Mid case · low ' + money(amountInBillions(data.global.prize_low), 1) + 'B / year'), text('p', 'st-meta st-impact-global-check', 'Terminal-revenue check: world terminal revenue ' + data.global.terminal_revenue_check), text('p', 'st-meta', data.global.input_summary));

        const conditions = card('conditions', 'What must be true', data.who_for);
        const assumptions = text('ul', 'st-impact-assumptions');
        data.what_must_be_true.forEach((item) => assumptions.append(text('li', 'st-card-sub', typeof item === 'string' ? item : item.text)));
        conditions.append(assumptions);
        grid.append(inputs, results, global, conditions);
        view.replaceChildren(grid, text('footer', 'st-meta st-impact-scope', data.scope));
        panel.replaceChildren(view);
        selectPreset('low');
      }
      function start() {
        if (!starting) starting = ctx.data('impact').then((data) => {
          render(data);
          ctx.log({who: 'Software', where: 'on this laptop', text: 'Prepared the forward-pilot projection from sourced assumptions.', label: data.label});
          ctx.done();
        });
        return starting;
      }
      return {
        start,
        async enter() {
          active = true;
          ctx.center(null);
          await start();
        },
        leave() { active = false; },
        async autoplay(autoCtx) {
          await start();
          if (!active) return;
          await autoCtx.wait(3500);
          const control = controls.get('annual_calls');
          const rect = control.input.getBoundingClientRect();
          const target = value(dataset.pilot.mid.inputs.annual_calls);
          const ratio = (target - control.min) / (control.max - control.min);
          const startRatio = (current.annual_calls - control.min) / (control.max - control.min);
          await autoCtx.drag({x: rect.left + startRatio * rect.width, y: rect.top + rect.height / 2}, {x: rect.left + ratio * rect.width, y: rect.top + rect.height / 2});
          // Native ranges do not react to synthetic events in every host.
          control.input.value = target;
          control.input.dispatchEvent(new Event('input', {bubbles: true}));
          await autoCtx.wait(5000);
          await autoCtx.click(presetButtons.get('mid'));
          await autoCtx.wait(4500);
        },
      };
    },
  });
})(typeof window !== 'undefined' ? window : globalThis);
