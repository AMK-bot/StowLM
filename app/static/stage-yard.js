/* StowLM stage: container-yard strip (Canvas 2D). Blocks are drawn as small 2.5-D stack grids; boxes leave as
 * they are loaded, relocations (yard rehandles) arc between stacks in red. Draws only the replayed state. */
(function () {
  'use strict';
  const L = (window.StageLib = window.StageLib || {});
  const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));

  L.createYard = function createYard(canvas, model) {
    const ctx = canvas.getContext('2d');
    const geo = (model.bundle.geometry || {}).yard || {};
    // Older lite-replay bundles listed relocation targets as yard addresses (BLOCK-BBB-RR-TT); fold them onto their
    // stack (BLOCK-BBB-RR) so each ground slot is drawn once.
    const KEY4 = /^([A-Z0-9]+)-(\d+)-(\d+)-(\d+)$/i;
    const allKeys = new Set((geo.stacks || []).map((s) => s.key));
    const stacks = (geo.stacks || []).filter((s) => { const m = KEY4.exec(s.key); return !m || !allKeys.has(`${m[1]}-${m[2]}-${m[3]}`); })
      .map((s) => { const m = KEY4.exec(s.key); return m ? { ...s, key: `${m[1]}-${m[2]}-${m[3]}`, block: m[1], bay: +m[2], row: +m[3] } : s; })
      .map((s) => ({ ...s, bay: +s.bay, row: +s.row, len: +s.length_ft === 40 || +s.length_ft === 45 ? 2 : 1 }));
    // stacks referenced by the state but missing from geometry: synthesise placement from the key
    const known = new Set(stacks.map((s) => s.key));
    for (const k of model.yardKeys()) {
      if (known.has(k)) continue;
      const m = /^([A-Z0-9]+)-(\d+)-(\d+)$/i.exec(k);
      stacks.push({ key: k, block: m ? m[1] : 'YARD', bay: m ? +m[2] : stacks.length + 1, row: m ? +m[3] : 1, len: 1 });
      known.add(k);
    }
    let maxT = +geo.max_tiers || 0;
    for (const k of model.yardKeys()) maxT = Math.max(maxT, model.maxYardHeight(k));
    maxT = Math.max(3, maxT || 5);
    const blocks = new Map();
    for (const s of stacks) {
      let b = blocks.get(s.block);
      if (!b) blocks.set(s.block, (b = { name: s.block, bmin: 1e9, bmax: -1e9, rows: new Set(), stacks: [] }));
      b.bmin = Math.min(b.bmin, s.bay); b.bmax = Math.max(b.bmax, s.bay + s.len - 1); b.rows.add(s.row); b.stacks.push(s);
    }
    const blockList = [...blocks.values()].sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, { numeric: true }));
    for (const b of blockList) { b.rowList = [...b.rows].sort((x, y) => x - y); }
    const byKey = new Map(stacks.map((s) => [s.key, s]));

    let W = 0, H = 0, dpr = 1, cw = 20, ch = 10, offX = 5, offY = 5, place = new Map();
    const layer = document.createElement('canvas');
    const GAP = 14, LABEL = 15;
    // Full-ship yards (hundreds of stacks) collapse to a top-down heat map: one cell per ground slot, shaded by
    // stack height and tinted by the top box's port; rehandles still flash and arc between cells.
    const HEAT_MIN_STACKS = 400;
    let heat = stacks.length > HEAT_MIN_STACKS;
    function layoutHeat() {
      const B = blockList.length || 1;
      const nb = Math.max(1, ...blockList.map((b) => b.bmax - b.bmin + 1)), nr = Math.max(1, ...blockList.map((b) => b.rowList.length));
      const HG = 8, HL = 11;
      let best = { s: 0, C: 1 };
      for (let C = 1; C <= B; C++) {
        const R = Math.ceil(B / C);
        const sx = (W - 10 - (C - 1) * HG) / (C * nb), sy = (H - 16 - (R - 1) * HG - R * HL) / (R * nr);
        const v = Math.min(sx, sy * 1.6);
        if (v > best.s) best = { s: v, C };
      }
      const C = best.C, R = Math.ceil(B / C);
      const cwH = Math.max(1.5, best.s), chH = Math.max(1.5, Math.min(cwH, (H - 16 - (R - 1) * HG - R * HL) / (R * nr)));
      const bw = cwH * nb, bh = chH * nr + HL;
      const totalW = C * bw + (C - 1) * HG, totalH = R * bh + (R - 1) * HG;
      const x0 = (W - totalW) / 2, y0 = Math.max(2, (H - 12 - totalH) / 2);
      cw = cwH; ch = chH / maxT; offX = 0; offY = 0;
      place = new Map();
      blockList.forEach((b, i) => {
        const c = i % C, r = Math.floor(i / C);
        b.x = x0 + c * (bw + HG); b.y = y0 + r * (bh + HG); b.w = bw; b.h = bh; b.label = HL;
        for (const s of b.stacks) {
          const ri = b.rowList.indexOf(s.row);
          const top = b.y + HL + ri * chH;
          place.set(s.key, { x: b.x + (s.bay - b.bmin) * cwH, ground: top + chH, w: s.len * cwH, ri: 0, block: b, cellH: chH });
        }
      });
    }
    function layout() {
      if (heat) { layoutHeat(); return; }
      const B = blockList.length || 1;
      const nb = Math.max(1, ...blockList.map((b) => b.bmax - b.bmin + 1)), nr = Math.max(1, ...blockList.map((b) => b.rowList.length));
      let best = { cw: 0, C: 1 };
      for (let C = 1; C <= B; C++) {
        const R = Math.ceil(B / C);
        const a = (W - 12 - (C - 1) * GAP) / (C * (nb + 0.3 * (nr - 1)));
        const b = (H - 6 - (R - 1) * GAP - R * LABEL) / (R * (0.46 * maxT + 0.24 * (nr - 1)));
        const v = Math.min(a, b, 46);
        if (v > best.cw) best = { cw: v, C };
      }
      if (best.cw < 6 && stacks.length > 120) { heat = true; layoutHeat(); return; }
      cw = Math.max(3, best.cw); ch = cw * 0.46; offX = cw * 0.3; offY = cw * 0.24;
      const C = best.C, R = Math.ceil(B / C);
      const bw = cw * (nb + 0.3 * (nr - 1)), bh = ch * maxT + offY * (nr - 1) + LABEL;
      const totalW = C * bw + (C - 1) * GAP, totalH = R * bh + (R - 1) * GAP;
      const x0 = (W - totalW) / 2, y0 = (H - totalH) / 2;
      place = new Map();
      blockList.forEach((b, i) => {
        const c = i % C, r = Math.floor(i / C);
        b.x = x0 + c * (bw + GAP); b.y = y0 + r * (bh + GAP); b.w = bw; b.h = bh;
        b.ground = b.y + bh; // ground line of the nearest row
        for (const s of b.stacks) {
          const ri = b.rowList.indexOf(s.row);
          const ri2 = b.rowList.length - 1 - ri; // row 1 nearest the viewer
          place.set(s.key, { x: b.x + (s.bay - b.bmin) * cw + ri2 * offX, ground: b.ground - ri2 * offY, w: s.len * cw, ri: ri2, block: b });
        }
      });
    }
    function boxRect(key, tier) {
      const p = place.get(key); if (!p) return null;
      if (heat) return { x: p.x - 1, y: p.ground - p.cellH - 1, w: Math.max(4, p.w + 1.4), h: Math.max(4, p.cellH + 1.4), ri: 0 };
      return { x: p.x + 1, y: p.ground - (tier + 1) * ch, w: p.w - 2, h: ch - 1, ri: p.ri };
    }
    function drawBox(g, r, boxId, opts = {}) {
      const box = model.boxes[boxId] || {};
      const rgb = model.podRgb(box.pod);
      const f = opts.alpha == null ? 1 : opts.alpha;
      g.fillStyle = `rgba(${rgb[0] * 0.86 | 0},${rgb[1] * 0.86 | 0},${rgb[2] * 0.86 | 0},${f})`;
      g.fillRect(r.x, r.y, r.w, r.h);
      g.fillStyle = `rgba(255,255,255,${0.16 * f})`; g.fillRect(r.x, r.y, r.w, Math.max(1, r.h * 0.18));
      g.strokeStyle = `rgba(0,0,0,${0.45 * f})`; g.lineWidth = 0.8; g.strokeRect(r.x + 0.4, r.y + 0.4, r.w - 0.8, r.h - 0.8);
      if (box.dg && r.w > 9) { const cx = r.x + r.w - 4.5, cy = r.y + r.h / 2, q = Math.min(3.5, r.h * 0.32); g.fillStyle = '#ff5b4a'; g.beginPath(); g.moveTo(cx, cy - q); g.lineTo(cx + q, cy); g.lineTo(cx, cy + q); g.lineTo(cx - q, cy); g.fill(); }
      if (box.reefer && r.w > 9) { g.fillStyle = '#e9fbff'; g.fillRect(r.x + 2, r.y + r.h / 2 - 1, 3, 2); }
    }
    let hits = [], curState = null, curExclude = {}, nextPick = null;
    function drawLayer() {
      const g = layer.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      hits = [];
      if (!curState) return;
      if (heat) { drawHeat(g); return; }
      for (const b of blockList) {
        g.fillStyle = 'rgba(140,160,190,0.75)'; g.font = `700 10.5px ${model.font}`; g.textBaseline = 'top'; g.textAlign = 'left';
        g.fillText(`BLOCK ${b.name}`, b.x, b.y);
        // ground pads, far rows first
        const sorted = [...b.stacks].sort((a, c) => place.get(c.key).ri - place.get(a.key).ri || a.bay - c.bay);
        for (const s of sorted) {
          const p = place.get(s.key);
          g.fillStyle = 'rgba(40,60,90,0.35)';
          g.beginPath(); g.moveTo(p.x, p.ground); g.lineTo(p.x + p.w, p.ground); g.lineTo(p.x + p.w + offX, p.ground - offY); g.lineTo(p.x + offX, p.ground - offY); g.closePath(); g.fill();
        }
        for (const s of sorted) {
          const list = curState.yard.get(s.key) || [];
          list.forEach((boxId, t) => {
            if (curExclude.box === boxId) return;
            const r = boxRect(s.key, t);
            drawBox(g, r, boxId);
            hits.push({ r, box: boxId });
            if (boxId === nextPick) { g.strokeStyle = '#ffb347'; g.lineWidth = 1.6; g.strokeRect(r.x - 0.5, r.y - 0.5, r.w + 1, r.h + 1); }
          });
        }
      }
    }
    function drawHeat(g) {
      for (const b of blockList) {
        g.fillStyle = 'rgba(140,160,190,0.8)'; g.font = `700 9px ${model.font}`; g.textBaseline = 'top'; g.textAlign = 'left';
        if (b.w > 22) g.fillText(String(b.name), b.x, b.y);
        g.fillStyle = 'rgba(40,60,90,0.32)'; g.fillRect(b.x, b.y + b.label, b.w, b.h - b.label);
        for (const s of b.stacks) {
          const p = place.get(s.key);
          const list = (curState.yard.get(s.key) || []).filter((id) => id !== curExclude.box);
          if (!list.length) continue;
          const top = list[list.length - 1];
          const rgb = model.podRgb((model.boxes[top] || {}).pod);
          const a = 0.3 + 0.7 * Math.min(1, list.length / maxT);
          const r = { x: p.x, y: p.ground - p.cellH, w: Math.max(1, p.w - 0.6), h: Math.max(1, p.cellH - 0.6) };
          g.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`; g.fillRect(r.x, r.y, r.w, r.h);
          hits.push({ r, box: top });
          if (nextPick && list.includes(nextPick)) { g.strokeStyle = '#ffb347'; g.lineWidth = 1.4; g.strokeRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2); }
        }
      }
      g.font = `600 9px ${model.font}`; g.fillStyle = 'rgba(160,178,205,0.75)'; g.textAlign = 'right'; g.textBaseline = 'bottom';
      g.fillText(`heat map · ${stacks.length} stacks · brighter = taller stack (max ${maxT} high) · colour = top box's port`, W - 4, H - 1);
    }
    function arc(g, a, b, t, color) {
      const mx = (a[0] + b[0]) / 2, my = Math.min(a[1], b[1]) - 30 - Math.abs(a[0] - b[0]) * 0.15;
      g.strokeStyle = color; g.lineWidth = 1.6; g.setLineDash([4, 4]);
      g.beginPath(); g.moveTo(a[0], a[1]);
      const n = 24;
      for (let i = 1; i <= n * t; i++) { const u = i / n; g.lineTo((1 - u) * (1 - u) * a[0] + 2 * (1 - u) * u * mx + u * u * b[0], (1 - u) * (1 - u) * a[1] + 2 * (1 - u) * u * my + u * u * b[1]); }
      g.stroke(); g.setLineDash([]);
      return [(1 - t) * (1 - t) * a[0] + 2 * (1 - t) * t * mx + t * t * b[0], (1 - t) * (1 - t) * a[1] + 2 * (1 - t) * t * my + t * t * b[1]];
    }
    return {
      resize() {
        const r = canvas.getBoundingClientRect();
        dpr = Math.min(2, window.devicePixelRatio || 1); W = Math.max(10, r.width); H = Math.max(10, r.height);
        for (const c of [canvas, layer]) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
        layout(); drawLayer();
      },
      setFrame(state, opts) { curState = state; curExclude = opts.exclude || {}; nextPick = opts.nextPick || null; drawLayer(); },
      draw(anim) {
        ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(layer, 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (!anim || !anim.yard) return;
        const y = anim.yard, p = anim.p;
        const src = boxRect(y.srcKey, y.srcTier);
        if (!src) return;
        if (y.kind === 'relocate') {
          const dst = boxRect(y.dstKey, y.dstTier);
          const flash = 0.5 + 0.5 * Math.sin(p * Math.PI * 6);
          const ps = place.get(y.srcKey);
          if (ps) { ctx.fillStyle = `rgba(255,70,70,${0.12 + 0.18 * flash})`; ctx.fillRect(ps.x, ps.ground - maxT * ch, ps.w, maxT * ch); }
          if (ps && heat) { const rr = 5 + 4 * flash; ctx.strokeStyle = `rgba(255,91,91,${0.55 + 0.45 * flash})`; ctx.lineWidth = 2; ctx.strokeRect(ps.x - rr, ps.ground - ps.cellH - rr, ps.w + 2 * rr, ps.cellH + 2 * rr); }
          const t = ease((p - 0.08) / 0.84);
          if (dst) {
            const a = [src.x + src.w / 2, src.y], b = [dst.x + dst.w / 2, dst.y];
            const at = arc(ctx, a, b, Math.max(0.001, t), 'rgba(255,91,91,0.9)');
            const r = { x: at[0] - src.w / 2, y: at[1] - (t <= 0 ? 0 : src.h * 0), w: src.w, h: src.h };
            if (t <= 0) { r.x = src.x; r.y = src.y; } else if (t >= 1) { r.x = dst.x; r.y = dst.y; }
            ctx.shadowColor = '#ff4d4d'; ctx.shadowBlur = 12; drawBox(ctx, r, y.box); ctx.shadowBlur = 0;
            ctx.strokeStyle = '#ff5b5b'; ctx.lineWidth = 1.6; ctx.strokeRect(r.x - 0.5, r.y - 0.5, r.w + 1, r.h + 1);
            ctx.font = `800 10px ${model.font}`; ctx.fillStyle = '#ff9a9a'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
            ctx.fillText('REHANDLE', Math.max(30, Math.min(W - 30, r.x + r.w / 2)), Math.max(12, r.y - 3));
          }
        } else if (y.kind === 'out') {
          const t = ease(p / 0.35);
          const r = { x: src.x, y: src.y - t * 34, w: src.w, h: src.h };
          ctx.shadowColor = '#ffa53a'; ctx.shadowBlur = 10; drawBox(ctx, r, y.box, { alpha: 1 - 0.85 * t }); ctx.shadowBlur = 0;
          if (p < 0.5) {
            ctx.strokeStyle = `rgba(255,165,58,${0.9 * (1 - t)})`; ctx.lineWidth = 1.4; ctx.beginPath();
            ctx.moveTo(src.x + src.w / 2, src.y); ctx.lineTo(src.x + src.w / 2, src.y - 40); ctx.stroke();
            ctx.font = `800 10px ${model.font}`; ctx.fillStyle = `rgba(255,198,112,${1 - t * 0.6})`; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
            ctx.fillText('TO QUAY ↑', src.x + src.w / 2, r.y - 4);
          }
        }
      },
      hit(px, py) {
        for (let i = hits.length - 1; i >= 0; i--) { const r = hits[i].r; if (px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h) return hits[i].box; }
        return null;
      },
    };
  };
})();
