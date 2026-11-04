/* StowLM Planner · load list (shell-owned; PLANNER.md "Left: Load list").
 * The call's containers from planner-data/boxes.json, with each box's status from the current plan (planned when it has a
 * slot). POD filter chips; click a row to find the box in the bay plan. A step may replace the rows (cargo intake shows its
 * separate 12-box demo list) and mark boxes (forced, moved, violation). Nothing here is computed beyond counting rows. */
(function () {
  'use strict';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const PALETTE = ['#0072b2', '#e69f00', '#009e73', '#cc79a7', '#56b4e9', '#d55e00', '#f0e442'];

  function podColours(pods) {
    const out = new Map();
    (pods || []).forEach((p, i) => out.set(p.code, p.colour || PALETTE[i % PALETTE.length]));
    return out;
  }

  function create(host, opts) {
    const o = opts || {};
    const S = { boxes: {}, pods: [], colour: new Map(), plan: null, rows: null, base: null, filter: null, marks: new Map(), sel: null, title: 'Load list', note: '' };
    host.innerHTML = `<div class="pl-ll-head"><h2><span data-v="title">Load list</span><small data-v="count"></small></h2><p class="pl-ll-note" data-v="note"></p><div class="pl-ll-filters" data-v="filters"></div></div>
      <div class="pl-ll-scroll"><table class="pl-ll-table"><thead><tr><th>Box</th><th>Size</th><th class="num">t</th><th>POD</th><th></th><th title="Status"></th></tr></thead><tbody data-v="rows"></tbody></table></div>`;
    const q = (k) => host.querySelector(`[data-v="${k}"]`);

    /* The call's boxes are the ones the plan places; boxes.json also lists the fixed synthetic cargo already on board. */
    function baseRows() {
      const planned = new Set(((S.plan && S.plan.slots) || []).map((s) => s.box_id));
      const ids = S.plan ? Object.keys(S.boxes).filter((id) => planned.has(id)) : Object.keys(S.boxes);
      S.fixed = S.plan ? Object.keys(S.boxes).length - ids.length : 0;
      return ids.map((id) => [id, S.boxes[id]]).map(([id, b]) => ({ box_id: id, size_ft: b.size_ft, weight_t: b.weight_t, pod: b.pod, dg_class: b.dg_class, reefer: !!b.reefer,
        status: S.plan ? (planned.has(id) ? 'planned' : 'unplanned') : 'to load' }));
    }
    function rows() { return S.rows || S.base || []; }
    function draw() {
      const all = rows(), shown = S.filter ? all.filter((r) => r.pod === S.filter) : all;
      q('title').textContent = S.title;
      const planned = all.filter((r) => r.status === 'planned').length;
      q('count').textContent = `${all.length} boxes to load${S.plan && !S.rows ? ` · ${planned} planned` : ''}`;
      q('note').textContent = S.note || (S.fixed && !S.rows ? `${S.fixed} more synthetic container records are yard cargo, not part of this call's list` : '');
      q('note').hidden = !q('note').textContent;
      const pods = [...new Set(all.map((r) => r.pod).filter(Boolean))];
      q('filters').innerHTML = pods.length > 1 ? `<button type="button" data-pod="" class="${S.filter ? '' : 'on'}">All</button>` + pods.map((p) => `<button type="button" data-pod="${esc(p)}" class="${S.filter === p ? 'on' : ''}"><span class="pl-pod"><i style="--pod:${esc(S.colour.get(p) || '#999')}"></i>${esc(p)}</span></button>`).join('') : '';
      q('rows').innerHTML = shown.length ? shown.map((r) => {
        const mk = S.marks.get(r.box_id);
        const st = mk === 'forced' ? 'forced' : mk === 'moved' ? 'moved' : r.status;
        return `<tr data-box="${esc(r.box_id)}" class="${mk ? 'mk-' + mk : ''}${S.sel === r.box_id ? ' sel' : ''}">
          <td><span class="pl-ll-id">${esc(r.box_id)}</span></td>
          <td><span class="pl-size">${r.size_ft ? esc(r.size_ft) + '′' : '—'}</span></td>
          <td class="num">${typeof r.weight_t === 'number' ? r.weight_t.toFixed(1) : '—'}</td>
          <td><span class="pl-pod"><i style="--pod:${esc(S.colour.get(r.pod) || '#999')}"></i>${esc(r.pod || '—')}</span></td>
          <td>${r.dg_class ? `<span class="pl-dg" title="Dangerous goods, IMDG class ${esc(r.dg_class)}">${esc(r.dg_class)}</span>` : r.reefer ? '<span class="pl-reefer" title="Reefer">❄︎</span>' : ''}</td>
          <td><i class="pl-st-dot ${esc(st.replace(/\s/g, '-'))}" title="${esc(st === 'forced' ? 'forced out by the rule' : st)}"></i></td></tr>`;
      }).join('') : '<tr><td colspan="6" class="pl-ll-empty">No boxes yet</td></tr>';
    }
    host.addEventListener('click', (e) => {
      const f = e.target.closest('[data-pod]');
      if (f) { S.filter = f.dataset.pod || null; draw(); return; }
      const tr = e.target.closest('tr[data-box]');
      if (tr) { api.focus(tr.dataset.box); if (o.onSelect) o.onSelect(tr.dataset.box); }
    });

    const api = {
      setData(d) { S.boxes = (d && d.boxes) || {}; S.pods = (d && d.pods) || []; S.colour = podColours(S.pods); S.base = baseRows(); draw(); },
      setPlan(plan) { S.plan = plan || null; S.base = baseRows(); draw(); },
      /* Replace the rows (e.g. cargo intake's separate demo list). title/note say what the list is. */
      setRows(list, meta) { S.rows = (list || []).map((r) => ({ status: 'to load', ...r })); S.title = (meta && meta.title) || 'Load list'; S.note = (meta && meta.note) || ''; S.filter = null; draw(); },
      reset() { S.rows = null; S.title = 'Load list'; S.note = ''; S.filter = null; draw(); },
      mark(ids, kind) { for (const id of ids || []) S.marks.set(id, kind); draw(); },
      clearMarks(kind) { if (!kind) S.marks.clear(); else for (const [k, v] of [...S.marks]) if (v === kind) S.marks.delete(k); draw(); },
      focus(id) {
        S.sel = id; S.filter = S.filter && rows().some((r) => r.box_id === id && r.pod === S.filter) ? S.filter : null; draw();
        const tr = host.querySelector(`tr[data-box="${CSS.escape(id)}"]`); if (tr) tr.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      },
      colourOf: (pod) => S.colour.get(pod) || null,
      get rows() { return rows().slice(); },
    };
    draw();
    return api;
  }
  window.PlannerLoadList = { create, podColours, PALETTE };
})();
