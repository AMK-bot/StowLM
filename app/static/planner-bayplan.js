/* StowLM bay plan. Data order is authoritative: rows run port to starboard,
 * tiers increase upwards, and an even 40 ft bay aliases its two odd neighbours.
 * Geometry and snapping are schematic; the caller owns full rule checking. */
(function (global) {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var instanceCount = 0;
  var latestPods = [];
  var STATE = {
    selected: { stroke: '#176698', width: 2.5 },
    forced: { stroke: '#926317', width: 2, dash: '5 3' },
    moved: { stroke: '#137c79', width: 2.5 },
    violation: { stroke: '#bf3e29', width: 2.5 }
  };
  var FALLBACK = ['#8dbbcf', '#e6b475', '#9dc7af', '#b6a6cf', '#dfabbb', '#c7c485'];
  function number(value) { return Number(value); }
  // Keep every displayed weight on the same one-decimal path, including titles
  // and accessibility text. Preserve full precision only in the cargo data.
  function weight(value) { var tonnes = Number(value); return Number.isFinite(tonnes) ? tonnes.toFixed(1) : '—'; }
  function same(a, b) { return String(a) === String(b) || Number(a) === Number(b); }
  function pad(value) { return String(value).padStart(2, '0'); }
  function key(s) { return s ? [+s.bay, +s.row, +s.tier].join(':') : ''; }
  function cloneSlot(s) {
    if (!s) return null;
    if (typeof s === 'string' && /^\d{7}$/.test(s)) {
      s = { bay: +s.slice(0, 3), row: +s.slice(3, 5), tier: +s.slice(5, 7) };
    }
    return { bay: +s.bay, row: +s.row, tier: +s.tier, deck: s.deck == null ? +s.tier >= 80 : !!s.deck };
  }
  function copy(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
  function merge(base, update) {
    var result = copy(base) || {};
    Object.keys(update).forEach(function (field) {
      var value = update[field];
      result[field] = value && typeof value === 'object' && !Array.isArray(value) ? merge(result[field], value) : copy(value);
    });
    return result;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function attr(node, attrs) {
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] != null) node.setAttribute(k, String(attrs[k])); });
    return node;
  }
  function svgNode(doc, name, attrs, text, parent) {
    var el = attr(doc.createElementNS(NS, name), attrs);
    if (text != null) el.textContent = String(text);
    if (parent) parent.appendChild(el);
    return el;
  }
  function listen(node, name, fn) { if (node.addEventListener) node.addEventListener(name, fn); }
  function rect(doc, parent, r, attrs) {
    return svgNode(doc, 'rect', Object.assign({ x: r.x, y: r.y, width: r.width, height: r.height, rx: 2 }, attrs), null, parent);
  }
  function label(doc, parent, x, y, text, attrs) {
    return svgNode(doc, 'text', Object.assign({ x: x, y: y, fill: '#52616d', 'font-size': 11 }, attrs), text, parent);
  }
  function center(r) { return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }
  function pointIn(p, r) { return p.x >= r.x && p.x <= r.x + r.width && p.y >= r.y && p.y <= r.y + r.height; }

  function create(host, options) {
    options = options || {};
    var doc = host.ownerDocument || global.document;
    var ship = options.ship || {};
    var bays = (ship.bays || []).slice();
    // With port on the left, the supplied transverse rows are viewed from aft.
    var aftView = ship.row_order === 'port_to_starboard';
    var boxData = options.boxes || {};
    var originalBoxes = copy(boxData.boxes || boxData);
    var boxes = copy(originalBoxes);
    var pods = options.pods || boxData.pods || [];
    latestPods = pods.slice();
    var bayMap = new Map(bays.map(function (b) { return [+b.bay, b]; }));
    var uid = 'bp-hatch-' + (++instanceCount);
    var svg = String(host.tagName || '').toLowerCase() === 'svg' ? host : svgNode(doc, 'svg', {}, null, host);
    if (host.classList) host.classList.add('bp-host');
    attr(svg, { xmlns: NS, class: ((svg.getAttribute('class') || '') + ' bp-svg').trim(), viewBox: '0 0 760 520', width: '100%', height: '100%',
      role: 'img', 'aria-label': 'Ship profile and bay sections', preserveAspectRatio: 'xMidYMid meet',
      'font-family': 'Inter, system-ui, sans-serif', 'font-variant-numeric': 'tabular-nums', style: 'background:#fff;touch-action:none' });
    var preGenerateMode = false, activeBay = null;
    var selectedBays = bays.slice(0, 2).map(function (b) { return +b.bay; });
    var slots = [], planMeta = {}, closed = new Map(), marks = new Map(), handlers = new Map();
    var highlights = { selected: new Set(), forced: new Set(), moved: new Set(), violation: new Set() };
    var layout = { width: 760, height: 520, slots: [], profile: [], sections: [] };
    var tooltipLayer, overlayLayer, drag = null, onDrop = null, allowSwap = false, suppressClick = false;
    var animation = null, playToken = 0, destroyed = false, svgListeners = [];
    function rootListen(name, fn) { listen(svg, name, fn); svgListeners.push([name, fn]); }
    function box(id) { return boxes[id] || {}; }
    function podColour(id) {
      var b = box(id), index = pods.findIndex(function (p) { return p.code === b.pod; });
      return index < 0 ? '#d8e0e5' : (pods[index].colour || FALLBACK[index % FALLBACK.length]);
    }
    function boxInk(id) {
      var colour = podColour(id), match = /^#([0-9a-f]{6})$/i.exec(colour);
      if (!match) return '#16323e';
      var rgb = [0, 2, 4].map(function (offset) {
        var channel = parseInt(match[1].slice(offset, offset + 2), 16) / 255;
        return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
      });
      return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722 < 0.23 ? '#fff' : '#16323e';
    }
    function cellAt(s) {
      var b = bayMap.get(+s.bay);
      return b && Array.isArray(b.cells) ? b.cells.find(function (cell) { return +cell.row === +s.row && +cell.tier === +s.tier; }) : null;
    }
    function compatible(s, id) {
      var cell = cellAt(s), b = box(id), size = +b.size_ft || 20;
      return !cell || ((!Array.isArray(cell.accepts) || cell.accepts.some(function (n) { return +n === size; })) && (!b.reefer || cell.reefer === true));
    }
    function suppressNextClick() {
      suppressClick = true;
      global.setTimeout(function () { suppressClick = false; }, 0);
    }
    function boxSlot(id) { return slots.find(function (s) { return String(s.box_id) === String(id); }) || null; }
    function footprint(s, id) {
      s = cloneSlot(s);
      if (!s) return [];
      // A 40 ft assignment occupies the paired odd bays, including when only
      // one of those sections is currently expanded.
      if (+box(id).size_ft >= 40 && +s.bay % 2 === 0) {
        return [-1, 1].map(function (d) { return Object.assign({}, s, { bay: +s.bay + d }); });
      }
      return [s];
    }
    function overlaps(a, aId, b, bId) {
      return footprint(a, aId).some(function (first) {
        return footprint(b, bId).some(function (second) { return key(first) === key(second); });
      });
    }
    function occupants(s, id, ignored) {
      return slots.filter(function (at) {
        return ignored.indexOf(String(at.box_id)) < 0 && overlaps(s, id, at, at.box_id);
      });
    }
    function occupied(s, except) { return occupants(s, except, [String(except)]).length > 0; }
    function isAnimating(id) {
      return animation && (String(animation.box_id) === String(id) || (animation.swap_with != null && String(animation.swap_with) === String(id)));
    }
    function occupant(s) {
      return slots.find(function (at) {
        return key(at) === key(s) || footprint(at, at.box_id).some(function (a) { return key(a) === key(s); });
      });
    }
    function bayTiers(b) {
      return { deck: (b.deck_tiers || []).map(number).sort(function (a, c) { return c - a; }),
        hold: (b.hold_tiers || []).map(number).sort(function (a, c) { return c - a; }) };
    }
    function validSlot(s) {
      var b = bayMap.get(+s.bay);
      return b && (!Array.isArray(b.cells) || !!cellAt(s)) && (b.rows || []).some(function (r) { return same(r, s.row); }) &&
        (b.deck_tiers || []).concat(b.hold_tiers || []).some(function (t) { return same(t, s.tier); });
    }
    function isClosed(s, id) {
      return closed.has(+s.bay) || footprint(s, id).some(function (a) { return closed.has(+a.bay); });
    }
    function legalPlacement(s, id, ignored) {
      if (!s || !validSlot(s) || !compatible(s, id) || isClosed(s, id)) return false;
      var size = +box(id).size_ft || 20, b = bayMap.get(+s.bay);
      if (size >= 40 ? +s.bay % 2 !== 0 : (+s.bay % 2 === 0 || +b.length_ft >= 40)) return false;
      if (!footprint(s, id).every(function (physical) { return validSlot(physical) && compatible(physical, id); })) return false;
      return occupants(s, id, ignored || [String(id)]).length === 0;
    }
    function swapCounterpart(id, from, to, expected) {
      var current = boxSlot(id);
      if (!current || !from || !to || key(current) !== key(from) || key(from) === key(to)) return null;
      var found = occupants(to, id, [String(id)]);
      if (found.length !== 1) return null;
      var other = found[0];
      if (expected != null && String(other.box_id) !== String(expected)) return null;
      if (String(other.box_id) === String(id)) return null;
      var ignored = [String(id), String(other.box_id)];
      if (!legalPlacement(to, id, ignored) || !legalPlacement(from, other.box_id, ignored)) return null;
      if (overlaps(to, id, from, other.box_id)) return null;
      return other;
    }
    function targetSlots(p, id) {
      if (!p || !Number.isFinite(+p.x) || !Number.isFinite(+p.y)) return [];
      var r = layout.slots.find(function (candidate) { return pointIn(p, candidate); });
      if (!r) return [];
      var s = cloneSlot(r.slot), size = +box(id).size_ft || 20;
      if (size >= 40 && +s.bay % 2) {
        return [+s.bay + 1, +s.bay - 1].filter(function (n) { return n > 0 && bayMap.has(n); })
          .map(function (n) { return Object.assign({}, s, { bay: n }); });
      }
      return [s];
    }
    function dropTarget(p, id, swaps) {
      var targets = targetSlots(p, id);
      // Prefer a free slot if two virtual 40 ft bays share the pointed odd bay.
      var free = targets.find(function (s) { return legalPlacement(s, id); });
      if (free) return { to: free };
      if (swaps) {
        for (var i = 0; i < targets.length; i++) {
          var other = swapCounterpart(id, cloneSlot(boxSlot(id)), targets[i]);
          if (other) return { to: targets[i], swap_with: other.box_id };
        }
      }
      return null;
    }
    function snap(p, id) {
      var proposed = dropTarget(p, id, false);
      return proposed ? cloneSlot(proposed.to) : null;
    }
    function chooseChanges() {
      if (preGenerateMode) return false;
      var changed = new Set(Array.from(highlights.forced).concat(Array.from(highlights.moved)));
      var counts = new Map();
      slots.forEach(function (at) {
        if (!changed.has(String(at.box_id)) || closed.has(+at.bay) || !bayMap.has(+at.bay)) return;
        if (!counts.has(+at.bay)) counts.set(+at.bay, new Set());
        counts.get(+at.bay).add(String(at.box_id));
      });
      var best = null, count = 0;
      bays.forEach(function (b) {
        var size = counts.has(+b.bay) ? counts.get(+b.bay).size : 0;
        if (size > count) { best = +b.bay; count = size; }
      });
      if (best == null) return false;
      selectedBays = [best]; return true;
    }
    function profileRect(s) {
      if (!s || !validSlot(s)) return null;
      var r = layout.profile.find(function (p) { return +p.bay === +s.bay; });
      if (!r) return null;
      var b = bayMap.get(+s.bay), tiers = bayTiers(b), rows = b.rows || [];
      var ri = rows.findIndex(function (row) { return +row === +s.row; });
      var side = tiers.deck.indexOf(+s.tier) >= 0 ? 0 : 1;
      var list = side ? tiers.hold : tiers.deck, ti = list.indexOf(+s.tier);
      if (ri < 0 || ti < 0) return null;
      var cw = Math.max(0.4, (r.width - 2) / Math.max(1, rows.length));
      var gap = preGenerateMode ? 16 : 8;
      var available = side ? r.y + r.height - r.hatchY - gap : r.hatchY - r.y - gap;
      var ch = Math.min(preGenerateMode ? 24 : 12, available / Math.max(1, list.length));
      var y = side ? r.hatchY + gap + ti * ch : r.hatchY - gap - (list.length - ti) * ch;
      return { slot: cloneSlot(s), x: r.x + 1 + ri * cw, y: y, width: cw, height: ch,
        cx: r.x + 1 + (ri + 0.5) * cw, cy: y + ch / 2 };
    }
    function geometry() {
      var width = 760, profileTop = preGenerateMode ? 40 : 32, profileBottom = preGenerateMode ? 288 : 136, profileHatch = preGenerateMode ? 168 : 80;
      var profileStep = 704 / Math.max(1, bays.length);
      var sectionCount = preGenerateMode ? 0 : selectedBays.length, sectionRows = Math.ceil(sectionCount / 2);
      var height = preGenerateMode ? 360 : Math.max(520, 192 + sectionRows * 304);
      layout = { width: width, height: height, slots: [], profile: [], profileSlots: [], sections: [], preGenerate: preGenerateMode, activeBay: activeBay };
      bays.forEach(function (b, i) {
        layout.profile.push({ bay: +b.bay, x: 40 + i * profileStep, y: profileTop, width: Math.max(2, profileStep - 8),
          height: profileBottom - profileTop, hatchY: profileHatch });
      });
      bays.forEach(function (b) {
        (b.rows || []).forEach(function (row) {
          (b.deck_tiers || []).concat(b.hold_tiers || []).forEach(function (tier) {
            var r = profileRect({ bay: +b.bay, row: +row, tier: +tier, deck: (b.deck_tiers || []).map(number).indexOf(+tier) >= 0 });
            if (r) layout.profileSlots.push(r);
          });
        });
      });
      (preGenerateMode ? [] : selectedBays).forEach(function (bay, i) {
        var b = bayMap.get(+bay); if (!b) return;
        var tiers = bayTiers(b), rows = (b.rows || []).slice();
        var single = sectionCount === 1;
        var outerX = single ? 24 : 24 + (i % 2) * 368;
        var outerY = 184 + Math.floor(i / 2) * 304;
        var availableW = single ? 712 : 344;
        var cw = Math.min(64, (availableW - 40) / Math.max(1, rows.length));
        var ch = Math.min(40, 240 / Math.max(1, tiers.deck.length + tiers.hold.length));
        var gridWidth = rows.length * cw, gridHeight = (tiers.deck.length + tiers.hold.length) * ch + 16;
        var gridX = outerX + 32 + Math.max(0, (availableW - 40 - gridWidth) / 2);
        var gridY = outerY + 32;
        var hatchY = gridY + tiers.deck.length * ch + 8;
        var section = { bay: +bay, x: outerX, y: outerY, width: availableW, height: gridHeight + 64,
          hatchY: hatchY, gridX: gridX, gridY: gridY, gridWidth: gridWidth, cellWidth: cw, cellHeight: ch };
        layout.sections.push(section);
        rows.forEach(function (row, ri) {
          [tiers.deck, tiers.hold].forEach(function (list, side) {
            list.forEach(function (tier, ti) {
              if (!validSlot({ bay: +bay, row: +row, tier: +tier })) return;
              var x = gridX + ri * cw, y = gridY + (side ? tiers.deck.length * ch + 16 : 0) + ti * ch;
              layout.slots.push({ slot: { bay: +bay, row: +row, tier: +tier, deck: !side },
                x: x, y: y, width: cw, height: ch, cx: x + cw / 2, cy: y + ch / 2 });
            });
          });
        });
      });
      return layout;
    }
    function details(id, s) {
      var b = box(id), parts = [String(id), (b.size_ft == null ? 'Size unknown' : b.size_ft + ' ft') + ' · ' +
        (b.weight_t == null ? 'Weight unknown' : weight(b.weight_t) + ' t'), 'POD ' + (b.pod || 'unknown'),
        'DG ' + (b.dg_class == null ? 'none' : b.dg_class) + ' · Reefer ' + (b.reefer ? 'yes' : 'no')];
      if (s) parts.push('Bay ' + pad(s.bay) + ' · Row ' + pad(s.row) + ' · Tier ' + pad(s.tier) + ' · ' + (s.deck ? 'deck' : 'hold'));
      var marked = s && (marks.get(key(s)) || footprint(s, id).map(function (physical) { return marks.get(key(physical)); }).find(Boolean)); if (marked) parts.push(marked);
      return parts;
    }
    function hideTooltip() { if (tooltipLayer) clear(tooltipLayer); }
    function showTooltip(id, s, p) {
      if (!tooltipLayer || drag || animation) return;
      clear(tooltipLayer);
      var lines = details(id, s), w = 256, h = 16 + lines.length * 16;
      var x = Math.max(8, Math.min(layout.width - w - 8, p.x + 12));
      var y = Math.max(8, Math.min(layout.height - h - 8, p.y + 16));
      var g = svgNode(doc, 'g', { class: 'bp-tooltip', 'pointer-events': 'none', role: 'tooltip' }, null, tooltipLayer);
      rect(doc, g, { x: x, y: y, width: w, height: h }, { fill: '#fff', stroke: '#c4cfd7', 'stroke-width': 1, rx: 4 });
      lines.forEach(function (line, i) { label(doc, g, x + 12, y + 20 + i * 16, line,
        { fill: i === 0 ? '#243541' : '#52616d', 'font-size': 11, 'font-weight': i === 0 ? 500 : 400 }); });
    }
    function emit(name) {
      var args = Array.prototype.slice.call(arguments, 1);
      (handlers.get(name) || []).slice().forEach(function (fn) { fn.apply(null, args); });
    }
    function select(id, s) {
      highlights.selected = new Set([String(id)]); render();
      emit('select', String(id), cloneSlot(s));
      var r = slotRect(s); if (r) showTooltip(id, s, center(r));
    }
    function svgPoint(event) {
      if (svg.createSVGPoint && svg.getScreenCTM) {
        var matrix = svg.getScreenCTM();
        if (matrix && matrix.inverse) {
          var p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY;
          return p.matrixTransform(matrix.inverse());
        }
      }
      var r = svg.getBoundingClientRect ? svg.getBoundingClientRect() : { left: 0, top: 0, width: layout.width, height: layout.height };
      var scale = Math.min((r.width || layout.width) / layout.width, (r.height || layout.height) / layout.height);
      return { x: (event.clientX - r.left - ((r.width || layout.width) - layout.width * scale) / 2) / scale,
        y: (event.clientY - r.top - ((r.height || layout.height) - layout.height * scale) / 2) / scale };
    }
    function outlines(parent, r, id, s, small, displayed) {
      Object.keys(STATE).forEach(function (kind, index) {
        if (!highlights[kind].has(String(id)) && !(kind === 'violation' && (marks.has(key(s)) || (displayed && marks.has(key(displayed)))))) return;
        var st = STATE[kind], inset = small ? 0 : index * 0.6;
        rect(doc, parent, { x: r.x + inset, y: r.y + inset, width: Math.max(1, r.width - inset * 2), height: Math.max(1, r.height - inset * 2) },
          { class: 'bp-state bp-' + kind, fill: 'none', stroke: st.stroke, 'stroke-width': small ? 1.3 : st.width,
            'stroke-dasharray': st.dash, 'pointer-events': 'none', 'vector-effect': 'non-scaling-stroke' });
        if (kind === 'moved' && !small) rect(doc, parent,
          { x: r.x + 3, y: r.y + 3, width: Math.max(1, r.width - 6), height: Math.max(1, r.height - 6) },
          { class: 'bp-state bp-moved', fill: 'none', stroke: st.stroke, 'stroke-width': 0.7, 'pointer-events': 'none' });
        if (kind === 'violation' && !small) label(doc, parent, r.x + r.width - 5, r.y + 11, '!',
          { fill: st.stroke, 'font-size': 12, 'font-weight': 800, 'text-anchor': 'end', class: 'bp-violation-icon' });
      });
    }
    function drawBox(parent, r, at, mini, ghost) {
      var id = at.box_id, b = box(id), actual = cloneSlot(at);
      var marked = marks.get(key(actual)) || (r.slot && marks.get(key(r.slot)));
      var ink = boxInk(id);
      var g = svgNode(doc, 'g', { class: 'bp-box' + (ghost ? ' ' + ghost : ''), 'data-box-id': id,
        'data-slot': key(actual), role: ghost ? null : 'button', tabindex: ghost || mini ? null : 0,
        'aria-label': details(id, actual).join(', '), 'aria-grabbed': ghost === 'bp-drag-ghost' ? 'true' : null }, null, parent);
      svgNode(doc, 'title', {}, details(id, actual).join('\n'), g);
      var inset = mini ? 0.6 : 2;
      var cr = { x: r.x + inset, y: r.y + inset, width: Math.max(1, r.width - inset * 2), height: Math.max(1, r.height - inset * 2) };
      rect(doc, g, cr, { class: 'bp-box-fill', fill: podColour(id), stroke: '#7b929e', 'stroke-width': mini ? 0.4 : 0.65, rx: mini ? 1 : 2 });
      if (!mini) {
        var wide = cr.width >= 40, tall = cr.height >= 26;
        var extended = +b.size_ft === 45;
        if (wide && cr.height >= 18 && (!extended || tall) && b.weight_t != null) label(doc, g, cr.x + 5, cr.y + 13,
          weight(b.weight_t) + 't', { class: 'bp-weight-label', fill: ink, 'font-size': 10 });
        if ((wide && tall) || (extended && cr.width >= 24 && cr.height >= 18)) label(doc, g, cr.x + 5, cr.y + cr.height - 5,
          (b.size_ft || 20) + '′', { class: 'bp-size-label' + (extended ? ' bp-size-45' : ''), fill: ink, 'font-size': 9, 'font-weight': extended ? 500 : 400 });
        if (b.dg_class != null && cr.width >= 20 && cr.height >= 18) {
          var dx = cr.x + cr.width - 10, dy = cr.y + (tall ? 12 : cr.height / 2), q = 7;
          svgNode(doc, 'path', { class: 'bp-dg', d: 'M' + dx + ',' + (dy - q) + 'l' + q + ',' + q + 'l-' + q + ',' + q + 'l-' + q + ',-' + q + 'Z',
            fill: '#fff', stroke: '#263b47', 'stroke-width': 0.8 }, null, g);
          label(doc, g, dx, dy + 2.5, b.dg_class, { class: 'bp-dg-class', fill: '#263b47', 'font-size': String(b.dg_class).length > 1 ? 6 : 8, 'text-anchor': 'middle' });
        }
        if (b.reefer && cr.width >= 20 && cr.height >= 18) label(doc, g, cr.x + cr.width - 9, cr.y + cr.height - (tall ? 3 : 4), '❄',
          { class: 'bp-reefer', fill: ink, 'font-size': 12, 'text-anchor': 'middle' });
        // A line at each end denotes a 40 ft box continuing through the paired bay.
        if (+b.size_ft >= 40) svgNode(doc, 'path', { class: 'bp-size-40', d: 'M' + (cr.x + 1) + ',' + (cr.y + cr.height / 2 - 3) + 'v6M' + (cr.x + cr.width - 1) + ',' + (cr.y + cr.height / 2 - 3) + 'v6', stroke: ink, 'stroke-width': 2, fill: 'none' }, null, g);
      }
      outlines(g, cr, id, actual, mini, r.slot);
      if (marked) svgNode(doc, 'title', {}, marked, g);
      if (!ghost) {
        listen(g, 'click', function () { if (!suppressClick) select(id, actual); suppressClick = false; });
        listen(g, 'keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(id, actual); } });
        listen(g, 'pointerenter', function (e) { showTooltip(id, actual, svgPoint(e)); });
        listen(g, 'pointermove', function (e) { if (!drag) showTooltip(id, actual, svgPoint(e)); });
        listen(g, 'pointerleave', hideTooltip);
        if (!mini) listen(g, 'pointerdown', function (e) { startDrag(e, at, r); });
      }
      return g;
    }
    function drawProfile(parent) {
      var group = svgNode(doc, 'g', { class: 'bp-profile' }, null, parent);
      label(doc, group, 24, 20, preGenerateMode ? 'On board before planning' : 'Ship profile', { 'font-size': preGenerateMode ? 12 : 10, fill: '#52616d' });
      label(doc, group, 736, 20, 'Deck above · Hold below', { 'text-anchor': 'end', 'font-size': 10 });
      layout.profile.forEach(function (r) {
        var b = bayMap.get(+r.bay), tiers = bayTiers(b), rows = b.rows || [];
        var g = svgNode(doc, 'g', { class: 'bp-profile-bay' + (+r.bay === activeBay ? ' bp-active-bay' : ''), 'data-bay': r.bay,
          tabindex: preGenerateMode ? null : 0, role: preGenerateMode ? 'group' : 'button',
          'aria-pressed': preGenerateMode ? null : selectedBays.indexOf(+r.bay) >= 0,
          'aria-label': (preGenerateMode ? '' : 'Show ') + (b.label || 'Bay ' + pad(b.bay)) }, null, group);
        rect(doc, g, r, { class: 'bp-profile-background', fill: '#fff', stroke: 'none', rx: 3 });
        [tiers.deck, tiers.hold].forEach(function (list, side) {
          list.forEach(function (tier, ti) {
            rows.forEach(function (row, ri) {
              var s = { bay: +r.bay, row: +row, tier: +tier, deck: !side };
              if (!validSlot(s)) return;
              var cr = profileRect(s);
              var at = occupant(s);
              rect(doc, g, { x: cr.x + 0.3, y: cr.y + 0.3, width: Math.max(0.3, cr.width - 0.6), height: Math.max(0.3, cr.height - 0.6) },
                { class: 'bp-slot', fill: '#fff', stroke: '#dce4e8', 'stroke-width': 0.45, rx: 0.7 });
              if (at && !isAnimating(at.box_id) && !(drag && drag.active && drag.box_id === at.box_id)) drawBox(g, cr, at, true);
            });
          });
        });
        svgNode(doc, 'line', { class: 'bp-hatch', x1: r.x, x2: r.x + r.width, y1: r.hatchY, y2: r.hatchY, stroke: '#73848e', 'stroke-width': b.hatch === false ? 0.7 : 1.5, 'stroke-dasharray': b.hatch === false ? '2 2' : null }, null, g);
        label(doc, g, r.x + r.width / 2, preGenerateMode ? 312 : 152, b.label || pad(b.bay), { 'text-anchor': 'middle', 'font-size': bays.length > 22 ? 8 : 10 });
        if (closed.has(+r.bay)) {
          rect(doc, g, r, { fill: '#fff', 'fill-opacity': 0.8, stroke: 'none' });
          rect(doc, g, r, { class: 'bp-closed', fill: 'url(#' + uid + ')', stroke: '#9da8ae', 'stroke-width': 1 });
          var closure = closed.get(+r.bay);
          svgNode(doc, 'title', {}, closure, g);
          if (r.width >= 40) {
            var limit = Math.max(6, Math.floor((r.width - 8) / 4.8)), lines = [''];
            closure.split(/\s+/).forEach(function (word) {
              var last = lines.length - 1;
              if (lines[last] && (lines[last] + ' ' + word).length > limit) lines.push(word);
              else lines[last] += (lines[last] ? ' ' : '') + word;
            });
            if (lines.length > 3) lines = lines.slice(0, 2).concat([lines.slice(2).join(' ')]);
            var plaqueY = r.hatchY - lines.length * 6 - 4;
            rect(doc, g, { x: r.x + 3, y: plaqueY, width: r.width - 6, height: lines.length * 12 + 8 }, { fill: '#fff', stroke: 'none' });
            lines.forEach(function (line, index) {
              label(doc, g, r.x + r.width / 2, plaqueY + 12 + index * 12, line,
                { class: 'bp-closed-label', 'text-anchor': 'middle', 'font-size': 9, fill: '#46545e' });
            });
          } else label(doc, g, r.x + r.width / 2, preGenerateMode ? 326 : 166, 'closed', { class: 'bp-closed-label', 'text-anchor': 'middle', 'font-size': 8, fill: '#5a6670' });
        }
        rect(doc, g, r, { class: 'bp-profile-frame', fill: 'none', stroke: +r.bay === activeBay || selectedBays.indexOf(+r.bay) >= 0 ? '#176698' : 'none',
          'stroke-width': 1.25, 'pointer-events': 'none', rx: 3 });
        listen(g, 'click', function (e) { if (e.target === g || !(e.target.closest && e.target.closest('.bp-box'))) focusBay(r.bay); });
        listen(g, 'keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); focusBay(r.bay); } });
      });
      svgNode(doc, 'line', { x1: 24, x2: 736, y1: preGenerateMode ? 336 : 176, y2: preGenerateMode ? 336 : 176, stroke: '#e3e9ed' }, null, group);
    }
    function drawSections(parent) {
      layout.sections.forEach(function (section) {
        var b = bayMap.get(+section.bay), tiers = bayTiers(b), rows = b.rows || [];
        var g = svgNode(doc, 'g', { class: 'bp-section', 'data-bay': b.bay, 'aria-label': b.label || 'Bay ' + pad(b.bay) }, null, parent);
        label(doc, g, section.x + 8, section.y + 16, (b.label || 'Bay ' + pad(b.bay)) + (aftView ? ' · aft view' : ''),
          { class: 'bp-section-title', fill: '#243b49', 'font-size': 13, 'font-weight': 600 });
        label(doc, g, section.x + section.width - 8, section.y + 16, 'Port  ←  →  Starboard', { 'text-anchor': 'end', 'font-size': 9 });
        var these = layout.slots.filter(function (r) { return +r.slot.bay === +b.bay; });
        these.forEach(function (r) {
          var at = occupant(r.slot);
          rect(doc, g, { x: r.x + 2, y: r.y + 2, width: r.width - 4, height: r.height - 4 },
            { class: 'bp-slot', fill: '#fff', stroke: '#dbe3e8', 'stroke-width': 0.8 });
          if (at && !isAnimating(at.box_id) && !(drag && drag.active && drag.box_id === at.box_id)) drawBox(g, r, at, false);
          if (!at && marks.has(key(r.slot))) {
            outlines(g, { x: r.x + 2, y: r.y + 2, width: r.width - 4, height: r.height - 4 }, '', r.slot, false);
            svgNode(doc, 'title', {}, marks.get(key(r.slot)), g);
          }
        });
        [tiers.deck, tiers.hold].forEach(function (list, side) {
          list.forEach(function (tier, ti) {
            var y = section.gridY + (side ? tiers.deck.length * section.cellHeight + 16 : 0) + (ti + 0.5) * section.cellHeight + 3;
            label(doc, g, section.gridX - 8, y, pad(tier), { class: 'bp-tier-label', 'text-anchor': 'end', 'font-size': 10 });
          });
        });
        rows.forEach(function (row, ri) {
          label(doc, g, section.gridX + (ri + 0.5) * section.cellWidth, section.gridY + (tiers.deck.length + tiers.hold.length) * section.cellHeight + 32,
            pad(row), { class: 'bp-row-label', 'text-anchor': 'middle', 'font-size': 10 });
        });
        svgNode(doc, 'line', { class: 'bp-hatch', x1: section.gridX - 1, x2: section.gridX + section.gridWidth + 1,
          y1: section.hatchY, y2: section.hatchY, stroke: '#73848e', 'stroke-width': b.hatch === false ? 1 : 3, 'stroke-dasharray': b.hatch === false ? '4 3' : null }, null, g);
        if (closed.has(+b.bay)) {
          rect(doc, g, { x: section.gridX - 1, y: section.gridY - 1, width: section.gridWidth + 2,
            height: (tiers.deck.length + tiers.hold.length) * section.cellHeight + 18 }, { fill: '#fff', 'fill-opacity': 0.8, stroke: 'none' });
          rect(doc, g, { x: section.gridX - 1, y: section.gridY - 1, width: section.gridWidth + 2,
            height: (tiers.deck.length + tiers.hold.length) * section.cellHeight + 18 }, { class: 'bp-closed', fill: 'url(#' + uid + ')', stroke: '#98a5ad' });
          var text = closed.get(+b.bay);
          var tw = Math.min(section.gridWidth - 8, Math.max(64, text.length * 6.4 + 16));
          rect(doc, g, { x: section.gridX + (section.gridWidth - tw) / 2, y: section.hatchY - 13, width: tw, height: 26 }, { fill: '#fff', stroke: '#a7b1b8' });
          label(doc, g, section.gridX + section.gridWidth / 2, section.hatchY + 4, text, { class: 'bp-closed-label', 'text-anchor': 'middle', fill: '#46545e', 'font-size': 11 });
        }
      });
      if (!preGenerateMode && !layout.sections.length) label(doc, parent, 380, 320, 'Select a bay in the ship profile', { 'text-anchor': 'middle', 'font-size': 14 });
    }
    function render() {
      if (destroyed) return;
      hideTooltip(); geometry(); clear(svg);
      attr(svg, { viewBox: '0 0 ' + layout.width + ' ' + layout.height, 'data-pre-generate': preGenerateMode ? 'true' : 'false', 'data-active-bay': activeBay == null ? '' : activeBay });
      svgNode(doc, 'title', {}, 'Bay plan' + (aftView ? ' · port to starboard, viewed from aft' : ''), svg);
      var defs = svgNode(doc, 'defs', {}, null, svg);
      var pattern = svgNode(doc, 'pattern', { id: uid, width: 8, height: 8, patternUnits: 'userSpaceOnUse' }, null, defs);
      svgNode(doc, 'path', { d: 'M-2,2L2,-2M0,8L8,0M6,10L10,6', fill: 'none', stroke: '#73848e', 'stroke-opacity': 0.65, 'stroke-width': 1 }, null, pattern);
      rect(doc, svg, { x: 0, y: 0, width: layout.width, height: layout.height }, { fill: '#fff', rx: 0 });
      drawProfile(svg); drawSections(svg);
      overlayLayer = svgNode(doc, 'g', { class: 'bp-overlay', 'pointer-events': 'none' }, null, svg);
      tooltipLayer = svgNode(doc, 'g', { class: 'bp-tooltip-layer', 'pointer-events': 'none' }, null, svg);
    }
    function slotRect(s) {
      if (!s) return null;
      var direct = layout.slots.find(function (r) { return key(r.slot) === key(s); });
      if (direct) return direct;
      // A 40 ft box can be addressed through either visible odd-bay section.
      if (+s.bay % 2 === 0) return layout.slots.find(function (r) {
        return Math.abs(+r.slot.bay - +s.bay) === 1 && +r.slot.row === +s.row && +r.slot.tier === +s.tier;
      }) || null;
      return null;
    }
    function pointFor(s) {
      var r = slotRect(s) || profileRect(s);
      return r ? center(r) : { x: 8, y: 176 };
    }
    function focusBay(bay) {
      if (preGenerateMode) return api;
      var explicit = bay != null && String(bay).trim() !== '' && bayMap.has(+bay);
      if (explicit) selectedBays = [+bay];
      else chooseChanges();
      render();
      if (selectedBays.length) emit('bay', selectedBays[0]);
      return api;
    }
    function cancelDrag() {
      if (!drag) return;
      var pointerId = drag.pointerId; drag = null;
      if (svg.releasePointerCapture) { try { svg.releasePointerCapture(pointerId); } catch (_) { /* pointer may be released */ } }
      render();
    }
    function startDrag(e, at, r) {
      if (!onDrop || animation || (e.button != null && e.button !== 0) || isClosed(at, at.box_id)) return;
      if (e.preventDefault) e.preventDefault();
      drag = { box_id: at.box_id, from: cloneSlot(at), rect: Object.assign({}, r), start: svgPoint(e), pointerId: e.pointerId, active: false };
      if (svg.setPointerCapture) { try { svg.setPointerCapture(e.pointerId); } catch (_) { /* detached SVG shim */ } }
    }
    function moveDrag(e) {
      if (!drag || e.pointerId !== drag.pointerId) return;
      var p = svgPoint(e);
      if (!drag.active && Math.hypot(p.x - drag.start.x, p.y - drag.start.y) < 4) return;
      if (!drag.active) { drag.active = true; render(); }
      clear(overlayLayer);
      var r = drag.rect;
      drawBox(overlayLayer, { x: p.x - r.width / 2, y: p.y - r.height / 2, width: r.width, height: r.height },
        Object.assign({ box_id: drag.box_id }, drag.from), false, 'bp-drag-ghost');
      var proposed = dropTarget(p, drag.box_id, allowSwap), targetRect = proposed && slotRect(proposed.to);
      if (targetRect) rect(doc, overlayLayer, { x: targetRect.x + 1, y: targetRect.y + 1, width: targetRect.width - 2, height: targetRect.height - 2 },
        { class: 'bp-snap', fill: 'none', stroke: '#176698', 'stroke-width': 2, 'stroke-dasharray': '4 2' });
    }
    function endDrag(e) {
      if (!drag || e.pointerId !== drag.pointerId) return;
      var saved = drag, proposed = saved.active ? dropTarget(svgPoint(e), saved.box_id, allowSwap) : null;
      var target = proposed && proposed.to;
      if (saved.active) suppressNextClick();
      cancelDrag();
      if (saved.active && target && key(target) !== key(saved.from)) {
        var drop = { box_id: saved.box_id, from: cloneSlot(saved.from), to: cloneSlot(target) };
        if (proposed.swap_with != null) drop.swap_with = proposed.swap_with;
        onDrop(drop);
      }
      else if (!saved.active) { suppressNextClick(); select(saved.box_id, saved.from); }
    }
    function escape(e) {
      if (e.key === 'Escape' && drag) { suppressNextClick(); cancelDrag(); if (e.preventDefault) e.preventDefault(); }
    }
    rootListen('pointermove', moveDrag); rootListen('pointerup', endDrag);
    rootListen('pointercancel', cancelDrag); rootListen('lostpointercapture', function () { if (drag) cancelDrag(); });
    rootListen('keydown', escape); listen(doc, 'keydown', escape);
    function animateMove(move, duration, token, swapping, timeline, index) {
      // Rebuild first: playback may just have changed mode or the expanded bay.
      // All animation anchors must refer to the geometry currently on screen.
      animation = { box_id: move.box_id, swap_with: swapping && swapping.box_id }; render();
      var from = cloneSlot(move.from), to = cloneSlot(move.to);
      var source = slotRect(from) || profileRect(from), target = slotRect(to) || profileRect(to);
      var start = from ? pointFor(from) : { x: -24, y: target ? target.cy : 176 };
      var finish = to ? pointFor(to) : { x: layout.width + 24, y: source ? source.cy : 176 };
      if (to && !swapping && (!from || timeline)) start = { x: finish.x, y: Math.max(24, finish.y - Math.max(48, (target ? target.height : 28) * 2)) };
      var visible = (timeline || !from) ? target || source : source || target;
      var w = (visible || { width: 48 }).width, h = (visible || { height: 28 }).height;
      var swapStart = swapping && pointFor(swapping), swapFinish = swapping && pointFor(from);
      var swapRect = swapping && (slotRect(swapping) || profileRect(swapping));
      return new Promise(function (resolve) {
        var started;
        function step(now) {
          if (token !== playToken || destroyed) { resolve(false); return; }
          if (timeline) {
            if (timeline.start == null) timeline.start = now;
            timeline.now = now;
            started = timeline.start + index * duration;
          } else if (started == null) started = now;
          var t = duration ? Math.min(1, Math.max(0, (now - started) / duration)) : 1;
          var eased = t * t * (3 - 2 * t);
          clear(overlayLayer);
          if (move.box_id == null) {
            var operation = String(move.kind || 'Hatch operation').replace(/_/g, ' ');
            operation = operation.charAt(0).toUpperCase() + operation.slice(1);
            var text = operation + (move.cover_id ? ' · ' + move.cover_id : '');
            var indicator = svgNode(doc, 'g', { class: 'bp-hatch-operation', role: 'status', 'aria-label': text }, null, overlayLayer);
            rect(doc, indicator, { x: 200, y: 144, width: 360, height: 28 }, { fill: '#fff', stroke: '#73848e', 'stroke-width': 1, rx: 3 });
            label(doc, indicator, 380, 162, text, { 'text-anchor': 'middle', fill: '#243b49', 'font-size': 12 });
          } else {
            drawBox(overlayLayer, { x: start.x + (finish.x - start.x) * eased - w / 2,
              y: start.y + (finish.y - start.y) * eased - h / 2, width: w, height: h },
            Object.assign({ box_id: move.box_id }, to || from || { bay: 0, row: 0, tier: 0 }), false, 'bp-moving-box');
            if (swapping) {
              var sw = swapRect ? swapRect.width : w, sh = swapRect ? swapRect.height : h;
              drawBox(overlayLayer, { x: swapStart.x + (swapFinish.x - swapStart.x) * eased - sw / 2,
                y: swapStart.y + (swapFinish.y - swapStart.y) * eased - sh / 2, width: sw, height: sh },
              Object.assign({ box_id: swapping.box_id }, from), false, 'bp-moving-box');
            }
          }
          if (t < 1) frame(step); else resolve(true);
        }
        if (timeline && timeline.now != null) step(timeline.now); else frame(step);
      });
    }
    function frame(fn) {
      if (global.requestAnimationFrame) global.requestAnimationFrame(fn);
      else global.setTimeout(function () { fn(Date.now()); }, 16);
    }
    var api = {
      preGenerate: function (input) {
        playToken++; animation = null; activeBay = null; cancelDrag();
        preGenerateMode = true; selectedBays = []; planMeta = {};
        Object.keys(highlights).forEach(function (kind) { highlights[kind].clear(); });
        marks.clear();
        var initial = Array.isArray(input) ? input : (input && input.initial_slots) || [];
        slots = initial.map(function (at) { return Object.assign(cloneSlot(at), { box_id: at.box_id }); });
        render(); return api;
      },
      setPlan: function (plan, opts) {
        if (opts && opts.preGenerate) return api.preGenerate(Array.isArray(plan) ? plan : (plan && (plan.initial_slots || plan.slots)) || []);
        if (preGenerateMode) selectedBays = bays.slice(0, 2).map(function (b) { return +b.bay; });
        preGenerateMode = false; activeBay = null;
        playToken++; animation = null; cancelDrag();
        plan = plan || {}; planMeta = Object.assign({}, plan); delete planMeta.slots;
        slots = (plan.slots || []).map(function (s) { return Object.assign(cloneSlot(s), { box_id: s.box_id }); });
        render(); return api;
      },
      showBays: function (list) {
        selectedBays = (preGenerateMode ? [] : list || []).map(number).filter(function (n, i, all) { return bayMap.has(n) && all.indexOf(n) === i; });
        render(); return api;
      },
      focusBay: focusBay,
      focusChanges: function () { chooseChanges(); render(); return api; },
      closeBay: function (bay, text) { closed.set(+bay, text || 'Bay ' + pad(bay) + ' · closed'); render(); return api; },
      openBay: function (bay) { closed.delete(+bay); render(); return api; },
      highlight: function (ids, kind) {
        if (!highlights[kind]) throw new Error('Unknown bay-plan highlight: ' + kind);
        highlights[kind] = new Set((ids || []).map(String));
        if (kind === 'forced' || kind === 'moved') chooseChanges();
        render(); return api;
      },
      play: async function (moves, opts) {
        opts = opts || {}; cancelDrag();
        if (preGenerateMode) selectedBays = bays.slice(0, 2).map(function (b) { return +b.bay; });
        preGenerateMode = false;
        var token = ++playToken, sequence = Array.isArray(moves) ? moves : (moves && moves.moves) || [];
        // Replan highlights describe the proposal's changed cargo. Crane replay
        // also loads unchanged cargo; its temporary outlines must not inflate
        // those counts when choosing the resulting plan's bay.
        var suppliedHighlights = highlights.forced.size || highlights.moved.size ? {
          forced: new Set(highlights.forced), moved: new Set(highlights.moved)
        } : null;
        var speed = Number(opts.speed); if (!(speed > 0)) speed = 1;
        var longPlay = sequence.length >= 40;
        var explicitSpeed = Number(opts.speed) > 0;
        var budget = Number(opts.durationMs);
        var duration = opts.durationMs != null && Number.isFinite(budget) && budget >= 0 ? budget / Math.max(1, sequence.length) : longPlay && !explicitSpeed ? 10000 / sequence.length : 320 / speed;
        var timeline = longPlay ? { start: null, now: null } : null, completed = 0;
        // Array order is the exported move order; `i` is provenance, not an index to sort.
        for (var index = 0; index < sequence.length; index++) {
          if (token !== playToken || destroyed) break;
          var move = sequence[index], swapping = null;
          if (longPlay && move.to && bayMap.has(+move.to.bay) && !closed.has(+move.to.bay)) {
            activeBay = +move.to.bay; selectedBays = [activeBay];
          }
          if (move.swap_with != null) {
            swapping = swapCounterpart(move.box_id, cloneSlot(move.from), cloneSlot(move.to), move.swap_with);
            if (!swapping) { animation = null; render(); throw new Error('Invalid bay-plan swap for ' + move.box_id + ' and ' + move.swap_with); }
          }
          var done = await animateMove(move, duration, token, swapping, timeline, index);
          if (!done || token !== playToken || destroyed) break;
          if (move.box_id != null) {
            slots = slots.filter(function (s) { return String(s.box_id) !== String(move.box_id) && (!swapping || String(s.box_id) !== String(swapping.box_id)); });
            if (move.to) slots.push(Object.assign(cloneSlot(move.to), { box_id: move.box_id }));
            if (swapping) {
              slots.push(Object.assign(cloneSlot(move.from), { box_id: swapping.box_id }));
              highlights.moved.add(String(swapping.box_id));
            }
            highlights.moved.add(String(move.box_id));
            if (move.forced) highlights.forced.add(String(move.box_id));
          }
          animation = null;
          completed++; render();
          if (opts.onMove) opts.onMove(move, completed, sequence.length);
          emit('move', move, completed, sequence.length);
        }
        if (token === playToken) {
          animation = null; activeBay = null;
          if (completed === sequence.length) {
            if (suppliedHighlights) {
              highlights.forced = suppliedHighlights.forced;
              highlights.moved = suppliedHighlights.moved;
            }
            // A prior stress view or the last animated destination must not own
            // the generated-plan focus. An explicit request remains authoritative.
            var requestedBay = opts.focusBay != null ? opts.focusBay : opts.bay;
            focusBay(requestedBay);
          } else render();
        }
        return { completed: completed, total: sequence.length, cancelled: token !== playToken || destroyed };
      },
      stop: function () { playToken++; animation = null; activeBay = null; render(); return api; },
      enableDrag: function (callback, opts) {
        allowSwap = !!(opts && opts.allowSwap);
        onDrop = typeof callback === 'function' ? callback : null;
        if (host.classList) { if (onDrop) host.classList.add('bp-drag-enabled'); else host.classList.remove('bp-drag-enabled'); }
        return api;
      },
      // Updates are local to this view. Unknown IDs are ignored rather than creating cargo.
      updateBoxes: function (overrides) {
        Object.keys(overrides || {}).forEach(function (id) {
          var change = overrides[id];
          if (!Object.prototype.hasOwnProperty.call(originalBoxes, id) || !change || typeof change !== 'object' || Array.isArray(change)) return;
          var next = merge(boxes[id], change);
          if (!Object.prototype.hasOwnProperty.call(change, 'dg_class') && Object.prototype.hasOwnProperty.call(change, 'dg')) {
            if (change.dg == null) next.dg_class = null;
            else if (Object.prototype.hasOwnProperty.call(change.dg, 'imdg_class')) next.dg_class = change.dg.imdg_class;
          }
          if (!Object.prototype.hasOwnProperty.call(change, 'weight_t') && Object.prototype.hasOwnProperty.call(change, 'weight_kg')) next.weight_t = change.weight_kg == null ? null : +change.weight_kg / 1000;
          boxes[id] = next;
        });
        cancelDrag(); render(); return api;
      },
      resetBoxes: function () { boxes = copy(originalBoxes); cancelDrag(); render(); return api; },
      markViolation: function (s, text) { marks.set(key(cloneSlot(s)), String(text || 'Rule violation')); render(); return api; },
      clearMarks: function () { marks.clear(); highlights.violation.clear(); render(); return api; },
      on: function (event, fn) {
        if (!handlers.has(event)) handlers.set(event, []);
        handlers.get(event).push(fn); return api;
      },
      off: function (event, fn) { handlers.set(event, (handlers.get(event) || []).filter(function (f) { return f !== fn; })); return api; },
      screenPoint: function (slotOrBoxId) {
        var s = typeof slotOrBoxId === 'object' ? cloneSlot(slotOrBoxId) : cloneSlot(boxSlot(slotOrBoxId));
        if (!s) return null;
        var p = pointFor(s);
        if (svg.createSVGPoint && svg.getScreenCTM) {
          var matrix = svg.getScreenCTM();
          if (matrix) { var sp = svg.createSVGPoint(); sp.x = p.x; sp.y = p.y; var transformed = sp.matrixTransform(matrix); return { x: transformed.x, y: transformed.y }; }
        }
        var r = svg.getBoundingClientRect ? svg.getBoundingClientRect() : { left: 0, top: 0, width: layout.width, height: layout.height };
        var scale = Math.min((r.width || layout.width) / layout.width, (r.height || layout.height) / layout.height);
        return { x: r.left + ((r.width || layout.width) - layout.width * scale) / 2 + p.x * scale,
          y: r.top + ((r.height || layout.height) - layout.height * scale) / 2 + p.y * scale };
      },
      geometry: function () { return JSON.parse(JSON.stringify(layout)); },
      snap: snap,
      getPlan: function () { return Object.assign({}, planMeta, { slots: slots.map(function (s) { return Object.assign({}, s); }) }); },
      toSVG: function () { return svg.outerHTML || (global.XMLSerializer ? new global.XMLSerializer().serializeToString(svg) : ''); },
      destroy: function () {
        destroyed = true; playToken++; drag = null; handlers.clear();
        if (svg.removeEventListener) svgListeners.forEach(function (entry) { svg.removeEventListener(entry[0], entry[1]); });
        svgListeners = [];
        if (host.classList) host.classList.remove('bp-drag-enabled');
        if (doc.removeEventListener) doc.removeEventListener('keydown', escape);
        if (svg !== host && svg.parentNode) svg.parentNode.removeChild(svg); else clear(svg);
      }
    };
    if (options.preGenerate) api.preGenerate(options.initial_slots || []); else render();
    return api;
  }

  function legend(host, options) {
    options = options || {};
    var doc = host.ownerDocument || global.document;
    var pods = Array.isArray(options) ? options : options.pods || latestPods;
    clear(host);
    var root = doc.createElement('div'); root.setAttribute('class', 'bp-legend');
    root.setAttribute('aria-label', 'Bay plan legend'); host.appendChild(root);
    function item(symbol, text, colour, kind) {
      var entry = doc.createElement('span'); entry.setAttribute('class', 'bp-legend-item');
      var mark = doc.createElement('span'); mark.setAttribute('class', 'bp-legend-' + (colour ? 'swatch' : 'symbol') + (kind ? ' bp-' + kind : ''));
      if (colour) mark.setAttribute('style', 'display:inline-block;width:12px;height:12px;border-radius:2px;background:' + colour + ';border:1px solid #7b929e');
      mark.textContent = symbol || ''; entry.appendChild(mark);
      var textNode = doc.createElement('span'); textNode.textContent = text; entry.appendChild(textNode); root.appendChild(entry);
    }
    pods.forEach(function (p, i) { item('', p.code + (p.name_words ? ' · ' + p.name_words : ''), p.colour || FALLBACK[i % FALLBACK.length]); });
    item('▯', '20′'); item('⊟', '40′ paired bays'); item('45′', '45′ extended'); item('◇3', 'DG class'); item('❄', 'Reefer');
    item('□', 'Selected', null, 'selected'); item('┄', 'Forced', null, 'forced');
    item('▣', 'Moved', null, 'moved'); item('□!', 'Violation', null, 'violation');
    return root;
  }
  global.BayPlan = { create: create, legend: legend };
})(typeof window !== 'undefined' ? window : globalThis);
