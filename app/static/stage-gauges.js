/* StowLM stage: physics gauges and small charts (Canvas 2D). Values come from bundle.physics.frames only. */
(function () {
  'use strict';
  const L = (window.StageLib = window.StageLib || {});
  const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif';
  const C = { text: '#dfe8f6', muted: '#8193ae', dim: '#4d5e78', grid: 'rgba(120,150,200,0.12)', ok: '#36d49b', fail: '#ff5b5b', sodium: '#ffa53a', blue: '#5fa8ff', band: 'rgba(54,212,155,0.08)' };
  const num = (v) => typeof v === 'number' && isFinite(v);
  const fmt = (v, d = 2) => (num(v) ? v.toFixed(d) : '—');

  function prep(c) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = c.clientWidth || 100, h = c.clientHeight || 60;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    g.textBaseline = 'alphabetic';
    return { g, w, h };
  }
  function title(g, t, sub, w, bad) {
    g.font = `700 10px ${FONT}`; g.fillStyle = C.muted; g.textAlign = 'left'; g.fillText(t.toUpperCase(), 9, 15);
    if (sub) {
      const room = 9 + g.measureText(t.toUpperCase()).width + 8; // a narrow gauge (v6 column) puts the note on its own line
      g.font = `10px ${FONT}`; g.fillStyle = bad ? C.fail : C.dim; g.textAlign = 'right';
      g.fillText(sub, w - 8, w - 8 - g.measureText(sub).width < room ? 27 : 15);
    }
  }
  function na(g, w, h, msg) { // word-wrapped reason, at most 4 lines
    g.font = `11px ${FONT}`; g.fillStyle = C.dim; g.textAlign = 'center';
    const words = String(msg || 'not available').split(/\s+/), lines = [];
    let cur = '';
    for (const wd of words) { const t = cur ? cur + ' ' + wd : wd; if (g.measureText(t).width > w - 20 && cur) { lines.push(cur); cur = wd; } else cur = t; }
    if (cur) lines.push(cur);
    if (lines.length > 4) { lines.length = 4; lines[3] += ' …'; }
    const y0 = h / 2 + 8 - ((lines.length - 1) * 14) / 2;
    lines.forEach((l, i) => g.fillText(l, w / 2, y0 + i * 14));
  }

  function gm(c, v) {
    const { g, w, h } = prep(c);
    const bad = num(v.gm) && num(v.min) && v.gm < v.min;
    title(g, 'GM', num(v.min) ? `min ${fmt(v.min)} m` : 'min —', w, false);
    if (!num(v.gm)) return na(g, w, h, v.reason || 'GM not reported');
    const cx = w / 2, cy = h - 34, r = Math.min(w / 2 - 22, h - 58);
    const max = Math.max(v.scaleMax || 0, v.gm * 1.25, (v.min || 0) * 3, 1);
    const ang = (x) => Math.PI + (Math.max(0, Math.min(max, x)) / max) * Math.PI;
    g.lineCap = 'butt';
    g.lineWidth = 9; g.strokeStyle = 'rgba(120,150,200,0.13)'; g.beginPath(); g.arc(cx, cy, r, Math.PI, 2 * Math.PI); g.stroke();
    if (num(v.min)) { g.strokeStyle = 'rgba(255,91,91,0.75)'; g.beginPath(); g.arc(cx, cy, r, Math.PI, ang(v.min)); g.stroke(); }
    const grd = g.createLinearGradient(cx - r, 0, cx + r, 0); grd.addColorStop(0, '#2b8f6b'); grd.addColorStop(1, C.ok);
    g.strokeStyle = bad ? C.fail : num(v.min) ? grd : C.blue; g.beginPath(); g.arc(cx, cy, r, num(v.min) ? ang(v.min) : Math.PI, ang(v.gm)); g.stroke();
    // ticks
    g.lineWidth = 1; g.strokeStyle = C.dim; g.fillStyle = C.dim; g.font = `9px ${FONT}`; g.textAlign = 'center';
    const step = max > 4 ? 1 : 0.5;
    for (let t = 0; t <= max + 1e-6; t += step) { const a = ang(t); g.beginPath(); g.moveTo(cx + Math.cos(a) * (r - 8), cy + Math.sin(a) * (r - 8)); g.lineTo(cx + Math.cos(a) * (r - 13), cy + Math.sin(a) * (r - 13)); g.stroke(); g.fillText(String(t), cx + Math.cos(a) * (r - 21), cy + Math.sin(a) * (r - 21) + 3); }
    // needle
    const a = ang(num(v.needle) ? v.needle : v.gm); // needle: motion only, the printed figure stays v.gm
    g.strokeStyle = '#fff'; g.lineWidth = 2; g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx + Math.cos(a) * (r + 2), cy + Math.sin(a) * (r + 2)); g.stroke();
    g.fillStyle = '#fff'; g.beginPath(); g.arc(cx, cy, 3.5, 0, 7); g.fill();
    g.font = `700 22px ${FONT}`; g.fillStyle = bad ? C.fail : C.text; g.textAlign = 'center'; g.fillText(`${fmt(v.gm)} m`, cx, h - 10);
  }

  function heel(c, v) {
    const { g, w, h } = prep(c);
    const deg = v.heel;
    const side = !num(deg) ? '' : Math.abs(deg) < 0.005 ? 'upright' : deg > 0 ? 'to port' : 'to starboard';
    title(g, 'Heel / list', num(v.trim) ? `trim ${fmt(v.trim)} m ${v.trim > 0.005 ? 'by stern' : v.trim < -0.005 ? 'by head' : ''}` : 'trim —', w);
    if (!num(deg)) return na(g, w, h, 'heel not reported');
    const cx = w / 2, cy = 24, r = Math.max(20, Math.min(w / 2 - 26, h - 66));
    const range = Math.max(5, Math.ceil(Math.abs(deg) / 5) * 5);
    // fixed scale (bottom arc, ±range); positive heel (to port) swings the plumb to the left
    g.strokeStyle = 'rgba(120,150,200,0.28)'; g.lineWidth = 1;
    g.beginPath(); g.arc(cx, cy, r, Math.PI * 0.5 - 0.9, Math.PI * 0.5 + 0.9); g.stroke();
    g.font = `9px ${FONT}`; g.fillStyle = C.dim; g.textAlign = 'center';
    for (const t of [-range, -range / 2, 0, range / 2, range]) {
      const a = Math.PI / 2 - (t / range) * 0.9;
      g.beginPath(); g.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r); g.lineTo(cx + Math.cos(a) * (r - 6), cy + Math.sin(a) * (r - 6)); g.stroke();
      g.fillText(`${Math.abs(t)}°`, cx + Math.cos(a) * (r + 10), cy + Math.sin(a) * (r + 10) + 3);
    }
    // ship section rotated by the measured heel (no exaggeration), pointer reads the scale
    g.save(); g.translate(cx, cy); g.rotate((-(num(v.needle) ? v.needle : deg) * Math.PI) / 180);
    const bw = r * 0.95, bh = r * 0.36, oy = r * 0.5;
    g.fillStyle = 'rgba(255,165,58,0.8)'; for (let i = -2; i <= 2; i++) g.fillRect(i * bw * 0.17 - bw * 0.07, oy - bh * 0.1 - bh * 0.5, bw * 0.14, bh * 0.46);
    g.fillStyle = '#21385e'; g.strokeStyle = '#7aa6e8'; g.lineWidth = 1.2;
    g.beginPath(); g.moveTo(-bw / 2, oy - bh * 0.1); g.lineTo(bw / 2, oy - bh * 0.1); g.lineTo(bw / 2 - 4, oy + bh * 0.55); g.quadraticCurveTo(0, oy + bh * 0.75, -bw / 2 + 4, oy + bh * 0.55); g.closePath(); g.fill(); g.stroke();
    g.strokeStyle = '#fff'; g.lineWidth = 1.6; g.beginPath(); g.moveTo(0, oy + bh * 0.7); g.lineTo(0, r - 3); g.stroke();
    g.restore();
    g.strokeStyle = 'rgba(95,168,255,0.5)'; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(cx - r * 0.7, cy + r * 0.5 + 0.5); g.lineTo(cx + r * 0.7, cy + r * 0.5 + 0.5); g.stroke(); g.setLineDash([]);
    g.font = `700 20px ${FONT}`; g.fillStyle = C.text; g.textAlign = 'left';
    g.fillText(`${fmt(Math.abs(deg))}°`, 9, h - 9);
    g.font = `10.5px ${FONT}`; g.fillStyle = C.muted; g.textAlign = 'right'; g.fillText(side, w - 9, h - 10);
  }

  function draught(c, v) {
    const { g, w, h } = prep(c);
    title(g, 'Draught', num(v.max) ? `max ${fmt(v.max)} m` : 'max —', w);
    const vals = [['AFT', v.aft], ['MEAN', v.mean], ['FWD', v.fwd]];
    if (!vals.some((x) => num(x[1]))) return na(g, w, h, 'draught not reported');
    const top = 30, bot = h - 34, scale = Math.max(...vals.map((x) => (num(x[1]) ? x[1] : 0)), num(v.max) ? v.max : 0) * 1.12 || 1;
    const y = (d) => bot - (d / scale) * (bot - top);
    const bw = (w - 40) / 3;
    vals.forEach(([lab, d], i) => {
      const x = 20 + i * bw + bw * 0.2, ww = bw * 0.6;
      g.fillStyle = 'rgba(120,150,200,0.08)'; g.fillRect(x, top, ww, bot - top);
      if (num(d)) {
        const over = num(v.max) && d > v.max;
        const gr = g.createLinearGradient(0, y(d), 0, bot); gr.addColorStop(0, over ? C.fail : '#4f9cf5'); gr.addColorStop(1, over ? '#7a1f1f' : '#183d70');
        g.fillStyle = gr; g.fillRect(x, y(d), ww, bot - y(d));
        g.font = `700 12.5px ${FONT}`; g.fillStyle = over ? '#fff' : C.text; g.textAlign = 'center'; g.fillText(fmt(d), x + ww / 2, bot - y(d) > 26 ? y(d) + 15 : y(d) - 4);
      }
      g.font = `9.5px ${FONT}`; g.fillStyle = C.dim; g.textAlign = 'center'; g.fillText(lab, x + ww / 2, bot + 13);
    });
    if (num(v.max)) {
      g.strokeStyle = C.fail; g.setLineDash([5, 3]); g.lineWidth = 1.2; g.beginPath(); g.moveTo(12, y(v.max)); g.lineTo(w - 12, y(v.max)); g.stroke(); g.setLineDash([]);
    }
    g.font = `10px ${FONT}`; g.fillStyle = C.dim; g.textAlign = 'center';
    g.fillText(num(v.disp) ? `Δ ${Math.round(v.disp).toLocaleString('en-US')} t` : '', w / 2, h - 6);
  }

  function line(c, v) {
    const { g, w, h } = prep(c);
    const xs = v.xs || [], ys = v.ys || [];
    const sc = v.scale || 1;
    const { text: status, bad } = xs.length ? window.StageRules.curveStatus(v) : { text: '', bad: false };
    title(g, v.title, status, w, bad);
    if (!xs.length || !ys.length) return na(g, w, h, v.reason || 'not reported');
    const pad = { l: 34, r: 8, t: 24, b: 18 };
    const all = ys.concat(v.lo || [], v.hi || []).filter(num);
    let lo = Math.min(0, ...all) / sc, hi = Math.max(0, ...all) / sc;
    if (hi - lo < 1e-9) hi = lo + 1;
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const X = (x) => pad.l + ((x - x0) / (x1 - x0 || 1)) * (w - pad.l - pad.r);
    const Y = (y) => pad.t + (1 - (y / sc - lo) / (hi - lo)) * (h - pad.t - pad.b);
    g.strokeStyle = C.grid; g.lineWidth = 1; g.beginPath(); g.moveTo(pad.l, Y(0)); g.lineTo(w - pad.r, Y(0)); g.stroke();
    // allowed band
    if (v.lo && v.hi && v.lo.length === xs.length) {
      g.fillStyle = C.band; g.beginPath();
      xs.forEach((x, i) => (i ? g.lineTo(X(x), Y(v.hi[i])) : g.moveTo(X(x), Y(v.hi[i]))));
      for (let i = xs.length - 1; i >= 0; i--) g.lineTo(X(xs[i]), Y(v.lo[i]));
      g.closePath(); g.fill();
      g.strokeStyle = 'rgba(255,91,91,0.55)'; g.setLineDash([3, 3]);
      for (const arr of [v.lo, v.hi]) { g.beginPath(); xs.forEach((x, i) => (i ? g.lineTo(X(x), Y(arr[i])) : g.moveTo(X(x), Y(arr[i])))); g.stroke(); }
      g.setLineDash([]);
    }
    g.strokeStyle = bad ? C.fail : C.sodium; g.lineWidth = 2; g.beginPath();
    xs.forEach((x, i) => (i ? g.lineTo(X(x), Y(ys[i])) : g.moveTo(X(x), Y(ys[i]))));
    g.stroke();
    g.font = `9px ${FONT}`; g.fillStyle = C.dim; g.textAlign = 'right';
    g.fillText(hi.toFixed(hi >= 100 ? 0 : 1), pad.l - 4, pad.t + 6); g.fillText(lo.toFixed(Math.abs(lo) >= 100 ? 0 : 1), pad.l - 4, h - pad.b);
    g.textAlign = 'left'; g.fillText(v.unit || '', 4, h - 4);
    g.textAlign = 'right'; g.fillText('aft → fwd', w - pad.r, h - 4);
  }

  function gz(c, v) {
    const { g, w, h } = prep(c);
    const pts = (v.pts || []).filter((p) => num(p.deg) && num(p.m));
    let maxP = null; for (const p of pts) if (!maxP || p.m > maxP.m) maxP = p;
    title(g, 'GZ curve', maxP ? `max ${fmt(maxP.m, 2)} m @ ${Math.round(maxP.deg)}°` : '', w);
    if (pts.length < 2) return na(g, w, h, 'GZ curve not reported');
    const pad = { l: 30, r: 10, t: 24, b: 20 };
    const d1 = Math.max(...pts.map((p) => p.deg)), m1 = Math.max(0.1, ...pts.map((p) => p.m)) * 1.12, m0 = Math.min(0, ...pts.map((p) => p.m));
    const X = (d) => pad.l + (d / d1) * (w - pad.l - pad.r), Y = (m) => pad.t + (1 - (m - m0) / (m1 - m0)) * (h - pad.t - pad.b);
    g.strokeStyle = C.grid; g.lineWidth = 1; g.beginPath();
    for (let d = 0; d <= d1; d += 10) { g.moveTo(X(d), pad.t); g.lineTo(X(d), h - pad.b); }
    g.moveTo(pad.l, Y(0)); g.lineTo(w - pad.r, Y(0)); g.stroke();
    const grd = g.createLinearGradient(0, pad.t, 0, h - pad.b); grd.addColorStop(0, 'rgba(95,168,255,0.35)'); grd.addColorStop(1, 'rgba(95,168,255,0.02)');
    g.fillStyle = grd; g.beginPath(); g.moveTo(X(pts[0].deg), Y(0)); for (const p of pts) g.lineTo(X(p.deg), Y(p.m)); g.lineTo(X(pts[pts.length - 1].deg), Y(0)); g.closePath(); g.fill();
    g.strokeStyle = C.blue; g.lineWidth = 2; g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(X(p.deg), Y(p.m)) : g.moveTo(X(p.deg), Y(p.m)))); g.stroke();
    if (maxP) { g.fillStyle = '#fff'; g.beginPath(); g.arc(X(maxP.deg), Y(maxP.m), 3, 0, 7); g.fill(); }
    g.font = `9px ${FONT}`; g.fillStyle = C.dim; g.textAlign = 'center';
    for (let d = 0; d <= d1; d += 20) g.fillText(`${d}°`, X(d), h - 6);
    g.textAlign = 'right'; g.fillText(m1.toFixed(1), pad.l - 4, pad.t + 6); g.fillText('m', pad.l - 4, h - pad.b);
  }

  L.gauges = { gm, heel, draught, line, gz, num, fmt };
})();
