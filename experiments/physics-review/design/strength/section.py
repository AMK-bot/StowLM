"""Idealised thin-walled double-skin container midship section (APPROXIMATION).

Purpose: (1) check that the assumed hull-girder Z is achievable with plausible
mild-steel plating; (2) derive an effective shear area A_eff = I*sum(t_web)/S_NA
from net side-shell + inner-skin thicknesses, instead of the 8Z/L heuristic.

Members (per side, mirrored): bottom shell (half-breadth), inner bottom at h_db,
side shell 0..D, inner skin (longitudinal bulkhead) h_db..D at wing width w,
deck strip from hatch edge to side at D, hatch-side coaming (vertical) D..D+h_c.
Longitudinal stiffeners are smeared into flange thickness by solving
for the deck and bottom 'equivalent' thicknesses that give Z_deck = Z_keel = Z.

Thickness (gross, mild steel k=1), APPROXIMATION / design assumption:
  side shell   t_s = 8.0 + 0.04 L  mm  (12.3 / 13.0 / 14.4 mm at L=108/124/160)
  inner skin   t_i = 6.5 + 0.03 L  mm  (9.7 / 10.2 / 11.3 mm)
Net for hull-girder section properties per S11A 1.3 Table 1 (alpha=0.5) and
Table 2 (sea 1.0 + ballast 1.0 + t_res 0.5 => t_c = 2.5 mm): t_net = t - 1.25 mm.
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import fsolve

def members(B, D, h_db, w, b_hatch, h_c, t_s, t_i, t_d, t_b, t_c=0.025):
    # (y0,z0,y1,z1,t) per starboard half; thickness in m
    half = B/2
    return [
        (0, 0, half, 0, t_b),                 # bottom shell (eq. incl. stiffeners)
        (0, h_db, half - w, h_db, t_b*0.8),   # inner bottom (eq.)
        (half, 0, half, D, t_s),              # side shell
        (half - w, h_db, half - w, D, t_i),   # inner skin
        (b_hatch, D, half, D, t_d),           # deck strip (eq.)
        (b_hatch, D, b_hatch, D + h_c, t_c),  # hatch side coaming
    ]

def props(ms, nseg=200):
    """I, z_NA, S at NA (whole section), sum web thickness cut at NA, z_top, using both halves."""
    pts = []
    for y0, z0, y1, z1, t in ms:
        L = np.hypot(y1-y0, z1-z0); s = (np.arange(nseg)+.5)/nseg
        z = z0 + (z1-z0)*s; a = np.full(nseg, 2*L*t/nseg)  # x2 for port side
        pts.append((z, a))
    z = np.concatenate([p[0] for p in pts]); a = np.concatenate([p[1] for p in pts])
    A = a.sum(); zna = (a*z).sum()/A
    I = (a*(z-zna)**2).sum()
    # own inertia of vertical members included via segmentation; horizontal plates' own inertia negligible
    S = (a*(z-zna))[z > zna].sum()
    webs = sum(2*t for y0, z0, y1, z1, t in ms if y0 == y1 and min(z0, z1) < zna < max(z0, z1))
    ztop = max(max(m[1], m[3]) for m in ms if m[0] != m[2])  # deck (not coaming top)
    return dict(A=A, zna=zna, I=I, S=S, webs=webs, Z_deck=I/(ztop-zna), Z_keel=I/zna,
                Z_coaming=I/(max(m[3] for m in ms)-zna), A_eff=I*webs/S)

def design(Z_target, L, B, D, h_db, w, b_hatch, h_c=0.6):
    ts = (8.0 + 0.04*L - 1.25)/1000; ti = (6.5 + 0.03*L - 1.25)/1000
    def f(v):
        td, tb = v
        p = props(members(B, D, h_db, w, b_hatch, h_c, ts, ti, td, tb))
        return [p['Z_deck']/Z_target - 1, p['Z_keel']/Z_target - 1]
    td, tb = fsolve(f, [0.03, 0.02])
    p = props(members(B, D, h_db, w, b_hatch, h_c, ts, ti, td, tb))
    p.update(t_side_net_mm=ts*1e3, t_inner_net_mm=ti*1e3, t_deck_eq_mm=td*1e3, t_bottom_eq_mm=tb*1e3,
             web_area_m2=2*(ts*D + ti*(D-h_db)))
    return p
