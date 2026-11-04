/* StowLM stage: isometric ship + quay + crane renderer (Canvas 2D, no dependencies).
 * The renderer only draws what the bundle and the replayed state say; it never computes physics or rules.
 * World frame follows src/stowage/conventions.py: x forward of AP, y positive to port, z up. The quay is on the
 * port side (far side, +y) so the near starboard hull can be drawn as a cut-away showing the holds. */
(function () {
  'use strict';
  const L = (window.StageLib = window.StageLib || {});

  // Oblique projection: screen = o + s * (x*AX + y*BX, x*AY + y*BY - z)
  const AX = 1, AY = 0.055, BX = 0.46, BY = -0.30;
  const VIEW = (() => { // direction toward the viewer (for painter's sort)
    let d = [-BX, AX, AX * BY - BX * AY];
    if (d[2] < 0) d = d.map((v) => -v);
    return d;
  })();
  const BOX_W = 2.438, LEN = { 20: 6.058, 40: 12.192, 45: 13.716 }, CELL20 = 6.058;
  const QZ = 2.4; // quay apron height above water (visual only)

  function hexRgb(h) {
    const m = /^#?([0-9a-f]{6})$/i.exec(h || '');
    if (!m) return [128, 140, 160];
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const css = (c, a) => (a == null ? `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})` : `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`);
  const mul = (c, f) => [Math.min(255, c[0] * f), Math.min(255, c[1] * f), Math.min(255, c[2] * f)];
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  const LIGHT = [255, 214, 160];
  const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
  const clamp01 = (t) => (t < 0 ? 0 : t > 1 ? 1 : t);
  const lerp = (a, b, t) => a + (b - a) * t;
  function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
  const DG_FILL = { 1: '#ff8a1c', 2: '#e8413c', 3: '#e8413c', 4: '#e8413c', 5: '#ffd21f', 6: '#f4f4f4', 7: '#ffd21f', 8: '#f4f4f4', 9: '#f4f4f4' };

  function pointInPoly(px, py, pts) {
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  L.createScene = function createScene(canvas, model) {
    const ctx = canvas.getContext('2d');
    const geo = model.bundle.geometry || {};
    const slotsById = new Map();
    for (const s of geo.slots || []) slotsById.set(String(s.id), s);
    const slotList = [...slotsById.values()];
    const isDeck = (s) => (s.on_deck != null ? !!s.on_deck : Number(s.tier) >= 80);

    // ---------- derived ship geometry (ship coordinates, z from keel) ----------
    const hull0 = geo.hull || {};
    let maxX = -1e9, minX = 1e9, maxY = 0, minDeckZ = 1e9, maxHoldTop = 0, maxDeckTop = 0;
    for (const s of slotList) {
      const x = +s.x_m, y = +s.y_m, z = +s.z_m;
      maxX = Math.max(maxX, x); minX = Math.min(minX, x); maxY = Math.max(maxY, Math.abs(y));
      if (isDeck(s)) { minDeckZ = Math.min(minDeckZ, z); maxDeckTop = Math.max(maxDeckTop, z + 2.9); } else maxHoldTop = Math.max(maxHoldTop, z + 2.6);
    }
    if (!slotList.length) { maxX = 100; minX = 20; maxY = 8; minDeckZ = 10; maxDeckTop = 15; maxHoldTop = 9; }
    const LOA = +hull0.loa_m || maxX + 14;
    const BEAM = +hull0.beam_m || 2 * maxY + 2.6;
    const DEPTH = +hull0.depth_m || (minDeckZ < 1e9 ? minDeckZ - 0.6 : maxHoldTop + 0.4);
    if (!maxDeckTop) maxDeckTop = DEPTH + 3;
    const HB = BEAM / 2;
    function hb(x) {
      const t = x / LOA;
      if (t < 0) return HB * 0.8;
      if (t < 0.07) return HB * (0.84 + 0.16 * Math.sqrt(t / 0.07));
      if (t < 0.7) return HB;
      if (t >= 1) return 0;
      const u = (t - 0.7) / 0.3;
      return HB * Math.sqrt(Math.max(0, 1 - u * u * u * 0.98 - u * u * 0.02));
    }
    const hbBottom = (x) => (x / LOA > 0.93 ? 0 : hb(x) * (x / LOA < 0.1 ? 0.6 + 0.3 * (x / LOA) / 0.1 : 0.9));

    function cellExt(s, lenFt) {
      const x = +s.x_m, y = +s.y_m, z = +s.z_m;
      const len = LEN[lenFt] || CELL20;
      return { x0: x - len / 2, x1: x + len / 2, y0: y - BOX_W / 2, y1: y + BOX_W / 2, z0: z, z1: z + 2.591, deck: isDeck(s) };
    }
    // BAPLIE slot id -> extents (40' boxes are addressed by the even bay and span the odd bays either side)
    const extCache = new Map();
    function slotExt(slotId, box) {
      const key = slotId + '|' + (box ? box.length_ft : '');
      if (extCache.has(key)) return extCache.get(key);
      let e = null;
      const s = slotsById.get(String(slotId));
      const h = box && box.height_m ? +box.height_m : 2.591;
      const lenFt = box ? +box.length_ft || 20 : 20;
      if (s && (+s.bay % 2 === 1 || lenFt === 20)) {
        e = cellExt(s, lenFt === 20 ? 20 : lenFt);
      } else {
        const m = /^(\d{3})(\d{2})(\d{2})$/.exec(String(slotId));
        if (m) {
          const bay = +m[1], row = m[2], tier = m[3];
          const a = slotsById.get(String(bay - 1).padStart(3, '0') + row + tier), b = slotsById.get(String(bay + 1).padStart(3, '0') + row + tier);
          const base = a || b || s;
          if (base) {
            const cx = a && b ? (+a.x_m + +b.x_m) / 2 : +base.x_m;
            const len = LEN[lenFt] || LEN[40];
            e = { x0: cx - len / 2, x1: cx + len / 2, y0: +base.y_m - BOX_W / 2, y1: +base.y_m + BOX_W / 2, z0: +base.z_m, z1: +base.z_m + 2.591, deck: isDeck(base) };
          }
        }
      }
      if (e) e.z1 = e.z0 + h;
      extCache.set(key, e);
      return e;
    }
    function cellsOf(slotId, box) { // odd-bay cell ids a box occupies
      const m = /^(\d{3})(\d{2})(\d{2})$/.exec(String(slotId));
      if (!m) return [String(slotId)];
      const bay = +m[1];
      if (bay % 2 === 0) return [String(bay - 1).padStart(3, '0') + m[2] + m[3], String(bay + 1).padStart(3, '0') + m[2] + m[3]];
      return [String(slotId)];
    }

    // hatch covers
    const covers = new Map();
    for (const c of geo.hatch_covers || []) {
      const bays = new Set((c.bays || []).map(Number)), rows = new Set((c.rows || []).map(Number));
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9, top = 1e9, n = 0;
      for (const s of slotList) {
        const match = (bays.size && rows.size) ? bays.has(+s.bay) && rows.has(+s.row) : s.hatch_cover_id === c.id;
        if (!match) continue;
        const e = cellExt(s, 20);
        x0 = Math.min(x0, e.x0); x1 = Math.max(x1, e.x1); y0 = Math.min(y0, e.y0); y1 = Math.max(y1, e.y1); n++;
        if (isDeck(s)) top = Math.min(top, +s.z_m);
      }
      if (!n) continue;
      if (top > 1e8) top = DEPTH + 0.7;
      top = Math.max(top, DEPTH + 0.3);
      covers.set(c.id, { id: c.id, x0: x0 - 0.25, x1: x1 + 0.25, y0: y0 - 0.2, y1: y1 + 0.2, z0: top - 0.55, z1: top });
    }

    // ---------- projection / fit ----------
    let W = 0, H = 0, dpr = 1, s = 8, ox = 0, oy = 0;
    const REF_DRAUGHT = (model.refDraught && model.refDraught()) || DEPTH * 0.55;
    const Y1 = HB + 4.5, Y2 = HB + 27, ZG = 33, Y_COVER = HB + 9.5, Y_TRUCK = HB + 17.5, Y_BUF = HB + 22.5;
    const proj = (x, y, z) => [ox + s * (x * AX + y * BX), oy + s * (x * AY + y * BY - z)];
    function fit() {
      const pts = [];
      const zTop = maxDeckTop - REF_DRAUGHT;
      for (const x of [-4, LOA + 2]) for (const y of [-HB, HB]) for (const z of [-REF_DRAUGHT - 1, zTop + 1]) pts.push([x, y, z]);
      pts.push([LOA * 0.45, Y2 + 4, ZG - 4], [LOA * 0.45, -HB - 9, ZG - 6], [-2, Y_BUF + 4, QZ]);
      let a = 1e9, b = -1e9, c = 1e9, d = -1e9;
      for (const [x, y, z] of pts) { const X = x * AX + y * BX, Y = x * AY + y * BY - z; a = Math.min(a, X); b = Math.max(b, X); c = Math.min(c, Y); d = Math.max(d, Y); }
      const padL = 26, padR = 30, padT = 34, padB = 58;
      s = Math.min((W - padL - padR) / (b - a), (H - padT - padB) / (d - c));
      ox = padL + ((W - padL - padR) - s * (b - a)) / 2 - s * a;
      oy = padT + ((H - padT - padB) - s * (d - c)) / 2 - s * c;
    }

    // ---------- primitive drawing ----------
    function poly(g, pts) { g.beginPath(); g.moveTo(pts[0][0], pts[0][1]); for (let i = 1; i < pts.length; i++) g.lineTo(pts[i][0], pts[i][1]); g.closePath(); }
    function faces(e) {
      const { x0, x1, y0, y1, z0, z1 } = e;
      return {
        top: [proj(x0, y0, z1), proj(x1, y0, z1), proj(x1, y1, z1), proj(x0, y1, z1)],
        front: [proj(x0, y0, z0), proj(x1, y0, z0), proj(x1, y0, z1), proj(x0, y0, z1)],
        end: [proj(x1, y0, z0), proj(x1, y1, z0), proj(x1, y1, z1), proj(x1, y0, z1)],
      };
    }
    const depthKey = (e) => ((e.x0 + e.x1) / 2) * VIEW[0] + ((e.y0 + e.y1) / 2) * VIEW[1] + ((e.z0 + e.z1) / 2) * VIEW[2];
    function solid(g, e, rgb, opt = {}) {
      const f = faces(e);
      const top = opt.top || mix(mul(rgb, 1.08), LIGHT, 0.16);
      g.fillStyle = css(mul(rgb, 0.86), opt.alpha); poly(g, f.front); g.fill();
      g.fillStyle = css(mul(rgb, 0.6), opt.alpha); poly(g, f.end); g.fill();
      g.fillStyle = css(top, opt.alpha); poly(g, f.top); g.fill();
      if (opt.stroke !== false) {
        g.strokeStyle = opt.stroke || 'rgba(0,0,0,0.5)'; g.lineWidth = opt.lw || 0.8;
        poly(g, f.front); g.stroke(); poly(g, f.end); g.stroke(); poly(g, f.top); g.stroke();
      }
      return f;
    }
    function containerBox(g, e, box, opt = {}) {
      const rgb = opt.rgb || model.podRgb(box && box.pod);
      const f = solid(g, e, rgb, opt);
      const pxLen = (e.x1 - e.x0) * s;
      // corrugation
      if (pxLen > 22 && opt.detail !== false) {
        g.strokeStyle = 'rgba(0,0,0,0.16)'; g.lineWidth = 1;
        const n = Math.max(4, Math.round((e.x1 - e.x0) / 0.75));
        g.beginPath();
        for (let i = 1; i < n; i++) {
          const x = lerp(e.x0, e.x1, i / n), a = proj(x, e.y0, e.z0 + 0.12), b = proj(x, e.y0, e.z1 - 0.12);
          g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]);
        }
        g.stroke();
        // door bars on the visible end
        g.strokeStyle = 'rgba(0,0,0,0.28)';
        g.beginPath();
        for (const t of [0.33, 0.67]) { const y = lerp(e.y0, e.y1, t), a = proj(e.x1, y, e.z0 + 0.15), b = proj(e.x1, y, e.z1 - 0.15); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); }
        g.stroke();
      }
      if (box && box.dg && opt.marks !== false) {
        const c = proj(e.x1 - 1.1, e.y0, (e.z0 + e.z1) / 2), r = Math.max(4, 0.62 * s);
        g.fillStyle = DG_FILL[String(box.dg.class || '').charAt(0)] || '#f4f4f4';
        g.beginPath(); g.moveTo(c[0], c[1] - r); g.lineTo(c[0] + r, c[1]); g.lineTo(c[0], c[1] + r); g.lineTo(c[0] - r, c[1]); g.closePath(); g.fill();
        g.strokeStyle = '#1b1b1b'; g.lineWidth = Math.max(1, r * 0.14); g.stroke();
        if (r >= 6.5) { g.fillStyle = '#111'; g.font = `700 ${Math.round(r * 0.85)}px ${model.font}`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(String(box.dg.class).split('.')[0], c[0], c[1] + 0.5); }
      }
      if (box && box.reefer && opt.marks !== false) {
        const c = proj(e.x0 + 1.0, e.y0, (e.z0 + e.z1) / 2), r = Math.max(3.5, 0.55 * s);
        g.strokeStyle = '#e9fbff'; g.lineWidth = Math.max(1.1, r * 0.2); g.lineCap = 'round';
        g.beginPath();
        for (let k = 0; k < 3; k++) { const a = (k * Math.PI) / 3; g.moveTo(c[0] - r * Math.cos(a), c[1] - r * Math.sin(a)); g.lineTo(c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)); }
        g.stroke(); g.lineCap = 'butt';
      }
      return f;
    }
    function glowDot(g, x, y, r, rgb, a) {
      const gr = g.createRadialGradient(x, y, 0, x, y, r);
      gr.addColorStop(0, css(rgb, a)); gr.addColorStop(0.25, css(rgb, a * 0.35)); gr.addColorStop(1, css(rgb, 0));
      g.fillStyle = gr; g.fillRect(x - r, y - r, 2 * r, 2 * r);
    }
    function line3(g, a, b) { const p = proj(...a), q = proj(...b); g.moveTo(p[0], p[1]); g.lineTo(q[0], q[1]); }

    // ---------- layers ----------
    const mk = () => document.createElement('canvas');
    const bg = mk(), shipL = mk(), xrayL = mk();
    const lctx = (c) => { const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); return g; };

    function drawBackground() {
      const g = lctx(bg);
      g.clearRect(0, 0, W, H);
      // sky + sea
      const horizon = proj(LOA / 2, Y2 + 70, 0)[1];
      let gr = g.createLinearGradient(0, 0, 0, H);
      gr.addColorStop(0, '#040a17'); gr.addColorStop(Math.max(0.05, Math.min(0.9, horizon / H)), '#0b1a33'); gr.addColorStop(1, '#03101f');
      g.fillStyle = gr; g.fillRect(0, 0, W, H);
      // haze glow over the terminal
      gr = g.createRadialGradient(W * 0.55, horizon, 10, W * 0.55, horizon, W * 0.6);
      gr.addColorStop(0, 'rgba(255,150,60,0.16)'); gr.addColorStop(1, 'rgba(255,150,60,0)');
      g.fillStyle = gr; g.fillRect(0, 0, W, H);
      // stars
      const r = rng(11);
      g.fillStyle = 'rgba(200,220,255,0.5)';
      for (let i = 0; i < 70; i++) { const x = r() * W, y = r() * horizon * 0.7; g.fillRect(x, y, r() < 0.2 ? 1.5 : 1, 1); }
      // sea surface (below quay edge): shimmering lines
      const r2 = rng(5);
      g.lineWidth = 1;
      for (let i = 0; i < 150; i++) {
        const y = -HB - 70 + r2() * (Y1 + 60), x = -40 + r2() * (LOA + 120);
        const a = proj(x, y, 0), b = proj(x + 3 + r2() * 9, y, 0);
        g.strokeStyle = `rgba(${r2() < 0.25 ? '255,170,90' : '120,160,220'},${0.05 + r2() * 0.1})`;
        g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke();
      }
      // distant terminal: decorative stacks (not data) + light poles
      const r3 = rng(21);
      const deco = [];
      for (let row = 0; row < 3; row++) for (let x = -50; x < LOA + 90; x += 6.6) {
        if (r3() < 0.18) continue;
        const tiers = 1 + Math.floor(r3() * 4), y = Y2 + 32 + row * 3.2;
        for (let t = 0; t < tiers; t++) deco.push({ x0: x, x1: x + 6.06, y0: y, y1: y + 2.44, z0: QZ + t * 2.59, z1: QZ + (t + 1) * 2.59, c: [[70, 90, 120], [90, 70, 80], [60, 95, 95], [100, 90, 70]][Math.floor(r3() * 4)] });
      }
      deco.sort((a, b) => depthKey(a) - depthKey(b));
      for (const e of deco) solid(g, e, e.c, { alpha: 0.42, stroke: 'rgba(0,0,0,0.25)', lw: 0.5 });
      // quay apron
      const Q0 = HB + 1.8, Q1 = Y2 + 46;
      g.fillStyle = '#141d2b';
      poly(g, [proj(-80, Q0, QZ), proj(LOA + 120, Q0, QZ), proj(LOA + 120, Q1, QZ), proj(-80, Q1, QZ)]); g.fill();
      gr = g.createLinearGradient(0, proj(0, Q1, QZ)[1], 0, proj(0, Q0, QZ)[1]);
      gr.addColorStop(0, 'rgba(255,170,80,0.0)'); gr.addColorStop(1, 'rgba(255,170,80,0.07)');
      g.fillStyle = gr; poly(g, [proj(-80, Q0, QZ), proj(LOA + 120, Q0, QZ), proj(LOA + 120, Q1, QZ), proj(-80, Q1, QZ)]); g.fill();
      // quay wall face
      g.fillStyle = '#2a3445';
      poly(g, [proj(-80, Q0, -3), proj(LOA + 120, Q0, -3), proj(LOA + 120, Q0, QZ), proj(-80, Q0, QZ)]); g.fill();
      g.strokeStyle = 'rgba(255,205,80,0.75)'; g.lineWidth = 1.5;
      g.beginPath(); line3(g, [-80, Q0 + 0.3, QZ], [LOA + 120, Q0 + 0.3, QZ]); g.stroke();
      g.fillStyle = '#0b0d12';
      for (let x = -76; x < LOA + 118; x += 9) poly(g, [proj(x, Q0 - 0.4, -1), proj(x + 1.6, Q0 - 0.4, -1), proj(x + 1.6, Q0 - 0.4, QZ - 0.2), proj(x, Q0 - 0.4, QZ - 0.2)]), g.fill();
      // crane rails + lane lines
      g.strokeStyle = 'rgba(160,180,210,0.35)'; g.lineWidth = 1.2;
      g.beginPath(); line3(g, [-80, Y1, QZ], [LOA + 120, Y1, QZ]); line3(g, [-80, Y2, QZ], [LOA + 120, Y2, QZ]); g.stroke();
      g.strokeStyle = 'rgba(255,200,70,0.28)'; g.setLineDash([10, 9]); g.lineWidth = 1;
      g.beginPath(); for (const y of [Y_TRUCK - 2, Y_TRUCK + 2, Y_BUF + 2.4]) line3(g, [-80, y, QZ], [LOA + 120, y, QZ]); g.stroke(); g.setLineDash([]);
      // berth name painted on the apron
      const port = model.portLabel();
      if (port) {
        const o = proj(LOA * 0.06, Y2 + 7, QZ), ex = [AX * s, AY * s], ey = [-BX * s, -BY * s];
        g.save();
        g.setTransform(dpr * ex[0], dpr * ex[1], dpr * ey[0] * 0.9, dpr * ey[1] * 0.9, dpr * o[0], dpr * o[1]);
        g.font = `800 4.6px ${model.font}`; g.fillStyle = 'rgba(220,230,245,0.10)'; g.textBaseline = 'middle';
        g.fillText(port, 0, 0);
        g.restore();
      }
      // light poles
      for (let x = -30; x < LOA + 60; x += 38) {
        const base = proj(x, Y2 + 30, QZ), top = proj(x, Y2 + 30, 30);
        g.strokeStyle = 'rgba(120,135,160,0.5)'; g.lineWidth = 1.2; g.beginPath(); g.moveTo(base[0], base[1]); g.lineTo(top[0], top[1]); g.stroke();
        g.globalCompositeOperation = 'lighter';
        glowDot(g, top[0], top[1], 30, [255, 160, 70], 0.55);
        glowDot(g, top[0], top[1], 6, [255, 220, 160], 0.9);
        g.globalCompositeOperation = 'source-over';
      }
    }

    // state-dependent ship layer
    let hits = [], extByBox = new Map(), curDraught = REF_DRAUGHT, coverPos = new Map(), bufPos = new Map(), lastState = null;
    const Z = (z) => z - curDraught; // ship -> world z
    const wz = (e) => ({ ...e, z0: Z(e.z0), z1: Z(e.z1) });

    function quayLayout(state) {
      coverPos = new Map(); bufPos = new Map();
      const covStacks = new Map();
      for (const id of state.coversOff) {
        const c = covers.get(id); if (!c) continue;
        const k = Math.round((c.x0 + c.x1) / 2 / 4);
        const n = covStacks.get(k) || 0; covStacks.set(k, n + 1);
        const w = c.y1 - c.y0;
        coverPos.set(id, { x0: c.x0, x1: c.x1, y0: Y_COVER - w / 2, y1: Y_COVER + w / 2, z0: QZ + n * 0.6, z1: QZ + n * 0.6 + 0.55 });
      }
      const bufStacks = new Map();
      for (const b of state.buffer) {
        const from = state.bufferFrom.get(b);
        const e0 = from ? slotExt(from, model.boxes[b]) : null;
        const cx = e0 ? (e0.x0 + e0.x1) / 2 : LOA / 2;
        const len = LEN[(model.boxes[b] || {}).length_ft] || CELL20;
        const k = Math.round(cx / 7);
        const n = bufStacks.get(k) || 0; bufStacks.set(k, n + 1);
        bufPos.set(b, { x0: cx - len / 2, x1: cx + len / 2, y0: Y_BUF - BOX_W / 2, y1: Y_BUF + BOX_W / 2, z0: QZ + n * 2.6, z1: QZ + n * 2.6 + 2.591 });
      }
    }

    function hullPts(side, z, x0 = 0, x1 = LOA, n = 48, f = hb) {
      const pts = [];
      for (let i = 0; i <= n; i++) { const x = lerp(x0, x1, i / n); pts.push([x, side * f(x), z]); }
      return pts;
    }

    function drawShip(state, exclude) {
      const g = lctx(shipL);
      g.clearRect(0, 0, W, H);
      hits = []; extByBox = new Map();
      quayLayout(state);
      const D = Z(DEPTH), K = Z(0);
      // quay items: covers then buffered boxes
      const qItems = [];
      for (const [id, e] of coverPos) if (exclude.cover !== id) qItems.push({ e, cover: id });
      for (const [b, e] of bufPos) if (exclude.box !== b) qItems.push({ e, box: b });
      qItems.sort((a, b) => depthKey(a.e) - depthKey(b.e));
      for (const it of qItems) {
        if (it.cover) drawCover(g, it.e);
        else { const f = containerBox(g, it.e, model.boxes[it.box]); hits.push({ box: it.box, f }); extByBox.set(it.box, it.e); ringRestow(g, it.e); }
      }
      // far-side bow exterior (faces +x)
      const fb = hullPts(1, D + 0.4, LOA * 0.7, LOA, 18).map((p) => proj(...p));
      const fbB = hullPts(1, K, LOA * 0.7, LOA * 0.93, 14, hbBottom).reverse().map((p) => proj(...p));
      g.fillStyle = '#16233a'; poly(g, fb.concat(fbB)); g.fill();
      // hold interior (far wall + floor)
      g.fillStyle = '#0a1424';
      poly(g, hullPts(1, D, 0, LOA, 40).concat(hullPts(1, K + 0.6, 0, LOA, 40, hbBottom).reverse()).map((p) => proj(...p))); g.fill();
      g.fillStyle = '#0c182b';
      poly(g, hullPts(1, K + 0.6, 0, LOA, 40, hbBottom).concat(hullPts(-1, K + 0.6, 0, LOA, 40, hbBottom).reverse()).map((p) => proj(...p))); g.fill();
      // hold boxes
      const hold = [], deck = [];
      for (const [slot, b] of state.ship) {
        if (exclude.box === b) continue;
        const box = model.boxes[b];
        const e = slotExt(slot, box); if (!e) continue;
        (e.deck ? deck : hold).push({ e: wz(e), box: b });
      }
      hold.sort((a, b) => depthKey(a.e) - depthKey(b.e));
      for (const it of hold) { const f = containerBox(g, it.e, model.boxes[it.box]); glowBox(g, f, it.box); hits.push({ box: it.box, f, hold: true }); extByBox.set(it.box, it.e); }
      // deck plane with hatch openings
      const outline = hullPts(-1, D, 0, LOA, 48).concat(hullPts(1, D, 0, LOA, 48).reverse()).map((p) => proj(...p));
      g.beginPath();
      g.moveTo(outline[0][0], outline[0][1]); for (const p of outline) g.lineTo(p[0], p[1]); g.closePath();
      const holes = [];
      for (const c of covers.values()) {
        const h = [proj(c.x0 + 0.15, c.y0 + 0.15, D), proj(c.x1 - 0.15, c.y0 + 0.15, D), proj(c.x1 - 0.15, c.y1 - 0.15, D), proj(c.x0 + 0.15, c.y1 - 0.15, D)];
        holes.push(h); g.moveTo(h[0][0], h[0][1]); for (const p of h) g.lineTo(p[0], p[1]); g.closePath();
      }
      g.fillStyle = '#1b2a42'; g.fill('evenodd');
      hits.push({ occluder: true, polys: [outline], holes });
      // coamings
      g.strokeStyle = 'rgba(255,190,90,0.35)'; g.lineWidth = 1;
      for (const h of holes) { poly(g, h); g.stroke(); }
      // near hull wall: glass cut-away
      const top = hullPts(-1, D + 0.3, 0, LOA, 48), bot = hullPts(-1, K, 0, LOA * 0.93, 40, hbBottom);
      const wall = top.concat(bot.reverse()).map((p) => proj(...p));
      const ytop = proj(LOA / 2, -HB, D)[1], ybot = proj(LOA / 2, -HB, K)[1];
      let gr = g.createLinearGradient(0, ytop, 0, ybot);
      gr.addColorStop(0, 'rgba(36,58,96,0.70)'); gr.addColorStop(0.18, 'rgba(26,44,76,0.42)'); gr.addColorStop(1, 'rgba(20,32,58,0.30)');
      g.fillStyle = gr; poly(g, wall); g.fill();
      g.strokeStyle = 'rgba(140,175,230,0.55)'; g.lineWidth = 1.3; poly(g, wall); g.stroke();
      // frames
      g.strokeStyle = 'rgba(140,175,230,0.10)'; g.lineWidth = 1; g.beginPath();
      for (let x = 6; x < LOA * 0.9; x += 6.5) line3(g, [x, -hb(x), D], [x, -hbBottom(x), K]);
      g.stroke();
      // sheer strake (solid band at the deck edge)
      const sheer = hullPts(-1, D + 0.3, 0, LOA, 48).concat(hullPts(-1, D - 1.1, 0, LOA, 48).reverse()).map((p) => proj(...p));
      g.fillStyle = '#24375a'; poly(g, sheer); g.fill();
      g.strokeStyle = 'rgba(255,255,255,0.35)'; g.lineWidth = 1; g.beginPath();
      const sl = hullPts(-1, D + 0.3, 0, LOA, 48).map((p) => proj(...p)); g.moveTo(sl[0][0], sl[0][1]); for (const p of sl) g.lineTo(p[0], p[1]); g.stroke();
      // forecastle bulwark
      const fc = hullPts(-1, D + 2.4, LOA * 0.86, LOA, 14).concat(hullPts(-1, D + 0.3, LOA * 0.86, LOA, 14).reverse()).map((p) => proj(...p));
      g.fillStyle = '#2a3f66'; poly(g, fc); g.fill();
      // superstructure (aft), only if there is room behind the cargo
      let aftCargo = 1e9; for (const s2 of slotList) aftCargo = Math.min(aftCargo, +s2.x_m - 3.1);
      if (aftCargo > 11) {
        const xa = 2.5, xb = Math.min(aftCargo - 1.6, 16);
        const blocks = [
          { x0: xa, x1: xb, y0: -HB * 0.82, y1: HB * 0.82, z0: D + 0.3, z1: D + 9.5 },
          { x0: xa + 1, x1: xb - 0.4, y0: -HB, y1: HB, z0: D + 9.5, z1: D + 12.2 },
        ];
        for (const e of blocks) {
          solid(g, e, [196, 204, 218], { stroke: 'rgba(0,0,0,0.4)' });
          // lit windows (decorative)
          const r = rng(3 + Math.round(e.z0));
          for (let z = e.z0 + 1.2; z < e.z1 - 0.8; z += 2.6) for (let x = e.x0 + 0.8; x < e.x1 - 0.6; x += 1.6) {
            const on = r() < 0.6;
            const a = proj(x, e.y0, z), b = proj(x + 0.9, e.y0, z + 0.9);
            g.fillStyle = on ? 'rgba(255,210,140,0.9)' : 'rgba(30,45,70,0.9)';
            g.fillRect(a[0], b[1], Math.max(1, b[0] - a[0]), Math.max(1, a[1] - b[1]));
          }
          const wl = [proj(e.x1, e.y0 + 0.6, e.z1 - 1.6), proj(e.x1, e.y1 - 0.6, e.z1 - 1.6)];
          g.strokeStyle = 'rgba(255,214,150,0.8)'; g.lineWidth = Math.max(1, s * 0.25); g.beginPath(); g.moveTo(wl[0][0], wl[0][1]); g.lineTo(wl[1][0], wl[1][1]); g.stroke();
        }
        solid(g, { x0: xa + 0.4, x1: xa + 3.6, y0: -1.6, y1: 1.6, z0: D + 12.2, z1: D + 16 }, [32, 44, 70]);
        solid(g, { x0: xa + 0.4, x1: xa + 3.6, y0: -1.6, y1: 1.6, z0: D + 14.2, z1: D + 15.1 }, [255, 140, 40], { stroke: false });
        const mast = proj((xa + xb) / 2, 0, D + 17.5);
        g.globalCompositeOperation = 'lighter'; glowDot(g, mast[0], mast[1], 10, [255, 255, 255], 0.9); g.globalCompositeOperation = 'source-over';
      }
      // bow mast light
      const bm = proj(LOA - 5, 0, D + 7);
      g.strokeStyle = 'rgba(160,180,210,0.6)'; g.lineWidth = 1.2; g.beginPath(); line3(g, [LOA - 5, 0, D + 2.4], [LOA - 5, 0, D + 7]); g.stroke();
      g.globalCompositeOperation = 'lighter'; glowDot(g, bm[0], bm[1], 9, [255, 255, 255], 0.85); g.globalCompositeOperation = 'source-over';

      // hatch covers on the ship, then deck boxes
      for (const c of covers.values()) {
        if (state.coversOff.has(c.id) || exclude.cover === c.id) continue;
        const e = wz(c); drawCover(g, e); hits.push({ occluder: true, polys: Object.values(faces(e)) });
      }
      deck.sort((a, b) => depthKey(a.e) - depthKey(b.e));
      for (const it of deck) { const f = containerBox(g, it.e, model.boxes[it.box]); glowBox(g, f, it.box); hits.push({ box: it.box, f }); extByBox.set(it.box, it.e); }
      // water over the submerged hull (tints what is below the waterline)
      const wTop = hullPts(-1, 0, 0, LOA, 48), wBot = hullPts(-1, Math.min(0, K), 0, LOA * 0.93, 40, hbBottom);
      const under = wTop.concat(wBot.reverse()).map((p) => proj(...p));
      gr = g.createLinearGradient(0, proj(LOA / 2, -HB, 0)[1], 0, proj(LOA / 2, -HB, K)[1]);
      gr.addColorStop(0, 'rgba(6,26,52,0.50)'); gr.addColorStop(1, 'rgba(3,14,30,0.86)');
      g.fillStyle = gr; poly(g, under); g.fill();
      const fbu = hullPts(1, 0, LOA * 0.7, LOA, 14).concat(hullPts(1, K, LOA * 0.7, LOA * 0.93, 12, hbBottom).reverse()).map((p) => proj(...p));
      poly(g, fbu); g.fill();
      // waterline foam
      g.strokeStyle = 'rgba(170,210,255,0.45)'; g.lineWidth = 1.2; g.beginPath();
      const wl = hullPts(-1, 0, 0, LOA, 48).map((p) => proj(...p)); g.moveTo(wl[0][0], wl[0][1]); for (const p of wl) g.lineTo(p[0], p[1]); g.stroke();
    }
    // v6 replan: the rule's boxes wear an amber glow, the search's own a quiet blue, on the cached ship layer.
    function glowBox(g, f, id) {
      const strong = morph && morph.glow.has(id);
      if (!strong && !(morph && morph.quiet && morph.quiet.has(id))) return;
      g.fillStyle = strong ? 'rgba(255,165,58,0.62)' : 'rgba(120,160,214,0.20)'; poly(g, f.top); g.fill(); poly(g, f.front); g.fill(); poly(g, f.end); g.fill();
      g.strokeStyle = strong ? '#ffd28a' : 'rgba(150,185,230,0.55)'; g.lineWidth = strong ? 1.8 : 1; poly(g, f.top); g.stroke(); poly(g, f.front); g.stroke(); poly(g, f.end); g.stroke();
    }
    function drawCover(g, e) {
      const f = solid(g, e, [62, 82, 112], { top: [84, 106, 138], stroke: 'rgba(0,0,0,0.55)' });
      g.strokeStyle = 'rgba(10,18,30,0.5)'; g.lineWidth = 1; g.beginPath();
      for (let y = e.y0 + 1.6; y < e.y1 - 0.5; y += 1.6) line3(g, [e.x0 + 0.3, y, e.z1], [e.x1 - 0.3, y, e.z1]);
      g.stroke();
      return f;
    }
    function ringRestow(g, e) { // quay-buffer boxes get an amber frame
      const f = faces(e);
      g.strokeStyle = 'rgba(255,181,71,0.95)'; g.lineWidth = 1.6;
      poly(g, f.front); g.stroke(); poly(g, f.top); g.stroke(); poly(g, f.end); g.stroke();
    }

    // ---------- crane ----------
    function drawCraneBack(g, cx, ty) {
      const st = [126, 146, 176];
      const legs = [[cx - 9, Y1], [cx + 9, Y1], [cx - 9, Y2], [cx + 9, Y2]];
      g.lineCap = 'round';
      const lw = Math.max(2, s * 0.55);
      g.strokeStyle = css(mul(st, 0.75)); g.lineWidth = lw;
      g.beginPath();
      for (const [x, y] of legs) line3(g, [x, y, QZ], [x, y, ZG]);
      g.stroke();
      g.lineWidth = Math.max(1.5, s * 0.4); g.strokeStyle = css(mul(st, 0.85));
      g.beginPath();
      line3(g, [cx - 9, Y1, 12], [cx - 9, Y2, 12]); line3(g, [cx + 9, Y1, 12], [cx + 9, Y2, 12]);
      line3(g, [cx - 9, Y2, ZG], [cx + 9, Y2, ZG]); line3(g, [cx - 9, Y1, ZG], [cx + 9, Y1, ZG]);
      line3(g, [cx - 9, Y1, ZG], [cx - 9, Y2, ZG]); line3(g, [cx + 9, Y1, ZG], [cx + 9, Y2, ZG]);
      // A-frame + stays
      const apex = [cx, (Y1 + Y2) / 2 + 2, ZG + 15];
      line3(g, [cx - 9, Y1, ZG], apex); line3(g, [cx + 9, Y1, ZG], apex); line3(g, [cx - 9, Y2, ZG], apex); line3(g, [cx + 9, Y2, ZG], apex);
      g.stroke();
      g.lineWidth = 1; g.strokeStyle = css(mul(st, 0.9), 0.8); g.beginPath();
      line3(g, apex, [cx, -HB - 9, ZG + 1.6]); line3(g, apex, [cx, Y2 + 14, ZG + 1.6]); line3(g, apex, [cx, Y1 - 12, ZG + 1.6]);
      g.stroke();
      g.lineCap = 'butt';
      // boom girder
      solid(g, { x0: cx - 1.3, x1: cx + 1.3, y0: -HB - 9, y1: Y2 + 14, z0: ZG, z1: ZG + 1.6 }, st, { top: [170, 186, 210], stroke: 'rgba(0,0,0,0.4)' });
      // machinery house
      solid(g, { x0: cx - 3.5, x1: cx + 3.5, y0: Y2 + 1, y1: Y2 + 9, z0: ZG + 1.6, z1: ZG + 5.2 }, [200, 208, 220], { stroke: 'rgba(0,0,0,0.4)' });
      // trolley + cab
      solid(g, { x0: cx - 2, x1: cx + 2, y0: ty - 1.8, y1: ty + 1.8, z0: ZG - 1.2, z1: ZG }, [255, 160, 50], { stroke: 'rgba(0,0,0,0.5)' });
      const cab = { x0: cx - 1.4, x1: cx + 0.6, y0: ty - 2.6, y1: ty - 0.8, z0: ZG - 3.4, z1: ZG - 1.2 };
      solid(g, cab, [200, 208, 220]);
      const cw = proj(cab.x0 + 0.3, cab.y0, cab.z0 + 1.3), cw2 = proj(cab.x1 - 0.2, cab.y0, cab.z0 + 1.9);
      g.fillStyle = 'rgba(255,214,150,0.95)'; g.fillRect(cw[0], cw2[1], cw2[0] - cw[0], cw[1] - cw2[1]);
      // lights
      g.globalCompositeOperation = 'lighter';
      for (const y of [-HB - 6, -HB + 3, HB - 2, Y1 + 6]) { const p = proj(cx, y, ZG - 0.2); glowDot(g, p[0], p[1], 26, [255, 165, 60], 0.5); glowDot(g, p[0], p[1], 4, [255, 230, 180], 1); }
      for (const [x, y] of legs) { const p = proj(x, y, ZG + 0.4); glowDot(g, p[0], p[1], 14, [255, 165, 60], 0.45); }
      const ap = proj(...apex); glowDot(g, ap[0], ap[1], 10, [255, 50, 40], 0.9);
      g.globalCompositeOperation = 'source-over';
    }
    function drawCraneFront(g, cx, ty, spreader) {
      // light cone + cables + spreader. spreader = {x0,x1,y0,y1,z} world
      const tz = ZG - 1.2;
      const t = proj(cx, ty, tz);
      const a = proj(spreader.x0, spreader.y0, spreader.z), b = proj(spreader.x1, spreader.y0, spreader.z), c = proj(spreader.x1, spreader.y1, spreader.z), d = proj(spreader.x0, spreader.y1, spreader.z);
      g.globalCompositeOperation = 'lighter';
      const gr = g.createLinearGradient(t[0], t[1], t[0], Math.max(a[1], b[1]));
      gr.addColorStop(0, 'rgba(255,170,70,0.20)'); gr.addColorStop(1, 'rgba(255,170,70,0.03)');
      g.fillStyle = gr; g.beginPath(); g.moveTo(t[0] - 3, t[1]); g.lineTo(t[0] + 3, t[1]); g.lineTo(b[0] + 14, b[1] + 6); g.lineTo(a[0] - 14, a[1] + 6); g.closePath(); g.fill();
      g.globalCompositeOperation = 'source-over';
      g.strokeStyle = 'rgba(210,220,235,0.85)'; g.lineWidth = 1; g.beginPath();
      const t1 = proj(cx - 0.8, ty - 0.8, tz), t2 = proj(cx + 0.8, ty - 0.8, tz), t3 = proj(cx + 0.8, ty + 0.8, tz), t4 = proj(cx - 0.8, ty + 0.8, tz);
      g.moveTo(t1[0], t1[1]); g.lineTo(a[0], a[1]); g.moveTo(t2[0], t2[1]); g.lineTo(b[0], b[1]); g.moveTo(t3[0], t3[1]); g.lineTo(c[0], c[1]); g.moveTo(t4[0], t4[1]); g.lineTo(d[0], d[1]);
      g.stroke();
      g.strokeStyle = '#ffb347'; g.lineWidth = Math.max(1.6, s * 0.28); poly(g, [a, b, c, d]); g.stroke();
    }

    // ---------- positions for a moving item ----------
    function truckBox(x, box) {
      const len = LEN[(box || {}).length_ft] || CELL20;
      return { x0: x - len / 2, x1: x + len / 2, y0: Y_TRUCK - BOX_W / 2, y1: Y_TRUCK + BOX_W / 2, z0: QZ + 1.5, z1: QZ + 1.5 + ((box && +box.height_m) || 2.591) };
    }
    function drawTruck(g, e) {
      const cx = (e.x0 + e.x1) / 2, len = e.x1 - e.x0;
      solid(g, { x0: cx - len / 2 - 0.2, x1: cx + len / 2 + 0.2, y0: Y_TRUCK - 1.2, y1: Y_TRUCK + 1.2, z0: QZ + 0.6, z1: QZ + 1.5 }, [40, 44, 52]);
      solid(g, { x0: cx + len / 2 + 0.6, x1: cx + len / 2 + 3.2, y0: Y_TRUCK - 1.25, y1: Y_TRUCK + 1.25, z0: QZ + 0.6, z1: QZ + 3.4 }, [230, 160, 40]);
    }
    const centre = (e) => [(e.x0 + e.x1) / 2, (e.y0 + e.y1) / 2];
    function pathFor(stepObj, state) {
      // returns {src: ext, dst: ext, item: {box|cover}, truckAt: 'src'|'dst'|null} in world coords, or null if not a quay-crane lift
      const k = stepObj.kind, box = stepObj.box ? model.boxes[stepObj.box] : null;
      const shipExt = (slot) => { const e = slot && slotExt(slot, box); return e ? wz(e) : null; };
      if (k === 'hatch_cover_off' || k === 'hatch_cover_on') {
        const c = covers.get(stepObj.cover); if (!c) return null;
        const onShip = wz(c);
        // destination on the quay: next free level in that x bucket
        let quay = coverPos.get(stepObj.cover);
        if (!quay) {
          const w = c.y1 - c.y0, kx = Math.round((c.x0 + c.x1) / 2 / 4);
          let n = 0; for (const id of state.coversOff) { const o = covers.get(id); if (o && Math.round((o.x0 + o.x1) / 2 / 4) === kx) n++; }
          quay = { x0: c.x0, x1: c.x1, y0: Y_COVER - w / 2, y1: Y_COVER + w / 2, z0: QZ + n * 0.6, z1: QZ + n * 0.6 + 0.55 };
        }
        return k === 'hatch_cover_off' ? { src: onShip, dst: quay, cover: stepObj.cover } : { src: quay, dst: onShip, cover: stepObj.cover };
      }
      if (!stepObj.box) return null;
      const srcShip = shipExt(model.isShipSlot(stepObj.src) ? stepObj.src : null);
      const dstShip = shipExt(model.isShipSlot(stepObj.dst) ? stepObj.dst : null);
      if (!srcShip && !dstShip) return null; // yard-only move
      let src = srcShip, dst = dstShip, truck = null;
      if (!src) {
        if (bufPos.has(stepObj.box)) src = bufPos.get(stepObj.box);
        else { src = truckBox(centre(dst)[0], box); truck = 'src'; }
      }
      if (!dst) {
        if (k === 'restow_off' || k === 'restow' || /BUF/i.test(String(stepObj.dst || ''))) {
          const cx = centre(src)[0], len = src.x1 - src.x0;
          let n = 0; for (const e of bufPos.values()) if (Math.abs((e.x0 + e.x1) / 2 - cx) < 3.5) n++;
          dst = { x0: cx - len / 2, x1: cx + len / 2, y0: Y_BUF - BOX_W / 2, y1: Y_BUF + BOX_W / 2, z0: QZ + n * 2.6, z1: QZ + n * 2.6 + (src.z1 - src.z0) };
        } else { dst = truckBox(centre(src)[0], box); truck = 'dst'; }
      }
      return { src, dst, box: stepObj.box, truck };
    }
    const clearZ = () => Z(maxDeckTop) + 3.2;
    function movingAt(path, p) {
      // returns {e: ext at phase p, craneX, trolleyY, prevX}
      const [sx, sy] = centre(path.src), [dx, dy] = centre(path.dst);
      const top = Math.max(clearZ(), path.src.z1 + 1.5, path.dst.z1 + 1.5);
      const h = path.src.z1 - path.src.z0;
      let x, y, z0;
      if (p < 0.22) { x = sx; y = sy; z0 = path.src.z0; }
      else if (p < 0.4) { x = sx; y = sy; z0 = lerp(path.src.z0, top, ease((p - 0.22) / 0.18)); }
      else if (p < 0.72) { const u = ease((p - 0.4) / 0.32); x = lerp(sx, dx, u); y = lerp(sy, dy, u); z0 = top; }
      else if (p < 0.94) { x = dx; y = dy; z0 = lerp(top, path.dst.z0, ease((p - 0.72) / 0.22)); }
      else { x = dx; y = dy; z0 = path.dst.z0; }
      const lx = (path.src.x1 - path.src.x0) / 2, ly = (path.src.y1 - path.src.y0) / 2;
      return { e: { x0: x - lx, x1: x + lx, y0: y - ly, y1: y + ly, z0, z1: z0 + h }, x, y };
    }

    // ---------- x-ray layer ----------
    let xrayInfo = null;
    function drawXray(state, info) {
      const g = lctx(xrayL);
      g.clearRect(0, 0, W, H);
      xrayInfo = info;
      if (!info) return;
      g.fillStyle = 'rgba(1,6,12,0.58)'; g.fillRect(0, 0, W, H);
      const occupied = new Set();
      for (const [slot, b] of state.ship) for (const c of cellsOf(slot, model.boxes[b])) occupied.add(c);
      const legal = info.legal ? new Set(info.legal.map(String)) : null;
      const ghosts = [];
      const legalCells = new Set();
      if (legal) for (const id of legal) {
        const e = slotExt(id, model.boxes[info.box] || null); if (!e) continue;
        for (const c of cellsOf(id)) legalCells.add(c);
        ghosts.push({ e: wz(e), kind: 'legal', id });
      }
      if (legal) for (const s2 of slotList) {
        if (occupied.has(String(s2.id)) || legalCells.has(String(s2.id))) continue;
        ghosts.push({ e: wz(cellExt(s2, 20)), kind: 'masked' });
      }
      ghosts.sort((a, b) => depthKey(a.e) - depthKey(b.e));
      // patterned fill for masked slots
      const pat = mk(); pat.width = pat.height = 8;
      const pg = pat.getContext('2d'); pg.strokeStyle = 'rgba(255,90,90,0.20)'; pg.lineWidth = 1; pg.beginPath(); pg.moveTo(0, 8); pg.lineTo(8, 0); pg.stroke();
      const hatch = g.createPattern(pat, 'repeat');
      for (const gh of ghosts) {
        const f = faces(gh.e);
        if (gh.kind === 'legal') {
          g.fillStyle = 'rgba(71,245,160,0.20)'; poly(g, f.front); g.fill(); poly(g, f.top); g.fill(); poly(g, f.end); g.fill();
          g.strokeStyle = 'rgba(71,245,160,0.95)'; g.lineWidth = 1.2; poly(g, f.top); g.stroke(); poly(g, f.front); g.stroke(); poly(g, f.end); g.stroke();
        } else {
          g.fillStyle = hatch; poly(g, f.top); g.fill(); poly(g, f.front); g.fill();
          g.strokeStyle = 'rgba(255,90,90,0.12)'; g.lineWidth = 0.6; poly(g, f.top); g.stroke();
        }
      }
      // occupied boxes re-drawn faintly so the stowage is still readable
      const occ = [];
      for (const [slot, b] of state.ship) { const e = slotExt(slot, model.boxes[b]); if (e) occ.push({ e: wz(e), b }); }
      occ.sort((a, b) => depthKey(a.e) - depthKey(b.e));
      for (const o of occ) { const f = faces(o.e); g.strokeStyle = 'rgba(160,190,230,0.35)'; g.lineWidth = 0.8; poly(g, f.top); g.stroke(); poly(g, f.front); g.stroke(); }
    }

    // ---------- public API ----------
    let curState = null, curExclude = {}, curCraneX = LOA / 2, lastTrolleyY = 0;
    let morph = null; // v6 replan: the scene draws this state and these flying boxes, whatever the replay is doing
    const api = {
      LOA, BEAM, DEPTH,
      resize() {
        const r = canvas.getBoundingClientRect();
        dpr = Math.min(2, window.devicePixelRatio || 1);
        W = Math.max(10, r.width); H = Math.max(10, r.height);
        for (const c of [canvas, bg, shipL, xrayL]) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
        fit(); drawBackground();
        if (curState) { drawShip(curState, curExclude); if (xrayInfo) drawXray(curState, xrayInfo); }
      },
      // { state, glow: Set(box), flyers: [{ box, from: slot, to: slot, p }] }, or null to give the ship back to the replay.
      setMorph(m) { morph = m; if (m) { curState = m.state; curExclude = {}; drawShip(m.state, curExclude); } else if (curState) drawShip(curState, curExclude); },
      setFlyers(list) { if (morph) morph.flyers = list; },
      setFrame(state, opts) {
        if (morph) { if (opts.draught != null) curDraught = opts.draught; drawShip(curState, curExclude); return; }
        curState = state; curExclude = opts.exclude || {}; curDraught = opts.draught != null ? opts.draught : REF_DRAUGHT;
        curCraneX = opts.craneX != null ? opts.craneX : curCraneX;
        drawShip(state, curExclude);
      },
      setXray(info) { if (curState) drawXray(curState, info); else xrayInfo = info; },
      pathFor: (st) => (curState ? pathFor(st, curState) : null),
      shipExtOfSlot(slot, boxId) { const e = slotExt(slot, model.boxes[boxId]); return e ? wz(e) : null; },
      project(x, y, z) { return proj(x, y, z); }, // world -> canvas CSS px (video director camera)
      unproject(px, py, y) { const x = ((px - ox) / s - y * BX) / AX; return { x, z: x * AY + y * BY - (py - oy) / s }; }, // on the plane of constant y
      get pixelScale() { return dpr; }, get scale() { return s; },
      slotList: () => slotList,
      kit: { faces, poly, containerBox, depthKey }, // v6 overlay canvas: draw on top without touching the ship layer
      craneXOfSlot(slot) { const e = slotExt(slot, null); return e ? (e.x0 + e.x1) / 2 : null; },
      coverX(id) { const c = covers.get(id); return c ? (c.x0 + c.x1) / 2 : null; },
      draw(anim, view) {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(bg, 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        let mv = null, craneX = curCraneX, ty = lastTrolleyY || 0;
        if (anim && anim.path) {
          mv = movingAt(anim.path, anim.p);
          const sx = centre(anim.path.src)[0];
          craneX = anim.p < 0.22 ? lerp(anim.prevX != null ? anim.prevX : sx, sx, ease(anim.p / 0.22)) : mv.x;
          ty = anim.p < 0.22 ? lerp(anim.prevY != null ? anim.prevY : 0, centre(anim.path.src)[1], ease(anim.p / 0.22)) : mv.y;
        } else ty = anim && anim.restY != null ? anim.restY : 0;
        drawCraneBack(ctx, craneX, ty);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(shipL, 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        let spreader;
        if (mv) {
          // target marker
          if (anim.p < 0.94) {
            const f = faces(anim.path.dst);
            ctx.setLineDash([4, 4]); ctx.strokeStyle = anim.nonproductive ? 'rgba(255,181,71,0.9)' : 'rgba(255,214,150,0.85)'; ctx.lineWidth = 1.3;
            poly(ctx, f.top); ctx.stroke(); poly(ctx, f.front); ctx.stroke(); ctx.setLineDash([]);
          }
          if (anim.path.truck === 'src' && anim.p < 0.4) drawTruck(ctx, truckBox(centre(anim.path.src)[0], model.boxes[anim.path.box]));
          if (anim.path.truck === 'dst' && anim.p > 0.72) drawTruck(ctx, truckBox(centre(anim.path.dst)[0], model.boxes[anim.path.box]));
          let f;
          if (anim.path.cover) f = drawCover(ctx, mv.e);
          else f = containerBox(ctx, mv.e, model.boxes[anim.path.box]);
          if (anim.nonproductive) {
            ctx.strokeStyle = anim.red ? '#ff5b5b' : '#ffb547'; ctx.lineWidth = 2;
            poly(ctx, f.top); ctx.stroke(); poly(ctx, f.front); ctx.stroke(); poly(ctx, f.end); ctx.stroke();
          }
          spreader = { x0: mv.e.x0 + 0.1, x1: mv.e.x1 - 0.1, y0: mv.e.y0 + 0.05, y1: mv.e.y1 - 0.05, z: mv.e.z1 + 0.25 };
          if (anim.p < 0.22) spreader.z = lerp(clearZ() + 2, mv.e.z1 + 0.25, ease(anim.p / 0.22));
          if (anim.p > 0.94) spreader.z = lerp(mv.e.z1 + 0.25, mv.e.z1 + 4, (anim.p - 0.94) / 0.06);
          if (anim.label) {
            const lp = proj(mv.x, mv.y, mv.e.z1 + 1.2);
            ctx.font = `800 11px ${model.font}`; const w = ctx.measureText(anim.label).width + 12;
            ctx.fillStyle = anim.red ? 'rgba(120,20,24,0.9)' : anim.nonproductive ? 'rgba(90,55,0,0.9)' : 'rgba(10,20,40,0.85)';
            ctx.fillRect(lp[0] - w / 2, lp[1] - 22, w, 17);
            ctx.fillStyle = anim.red ? '#ffb0b0' : anim.nonproductive ? '#ffd28a' : '#dfe8f6'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText(anim.label, lp[0], lp[1] - 13.5);
          }
        } else {
          const hz = clearZ() + 2;
          spreader = { x0: craneX - 3, x1: craneX + 3, y0: ty - 1.2, y1: ty + 1.2, z: hz };
        }
        drawCraneFront(ctx, craneX, ty, spreader);
        lastTrolleyY = ty;
        for (const fl of (morph && morph.flyers) || []) {
          const a = slotExt(fl.from, model.boxes[fl.box]), b = slotExt(fl.to, model.boxes[fl.box]); if (!a || !b) continue;
          const mf = movingAt({ src: wz(a), dst: wz(b) }, fl.p), f = containerBox(ctx, mf.e, model.boxes[fl.box]);
          const strong = morph.glow.has(fl.box);
          ctx.shadowColor = strong ? '#ffa53a' : '#78a0d6'; ctx.shadowBlur = strong ? 14 : 5; ctx.strokeStyle = strong ? '#ffd28a' : 'rgba(170,200,240,0.8)'; ctx.lineWidth = strong ? 1.8 : 1.1;
          poly(ctx, f.top); ctx.stroke(); poly(ctx, f.front); ctx.stroke(); poly(ctx, f.end); ctx.stroke(); ctx.shadowBlur = 0;
        }
        for (const m of (morph && morph.marks) || []) { // drawn through the hull and the covers: the rule's boxes sit in the hold
          const e = slotExt(m.slot, model.boxes[m.box]); if (!e) continue;
          const f = faces(wz(e));
          ctx.shadowColor = '#ffa53a'; ctx.shadowBlur = 18; ctx.fillStyle = 'rgba(255,165,58,0.55)'; ctx.strokeStyle = '#ffd28a'; ctx.lineWidth = 2;
          for (const side of [f.top, f.front, f.end]) { poly(ctx, side); ctx.fill(); ctx.stroke(); }
          ctx.shadowBlur = 0;
        }
        if (view && view.xray) { ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(xrayL, 0, 0); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          if (xrayInfo && xrayInfo.chosen) {
            const e = slotExt(xrayInfo.chosen, model.boxes[xrayInfo.box]);
            if (e) { const f = faces(wz(e)); const pulse = 0.55 + 0.45 * Math.sin(performance.now() / 180);
              ctx.shadowColor = '#47f5a0'; ctx.shadowBlur = 16; ctx.strokeStyle = `rgba(220,255,236,${pulse})`; ctx.lineWidth = 2.4;
              poly(ctx, f.top); ctx.stroke(); poly(ctx, f.front); ctx.stroke(); poly(ctx, f.end); ctx.stroke(); ctx.shadowBlur = 0; }
          }
        }
        for (const [b, col, w] of [[view && view.hover, 'rgba(255,255,255,0.95)', 1.6], [view && view.selected, '#ffb347', 2.4]]) {
          if (!b || !extByBox.has(b)) continue;
          const f = faces(extByBox.get(b));
          ctx.shadowColor = col; ctx.shadowBlur = 10; ctx.strokeStyle = col; ctx.lineWidth = w;
          poly(ctx, f.top); ctx.stroke(); poly(ctx, f.front); ctx.stroke(); poly(ctx, f.end); ctx.stroke(); ctx.shadowBlur = 0;
        }
      },
      hit(px, py) {
        for (let i = hits.length - 1; i >= 0; i--) {
          const h = hits[i];
          if (h.occluder) {
            for (const p of h.polys) if (pointInPoly(px, py, p)) { if (h.holes && h.holes.some((q) => pointInPoly(px, py, q))) continue; return null; }
            continue;
          }
          if (h.hold) { if (pointInPoly(px, py, h.f.front) || pointInPoly(px, py, h.f.top) || pointInPoly(px, py, h.f.end)) return h.box; continue; }
          if (pointInPoly(px, py, h.f.front) || pointInPoly(px, py, h.f.top) || pointInPoly(px, py, h.f.end)) return h.box;
        }
        return null;
      },
      cellsOf,
    };
    return api;
  };
})();
