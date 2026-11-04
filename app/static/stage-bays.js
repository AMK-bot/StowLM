/* StowLM stage: 2D bay-plan view (fallback for full-ship scale). Every bay is drawn as a cross-section seen from
 * aft (port on the left): deck tiers above the hatch-cover line, hold tiers below. 40' boxes (even bay) occupy the
 * cells of both odd bays and are marked with a joining notch. Draws only the replayed state. */
(function () {
  'use strict';
  const L = (window.StageLib = window.StageLib || {});
  const DG_FILL = { 1: '#ff8a1c', 2: '#e8413c', 3: '#e8413c', 4: '#e8413c', 5: '#ffd21f', 6: '#f4f4f4', 7: '#ffd21f', 8: '#f4f4f4', 9: '#f4f4f4' };

  L.createBays = function createBays(canvas, model) {
    const ctx = canvas.getContext('2d');
    const geo = model.bundle.geometry || {};
    const slots = (geo.slots || []).filter((s) => +s.bay % 2 === 1);
    const isDeck = (s) => (s.on_deck != null ? !!s.on_deck : +s.tier >= 80);
    const bayX = new Map(), rowY = new Map(), holdT = new Set(), deckT = new Set(), cellSet = new Set(), cellCover = new Map();
    for (const s of slots) {
      const b = +s.bay;
      if (!bayX.has(b)) bayX.set(b, []);
      bayX.get(b).push(+s.x_m);
      rowY.set(+s.row, +s.y_m);
      (isDeck(s) ? deckT : holdT).add(+s.tier);
      cellSet.add(String(s.id));
      if (s.hatch_cover_id) cellCover.set(String(s.id), s.hatch_cover_id);
    }
    const bayList = [...bayX.keys()].map((b) => ({ bay: b, x: bayX.get(b).reduce((a, c) => a + c, 0) / bayX.get(b).length }))
      .sort((a, b) => a.x - b.x); // stern (left) -> bow (right), same as the 3D view
    const rows = [...rowY.keys()].sort((a, b) => rowY.get(b) - rowY.get(a)); // port (+y) on the left, seen from aft
    const deckTiers = [...deckT].sort((a, b) => b - a), holdTiers = [...holdT].sort((a, b) => b - a);
    const id = (bay, row, tier) => String(bay).padStart(3, '0') + String(row).padStart(2, '0') + String(tier).padStart(2, '0');
    function cellsOf(slotId) {
      const m = /^(\d{3})(\d{2})(\d{2})$/.exec(String(slotId));
      if (!m) return [];
      const bay = +m[1];
      if (bay % 2 === 0) return [id(bay - 1, m[2], m[3]), id(bay + 1, m[2], m[3])];
      return [String(slotId)];
    }

    let W = 0, H = 0, dpr = 1, cell = 8, rects = new Map();
    const layer = document.createElement('canvas');
    function layout() {
      const nb = Math.max(1, bayList.length), nr = Math.max(1, rows.length), nt = deckTiers.length + holdTiers.length + 0.9;
      const padT = 70, padB = 58, padX = 18, gapB = 10, lab = 16, gapL = 14;
      let best = { c: 0, lines: 1 };
      for (let lines = 1; lines <= 4; lines++) {
        const per = Math.ceil(nb / lines);
        const c1 = (W - 2 * padX - (per - 1) * gapB) / (per * nr);
        const c2 = (H - padT - padB - lines * lab - (lines - 1) * gapL) / (lines * nt);
        const c = Math.min(c1, c2, 26);
        if (c > best.c) best = { c, lines };
      }
      cell = Math.max(2, best.c);
      const per = Math.ceil(nb / best.lines);
      const bw = nr * cell, bh = nt * cell;
      const totalW = per * bw + (per - 1) * gapB, totalH = best.lines * (bh + lab) + (best.lines - 1) * gapL;
      const x0 = (W - totalW) / 2, y0 = padT + (H - padT - padB - totalH) / 2;
      rects = new Map();
      bayList.forEach((b, i) => {
        const line = Math.floor(i / per), col = i % per;
        b.px = x0 + col * (bw + gapB); b.py = y0 + line * (bh + lab + gapL) + lab; b.w = bw; b.h = bh;
        rows.forEach((r, ri) => {
          deckTiers.forEach((t, ti) => rects.set(id(b.bay, r, t), { x: b.px + ri * cell, y: b.py + ti * cell, w: cell, h: cell, bay: b.bay }));
          holdTiers.forEach((t, ti) => rects.set(id(b.bay, r, t), { x: b.px + ri * cell, y: b.py + (deckTiers.length + 0.9 + ti) * cell, w: cell, h: cell, bay: b.bay }));
        });
        b.hatchY = b.py + (deckTiers.length + 0.45) * cell;
      });
    }

    let state = null, hits = [], xray = null;
    function drawBox(g, r, box, inset, joinDir) {
      const rgb = model.podRgb(box && box.pod);
      const x = r.x + inset, y = r.y + inset, w = r.w - 2 * inset, h = r.h - 2 * inset;
      g.fillStyle = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`; g.fillRect(x, y, w, h);
      if (w > 6) { g.fillStyle = 'rgba(255,255,255,0.18)'; g.fillRect(x, y, w, Math.max(1, h * 0.16)); }
      if (joinDir) { g.fillStyle = 'rgba(255,255,255,0.75)'; const jx = joinDir > 0 ? x + w - Math.max(1.5, w * 0.12) : x; g.fillRect(jx, y + h * 0.3, Math.max(1.5, w * 0.12), h * 0.4); }
      if (box && box.dg && w >= 6) {
        const c = [x + w / 2, y + h / 2], q = Math.max(2, w * 0.3);
        g.fillStyle = DG_FILL[String(box.dg.class || '').charAt(0)] || '#f4f4f4';
        g.beginPath(); g.moveTo(c[0], c[1] - q); g.lineTo(c[0] + q, c[1]); g.lineTo(c[0], c[1] + q); g.lineTo(c[0] - q, c[1]); g.closePath(); g.fill();
        g.strokeStyle = '#111'; g.lineWidth = 0.8; g.stroke();
      } else if (box && box.reefer && w >= 6) {
        g.strokeStyle = '#e9fbff'; g.lineWidth = 1; const c = [x + w / 2, y + h / 2], q = w * 0.28;
        g.beginPath(); for (let k = 0; k < 3; k++) { const a = (k * Math.PI) / 3; g.moveTo(c[0] - q * Math.cos(a), c[1] - q * Math.sin(a)); g.lineTo(c[0] + q * Math.cos(a), c[1] + q * Math.sin(a)); } g.stroke();
      }
    }
    function drawLayer(exclude) {
      const g = layer.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      const gr = g.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, '#061024'); gr.addColorStop(1, '#030914');
      g.fillStyle = gr; g.fillRect(0, 0, W, H);
      hits = [];
      g.font = `700 ${Math.max(9, Math.min(12, cell * 0.9))}px ${model.font}`; g.textAlign = 'center'; g.textBaseline = 'bottom';
      for (const b of bayList) {
        g.fillStyle = 'rgba(140,165,200,0.8)'; g.fillText(String(b.bay).padStart(3, '0'), b.px + b.w / 2, b.py - 3);
        // hold background + hatch line
        g.fillStyle = 'rgba(20,34,58,0.65)';
        g.fillRect(b.px - 1, b.hatchY + cell * 0.45, b.w + 2, holdTiers.length * cell + 1);
      }
      // empty cells
      g.strokeStyle = 'rgba(120,150,200,0.16)'; g.lineWidth = 1;
      for (const [cid, r] of rects) if (cellSet.has(cid)) g.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
      // hatch covers (between deck and hold): solid when on, dashed when off
      for (const b of bayList) {
        const covers = new Map();
        rows.forEach((r, ri) => { const c = cellCover.get(id(b.bay, r, holdTiers[0])) || cellCover.get(id(b.bay, r, deckTiers[deckTiers.length - 1])); if (c) { const e = covers.get(c) || { a: ri, z: ri }; e.a = Math.min(e.a, ri); e.z = Math.max(e.z, ri); covers.set(c, e); } });
        if (!covers.size) covers.set('_', { a: 0, z: rows.length - 1 });
        for (const [cid, e] of covers) {
          const off = state && state.coversOff.has(cid);
          const x = b.px + e.a * cell + 1, w = (e.z - e.a + 1) * cell - 2;
          if (off) { g.strokeStyle = 'rgba(255,181,71,0.85)'; g.setLineDash([3, 3]); g.lineWidth = 1.4; g.beginPath(); g.moveTo(x, b.hatchY); g.lineTo(x + w, b.hatchY); g.stroke(); g.setLineDash([]); }
          else { g.fillStyle = '#5a7398'; g.fillRect(x, b.hatchY - Math.max(1.2, cell * 0.14), w, Math.max(2.4, cell * 0.28)); }
        }
      }
      if (!state) return;
      const inset = cell > 8 ? 0.8 : 0.3;
      for (const [slot, bxId] of state.ship) {
        if (exclude && exclude.box === bxId) continue;
        const cs = cellsOf(slot), box = model.boxes[bxId];
        cs.forEach((c, i) => {
          const r = rects.get(c); if (!r) return;
          drawBox(g, r, box, inset, cs.length === 2 ? (i === 0 ? 1 : -1) : 0);
          hits.push({ r, box: bxId });
        });
      }
      // bow / stern hints
      g.font = `600 10px ${model.font}`; g.fillStyle = 'rgba(140,165,200,0.55)'; g.textBaseline = 'alphabetic';
      g.textAlign = 'left'; g.fillText('◂ STERN (AFT)', 18, 60);
      g.textAlign = 'right'; g.fillText('BOW (FWD) ▸', W - 18, 60);
      g.textAlign = 'center'; g.fillText('each bay seen from aft · port ◂ ▸ starboard · deck above the hatch covers, hold below', W / 2, 60);
    }
    function drawXray(g) {
      if (!xray) return;
      g.fillStyle = 'rgba(1,6,12,0.55)'; g.fillRect(0, 0, W, H);
      const occ = new Set(); if (state) for (const s of state.ship.keys()) for (const c of cellsOf(s)) occ.add(c);
      const legal = new Set(); if (xray.legal) for (const s of xray.legal) for (const c of cellsOf(s)) legal.add(c);
      for (const [cid, r] of rects) {
        if (!cellSet.has(cid)) continue;
        if (legal.has(cid)) { g.fillStyle = 'rgba(71,245,160,0.28)'; g.fillRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1); g.strokeStyle = 'rgba(71,245,160,0.95)'; g.lineWidth = 1; g.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1); }
        else if (occ.has(cid)) { g.strokeStyle = 'rgba(160,190,230,0.35)'; g.lineWidth = 1; g.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1); }
        else if (xray.legal) { g.strokeStyle = 'rgba(255,90,90,0.35)'; g.lineWidth = 1; g.beginPath(); g.moveTo(r.x + 1, r.y + r.h - 1); g.lineTo(r.x + r.w - 1, r.y + 1); g.stroke(); }
      }
      if (xray.chosen) {
        const pulse = 0.55 + 0.45 * Math.sin(performance.now() / 180);
        for (const c of cellsOf(xray.chosen)) { const r = rects.get(c); if (!r) continue; g.shadowColor = '#47f5a0'; g.shadowBlur = 12; g.strokeStyle = `rgba(220,255,236,${pulse})`; g.lineWidth = 2.2; g.strokeRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2); g.shadowBlur = 0; }
      }
    }
    function outline(g, slotId, col, w) {
      for (const c of cellsOf(slotId)) { const r = rects.get(c); if (!r) continue; g.strokeStyle = col; g.lineWidth = w; g.strokeRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2); }
    }
    return {
      resize() {
        const r = canvas.getBoundingClientRect();
        dpr = Math.min(2, window.devicePixelRatio || 1); W = Math.max(10, r.width); H = Math.max(10, r.height);
        for (const c of [canvas, layer]) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
        layout(); drawLayer();
      },
      setFrame(st, opts) { state = st; drawLayer(opts && opts.exclude); },
      setXray(info) { xray = info || null; },
      draw(anim, view) {
        ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(layer, 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (view && view.xray) drawXray(ctx);
        if (anim && anim.step) {
          const st = anim.step, pulse = 0.5 + 0.5 * Math.sin(performance.now() / 120);
          const col = anim.red ? '#ff5b5b' : anim.nonproductive ? '#ffb547' : '#ffd79a';
          if (st.box && /^\d{7}$/.test(String(st.dst || '')) && anim.p > 0.5) {
            const box = model.boxes[st.box]; const cs = cellsOf(st.dst);
            cs.forEach((c, i) => { const r = rects.get(c); if (r) { ctx.globalAlpha = Math.min(1, (anim.p - 0.5) * 2.5); drawBox(ctx, r, box, 0.8, cs.length === 2 ? (i ? -1 : 1) : 0); ctx.globalAlpha = 1; } });
          }
          if (/^\d{7}$/.test(String(st.src || ''))) outline(ctx, st.src, `rgba(255,181,71,${pulse})`, 2);
          if (/^\d{7}$/.test(String(st.dst || ''))) outline(ctx, st.dst, col, 2);
        }
        for (const [b, col, w] of [[view && view.hover, 'rgba(255,255,255,0.95)', 1.5], [view && view.selected, '#ffb347', 2.2]]) {
          if (!b || !state) continue;
          const at = state.loc.get(b); if (at && at.w === 'ship') outline(ctx, at.k, col, w);
        }
      },
      hit(px, py) { for (let i = hits.length - 1; i >= 0; i--) { const r = hits[i].r; if (px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h) return hits[i].box; } return null; },
    };
  };
})();
