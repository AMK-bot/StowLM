"""Physics review 3.2: independent hand calculation of S11A 2.3 wave loads.

Run: UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/physics-review/strength/hand_s11a_waves.py

Formulas typed from the rendered PDF pages 9-12 of data/raw/iacs/ur-s11a.pdf
(June 2015). Hull coefficients come from HullGeometry (reviewed separately);
breadths for f_Bow are obtained by finite-differencing sectional areas in z, not
from strength_limits' analytic q(x)*v(z) shortcut.
Also: S7.1 minimum section modulus cross-check, and Fig.7 end-value check.
"""
from __future__ import annotations

import math

import numpy as np

from stowage.checker.strength_limits import wave_loads, design_summary
from stowage.params import load_param_file
from stowage.vessel.hydrostatics import HullGeometry

FR = 0.85


def C(L, cw, ref):
    lref = ref * cw ** -1.3
    r = math.sqrt(L / lref)
    return 1 - 1.5 * (1 - r) ** 2.2 if L <= lref else 1 - 0.45 * (r - 1) ** 1.7


def breadth_fore(geom, z, L, rho):
    e = 1e-3
    lo = geom.submerged(z - e, z - e, 0., rho)
    hi = geom.submerged(z + e, z + e, 0., rho)
    b = (hi.sectional_areas_m2 - lo.sectional_areas_m2) / (2 * e)
    xs = np.linspace(.8 * L, L, 2001)
    bb = np.interp(xs, lo.x_m, b)  # Gauss stations: linear interpolation, ends extrapolated flat
    return np.trapezoid(bb, xs), lo.x_m, b


for name in ('feeder_500', 'feeder_1000', 'feeder_1700'):
    p = load_param_file('fleet', name)
    plain = lambda v: v['value'] if isinstance(v, dict) and 'value' in v else v
    h = {k: plain(v) for k, v in p['hull'].items()}
    geom = HullGeometry.from_params(p)
    L, B, T, D = (float(h[k]) for k in ('lpp_m', 'beam_m', 'scantling_draught_m', 'depth_m'))
    rho = 1025.
    wet = geom.submerged(T, T, 0., rho)
    cb = wet.volume_m3 / (L * B * T)
    cw = wet.waterplane_area_m2 / (L * B)
    adk, xs, b = breadth_fore(geom, D - 1e-3, L, rho)
    awl, _, _ = breadth_fore(geom, T, L, rho)
    fbow = (adk - awl) / (.2 * L * (D - T))
    fh = min(1.1, .3 * cb / cw * math.sqrt(T))
    fs = max(1.0, 4.5 * (1 + .2 * fbow) / (cw * math.sqrt(cb) * L ** .3))
    k = FR * C(L, cw, 315) * cw * (B / L) ** .8
    mh = 1.5 * L ** 3 * k * fh  # kN m
    ms = -1.5 * L ** 3 * k * fs
    ks = FR * C(L, cw, 330) * cw * (B / L) ** .8 * L ** 2
    f = dict(hog_aft=5.2 * ks * (.3 + .7 * fh), hog_fore=-5.7 * ks * fh,
             sag_aft=-5.2 * ks * (.3 + .7 * fs), sag_fore=5.7 * ks * (.25 + .75 * fs), mid=4.0 * ks)
    kw = dict(length_m=L, beam_m=B, draught_m=T, block_coefficient=cb, waterplane_coefficient=cw,
              bow_flare_coefficient=fbow)
    code_mid = wave_loads(.45 * L, **kw)
    print(f'== {name}: L={L} B={B} T={T} D={D} CB={cb:.4f} Cw={cw:.4f} fBow(hand,FD breadth)={fbow:.4f} '
          f'fNL-Hog={fh:.4f} fNL-Sag={fs:.4f}')
    print(f'   hand MwHog={mh/1e3:.1f} MN.m  MwSag={ms/1e3:.1f} MN.m; code(same fBow) hog={code_mid["hogging_nm"]/1e6:.1f} '
          f'sag={code_mid["sagging_nm"]/1e6:.1f}')
    print('   hand Fw (MN): ' + ', '.join(f'{k_}={v/1e3:+.2f}' for k_, v in f.items()))
    summ = design_summary(p, geom)
    print(f'   code design_summary fBow={summ["bow_flare_coefficient"]:.4f} MwHog={summ["wave_peak_hogging_nm"]/1e6:.1f} '
          f'MwSag={summ["wave_peak_sagging_nm"]/1e6:.1f}; designer SW peak BM={summ["design_sw_peak_bm_nm"]/1e6:.1f} '
          f'(= {summ["design_sw_peak_bm_nm"]/1e3/mh:.2f} x MwHog); SW peak SF={summ["design_sw_peak_sf_n"]/1e6:.1f} MN '
          f'(= {summ["design_sw_peak_sf_n"]/1e3/f["mid"]:.2f} x FwMid)')
    # Fig.7 ends: rule has negative curve = 0.25*FwSagAft at AE (x=0) and 0 at FE.
    ae, fe = wave_loads(0., **kw), wave_loads(L, **kw)
    print(f'   Fig7 ends, model signs (max_shear = -S11A negative curve): rule AE max_shear={-.25*f["sag_aft"]/1e3:+.3f} MN, '
          f'FE max_shear=0; code AE={ae["max_shear_n"]/1e6:+.3f} MN, FE={fe["max_shear_n"]/1e6:+.3f} MN')
    # S7.1 minimum section modulus (cm3 -> m3), Rule L ~ Lpp, Cb>=0.6
    cn = 10.75 - ((300 - L) / 100) ** 1.5
    wmin = cn * L ** 2 * B * (max(cb, .6) + .7) * 1e-6
    print(f'   S7.1 Wmin={wmin:.2f} m3 vs code assumed effective net Z={summ["assumed_effective_net_section_modulus_m3"]:.2f} m3; '
          f'S11A 3.2 Inet_min=1.55 L (Ms+Mw) 1e-7 with Ms+Mw=hog total -> {1.55*L*(summ["design_sw_peak_bm_nm"]/1e3+mh)*1e-7:.2f} m4')
    # 0.97 LWL rule-length sensitivity (synthetic hull LWL == Lpp)
    L97 = .97 * L
    k97 = FR * C(L97, cw, 315) * cw * (B / L97) ** .8
    print(f'   Rule length sensitivity: MwHog(L=0.97 Lpp)/MwHog(Lpp) = {1.5*L97**3*k97*fh/mh:.3f}')
